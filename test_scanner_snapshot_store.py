"""Offline safety regressions for external Scanner snapshot publication."""

import hashlib
import json

import pytest

import scanner_snapshot_store as store
from hvf_web import server


def _snapshot():
    return {
        "generated_utc": "2026-08-12T10:14:25.305609+00:00",
        "count": 1,
        "records": [{"ticker": "RR.L", "has_signal": True}],
    }


class _Response:
    def __init__(self, status_code=200, content=b"", body=None, text=""):
        self.status_code = status_code
        self.content = content
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


def test_snapshot_validation_rejects_incomplete_or_mismatched_candidates():
    with pytest.raises(store.SnapshotStoreError, match="generated_utc"):
        store.validate_snapshot({"count": 0, "records": []})
    with pytest.raises(store.SnapshotStoreError, match="count"):
        store.validate_snapshot({**_snapshot(), "count": 2})
    with pytest.raises(store.SnapshotStoreError, match="ticker"):
        store.validate_snapshot({**_snapshot(), "records": [{}]})


def test_publish_uploads_immutable_object_before_advancing_pointer(monkeypatch):
    calls = []
    monkeypatch.setattr(store, "ensure_private_bucket", lambda: "scanner-artifacts")
    monkeypatch.setattr(store, "_project_url", lambda: "https://example.supabase.co")
    monkeypatch.setattr(store, "_headers", lambda *a, **k: {"apikey": "redacted"})
    monkeypatch.setattr(
        store, "_request",
        lambda method, url, **kwargs: calls.append((method, url, kwargs["data"])) or _Response(201),
    )
    monkeypatch.setattr(
        store, "_record_publication",
        lambda meta: calls.append(("pointer", meta["object_path"], meta["sha256"])) or 7,
    )

    meta = store.publish_snapshot(_snapshot(), source="test")

    assert calls[0][0] == "POST"
    assert calls[0][1].startswith("https://example.supabase.co/storage/v1/object/scanner-artifacts/snapshots/")
    assert calls[1][0] == "pointer"
    assert calls[0][1].endswith(meta["object_path"])
    assert hashlib.sha256(calls[0][2]).hexdigest() == meta["sha256"]
    assert meta["version_id"] == 7 and meta["record_count"] == 1


def test_missing_bucket_uses_supabase_inner_404_and_is_created_private(monkeypatch):
    calls = []
    missing = _Response(
        400,
        content=b'{"statusCode":"404","error":"Bucket not found","message":"Bucket not found"}',
        body={"statusCode": "404", "error": "Bucket not found", "message": "Bucket not found"},
    )
    monkeypatch.setattr(store, "_bucket", lambda: "scanner-artifacts")
    monkeypatch.setattr(store, "_project_url", lambda: "https://example.supabase.co")
    monkeypatch.setattr(store, "_headers", lambda *a, **k: {"apikey": "redacted"})

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return missing if method == "GET" else _Response(200)

    monkeypatch.setattr(store, "_request", request)

    assert store.ensure_private_bucket() == "scanner-artifacts"
    assert [call[0] for call in calls] == ["GET", "POST"]
    assert calls[1][2]["json"]["public"] is False


def test_unrelated_bucket_http_400_is_not_treated_as_missing(monkeypatch):
    response = _Response(400, content=b'{"statusCode":"400","message":"Invalid request"}',
                         body={"statusCode": "400", "message": "Invalid request"})
    monkeypatch.setattr(store, "_bucket", lambda: "scanner-artifacts")
    monkeypatch.setattr(store, "_project_url", lambda: "https://example.supabase.co")
    monkeypatch.setattr(store, "_headers", lambda *a, **k: {"apikey": "redacted"})
    monkeypatch.setattr(store, "_request", lambda *a, **k: response)

    with pytest.raises(store.SnapshotStoreError, match=r"inspect Storage bucket \(400\)"):
        store.ensure_private_bucket()


def test_download_rejects_object_before_json_parse_when_checksum_differs(monkeypatch):
    data = json.dumps(_snapshot(), separators=(",", ":")).encode()
    meta = {
        "object_path": "snapshots/example.json", "sha256": "0" * 64,
        "record_count": 1, "generated_utc": _snapshot()["generated_utc"],
    }
    monkeypatch.setattr(store, "_bucket", lambda: "scanner-artifacts")
    monkeypatch.setattr(store, "_project_url", lambda: "https://example.supabase.co")
    monkeypatch.setattr(store, "_headers", lambda *a, **k: {"apikey": "redacted"})
    monkeypatch.setattr(store, "_request", lambda *a, **k: _Response(200, content=data))

    with pytest.raises(store.SnapshotStoreError, match="checksum"):
        store.download_snapshot(meta)


