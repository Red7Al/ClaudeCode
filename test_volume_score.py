"""Unit tests for volume_score.py (user 2026-07-24, ToDo P-02 L49)."""
import datetime as dt
import volume_score as vs


def _mk(n, base_price=100.0, vol=1000.0, drift=0.0):
    """n ascending daily bars; simple flat-ish series we then perturb in each test."""
    bars, d = [], dt.date(2026, 1, 1)
    p = base_price
    for k in range(n):
        p = base_price + drift * k
        h, l, c = p + 1, p - 1, p
        bars.append((d + dt.timedelta(days=k), h, l, c, vol))
    return bars


def test_max_score_bull_breakout():
    """A textbook BULL breakout should tick every component → 12, pass."""
    bars = _mk(80, base_price=100.0, vol=1000.0, drift=0.0)
    i = len(bars) - 1
    td = bars[i][0]
    # Expand ATR over the recent half: widen the daily range for the last 14 bars.
    for k in range(i - 13, i + 1):
        d, h, l, c, v = bars[k]
        mid = c
        bars[k] = (d, mid + 5, mid - 5, c, v)
    # Break into fresh highs (LVN above the prior range) with a volume spike on the last bar.
    d, h, l, c, v = bars[i]
    bars[i] = (d, 130, 118, 128, 6000)      # gaps well above the ~100 range, huge volume
    # Rising OBV: last few closes step up so signed volume accrues positive.
    for k in range(i - 4, i):
        dd, hh, ll, cc, vv = bars[k]
        bars[k] = (dd, hh + 2, ll + 2, cc + 2 * (k - (i - 5)), vv)
    r = vs.volume_score(bars, td, True, squeeze_strong=True)
    got = {c["key"]: c["got"] for c in r["components"]}
    assert got["strong_squeeze"] is True
    assert got["rvol"] is True, got
    assert got["breakout_vol_top20"] is True, got
    assert got["above_vwap"] is True, got
    assert got["obv_confirms"] is True, got
    assert got["break_into_lvn"] is True, got
    assert got["atr_expanding"] is True, got
    assert r["score"] == 12 and r["pass"] is True, r


def test_low_score_no_volume_confirmation():
    """Flat volume, no spike, squeeze weak → well below the pass threshold."""
    bars = _mk(80, base_price=100.0, vol=1000.0, drift=0.0)
    i = len(bars) - 1
    td = bars[i][0]
    r = vs.volume_score(bars, td, True, squeeze_strong=False)
    assert r["score"] < vs.PASS_THRESHOLD, r
    assert r["pass"] is False


def test_rvol_component_reconciles_with_server_formula():
    """The RVOL component must use the SAME formula as hvf_web/server.py::_rvol_at."""
    bars = _mk(30, vol=1000.0)
    i = len(bars) - 1
    # Set the trigger bar to exactly 2.0x the 20-bar prior average (all priors are 1000).
    d, h, l, c, _ = bars[i]
    bars[i] = (d, h, l, c, 2000)
    rv = vs._rvol_at(bars, i)
    assert rv == 2.0, rv
    r = vs.volume_score(bars, bars[i][0], True, squeeze_strong=None)
    rc = next(x for x in r["components"] if x["key"] == "rvol")
    assert rc["got"] is True and "2.0" in rc["note"], rc


def test_missing_trigger_bar_is_safe():
    bars = _mk(40)
    r = vs.volume_score(bars, dt.date(2099, 1, 1), True, squeeze_strong=True)
    assert r["score"] == 3           # only the caller-supplied squeeze scores
    assert r["max"] == 12


def test_bear_direction_flips_vwap():
    """For a BEAR break, 'Above VWAP' passes when price is BELOW vwap."""
    bars = _mk(60, base_price=100.0, vol=1000.0)
    i = len(bars) - 1
    d, h, l, c, v = bars[i]
    bars[i] = (d, 80, 70, 72, v)     # closes well below the ~100 rolling VWAP
    r = vs.volume_score(bars, bars[i][0], False, squeeze_strong=None)
    av = next(x for x in r["components"] if x["key"] == "above_vwap")
    assert av["got"] is True, av


if __name__ == "__main__":
    import sys, traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            fails += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
