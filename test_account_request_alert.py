"""Account-request email alert (owner 2026-11-02: "when requests in for a new user - I would like an email
alert - checking each day is fine").

run_cron_watch.check_account_requests rides on the hourly Cron Watch job. These tests hold the properties that
decide whether the alert is worth having: it tells once per request, never loses a request to a failed send,
forgets approved/rejected names so a re-request alerts again, sends nothing on a dry run, and can never cost the
job-failure alerts that the same job exists for.

FIXTURES ARE REAL REQUESTS. Each pending request is written by web_users.add_request into a temporary users
file (a non-default _USERS_FILE turns the Supabase path off -- web_users._remote_state_enabled), and removed by
the real approve_request / reject_request. Only the two outward edges are faked: web_store's JSON store (the
told list) and the email sender.
"""

import copy
import sys

import pytest
import yaml

import run_cron_watch as w
import trade_email
import web_store
from hvf_web import web_users


@pytest.fixture
def users_store(monkeypatch, tmp_path):
    """A private, file-only web_users store; the module cache is reset before and after."""
    def _reset():
        web_users._STATE_CACHE.update(at=0.0, source=None, users=None, remote_available=None, revision=None)
    _reset()
    monkeypatch.setattr(web_users, "_USERS_FILE", str(tmp_path / "web_users.json"))
    assert web_users._remote_state_enabled() is False      # apparatus: no Supabase read/write from here
    yield web_users
    _reset()


@pytest.fixture
def json_store(monkeypatch):
    """In-memory stand-in for web_store's JSON documents; records every save."""
    docs, saves = {}, []

    def load(key):
        return copy.deepcopy(docs.get(key))

    def save(key, payload):
        saves.append((key, copy.deepcopy(payload)))
        docs[key] = copy.deepcopy(payload)
        return True

    monkeypatch.setattr(web_store, "load_json_store", load)
    monkeypatch.setattr(web_store, "save_json_store", save)
    return docs, saves


@pytest.fixture
def mailer(monkeypatch):
    """Fake trade_email.send_simple_email; `result` decides what the send returns."""
    state = {"result": True, "sent": []}

    def send(subject=None, text=None, html=None, recipients=None, **kw):
        state["sent"].append({"subject": subject, "text": text, "html": html})
        return state["result"]

    monkeypatch.setattr(trade_email, "send_simple_email", send)
    return state


def _request(name, email=None, note=""):
    assert web_users.add_request(name, email or f"{name.lower()}@example.com", note) is True
    return name


def _told(docs):
    return (docs.get(w.REQUESTS_KEY) or {}).get("told")


# ── tell once, on change ────────────────────────────────────────────────────────────────────────────────

def test_no_pending_requests_sends_nothing_and_saves_nothing(users_store, json_store, mailer):
    docs, saves = json_store
    assert w.check_account_requests() == 0
    assert mailer["sent"] == []
    assert saves == [], "an unchanged (empty) told list must not be rewritten every hour"


def test_a_new_request_is_emailed_once_with_its_details_and_marked_told(users_store, json_store, mailer):
    docs, saves = json_store
    _request("Carol", "carol@example.com", "friend of Rich")
    assert w.check_account_requests() == 1
    assert len(mailer["sent"]) == 1
    mail = mailer["sent"][0]
    assert "Carol" in mail["subject"]
    assert "carol@example.com" in mail["text"] and "friend of Rich" in mail["text"]
    ts = web_users.list_requests()[0]["ts"]
    assert ts in mail["text"]
    assert _told(docs) == ["Carol"]
    assert saves and saves[-1][0] == w.REQUESTS_KEY

    # Next hour: nothing new -> no email, no save.
    n_saves = len(saves)
    assert w.check_account_requests() == 0
    assert len(mailer["sent"]) == 1, "a request already reported must not be emailed again"
    assert len(saves) == n_saves


