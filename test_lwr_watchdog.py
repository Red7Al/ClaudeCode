"""The Let Winners Run watchdog must alert when positions are unprotected, and stay quiet otherwise.

WHY. Let Winners Run places orders with NO take-profit, so the gain is protected only while the stop
manager keeps running. With a take-profit the broker holds it even if everything of ours is down; without
one, a bridge that quietly stops leaves an open winner able to round-trip to its original stop. The Order
Bridge runs every two hours on weekdays, so gaps are normal and the watchdog has to tell a normal gap
from a failure -- otherwise it gets muted, and a muted watchdog is worse than none.
"""

from datetime import datetime, timedelta, timezone

import pytest

import run_lwr_watchdog as w

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _beat(hours_ago, managed=3, mode="live"):
    return {"at": (NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds"),
            "mode": mode, "managed": managed, "checked": 9, "locked": 0, "trailing": 0}


def test_alerts_when_managed_positions_have_gone_unmanaged():
    """THE CASE IT EXISTS FOR: positions rely on the manager and it has stopped."""
    v = w.assess(_beat(9, managed=3), max_age_hours=5.0, now=NOW)

    assert v["alert"] is True
    assert v["managed"] == 3
    assert "9.0 hours" in v["reason"]


def test_silent_for_a_normal_gap_between_passes():
    """The bridge runs every two hours; a two-hour-old pass is healthy, not a fault."""
    assert w.assess(_beat(2), max_age_hours=5.0, now=NOW)["alert"] is False


def test_silent_when_stale_but_nothing_was_being_managed():
    """A stale pass with no bound positions is not a risk, and alerting on it would train you to ignore it."""
    v = w.assess(_beat(40, managed=0), max_age_hours=5.0, now=NOW)

    assert v["alert"] is False
    assert v["managed"] == 0


def test_silent_when_the_manager_has_never_run():
    """The shipped state: the feature is off, positions keep their take-profit, nothing is at risk."""
    v = w.assess(None, max_age_hours=5.0, now=NOW)

    assert v["alert"] is False
    assert "never run" in v["reason"]


def test_alerts_on_an_unreadable_timestamp():
    """A heartbeat it cannot age is not evidence of health, so it must not be read as health."""
    assert w.assess({"at": "not-a-date", "managed": 2}, max_age_hours=5.0, now=NOW)["alert"] is True


def test_a_weekend_gap_can_be_tolerated_by_configuration():
    """Positions are unmanaged overnight and at weekends BY DESIGN; the threshold is how you say so."""
    beat = _beat(60, managed=2)

    assert w.assess(beat, max_age_hours=5.0, now=NOW)["alert"] is True
    assert w.assess(beat, max_age_hours=72.0, now=NOW)["alert"] is False


def test_observe_mode_passes_still_count_as_management():
    """During observation the manager is not protecting anything, but it IS running -- and the heartbeat
    records which mode, so an alert says whether the positions were actually being managed."""
    v = w.assess(_beat(9, managed=2, mode="observe"), max_age_hours=5.0, now=NOW)

    assert v["alert"] is True
    assert v["mode"] == "observe"


def test_it_needs_no_ig_credentials_and_cannot_touch_a_position():
    """It reads one heartbeat. It must never grow the ability to act on the account it is watching."""
    src = __import__("pathlib").Path(w.__file__).read_text(encoding="utf-8")

    for forbidden in ("ig_shim", "close_trade", "update_stop", "attach_trailing_stop", "place_"):
        assert forbidden not in src, f"the watchdog must not reach {forbidden}"


def test_slack_posting_honours_the_channel_switch(monkeypatch):
    """Every direct Slack poster must check slack_enabled; the off switch is not automatic here."""
    import notify
    posted = []
    monkeypatch.setattr(w, "SLACK_URL", "https://hooks.example/x")
    monkeypatch.setattr(notify, "slack_enabled", lambda ch: False)
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: posted.append(a) or None)

    assert w.post("anything") is False
    assert posted == [], "posted despite the alerts channel being switched off"
