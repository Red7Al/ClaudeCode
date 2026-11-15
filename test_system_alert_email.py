"""System alerts are emailed (Slack is not used), capped (owner 2026-10-27: "140 is way too many"): the same alert once a
UTC day, at most notify.ALERT_EMAILS_PER_DAY a day across every job, counted in the shared app_config store."""
import pytest

import notify


@pytest.fixture
def wired(monkeypatch):
    store, sent = {}, []
    import config_store
    import trade_email
    monkeypatch.setattr(config_store, "get_value", lambda k, d="": store.get(k, d))
    monkeypatch.setattr(config_store, "set_value", lambda k, v, updated_by="": store.__setitem__(k, v) or True)
    monkeypatch.setattr(trade_email, "send_simple_email", lambda **k: sent.append(k) or True)
    monkeypatch.setattr(notify, "_send", lambda *a, **k: False)                  # Slack
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    return store, sent


def test_a_system_alert_is_emailed_once_a_day(wired):
    store, sent = wired
    for _ in range(3):
        notify.alert_system_error("IG_ORDER", "open_trade", "Deal rejected for AAL BUY -- MARKET_CLOSED")
    assert len(sent) == 1
    assert sent[0]["subject"] == "System alert: open_trade - Deal rejected for AAL BUY -- MARKET_CLOSED"


def test_no_more_than_the_daily_ceiling_is_emailed(wired):
    store, sent = wired
    for i in range(notify.ALERT_EMAILS_PER_DAY + 5):
        notify.alert_system_error("S", "c", f"fault {i}")
    assert len(sent) == notify.ALERT_EMAILS_PER_DAY == 10


def test_without_a_sender_nothing_is_sent_or_counted(wired, monkeypatch):
    store, sent = wired
    monkeypatch.delenv("RESEND_API_KEY")
    monkeypatch.delenv("YAHOO_USER", raising=False)
    notify.alert_system_error("S", "c", "fault")
    assert sent == [] and store == {}


def test_a_count_that_cannot_be_recorded_still_sends_once(wired, monkeypatch):
    """Owner 2026-11-15: when the database is down the alert is sent anyway, once per subject per process."""
    store, sent = wired
    import config_store
    monkeypatch.setattr(notify, "_ALERTED_NO_DB", set())
    monkeypatch.setattr(config_store, "set_value", lambda *a, **k: False)
    notify.alert_system_error("S", "c", "fault")
    notify.alert_system_error("S", "c", "fault")          # same alert again: not repeated
    notify.alert_system_error("S", "c", "another fault")
    assert len(sent) == 2


def test_every_workflow_that_logs_in_to_ig_can_email():
    """MEASURED 2026-10-27: the IG login pause emails were skipped ("No email sender configured") in Closing Window
    runs 40491593154 and 40506969984 -- 15 IG workflows carried no sender. The offline test run stays placeholder-only."""
    import pathlib
    wf = pathlib.Path(__file__).with_name(".github") / "workflows"
    missing = [p.name for p in sorted(wf.glob("*.yml"))
               if p.name != "trading-hvf-tests.yml" and "secrets.IG_API_KEY" in p.read_text(encoding="utf-8")
               and not all("${{ secrets." + k + " }}" in p.read_text(encoding="utf-8")
                           for k in ("RESEND_API_KEY", "YAHOO_USER", "YAHOO_APP_PASSWORD"))]
    assert missing == []


def test_the_cron_watch_sends_one_email_not_two(wired):
    store, sent = wired
    import run_cron_watch
    run_cron_watch._notify("Order Bridge failed", "run 1 failed")
    assert [s["subject"] for s in sent] == ["Order Bridge failed"]


def test_slack_origin_alerts_have_their_own_ceiling_of_ten_a_day(wired, monkeypatch):
    """Owner 2026-11-15: CHANNEL_ALERT_EMAILS_PER_DAY is 10, counted apart from the system-error count."""
    store, sent = wired
    assert notify.CHANNEL_ALERT_EMAILS_PER_DAY == 10
    for i in range(15):
        notify._email_alert(f"Alert: fault {i}", "body", bucket="channel_alert_emails", cap=notify.CHANNEL_ALERT_EMAILS_PER_DAY)
    assert len(sent) == 10
