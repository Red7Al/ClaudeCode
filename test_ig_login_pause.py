"""After IG refuses a login, every job stops logging in; one test login per 2 hours; the first accepted login lifts
the pause (owner 2026-10-19). MEASURED 2026-10-19: IG refused the login from 05:00 to 18:25 while the Order Bridge
(6 logins per run, every 2 h) and Closing Window (3 logins per run, every 10 min) kept trying. The REAL
IGSession.authenticate runs; only the HTTP call, the clock, the email and the app_config store (conftest) are
replaced. The assertions are the number of logins sent to IG and the emails."""
import json

import pytest
import requests

import ig_shim

INVALID = "error.security.invalid-details"
T0 = 1_792_386_000.0                                    # 2026-10-19 05:00 UTC


class _Resp:
    def __init__(self, status, code=INVALID):
        self.status_code, self._code = status, code
        self.headers = {"X-SECURITY-TOKEN": "t", "CST": "c"} if status == 200 else {}

    def json(self):
        return {} if self.status_code == 200 else {"errorCode": self._code}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error for url: /session", response=self)


@pytest.fixture
def ig(monkeypatch, _ig_login_pause_in_memory):
    """IG answers `state["status"]` to every login; the clock is state["now"]."""
    state = {"status": 401, "now": T0, "posts": [], "emails": []}
    monkeypatch.setenv("GITHUB_WORKFLOW", "Closing Window")
    monkeypatch.setattr(ig_shim, "_now", lambda: state["now"])
    monkeypatch.setattr(ig_shim.requests, "post",
                        lambda url, **k: state["posts"].append(state["now"]) or _Resp(state["status"]))
    import trade_email
    monkeypatch.setattr(trade_email, "send_simple_email", lambda **k: state["emails"].append(k) or True)
    state["store"] = _ig_login_pause_in_memory
    return state


def _job():
    """A fresh process: a new session, nothing remembered in memory -- only the shared record carries over."""
    ig_shim._LOCAL_BLOCKS.clear()
    return ig_shim.IGSession(api_key="k", username="u", password="p", account_id="ACC")


def _login(s):
    try:
        s.authenticate()
        return "ok"
    except ig_shim.IGLoginBlocked:
        return "blocked"
    except requests.HTTPError:
        return "refused"


def test_a_refusal_pauses_every_job_and_emails_once(ig):
    assert _login(_job()) == "refused"
    assert [_login(_job()) for _ in range(5)] == ["blocked"] * 5     # five other jobs: nothing sent to IG
    assert len(ig["posts"]) == 1
    assert [e["subject"] for e in ig["emails"]] == [f"IG refused the login ({INVALID}) - IG logins paused"]
    text = ig["emails"][0]["text"]
    assert "Job: Closing Window" in text and "next after 2026-10-19 06:55 UTC" in text


def test_the_same_process_does_not_log_in_again(ig):
    s = _job()
    assert [_login(s) for _ in range(4)] == ["refused", "blocked", "blocked", "blocked"]
    assert len(ig["posts"]) == 1


def test_a_refused_test_login_two_hours_on_keeps_the_pause_and_sends_no_new_email(ig):
    _login(_job())
    ig["now"] = T0 + 115 * 60 - 1
    assert _login(_job()) == "blocked"
    ig["now"] = T0 + 115 * 60
    assert _login(_job()) == "refused"                                # the one test login
    assert _login(_job()) == "blocked"                                # and the next job is held again
    assert len(ig["posts"]) == 2 and len(ig["emails"]) == 1
    rec = json.loads(ig["store"]["ig_login_pause:ACC"])
    assert rec["since"] == "2026-10-19 05:00 UTC" and rec["last_try"] == T0 + 115 * 60


def test_an_accepted_test_login_lifts_the_pause_for_every_job_and_says_so(ig):
    _login(_job())
    ig["now"], ig["status"] = T0 + 2 * 3600, 200
    assert _login(_job()) == "ok"
    assert [e["subject"] for e in ig["emails"]][-1] == "IG accepting logins again - jobs resumed"
    assert "paused since 2026-10-19 05:00 UTC" in ig["emails"][-1]["text"]
    ig["now"] += 60
    assert [_login(_job()) for _ in range(3)] == ["ok"] * 3          # no waiting for anyone's next test login
    assert len(ig["posts"]) == 5 and len(ig["emails"]) == 2


