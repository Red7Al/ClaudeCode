# ======================================================================================================
# Automated Stop-Loss Amendment — the trailing-stop formula (ig_shim.compute_trailing_stop).
#
# This is the formula that will move REAL IG stops once amend_open_stops() is wired in, and today it
# drives the "let winners run" and stop-loss simulations the decision to enable it rests on. It had no
# direct test of its own: every existing test monkeypatches it to a constant, so the arithmetic itself
# was never checked and a 31% gain giving back to 13% went unnoticed.
# ======================================================================================================
import os

os.environ.setdefault("IG_API_KEY", "test")
os.environ.setdefault("IG_USERNAME", "test")
os.environ.setdefault("IG_PASSWORD", "test")
os.environ.setdefault("IG_ACCOUNT_ID", "test")
os.environ.setdefault("SUPABASE_USER", "test")
os.environ.setdefault("SUPABASE_DB_PASSWORD", "test")

import ig_shim  # noqa: E402


def _kept_pct(direction, entry, stop, price, threshold):
    """Percentage of the move from entry that the amended stop actually locks in."""
    new_stop = ig_shim.compute_trailing_stop(direction, entry, stop, price, threshold)
    if new_stop is None:
        return None
    return ((new_stop - entry) / entry * 100.0) if direction == "BUY" else ((entry - new_stop) / entry * 100.0)


def test_a_31_percent_run_retains_at_least_28_percent():
    """The user's requirement, 2026-08-16: "if we achieved 31% ... we must get at least 28%".

    Verbatim from the case that exposed it — 600048.SS, a SHORT, marked +31% and simulated back to
    +13.38% by the old formula.
    """
    for direction, entry, stop, price in (("BUY", 100.0, 95.0, 131.0),      # long, +31%
                                          ("SELL", 100.0, 105.0, 69.0)):    # short, +31%
        kept = _kept_pct(direction, entry, stop, price, 0.92)
        assert kept is not None, f"{direction}: no amendment produced on a 31% run"
        assert kept >= 28.0, f"{direction}: only {kept:.2f}% of a 31% run retained"


def test_threshold_means_the_share_of_the_run_retained():
    """0.5 keeps half the run, 0.9 keeps ninety percent — the meaning the setting always claimed."""
    assert round(_kept_pct("BUY", 100.0, 95.0, 131.0, 0.5), 4) == 15.5
    assert round(_kept_pct("BUY", 100.0, 95.0, 131.0, 0.9), 4) == 27.9
    assert round(_kept_pct("SELL", 100.0, 105.0, 69.0, 0.9), 4) == 27.9
    # The example previously recorded in the source header, now delivering its stated intent.
    assert ig_shim.compute_trailing_stop("BUY", 105.0, 100.0, 120.0, 0.5) == 112.5


def test_the_stop_does_not_depend_on_how_many_bars_were_held():
    """The old rule ADDED an increment each bar, so the level tracked bar count, not the peak.

    Walking the same price path one bar at a time must land on the same stop as jumping straight to the
    final price -- otherwise the same trade sampled daily and weekly gets different stops.
    """
    entry, threshold = 100.0, 0.9
    stop = 95.0
    for price in (105.0, 112.0, 118.0, 124.0, 131.0):          # incremental walk
        moved = ig_shim.compute_trailing_stop("BUY", entry, stop, price, threshold)
        if moved is not None:
            stop = moved
    direct = ig_shim.compute_trailing_stop("BUY", entry, 95.0, 131.0, threshold)
    assert stop == direct, f"path-dependent: walked to {stop}, jumped to {direct}"


def test_never_widens_and_never_trails_at_a_loss():
    # Price below entry on a long: no amendment at all.
    assert ig_shim.compute_trailing_stop("BUY", 100.0, 95.0, 85.0, 0.9) is None
    # An already-tighter stop is left alone rather than loosened back toward entry.
    assert ig_shim.compute_trailing_stop("BUY", 100.0, 129.0, 131.0, 0.9) is None
    assert ig_shim.compute_trailing_stop("SELL", 100.0, 71.0, 69.0, 0.9) is None
    # Threshold off = feature off.
    assert ig_shim.compute_trailing_stop("BUY", 100.0, 95.0, 131.0, 0) is None


