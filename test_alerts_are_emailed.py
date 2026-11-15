"""Owner 2026-11-14: "slack only alerts should go to email". Every message sent to the 'alerts' channel is also emailed
(capped and once a day per subject by notify._email_alert); other channels are not; a caller that already emails opts out."""
import notify


def _capture(monkeypatch):
    mails, posts = [], []
    monkeypatch.setattr(notify, "_email_alert", lambda subject, text, **kw: mails.append((subject, text)) or True)
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


def test_first_occurrence_missed_trade_post_is_not_emailed(monkeypatch):
    """The missed-trade post opts out (email=False): the quiet classes stay quiet (owner 2026-11-06)."""
    import db_pool
    mails, _ = _capture(monkeypatch)
    def no_db(*a, **k): raise RuntimeError("db down")
    monkeypatch.setattr(db_pool, "get_db", no_db)
    notify.alert_missed_trade("AAPL", "BUY", "some unclassified reason")
    assert mails == []


def _real_cap(monkeypatch):
    """The REAL _email_alert (dedupe + cap) over an in-memory config_store; returns the subjects actually mailed."""
    import config_store, trade_email
    store, sent = {}, []
    monkeypatch.setenv("RESEND_API_KEY", "x")
    monkeypatch.setattr(config_store, "get_value", lambda k, d="": store.get(k, d))
    monkeypatch.setattr(config_store, "set_value", lambda k, v, updated_by="": store.__setitem__(k, v) or True)
    monkeypatch.setattr(trade_email, "send_simple_email", lambda subject, text, recipients=None: sent.append(subject) or True)
    monkeypatch.setattr(notify, "slack_enabled", lambda ch: False)
    return sent


import pytest


def test_two_users_circuit_breaker_both_emailed(monkeypatch):
    sent = _real_cap(monkeypatch)
    notify.alert_circuit_breaker("alex", "TSLA", "three stops")
    notify.alert_circuit_breaker("bob", "NVDA", "two stops")
    assert len(sent) == 2


def test_noisy_alerts_cannot_starve_a_system_error(monkeypatch):
    sent = _real_cap(monkeypatch)
    for i in range(10):
        notify.alert_watchdog_trigger(f"Job {i}", f"wf{i}.yml", 20)
    notify.alert_system_error("S", "scan", "critical fault", "d")
    assert any(s.startswith("System alert") for s in sent)


def test_every_direct_send_to_alerts_outside_notify_chooses_whether_to_email():
    """Enumerated 2026-11-14 after the fact: ig_shim's per-trade post-trade review called _send("alerts", ...) and would
    have emailed on every closed trade. A caller outside notify.py must say email=True/False; it is never left to the default."""
    import ast
    import pathlib
    import subprocess
    root = pathlib.Path(__file__).parent
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=root, capture_output=True, text=True).stdout.split()
    bad = []
    for f in files:
        if f in ("notify.py",) or pathlib.Path(f).name.startswith("test_"):
            continue
        for n in ast.walk(ast.parse((root / f).read_text(encoding="utf8", errors="ignore"))):
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "_send" \
                    and not any(k.arg == "email" for k in n.keywords):
                bad.append(f"{f}:{n.lineno}")
    assert not bad, f"_send called without email=True/False: {bad}"
