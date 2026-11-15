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


def _subjects(monkeypatch):
    subjects = []
    monkeypatch.setattr(notify, "_email_alert", lambda subject, text, **kw: subjects.append(subject) or True)
    monkeypatch.setattr(notify, "slack_enabled", lambda ch: False)
    return subjects


def test_alert_subjects_are_bounded_and_carry_the_instrument(monkeypatch):
    subjects = _subjects(monkeypatch)
    notify.alert_circuit_breaker("alex", "TSLA", "x: " * 2000)
    notify.alert_calendar_block("FOMC: " + "y" * 500, "US Open")
    notify.alert_stop_slippage("AAPL", "BUY", 100, 95, 90, 10, 50, 100, 2.0, "z" * 3000)
    assert len(subjects) == 3 and all(0 < len(s) <= 200 for s in subjects)
    assert "TSLA" in subjects[0] and "AAPL" in subjects[2]


def test_two_users_circuit_breakers_get_different_subjects(monkeypatch):
    subjects = _subjects(monkeypatch)
    notify.alert_circuit_breaker("alex", "TSLA", "r")
    notify.alert_circuit_breaker("bob", "TSLA", "r")
    assert subjects[0] != subjects[1]


def test_two_users_daily_loss_limit_get_different_subjects(monkeypatch):
    subjects = _subjects(monkeypatch)
    notify.alert_daily_loss_limit("alex", -312.5, 5.0)
    notify.alert_daily_loss_limit("bob", -312.5, 5.0)
    assert subjects[0] != subjects[1]


def test_end_of_day_digest_is_a_report_not_an_email(monkeypatch):
    """Owner 2026-11-15: "if I am getting too many emails I will ignore them". The digest and the post-trade review are
    routine reports; only real alerts are emailed."""
    mails, _ = _capture(monkeypatch)
    class _Db:
        def run(self, *a, **k): return [("TSLA", "BUY", "TIGHT_STOP", 3, "09:00", "10:00", "stop too tight")]
        def close(self): pass
    monkeypatch.setattr("db_pool.get_db", lambda: _Db())
    sent = []
    monkeypatch.setattr(notify, "_send", lambda ch, blocks, email=None, **k: sent.append((ch, email)) or True)
    notify.summarize_missed_trades()
    assert sent == [("alerts", False)]   # the digest really posted, and said it is not to be emailed ...
    assert mails == []