def test_several_new_requests_go_in_one_email_listing_each(users_store, json_store, mailer):
    docs, _ = json_store
    _request("Carol")
    _request("Dave")
    assert w.check_account_requests() == 2
    assert len(mailer["sent"]) == 1
    body = mailer["sent"][0]["text"]
    assert "Carol" in body and "Dave" in body
    assert sorted(_told(docs)) == ["Carol", "Dave"]


def test_only_the_new_request_is_announced_when_an_old_one_is_still_waiting(users_store, json_store, mailer):
    docs, _ = json_store
    _request("Carol")
    w.check_account_requests()
    _request("Dave")
    assert w.check_account_requests() == 1
    mail = mailer["sent"][-1]
    assert "Dave" in mail["subject"] and "Carol" not in mail["subject"]
    assert sorted(_told(docs)) == ["Carol", "Dave"]


# ── never lose a request to a failed send ───────────────────────────────────────────────────────────────

def test_a_failed_send_saves_nothing_and_the_next_run_retries(users_store, json_store, mailer):
    docs, saves = json_store
    _request("Carol")
    mailer["result"] = False
    assert w.check_account_requests() == 1
    assert saves == [], "told must be saved ONLY after the email is accepted"
    assert _told(docs) is None

    mailer["result"] = True
    assert w.check_account_requests() == 1
    assert len(mailer["sent"]) == 2, "the failed request must be retried"
    assert _told(docs) == ["Carol"]


def test_a_failed_send_does_not_forget_requests_already_told(users_store, json_store, mailer):
    docs, saves = json_store
    _request("Carol")
    w.check_account_requests()
    _request("Dave")
    mailer["result"] = False
    n = len(saves)
    w.check_account_requests()
    assert len(saves) == n
    assert _told(docs) == ["Carol"]


# ── approved / rejected names drop out, so a re-request alerts again ────────────────────────────────────

def test_an_approved_request_drops_out_of_told(users_store, json_store, mailer):
    docs, _ = json_store
    _request("Carol")
    _request("Dave")
    w.check_account_requests()
    assert web_users.approve_request("Carol") is True
    assert w.check_account_requests() == 0
    assert _told(docs) == ["Dave"], "an approved name must leave the told list"
    assert len(mailer["sent"]) == 1


def test_a_rejected_then_re_requested_name_alerts_again(users_store, json_store, mailer):
    docs, _ = json_store
    _request("Carol")
    w.check_account_requests()
    web_users.reject_request("Carol")
    w.check_account_requests()
    assert _told(docs) == []
    _request("Carol", note="second try")
    assert w.check_account_requests() == 1
    assert len(mailer["sent"]) == 2
    assert "second try" in mailer["sent"][-1]["text"]


# ── dry run ─────────────────────────────────────────────────────────────────────────────────────────────

def test_dry_run_sends_nothing_and_saves_nothing(users_store, json_store, mailer):
    docs, saves = json_store
    _request("Carol")
    assert w.check_account_requests(dry_run=True) == 1
    assert mailer["sent"] == []
    assert saves == []


def test_dry_run_does_not_forget_approved_names(users_store, json_store, mailer):
    docs, saves = json_store
    _request("Carol")
    w.check_account_requests()
    web_users.reject_request("Carol")
    n = len(saves)
    w.check_account_requests(dry_run=True)
    assert len(saves) == n
    assert _told(docs) == ["Carol"]


def test_the_told_list_uses_its_own_key_only(users_store, json_store, mailer):
    docs, saves = json_store
    _request("Carol")
    w.check_account_requests()
    assert {k for k, _ in saves} == {"account_request_alerts"}


# ── untrusted input: the request comes from an unauthenticated public form ──────────────────────────────

