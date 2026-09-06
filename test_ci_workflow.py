# ======================================================================================================
# The CI workflow's own guarantees (2026-09-06).
#
# CI had produced NO usable signal for at least twenty consecutive runs: twelve failures, then eight
# "cancelled" -- and cancelled is what a job killed by timeout-minutes reports. The Slack alert was
# conditioned on failure() alone, so it stayed silent through every one of those eight. The single
# mechanism meant to report the outage was blind to the way the outage actually happened.
#
# This is the repository's signature defect in test form: something correct that nothing effectively
# invokes, reporting green-ish while doing nothing.
# ======================================================================================================

import re
from pathlib import Path

import pytest

WORKFLOW = Path(".github/workflows/trading-hvf-tests.yml")


@pytest.fixture(scope="module")
def wf() -> str:
    assert WORKFLOW.exists(), f"{WORKFLOW} is missing"
    return WORKFLOW.read_text(encoding="utf-8")


def test_the_offline_suite_has_room_to_finish(wf):
    """Measured 2026-09-06: 588s of pytest on the runner against a 600s cap. The margin was 12 seconds,
    which is why eight consecutive runs died. Locally the same suite is ~150s -- the runner is slower and
    test_js_behaviour spawns a Node subprocess per test -- so the local time is not a safe guide."""
    m = re.search(r"timeout-minutes:\s*(\d+)", wf)
    assert m, "the job has no timeout at all"
    assert int(m.group(1)) >= 20, (
        f"timeout-minutes is {m.group(1)}; the suite already needed 9m48s in September 2026 and keeps "
        "growing, so anything under 20 puts it back on the edge of silent cancellation")


def test_a_timed_out_run_still_raises_the_alarm(wf):
    """The failure that actually happened. A job killed by timeout-minutes is CANCELLED, not failed."""
    m = re.search(r"- name: Alert on failure\s*\n\s*if:\s*([^\n]+)", wf)
    assert m, "the failure alert is gone"
    cond = m.group(1)
    assert "cancelled()" in cond, (
        f"the alert fires on {cond.strip()!r}, which stays silent when the job is killed by its timeout "
        "-- exactly the eight runs nobody noticed")
    assert "failure()" in cond, "it must still fire on an ordinary test failure"


def test_the_alert_does_not_promise_a_failure_it_cannot_distinguish(wf):
    """A cancelled run has not proven the detection behaviour changed -- it has proven nothing at all.
    Saying "FAILED" for a timeout would send someone hunting a regression that may not exist."""
    m = re.search(r"--data \\s*\n\s*'(\{.*?\})'", wf, re.S)
    assert m, "the alert payload could not be read"
    assert "did not pass" in m.group(1), "the wording must cover both outcomes honestly"


def test_ci_still_deselects_the_live_state_tests(wf):
    """These need the Supabase user store and a built snapshot; in CI the env vars are placeholders, so
    without the marker they fail for reasons that say nothing about the code under review."""
    assert 'pytest -q -m "not live_state"' in wf


def test_ci_supplies_placeholders_and_never_real_credentials(wf):
    """Import-time os.environ lookups need these to exist. Nothing here may authenticate anywhere."""
    for var in ("IG_API_KEY", "SUPABASE_USER", "SUPABASE_DB_PASSWORD"):
        m = re.search(rf"{var}:\s*([^\n]+)", wf)
        assert m, f"{var} is not set for the test step"
        assert "placeholder" in m.group(1), f"{var} does not look like a placeholder: {m.group(1)!r}"
        assert "secrets." not in m.group(1), f"{var} must never come from a real secret in this job"
