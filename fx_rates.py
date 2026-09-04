# ======================================================================================================================
# File:         fx_rates.py
# Author:       Alex Hind (via Claude)
# Created:      2026-09-04
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Currency -> GBP rates, so a single market-cap floor can be compared against instruments quoted in nine different
# currencies (user 2026-09-04: "MCAP is expected to be in GBP in our system").
#
# WHY THIS EXISTS. mcap_backfill.py stores market cap in the instrument's OWN currency and says so explicitly --
# "cross-currency comparison is the caller's concern" -- but every caller then compared it against one absolute
# number anyway. hvf_web/server.py::_mcap_map() read `ticker, mcap` and discarded the `currency` column entirely.
#
# Measured on the live book 2026-09-04, against the saved floor min_instrument_value = 100,000,000,000:
#   BP.L    judged on GBP    79,500,816,431  -> BREACH, though in USD it is above the floor
#   4519.T  judged on JPY 11,573,344,215,511 -> OK,     though in GBP it is about 55bn and well below it
# All four JPY orders were in the six that passed. 31 of 62 pending orders breached on instrument value and
# nothing else, and those 31 were 25 GBP, 3 USD, 2 HKD, 1 AUD -- the filter was sorting by denomination as much
# as by size, and the IG Account breach panel would have offered every one of them for cancellation.
#
# SCHEDULING. refresh() is called by mcap_backfill.main(), which the weekly Market Cap Backfill job already runs
# (Sundays 05:00 UTC, setup_cronjobs.py::JOBS). That is deliberate: a rate table is only meaningful next to the
# caps it converts, and this repository's recurring defect is correct code that nothing ever invokes. It is
# called at the START of that run rather than the end, so a backfill that times out on the 1,700-ticker loop
# still leaves the rates refreshed.
#
# A MISSING RATE IS NOT A RATE OF 1.0. An unconvertible currency yields None and the caller drops the ticker, so
# it reads as "market cap unknown" rather than silently passing or failing a floor. Treating an unknown currency
# as GBP is precisely the bug this file exists to remove.
#
# Usage:  python fx_rates.py            # refresh from yfinance and print the table
# ======================================================================================================================

import logging
import time as _time

log = logging.getLogger("fx_rates")

# Pence-quoted markets. mcap_backfill already converts these to pounds and relabels them 'GBP', so this is a
# defensive second line for any other producer that stores the raw label.
_PENCE = {"GBp", "GBX", "gbx"}

_TTL = 6 * 3600                       # in-process cache; the stored rates only change weekly
_CACHE = {"ts": 0.0, "data": None}

# A rate to GBP outside this band is a bad read, not a currency. JPY is the smallest we hold at ~0.0047 and no
# currency we quote is worth more than a few pounds; a yfinance hiccup returning 0 or a price in the wrong
# direction would otherwise be written to the table and silently rescale a whole market.
_MIN_RATE, _MAX_RATE = 1e-6, 100.0


def ensure_schema(db):
    db.run("""create table if not exists fx_rates (
                 currency     text primary key,
                 rate_to_gbp  double precision not null,
                 as_of        timestamptz default now())""")


def _held_currencies(db):
    """The currencies actually present in instrument_mcap -- there is no point fetching any other."""
    rows = db.run("select distinct currency from instrument_mcap where currency is not null") or []
    return sorted({str(r[0]).strip() for r in rows if str(r[0]).strip()})


def _fetch_to_gbp(currency: str):
    """Rate that multiplies `currency` into GBP, or None. GBP is 1.0 without a network call."""
    if currency in ("GBP", "gbp"):
        return 1.0
    if currency in _PENCE:
        return 0.01
    try:
        import yfinance as yf
        fi = yf.Ticker(f"{currency}GBP=X").fast_info
        rate = getattr(fi, "last_price", None)
        rate = float(rate) if rate is not None else None
    except Exception as ex:
        log.warning("FX fetch failed for %s: %s", currency, ex)
        return None
    if rate is None or not (_MIN_RATE < rate < _MAX_RATE):
        log.warning("FX rate for %s rejected as implausible: %r", currency, rate)
        return None
    return rate


def refresh(currencies=None, db=None) -> dict:
    """Fetch and upsert every held currency's GBP rate. Returns what was written.

    A currency that fails to fetch is LEFT ALONE rather than deleted or zeroed: last week's rate is a far
    better answer than no rate, which would blank the market cap of every instrument quoted in it.
    """
    from db_pool import get_db
    own = db is None
    db = db or get_db()
    written = {}
    try:
        ensure_schema(db)
        for cur in (currencies if currencies is not None else _held_currencies(db)):
            rate = _fetch_to_gbp(cur)
            if rate is None:
                continue
            db.run("""insert into fx_rates (currency, rate_to_gbp, as_of) values (:c, :r, now())
                      on conflict (currency) do update set rate_to_gbp = :r, as_of = now()""",
                   c=cur, r=rate)
            written[cur] = rate
    finally:
        if own:
            db.close()
    _CACHE.update(ts=0.0, data=None)          # a refresh must be visible to this process immediately
    log.info("FX rates refreshed to GBP: %s", ", ".join(f"{c}={r:.6g}" for c, r in sorted(written.items())))
    return written


def rates() -> dict:
    """{currency: rate_to_gbp}, cached in-process. {} if the table cannot be read."""
    now = _time.time()
    if _CACHE["data"] is not None and now - _CACHE["ts"] < _TTL:
        return _CACHE["data"]
    out = {}
    try:
        from db_pool import get_db
        db = get_db()
        try:
            for cur, rate in (db.run("select currency, rate_to_gbp from fx_rates") or []):
                if rate is not None and _MIN_RATE < float(rate) < _MAX_RATE:
                    out[str(cur)] = float(rate)
        finally:
            db.close()
    except Exception as ex:
        log.warning("FX rates unavailable: %s", ex)
        return _CACHE["data"] or {}
    out.setdefault("GBP", 1.0)                # true by definition; never depends on a fetch having worked
    _CACHE.update(ts=now, data=out)
    return out


def to_gbp(value, currency, table=None):
    """`value` in `currency` expressed in GBP, or None if it cannot be converted honestly."""
    if value is None:
        return None
    table = rates() if table is None else table
    cur = str(currency or "").strip()
    rate = table.get(cur)
    if rate is None and cur in _PENCE:
        rate = 0.01
    if rate is None:
        return None
    try:
        return float(value) * rate
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    from dotenv import load_dotenv; load_dotenv(override=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    written = refresh()
    print(f"\n{len(written)} rate(s) stored:\n")
    for cur, rate in sorted(written.items()):
        print(f"  1 {cur:4} = {rate:.6f} GBP")
