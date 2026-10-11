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


def test_any_login_but_the_owners_is_refused_even_with_the_key(monkeypatch):
    # Owner 2026-10-11 (ninth complaint): the route answered for ANY login. "Bob" is a real login here, so the
    # refusal is the owner-only rule, not "unknown login"; his records and settings are never built.
    c = _client(monkeypatch)
    monkeypatch.setattr(server._wu, "token_for", lambda n: {"Alex": "tok-alex", "Bob": "tok-bob"}.get(n, ""))
    built = []
    monkeypatch.setattr(server, "api_records", lambda: built.append(1) or server.jsonify({}))
    for who in ("Bob", "Nobody", "alex", ""):
        r = c.get(f"/api/report-check?login={who}", headers={"X-Report-Check-Key": "k" * 40})
        assert r.status_code == 403 and "tok-bob" not in r.get_data(as_text=True), who
    assert built == []
    assert c.get("/api/report-check?login=Alex", headers={"X-Report-Check-Key": "k" * 40}).status_code == 200


def test_it_returns_exactly_what_that_logins_page_receives(monkeypatch):
    j = _client(monkeypatch).get("/api/report-check?login=Alex", headers={"X-Report-Check-Key": "k" * 40}).get_json()
    assert j["records"]["records"] == [{"ticker": "ZS"}] and j["config"]["limits"] == {"min_rvol": 1.7}
    assert j["records"]["seen"] == "tok-alex" == j["config"]["seen"], "built with the login's own session"
    assert "tok-alex" not in [j.get("login")], "the token itself is never returned"


def test_the_process_query_answers_with_this_processes_pid(monkeypatch):
    import os
    j = _client(monkeypatch).get("/api/report-check?process=1", headers={"X-Report-Check-Key": "k" * 40}).get_json()
    assert j["pid"] == os.getpid() and "module_loaded_at" in j


def test_the_process_query_still_needs_the_key(monkeypatch):
    assert _client(monkeypatch).get("/api/report-check?process=1").status_code == 403


def test_the_summary_query_says_whether_the_file_is_in_use(monkeypatch):
    c = _client(monkeypatch)
    monkeypatch.setattr(server, "_load_snapshot", lambda: {"generated_utc": "G"})
    monkeypatch.setattr(server, "_summary", lambda: {"generated_utc": "G", "built_at": "B", "sections": {"wk52": {}}})
    j = c.get("/api/report-check?summary=1", headers={"X-Report-Check-Key": "k" * 40}).get_json()
    assert j["in_use"] is True and j["sections"] == ["wk52"]
    monkeypatch.setattr(server, "_summary", lambda: {"generated_utc": "OLD", "sections": {}})
    j = c.get("/api/report-check?summary=1", headers={"X-Report-Check-Key": "k" * 40}).get_json()
    assert j["in_use"] is False
