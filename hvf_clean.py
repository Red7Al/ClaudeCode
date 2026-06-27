# ======================================================================================================================
# File:         hvf_clean.py
# Author:       Alex Hind
# Created:      2026-06-22
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# CLEAN Hunt Volatility Funnel detection — Francis Hunt / RW 5-rule method, ONE implementation for both daily and
# weekly bars (user 2026-06-22: "rebuild to the clean RW ruleset", "funnel logic in ONE place", "remove fuzzy areas").
#
# Deliberately DROPS the heuristics that accumulated in price_action.get_hvf_signal / _run_hvf_on_hist:
#   - synthetic L3 (a fabricated third pivot at spot)        -> L3 must be a REAL swing low
#   - flat-top tolerance (h3 <= h2 * 1.005, l >= l*0.995)    -> strict alternating swings
#   - recent-trend override Methods A/B (rising-highs flip)  -> direction comes ONLY from the (medium-term-aware) trend
#
# The five rules (all must pass):
#   1. Clear prior trend: |move| >= MIN_PRIOR_TREND_PCT, and the trade direction MATCHES it (bull->long, bear->short).
#   2. Three alternating swings (REAL candle pivots): lower highs H1>H2>H3 AND higher lows L1<L2<L3, interleaved.
#   3. Tightness: (H3 - L3) / AMP1 <= HVF_TIGHTNESS_MAX (RW = 0.35).
#   4. Levels: AMP1 = H1 - L1; Mid = (H3+L3)/2; entry = H3 (long) / L3 (short); stop beyond the opposite 3rd pivot;
#      target = Mid + AMP1 (long) / Mid - AMP1 (short).
#   5. R:R = |target-entry| / |entry-stop| >= MIN_RISK_REWARD (3.0).
#   KLOS: the 52-week low/high are flagged (key levels of significance) so a short's floor / a long's ceiling is known.
#   Weekly-close confirmation: a setup is READY until price closes beyond the entry pivot; TRIGGERED on that close.
#
# NOT yet wired into production — built alongside get_hvf_signal_mtf for verification + shadow-diff before cut-over.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 0.4.0   2026-06-27  Alex Hind   (user 2026-06-27) current_price now uses the last NON-NaN close — a forming/holiday bar
#                                 came back NaN and was published as SBUX "now: nan". Rejects if no valid closes.
# 0.3.0   2026-06-23  Alex Hind   (user 2026-06-23) Emit the pivot DATES (h1_date..l3_date) + H2/L2 levels again — the X card
#                                 needs them to draw the H1->H2->H3 / L1->L2->L3 funnel overlay (lost in the 0.2.0 cutover).
# 0.2.0   2026-06-22  Alex Hind   WIRED IN — get_hvf_signal_mtf now calls detect_hvf for every timeframe (price_action 1.34.0);
#                                 the old daily/weekly detectors were deleted. Added volume_confirmed key for consumer parity.
# 0.1.0   2026-06-22  Alex Hind   Initial clean-room build (standalone, not wired in). RW 5 rules + KLOS, no synthetic L3,
#                                 no flat-top tolerance, no Method-A/B override; one detect() for daily + weekly.
# ======================================================================================================================

import logging

log = logging.getLogger("hvf_clean")

# Rule thresholds — single source of truth (mirrors config where one already exists).
from config import MIN_RISK_REWARD, MIN_PRIOR_TREND_PCT
HVF_TIGHTNESS_MAX = 0.35     # Rule 3 — (H3-L3)/AMP1; RW's compression ceiling (stricter than AH's 0.70 convergence)
_FRESH_MAX_DAILY  = 60       # H3 within this many bars
_FRESH_MAX_WEEKLY = 40
_MIN_SPAN_DAILY   = 10       # H1->H3 span
_MIN_SPAN_WEEKLY  = 4


def _empty():
    return {
        "hvf_type": None, "hvf_signal": None, "h3_level": None, "l3_level": None,
        "stop_level": None, "target": None, "risk_reward": None, "h1_level": None,
        "h2_level": None, "l1_level": None, "l2_level": None, "pattern_range": None,
        "bars_since_h3": None, "pattern_quality": 0, "convergence": None, "tightness": None,
        "volume_confirmed": False, "current_price": None, "klos_low": None, "klos_high": None,
        "h1_date": None, "h2_date": None, "h3_date": None,
        "l1_date": None, "l2_date": None, "l3_date": None, "reject_reason": None,
    }


