"""Owner 2026-11-14: "slack only alerts should go to email". Every message sent to the 'alerts' channel is also emailed
(capped and once a day per subject by notify._email_alert); other channels are not; a caller that already emails opts out."""
import notify


def _capture(monkeypatch):
    mails, posts = [], []
    monkeypatch.setattr(notify, "_email_alert", lambda subject, text: mails.append((subject, text)) or True)
    monkeypatch.setattr(notify, "slack_enabled", lambda ch: False)
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: posts.append(a))
    return mails, posts


def test_circuit_breaker_reaches_email_with_its_facts_and_posts_nothing_to_slack(monkeypatch):
    mails, posts = _capture(monkeypatch)
    notify.alert_circuit_breaker("alex", "TSLA", "three stops in a row")
    assert len(mails) == 1 and posts == []
    subject, text = mails[0]
    assert subject.startswith("Alert:") and "TSLA" in text and "three stops in a row" in text


def test_daily_loss_limit_and_watchdog_are_emailed(monkeypatch):
    mails, _ = _capture(monkeypatch)
    notify.alert_daily_loss_limit("alex", -312.5, 5.0)
    notify.alert_watchdog_trigger("Closing Window", "trading-closing-window.yml", 20)
    assert len(mails) == 2 and "312" in mails[0][1] and "Closing Window" in mails[1][1]


def test_other_channels_are_not_emailed(monkeypatch):
    mails, _ = _capture(monkeypatch)
    notify._send("trades", [{"type": "header", "text": {"type": "plain_text", "text": "x"}}])
    assert mails == []


def test_system_error_is_emailed_once_not_twice(monkeypatch):
    mails, _ = _capture(monkeypatch)
    notify.alert_system_error("S", "scan", "zero results", "detail")
    assert len(mails) == 1 and mails[0][0].startswith("System alert:")


def test_a_failing_email_never_stops_the_slack_path(monkeypatch):
    mails, posts = _capture(monkeypatch)
    def boom(s, t): raise RuntimeError("smtp down")
    monkeypatch.setattr(notify, "_email_alert", boom)
    assert notify._send("alerts", [{"type": "header", "text": {"type": "plain_text", "text": "x"}}]) is False