def test_a_pause_lifted_by_another_job_ends_this_processes_too(ig):
    s = _job()
    _login(s)                                                         # this process remembers the pause
    ig["now"], ig["status"] = T0 + 2 * 3600, 200
    other = ig_shim.IGSession(api_key="k", username="u", password="p", account_id="ACC")
    saved = dict(ig_shim._LOCAL_BLOCKS)
    ig_shim._LOCAL_BLOCKS.clear()
    assert _login(other) == "ok"                                      # another job's test login is accepted
    ig_shim._LOCAL_BLOCKS.clear()
    ig_shim._LOCAL_BLOCKS.update(saved)                               # back in the first process
    ig["now"] += 60
    assert _login(s) == "ok"


def test_an_unreadable_store_still_holds_this_process(ig, monkeypatch):
    s = _job()
    _login(s)
    import config_store
    monkeypatch.setattr(config_store, "get_value", lambda k, d="": d)  # a database outage reads as "no record"
    assert [_login(s) for _ in range(3)] == ["blocked"] * 3
    assert len(ig["posts"]) == 1


@pytest.mark.parametrize("status", [500, 503])
def test_an_ig_server_error_is_not_a_refusal_and_pauses_nothing(ig, status):
    ig["status"] = status
    assert _login(_job()) == "refused"                                # raised as before: HTTPError
    assert ig["store"] == {} and ig["emails"] == []
    assert _login(_job()) == "refused" and len(ig["posts"]) == 2


def test_a_refusal_without_an_errorcode_names_the_status(ig, monkeypatch):
    monkeypatch.setattr(ig_shim.requests, "post", lambda url, **k: _Resp(403, code=None))
    _login(_job())
    assert ig["emails"][0]["subject"] == "IG refused the login (HTTP 403) - IG logins paused"


def test_while_paused_callers_see_igs_last_refusal_without_contacting_ig(ig):
    """The open/close/amend/stop handlers catch requests.HTTPError and read e.response.status_code: a pause must
    look to them exactly like IG's refusal did."""
    _login(_job())
    with pytest.raises(requests.HTTPError) as e:
        _job().get("/positions")
    assert e.value.response.status_code == 401 and e.value.response.json() == {"errorCode": INVALID}
    assert len(ig["posts"]) == 1


def test_the_audits_record_paused_and_send_no_email(ig, monkeypatch):
    monkeypatch.setattr(ig_shim, "session", _job())
    _login(_job())
    note = ig_shim.login_for_job("price audit")
    assert note.startswith("IG LOGINS PAUSED") and len(ig["posts"]) == 1 and len(ig["emails"]) == 1


def test_other_accounts_are_not_paused(ig):
    _login(_job())
    ig["status"] = 200
    other = ig_shim.IGSession(api_key="k", username="u2", password="p", account_id="OTHER")
    assert _login(other) == "ok"


def test_today_replayed_refused_logins_fall_from_about_267_to_7(ig):
    """2026-10-19, measured job timings: Closing Window every 10 min 05:00-20:50 (3 logins per run), Order Bridge
    every 2 h 06:00-22:00 (6 per run). IG refused every login until 18:25. Each run is a fresh process."""
    runs = sorted([(T0 + m * 60, 3) for m in range(0, 15 * 60 + 51, 10)] +
                  [(T0 + 3600 + h * 7200, 6) for h in range(9)])
    refused_until = T0 + 13 * 3600 + 25 * 60
    for at, logins in runs:
        ig["now"] = at
        ig["status"] = 401 if at < refused_until else 200
        s = _job()
        for _ in range(logins):
            s._token = None
            _login(s)
    refused = [t for t in ig["posts"] if t < refused_until]
    assert len(refused) == 7                                          # 05:00, then 07:00, 09:00 ... 17:00
    assert [e["subject"] for e in ig["emails"]] == [f"IG refused the login ({INVALID}) - IG logins paused",
                                                    "IG accepting logins again - jobs resumed"]
    first_ok = min(t for t in ig["posts"] if t >= refused_until)
    assert first_ok - refused_until <= 2 * 3600                       # resumed within 2 h, with nobody acting


def test_while_paused_a_working_order_cancel_returns_false_as_it_did_on_a_refusal(ig, monkeypatch):
    monkeypatch.setattr(ig_shim, "session", _job())
    _login(_job())
    assert ig_shim.delete_working_order("DIAAAA", reason="test") is False
    assert len(ig["posts"]) == 1                                      # the cancel never reached IG