def test_request_text_is_html_escaped_in_the_email(users_store, json_store, monkeypatch):
    """/api/request-account is PUBLIC. Its name and note reach the owner's inbox; send_simple_email builds
    the HTML part as f"<pre>{text}</pre>" when no html is given, so unescaped text becomes live markup (a
    stranger's "Approve here" link in an email that looks like it came from the site)."""
    captured = []
    monkeypatch.setenv("RESEND_API_KEY", "test-placeholder")
    monkeypatch.setattr(trade_email, "_send_via_resend",
                        lambda subject, text, html, charts, rcpts: captured.append(html) or True)
    _request("Mallory", note='<a href="https://evil.example/login">Approve here</a>')
    w.check_account_requests()
    assert captured, "the email was not sent through the real send_simple_email"
    assert '<a href="https://evil.example' not in captured[0]


def test_a_line_break_in_a_requested_name_cannot_reach_the_subject(users_store, json_store, mailer):
    """add_request strips only the ends of the name, so an inner CR/LF survives. In a Subject header it is
    header injection on the SMTP path, and EmailMessage refuses it outright (ValueError -> send returns False),
    which would make the alert fail -- and retry -- forever, hiding every later request with it."""
    _request("Eve\r\nBcc: x@example.com", "eve@example.com")
    w.check_account_requests()
    assert mailer["sent"]
    subj = mailer["sent"][0]["subject"]
    assert "\n" not in subj and "\r" not in subj


# ── main(): wired in, and never costs the failure alerts ────────────────────────────────────────────────

def _run_main(monkeypatch, failing, requests_fn, argv=()):
    calls = {"check": 0, "requests": []}

    def fake_check(**kw):
        calls["check"] += 1
        return failing

    def fake_requests(dry_run=False):
        calls["requests"].append(dry_run)
        return requests_fn()

    monkeypatch.setattr(w, "check", fake_check)
    monkeypatch.setattr(w, "check_account_requests", fake_requests)
    monkeypatch.setattr(sys, "argv", ["run_cron_watch.py", *argv])
    return w.main(), calls


def test_main_calls_the_request_check(monkeypatch):
    rc, calls = _run_main(monkeypatch, 0, lambda: 0)
    assert rc == 0 and calls["check"] == 1 and calls["requests"] == [False]


def test_main_passes_dry_run_through(monkeypatch):
    _, calls = _run_main(monkeypatch, 0, lambda: 0, argv=["--dry-run"])
    assert calls["requests"] == [True]


@pytest.mark.parametrize("failing,expected", [(0, 0), (2, 1)])
def test_an_exception_in_the_request_check_does_not_change_main_exit_code(monkeypatch, failing, expected):
    def boom():
        raise RuntimeError("supabase down")
    rc, calls = _run_main(monkeypatch, failing, boom)
    assert rc == expected
    assert calls["check"] == 1, "the failure check must still run"


def test_the_failure_check_runs_before_the_request_check(monkeypatch):
    order = []
    monkeypatch.setattr(w, "check", lambda **kw: order.append("check") or 0)
    monkeypatch.setattr(w, "check_account_requests", lambda dry_run=False: order.append("requests") or 0)
    monkeypatch.setattr(sys, "argv", ["run_cron_watch.py"])
    w.main()
    assert order == ["check", "requests"]


# ── the workflow: emails the same way as every job ──────────────────────────────────────────────────────

def _workflow():
    with open(".github/workflows/trading-cron-watch.yml", encoding="utf-8") as fh:
        text = fh.read()
    return text, yaml.safe_load(text)


def test_workflow_passes_every_email_secret():
    _, wf = _workflow()
    env = wf["jobs"]["watch"]["env"]
    for key in ("RESEND_API_KEY", "YAHOO_USER", "YAHOO_APP_PASSWORD"):
        assert env.get(key) == "${{ secrets.%s }}" % key, key


def test_workflow_has_no_github_schedule_and_is_dated():
    text, wf = _workflow()
    triggers = wf.get("on", wf.get(True))       # PyYAML reads a bare `on:` key as True
    assert "schedule" not in (triggers or {}), "GitHub-native schedule: blocks are banned"
    assert "2026-11-02" in wf["name"]
