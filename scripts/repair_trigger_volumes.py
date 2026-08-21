"""Repair only confirmed zero-volume trigger bars used by the Best Settings audit.

The tool is deliberately narrow: it downloads a four-day window around each listed
trigger, reports the provider's exact trigger-day volume, and only writes that one
bar when the provider reports a positive value.  It never invents RVOL or alters
any other historical bar.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

# Scripts run with their own directory first; import the application store from
# the repository root without requiring callers to alter PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import price_store


TARGETS = (
    ("IPF.L", "2026-08-04"),
    ("2688.HK", "2026-02-16"),
    ("2269.HK", "2023-12-04"),
    ("0019.HK", "2024-12-24"),
    ("6618.HK", "2025-01-28"),
    ("0384.HK", "2024-02-09"),
    ("1038.HK", "2025-01-28"),
)


def _trigger_volume(frame, when):
    if frame is None or frame.empty or when not in frame.index:
        return None
    volume = frame.loc[when, "Volume"]
    if getattr(volume, "ndim", 0):
        volume = volume.iloc[0]
    return None if pd.isna(volume) else int(volume)


def run(apply=False):
    yf.set_tz_cache_location(".yf-cache-metric-audit")
    results = []
    for ticker, date_text in TARGETS:
        when = pd.Timestamp(date_text)
        frame = yf.download(
            ticker,
            start=(when - pd.Timedelta(days=2)).strftime("%Y-%m-%d"),
            end=(when + pd.Timedelta(days=2)).strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        volume = _trigger_volume(frame, when)
        written = 0
        if apply and volume and volume > 0:
            written = price_store.upsert_bars(
                ticker, frame.loc[[when]], "YF_AUDIT_20260821"
            )
        results.append({"ticker": ticker, "date": date_text, "provider_volume": volume,
                        "written": written})
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="write only provider-confirmed positive exact-day volumes")
    args = parser.parse_args()
    for result in run(apply=args.apply):
        print(result)
