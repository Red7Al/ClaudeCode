"""Behaviour tests: run_hvf_report._publish_scanner_snapshot takes the IONOS fallback on ANY Storage refusal.

Incident: Morning Chain run 41164681023 (2026-10-31) failed with
``SnapshotStoreError: snapshot upload failed (400)``; only a "(402)" took the fallback, so the 400 re-raised
and every later step of the chain was skipped. Since 2026-11-02 (owner approved) any SnapshotStoreError writes
hvf_web/.ionos-fallback-required and returns; the workflow must then install AND verify the IONOS copy.

These tests EXECUTE the function. Every path it writes (snapshot.json, its .tmp, the marker) is derived from
os.path.dirname(run_hvf_report.__file__), so redirecting __file__ into tmp_path keeps the real hvf_web/ untouched.
"""
import json
import os

import pytest

import run_hvf_report
import scanner_snapshot_store
from scanner_snapshot_store import SnapshotStoreError

SNAPSHOT = {"records": [{"ticker": "TEST.L"}], "generated_at": "2026-11-02T03:30:00Z"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "hvf_web").mkdir()
    monkeypatch.setattr(run_hvf_report, "__file__", str(tmp_path / "run_hvf_report.py"))
    monkeypatch.setenv("SUPABASE_SCANNER_PUBLISH_KEY", "test-placeholder")
    monkeypatch.setattr("hvf_web.build_snapshot.build", lambda scan_results=None: dict(SNAPSHOT))
    monkeypatch.setattr("squeeze_history.refresh_daily", lambda snapshot: {"stub": True})
    monkeypatch.setattr("instrument_metrics.record_daily", lambda snapshot, **kw: {"stub": True})

    calls = {"publish": 0, "verify": 0}
    state = {"publish": None, "verify": None}

    def fake_publish(snapshot, source="manual"):
        calls["publish"] += 1
        assert snapshot == SNAPSHOT
        r = state["publish"]
        if isinstance(r, BaseException):
            raise r
        return r

    def fake_verify():
        calls["verify"] += 1
        return state["verify"]

    monkeypatch.setattr("scanner_snapshot_store.publish_snapshot", fake_publish)
    monkeypatch.setattr("scanner_snapshot_store.verify_current", fake_verify)
    web = tmp_path / "hvf_web"
    return {"web": web, "marker": web / ".ionos-fallback-required", "snap": web / "snapshot.json",
            "state": state, "calls": calls}


def _assert_snapshot_written(env):
    assert json.loads(env["snap"].read_text(encoding="utf-8")) == SNAPSHOT
    assert [p.name for p in env["web"].iterdir() if ".tmp-" in p.name] == []


@pytest.mark.parametrize("message", ["snapshot upload failed (400)", "snapshot upload failed (402)"])
def test_any_storage_refusal_takes_ionos_fallback(env, message):
    env["state"]["publish"] = SnapshotStoreError(message)
    run_hvf_report._publish_scanner_snapshot({})  # must not raise
    assert env["marker"].read_text(encoding="utf-8") == message
    _assert_snapshot_written(env)
    assert env["calls"] == {"publish": 1, "verify": 0}


def test_refusal_from_verify_also_takes_fallback(env, monkeypatch):
    env["state"]["publish"] = {"sha256": "a", "record_count": 1, "version_id": "v1"}

    def boom():
        raise SnapshotStoreError("snapshot read failed (500)")
    monkeypatch.setattr("scanner_snapshot_store.verify_current", boom)
    run_hvf_report._publish_scanner_snapshot({})
    assert env["marker"].read_text(encoding="utf-8") == "snapshot read failed (500)"


def test_success_with_matching_sha_writes_no_marker(env):
    env["state"]["publish"] = {"sha256": "abc", "record_count": 1, "version_id": "v1"}
    env["state"]["verify"] = {"sha256": "abc"}
    run_hvf_report._publish_scanner_snapshot({})
    assert not env["marker"].exists()
    _assert_snapshot_written(env)
    assert env["calls"] == {"publish": 1, "verify": 1}


def test_verify_mismatch_still_raises(env):
    env["state"]["publish"] = {"sha256": "abc", "record_count": 1, "version_id": "v1"}
    env["state"]["verify"] = {"sha256": "different"}
    with pytest.raises(RuntimeError, match="did not read-verify"):
        run_hvf_report._publish_scanner_snapshot({})
    assert not env["marker"].exists()


def test_non_storage_exception_still_raises(env):
    env["state"]["publish"] = RuntimeError("unexpected bug")
    with pytest.raises(RuntimeError, match="unexpected bug") as ei:
        run_hvf_report._publish_scanner_snapshot({})
    assert not isinstance(ei.value, SnapshotStoreError)
    assert not env["marker"].exists()


def test_no_publisher_key_skips_everything(env, monkeypatch):
    monkeypatch.delenv("SUPABASE_SCANNER_PUBLISH_KEY")
    run_hvf_report._publish_scanner_snapshot({})
    assert not env["snap"].exists() and not env["marker"].exists()
    assert env["calls"] == {"publish": 0, "verify": 0}


def test_real_hvf_web_untouched_by_redirect(env):
    """Apparatus check: the redirect really moves the write target out of the repo."""
    assert os.path.dirname(run_hvf_report.__file__) == str(env["web"].parent)
