"""Regression tests for the externally managed trading schedules."""

import importlib
import sys


def _load_schedule_module(monkeypatch):
    # The module validates this credential at import time, but schedule conversion
    # itself is pure and makes no network call.
    monkeypatch.setenv("CRONJOB_API_KEY", "test-only-placeholder")
    sys.modules.pop("setup_cronjobs", None)
    return importlib.import_module("setup_cronjobs")


def test_price_data_refresh_runs_at_0430_utc_monday_to_saturday(monkeypatch):
    schedules = _load_schedule_module(monkeypatch)
    jobs = {title: (cron, workflow) for title, cron, workflow in schedules.JOBS}

    cron, workflow = jobs["Price Data Refresh"]

    assert cron == "30 4 * * 1-6"
    # Own workflow file since 2026-08-07 (ChangeRequest P-08, "the job names look peculiar") — it used to
    # share trading-price-audit.yml with "Price History Audit" (see test below), which made the two jobs
    # show byte-identical GitHub Actions run stats in the Scheduled Jobs tab despite running 18.5 hours
    # apart for different reasons.
    assert workflow == "trading-price-refresh.yml"
    assert schedules._cron_to_schedule(cron) == {
        "timezone": "UTC",
        "expiresAt": 0,
        "hours": [4],
        "mdays": [-1],
        "minutes": [30],
        "months": [-1],
        "wdays": [1, 2, 3, 4, 5, 6],
    }


def test_scheduled_jobs_use_squeeze_display_names_without_changing_workflows():
    from hvf_web import scheduled_jobs

    assert scheduled_jobs._display_name("HVF Daily Report") == "Squeeze Daily Report"
    assert scheduled_jobs._display_name("HVF Orders") == "Squeeze Orders"
    assert scheduled_jobs._display_name("UK HVF Watch") == "UK Squeeze Watch"
    assert scheduled_jobs._display_name("AUS HVF Watch") == "AUS Squeeze Watch"
    assert scheduled_jobs._display_name("Price Data Refresh") == "Price Data Refresh"


def test_aus_squeeze_watch_is_scheduled_during_the_aus_session(monkeypatch):
    schedules = _load_schedule_module(monkeypatch)
    jobs = {title: (cron, workflow) for title, cron, workflow in schedules.JOBS}

    assert jobs["AUS HVF Watch"] == ("30 0,2,4 * * 1-5", "trading-aus-hvf-watch.yml")


def test_price_jobs_are_in_the_pricing_category():
    from hvf_web import scheduled_jobs

    assert scheduled_jobs._category("Price Data Refresh") == "Pricing"
    assert scheduled_jobs._category("Price History Audit") == "Pricing"


def test_supabase_backup_runs_daily_at_2330_utc(monkeypatch):
    schedules = _load_schedule_module(monkeypatch)
    jobs = {title: (cron, workflow) for title, cron, workflow in schedules.JOBS}

    assert jobs["Supabase Database Backup"] == ("30 23 * * *", "supabase-backup.yml")


def test_no_two_jobs_share_a_workflow_file(monkeypatch):
    """Regression guard for ChangeRequest P-08 ("the job names look peculiar", 2026-08-07): two jobs on the
    same workflow file get byte-identical GitHub Actions run stats in the Scheduled Jobs tab
    (hvf_web/scheduled_jobs.get_jobs() caches stats per workflow FILE, not per cron-job.org job — see
    _fetch_runs' stats_cache), which reads as a stray duplicate even when the jobs run on different
    schedules for different reasons. "Price Data Refresh" vs "Price History Audit" was exactly this — split
    onto its own workflow file 2026-08-07.

    "Commodity Monitor AM"/"Commodity Monitor PM" are the one deliberate exception: the workflow's own
    header explains they are ONE conceptual */10-min monitor split into two cron windows "to cover the gaps
    between session monitors" (not two different operations), and the AM/PM-suffixed names already signal
    that pairing to anyone reading the tab — unlike "Refresh" vs "Audit", which don't read as related.
    """
    schedules = _load_schedule_module(monkeypatch)
    known_pairs = {"trading-commodity-monitor.yml"}   # ONE conceptual monitor, two cron windows — see docstring

    by_workflow = {}
    for title, _cron, workflow in schedules.JOBS:
        by_workflow.setdefault(workflow, []).append(title)

    shared = {wf: titles for wf, titles in by_workflow.items()
             if len(titles) > 1 and wf not in known_pairs}
    assert shared == {}, f"jobs sharing a workflow file (will show identical run stats): {shared}"


# ======================================================================================================
# A successful creation must never be reported as a failure (found live, 2026-08-25).
#
# create_missing_jobs() printed "CREATED: ... → ..." INSIDE the try that wraps the POST. On a Windows
# cp1252 console that U+2192 raised UnicodeEncodeError AFTER the job had already been created, so the
# except reported a success as "FAIL". The run summarised "0 created, 5 failed" while the cron-job.org
# account had gone from 33 jobs to 35 — two jobs created and both reported as failures.
#
# That is worse than a cosmetic bug: acting on the false report means retrying, which risks duplicate
# scheduled jobs, or believing a job is absent when it is live.
# ======================================================================================================

def test_created_message_survives_a_windows_console():
    """Every string the create loops PRINT must encode on cp1252, or a success reports as a failure."""
    import re
    from pathlib import Path

    src = Path(__file__).with_name("setup_cronjobs.py").read_text(encoding="utf-8")
    printed = re.findall(r'^\s*print\(f?"([^"]*)"', src, re.M)

    offenders = []
    for line in printed:
        try:
            line.encode("cp1252")
        except UnicodeEncodeError as e:
            offenders.append((line, str(e)))

    assert not offenders, (
        "these printed strings cannot be encoded on a Windows console, so printing them raises "
        f"and can flip a success into a reported failure: {offenders}")


def test_creation_is_counted_before_it_is_reported():
    """Structural guard: nothing after the POST may be able to change the verdict.

    The counter must be incremented OUTSIDE the try that wraps create_job, so a later failure
    (printing, formatting, anything at all) cannot reclassify a job the API has already created.
    Checked with ast rather than a regex, because the shape spans an except block.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(__file__).with_name("setup_cronjobs.py").read_text(encoding="utf-8"))

    def calls_create_job(node):
        return any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "create_job"
                   for n in ast.walk(node))

    def increments_created(nodes):
        return any(isinstance(n, ast.AugAssign) and getattr(n.target, "id", "") == "created"
                   for node in nodes for n in ast.walk(node))

    guarded = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and calls_create_job(ast.Module(body=node.body, type_ignores=[])):
            guarded += 1
            assert not increments_created(node.body), (
                f"line {node.lineno}: `created += 1` is inside the try that wraps create_job, so a "
                "failure after the POST reports a created job as failed (2026-08-25 regression)")

    assert guarded == 2, f"expected the two create loops, found {guarded}"
