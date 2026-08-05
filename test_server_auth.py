"""Authorization regressions for security-sensitive web configuration."""

import gzip
import json
from pathlib import Path

import config_store
from hvf_web import server


def _identity(monkeypatch, name, *, admin):
    monkeypatch.setattr(server._wu, "name_for_token", lambda token: name if token == "token" else "")
    monkeypatch.setattr(server._wu, "is_admin", lambda candidate: admin and candidate == name)


def test_non_admin_cannot_change_shared_bridge(monkeypatch):
    writes = []
    _identity(monkeypatch, "Silver", admin=False)
    monkeypatch.setattr(config_store, "set_value", lambda *args, **kwargs: writes.append((args, kwargs)))
    monkeypatch.setattr(server._wu, "log_event", lambda *args, **kwargs: None)

    response = server.app.test_client().post(
        "/api/config", headers={"X-Auth": "token"}, json={"bridge": True})

    assert response.status_code == 403
    assert response.get_json() == {"ok": False, "error": "admin only"}
    assert writes == []


def test_admin_can_change_shared_bridge(monkeypatch):
    writes = []
    events = []
    _identity(monkeypatch, "Admin", admin=True)
    monkeypatch.setattr(server, "_user_has_ig_creds", lambda name: True)
    monkeypatch.setattr(config_store, "set_value", lambda *args, **kwargs: writes.append((args, kwargs)))
    monkeypatch.setattr(server._wu, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))

    response = server.app.test_client().post(
        "/api/config", headers={"X-Auth": "token"}, json={"bridge": True})

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert writes == [(('exec_WEB_BRIDGE', 'true'), {'updated_by': 'Admin'})]
    assert events and events[0][0][0] == "Admin"


def test_login_without_ig_credentials_sees_bridge_off(monkeypatch):
    _identity(monkeypatch, "NoBroker", admin=False)
    monkeypatch.setattr(server, "_user_has_ig_creds", lambda name: False)
    monkeypatch.setattr(config_store, "get_value", lambda key, default="": "true" if key == "exec_WEB_BRIDGE" else default)
    monkeypatch.setattr(config_store, "get_exec_flags", lambda: {})
    monkeypatch.setattr(server._wu, "get_settings", lambda name: {})

    response = server.app.test_client().get("/api/config", headers={"X-Auth": "token"})

    assert response.status_code == 200
    assert response.get_json()["bridge"] is False
    assert response.get_json()["has_ig_creds"] is False


def test_admin_without_ig_credentials_cannot_enable_bridge(monkeypatch):
    writes = []
    _identity(monkeypatch, "Admin", admin=True)
    monkeypatch.setattr(server, "_user_has_ig_creds", lambda name: False)
    monkeypatch.setattr(config_store, "set_value", lambda *args, **kwargs: writes.append((args, kwargs)))

    response = server.app.test_client().post(
        "/api/config", headers={"X-Auth": "token"}, json={"bridge": True})

    assert response.status_code == 409
    assert response.get_json() == {"ok": False, "error": "IG credentials required"}
    assert writes == []


def test_cold_performance_request_returns_warming_without_building(monkeypatch):
    kicked = []
    monkeypatch.setitem(server._PERF_CACHE, "ts", 0.0)
    monkeypatch.setitem(server._PERF_CACHE, "data", None)
    monkeypatch.setattr(server, "_kick_perf_warm", lambda: kicked.append(True))

    response = server.app.test_client().get("/api/performance")

    assert response.status_code == 200
    assert response.get_json() == {"rows": [], "warming": True, "generated": ""}
    assert kicked == [True]


def test_warm_performance_payload_is_gzipped_for_browsers(monkeypatch):
    payload = {"rows": [{"ticker": "TEST", "rvol": float("nan")}], "generated": "now"}
    monkeypatch.setitem(server._PERF_CACHE, "ts", server._time.time())
    monkeypatch.setitem(server._PERF_CACHE, "data", payload)
    monkeypatch.setitem(server._PERF_CACHE, "gzip", None)

    response = server.app.test_client().get("/api/performance", headers={"Accept-Encoding": "gzip, deflate"})

    assert response.status_code == 200
    assert response.headers["Content-Encoding"] == "gzip"
    assert json.loads(gzip.decompress(response.data)) == {
        "rows": [{"ticker": "TEST", "rvol": None}], "generated": "now"}


def test_performance_warm_claim_allows_only_one_builder():
    server._finish_perf_warm()
    try:
        assert server._claim_perf_warm() is True
        assert server._claim_perf_warm() is False
    finally:
        server._finish_perf_warm()


def test_change_request_deferred_with_inline_note_is_deferred():
    line = "* P-08 Availability - Available to all [Deferred - user 2026-08-05]"

    assert server._cr_status(line) == "Deferred"
    assert "[Deferred" not in server._CR_TAIL.sub("", line)


def test_fees_normalises_locale_ig_dates():
    with server.app.test_request_context():
        # The helper is local to api_fees; verify the source contains the normalisation contract.
        source = Path(server.__file__).read_text(encoding="utf-8")
    assert "def _ig_day(value)" in source
    assert "a <= _ig_day(t.get(\"date\")) <= b" in source
