"""Owner 2026-11-14: "the slack code does not need to go as it may be used in the future BUT no alerts should currently
use slack". Measured the same day: slack_ch_alerts was unset, the code treated unset as ON, and a webhook was configured."""
import pathlib
import re
import subprocess

import pytest

import notify

ROOT = pathlib.Path(__file__).parent


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    notify._SLACK_FLAGS.clear()
    yield
    notify._SLACK_FLAGS.clear()


def _cfg(monkeypatch, fn):
    import config_store
    monkeypatch.setattr(config_store, "get_value", fn)


def test_unset_means_off(monkeypatch):
    _cfg(monkeypatch, lambda k, d=None: d)
    assert notify.slack_enabled("alerts") is False


def test_unreadable_config_means_off_not_on(monkeypatch):
    def boom(k, d=None): raise RuntimeError("db down")
    _cfg(monkeypatch, boom)
    assert notify.slack_enabled("alerts") is False


@pytest.mark.parametrize("v,want", [("0", False), ("off", False), ("", False), ("1", True), ("true", True), ("ON", True)])
def test_only_an_explicit_on_enables_a_channel(monkeypatch, v, want):
    _cfg(monkeypatch, lambda k, d=None: v)
    assert notify.slack_enabled("alerts") is want


def test_an_alert_posts_nothing_to_slack_when_unset(monkeypatch):
    _cfg(monkeypatch, lambda k, d=None: d)
    monkeypatch.setitem(notify.WEBHOOKS, "alerts", "http://example.invalid/hook")
    def no_post(*a, **k): raise AssertionError("posted to Slack")
    monkeypatch.setattr(notify.requests, "post", no_post)
    monkeypatch.setattr("trade_email.send_simple_email", lambda *a, **k: True)
    monkeypatch.setattr("db_pool.get_db", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    notify._EMAILED_NO_DB.clear()
    notify.alert_missed_trade("AAPL", "BUY", "[working order] NOT PLACED -- the request failed (x) and IG holds no such order or position; safe to retry", "", email=True)
    assert notify._send("alerts", []) is False


# Every module that can post to a Slack webhook must ask slack_enabled first. Exempt, with the reason:
EXEMPT = {"run_slack_test.py": "the explicit manual webhook test",
          "hvf_web/server.py": "admin secrets form lists the names; its requests.post is not a Slack post"}


def test_every_slack_poster_asks_slack_enabled():
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    bad = []
    for f in files:
        if pathlib.Path(f).name.startswith(("test_", "conftest")) or f.startswith("skills_src") or f in EXEMPT:
            continue
        t = (ROOT / f).read_text(encoding="utf8", errors="ignore")
        posts_slack = re.search(r"hooks\.slack\.com|WEBHOOKS|SLACK_(?:TRADES|SIGNALS|ALERTS|WEEKLY|DAILY|ORDERS|TWITTER|RW_HVF)", t)
        if posts_slack and re.search(r"\.post\(", t) and "slack_enabled" not in t:
            bad.append(f)
    assert not bad, f"these post to Slack without asking slack_enabled: {bad}"


# The one exception: the X-publication channel is not an alert, so it keeps its old default (ON when unset).
def test_the_x_publication_channel_keeps_its_old_default(monkeypatch):
    _cfg(monkeypatch, lambda k, d=None: d)
    assert notify.slack_enabled("twitter") is True
    notify._SLACK_FLAGS.clear()
    _cfg(monkeypatch, lambda k, d=None: "0")
    assert notify.slack_enabled("twitter") is False          # an explicit off is still honoured
    notify._SLACK_FLAGS.clear()
    def boom(k, d=None): raise RuntimeError("db down")
    _cfg(monkeypatch, boom)
    assert notify.slack_enabled("twitter") is True and notify.slack_enabled("alerts") is False


def test_every_alert_channel_is_off_when_unset(monkeypatch):
    _cfg(monkeypatch, lambda k, d=None: d)
    for ch in ("alerts", "daily", "signals", "trades", "weekly", "orders"):
        notify._SLACK_FLAGS.clear()
        assert notify.slack_enabled(ch) is False, ch


def test_the_admin_page_does_not_decide_the_default_on_its_own():
    """One fact, one function: the page showed channels ON from its own default while the code had them OFF."""
    src = (ROOT / "hvf_web" / "server.py").read_text(encoding="utf8")
    assert 'get_value(f"slack_ch_{ch}", "1")' not in src and "slack_enabled(ch)" in src
