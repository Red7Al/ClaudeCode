"""The scheduled-job watcher: it must alert on CHANGE, ignore jobs that are off, and never go quiet.

WHY. On 2026-09-21 the owner asked whether cron failures were "managed without my input". They were
not: the Morning Chain had failed on five separate days and nothing reached anyone, and only 5 of 61
workflows carry any failure step. The watcher exists to close that, and these tests hold the properties
that decide whether it is worth having.

EVERY AGE IN HERE IS RELATIVE TO NOW, AND THAT IS NOT A STYLE CHOICE. The first version of this file
pinned `last_time="2026-09-21T03:30:00Z"` and compared it against the real clock. It passed on the day it
was written and MEASURED 2026-09-25 it was failing 6 of its 9 tests -- every fixture had drifted past the
staleness threshold as the days passed. CI is push-triggered and there had been no push since
2026-09-21T17:18Z, so nothing reported it. A test that depends on the wall clock is a test that lies
about tomorrow, and this one hid the very defect it was written to guard: the flat staleness threshold
that had the watcher red and emailing hourly about five healthy jobs.
"""

import datetime as dt

import run_cron_watch as w


class _Jobs:
    """Stands in for hvf_web.scheduled_jobs.get_jobs."""

    def __init__(self, jobs):
        self.jobs = jobs

    def get_jobs(self, force=False):
        return {"jobs": self.jobs}


def _wire(monkeypatch, jobs, previous=None, enabled=None, previously_stale=None):
    sent = []
    monkeypatch.setattr(w, "_state", lambda: previous or {})
    monkeypatch.setattr(w, "_stale_titles", lambda: set(previously_stale or ()))
    monkeypatch.setattr(w, "_save_state", lambda *a, **k: True)
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


def _job(title, status, age_days=0.0, cron="30 3 * * 1-6"):
    """A job whose last run was `age_days` ago, measured from now so the fixture cannot rot."""
    when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=age_days)
    return {"raw_title": title, "title": title, "last_status": status,
            "last_time": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "workflow": f"{title.lower().replace(' ', '-')}.yml", "cron": cron}


# ── failure, recovery, and not repeating yourself ──────────────────────────────────────────────────────

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
    jobs = [_job("UK Monitor", "failure", age_days=50),
            _job("Morning Chain", "success")]
    sent = _wire(monkeypatch, jobs, previous={}, enabled={"Morning Chain"})
    assert w.check() == 0, "a disabled job must not make the watcher red"
    assert not sent


# ── staleness: the threshold must come from the job's own schedule ─────────────────────────────────────

def test_a_job_that_stopped_firing_is_caught_even_though_it_never_failed(monkeypatch):
    """THE FAILURE MODE THAT HID AN EXPIRED GH_PAT FOR EIGHT WEEKS. The jobs did not fail -- they
    stopped running, emitted nothing, and the page stayed green. A stale success is not a success."""
    jobs = [_job("Weekend Review", "success", age_days=55, cron="0 9 * * 6")]
    sent = _wire(monkeypatch, jobs, previous={"Weekend Review": "success"}, enabled={"Weekend Review"})
    assert w.check() == 1
    assert sent and "STOPPED RUNNING" in sent[0][0]
    assert "Weekend Review" in sent[0][1]


def test_a_healthy_weekly_job_is_not_stale_five_days_after_its_run(monkeypatch):
    """THE REGRESSION THIS FILE EXISTS FOR. MEASURED 2026-09-25: five weekly Sunday jobs were reported
    "STOPPED RUNNING" at 4.8-5.6 days old, because age was compared against one flat 3 days whatever the
    schedule. They had each run exactly on their Sunday. A weekly job is stale at 3 days by construction,
    so the watcher failed 31 of 31 runs and sent 31 emails in 31 hours, all of them false."""
    jobs = [_job("Market Cap Backfill", "success", age_days=5.6, cron="0 5 * * 0"),
            _job("Supabase Database Backup", "success", age_days=4.8, cron="30 23 * * 0"),
            _job("GH PAT Expiry Check", "success", age_days=5.5, cron="0 7 * * 0"),
            _job("DB Index Audit", "success", age_days=5.5, cron="0 8 * * 0"),
            _job("Sector Cache Backfill", "success", age_days=5.5, cron="0 6 * * 0")]
    sent = _wire(monkeypatch, jobs, previous={j["raw_title"]: "success" for j in jobs},
                 enabled={j["raw_title"] for j in jobs})
    assert w.check() == 0, "a weekly job five days after its run is on schedule, not stale"
    assert not sent, "these five false alarms are the whole reason for this change"


