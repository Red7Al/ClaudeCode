"""cron_spec: one reading of when a cron fires, and the interval that falls out of it.

WHY THE GAPS ARE HAND-COMPUTED HERE. The watcher's staleness threshold is derived from max_gap_days, so a
wrong gap silently restores the defect this module was written to remove -- either crying wolf about a
healthy job or, worse, going quiet about a dead one. Each expected value below is worked out by hand in
its comment rather than captured from the implementation, because a test that records what the code did
cannot tell you the code is wrong.
"""

import ast

import pytest

import cron_spec


def _registry_crons():
    """Every cron expression in the live JOBS registry, read the same way scheduled_jobs reads it.

    Not imported: setup_cronjobs raises SystemExit at module scope without CRONJOB_API_KEY
    (setup_cronjobs.py:116-118), which is why hvf_web.scheduled_jobs._load_jobs AST-parses it too.
    """
    tree = ast.parse(open("setup_cronjobs.py", encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "JOBS" for t in node.targets):
            return [tuple(x) for x in ast.literal_eval(node.value)]
    raise AssertionError("JOBS not found in setup_cronjobs.py")


# ── the expander ───────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field,lo,hi,expected", [
    ("*", 0, 6, [0, 1, 2, 3, 4, 5, 6]),
    ("5", 0, 59, [5]),
    ("1-5", 0, 6, [1, 2, 3, 4, 5]),
    ("1,5", 0, 6, [1, 5]),
    ("*/15", 0, 59, [0, 15, 30, 45]),
    ("6-22/2", 0, 23, [6, 8, 10, 12, 14, 16, 18, 20, 22]),
    ("0,2,4", 0, 23, [0, 2, 4]),
])
def test_expand_field(field, lo, hi, expected):
    assert cron_spec.expand_field(field, lo, hi) == expected


def test_an_unreadable_field_raises_rather_than_returning_nothing():
    """An empty set would read as "never fires", which is indistinguishable from a valid schedule and
    would make a dead job look deliberately quiet."""
    with pytest.raises(Exception):
        cron_spec.expand_field("tuesday", 0, 6)


def test_the_expander_still_agrees_with_setup_cronjobs_on_the_whole_registry():
    """THE REFACTOR GUARD. The expander was lifted out of setup_cronjobs._cron_to_schedule on 2026-09-25;
    that function feeds the live cron-job.org API, so a divergence here would silently re-schedule
    production trading jobs. It keeps the [-1] "every" sentinel, which is its API's convention, not
    cron's -- so that mapping is applied here too."""
    import os
    os.environ.setdefault("CRONJOB_API_KEY", "test-placeholder")
    import setup_cronjobs

    def sentinel(field, lo, hi):
        return [-1] if field == "*" else cron_spec.expand_field(field, lo, hi)

    for title, cron, _wf in _registry_crons():
        minute, hour, mday, month, wday = cron.split()
        assert setup_cronjobs._cron_to_schedule(cron) == {
            "timezone": "UTC", "expiresAt": 0,
            "minutes": sentinel(minute, 0, 59), "hours": sentinel(hour, 0, 23),
            "mdays": sentinel(mday, 1, 31), "months": sentinel(month, 1, 12),
            "wdays": sentinel(wday, 0, 6),
        }, f"{title}: cron_spec and setup_cronjobs disagree on '{cron}'"


# ── the interval ───────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cron,expected,why", [
    ("5 * * * *",      1 / 24.0, "hourly: one hour"),
    ("30 18 * * *",    1.0,      "daily at a fixed time: 24 hours"),
    ("30 3 * * 1-6",   2.0,      "Mon-Sat: the Saturday-to-Monday gap"),
    ("30 22 * * 1-5",  3.0,      "Mon-Fri: the Friday-to-Monday gap"),
    ("0 9 * * 1,5",    4.0,      "Mon and Fri: Monday-to-Friday is 4, NOT the 3 of Friday-to-Monday"),
    ("0 5 * * 0",      7.0,      "weekly on Sunday"),
    ("0 9 * * 6",      7.0,      "weekly on Saturday"),
    ("0 5 1 * *",      31.0,     "monthly on the 1st: the longest month in the window"),
])
def test_max_gap_days(cron, expected, why):
    assert cron_spec.max_gap_days(cron) == pytest.approx(expected), why


