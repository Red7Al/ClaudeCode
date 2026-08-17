# ======================================================================================================
# Backtest integrity — properties every price-path replay must satisfy.
#
# WHY THIS EXISTS. On 2026-08-17 two separate replays I wrote produced flattering, wrong numbers:
#
#   * a split-position design measured +5.01% average return. The banked half scanned the whole forward
#     path for a target touch WITHOUT stopping at the hard stop, so it banked targets on trades that had
#     been stopped out days earlier. Corrected: +2.84%. A 76% overstatement, on a number that was about
#     to inform whether to change live order placement.
#   * an index-duplicate query grouped on columns while ignoring SORT DIRECTION, and proposed dropping
#     price_history's 168 MB PRIMARY KEY as redundant.
#
# Both were caught by eye. Neither had to be. The properties below are mechanical, cheap, and would have
# failed immediately on the first.
#
# THE CENTRAL PROPERTY IS TRUNCATION INVARIANCE. A replay that exits at bar k has, by definition, seen
# nothing after bar k. So appending bars after the exit, or truncating them, must not change the result.
# Any look-ahead breaks this the moment a future bar would have mattered. It is the cheapest general
# detector of the single most dangerous class of backtest bug there is.
# ======================================================================================================
import os

os.environ.setdefault("IG_API_KEY", "test")
os.environ.setdefault("IG_USERNAME", "test")
os.environ.setdefault("IG_PASSWORD", "test")
os.environ.setdefault("IG_ACCOUNT_ID", "test")
os.environ.setdefault("SUPABASE_USER", "test")
os.environ.setdefault("SUPABASE_DB_PASSWORD", "test")

import pytest  # noqa: E402
from hvf_web import server  # noqa: E402


def _bars(closes, spread=0.005):
    """(date, high, low, close) with a small intrabar range around each close."""
    return [(f"2026-01-{i + 1:02d}", c * (1 + spread), c * (1 - spread), c)
            for i, c in enumerate(closes)]


# A deliberately awkward mix: runs, reversals, gaps through the stop, and paths that never resolve.
PATHS = [
    [100, 108, 116, 124, 132, 140],                 # clean run
    [100, 112, 121, 108, 99, 94, 90],               # target then collapse through the stop
    [100, 96, 92, 88],                              # straight to the stop
    [100, 101, 100, 99, 101, 100],                  # noise, never resolves
    [100, 150, 90, 160, 80, 200],                   # violent whipsaw
    [100, 121, 121, 121, 121],                      # sits exactly on target
]


@pytest.mark.parametrize("closes", PATHS)
@pytest.mark.parametrize("direction,entry,stop,target",
                         [("BULLISH", 100.0, 95.0, 120.0), ("BEARISH", 100.0, 105.0, 80.0)])
def test_run_path_is_truncation_invariant(direction, entry, stop, target, closes):
    """Bars after the exit must not change the exit.

    _run_path returns the exit DATE, so the check is exact: replay the full path, find where it left,
    then replay again with everything after that bar removed. Same outcome, same price, or the walk is
    reading the future.
    """
    if direction == "BEARISH":
        closes = [200 - c for c in closes]          # mirror the path for a short

    bars = _bars(closes)
    outcome, exit_price, exit_date = server._run_path(
        direction, entry, stop, target, bars, 0.04, 0, return_date=True)

    if outcome == "OPEN":
        return                                      # never exited; nothing to truncate to

    cut = next(i for i, b in enumerate(bars) if str(b[0]) == str(exit_date))
    truncated = bars[:cut + 1]
    t_outcome, t_price, _ = server._run_path(
        direction, entry, stop, target, truncated, 0.04, 0, return_date=True)

    assert (t_outcome, t_price) == (outcome, exit_price), (
        f"look-ahead: full path gave {outcome} @ {exit_price}, truncating at the exit bar gave "
        f"{t_outcome} @ {t_price}. The walk is using bars it could not have seen."
    )


@pytest.mark.parametrize("closes", PATHS)
def test_run_path_exit_is_a_price_that_actually_occurred(closes):
    """The exit must lie inside the range the instrument actually traded, or it is fabricated.

    A stop or target can be filled at its level, and an open trade exits at the last close -- but nothing
    can exit beyond the extremes of the bars walked.
    """
    bars = _bars(closes)
    outcome, exit_price = server._run_path("BULLISH", 100.0, 95.0, 120.0, bars, 0.04, 0)
    if exit_price is None:
        return
    lo = min(min(b[2] for b in bars), 95.0)          # the stop level is a legitimate fill
    hi = max(max(b[1] for b in bars), 120.0)
    assert lo - 1e-9 <= exit_price <= hi + 1e-9, (
        f"{outcome} exited at {exit_price}, outside the traded range [{lo}, {hi}]"
    )


