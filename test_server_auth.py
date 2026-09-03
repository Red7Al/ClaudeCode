"""Authorization regressions for security-sensitive web configuration."""

import gzip
import json
from contextlib import nullcontext
from pathlib import Path

import pytest

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


def test_non_admin_cannot_create_user(monkeypatch):
    _identity(monkeypatch, "Silver", admin=False)
    calls = []
    monkeypatch.setattr(server._wu, "admin_create_user", lambda *a, **k: calls.append((a, k)) or True)

    response = server.app.test_client().post(
        "/api/users", headers={"X-Auth": "token"},
        json={"name": "NewPerson", "action": "create", "email": "new@example.com"})

    assert response.status_code == 403
    assert calls == []


def test_admin_can_create_user(monkeypatch):
    _identity(monkeypatch, "Admin", admin=True)
    created = []
    events = []
    monkeypatch.setattr(server._wu, "admin_create_user",
                        lambda name, email, sub, adm, sup: created.append((name, email, sub, adm, sup)) or True)
    monkeypatch.setattr(server._wu, "request_reset_code", lambda *a, **k: True)
    monkeypatch.setattr(server._wu, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))
    monkeypatch.setattr(server._wu, "list_users", lambda: [])
    monkeypatch.setattr(server._wu, "list_requests", lambda: [])

    response = server.app.test_client().post(
        "/api/users", headers={"X-Auth": "token"},
        json={"name": "NewPerson", "action": "create", "email": "new@example.com",
              "subscription": "silver", "admin": False})

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert created == [("NewPerson", "new@example.com", "silver", False, False)]
    assert events and events[0][0][0] == "Admin"


