# ======================================================================================================================
# File:         shadow_diff_amp1.py
# Author:       Alex Hind
# Created:      2026-06-12
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Shadow-diff for backlog 9a — the official-method AMP1 exhaustion anchor (user 2026-06-12). Compares, per setup, the
# LIVE target/R:R (in-window H1/L1) against the OFFICIAL-method target/R:R (price_action.compute_exhaustion_amp1, which
# anchors AMP1 at the prior trend's true exhaustion high + first natural-support pullback low). NOTHING is changed in
# the production detection path — this only REPORTS what would change, so the behaviour change can be reviewed before
# any merge (per the HVF correctness contract: suite green + universe shadow-diff before a detection change merges).
#
# Entry and stop are identical in both (funnel 3rd pivots); only the target AMPLITUDE differs. R:R is recomputed from
# the same entry/stop with the official target.
#
# Usage:
#   python shadow_diff_amp1.py RR.L MONY.L OCI.L      # specific tickers
#   python shadow_diff_amp1.py 15                     # today's top 15 tradeable from hvf_scan_log
#   python shadow_diff_amp1.py                        # today's top 10
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.1.0   2026-06-14  Alex Hind   Code-review: ticker sort now uses price_action.hvf_weight() (single source of truth for
#                                 weight order). Behaviour identical for READY/TRIGGERED rows.
# 1.0.0   2026-06-12  Alex Hind   Initial build — shadow-diff only, no production wiring.
# ======================================================================================================================

import io
import sys
import logging

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
logging.basicConfig(level=logging.WARNING, format="%(message)s")
log = logging.getLogger("shadow_diff_amp1")


def _today_tradeable(limit: int) -> list:
    from db_pool import get_db
    db = get_db()
    try:
        rows = db.run(
            """select distinct on (ticker) ticker, hvf_signal, pattern_quality
                 from hvf_scan_log
                where scan_time::date = current_date
                  and hvf_signal in ('READY', 'TRIGGERED')
                order by ticker, recorded_at desc""")
    finally:
        db.close()
    from price_action import hvf_weight          # (ticker, hvf_signal, pattern_quality)
    rows.sort(key=lambda r: hvf_weight(r[1], r[2]))
    return [r[0] for r in rows[:limit]]


def main():
    args = sys.argv[1:]
    if args and not args[0].isdigit():
        tickers = args
    else:
        tickers = _today_tradeable(int(args[0]) if args else 10)

    from price_action import get_hvf_signal_mtf, get_trend_structure, compute_exhaustion_amp1

    print(f"{'Ticker':<9}{'TF':<11}{'Dir':<8}{'Entry':>9}{'Stop':>9}"
          f"{'Tgt(now)':>10}{'RR(now)':>8}{'Tgt(off)':>10}{'RR(off)':>8}  Δ")
    print("-" * 96)
    material = 0
    for t in tickers:
        try:
            r = get_hvf_signal_mtf(t, trend_hint=get_trend_structure(t))
            if not r.get("hvf_type"):
                print(f"{t:<9}no pattern"); continue
            r["ticker"] = t
            entry = r["h3_level"] if r["hvf_type"] == "BULLISH" else r["l3_level"]
            stop  = r["stop_level"]
            risk  = abs(entry - stop) if (entry is not None and stop is not None) else None
            rr_now = r.get("risk_reward")
            off = compute_exhaustion_amp1(t, r)
            if not off or risk in (None, 0):
                print(f"{t:<9}{r.get('hvf_timeframe',''):<11}{r['hvf_type']:<8}"
                      f"{entry:>9.2f}{stop:>9.2f}{(r.get('target') or 0):>10.2f}"
                      f"{(rr_now or 0):>8.1f}{'—':>10}{'—':>8}  (no exhaustion calc)")
                continue
            rr_off = round(abs(off["target_official"] - entry) / risk, 2)
            d_rr = (rr_off - (rr_now or 0))
            flag = "MATERIAL" if abs(d_rr) >= 1.0 or abs(off["target_official"] - (r.get("target") or 0)) / max(entry, 1) > 0.03 else ""
            if flag:
                material += 1
            print(f"{t:<9}{r.get('hvf_timeframe',''):<11}{r['hvf_type']:<8}"
                  f"{entry:>9.2f}{stop:>9.2f}{(r.get('target') or 0):>10.2f}{(rr_now or 0):>8.1f}"
                  f"{off['target_official']:>10.2f}{rr_off:>8.1f}  {flag}")
        except Exception as e:
            print(f"{t:<9}ERROR {e}")
    print("-" * 96)
    print(f"{material} setups would change materially (ΔR:R ≥ 1.0 or target shift > 3% of entry)")
    print("Entry & stop are IDENTICAL in both; only the target amplitude (AMP1 anchor) differs.")


if __name__ == "__main__":
    main()
