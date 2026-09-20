#!/usr/bin/env python
"""What this project is actually spending its Supabase egress allowance on.

WHY THIS EXISTS. Supabase Storage has been latched off since 2026-08-16 with exceed_egress_quota. The
cause was assumed for weeks to be snapshot downloads; measured on 2026-09-20 it was not even close --
snapshot Storage is ~0.1% of what price_history reads cost. The free tier allows 5 GB of egress a MONTH,
UNIFIED across Database, Auth, Storage and the shared pooler, and scoped to the ORGANISATION, so ordinary
query traffic is what exhausts it and Storage is merely what gets cut off.

THE TRAP THIS TOOL EXISTS TO AVOID. pg_stat_statements is a LIFETIME cumulative counter. Reading it once
gives an average over the whole life of the project, which silently blends traffic from code that has
since been fixed with traffic that is still happening. On 2026-09-20 that nearly caused a "fix" to a
query that commit 7db0d03 had already deleted on 2026-09-04 -- 118 million rows of it, all historical.
A single reading cannot tell you what is live. Only DIFFERENCING two readings can.

    ./.venv/Scripts/python.exe egress_report.py --baseline    # record a starting point
    ./.venv/Scripts/python.exe egress_report.py               # rate since that baseline
    ./.venv/Scripts/python.exe egress_report.py --width       # re-measure bytes/row, do not trust the constant

DELIBERATELY MANUAL. This repository's recurring defect is correct code that nothing ever calls, so to be
explicit: nothing schedules this and nothing should. It is an on-demand diagnostic, run when someone
wants to know where the allowance is going -- like run_price_history_prune.py, which must also never be
scheduled.

THE BASELINE FILE IS NOT IN GIT. .gitignore excludes *.json, so egress_baseline.json is local to
whichever machine recorded it and does NOT travel with the repository. That is why the baseline figures
are also written into the ChangeRequests register in prose -- if this file is lost, the register still
holds the numbers to difference against, and --baseline can simply be run again to restart the clock.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "egress_baseline.json")

# MEASURED, not assumed: bytes actually read off pg8000's buffered reader for the 6-column price_history
# shape, 2026-09-20. 20,000 rows -> 94.37 B/row; 100,000 -> 101.59; 200,000 -> 20,343,744 bytes = 101.72,
# converged. An earlier estimate of 88.0 understated the burn by 16%. This counts bytes at the
# APPLICATION layer, so TLS and TCP framing put real egress slightly higher: treat it as a floor.
BYTES_PER_ROW = 101.72
FREE_TIER_GB_PER_MONTH = 5

SHAPES = """
    case when query ilike '%%join (values%%' and query ilike '%%p.volume%%' then '_perf_bars (web/report bars)'
         when query ilike '%%join (values%%'                               then '_window_bars (replay bars)'
         when query ilike '%%COPY public.price_history%%'                  then 'pg_dump COPY (backup)'
         when query ilike '%%price_history%%'                              then 'other price_history'
         when query ilike '%%pg_timezone_names%%'                          then 'pg_timezone_names (not ours)'
         else 'everything else' end
"""


def _rows(db):
    return db.run(f"select {SHAPES} as shape, sum(rows) r, sum(calls) c "
                  "from pg_stat_statements group by 1 order by r desc")


def _snapshot(db):
    out = {}
    for shape, r, c in _rows(db):
        out[shape] = {"rows": int(r or 0), "calls": int(c or 0)}
    return out


def measure_width(db):
    """Re-derive bytes/row off the socket rather than trusting the constant above.

    THIS COSTS WHAT IT MEASURES: the three samples pull 320,000 rows, about 32 MB, roughly 0.6% of the
    monthly allowance. Cheap enough to confirm the constant occasionally, expensive enough that it must
    not be run in a loop or on a schedule."""
    raw = getattr(db, "_conn", None) or db
    buf = raw._sock
    seen = {"n": 0}
    original = buf.read

    def counted(size=-1):
        chunk = original(size)
        seen["n"] += len(chunk) if chunk else 0
        return chunk

    buf.read = counted
    try:
        for n in (20000, 100000, 200000):
            seen["n"] = 0
            got = db.run("select p.ticker, p.bar_date, p.high, p.low, p.close, p.volume "
                         "from price_history p order by p.ticker, p.bar_date limit :n", n=n)
            if got:
                print(f"  {len(got):>7,} rows -> {seen['n']:>12,} bytes = {seen['n'] / len(got):6.2f} bytes/row")
    finally:
        buf.read = original


def _gb(rows):
    return rows * BYTES_PER_ROW / 1024 ** 3


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", action="store_true", help="record the current counters as the starting point")
    ap.add_argument("--width", action="store_true", help="re-measure bytes/row off the wire")
    args = ap.parse_args()

    from db_pool import get_db
    db = get_db()
    try:
        if args.width:
            print("bytes actually received, 6-column price_history shape:")
            measure_width(db)
            return 0

        now = db.run("select now()")[0][0]
        current = _snapshot(db)

        if args.baseline:
            with open(BASELINE, "w", encoding="utf-8") as fh:
                json.dump({"at": now.isoformat(), "shapes": current}, fh, indent=2, sort_keys=True)
            total = sum(v["rows"] for v in current.values())
            print(f"baseline written {BASELINE}\n  at {now.isoformat()}\n  {total:,} rows across "
                  f"{len(current)} shapes")
            return 0

        if not os.path.exists(BASELINE):
            print("No baseline yet. Run with --baseline first, wait a day or two, then run again.",
                  file=sys.stderr)
            return 2

        with open(BASELINE, encoding="utf-8") as fh:
            base = json.load(fh)
        t0 = datetime.fromisoformat(base["at"])
        hours = (now - t0).total_seconds() / 3600.0
        if hours < 0.5:
            print(f"Only {hours:.2f}h since the baseline -- too short to mean anything. Wait longer.",
                  file=sys.stderr)
            return 2

        print(f"Egress since baseline {base['at']}  ({hours:.1f}h elapsed)")
        print(f"{'shape':<34}{'rows':>16}{'calls':>9}{'GB/30d':>10}{'share':>8}")
        deltas, total_rows = [], 0
        for shape, cur in current.items():
            was = base["shapes"].get(shape, {"rows": 0, "calls": 0})
            d_rows = cur["rows"] - was["rows"]
            d_calls = cur["calls"] - was["calls"]
            if d_rows > 0 or d_calls > 0:
                deltas.append((shape, d_rows, d_calls))
                total_rows += d_rows
        for shape, d_rows, d_calls in sorted(deltas, key=lambda x: -x[1]):
            gb30 = _gb(d_rows) / hours * 24 * 30
            share = 100.0 * d_rows / total_rows if total_rows else 0.0
            print(f"{shape:<34}{d_rows:>16,}{d_calls:>9,}{gb30:>10.2f}{share:>7.1f}%")
        gb30 = _gb(total_rows) / hours * 24 * 30
        print(f"{'TOTAL':<34}{total_rows:>16,}{'':>9}{gb30:>10.2f}")
        verdict = "WITHIN" if gb30 <= FREE_TIER_GB_PER_MONTH else "OVER"
        print(f"\n{verdict} the {FREE_TIER_GB_PER_MONTH} GB/month free allowance "
              f"({gb30 / FREE_TIER_GB_PER_MONTH:.2f}x).")
        print("Projected from the elapsed window only. A window that misses the 05:00 refresh or the "
              "18:30 scan will understate the real rate -- cover at least one full day.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
