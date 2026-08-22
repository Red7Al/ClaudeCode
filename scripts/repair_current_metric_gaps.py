"""Inspect and selectively repair current-instrument metric gaps from Yahoo Finance.

The current data-quality audit remains the authority for which rows are unresolved.
This tool fetches a bounded recent window for those tickers and writes only series
with usable OHLCV observations.  Empty or volume-less Yahoo responses are reported
but never written, so a source outage cannot replace retained market history.
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import price_store
import web_store


# Verified corporate actions.  The retained scanner ticker remains the identity
# used throughout the application; only the Yahoo retrieval symbol changes.
_SUCCESSOR_SYMBOLS = {"MMC": "MRSH", "FI": "FISV"}


def _volume_count(frame):
    if frame is None or frame.empty or "Volume" not in frame:
        return 0
    volume = frame["Volume"]
    if getattr(volume, "ndim", 1) > 1:
        volume = volume.iloc[:, 0]
    return int(volume.fillna(0).gt(0).sum())


def _targets():
    ok, audit = web_store.read_json_store("current_instrument_metric_audit")
    if not ok:
        raise RuntimeError("current instrument audit is unavailable")
    return [row["ticker"] for row in audit.get("unresolved", []) if row.get("ticker")]


def _yahoo_symbol(ticker):
    """Use NSE only as a labelled fallback for a BSE-listed instrument."""
    if ticker.endswith(".BO"):
        return f"{ticker[:-3]}.NS", "YF_NSE_FALLBACK_20260822"
    if ticker in _SUCCESSOR_SYMBOLS:
        return _SUCCESSOR_SYMBOLS[ticker], "YF_TICKER_SUCCESSOR_20260822"
    return ticker, "YF_DATA_QUALITY_20260822"


def run(apply=False, days=150):
    yf.set_tz_cache_location(".yf-cache-metric-audit")
    start = dt.date.today() - dt.timedelta(days=days)
    end = dt.date.today() + dt.timedelta(days=1)
    results = []
    for ticker in _targets():
        yahoo_symbol, source = _yahoo_symbol(ticker)
        frame = yf.download(yahoo_symbol, start=start.isoformat(), end=end.isoformat(),
                            progress=False, auto_adjust=True)
        bars = 0 if frame is None else len(frame)
        volume_bars = _volume_count(frame)
        written = 0
        # Twenty prior volume bars are necessary for current RVOL; fewer rows
        # cannot resolve the reported insufficiency and are retained as evidence only.
        if apply and bars >= 21 and volume_bars >= 21:
            # Retain the BSE instrument identity; source makes the NSE fallback
            # explicit and auditable on every written bar.
            written = price_store.upsert_bars(ticker, frame, source)
        results.append({"ticker": ticker, "yahoo_symbol": yahoo_symbol, "source": source,
                        "bars": bars, "volume_bars": volume_bars,
                        "first": None if not bars else str(frame.index.min().date()),
                        "last": None if not bars else str(frame.index.max().date()),
                        "written": written})
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="write only Yahoo series with >=21 reported-volume bars")
    args = parser.parse_args()
    for result in run(apply=args.apply):
        print(result)
