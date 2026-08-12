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


def test_daily_report_reuses_scan_for_snapshot_publication():
    text = (store.ROOT / "run_hvf_report.py").read_text(encoding="utf-8")
    assert "build(scan_results=all_results)" in text
    assert "_publish_scanner_snapshot(all_results)" in text