def test_the_longest_gap_is_taken_not_the_shortest():
    """'0 9 * * 1,5' is the case that makes this more than an average. Mon->Fri is 4 days and Fri->Mon is
    3; a job judged on the shorter one is called stale every single week while healthy."""
    assert cron_spec.max_gap_days("0 9 * * 1,5") == pytest.approx(4.0)


def test_an_intraday_gap_counts_when_it_is_the_widest():
    """'0 9,10 * * *' fires twice a day but the wide gap is 10:00 to next 09:00 -- 23 hours."""
    assert cron_spec.max_gap_days("0 9,10 * * *") == pytest.approx(23 / 24.0)


@pytest.mark.parametrize("cron", ["", "not a cron", "1 2 3", "* * * * * *", "0 tuesday * * *"])
def test_an_unreadable_expression_returns_none_rather_than_a_number(cron):
    """None means "do not judge". A number here would be a guess presented as a measurement."""
    assert cron_spec.max_gap_days(cron) is None


def test_every_registry_schedule_yields_a_usable_interval():
    """If any live job's schedule cannot be measured it silently falls back to the old flat 3 days, which
    is the behaviour being replaced -- so this asserts the fallback is never needed in production."""
    unreadable = [(t, c) for t, c, _w in _registry_crons() if cron_spec.max_gap_days(c) is None]
    assert not unreadable, f"these registered schedules cannot be measured: {unreadable}"


# ── the threshold ──────────────────────────────────────────────────────────────────────────────────────

def test_the_threshold_clears_a_healthy_weekly_job_and_catches_a_dead_one():
    """The five false positives of 2026-09-25 were weekly jobs at 4.8-5.6 days. 10.5 days is one missed
    Sunday plus half the next interval."""
    limit = cron_spec.stale_after_days("0 5 * * 0")
    assert limit == pytest.approx(10.5)
    assert 5.6 < limit, "a weekly job five days after its Sunday is on schedule"
    assert 12.0 > limit, "a weekly job that missed two Sundays must still be caught"


def test_the_threshold_has_a_floor_so_high_frequency_jobs_tolerate_one_late_run():
    """Without it the hourly watcher would call itself stale after 90 minutes."""
    assert cron_spec.stale_after_days("5 * * * *") == pytest.approx(0.5)


def test_an_unreadable_schedule_keeps_the_old_flat_threshold():
    """3 days was the flat value for every job before 2026-09-25. Falling back to it means this change
    cannot make anything quieter than it already was."""
    assert cron_spec.stale_after_days("not a cron") == pytest.approx(3.0)
    assert cron_spec.stale_after_days("") == pytest.approx(3.0)
    assert cron_spec.stale_after_days(None) == pytest.approx(3.0)


def test_every_registered_job_gets_a_threshold_above_its_own_interval():
    """THE PROPERTY THAT MATTERS, asserted over the live registry rather than over examples: a job
    running exactly to schedule can never be reported stale. That was false for 19 of 38 jobs under the
    flat threshold."""
    for title, cron, _wf in _registry_crons():
        gap, limit = cron_spec.max_gap_days(cron), cron_spec.stale_after_days(cron)
        assert limit > gap, f"{title} ('{cron}') would be called stale while running on schedule"


def test_the_module_has_no_import_time_side_effects():
    """It is imported by the web app and by the watcher. setup_cronjobs cannot be imported by either
    (SystemExit without CRONJOB_API_KEY) and this module exists partly to avoid that trap -- so it must
    not acquire one."""
    src = open("cron_spec.py", encoding="utf-8").read()
    tree = ast.parse(src)
    for node in tree.body:
        assert isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef,
                                 ast.Assign, ast.AnnAssign, ast.Expr)), \
            f"unexpected module-level statement {type(node).__name__} at line {node.lineno}"
        if isinstance(node, ast.Expr):
            assert isinstance(node.value, ast.Constant), "the only bare expression may be the docstring"
    assert "import os" not in src and "import requests" not in src, \
        "cron_spec must stay a standard-library leaf module"
