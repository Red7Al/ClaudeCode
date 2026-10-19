"""ig_shim._try_login / login_for_job: a refused login is sent ONCE; only a login that never reached IG is
retried (owner 2026-10-18: one refused login, not 1,140; "what about handling temporary network
glitch"). The REAL _try_login, login_for_job and IGSession.authenticate run; only the HTTP call, the 30 s wait
and the email are replaced. The assertions are the number of logins sent to IG."""
import pytest
import requests
from urllib3.exceptions import MaxRetryError, NewConnectionError

import ig_shim
import price_audit
import run_data_quality_audit as dqa
from test_price_audit_prev_bar import PREV, _frame, _wire

INVALID = "error.security.invalid-details"


def _no_connection():
    """What requests raises when the connection is never made (refused / DNS): IG never saw the login."""
    return requests.ConnectionError(MaxRetryError(None, "/session", NewConnectionError(None, "refused")))


class _Resp:
    def __init__(self, status):
        self.status_code = status
        self.headers = {"X-SECURITY-TOKEN": "t", "CST": "c"} if status == 200 else {}

    def json(self):
        return {} if self.status_code == 200 else {"errorCode": INVALID}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error for url: /session", response=self)


@pytest.fixture
def ig(monkeypatch):
    """script(...) sets IG's answers in order: an int is an HTTP status, anything else is called and raised."""
    posts, waits, emails = [], [], []
    monkeypatch.delenv("GITHUB_WORKFLOW", raising=False)
    monkeypatch.setattr(ig_shim, "session", ig_shim.IGSession(api_key="k", username="u", password="p"))
    monkeypatch.setattr(ig_shim, "get_epic", lambda t: "KA.D.X.DAILY.IP")
    monkeypatch.setattr(ig_shim, "_wait", lambda s: waits.append(s), raising=False)
    import trade_email
    monkeypatch.setattr(trade_email, "send_simple_email", lambda **k: emails.append(k) or True)

    def script(*answers):
        queue = list(answers)

        def post(url, **k):
            posts.append(url)
            a = queue.pop(0)
            if isinstance(a, int):
                return _Resp(a)
            raise a()
        monkeypatch.setattr(ig_shim.requests, "post", post)
    return posts, waits, emails, script


@pytest.mark.parametrize("answers, outcome, logins", [
    ((401,), "refused", 1),
    ((403,), "refused", 1),
    ((200,), "ok", 1),
    ((_no_connection, _no_connection, 200), "ok", 3),
    ((requests.ConnectTimeout, 200), "ok", 2),
    ((503,), "unreachable", 1),                         # IG received it and may count it: never resent
    ((_no_connection, 401), "refused", 2),              # a refusal during a retry ends it at once
    ((_no_connection, _no_connection, _no_connection), "unreachable", 3),
    # IG may have received these logins -- and refused them -- so they are NEVER retried:
    ((requests.ReadTimeout,), "unreachable", 1),
    ((requests.ConnectionError,), "unreachable", 1),    # connection dropped: no proof IG never saw it
    ((requests.exceptions.ChunkedEncodingError,), "unreachable", 1),
    ((KeyError,), "error", 1),                          # a code fault, not a network one
    ((lambda: requests.HTTPError("no status attached"),), "error", 1),     # no proof IG answered; never retried
])
def test_login_outcomes(ig, answers, outcome, logins):
    posts, waits, emails, script = ig
    script(*answers)
    assert ig_shim._try_login()[0] == outcome
    assert len(posts) == logins
    assert waits == [30] * (logins - 1)                 # 30 s between attempts, none after the last


def test_a_refusal_carries_igs_own_reason(ig):
    posts, waits, emails, script = ig
    script(401)
    assert ig_shim._try_login() == ("refused", f"HTTP 401, errorCode {INVALID}", 1)


@pytest.mark.parametrize("answers, reached", [((_no_connection, _no_connection, _no_connection), 0),
                                              ((requests.ReadTimeout,), 1), ((503,), 1), ((KeyError,), 0),
                                              ((_no_connection, 200), 1)])
def test_only_logins_that_reached_ig_are_counted(ig, answers, reached):
    posts, waits, emails, script = ig
    script(*answers)
    assert ig_shim._try_login()[2] == reached


def test_never_more_than_three_attempts(ig):
    posts, waits, emails, script = ig
    script(*([_no_connection] * 10))
    assert ig_shim._try_login()[2] == 0 and len(posts) == 3     # 3 tries, none of which reached IG


