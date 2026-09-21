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


def _code_only(fn):
    """A function's source with docstrings and comments removed.

    The first version of the guard below matched the DOCSTRING that explains what not to do, and so
    failed against perfectly correct code. A guard that reads prose is not reading the code.
    """
    import inspect
    import re
    src = re.sub(r'"""(?:.|\n)*?"""', "", inspect.getsource(fn))
    return "\n".join(line.split("#")[0] for line in src.split("\n"))


def test_a_fractional_retention_does_not_break_the_nightly_prune():
    """THE BUG THIS CHANGE ALMOST SHIPPED, and it would have failed SILENTLY.

    make_interval(years => ...) takes an INTEGER. The moment retention became 4.05 the nightly prune
    raised `invalid input syntax for type integer: "4.05"` -- proven against the live database on
    2026-09-21, where years=5 returns 2021-09-21 and years=4.05 raises. price_audit.py calls
    prune_older_than inside a bare `except Exception` that logs a warning, so it would have failed
    every night behind a green job: retention simply would not have been enforced, and nobody would
    have known. Before the change it was a working no-op; after it, a silent error.

    Checked as source text rather than by pruning, because the only honest live test deletes data.
    """
    import price_store
    src = _code_only(price_store.retention_cutoff) + _code_only(price_store.prune_older_than)
    assert "make_interval(years" not in src, (
        "make_interval(years => ...) cannot take a fractional year; build the cutoff with the "
        "(:y || ' years')::interval form, which Postgres accepts")
    assert "years')::interval" in src, (
        "the cutoff must be built from an interval expression that accepts a fractional year")


def test_both_prune_paths_share_one_cutoff_definition():
    """The nightly prune and the manual script must delete exactly the SAME rows.

    They computed the boundary separately until 2026-09-21 and disagreed by 13 days on the identical
    constant once it went fractional -- 2022-08-21 from the script against 2022-09-03 from
    price_store. "I pruned it" means nothing when two prunes mean two different things.
    """
    import run_price_history_prune as rp
    assert "price_store.retention_cutoff" in _code_only(rp._cutoff), (
        "run_price_history_prune must delegate to price_store.retention_cutoff rather than computing "
        "its own boundary from the same constant")
