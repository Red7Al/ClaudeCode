"""Authorization regressions for security-sensitive web configuration."""

import gzip
import json

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
    monkeypatch.setattr(config_store, "set_value", lambda *args, **kwargs: writes.append((args, kwargs)))
    monkeypatch.setattr(server._wu, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))

    response = server.app.test_client().post(
        "/api/config", headers={"X-Auth": "token"}, json={"bridge": True})

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert writes == [(('exec_WEB_BRIDGE', 'true'), {'updated_by': 'Admin'})]
    assert events and events[0][0][0] == "Admin"


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
