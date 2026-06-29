# ======================================================================================================================
# File:         price_audit.py
# Author:       Alex Hind
# Created:      2026-06-29
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Daily audit + backfill of the Supabase price_history "golden" dataset (user 2026-06-29). For every monitored
# instrument it re-fetches the recent OHLCV window from the authoritative source (yfinance today; IG can be added),
# compares it against what is stored, records any discrepancy, then UPSERTs so the stored series is corrected and
# extended. Each price keeps its source + recorded_at / updated_at (handled by price_store). A summary row is written
# to price_audit_log so each run is itself auditable.
#
# Usage:
#   python price_audit.py                 # daily audit — re-verify the last AUDIT_LOOKBACK_DAYS for every ticker
#   python price_audit.py --backfill 1100 # one-off deep backfill (~3y) for tickers that are missing / short
#   python price_audit.py --tickers ABF.L,DIS [--lookback 30] [--source YF] [--slack]
#
# A discrepancy = stored close differs from the freshly-fetched close for the same bar_date by > DISCREPANCY_TOL_PCT.
# These are reported (and corrected) — that is the point of the audit: keep the golden set provably correct.
#
# Environment: SUPABASE_USER, SUPABASE_DB_PASSWORD (via db_pool); optionally SLACK_TWITTER for the --slack summary.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-29  Alex Hind   Initial build — per-ticker re-fetch/compare/correct, discrepancy detection,
#                                 price_audit_log run record, optional Slack summary.
# ======================================================================================================================

import argparse
import logging
import time
from datetime import datetime, timezone, timedelta

import pandas as pd

import price_store
from db_pool import get_db

log = logging.getLogger("price_audit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

AUDIT_LOOKBACK_DAYS = 30        # daily mode re-verifies the trailing month (catches YF revisions + gaps)
DISCREPANCY_TOL_PCT = 0.1       # |stored-fetched|/fetched*100 above this = a real discrepancy
PER_TICKER_PAUSE_S = 0.0        # raise if yfinance starts throttling

_AUDIT_LOG_DDL = """create table if not exists price_audit_log (
    id              bigserial primary key,
    run_at          timestamptz not null default now(),
    mode            text        not null,
    source          text        not null,
    tickers_checked int         not null,
    bars_written    int         not null,
    discrepancies   int         not null,
    max_drift_pct   double precision,
    duration_s      double precision,
    notes           text
)"""


def _ensure_audit_log(db):
    db.run(_AUDIT_LOG_DDL)


def _universe():
    """{ticker: yahoo_symbol} for every monitored instrument."""
    from run_hvf_report import UNIVERSE
    try:
        from config import YAHOO_MAP
    except Exception:
        YAHOO_MAP = {}
    out = {}
    for tickers in UNIVERSE.values():
        for tk in tickers:
            out[tk] = YAHOO_MAP.get(tk, tk)
    return out


def audit_ticker(ticker, yahoo_symbol, window_days, source, db):
    """Re-fetch the trailing `window_days`, compare to stored, UPSERT. Returns
    (bars_written, discrepancies, max_drift_pct)."""
    import yfinance as yf
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=window_days)
    try:
        dl = yf.download(yahoo_symbol, start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True)
    except Exception as e:
        log.warning(f"{ticker}: fetch failed: {e}")
        return 0, 0, None
    if dl is None or dl.empty:
        log.warning(f"{ticker}: source returned no data")
        return 0, 0, None
    if getattr(dl.columns, "nlevels", 1) > 1:
        dl = dl.copy()
        dl.columns = dl.columns.get_level_values(0)

    # Compare against what is stored for the same window BEFORE overwriting.
    discrepancies, max_drift = 0, 0.0
    stored = price_store.get_bars(ticker, start, end, db=db)
    if stored is not None and not stored.empty:
        s_close = stored["Close"]
        for idx, c in dl["Close"].items():
            if pd.isna(c):
                continue
            ts = pd.Timestamp(idx).normalize()
            if ts in s_close.index:
                old = float(s_close.loc[ts])
                drift = abs(old - float(c)) / float(c) * 100 if c else 0.0
                if drift > DISCREPANCY_TOL_PCT:
                    discrepancies += 1
                    max_drift = max(max_drift, drift)
                    log.info(f"{ticker} {ts.date()}: stored {old:g} -> source {float(c):g} ({drift:.2f}% drift) - correcting")

    written = price_store.upsert_bars(ticker, dl, source, db=db)
    return written, discrepancies, (max_drift or None)


def run(mode, window_days, source, tickers=None, slack=False):
    t0 = time.time()
    uni = _universe()
    if tickers:
        uni = {tk: uni.get(tk, tk) for tk in tickers}
    log.info(f"price audit [{mode}] - {len(uni)} instruments, window {window_days}d, source {source}")

    db = get_db()
    tot_written = tot_disc = checked = 0
    max_drift = 0.0
    try:
        price_store.ensure_schema(db)
        _ensure_audit_log(db)
        for tk, ysym in uni.items():
            w, d, md = audit_ticker(tk, ysym, window_days, source, db)
            checked += 1
            tot_written += w
            tot_disc += d
            if md:
                max_drift = max(max_drift, md)
            if PER_TICKER_PAUSE_S:
                time.sleep(PER_TICKER_PAUSE_S)
        dur = time.time() - t0
        notes = f"{checked} checked"
        db.run("insert into price_audit_log (mode,source,tickers_checked,bars_written,discrepancies,"
               "max_drift_pct,duration_s,notes) values (:m,:s,:tc,:bw,:dc,:md,:du,:no)",
               m=mode, s=source, tc=checked, bw=tot_written, dc=tot_disc,
               md=(max_drift or None), du=round(dur, 1), no=notes)
    finally:
        db.close()

    summary = (f"Price audit [{mode}] done: {checked} instruments, {tot_written} bars written, "
               f"{tot_disc} discrepancies corrected (max drift {max_drift:.2f}%), {dur:.0f}s")
    log.info(summary)
    if slack:
        try:
            from intraday_signals import _post_text_to_slack  # best-effort; skip if unavailable
            _post_text_to_slack(summary)
        except Exception as e:
            log.warning(f"slack summary skipped: {e}")
    return summary


def main():
    ap = argparse.ArgumentParser(description="Audit/backfill the Supabase price_history golden dataset.")
    ap.add_argument("--backfill", type=int, metavar="DAYS",
                    help="deep backfill window in days (e.g. 1100 ~ 3y) instead of the daily audit window")
    ap.add_argument("--lookback", type=int, default=AUDIT_LOOKBACK_DAYS,
                    help=f"daily audit window in days (default {AUDIT_LOOKBACK_DAYS})")
    ap.add_argument("--tickers", type=str, help="comma-separated subset (default: whole universe)")
    ap.add_argument("--source", type=str, default="YF", help="source label to record (default YF)")
    ap.add_argument("--slack", action="store_true", help="post the run summary to Slack")
    a = ap.parse_args()
    tickers = [t.strip() for t in a.tickers.split(",") if t.strip()] if a.tickers else None
    if a.backfill:
        run("backfill", a.backfill, a.source, tickers, a.slack)
    else:
        run("daily", a.lookback, a.source, tickers, a.slack)


if __name__ == "__main__":
    main()