def detect_hvf(ticker: str, hist, trend_signal: str, *, weekly: bool = False) -> dict:
    """Clean RW HVF detection on a pre-fetched OHLC frame. `trend_signal` is the
    (medium-term-aware) get_trend_structure result — direction comes ONLY from it.
    Returns the standard HVF result dict (compatible with the existing consumers)."""
    from price_action import _find_swing_highs_lows

    r = _empty()
    if hist is None or hist.empty or len(hist) < (8 if weekly else 60):
        r["reject_reason"] = "insufficient history"
        return r

    # Last NON-NaN close — a forming/holiday bar can be NaN (user 2026-06-27: SBUX "now" = nan);
    # iloc[-1] alone published that NaN as the current price.
    _closes = hist["Close"].dropna()
    if _closes.empty:
        r["reject_reason"] = "no valid close prices"
        return r
    current = float(_closes.iloc[-1])
    r["current_price"] = round(current, 6)

    # KLOS — 52-week (≈52 weekly / 252 daily) low & high. Key levels of significance.
    _win = hist.tail(52 if weekly else 252)
    r["klos_low"]  = round(float(_win["Low"].min()), 6)
    r["klos_high"] = round(float(_win["High"].max()), 6)

    # Rule 1 — clear prior trend, direction from the (medium-term-aware) trend signal ONLY.
    if trend_signal in ("STRONG_UPTREND", "UPTREND"):
        bullish = True
    elif trend_signal in ("STRONG_DOWNTREND", "DOWNTREND"):
        bullish = False
    else:
        r["reject_reason"] = f"no clear prior trend ({trend_signal})"
        return r

    fresh_max = _FRESH_MAX_WEEKLY if weekly else _FRESH_MAX_DAILY
    min_span  = _MIN_SPAN_WEEKLY  if weekly else _MIN_SPAN_DAILY
    n_small   = 2 if weekly else 3

    # Search both swing windows (±5 and ±n_small bars) for the best valid funnel.
    best = None
    swing_sets = [_find_swing_highs_lows(hist, n=5), _find_swing_highs_lows(hist, n=n_small)]
    for swing_highs, swing_lows in swing_sets:
        if len(swing_highs) < 3 or len(swing_lows) < 3:
            continue
        highs = swing_highs[-10:]
        lows  = swing_lows[-10:]
        for hi in range(len(highs)):
            for hj in range(hi + 1, len(highs)):
                for hk in range(hj + 1, len(highs)):
                    h1, h2, h3 = highs[hi], highs[hj], highs[hk]
                    # Rule 2 — STRICT lower highs (no flat-top tolerance).
                    if not (h1[1] > h2[1] > h3[1]):
                        continue
                    bars_since_h3 = len(hist) - 1 - h3[0]
                    if bars_since_h3 > fresh_max or (h3[0] - h1[0]) < min_span:
                        continue
                    lows_12 = [l for l in lows if h1[0] < l[0] < h2[0]]
                    lows_23 = [l for l in lows if h2[0] < l[0] < h3[0]]
                    lows_3p = [l for l in lows if l[0] > h3[0]]
                    if not (lows_12 and lows_23 and lows_3p):
                        continue
                    l1 = min(lows_12, key=lambda x: x[1])
                    l2 = min(lows_23, key=lambda x: x[1])
                    # Rule 2 — STRICT higher lows (real pivots; L3 is a real post-H3 swing low, never synthetic).
                    l3 = max(lows_3p, key=lambda x: x[0])
                    if not (l1[1] < l2[1] < l3[1] < h3[1]):
                        continue
                    amp1 = h1[1] - l1[1]
                    cur_range = h3[1] - l3[1]
                    if amp1 <= 0 or cur_range <= 0:
                        continue
                    tightness = cur_range / amp1
                    # Rule 3 — compression.
                    if tightness > HVF_TIGHTNESS_MAX:
                        continue
                    # Quality: tighter + fresher + more symmetric = better (0-100).
                    q = int((1 - tightness / HVF_TIGHTNESS_MAX) * 50) + max(0, 30 - bars_since_h3)
                    s12, s23 = h2[0] - h1[0], h3[0] - h2[0]
                    if s12 > 0:
                        q += int(min(s12, s23) / max(s12, s23) * 20)
                    q = min(100, q)
                    if best is None or q > best["q"]:
                        best = {"h1": h1, "h2": h2, "h3": h3, "l1": l1, "l2": l2, "l3": l3,
                                "amp1": amp1, "tightness": tightness, "bars_since_h3": bars_since_h3, "q": q}

    if best is None:
        r["reject_reason"] = "no valid 3-swing converging funnel"
        return r

    h1, h2, h3 = best["h1"], best["h2"], best["h3"]
    l1, l2, l3 = best["l1"], best["l2"], best["l3"]
    amp1 = best["amp1"]
    mid  = (h3[1] + l3[1]) / 2.0

    # Rule 4 — levels.
    if bullish:
        hvf_type, entry = "BULLISH", round(h3[1], 6)
        stop   = round(l3[1] * 0.998, 6)
        target = round(mid + amp1, 6)
        triggered = current > h3[1]
    else:
        hvf_type, entry = "BEARISH", round(l3[1], 6)
        stop   = round(h3[1] * 1.002, 6)
        target = round(mid - amp1, 6)
        triggered = current < l3[1]
    if target <= 0:
        r["reject_reason"] = "non-positive target"
        return r

    # Rule 5 — R:R.
    risk = abs(entry - stop)
    rr = round(abs(target - entry) / risk, 2) if risk > 0 else 0.0
    signal = "DEVELOPING" if rr < MIN_RISK_REWARD else ("TRIGGERED" if triggered else "READY")

    # Pivot DATES + the H2/L2 levels are needed by the X card to DRAW the H1->H2->H3 / L1->L2->L3
    # funnel overlay (user 2026-06-22: the visual went missing when detection moved to the clean
    # engine — it wasn't emitting these). Map each pivot's bar index back to its calendar date.
    def _pdate(piv):
        try:
            return hist.index[piv[0]].strftime("%Y-%m-%d")
        except Exception:
            return None
    r.update({
        "hvf_type": hvf_type, "hvf_signal": signal, "h3_level": entry, "l3_level": round(l3[1], 6),
        "stop_level": stop, "target": target, "risk_reward": rr, "h1_level": round(h1[1], 6),
        "h2_level": round(h2[1], 6), "l1_level": round(l1[1], 6), "l2_level": round(l2[1], 6),
        "pattern_range": round(amp1, 6), "bars_since_h3": best["bars_since_h3"],
        "pattern_quality": best["q"], "tightness": round(best["tightness"], 3),
        "convergence": round(best["tightness"], 3),
        "h1_date": _pdate(h1), "h2_date": _pdate(h2), "h3_date": _pdate(h3),
        "l1_date": _pdate(l1), "l2_date": _pdate(l2), "l3_date": _pdate(l3),
    })
    return r
