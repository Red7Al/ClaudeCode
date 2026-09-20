"""Retention has ONE definition, and it may not fall below what the analysis actually reads.

WHY THIS FILE EXISTS. Until 2026-09-20 retention was declared twice and the two disagreed:
config.PRICE_HISTORY_RETENTION_YEARS was 4.5, which run_price_history_prune.py uses, while
price_store.RETENTION_YEARS was 5 -- and price_store's value is the DEFAULT ARGUMENT of
prune_older_than(), which price_audit.py calls with no argument on a NIGHTLY schedule. So the number
governing the live table was the 5 that nobody edited, not the 4.5 that was written down as policy.
Neither was ever tested, because a 5-year cutoff predates the oldest bar (2022-02-17) and so deleted
nothing. That is this repository's signature defect: one fact, two pieces of code each deciding what it
means.

The floor is measured, not chosen. A prune is irreversible without re-fetching from Yahoo, so the number
has to be defended by a test rather than by a comment.
"""

import config
import price_store

# The replay's longest window, and the longest lookback any metric applies to a trade inside it.
REPLAY_YEARS = 3
LONGEST_LOOKBACK_DAYS = 365          # _WK52_LOOKBACK_DAYS; VolumeScore's 160 is shorter and thus covered
FLOOR_YEARS = REPLAY_YEARS + LONGEST_LOOKBACK_DAYS / 365.0


def test_retention_has_exactly_one_definition():
    """price_store must READ config, not declare a second number."""
    assert price_store.RETENTION_YEARS == config.PRICE_HISTORY_RETENTION_YEARS, (
        f"price_store.RETENTION_YEARS ({price_store.RETENTION_YEARS}) has drifted from "
        f"config.PRICE_HISTORY_RETENTION_YEARS ({config.PRICE_HISTORY_RETENTION_YEARS}); "
        "config is the only definition")


def test_the_nightly_prune_uses_that_same_definition():
    """price_audit.py calls prune_older_than() with NO argument, so the default IS the live cutoff."""
    default = price_store.prune_older_than.__defaults__[0]
    assert default == config.PRICE_HISTORY_RETENTION_YEARS, (
        f"prune_older_than defaults to {default} but config says "
        f"{config.PRICE_HISTORY_RETENTION_YEARS}; the nightly audit would prune to the wrong depth")


def test_retention_cannot_drop_below_what_the_three_year_replay_reads():
    """THE FLOOR, AND WHY 3.25 WAS REFUSED (measured 2026-09-20).

    A trade at the START of the 3-year window needs bars from BEFORE it to score: 365 days for the
    52-week high/low and 160 for VolumeScore. At 3.25 years the cutoff was 2023-06-21 while those bars
    reach back to 2022-09-21 -- 273 days short. Nothing would have raised; the oldest trades would simply
    have been computed from truncated windows, which is worse than an error because it looks like an
    answer. 4.05 clears the 4.0 floor by 19 days.

    If this test ever fails because someone lowered retention deliberately, the fix is NOT to lower the
    floor: shorten the replay window or the lookbacks first, then the data they no longer read can go.
    """
    assert config.PRICE_HISTORY_RETENTION_YEARS >= FLOOR_YEARS, (
        f"retention {config.PRICE_HISTORY_RETENTION_YEARS}y is below the {FLOOR_YEARS:.2f}y floor "
        f"({REPLAY_YEARS}y replay + {LONGEST_LOOKBACK_DAYS}d lookback). The 3-year figures would be "
        "computed from truncated windows rather than failing visibly.")


def test_the_floor_is_derived_from_the_real_lookback_not_a_magic_number():
    """Guards the guard: if _WK52_LOOKBACK_DAYS grows, the floor must grow with it, or this test is
    protecting a number that no longer describes what the code reads."""
    from hvf_web import server
    assert LONGEST_LOOKBACK_DAYS >= server._WK52_LOOKBACK_DAYS, (
        f"_WK52_LOOKBACK_DAYS is now {server._WK52_LOOKBACK_DAYS} but this floor still assumes "
        f"{LONGEST_LOOKBACK_DAYS}; raise LONGEST_LOOKBACK_DAYS and re-check retention")
    assert LONGEST_LOOKBACK_DAYS >= server._VOLSCORE_LOOKBACK_DAYS, (
        "VolumeScore's lookback now exceeds the one the floor is derived from")
