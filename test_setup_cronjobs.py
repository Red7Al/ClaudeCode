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
