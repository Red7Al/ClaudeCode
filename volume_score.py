"""VolumeScore — a 0–12 confirmation score for a squeeze breakout (user 2026-07-24, ToDo P-02 L49).

The scoring recipe (from the request, verbatim on the points):

    Strong squeeze                     +3
    RVOL > 1.8                         +2
    Breakout volume highest in 20 bars +2
    Above VWAP                         +1
    OBV confirms                       +1
    Break into LVN (low-volume node)   +2
    ATR expanding                      +1
                                       ---
    maximum                             12   → "only trade if score >= 8"

Everything here is derived from DAILY OHLCV bars — the only history the engine stores
(price_history: bar_date, high, low, close, volume). That constrains two components to
sensible daily approximations, documented at each one:

* "Above VWAP" — there is no intraday tape, so we use a 20-bar rolling VWAP of the daily
  typical price ((H+L+C)/3) weighted by volume, and read it DIRECTION-AWARE: a BULL break
  wants the close above VWAP, a BEAR break below it (i.e. on the favourable side).
* "Break into LVN" — we build a volume-by-price profile over the lookback and pass the
  component when the breakout close lands in a low-volume node (a price bin whose traded
  volume is in the bottom third), i.e. price is breaking into thin air with little overhead
  supply/demand to fight.

"Strong squeeze" is not visible in OHLCV alone, so the caller supplies it (from the funnel's
tightness / quality) — see `squeeze_strong`. When a value is unknown or an instrument carries
no real volume (FX, indices), that component scores 0 and is marked na, never fabricated.

Pure, dependency-free (matches hvf_web/server.py::_rvol_at), and never raises: any component
that cannot be computed contributes 0 with a note, so the total is always a valid 0–12.
"""

from __future__ import annotations

RVOL_BARS = 20              # mirrors hvf_web/server.py::RVOL_BARS — keep in lockstep
RVOL_MIN = 1.8              # the request's threshold
VWAP_BARS = 20             # rolling window for the daily-VWAP approximation
OBV_BARS = 20              # window over which OBV must confirm the break direction
ATR_PERIOD = 14            # ATR window; "expanding" compares the last period vs the one before
LVN_BINS = 20              # price bins for the volume-by-price profile
LVN_LOOKBACK = 60          # bars of profile history behind the break
LVN_BOTTOM_FRACTION = 1 / 3.0   # a node is "low volume" if in the bottom third of bin volumes

MAX_SCORE = 12
PASS_THRESHOLD = 8

# Points per component, in display order.
_POINTS = {
    "strong_squeeze": 3,
    "rvol": 2,
    "breakout_vol_top20": 2,
    "above_vwap": 1,
    "obv_confirms": 1,
    "break_into_lvn": 2,
    "atr_expanding": 1,
}
_LABELS = {
    "strong_squeeze": "Strong squeeze",
    "rvol": "RVOL > 1.8",
    "breakout_vol_top20": "Breakout volume highest in 20 bars",
    "above_vwap": "Above VWAP",
    "obv_confirms": "OBV confirms",
    "break_into_lvn": "Break into LVN",
    "atr_expanding": "ATR expanding",
}


def _bar_index(bars, trigger_date):
    """Index of the trigger bar in ascending `bars`, or None."""
    return next((k for k, b in enumerate(bars) if b[0] == trigger_date), None)


def _rvol_at(bars, i):
    """RVOL on bar index `i` = its volume / mean volume of the RVOL_BARS bars before it.
    Mirrors hvf_web/server.py::_rvol_at so the RVOL component reconciles with the Scanner
    column. None when there is no real volume to average against."""
    if i is None or i >= len(bars):
        return None
    vol = bars[i][4]
    prior = [b[4] for b in bars[max(0, i - RVOL_BARS):i] if b[4]]
    if not vol or len(prior) < 5:
        return None
    avg = sum(prior) / len(prior)
    return round(vol / avg, 2) if avg > 0 else None


def _breakout_vol_top20(bars, i):
    """True when the trigger bar's volume is the highest of the last 20 bars (incl. itself)."""
    vol = bars[i][4]
    if not vol:
        return None
    window = [b[4] for b in bars[max(0, i - RVOL_BARS + 1):i + 1] if b[4]]
    if len(window) < 5:
        return None
    return vol >= max(window)


def _above_vwap(bars, i, bull):
    """Direction-aware close vs a VWAP_BARS rolling VWAP of the daily typical price."""
    window = bars[max(0, i - VWAP_BARS + 1):i + 1]
    tot_v = sum(b[4] for b in window if b[4])
    if tot_v <= 0:
        return None
    tp_v = sum(((b[1] + b[2] + b[3]) / 3.0) * b[4] for b in window if b[4] and None not in (b[1], b[2], b[3]))
    vwap = tp_v / tot_v
    close = bars[i][3]
    if close is None:
        return None
    return close > vwap if bull else close < vwap