def test_admin_create_user_passes_support_flag(monkeypatch):
    """Support can be set at creation (user 2026-08-08): the create action forwards `support`."""
    _identity(monkeypatch, "Admin", admin=True)
    created = []
    monkeypatch.setattr(server._wu, "admin_create_user",
                        lambda name, email, sub, adm, sup: created.append((name, email, sub, adm, sup)) or True)
    monkeypatch.setattr(server._wu, "request_reset_code", lambda *a, **k: True)
    monkeypatch.setattr(server._wu, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(server._wu, "list_users", lambda: [])
    monkeypatch.setattr(server._wu, "list_requests", lambda: [])

    response = server.app.test_client().post(
        "/api/users", headers={"X-Auth": "token"},
        json={"name": "Sup", "action": "create", "email": "sup@example.com",
              "subscription": "guest", "admin": False, "support": True})

    assert response.status_code == 200
    assert created == [("Sup", "sup@example.com", "guest", False, True)]


def test_approving_request_emails_the_new_user(monkeypatch):
    """user 2026-08-08: approving a pending request must notify the person (fire a setup email to
    their registered address), matching the '+ Add user' path — not create the account silently."""
    _identity(monkeypatch, "Admin", admin=True)
    reset_calls = []
    monkeypatch.setattr(server._wu, "approve_request", lambda target: True)
    monkeypatch.setattr(server._wu, "email_for", lambda target: "carl@example.com")
    monkeypatch.setattr(server._wu, "request_reset_code",
                        lambda name, email: reset_calls.append((name, email)) or True)
    monkeypatch.setattr(server._wu, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(server._wu, "list_users", lambda: [])
    monkeypatch.setattr(server._wu, "list_requests", lambda: [])

    response = server.app.test_client().post(
        "/api/users", headers={"X-Auth": "token"},
        json={"name": "Carl", "action": "approve"})

    assert response.status_code == 200
    assert reset_calls == [("Carl", "carl@example.com")]


def test_set_temp_password_works_for_non_ig_account(monkeypatch):
    """user 2026-08-08: admin can set a temporary password for an account WITHOUT IG credentials."""
    _identity(monkeypatch, "Admin", admin=True)
    monkeypatch.setattr(server, "_user_has_ig_creds", lambda n: False)
    calls = []
    monkeypatch.setattr(server._wu, "reset_password",
                        lambda name, email, pwd, ip="": calls.append((name, email, pwd)) or True)
    monkeypatch.setattr(server._wu, "email_for", lambda n: "carl@example.com")
    monkeypatch.setattr(server._wu, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(server._wu, "list_users", lambda: [])

    response = server.app.test_client().post(
        "/api/users", headers={"X-Auth": "token"},
        json={"name": "Carl", "action": "set_temp_password", "new_pwd": "TempPass1"})

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert calls == [("Carl", "carl@example.com", "TempPass1")]


def test_set_temp_password_blocked_for_ig_linked_account(monkeypatch):
    """The no-IG guard is enforced server-side, not just hidden in the UI."""
    _identity(monkeypatch, "Admin", admin=True)
    monkeypatch.setattr(server, "_user_has_ig_creds", lambda n: True)
    calls = []
    monkeypatch.setattr(server._wu, "reset_password", lambda *a, **k: calls.append(a) or True)
    monkeypatch.setattr(server._wu, "email_for", lambda n: "trader@example.com")
    monkeypatch.setattr(server._wu, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(server._wu, "list_users", lambda: [])

    response = server.app.test_client().post(
        "/api/users", headers={"X-Auth": "token"},
        json={"name": "Trader", "action": "set_temp_password", "new_pwd": "TempPass1"})

    assert response.status_code == 400
    assert calls == []   # never touched the password


def test_set_temp_password_denied_for_non_admin(monkeypatch):
    """Only admins can use it (the whole /api/users route is admin-gated)."""
    _identity(monkeypatch, "Silver", admin=False)
    monkeypatch.setattr(server, "_user_has_ig_creds", lambda n: False)
    calls = []
    monkeypatch.setattr(server._wu, "reset_password", lambda *a, **k: calls.append(a) or True)

    response = server.app.test_client().post(
        "/api/users", headers={"X-Auth": "token"},
        json={"name": "Carl", "action": "set_temp_password", "new_pwd": "TempPass1"})

    assert response.status_code == 403
    assert calls == []


def _identity_with_support(monkeypatch, name, *, admin, support):
    monkeypatch.setattr(server._wu, "name_for_token", lambda token: name if token == "token" else "")
    monkeypatch.setattr(server._wu, "is_admin", lambda candidate: admin and candidate == name)
    monkeypatch.setattr(server._wu, "is_support", lambda candidate: support and candidate == name)


def test_support_role_can_read_ops_endpoints_but_not_admin_only(monkeypatch):
    """Support (2026-08-07): read-only System Logs / Batch Activity / Scheduled Jobs, nothing else."""
    _identity_with_support(monkeypatch, "Ops", admin=False, support=True)
    monkeypatch.setattr(server, "_read_json_entries", lambda *a, **k: [])
    monkeypatch.setattr("web_store.list_batch", lambda: [], raising=False)

    batch = server.app.test_client().get("/api/batch-activity", headers={"X-Auth": "token"})
    syslogs = server.app.test_client().get("/api/system-logs", headers={"X-Auth": "token"})
    version = server.app.test_client().get("/api/version-history", headers={"X-Auth": "token"})
    users = server.app.test_client().get("/api/users", headers={"X-Auth": "token"})

    assert batch.status_code == 200
    assert syslogs.status_code == 200
    assert version.status_code == 403   # Support does not get Version History
    assert users.status_code == 403     # or User Management


def test_guest_without_support_cannot_read_ops_endpoints(monkeypatch):
    _identity_with_support(monkeypatch, "Guest", admin=False, support=False)

    response = server.app.test_client().get("/api/batch-activity", headers={"X-Auth": "token"})

    assert response.status_code == 403


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
    # A token is required from 2026-08-28 -- see test_performance_is_not_served_without_a_token.
    monkeypatch.setattr(server._wu, "name_for_token", lambda token: "alex" if token else "")

    response = server.app.test_client().get("/api/performance", headers={"X-Auth": "t"})

    assert response.status_code == 200
    assert response.get_json() == {"rows": [], "warming": True, "generated": ""}
    assert kicked == [True]


def test_warm_performance_payload_is_gzipped_for_browsers(monkeypatch):
    payload = {"rows": [{"ticker": "TEST", "rvol": float("nan")}], "generated": "now"}
    monkeypatch.setitem(server._PERF_CACHE, "ts", server._time.time())
    monkeypatch.setitem(server._PERF_CACHE, "data", payload)
    monkeypatch.setitem(server._PERF_CACHE, "gzip", None)

    monkeypatch.setattr(server._wu, "name_for_token", lambda token: "alex" if token else "")
    response = server.app.test_client().get(
        "/api/performance", headers={"Accept-Encoding": "gzip, deflate", "X-Auth": "t"})

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


def test_fees_returns_a_third_two_months_ago_period(monkeypatch):
    """ChangeRequest P-09 (2026-08-07): "add another previous month tab so we can see two previous months
    plus current month" — /api/fees now returns prev_month alongside last_month/this_month, covering the
    calendar month before last_month, via the same app-ledger fallback path (no IG session -> _period())."""
    import datetime as _dt

    class FakeDb:
        def run(self, query, **params):
            if "trade_log" in query:
                return []
            return [(0, 0, 0, 0)]

        def close(self):
            pass

    monkeypatch.setattr("db_pool.get_db", lambda: FakeDb(), raising=False)
    monkeypatch.setattr(server._wu, "name_for_token", lambda token: "")   # no viewer -> skips the IG branch entirely

    response = server.app.test_client().get("/api/fees")

    assert response.status_code == 200
    j = response.get_json()
    assert j["prev_month"] is not None and j["last_month"] is not None and j["this_month"] is not None

    today = _dt.date.today()
    first_this = today.replace(day=1)
    last_month_end = first_this - _dt.timedelta(days=1)
    first_last = last_month_end.replace(day=1)
    prev_month_end = first_last - _dt.timedelta(days=1)
    first_prev = prev_month_end.replace(day=1)

    assert j["prev_month"]["start"] == first_prev.isoformat()
    assert j["prev_month"]["end"] == prev_month_end.isoformat()
    assert j["prev_month"]["label"] == first_prev.strftime("%B %Y")
    # The three periods are contiguous, oldest to newest, with no gap or overlap.
    assert j["last_month"]["start"] == first_last.isoformat()
    assert prev_month_end.isoformat() < first_last.isoformat()
    assert j["this_month"]["start"] == first_this.isoformat()
    assert last_month_end.isoformat() < first_this.isoformat()


def test_hvf_links_return_latest_requested_visuals(monkeypatch):
    import db_pool

    class FakeDb:
        def run(self, query, **params):
            if "x_publications" in query:
                return []
            return [
                ("Investing Visual", "https://x.com/InvestingVisual/status/222", "2026-08-06"),
                ("Investing Visual", "https://x.com/InvestingVisual/status/111", "2026-08-01"),
                ("Rated Markets", "https://x.com/ratedmarkets/status/333", "2026-08-05"),
            ]

        def close(self):
            pass

    monkeypatch.setattr(db_pool, "get_db", lambda: FakeDb())
    response = server.app.test_client().get("/api/links/NVDA")

    assert response.status_code == 200
    visuals = {row["key"]: row for row in response.get_json()["visuals"]}
    assert visuals["investingvisual"]["url"].endswith("/222")
    assert visuals["ratedmarkets"]["url"].endswith("/333")


def _best_history_snapshot():
    option = {
        "label": "Balanced",
        "settings": {"scope": "All markets", "min_rr": 3, "min_quality": 50,
                     "min_volume_score": 4, "min_rvol": 1.5,
                     "require_above_vwap": True, "require_atr_expanding": False,
                     "max_position_pct": 2, "max_open": 25},
        "results": {"annual_return": 0.25, "max_drawdown": 0.08,
                    "funded_trades": 40, "eligible_trades": 45,
                    "positive_quarters": 4, "quarters": 4},
    }
    return {"dataset_generated": "2026-08-06 11:30 UTC", "data_through": "2026-08-05",
            "model": {"wallet": 10000, "minimum_trade": 25,
                      "position_pct": 2, "max_open": 50}, "options": [option]}


# Needs live runtime state that a clean CI checkout does not have (user 2026-08-15).
@pytest.mark.live_state
def test_best_settings_history_requires_login():
    response = server.app.test_client().get("/api/best-settings-history")

    assert response.status_code == 401


def test_best_settings_history_records_normalised_daily_snapshot(monkeypatch):
    import web_store

    captured = []
    _identity(monkeypatch, "Silver", admin=False)
    monkeypatch.setattr(web_store, "record_best_settings_history",
                        lambda user, snapshot: captured.append((user, snapshot)) or "inserted")
    monkeypatch.setattr(web_store, "list_best_settings_history",
                        lambda user, limit: [{"snapshot_day": "2026-08-06", **captured[0][1]}])
    monkeypatch.setattr(server._wu, "log_event", lambda *args, **kwargs: None)

    response = server.app.test_client().post(
        "/api/best-settings-history", headers={"X-Auth": "token"}, json=_best_history_snapshot())

    assert response.status_code == 200
    assert response.get_json()["result"] == "inserted"
    assert captured[0][0] == "Silver"
    assert captured[0][1]["options"][0]["settings"]["max_open"] == 25


def test_best_settings_history_rejects_non_finite_results():
    snapshot = _best_history_snapshot()
    snapshot["options"][0]["results"]["annual_return"] = float("nan")

    try:
        server._normalise_best_history_snapshot(snapshot)
    except ValueError as exc:
        assert "numeric" in str(exc)
    else:
        raise AssertionError("non-finite annual return was accepted")


def test_ig_close_requires_explicit_confirmation(monkeypatch):
    _identity(monkeypatch, "Alex", admin=True)
    response = server.app.test_client().post(
        "/api/ig-close-positions", headers={"X-Auth": "token"}, json={"deal_ids": ["D1"]})
    assert response.status_code == 400
    assert "confirmation" in response.get_json()["error"]


def test_ig_close_rereads_the_acting_users_open_positions(monkeypatch):
    import ig_shim
    _identity(monkeypatch, "Alex", admin=True)
    calls = []
    monkeypatch.setattr(ig_shim, "session_for", lambda name: object() if name == "Alex" else None)
    monkeypatch.setattr(ig_shim, "acting_session", lambda name: nullcontext())
    monkeypatch.setattr(ig_shim, "get_open_positions", lambda: [{"position": {"dealId": "D1"}}])
    monkeypatch.setattr(ig_shim, "close_trade", lambda deal_id, reason: calls.append((deal_id, reason)) or True)
    monkeypatch.setattr(ig_shim, "last_close_outcome", lambda: {"closed": True, "reason": ""})
    monkeypatch.setattr(server._wu, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_append_batch", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_append_ig_close_audit", lambda *args, **kwargs: None)

    response = server.app.test_client().post(
        "/api/ig-close-positions", headers={"X-Auth": "token"},
        json={"deal_ids": ["D1", "OTHER"], "confirmed": True})

    assert response.status_code == 200
    assert calls == [("D1", "WEB_USER_CONFIRMED")]
    assert response.get_json()["results"] == [
        {"deal_id": "D1", "closed": True, "error": ""},
        {"deal_id": "OTHER", "closed": False, "error": "not currently open"},
    ]


def test_ig_close_returns_and_persists_the_broker_rejection_reason(monkeypatch):
    import ig_shim
    _identity(monkeypatch, "Alex", admin=True)
    events = []
    monkeypatch.setattr(ig_shim, "session_for", lambda name: object())
    monkeypatch.setattr(ig_shim, "acting_session", lambda name: nullcontext())
    monkeypatch.setattr(ig_shim, "get_open_positions", lambda: [{"position": {"dealId": "D1"}}])
    monkeypatch.setattr(ig_shim, "close_trade", lambda deal_id, reason: False)
    monkeypatch.setattr(ig_shim, "last_close_outcome", lambda: {"closed": False, "reason": "MARKET_CLOSED"})
    monkeypatch.setattr(server._wu, "log_event", lambda *args: events.append(args))
    monkeypatch.setattr(server, "_append_batch", lambda *args, **kwargs: None)
    audit = []
    monkeypatch.setattr(server, "_append_ig_close_audit", lambda *args, **kwargs: audit.append(args))

    response = server.app.test_client().post(
        "/api/ig-close-positions", headers={"X-Auth": "token"}, json={"deal_ids": ["D1"], "confirmed": True})

    assert response.status_code == 200
    assert response.get_json()["results"] == [{"deal_id": "D1", "closed": False, "error": "MARKET_CLOSED"}]
    assert response.get_json()["close_handler_version"] == "2026-08-21-audit-v2"
    assert any("MARKET_CLOSED" in event[-1] for event in events)
    assert [entry[2] for entry in audit] == ["submitted", "not_closed"]


def test_ig_close_never_hides_a_missing_broker_outcome(monkeypatch):
    import ig_shim
    _identity(monkeypatch, "Alex", admin=True)
    monkeypatch.setattr(ig_shim, "session_for", lambda name: object())
    monkeypatch.setattr(ig_shim, "acting_session", lambda name: nullcontext())
    monkeypatch.setattr(ig_shim, "get_open_positions", lambda: [{"position": {"dealId": "D1"}}])
    monkeypatch.setattr(ig_shim, "close_trade", lambda deal_id, reason: False)
    monkeypatch.setattr(ig_shim, "last_close_outcome", lambda: {})
    monkeypatch.setattr(server._wu, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_append_batch", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_append_ig_close_audit", lambda *args, **kwargs: None)

    response = server.app.test_client().post(
        "/api/ig-close-positions", headers={"X-Auth": "token"}, json={"deal_ids": ["D1"], "confirmed": True})

    assert response.status_code == 200
    assert response.get_json()["results"][0]["error"] == (
        "Internal close path returned no broker outcome; no IG confirmation was accepted.")


# ── Documentation guides access (user 2026-08-08, P-13) ──────────────────────────────────────────────
_GUIDES_SAMPLE = [
    {"slug": "user-guide-x", "title": "UG", "category": "User Guide", "subtitle": "", "access": "login",
     "file": "user-guide-x.html"},
    {"slug": "support-y", "title": "SUP", "category": "Support", "subtitle": "", "access": "staff",
     "file": "support-y.html"},
]


# Needs live runtime state that a clean CI checkout does not have (user 2026-08-15).
@pytest.mark.live_state
def test_guides_list_requires_login():
    resp = server.app.test_client().get("/api/guides")   # no X-Auth
    assert resp.status_code == 401


def test_guides_list_filters_staff_guides_for_plain_user(monkeypatch):
    """A logged-in non-staff user sees User Guides only; staff guides are withheld."""
    _identity(monkeypatch, "Guest", admin=False)
    monkeypatch.setattr(server._wu, "is_support", lambda n: False)
    monkeypatch.setattr(server, "_load_guides_manifest", lambda: list(_GUIDES_SAMPLE))
    slugs = [g["slug"] for g in server.app.test_client()
             .get("/api/guides", headers={"X-Auth": "token"}).get_json()["guides"]]
    assert slugs == ["user-guide-x"]   # staff-only "support-y" excluded


def test_guides_list_includes_staff_guides_for_admin(monkeypatch):
    _identity(monkeypatch, "Admin", admin=True)
    monkeypatch.setattr(server._wu, "is_support", lambda n: False)
    monkeypatch.setattr(server, "_load_guides_manifest", lambda: list(_GUIDES_SAMPLE))
    slugs = [g["slug"] for g in server.app.test_client()
             .get("/api/guides", headers={"X-Auth": "token"}).get_json()["guides"]]
    assert set(slugs) == {"user-guide-x", "support-y"}


def test_guide_file_denied_for_plain_user(monkeypatch):
    """The file route enforces the same role rule — a staff guide is 404 for a non-staff user."""
    _identity(monkeypatch, "Guest", admin=False)
    monkeypatch.setattr(server._wu, "is_support", lambda n: False)
    monkeypatch.setattr(server, "_load_guides_manifest", lambda: list(_GUIDES_SAMPLE))
    resp = server.app.test_client().get("/api/guides/support-y", headers={"X-Auth": "token"})
    assert resp.status_code == 404


# ======================================================================================================
# A guest account may not enter or save IG credentials (user 2026-08-30).
#
# Guests are read-only and cannot place orders, so storing broker credentials for one can only mislead.
# The gate is SERVER-side: /api/credentials takes a plain POST, so hiding the inputs would stop the page
# and nothing else.
# ======================================================================================================

def _guest(monkeypatch, name="Guest", *, sub="guest", admin=False):
    _identity(monkeypatch, name, admin=admin)
    monkeypatch.setattr(server._wu, "get_subscription", lambda candidate: sub)
    monkeypatch.setattr(server._wu, "log_event", lambda *a, **k: None)


def test_a_guest_cannot_save_ig_credentials(monkeypatch):
    writes = []
    _guest(monkeypatch)
    monkeypatch.setattr(server._wu, "set_secret", lambda *a, **k: writes.append(a))

    response = server.app.test_client().post(
        "/api/credentials", headers={"X-Auth": "token"},
        json={"section": "IG", "values": {"ig_api_key": "SECRET-KEY"}})

    assert response.status_code == 403
    assert writes == [], "a rejected save must not store the credential"
    assert "guest account" in response.get_json()["error"].lower(), "the reason must name the cause"


def test_the_refusal_never_echoes_the_submitted_secret(monkeypatch):
    _guest(monkeypatch)
    monkeypatch.setattr(server._wu, "set_secret", lambda *a, **k: None)

    response = server.app.test_client().post(
        "/api/credentials", headers={"X-Auth": "token"},
        json={"section": "IG", "values": {"ig_password": "hunter2-do-not-echo"}})

    assert "hunter2-do-not-echo" not in response.get_data(as_text=True)


def test_a_paying_subscriber_can_still_save_ig_credentials(monkeypatch):
    """The gate must not catch the people it is not aimed at."""
    writes = []
    _guest(monkeypatch, "Silver", sub="silver")
    monkeypatch.setattr(server._wu, "set_secret", lambda *a, **k: writes.append(a))
    monkeypatch.setattr(server, "_record_ig_account_identity", lambda *a, **k: None, raising=False)

    response = server.app.test_client().post(
        "/api/credentials", headers={"X-Auth": "token"},
        json={"section": "IG", "values": {"ig_api_key": "KEY"}})

    assert response.status_code == 200
    assert writes, "a silver subscriber's own IG credentials must still save"


def test_an_admin_on_a_guest_subscription_is_not_locked_out(monkeypatch):
    """Gated on the same axis as pre-orders (guest AND not admin), so administering the system never
    depends on the subscription field."""
    writes = []
    _guest(monkeypatch, "Admin", sub="guest", admin=True)
    monkeypatch.setattr(server._wu, "set_secret", lambda *a, **k: writes.append(a))
    monkeypatch.setattr(server, "_record_ig_account_identity", lambda *a, **k: None, raising=False)

    response = server.app.test_client().post(
        "/api/credentials", headers={"X-Auth": "token"},
        json={"section": "IG", "values": {"ig_api_key": "KEY"}})

    assert response.status_code == 200
    assert writes


def test_the_guest_ig_section_is_returned_locked_with_a_reason(monkeypatch):
    """So the page can say why, instead of showing inputs that fail on submit."""
    _guest(monkeypatch)
    monkeypatch.setattr(server._wu, "get_secret", lambda *a, **k: "")
    monkeypatch.setattr(server._wu, "get_app_secret", lambda *a, **k: "")

    body = server.app.test_client().get(
        "/api/credentials", headers={"X-Auth": "token"}).get_json()
    ig = next(s for s in body["sections"] if s["scope"] == "ig")

    assert ig["editable"] is False
    assert "guest account" in ig["locked_reason"].lower()


# ======================================================================================================
# Per-trade winners rows are for signed-in users only (user 2026-09-01: "transaction evidence must NOT be
# shared if a user is NOT LOGGED IN ... this is taboo").
#
# /api/winners, -sl and -run each served ~4,948 rows to anyone, with no auth check. The browser gate was
# not a boundary: LIMITED defaulted to false, and the endpoints could be called directly regardless.
# ======================================================================================================

@pytest.mark.parametrize("route", ["/api/winners", "/api/winners-sl", "/api/winners-run"])
def test_anonymous_callers_get_no_per_trade_rows(monkeypatch, route):
    monkeypatch.setattr(server._wu, "name_for_token", lambda token: "")

    body = server.app.test_client().get(route).get_json()

    assert body["rows"] == [], f"{route} still serves per-trade rows to an anonymous caller"
    assert body.get("limited") is True, "the client needs to know why the rows are absent"


@pytest.mark.parametrize("route", ["/api/winners", "/api/winners-sl", "/api/winners-run"])
def test_the_page_still_loads_for_an_anonymous_caller(monkeypatch, route):
    """200 with an empty set, not 401: Performance is a public tab by design, so the page must render."""
    monkeypatch.setattr(server._wu, "name_for_token", lambda token: "")

    assert server.app.test_client().get(route).status_code == 200


def test_a_signed_in_caller_still_gets_rows(monkeypatch):
    """The gate must not catch the people it is not aimed at."""
    monkeypatch.setattr(server._wu, "name_for_token", lambda token: "Someone" if token == "tok" else "")
    monkeypatch.setattr(server, "_winners_stored", lambda years: {"rows": [{"ticker": "X"}], "months": 12})

    body = server.app.test_client().get("/api/winners", headers={"X-Auth": "tok"}).get_json()

    assert body["rows"], "a signed-in user must still receive the replay rows"


# ======================================================================================================
# The PUBLIC Best Settings cards (user 2026-09-03: "logged out users should see cards BUT NOT THE
# UNDERLYING EVIDENCE TABLE").
#
# The cards are aggregates of the very rows _winners_rows_allowed withholds, so the danger is not that
# the endpoint is open -- it is meant to be -- but that a summary quietly carries a row with it. The
# endpoint rebuilds its response from an allow-list rather than forwarding what it read, and these tests
# feed it a deliberately contaminated store to prove the allow-list is what decides.
# ======================================================================================================

_CARD = {
    "label": "Balanced", "why": "why", "colour": "var(--bull)", "ret": 1.23, "dd": 0.05,
    "n": 140, "eligible": 260, "posPeriods": 4, "periods": 4,
    "wlEligible": {"w": 90, "l": 50, "pct": 64}, "wlActual": {"w": 88, "l": 52, "pct": 63},
    "scope": {"kind": "all", "label": "All markets", "display": "All markets", "offList": []},
    "rr": 3, "q": 50, "vs": 4, "rv": 0, "vw": True, "atr": False, "st": 5, "mo": 20,
    "years": [{"from": "2025-08-27", "to": "2026-08-27", "ret": 1.23, "n": 140}],
}


def _store_cards(monkeypatch, payload, *, dataset="GEN-1", snapshot_gen="GEN-1"):
    import web_store
    monkeypatch.setattr(server, "_load_snapshot", lambda: {"generated_utc": snapshot_gen})
    monkeypatch.setattr(web_store, "load_json_store",
                        lambda key: {"payload": payload, "dataset": dataset} if key == "best_settings_cards" else None)


def test_the_public_cards_need_no_token(monkeypatch):
    """They are the whole point of the endpoint: a logged-out visitor must see the cards."""
    _store_cards(monkeypatch, {"cards": [dict(_CARD)], "model": {"wallet": 10000, "position_pct": 5},
                               "data_through": "2026-08-27", "recommended3y": False})

    response = server.app.test_client().get("/api/best-settings-cards")

    assert response.status_code == 200
    body = response.get_json()
    assert [c["label"] for c in body["cards"]] == ["Balanced"]
    assert body["cards"][0]["ret"] == 1.23 and body["cards"][0]["n"] == 140
    assert body["data_through"] == "2026-08-27"


def test_the_public_cards_never_carry_a_per_trade_row(monkeypatch):
    """The store is contaminated on purpose: an allow-list, not a hope, is what keeps rows out."""
    contaminated = dict(_CARD, seq=[{"ticker": "VOD.L", "trig_date": "2026-05-01", "perf": 4.2}],
                        proof=[{"r": {"ticker": "VOD.L"}, "placed": True}])
    _store_cards(monkeypatch, {"cards": [contaminated],
                               "rows": [{"ticker": "BP.L", "trig_date": "2026-04-01", "perf": -2.0}],
                               "model": {}, "recommended3y": False})

    body = server.app.test_client().get("/api/best-settings-cards").get_json()
    text = json.dumps(body)

    assert "VOD.L" not in text and "BP.L" not in text, f"a per-trade row reached an anonymous visitor: {text}"
    assert "seq" not in body["cards"][0] and "proof" not in body["cards"][0]
    assert "rows" not in body and "trig_date" not in text
    assert body["cards"][0]["ret"] == 1.23, "...while the aggregate itself still arrives"


def test_cards_built_from_a_different_scan_are_not_served(monkeypatch):
    """Stale cards on a public page are worse than none: the page says it is recalculating instead."""
    _store_cards(monkeypatch, {"cards": [dict(_CARD)]}, dataset="GEN-OLD", snapshot_gen="GEN-NEW")

    body = server.app.test_client().get("/api/best-settings-cards").get_json()

    assert body["cards"] == [] and body["pending"] is True


def test_the_winners_rows_are_still_withheld_from_the_same_visitor(monkeypatch):
    """The cards being public must not have loosened the gate they exist to work around."""
    monkeypatch.setattr(server._wu, "name_for_token", lambda token: "")

    assert server.app.test_client().get("/api/winners").get_json()["rows"] == []


# ======================================================================================================
# MCAP / RVOL / VWAP / ATR on the IG Account working-orders table (user 2026-09-03, asked four times in
# one message: "i still can't see the columns I requested on the IG orders table to help me check it
# meets the criteria of the settings").
#
# There are three order tables. Two of them got these columns in August; this one -- the only one showing
# the orders IG itself is holding, and therefore the one you reach for to check LIVE orders against the
# saved filters -- never had a single one of them. The metrics come from _attach_setup_metrics, the same
# resolver /api/order-ops uses, so the two order screens cannot disagree about one instrument's setup.
# ======================================================================================================

class _FakeIG:
    """Just enough of ig_shim for /api/ig-account to run without an IG session."""
    class _Lock:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    _IG_LOCK = _Lock()

    @staticmethod
    def session_for(name): return object()

    @staticmethod
    def acting_session(name): return _FakeIG._Lock()

    @staticmethod
    def get_open_positions(): return []

    @staticmethod
    def get_working_orders():
        return [{"workingOrderData": {"epic": "EPIC1", "direction": "BUY", "orderSize": 3,
                                      "orderLevel": 101.5, "orderType": "LIMIT",
                                      "createdDateUTC": "2026-08-20T09:00:00",
                                      "goodTillDate": "2026-09-30T00:00:00"},
                 "marketData": {"epic": "EPIC1", "instrumentName": "Vodafone"}}]

    @staticmethod
    def get_account_info(): return {}


def _ig_account(monkeypatch, *, attach=None, placed="2026-08-18"):
    import sys
    _identity(monkeypatch, "Alex", admin=True)
    monkeypatch.setitem(sys.modules, "ig_shim", _FakeIG)

    class _DB:
        def run(self, sql, **kw):
            if "epic_lookup" in sql:
                return [("VOD.L", "EPIC1")]
            if "working_orders" in sql:
                return [("EPIC1", "WEB_BRIDGE", placed)]
            return []

        def close(self): pass

    import db_pool
    monkeypatch.setattr(db_pool, "get_db", lambda: _DB())
    monkeypatch.setattr(server, "_load_snapshot",
                        lambda: {"generated_utc": "g", "records": [{"ticker": "VOD.L", "name": "Vodafone",
                                                                    "market": "FTSE 100"}]})
    monkeypatch.setattr(server, "_mcap_map", lambda: {"VOD.L": 2.4e10})
    monkeypatch.setattr(server._wu, "record_ig_account_audit", lambda *a, **k: None)
    monkeypatch.setattr(server, "_attach_setup_metrics", attach or (lambda rows: None))
    return server.app.test_client().get("/api/ig-account", headers={"X-Auth": "token"}).get_json()


def test_the_ig_working_orders_carry_the_setup_metrics(monkeypatch):
    """THE REQUEST. Every metric the saved filters are expressed in has to be on the row."""
    def attach(rows):
        for r in rows:
            r.update({"quality": 71, "rr": 4.2, "rvol": 1.9,
                      "volume_score": 8, "above_vwap": True, "atr_expanding": False})

    body = _ig_account(monkeypatch, attach=attach)
    order = body["orders"][0]

    for field in ("mcap", "rvol", "above_vwap", "atr_expanding", "volume_score", "rr", "quality"):
        assert field in order, f"{field} is missing from the IG working-order row"
    assert order["mcap"] == 2.4e10
    assert (order["rvol"], order["above_vwap"], order["atr_expanding"]) == (1.9, True, False)


def test_the_metrics_come_from_the_shared_resolver(monkeypatch):
    """Not a second copy: two order screens quoting different numbers for one setup is the failure mode."""
    seen = []
    _ig_account(monkeypatch, attach=lambda rows: seen.append([r.get("ticker") for r in rows]))

    assert seen == [["VOD.L"]], (
        "/api/ig-account must hand its orders to the same _attach_setup_metrics /api/order-ops uses")


def test_the_trigger_is_matched_against_our_own_placement_date(monkeypatch):
    """_attach_setup_metrics picks the latest trigger at or before placed_at.

    IG's createdDate moves when an order is amended, which would judge it against a later trigger than the
    one that caused it. working_orders.placed_at is written at placement and does not move.
    """
    seen = {}
    _ig_account(monkeypatch, attach=lambda rows: seen.update(rows[0]), placed="2026-08-18")

    assert seen["placed_at"] == "2026-08-18", (
        f"expected our own placement date, got {seen.get('placed_at')!r} "
        "(IG's createdDateUTC is 2026-08-20 and would match the wrong trigger)")


def test_a_missing_metric_leaves_the_row_rather_than_failing_the_table(monkeypatch):
    """The orders themselves are correct without the metrics; blank cells are honest, an error is not."""
    def boom(rows):
        raise RuntimeError("squeeze_history unavailable")

    body = _ig_account(monkeypatch, attach=boom)

    assert len(body["orders"]) == 1 and body["orders"][0]["ticker"] == "VOD.L"
    assert not body.get("error")
