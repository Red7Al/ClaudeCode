# ======================================================================================================================
# File:         run_fundamentals_precompute.py
# Author:       Alex Hind
# Created:      2026-09-18
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Fetches the Fundamentals and Broker-analysis panels for the whole universe and stores them, because the
# web host cannot fetch them itself.
#
# WHY. /api/fundamentals and /api/broker both called yfinance on the request thread. yfinance imports
# pandas, pandas imports numpy, and numpy is SIGSYS-killed on the IONOS host -- its bundled OpenBLAS calls
# mbind(2) and the host's seccomp filter kills the process rather than failing the call (proven by strace,
# 2026-09-14). Both endpoints therefore returned HTTP 500 after ~120 SECONDS, measured on the live site on
# 2026-09-18, and had been dead for weeks alongside /api/card and the old /api/pricewin.
#
# A try/except CANNOT save them: SIGSYS kills the process, it does not raise. So the endpoints must never
# import yfinance at all, and the fetch has to happen somewhere numpy works -- here, in GitHub Actions,
# exactly as the winners and performance payloads already do.
#
# THE EXTRACTION LOGIC LIVES HERE AND ONLY HERE. It was moved out of hvf_web/server.py rather than copied,
# because "one fact, several pieces of code each deciding what it means" is this repository's other
# recurring defect. The server now serves what this produces and computes none of it.
#
# Cost, MEASURED 2026-09-18 over five real tickers: median 692 bytes and 0.33s per instrument, 29 KPIs
# each. For ~1,357 equity-like instruments that projects to about 0.9 MB stored and ~7 minutes serial.
# Non-equities (FX "=X", futures "=F", indices "^", crypto "-USD"/"-USDT") have no company fundamentals
# and are skipped, which is the same rule the endpoint already applied.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-09-18  Alex Hind   Initial build — universe-wide fundamentals + broker precompute.
# ======================================================================================================================

import argparse
import logging
import sys
import time

log = logging.getLogger("fundamentals_precompute")

FUND_STORE_KEY = "fundamentals_by_ticker"
BROKER_STORE_KEY = "broker_by_ticker"

# The endpoint skipped these outright rather than spam Yahoo's transient quoteSummary 404s for instruments
# that have no company behind them (user 2026-07-27). Same rule, same place in the flow.
_NON_EQUITY_SUFFIXES = ("=X", "=F", "-USD", "-USDT")


def is_equity(ticker: str) -> bool:
    t = (ticker or "").upper()
    return bool(t) and not (t.startswith("^") or t.endswith(_NON_EQUITY_SUFFIXES))


def _num(info: dict, key):
    v = info.get(key)
    return v if isinstance(v, (int, float)) else None


def fetch_fundamentals(ticker: str) -> dict | None:
    """The Fundamentals card for one instrument, or None when Yahoo gave us nothing usable.

    Field-for-field what /api/fundamentals used to build inline, including the dividend-yield quirk:
    yfinance's own yield handles the .L pence/pounds units, so prefer it and only fall back to
    rate/price; then normalise percent-vs-fraction, because some versions give 2.9 and some 0.029.
    """
    import yfinance as yf
    try:
        from config import YAHOO_MAP
    except Exception:
        YAHOO_MAP = {}
    info = yf.Ticker(YAHOO_MAP.get(ticker, ticker)).info or {}
    if not info:
        return None
    cur = info.get("currency") or ("GBp" if ticker.endswith(".L") else "USD")
    price = _num(info, "currentPrice") or _num(info, "regularMarketPrice")
    drate = _num(info, "dividendRate")
    dyield = _num(info, "dividendYield")
    if dyield is None and drate and price:
        dyield = drate / price
    if isinstance(dyield, (int, float)) and dyield > 1.5:
        dyield = dyield / 100.0
    kpis = {
        "marketCap": _num(info, "marketCap"), "totalRevenue": _num(info, "totalRevenue"),
        "ebitda": _num(info, "ebitda"), "trailingPE": _num(info, "trailingPE"),
        "forwardPE": _num(info, "forwardPE"),
        "pegRatio": _num(info, "trailingPegRatio") or _num(info, "pegRatio"),
        "priceToBook": _num(info, "priceToBook"), "evToEbitda": _num(info, "enterpriseToEbitda"),
        "priceToSales": _num(info, "priceToSalesTrailing12Months"),
        "trailingEps": _num(info, "trailingEps"), "forwardEps": _num(info, "forwardEps"),
        "dividendRate": drate, "dividendYield": dyield, "payoutRatio": _num(info, "payoutRatio"),
        "freeCashflow": _num(info, "freeCashflow"), "operatingCashflow": _num(info, "operatingCashflow"),
        "profitMargin": _num(info, "profitMargins"), "operatingMargin": _num(info, "operatingMargins"),
        "grossMargin": _num(info, "grossMargins"), "roe": _num(info, "returnOnEquity"),
        "roa": _num(info, "returnOnAssets"), "revenueGrowth": _num(info, "revenueGrowth"),
        "earningsGrowth": _num(info, "earningsGrowth"), "debtToEquity": _num(info, "debtToEquity"),
        "currentRatio": _num(info, "currentRatio"), "quickRatio": _num(info, "quickRatio"),
        "beta": _num(info, "beta"), "fiftyTwoWeekHigh": _num(info, "fiftyTwoWeekHigh"),
        "fiftyTwoWeekLow": _num(info, "fiftyTwoWeekLow"),
    }
    if not any(v is not None for v in kpis.values()):
        return None          # an all-null card is not worth storing over a good older copy
    return {"currency": cur, "kpis": kpis}


