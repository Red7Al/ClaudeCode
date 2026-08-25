# ======================================================================================================================
# File:         test_hvf_method.py
# Author:       Alex Hind
# Created:      2026-06-12
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# HVF method regression suite (user 2026-06-12: "any changes to prices, volumes etc must NOT negatively impact the
# correct calculation of the HVF method"). Fully OFFLINE and deterministic — no network, no live data:
#
#   Synthetic cases (constructed price paths with KNOWN correct answers):
#     1. Textbook bullish funnel        → must detect BULLISH at the exact H3 entry
#     2. Flat-top funnel (H2 ≈ H3)      → must detect (the RR.L fix, 2026-06-12)
#     3. Phantom-wick injection         → sanitiser must clip; detection identical to the clean series
#     4. Hammer-bottom lows             → must be swing-low pivots (the removed-filter fix, 2026-06-12)
#     5. Non-converging channel         → must NOT detect (convergence ≥ 0.70)
#     6. Stale H3 (> 60 bars old)       → must NOT detect
#     7. Invariant checker self-test    → corrupt results must be flagged
#
#   Frozen real-data fixtures (tests_fixtures/*.csv — raw Yahoo bars INCLUDING the phantom prints, captured 2026-06-12):
#     8. RR.L weekly  → BULLISH, entry ≈ 1330 (the colleague-verified funnel our scanner originally missed)
#     9. NVDA daily   → NO pattern (the false positive that died when the hammer filter was removed)
#    10. HIK.L daily  → BULLISH TRIGGERED (regression anchor for a known-good detection)
#    11. RR.L daily sanitiser → the four phantom bars (29-May 1,420 high; 990/1,051/1,107 lows) must be clipped
#
# Every emitted pattern is ALSO checked against price_action.check_hvf_invariants — the same geometry rules the
# production runtime guard enforces, so tests and production can never disagree about what "correct" means.
#
# Usage:
#   python test_hvf_method.py            # full suite (exit 0 = pass, 1 = fail)
#   python test_hvf_method.py --quick    # synthetic cases only (pre-commit speed, no file IO)
#
# CI: .github/workflows/trading-tests.yml runs this on every push touching detection code.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.1.0   2026-06-12  Alex Hind   Case 13 — official-method AMP1 exhaustion anchor (9a merged): re-anchors when the
#                                 window clips the true top, no-op when it doesn't, entry/stop never move.
# 1.1.0   2026-06-22  Alex Hind   Repointed to the clean RW engine (price_action 1.34.0): detect() runs hvf_clean via the
#                                 get_hvf_signal shim; case 2 flat-top now expects REJECTION; case 8x rebuilt as a real clean
#                                 bearish funnel (real L3); case 13 (exhaustion re-anchor) removed; frozen RR.L/HIK now expect
#                                 rejection (flat-top / synthetic L3 no longer detect). 21/21.
# 1.0.0   2026-06-12  Alex Hind   Initial build — cases covering every HVF defect found 2026-06-11/12.
# ======================================================================================================================

import os
import sys
import numpy as np
import pandas as pd

FIXDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests_fixtures")
PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


# ----------------------------------------------------------------------------------------------------------------------
# Synthetic price-path builder
# ----------------------------------------------------------------------------------------------------------------------

def build_frame(pivots: list, n_bars: int, start_level: float = 100.0, end_level: float = None) -> pd.DataFrame:
    """
    Piecewise-linear daily OHLCV frame through (bar_index, level) pivot points.
    Pivot bars become clean window extremes so _find_swing_highs_lows sees them.
    """
    pts = sorted(pivots, key=lambda p: p[0])
    if pts[0][0] != 0:
        pts = [(0, start_level)] + pts
    if pts[-1][0] != n_bars - 1:
        pts = pts + [(n_bars - 1, end_level if end_level is not None else pts[-1][1])]
    xs, ys = zip(*pts)
    path = np.interp(np.arange(n_bars), xs, ys)
    df = pd.DataFrame({
        "Open":   path,
        "High":   path + 0.6,
        "Low":    path - 0.6,
        "Close":  path,
        "Volume": np.full(n_bars, 1_000_000.0),
    }, index=pd.bdate_range(end="2026-06-12", periods=n_bars))
    return df