def test_a_weekly_job_that_missed_two_sundays_is_still_caught(monkeypatch):
    """The threshold must be looser for a weekly job, NOT absent. 10.5 days is one missed Sunday plus
    half of the next interval -- a genuinely dead weekly job must not hide behind the wider window."""
    jobs = [_job("Market Cap Backfill", "success", age_days=12, cron="0 5 * * 0")]
    sent = _wire(monkeypatch, jobs, previous={"Market Cap Backfill": "success"},
                 enabled={"Market Cap Backfill"})
    assert w.check() == 1
    assert sent and "STOPPED RUNNING" in sent[0][0]


def test_the_hourly_watcher_is_not_called_stale_for_one_late_run(monkeypatch):
    """Its own schedule is '5 * * * *', a one-hour gap. Without a floor under the threshold, 90 minutes
    of lateness would be an outage. Twelve hours of silence is; one late run is not."""
    jobs = [_job("Cron Watch", "success", age_days=2 / 24.0, cron="5 * * * *")]
    sent = _wire(monkeypatch, jobs, previous={"Cron Watch": "success"}, enabled={"Cron Watch"})
    assert w.check() == 0
    assert not sent


def test_a_weekday_job_silent_over_the_weekend_is_not_stale(monkeypatch):
    """'30 22 * * 1-5' has a normal Friday-to-Monday gap of 3 days. MEASURED 2026-09-25: 19 of the 38
    registered jobs normally go 3 days or more between runs, so the flat 3-day threshold put half the
    registry in reach of a false alarm."""
    jobs = [_job("Trading State Audit", "success", age_days=3.2, cron="30 22 * * 1-5")]
    sent = _wire(monkeypatch, jobs, previous={"Trading State Audit": "success"},
                 enabled={"Trading State Audit"})
    assert w.check() == 0
    assert not sent


def test_a_flat_override_still_works(monkeypatch):
    """--stale-after-days remains an escape hatch: one flat number for every job, whatever its cron."""
    jobs = [_job("Market Cap Backfill", "success", age_days=5.6, cron="0 5 * * 0")]
    sent = _wire(monkeypatch, jobs, previous={}, enabled={"Market Cap Backfill"})
    assert w.check(stale_after_days=3.0) == 1, "an explicit flat threshold must override the schedule"
    assert sent and "STOPPED RUNNING" in sent[0][0]


def test_an_unreadable_schedule_falls_back_to_the_old_flat_threshold(monkeypatch):
    """A cron we cannot parse must not go quiet -- it keeps exactly the behaviour it had before."""
    jobs = [_job("Mystery Job", "success", age_days=4, cron="this is not a cron")]
    sent = _wire(monkeypatch, jobs, previous={}, enabled={"Mystery Job"})
    assert w.check() == 1
    assert sent and "STOPPED RUNNING" in sent[0][0]


# ── staleness must obey the same "only on change" rule as failure ──────────────────────────────────────

def test_a_known_stale_job_does_not_re_alert_every_run(monkeypatch):
    """THE SECOND HALF OF THE 2026-09-25 DEFECT. `newly_failing` and `recovered` were change-gated and
    `stale` was not, so a known-stale job emailed on every single pass -- the exact behaviour the module
    docstring promises it does not have. It still counts towards the exit code, as a live failure does."""
    jobs = [_job("Weekend Review", "success", age_days=55, cron="0 9 * * 6")]
    sent = _wire(monkeypatch, jobs, previous={"Weekend Review": "success"},
                 enabled={"Weekend Review"}, previously_stale={"Weekend Review"})
    assert w.check() == 1, "a still-stale job must keep the run red"
    assert not sent, "a known-stale job must not re-alert on every pass"


def test_a_newly_stale_job_alerts_even_when_another_is_already_stale(monkeypatch):
    """A second job going quiet is news, and must not be swallowed because the first one already had."""
    jobs = [_job("Weekend Review", "success", age_days=55, cron="0 9 * * 6"),
            _job("COT Report", "success", age_days=40, cron="0 10 * * 6")]
    sent = _wire(monkeypatch, jobs, previous={j["raw_title"]: "success" for j in jobs},
                 enabled={j["raw_title"] for j in jobs}, previously_stale={"Weekend Review"})
    assert w.check() == 2, "both stale jobs count towards the exit code"
    assert len(sent) == 1 and "STOPPED RUNNING" in sent[0][0]
    assert "COT Report" in sent[0][1], "the newly stale job is the news"
    assert "Still stale from before" in sent[0][1] and "Weekend Review" in sent[0][1]


# ── the things that must never go quiet ────────────────────────────────────────────────────────────────

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
