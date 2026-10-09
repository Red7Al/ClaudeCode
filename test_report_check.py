"""/api/report-check -- the agent's read-only view of a login's live Scanner Report (owner 2026-10-09)."""

from hvf_web import server


def _client(monkeypatch, key="k" * 40):
    if key is None:
        monkeypatch.delenv("REPORT_CHECK_KEY", raising=False)
    else:
        monkeypatch.setenv("REPORT_CHECK_KEY", key)
    monkeypatch.setattr(server._wu, "token_for", lambda n: "tok-alex" if n == "Alex" else "")
    monkeypatch.setattr(server, "api_records", lambda: server.jsonify({"records": [{"ticker": "ZS"}],
                                                                        "seen": server.request.headers.get("X-Auth")}))
    monkeypatch.setattr(server, "api_config", lambda: server.jsonify({"limits": {"min_rvol": 1.7},
                                                                       "seen": server.request.headers.get("X-Auth")}))
    return server.app.test_client()


def test_the_route_does_not_exist_until_a_key_is_provisioned(monkeypatch):
    assert _client(monkeypatch, key=None).get("/api/report-check?login=Alex").status_code == 404


def test_a_wrong_or_missing_key_is_refused(monkeypatch):
    c = _client(monkeypatch)
    assert c.get("/api/report-check?login=Alex").status_code == 403
    assert c.get("/api/report-check?login=Alex", headers={"X-Report-Check-Key": "wrong"}).status_code == 403


def test_an_unknown_login_is_404(monkeypatch):
    c = _client(monkeypatch)
    assert c.get("/api/report-check?login=Nobody", headers={"X-Report-Check-Key": "k" * 40}).status_code == 404


def test_it_returns_exactly_what_that_logins_page_receives(monkeypatch):
    j = _client(monkeypatch).get("/api/report-check?login=Alex", headers={"X-Report-Check-Key": "k" * 40}).get_json()
    assert j["records"]["records"] == [{"ticker": "ZS"}] and j["config"]["limits"] == {"min_rvol": 1.7}
    assert j["records"]["seen"] == "tok-alex" == j["config"]["seen"], "built with the login's own session"
    assert "tok-alex" not in [j.get("login")], "the token itself is never returned"