def detect(frame: pd.DataFrame, lookback: int = 220):
    """Run the production daily detector on a constructed frame (offline)."""
    import price_action as pa
    orig = pa._get_daily
    # Mirror production _get_daily exactly: the sanitiser runs on every fetch.
    pa._get_daily = lambda ticker, days=220: pa._sanitise_ohlc(frame.tail(days), ticker)
    try:
        r = pa.get_hvf_signal("SYNTH", lookback_days=lookback,
                              trend_hint={"signal": "UPTREND"})
    finally:
        pa._get_daily = orig
    return r


# Standard funnel geometry used by several cases:
# uptrend 100→192, then H1=192 > H2=186.5 > H3=183 against L1=170 < L2=176 < L3=178.5.
# Ceiling decline kept < 5% so the recent-trend override does not flip the
# pattern bearish, and initial range sized so R:R clears the 3.0:1 gate —
# both constraints are part of the detector's contract (verified by case 1).
FUNNEL = [(70, 192), (78, 170), (88, 186.5), (96, 176), (106, 183), (114, 178.5)]


def case_textbook():
    from price_action import check_hvf_invariants
    f = build_frame(FUNNEL, 126, end_level=181)
    r = detect(f)
    check("1a textbook funnel detected", r.get("hvf_type") == "BULLISH", str(r.get("hvf_type")))
    check("1b entry at H3 level", r.get("h3_level") is not None and abs(r["h3_level"] - 183.6) < 1.5,
          f"entry={r.get('h3_level')}")
    check("1c invariants clean", check_hvf_invariants(r) == [], str(check_hvf_invariants(r)))
    return r


def case_flat_top():
    # H3 (190.4) sits ABOVE H2 (190) — a FLAT/rising ceiling, not three strictly lower highs.
    # The clean RW ruleset (2026-06-22) requires strict H1>H2>H3 (the old flat-top tolerance is
    # GONE), so this shape is correctly REJECTED — a flat top is not a converging funnel.
    pivots = [(70, 200), (78, 155), (88, 190), (96, 170), (106, 190.4), (114, 180)]
    f = build_frame(pivots, 126, end_level=187)
    r = detect(f)
    check("2 flat-top funnel REJECTED (clean rule: strict lower highs)", not r.get("hvf_type"),
          f"type={r.get('hvf_type')}")


def case_phantom_wick():
    clean = build_frame(FUNNEL, 126, end_level=181)
    dirty = clean.copy()
    # Phantom prints mid-funnel: a fake high above H1 and a fake crash low —
    # exactly the RR.L 29-May/20-May failure mode.
    dirty.iloc[100, dirty.columns.get_loc("High")] = 230.0
    dirty.iloc[92,  dirty.columns.get_loc("Low")]  = 120.0
    r_clean, r_dirty = detect(clean), detect(dirty)
    check("3a phantom wicks do not change detection",
          r_dirty.get("hvf_type") == r_clean.get("hvf_type") == "BULLISH",
          f"clean={r_clean.get('hvf_type')} dirty={r_dirty.get('hvf_type')}")
    same_entry = (r_clean.get("h3_level") and r_dirty.get("h3_level")
                  and abs(r_clean["h3_level"] - r_dirty["h3_level"]) < 0.5)
    check("3b entry level unchanged by phantom wicks", bool(same_entry),
          f"clean={r_clean.get('h3_level')} dirty={r_dirty.get('h3_level')}")