def test_ratchets_upward_as_price_advances():
    entry, stop = 100.0, 95.0
    levels = []
    for price in (110.0, 120.0, 130.0):
        stop = ig_shim.compute_trailing_stop("BUY", entry, stop, price, 0.9) or stop
        levels.append(stop)
    assert levels == sorted(levels), f"stop must only tighten: {levels}"
    assert levels[-1] > entry, "after a 30% run the stop must be above entry"


# ------------------------------------------------------------------------------------------------------
# ABOVE TARGET the SAME rule applies -- keep this share of the run -- with the target as a hard floor.
#
# A first attempt used a tight distance-below-price trail above target, reasoning that a banked gain
# deserves a minimal stop. The user's question settled it: "if we keep 95% of gain - how can number 3
# allow must more to go back?". The two rules used different UNITS, so a 3%-of-price trail hands back
# 5.37 points on a +79% run where keeping 95% of the run hands back 3.95. Proportional is tighter AND
# consistent, and needs one number instead of two that can silently disagree.
# ------------------------------------------------------------------------------------------------------
KEEP = 0.95


def _bars(closes):
    return [(f"2026-01-{i + 1:02d}", c * 1.005, c * 0.995, c) for i, c in enumerate(closes)]


def _run(direction, entry, stop, target, closes, thr, stop_thr=0.0):
    from hvf_web import server
    outcome, exit_price = server._run_path(direction, entry, stop, target, _bars(closes), thr, stop_thr)
    gain = ((exit_price - entry) / entry * 100.0) if direction == "BULLISH" else ((entry - exit_price) / entry * 100.0)
    return outcome, exit_price, gain


def test_give_back_above_target_is_proportional_to_the_run():
    """A +79% run past a +20% target must hand back ~5% of the run, not collapse to the target."""
    closes = [100, 112, 125, 140, 155, 170, 179, 150, 130, 118, 105]
    _, _, gain = _run("BULLISH", 100.0, 95.0, 120.0, closes, KEEP)
    given_back = 79.0 - gain
    assert gain > 70.0, f"only {gain:.2f}% realised from a 79% peak"
    assert given_back <= 79.0 * (1 - KEEP) + 0.01, f"gave back {given_back:.2f} of 79 points at keep={KEEP}"


def test_the_same_share_is_kept_before_and_after_target():
    """Consistency: the give-back is the same fraction of the run either side of the target."""
    below = _run("BULLISH", 100.0, 95.0, 500.0, [100, 112, 125, 131, 118, 104], KEEP, KEEP)[2]
    above = _run("BULLISH", 100.0, 95.0, 120.0, [100, 112, 125, 131, 118, 104], KEEP, KEEP)[2]
    assert abs(below - above) < 0.01, f"below target {below:.2f}% vs above {above:.2f}% -- rules disagree"


def test_a_tighter_keep_retains_more():
    closes = [100, 112, 125, 140, 155, 170, 179, 150, 130, 118, 105]
    tight = _run("BULLISH", 100.0, 95.0, 120.0, closes, 0.95)[2]
    loose = _run("BULLISH", 100.0, 95.0, 120.0, closes, 0.50)[2]
    assert tight > loose, f"keep 0.95 -> {tight:.2f}%, keep 0.50 -> {loose:.2f}%"


def test_the_target_gain_is_never_surrendered():
    """Target hit then an immediate collapse: the exit must be the target, never worse."""
    closes = [100, 112, 121, 108, 99, 94]
    for thr in (0.5, KEEP):
        _, exit_price, gain = _run("BULLISH", 100.0, 95.0, 120.0, closes, thr)
        assert exit_price >= 120.0 - 1e-9, f"thr={thr}: exited at {exit_price}, below the 120 target"
        assert gain >= 20.0 - 1e-9


def test_short_above_target_behaves_as_the_mirror():
    closes = [100, 88, 75, 60, 45, 30, 21, 50, 70, 82]      # short running to +79%
    _, exit_price, gain = _run("BEARISH", 100.0, 105.0, 80.0, closes, KEEP)
    assert gain > 70.0, f"short only realised {gain:.2f}% from a 79% peak"
    assert exit_price <= 80.0 + 1e-9, "short must never exit above its target level"