def _obv_confirms(bars, i, bull):
    """OBV over the last OBV_BARS bars must move WITH the break: rising OBV for a BULL break,
    falling for a BEAR break. OBV change over the window = sum of signed volume."""
    window = bars[max(0, i - OBV_BARS + 1):i + 1]
    if len(window) < 5:
        return None
    obv = 0.0
    seen_vol = False
    for a, b in zip(window, window[1:]):
        pc, cc, v = a[3], b[3], b[4]
        if cc is None or pc is None or not v:
            continue
        seen_vol = True
        obv += v if cc > pc else (-v if cc < pc else 0)
    if not seen_vol:
        return None
    return obv > 0 if bull else obv < 0


def _atr_expanding(bars, i):
    """True-range ATR over the ATR_PERIOD bars ending at the trigger vs the period before it."""
    lo = i - 2 * ATR_PERIOD
    if lo < 0:
        return None
    trs = []
    for k in range(lo + 1, i + 1):
        h, l, pc = bars[k][1], bars[k][2], bars[k - 1][3]
        if None in (h, l, pc):
            trs.append(None)
            continue
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    recent = [t for t in trs[-ATR_PERIOD:] if t is not None]
    prior = [t for t in trs[:ATR_PERIOD] if t is not None]
    if len(recent) < ATR_PERIOD // 2 or len(prior) < ATR_PERIOD // 2:
        return None
    return (sum(recent) / len(recent)) > (sum(prior) / len(prior))


def _break_into_lvn(bars, i):
    """Volume-by-price profile over the LVN_LOOKBACK bars before the break: pass when the
    breakout close sits in a low-volume node (a price bin in the bottom third by volume)."""
    hist = bars[max(0, i - LVN_LOOKBACK):i]     # profile is history BEFORE the break
    close = bars[i][3]
    pts = [(b[3], b[4]) for b in hist if b[3] is not None and b[4]]
    if close is None or len(pts) < 20:
        return None
    prices = [p for p, _ in pts]
    lo, hi = min(prices), max(prices)
    if hi <= lo:
        return None
    width = (hi - lo) / LVN_BINS
    bins = [0.0] * LVN_BINS
    for p, v in pts:
        idx = min(LVN_BINS - 1, int((p - lo) / width))
        bins[idx] += v
    # Which bin does the breakout close fall in? (clamped into the profile range)
    cidx = min(LVN_BINS - 1, max(0, int((close - lo) / width)))
    occupied = sorted(v for v in bins if v > 0)
    if not occupied:
        return None
    cutoff = occupied[max(0, int(len(occupied) * LVN_BOTTOM_FRACTION) - 1)]
    return bins[cidx] <= cutoff


def volume_score(bars, trigger_date, bull, *, squeeze_strong=None):
    """Compute the VolumeScore for a breakout.

    bars          : [(bar_date, high, low, close, volume), ...] ascending daily bars, with
                    enough history before `trigger_date` for the 20/60-bar windows.
    trigger_date  : the bar the setup fired on (must match a bar_date in `bars`).
    bull          : True for a BULL setup, False for BEAR (drives VWAP / OBV direction).
    squeeze_strong: bool from the caller (funnel tightness / quality proxy) or None if unknown.

    Returns {"score", "max", "threshold", "pass", "components": [{key,label,points,got,earned,note}]}.
    `got` is True/False/None (None = could not be computed → 0 earned)."""
    comps = []
    i = _bar_index(bars, trigger_date)

    def add(key, got, note=""):
        pts = _POINTS[key]
        earned = pts if got is True else 0
        comps.append({"key": key, "label": _LABELS[key], "points": pts,
                      "got": got, "earned": earned, "note": note})

    if i is None:
        # No trigger bar in the window — every volume/price component is uncomputable.
        add("strong_squeeze", bool(squeeze_strong) if squeeze_strong is not None else None,
            "" if squeeze_strong is not None else "unknown")
        for k in ("rvol", "breakout_vol_top20", "above_vwap", "obv_confirms", "break_into_lvn", "atr_expanding"):
            add(k, None, "no trigger bar in history")
        return _assemble(comps)

    add("strong_squeeze", bool(squeeze_strong) if squeeze_strong is not None else None,
        "" if squeeze_strong is not None else "supplied by caller (tightness/quality)")

    rv = _rvol_at(bars, i)
    add("rvol", (rv > RVOL_MIN) if rv is not None else None,
        f"RVOL {rv}" if rv is not None else "no volume")

    add("breakout_vol_top20", _breakout_vol_top20(bars, i), "")
    add("above_vwap", _above_vwap(bars, i, bull), "20-bar daily VWAP, direction-aware")
    add("obv_confirms", _obv_confirms(bars, i, bull), "")
    add("break_into_lvn", _break_into_lvn(bars, i), "volume-by-price profile")
    add("atr_expanding", _atr_expanding(bars, i), "")
    return _assemble(comps)


def _assemble(comps):
    score = sum(c["earned"] for c in comps)
    return {"score": score, "max": MAX_SCORE, "threshold": PASS_THRESHOLD,
            "pass": score >= PASS_THRESHOLD, "components": comps}