def case_hammer_lows():
    from price_action import _find_swing_highs_lows
    f = build_frame(FUNNEL, 126, end_level=181)
    # Make every L-pivot a hammer: deep low, close near the high of the bar —
    # the shape the removed spike-wick filter wrongly excluded.
    for bar in (78, 96, 114):
        f.iloc[bar, f.columns.get_loc("Low")]   = f["Close"].iloc[bar] - 3.0
        f.iloc[bar, f.columns.get_loc("High")]  = f["Close"].iloc[bar] + 0.7
    _, lows = _find_swing_highs_lows(f, n=5)
    low_bars = {i for i, _ in lows}
    check("4 hammer-bottom lows are swing pivots", {78, 96, 114}.issubset(low_bars),
          f"found={sorted(low_bars)}")


def case_non_converging():
    pivots = [(70, 200), (78, 170), (88, 197), (96, 171), (106, 195), (114, 172)]
    f = build_frame(pivots, 126, end_level=185)
    r = detect(f)
    check("5 non-converging channel rejected", not r.get("hvf_type"), f"type={r.get('hvf_type')}")


def case_stale_h3():
    pivots = [(20, 200), (28, 170), (38, 190), (46, 176), (56, 183), (64, 178.5)]
    f = build_frame(pivots, 200, end_level=181)   # H3 at bar 56 of 200 → 143 bars old
    r = detect(f)
    check("6 stale H3 (>60 bars) rejected", not r.get("hvf_type"), f"type={r.get('hvf_type')}")


def case_bearish():
    """
    Bearish (inverted) funnel after a downtrend. Added 2026-06-12 after the META
    incident: every bearish result stores ENTRY in h3_level (= L3 by design), so
    a naive H3<=L3 invariant check suppressed a legitimate bearish setup in
    production. The suite previously had NO bearish detection case — this is it.
    """
    import price_action as pa
    from price_action import check_hvf_invariants
    # Downtrend 250→ into a converging funnel that ENDS with a REAL higher low L3 (the clean engine
    # needs a real post-H3 swing low — no synthetic L3). Lower highs 196>190>186; higher lows
    # 174<179<182; entry = break below L3 (182). AMP1 = 196-174 = 22; tightness (186-182)/22 = 0.18.
    # Prior DOWNTREND 250->170 (bar 60), then a converging funnel that ALTERNATES so every pivot is a
    # real swing: H1 196 (peak) / L1 174 / H2 190 / L2 179 / H3 186 / L3 182 (confirmed low, price
    # rises to ~184 after it). Lower highs 196>190>186; higher lows 174<179<182; AMP1 22; tight 0.18.
    pivots = [(60, 170), (70, 196), (78, 174), (88, 190), (98, 179), (108, 186), (118, 182)]
    pts = [(0, 250)] + pivots
    f = build_frame(pts, 126, end_level=184)
    orig = pa._get_daily
    pa._get_daily = lambda ticker, days=220: pa._sanitise_ohlc(f.tail(days), ticker)
    try:
        r = pa.get_hvf_signal("SYNTH", lookback_days=220,
                              trend_hint={"signal": "DOWNTREND"})
    finally:
        pa._get_daily = orig
    check("8x bearish funnel detected", r.get("hvf_type") == "BEARISH",
          f"type={r.get('hvf_type')}")
    if r.get("hvf_type") == "BEARISH":
        check("8y bearish entry==L3 by design, invariants must accept it",
              check_hvf_invariants(r) == [], str(check_hvf_invariants(r)))
        check("8z bearish order: target < entry < stop",
              r.get("target") < r.get("h3_level") < r.get("stop_level"),
              f"t={r.get('target')} e={r.get('h3_level')} s={r.get('stop_level')}")