def test_publication_verification_reuses_publish_key_for_readback(monkeypatch):
    purposes = []
    monkeypatch.setattr(
        store,
        "download_snapshot",
        lambda meta=None, purpose="read": purposes.append(purpose) or (_snapshot(), {"version_id": 7}, b"{}"),
    )

    assert store.verify_current() == {"version_id": 7}
    assert purposes == ["publish"]


def test_remote_outage_keeps_last_known_good_local_snapshot(monkeypatch, tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(_snapshot()), encoding="utf-8")
    monkeypatch.setattr(store, "storage_configured", lambda purpose="read": True)
    monkeypatch.setattr(store, "pull_current", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))

    assert store.load_snapshot(path) == _snapshot()


def test_secret_key_never_uses_new_secret_as_bearer(monkeypatch):
    monkeypatch.setenv("SUPABASE_SCANNER_WEB_KEY", "sb_secret_example")
    assert store._headers("read") == {"apikey": "sb_secret_example"}


def test_new_scanner_workflow_is_external_and_has_no_github_schedule():
    text = (store.ROOT / ".github" / "workflows" / "trading-scanner-snapshot.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "publish_scanner_snapshot.py --build" in text
    assert "SUPABASE_SCANNER_PUBLISH_KEY" in text
    assert "group: scanner-snapshot-publish" in text
    assert "cancel-in-progress: false" in text


def test_web_refresh_dispatches_worker_without_running_local_builder(monkeypatch):
    calls = []

    class _DispatchResponse:
        def raise_for_status(self):
            return None

    monkeypatch.setattr("app_secrets.get_secret", lambda name: "token" if name == "GH_PAT" else "")
    monkeypatch.setattr("requests.post", lambda url, **kwargs: calls.append((url, kwargs)) or _DispatchResponse())
    monkeypatch.setattr(server, "_load_snapshot", lambda: {"generated_utc": "2026-08-12T10:14:25+00:00"})
    monkeypatch.setitem(server._REFRESHING, "on", False)
    monkeypatch.setitem(server._REFRESHING, "mode", None)

    assert server._dispatch_snapshot_rebuild(["FTSE 100"]) is True
    assert len(calls) == 1
    assert calls[0][0].endswith("/actions/workflows/trading-scanner-snapshot.yml/dispatches")
    assert calls[0][1]["json"]["inputs"] == {"markets": "FTSE 100"}
    assert server._REFRESHING["mode"] == "external"


def test_web_refresh_uses_expiring_cron_broker_when_github_token_is_absent(monkeypatch):
    calls = []

    class Response:
        def __init__(self, body=None):
            self.body = body or {}

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    monkeypatch.setattr("app_secrets.get_secret", lambda name: "cron-key" if name == "CRONJOB_API_KEY" else "")
    monkeypatch.setattr(server, "_load_snapshot", lambda: {"generated_utc": "2026-08-12T10:14:25+00:00"})
    monkeypatch.setitem(server._REFRESHING, "on", False)

    def get(url, **kwargs):
        calls.append(("GET", url, kwargs))
        if url.endswith("/jobs"):
            return Response({"jobs": [{"title": "Scanner Snapshot Refresh", "jobId": 8257085}]})
        return Response({"jobDetails": {
            "url": "https://api.github.com/repos/Red7Al/ClaudeCode/actions/workflows/trading-scanner-snapshot.yml/dispatches",
            "saveResponses": False, "requestMethod": 1,
            "extendedData": {"headers": {"Authorization": "Bearer opaque", "Content-Type": "application/json"},
                             "body": '{"ref":"main"}'},
        }})

    monkeypatch.setattr("requests.get", get)
    monkeypatch.setattr("requests.put", lambda url, **kwargs: calls.append(("PUT", url, kwargs)) or Response({"jobId": 9}))
    monkeypatch.setattr("requests.post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no direct dispatch")))

    assert server._dispatch_snapshot_rebuild(["FTSE 100"]) is True
    put = next(call for call in calls if call[0] == "PUT")
    job = put[2]["json"]["job"]
    assert job["title"] == "Scanner Snapshot Refresh On Demand"
    assert job["schedule"]["expiresAt"] > 0
    assert json.loads(job["extendedData"]["body"])["inputs"] == {"markets": "FTSE 100"}
    assert server._REFRESHING["worker"] == "cron-job.org → GitHub Actions"


def test_daily_report_reuses_scan_for_snapshot_publication():
    text = (store.ROOT / "run_hvf_report.py").read_text(encoding="utf-8")
    assert "build(scan_results=all_results)" in text
    assert "_publish_scanner_snapshot(all_results)" in text
