"""The scheduled-job watcher: it must alert on CHANGE, ignore jobs that are off, and never go quiet.

WHY. On 2026-09-21 the owner asked whether cron failures were "managed without my input". They were
not: the Morning Chain had failed on five separate days and nothing reached anyone, and only 5 of 61
workflows carry any failure step. The watcher exists to close that, and these tests hold the three
properties that decide whether it is worth having.
"""

import run_cron_watch as w


class _Jobs:
    """Stands in for hvf_web.scheduled_jobs.get_jobs."""

    def __init__(self, jobs):
        self.jobs = jobs

    def get_jobs(self, force=False):
        return {"jobs": self.jobs}


def _wire(monkeypatch, jobs, previous=None, enabled=None):
    sent = []
    monkeypatch.setattr(w, "_state", lambda: previous or {})
    monkeypatch.setattr(w, "_save_state", lambda s: True)
    monkeypatch.setattr(w, "_enabled_titles", lambda: enabled)
    monkeypatch.setattr(w, "_notify", lambda subject, body: sent.append((subject, body)))
    # PATCH THE PACKAGE ATTRIBUTE, NOT sys.modules. check() does `from hvf_web import scheduled_jobs`,
    # which reads the attribute on the hvf_web package; once the real module has been imported by any
    # other test in the session, that attribute already exists and a sys.modules entry is ignored. These
    # tests passed alone and failed in the full suite until this was fixed — the failure depended on
    # which other test files ran first, which is the worst kind of flake.
    import types
    import hvf_web
    mod = types.ModuleType("hvf_web.scheduled_jobs")
    mod.get_jobs = _Jobs(jobs).get_jobs
    monkeypatch.setattr(hvf_web, "scheduled_jobs", mod, raising=False)
    return sent


def _job(title, status, last_time="2026-09-21T03:30:00Z", cron="30 3 * * 1-6"):
    return {"raw_title": title, "title": title, "last_status": status,
            "last_time": last_time, "workflow": f"{title.lower().replace(' ', '-')}.yml", "cron": cron}


def test_a_newly_failing_job_raises_an_alert(monkeypatch):
    sent = _wire(monkeypatch, [_job("Morning Chain", "failure")], previous={"Morning Chain": "success"})
    assert w.check() == 1
    assert sent and "FAILING" in sent[0][0]
    assert "Morning Chain" in sent[0][1]


def test_a_job_that_is_still_failing_does_not_alert_again(monkeypatch):
    """AN ALERT THAT REPEATS IS AN ALERT THAT GETS FILTERED, and then the next real one is filtered
    too. It still counts towards the exit code, so the watcher's own run stays red."""
    sent = _wire(monkeypatch, [_job("Morning Chain", "failure")], previous={"Morning Chain": "failure"})
    assert w.check() == 1, "a still-failing job must keep the run red"
    assert not sent, "a known failure must not re-alert on every pass"


def test_recovery_is_reported(monkeypatch):
    """Owner 2026-09-19, P-10: tell me when a failing job starts succeeding again."""
    sent = _wire(monkeypatch, [_job("Morning Chain", "success")], previous={"Morning Chain": "failure"})
    assert w.check() == 0
    assert sent and "RECOVERED" in sent[0][0]


def test_a_disabled_job_is_judged_neither_failing_nor_stale(monkeypatch):
    """15 of the registry's jobs are off by design -- the session monitors, because WEB_BRIDGE is the
    only enabled execution source. A first pass without this filter cried wolf 15 times."""
    jobs = [_job("UK Monitor", "failure", last_time="2026-08-06T16:55:17Z"),
            _job("Morning Chain", "success")]
    sent = _wire(monkeypatch, jobs, previous={}, enabled={"Morning Chain"})
    assert w.check() == 0, "a disabled job must not make the watcher red"
    assert not sent


def test_a_job_that_stopped_firing_is_caught_even_though_it_never_failed(monkeypatch):
    """THE FAILURE MODE THAT HID AN EXPIRED GH_PAT FOR EIGHT WEEKS. The jobs did not fail -- they
    stopped running, emitted nothing, and the page stayed green. A stale success is not a success."""
    jobs = [_job("Weekend Review", "success", last_time="2026-08-01T09:00:40Z")]
    sent = _wire(monkeypatch, jobs, previous={"Weekend Review": "success"}, enabled={"Weekend Review"})
    assert w.check(stale_after_days=3.0) == 1
    assert sent and "STOPPED RUNNING" in sent[0][0]
    assert "Weekend Review" in sent[0][1]


def test_an_empty_job_list_is_an_error_not_a_clean_bill_of_health(monkeypatch):
    """If the registry or the API gives us nothing, we know nothing -- and must not report 'all fine'."""
    sent = _wire(monkeypatch, [], previous={})
    assert w.check() == 1
    assert not sent


def test_dry_run_sends_nothing(monkeypatch):
    sent = _wire(monkeypatch, [_job("Morning Chain", "failure")], previous={})
    assert w.check(dry_run=True) == 1
    assert not sent


def test_an_unreadable_enabled_list_judges_everything_rather_than_nothing(monkeypatch):
    """Failing towards a noisy alert beats failing towards silence -- silence is the bug being fixed."""
    sent = _wire(monkeypatch, [_job("Morning Chain", "failure")], previous={}, enabled=None)
    assert w.check() == 1
    assert sent, "with no enabled list we must still alert, not skip everything"


def test_an_unparseable_timestamp_is_not_treated_as_fresh(monkeypatch):
    assert w._age_days("not a date") is None
    assert w._age_days("") is None
    assert w._age_days(None) is None