def case_absurd_target():
    """
    ETHUSD 2026-06-12: a bearish funnel low on the chart after a huge prior
    range projected target −606 (Hunt's full-AMP1 below zero) and alerted every
    5-minute scan. Such projections cannot physically complete and must be
    REJECTED AT DETECTION — never emitted, never alerted.
    """
    import price_action as pa
    # Prior collapse 1000→~150, funnel at the lows: AMP1 (≈300) dwarfs price.
    pivots = [(0, 1000), (70, 400), (78, 100), (88, 250), (96, 140), (106, 180), (114, 150)]
    f = build_frame(pivots, 126, end_level=165)
    orig = pa._get_daily
    pa._get_daily = lambda ticker, days=220: pa._sanitise_ohlc(f.tail(days), ticker)
    try:
        r = pa.get_hvf_signal("SYNTH", lookback_days=220,
                              trend_hint={"signal": "DOWNTREND"})
    finally:
        pa._get_daily = orig
    ok = not r.get("hvf_type") or (r.get("target") or 1) > 0
    check("12 absurd bearish target (ETHUSD class) rejected at detection", ok,
          f"type={r.get('hvf_type')} target={r.get('target')}")


def case_invariant_selftest():
    from price_action import check_hvf_invariants
    bad = {"hvf_type": "BEARISH", "hvf_signal": "READY", "h1_level": 100, "l1_level": 80,
           "h3_level": 90, "l3_level": 85, "stop_level": 90.2, "target": -13.9,
           "risk_reward": 19.0, "convergence": 0.25}
    v = check_hvf_invariants(bad)
    check("7 invariant checker flags negative target (OCDO.L class)",
          any("target" in x for x in v), str(v))
    too_far = dict(bad, target=1.0, risk_reward=10.6)
    v = check_hvf_invariants(too_far)
    check("7b invariant checker rejects extreme R:R", any("risk_reward" in x for x in v), str(v))


# ----------------------------------------------------------------------------------------------------------------------
# Frozen real-data fixture cases
# ----------------------------------------------------------------------------------------------------------------------

def _load(name):
    df = pd.read_csv(os.path.join(FIXDIR, f"{name}.csv"), index_col=0, parse_dates=True)
    return df


def fixture_cases():
    import price_action as pa
    import yfinance

    daily = {"RR.L": _load("rrl_daily"), "HIK.L": _load("hikl_daily"),
             "NVDA": _load("nvda_daily"), "MONY.L": _load("monyl_daily")}
    weekly = {"RR.L": _load("rrl_weekly")}

    class FakeTicker:
        def __init__(self, t): self.t = t
        def history(self, period=None, interval="1d"):
            src = weekly if interval == "1wk" else daily
            if self.t not in src:
                return pd.DataFrame()
            return src[self.t].copy()

    orig_ticker = yfinance.Ticker
    yfinance.Ticker = FakeTicker
    try:
        from price_action import check_hvf_invariants

        # Clean RW ruleset (2026-06-22): RR.L's frozen shape is a FLAT ceiling (1,328/1,330/1,337 —
        # not three strictly lower highs), so it is correctly REJECTED — the old flat-top tolerance
        # that detected it is gone.
        r = pa.get_hvf_signal_mtf("RR.L", trend_hint={"signal": "UPTREND"})
        check("8a RR.L frozen: flat-top REJECTED (clean rule: strict lower highs)",
              not r.get("hvf_type"), f"type={r.get('hvf_type')}")
        check("8c RR.L frozen: invariants clean (empty result)", check_hvf_invariants(r) == [],
              str(check_hvf_invariants(r)))

        r = pa.get_hvf_signal_mtf("NVDA", trend_hint={"signal": "UPTREND"})
        check("9 NVDA frozen: false positive stays dead", not r.get("hvf_type"),
              f"type={r.get('hvf_type')}")

        # Clean RW ruleset: HIK's frozen funnel relied on a SYNTHETIC L3 (no real post-H3 swing
        # low), which the clean engine forbids → correctly REJECTED (a fabricated pivot is not a
        # confirmed swing).
        r = pa.get_hvf_signal_mtf("HIK.L", trend_hint={"signal": "UPTREND"})
        check("10 HIK.L frozen: synthetic-L3 setup REJECTED (clean rule: real L3 only)",
              not r.get("hvf_type"), f"type={r.get('hvf_type')} sig={r.get('hvf_signal')}")

        s = pa._sanitise_ohlc(daily["RR.L"].copy(), "RR.L")
        check("11a RR.L sanitiser: phantom 1,420 high clipped",
              float(s.loc["2026-05-29", "High"]) < 1400,
              f"high={float(s.loc['2026-05-29', 'High'])}")
        check("11b RR.L sanitiser: phantom 990 low clipped",
              float(s.loc["2026-05-20", "Low"]) > 1100,
              f"low={float(s.loc['2026-05-20', 'Low'])}")
        check("11c RR.L sanitiser: phantom 1,107 low clipped",
              float(s.loc["2026-06-02", "Low"]) > 1200,
              f"low={float(s.loc['2026-06-02', 'Low'])}")
    finally:
        yfinance.Ticker = orig_ticker