@pytest.mark.parametrize("answers, subject", [
    ((401,), "IG refused the login (error.security.invalid-details) - IG logins paused"),     # emailed once, by the pause (test_ig_login_pause)
    ((KeyError,), "IG login fault - Data Quality Audit"),       # a code fault would otherwise be silent nightly
    ((requests.ReadTimeout,), None),
    ((200,), None),
])
def test_a_refusal_or_a_fault_is_emailed_a_network_glitch_is_not(ig, answers, subject):
    posts, waits, emails, script = ig
    script(*answers)
    note = ig_shim.login_for_job("Data Quality Audit")
    assert [e["subject"] for e in emails] == ([subject] if subject else [])
    assert (note is None) == (answers == (200,))


def test_the_refusal_email_names_igs_reason_and_the_workflow(ig, monkeypatch):
    posts, waits, emails, script = ig
    monkeypatch.setenv("GITHUB_WORKFLOW", "Price History Audit (2026-10-18)")
    script(_no_connection, 401)                         # 1st never reached IG; 2nd refused
    note = ig_shim.login_for_job("price audit")
    assert len(posts) == 2 and INVALID in note
    assert [e["subject"] for e in emails] == [f"IG refused the login ({INVALID}) - IG logins paused"]
    assert "Job: Price History Audit (2026-10-18)" in emails[0]["text"]


def test_price_audit_refused_logs_in_once_and_says_refused(ig, monkeypatch):
    posts, waits, emails, script = ig
    tickers = [f"T{i}.L" for i in range(20)]
    db, _ = _wire(monkeypatch, {t: _frame([PREV]) for t in tickers}, {t: _frame([PREV]) for t in tickers})
    script(401)
    price_audit.run("daily", 5, "YF", use_ig=True, max_seconds=0)
    assert len(posts) == 1 and len(emails) == 1
    assert f"IG LOGIN REFUSED (HTTP 401, errorCode {INVALID})" in db.inserts[-1]["no"]


def test_price_audit_unreachable_says_unreachable_and_sends_no_email(ig, monkeypatch):
    posts, waits, emails, script = ig
    tickers = [f"T{i}.L" for i in range(20)]
    db, _ = _wire(monkeypatch, {t: _frame([PREV]) for t in tickers}, {t: _frame([PREV]) for t in tickers})
    script(_no_connection, _no_connection, _no_connection)
    price_audit.run("daily", 5, "YF", use_ig=True, max_seconds=0)
    assert len(posts) == 3 and emails == []
    assert "IG UNREACHABLE" in db.inserts[-1]["no"] and "REFUSED" not in db.inserts[-1]["no"]


def test_price_audit_glitch_then_ok_runs_the_ig_check(ig, monkeypatch):
    posts, waits, emails, script = ig
    tickers = [f"T{i}.L" for i in range(4)]
    db, _ = _wire(monkeypatch, {t: _frame([PREV]) for t in tickers}, {t: _frame([PREV]) for t in tickers})
    verified = []
    monkeypatch.setattr(price_audit, "_ig_verify", lambda tk, d: verified.append(tk) or (0, 0, 9000))
    script(_no_connection, 200)
    price_audit.run("daily", 5, "YF", use_ig=True, max_seconds=0)
    assert len(posts) == 2 and verified == tickers and emails == []
    assert "IG LOGIN" not in db.inserts[-1]["no"] and "IG UNREACHABLE" not in db.inserts[-1]["no"]


def test_data_quality_refused_logs_in_once_emails_and_records_it(ig, monkeypatch):
    posts, waits, emails, script = ig
    import web_store
    monkeypatch.setattr(web_store, "append_batch", lambda src, text, by=None: None)
    monkeypatch.setattr(dqa.sys, "argv", ["run_data_quality_audit.py"] + [f"T{i}.L" for i in range(25)])
    monkeypatch.setattr(dqa, "_audit_ticker", lambda t: pytest.fail("IG comparison ran after a refusal"))
    monkeypatch.setattr(dqa, "_identity_sweep_all_cached", lambda: [{"ticker": "X"}])
    monkeypatch.setattr(dqa, "_save", lambda rows: None)
    monkeypatch.setattr(dqa, "_post_slack", lambda rows, rem: None)
    monkeypatch.setattr(dqa, "_absurd_outcomes", lambda *a, **k: (_ for _ in ()).throw(StopIteration))
    script(401)
    with pytest.raises(StopIteration):
        dqa.main()
    assert len(posts) == 1 and [e["subject"] for e in emails] == [f"IG refused the login ({INVALID}) - IG logins paused"]