def test_run_path_never_returns_more_than_perfect_foresight():
    """No strategy can beat selling the highest high. A replay that does is reading the future."""
    closes = [100, 112, 121, 108, 145, 99, 94]
    bars = _bars(closes)
    entry = 100.0
    _out, exit_price = server._run_path("BULLISH", entry, 95.0, 120.0, bars, 0.04, 0)
    best = max(b[1] for b in bars)
    assert exit_price <= best + 1e-9, f"exited at {exit_price}, above the highest high {best}"


def test_run_path_a_looser_trail_never_beats_a_tighter_one_on_a_pure_reversal():
    """Monotonicity. On a path that peaks then falls to the stop, widening the trail can only give back
    more. If it does not, the trail is not being applied on the way down."""
    closes = [100, 115, 130, 145, 160, 120, 100, 90]
    prev = None
    for thr in (0.02, 0.05, 0.10, 0.20):
        _out, ex = server._run_path("BULLISH", 100.0, 95.0, 120.0, _bars(closes), thr, 0)
        if prev is not None:
            assert ex <= prev + 1e-9, f"trail {thr:.0%} exited at {ex}, better than the tighter one {prev}"
        prev = ex


def test_the_target_floor_holds_across_every_path():
    """Once target is touched the exit can never be below it -- the guarantee the live design rests on.

    Stated as a property over all the awkward paths rather than one hand-picked example, because the
    live money argument is 'this can never happen', not 'this did not happen in the case I tried'.
    """
    target = 120.0
    for closes in PATHS:
        bars = _bars(closes)
        touched = any(b[1] >= target for b in bars)
        outcome, exit_price = server._run_path("BULLISH", 100.0, 95.0, target, bars, 0.04, 0)
        if touched and outcome != "OPEN":
            assert exit_price >= target - 1e-9, (
                f"path {closes} touched {target} but exited at {exit_price}"
            )


# ------------------------------------------------------------------------------------------------------
# Proving the detector has teeth. A guard that has never failed is not evidence of anything, so the
# actual bug from 2026-08-17 is reconstructed here and the property must reject it.
# ------------------------------------------------------------------------------------------------------
def _banked_half_with_lookahead(entry, stop, target, bars):
    """The bug verbatim: 'did this trade EVER reach target?' scanned over the whole forward path, with
    no regard for the stop having been hit first. Inflated a 3,851-trade average from +2.84% to +5.01%."""
    if any(b[1] >= target for b in bars):
        return target
    for _bd, _hi, lo, cl in bars:
        if lo <= stop:
            return stop
    return bars[-1][3]


def _banked_half_correct(entry, stop, target, bars):
    """Walked in order: whichever level the price reaches FIRST is the one that fills."""
    for _bd, hi, lo, cl in bars:
        if lo <= stop:
            return stop
        if hi >= target:
            return target
    return bars[-1][3]


def test_the_lookahead_detector_actually_catches_lookahead():
    """A path that stops out BEFORE later rallying through the target -- the shape that fooled me.

    The correct walk fills at the stop. The buggy one sees the later rally and claims the target. If the
    property below cannot tell them apart it is decoration, not a test.
    """
    entry, stop, target = 100.0, 95.0, 120.0
    closes = [100, 97, 92, 110, 125, 130]            # stopped at bar 3, target only reached at bar 5
    bars = _bars(closes)

    assert _banked_half_correct(entry, stop, target, bars) == stop
    assert _banked_half_with_lookahead(entry, stop, target, bars) == target, (
        "the reconstruction must reproduce the original bug, or this test proves nothing"
    )

    # Truncation invariance, applied to both. The correct version exits at the stop on bar 3, so bars
    # after it are irrelevant. The buggy one changes its answer the moment the future is removed.
    stopped_at = next(i for i, b in enumerate(bars) if b[2] <= stop)
    truncated = bars[:stopped_at + 1]

    assert _banked_half_correct(entry, stop, target, truncated) == \
        _banked_half_correct(entry, stop, target, bars), "the correct walk is truncation invariant"

    assert _banked_half_with_lookahead(entry, stop, target, truncated) != \
        _banked_half_with_lookahead(entry, stop, target, bars), (
        "TRUNCATION INVARIANCE FAILED TO DETECT LOOK-AHEAD -- the guard is useless"
    )


def test_perfect_foresight_bound_also_catches_it():
    """Second, independent net: no exit may beat the best price actually available while the position
    was still open. The buggy walk exits at 120 having been stopped out at 95 three bars earlier."""
    entry, stop, target = 100.0, 95.0, 120.0
    bars = _bars([100, 97, 92, 110, 125, 130])
    stopped_at = next(i for i, b in enumerate(bars) if b[2] <= stop)
    best_while_open = max(b[1] for b in bars[:stopped_at + 1])
    assert _banked_half_with_lookahead(entry, stop, target, bars) > best_while_open, (
        "the bug should exceed what was reachable before the stop -- that is what makes it detectable"
    )
    assert _banked_half_correct(entry, stop, target, bars) <= best_while_open