def main():
    quick = "--quick" in sys.argv
    print("HVF method regression suite" + (" (quick: synthetic only)" if quick else ""))
    print("— synthetic cases —")
    case_textbook()
    case_flat_top()
    case_phantom_wick()
    case_hammer_lows()
    case_non_converging()
    case_stale_h3()
    case_bearish()
    case_absurd_target()
    case_invariant_selftest()
    if not quick:
        print("— frozen fixture cases —")
        fixture_cases()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


def test_hvf_regression_suite():
    """Expose the complete offline regression suite to pytest collection."""
    PASS.clear()
    FAIL.clear()
    case_textbook()
    case_flat_top()
    case_phantom_wick()
    case_hammer_lows()
    case_non_converging()
    case_stale_h3()
    case_bearish()
    case_absurd_target()
    case_invariant_selftest()
    fixture_cases()
    assert not FAIL, "HVF regressions failed: " + ", ".join(FAIL)


if __name__ == "__main__":
    main()


# ======================================================================================================
# Documentation drift guard (found 2026-08-25).
#
# docs/SQUEEZE_METHOD.md stated MIN_PUBLISH_QUALITY = 70 for two months and three days after the user
# lowered it to 25 on 2026-06-22 -- while the same document asserted that its thresholds "are read from
# price_action.py and config.py at runtime". True of the code; the prose does not follow automatically.
# That value decides what is published PUBLICLY to X, so a reader trusting the doc would have believed
# the gate roughly three times tighter than it is.
#
# Correcting the number alone would drift again. This asserts the two agree.
# ======================================================================================================

def test_squeeze_method_doc_states_the_live_publication_floor():
    import re
    from pathlib import Path

    import config

    doc = Path(__file__).with_name("docs").joinpath("SQUEEZE_METHOD.md").read_text(encoding="utf-8")
    stated = re.search(r"`MIN_PUBLISH_QUALITY`,\s*currently\s*\*\*(\d+)\*\*", doc)

    assert stated, "SQUEEZE_METHOD.md must state the publication floor in a checkable form"
    assert int(stated.group(1)) == config.MIN_PUBLISH_QUALITY, (
        f"docs/SQUEEZE_METHOD.md says the publication floor is {stated.group(1)}, "
        f"config.py says {config.MIN_PUBLISH_QUALITY} — the doc drifted")


def test_squeeze_method_doc_states_the_live_risk_reward_floor():
    import re
    from pathlib import Path

    import config

    doc = Path(__file__).with_name("docs").joinpath("SQUEEZE_METHOD.md").read_text(encoding="utf-8")
    stated = re.search(r"currently\s*\*\*([\d.]+)\*\*\)", doc)

    assert stated, "SQUEEZE_METHOD.md must state MIN_RISK_REWARD in a checkable form"
    assert float(stated.group(1)) == float(config.MIN_RISK_REWARD), (
        f"docs/SQUEEZE_METHOD.md says the R:R floor is {stated.group(1)}, "
        f"config.py says {config.MIN_RISK_REWARD} — the doc drifted")
