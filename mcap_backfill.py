# ======================================================================================================================
# File:         mcap_backfill.py
# Author:       Alex Hind (via Claude)
# Created:      2026-08-01
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Backfill a market-cap store so the Back Test / "What separates the winners" tables and the Min/Max instrument
# value (MCAP) filters have data. Nothing captured market cap before (user 2026-08-01, P-07/P-08).
#
# Fetches marketCap per universe ticker via yfinance fast_info and NORMALISES currency:
#   - yfinance reports market_cap in the instrument's own currency, EXCEPT pence-quoted markets (currency 'GBp'/
#     'GBX', e.g. LSE '.L') where it is ~100x inflated (Barclays BARC.L -> £6.86T raw vs ~£68B). Those are /100
#     and re-labelled 'GBP'.
# Stores raw-normalised value + currency; cross-currency comparison is the caller's concern (the value is shown
# in its own currency). Idempotent upsert; safe to re-run.
#
# Usage:  python mcap_backfill.py [--tickers A,B,C]
# ======================================================================================================================

import argparse
import datetime as dt
import logging

from dotenv import load_dotenv; load_dotenv(override=True)
import yfinance as yf

from db_pool import get_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mcap_backfill")

_PENCE = {"GBp", "GBX", "gbp", "GBp "}   # pence-quoted → /100 to pounds


def ensure_schema(db):
    db.run("""create table if not exists instrument_mcap (
                 ticker      text primary key,
                 mcap        double precision,
                 currency    text,
                 updated_at  timestamptz default now())""")
    # HISTORY (user 2026-08-29: "have a table for mcap is fine with periodic new rows containing latest
    # data"). instrument_mcap above is CURRENT only -- one row per ticker that each run overwrites -- so
    # no past value has ever survived. That made "did this setup meet the MCap band ON ITS TRIGGER DATE"
    # unanswerable, which is what the requester actually wanted to check.
    #
    # A separate table rather than re-keying the one above, deliberately: order_metrics.py and
    # hvf_web/server.py both read instrument_mcap expecting exactly one row per ticker, and changing its
    # key under them would silently give either an arbitrary historical value.
    #
    # Keyed (ticker, as_of) so a same-day re-run overwrites that day instead of duplicating it.
    db.run("""create table if not exists instrument_mcap_history (
                 ticker      text not null,
                 as_of       date not null,
                 mcap        double precision,
                 currency    text,
                 recorded_at timestamptz default now(),
                 primary key (ticker, as_of))""")


def _fetch_one(tk: str):
    """(mcap_normalised, currency) or (None, None). Pence markets are converted to pounds."""
    try:
        fi = yf.Ticker(tk).fast_info
        mc = getattr(fi, "market_cap", None)
        cur = getattr(fi, "currency", None) or ""
        if mc is None or mc <= 0:
            return None, None
        if cur in _PENCE:
            mc = mc / 100.0
            cur = "GBP"
        return float(mc), cur
    except Exception as e:
        log.debug(f"{tk}: {e}")
        return None, None


def main():
    ap = argparse.ArgumentParser(description="Backfill market cap per ticker into instrument_mcap.")
    ap.add_argument("--tickers", type=str, help="comma-separated subset (default: whole universe)")
    a = ap.parse_args()

    from price_audit import _universe
    tickers = [t.strip() for t in a.tickers.split(",")] if a.tickers else list(_universe())

    db = get_db()
    try:
        ensure_schema(db)
    finally:
        db.close()

    t0 = dt.datetime.now()
    ok = miss = 0
    for i, tk in enumerate(tickers, 1):
        mc, cur = _fetch_one(tk)
        if mc is None:
            miss += 1
        else:
            ok += 1
            db = get_db()
            try:
                db.run("""insert into instrument_mcap (ticker, mcap, currency, updated_at)
                          values (:t, :m, :c, now())
                          on conflict (ticker) do update set mcap = :m, currency = :c, updated_at = now()""",
                       t=tk, m=mc, c=cur)
                # ...and append it to the history, so this run's value outlives the next one.
                db.run("""insert into instrument_mcap_history (ticker, as_of, mcap, currency, recorded_at)
                          values (:t, current_date, :m, :c, now())
                          on conflict (ticker, as_of) do update set mcap = :m, currency = :c,
                                                                    recorded_at = now()""",
                       t=tk, m=mc, c=cur)
            finally:
                db.close()
        if i % 50 == 0 or i == len(tickers):
            el = (dt.datetime.now() - t0).total_seconds()
            log.info(f"  {i}/{len(tickers)} — {ok} stored, {miss} missing — {el/60:.1f}m elapsed")
    log.info(f"mcap backfill done: {ok} stored, {miss} missing, {(dt.datetime.now()-t0).total_seconds()/60:.1f} min")


if __name__ == "__main__":
    main()
