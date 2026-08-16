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