def fetch_broker(ticker: str) -> dict | None:
    """Net analyst upgrades vs downgrades over 6 and 12 months, or None when Yahoo has no coverage."""
    import pandas as pd
    import yfinance as yf
    try:
        from config import YAHOO_MAP
    except Exception:
        YAHOO_MAP = {}
    ud = yf.Ticker(YAHOO_MAP.get(ticker, ticker)).upgrades_downgrades
    if ud is None or ud.empty:
        return None
    res = {"up6": 0, "down6": 0, "up12": 0, "down12": 0, "available": True}
    now = pd.Timestamp.now(tz="UTC")
    for dt, row in ud.iterrows():
        try:
            d = pd.Timestamp(dt)
            d = d.tz_localize("UTC") if d.tzinfo is None else d.tz_convert("UTC")
        except Exception:
            continue
        months = (now - d).days / 30.44
        act = str(row.get("Action", "")).lower()
        if 0 <= months <= 12:
            if act == "up":
                res["up12"] += 1
                res["up6"] += (months <= 6)
            elif act == "down":
                res["down12"] += 1
                res["down6"] += (months <= 6)
    return {k: int(v) for k, v in res.items()}


def universe() -> list:
    """Every ticker in the published snapshot. Read through the server so there is ONE definition of what
    the universe is; a second list here would drift the moment the scan changed."""
    from hvf_web import server
    recs = (server._load_snapshot() or {}).get("records") or []
    return [r.get("ticker") for r in recs if r.get("ticker")]


def build(limit: int = 0, dry_run: bool = False) -> int:
    """Fetch and store both payloads. Returns the number of payloads that FAILED to store (0 = success)."""
    import web_store

    tickers = [t for t in universe() if is_equity(t)]
    if limit:
        tickers = tickers[:limit]
    if not tickers:
        log.error("no instruments in the snapshot; refusing to store an empty universe")
        return 2

    log.info("Fetching fundamentals + broker for %d equity instruments", len(tickers))
    funds, brokers, t0 = {}, {}, time.time()
    for i, tk in enumerate(tickers, 1):
        try:
            f = fetch_fundamentals(tk)
            if f:
                funds[tk] = f
        except Exception as ex:
            log.warning("  fundamentals failed for %s: %s", tk, ex)
        try:
            b = fetch_broker(tk)
            if b:
                brokers[tk] = b
        except Exception as ex:
            log.warning("  broker failed for %s: %s", tk, ex)
        if i % 100 == 0:
            log.info("  %d/%d (%.0fs elapsed)", i, len(tickers), time.time() - t0)

    took = time.time() - t0
    log.info("fetched %d fundamentals and %d broker records in %.0fs", len(funds), len(brokers), took)

    # REFUSE TO STORE AN EMPTY RESULT over a good older copy -- the same rule the winners precompute
    # applies. A Yahoo outage must make the panel stale, never blank.
    failed = 0
    for key, data, name in ((FUND_STORE_KEY, funds, "fundamentals"), (BROKER_STORE_KEY, brokers, "broker")):
        if not data:
            log.error("  %s produced NOTHING; refusing to store it", name)
            failed += 1
            continue
        if dry_run:
            log.info("  %s: %d records (dry run, not stored)", name, len(data))
            continue
        doc = {"built_at": time.time(), "count": len(data), "records": data}
        if web_store.save_json_store(key, doc):
            log.info("  %s: %d records -> %s", name, len(data), key)
        else:
            log.error("  %s: built %d records but the store write FAILED", name, len(data))
            failed += 1
    return failed


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Precompute the Fundamentals and Broker panels for the universe.")
    ap.add_argument("--limit", type=int, default=0, help="only the first N instruments (testing)")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, store nothing")
    a = ap.parse_args()
    failed = build(limit=a.limit, dry_run=a.dry_run)
    if failed:
        log.error("%d payload(s) failed; the panels keep their previous copy", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
