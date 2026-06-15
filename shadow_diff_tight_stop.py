# ======================================================================================================================
# File:         shadow_diff_tight_stop.py
# Author:       Alex Hind
# Created:      2026-06-15
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# SHADOW-DIFF for backlog #9b — the tight-stop flag. Before the behaviour change (skip trading a funnel whose stop is
# closer than config.TIGHT_STOP_MIN_PCT of price) goes live, this shows its BLAST RADIUS against real recent scans:
# which instruments WOULD be flagged, how tight their stop is, and whether they were tradeable (READY/TRIGGERED) or only
# DEVELOPING. Read-only — it queries hvf_scan_log (which stores entry_level/stop_level), computes stop% exactly as the
# detector now does, and prints the verdict. No writes, no trades, no posting.
#
# Per the HVF correctness contract, a detection/behaviour change must be shadow-diffed and reviewed before merge.
#
# Usage:
#   python shadow_diff_tight_stop.py            # last 7 days of scans
#   python shadow_diff_tight_stop.py 1          # last 1 day
#   python shadow_diff_tight_stop.py 30         # last 30 days
#
# Environment Variables Required:
#   SUPABASE_USER / SUPABASE_DB_PASSWORD
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-15  Alex Hind   Initial build — blast-radius review for the tight-stop flag (#9b) from hvf_scan_log.
# ======================================================================================================================

import io
import sys
import logging

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

from config import TIGHT_STOP_MIN_PCT


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 7

    from db_pool import get_db
    db = get_db()
    try:
        rows = db.run(
            """select distinct on (ticker, hvf_timeframe)
                      ticker, index_name, hvf_signal, hvf_timeframe,
                      entry_level, stop_level, risk_reward, pattern_quality
                 from hvf_scan_log
                where recorded_at >= now() - (:d || ' days')::interval
                  and entry_level is not null and stop_level is not null
                  and entry_level <> 0
                order by ticker, hvf_timeframe, recorded_at desc""", d=days)
    finally:
        db.close()

    flagged = []   # (ticker, index, signal, tf, stop_pct, rr, quality)
    tradeable_total = 0
    for tk, idx, sig, tf, entry, stop, rr, q in rows:
        stop_pct = abs(float(entry) - float(stop)) / abs(float(entry)) * 100.0
        if sig in ("READY", "TRIGGERED"):
            tradeable_total += 1
        if stop_pct < TIGHT_STOP_MIN_PCT:
            flagged.append((tk, idx, sig, tf, stop_pct, rr, q))

    # Weight order: tradeable first, then tightest stop first (most dangerous).
    flagged.sort(key=lambda r: (r[2] not in ("READY", "TRIGGERED"), r[4]))

    print(f"\nTIGHT-STOP SHADOW-DIFF — last {days} day(s)   (threshold {TIGHT_STOP_MIN_PCT}% of price)")
    print("=" * 96)
    print(f"Scanned patterns examined : {len(rows)}")
    print(f"  of which tradeable      : {tradeable_total} (READY/TRIGGERED)")
    print(f"Would be FLAGGED tight    : {len(flagged)}")
    n_trade = sum(1 for f in flagged if f[2] in ("READY", "TRIGGERED"))
    print(f"  of which tradeable      : {n_trade}  ← these would be SKIPPED from trading (still reported, labelled)")
    print("-" * 96)
    if not flagged:
        print("No setups breach the tight-stop floor in this window — zero blast radius.")
        return
    print(f"{'TICKER':12} {'MARKET':10} {'SIGNAL':11} {'TF':10} {'STOP%':>7} {'R:R':>6} {'Q':>4}")
    print("-" * 96)
    for tk, idx, sig, tf, stop_pct, rr, q in flagged:
        rr_s = f"{float(rr):.1f}" if rr is not None else "—"
        print(f"{tk:12} {(idx or '')[:10]:10} {sig:11} {(tf or ''):10} "
              f"{stop_pct:6.3f}% {rr_s:>6} {q if q is not None else '—':>4}")
    print("-" * 96)
    print("Tradeable flagged setups would no longer be traded (no INSUFFICIENT geometry, no daily missed-trade alert);")
    print("they remain visible in the HVF report, labelled 'stop too tight for IG intraday'. DEVELOPING ones never")
    print("traded anyway — the label is purely informational. Review this list before merging the behaviour change.")


if __name__ == "__main__":
    main()
