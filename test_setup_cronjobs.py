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
    assert workflow == "trading-price-audit.yml"
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
