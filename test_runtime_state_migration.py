"""Offline regressions for IONOS packaging and Supabase-backed runtime state."""

import copy
import json
from pathlib import Path

import pytest

import build_ionos_package
import migrate_runtime_state_to_supabase as migration
import sector_cache
import web_store
from hvf_web import build_snapshot, server, web_users


class _JsonStoreDb:
    def __init__(self):
        self.payloads = {}
        self.revisions = {}
        self.closed = False

    def run(self, sql, **params):
        compact = " ".join(sql.lower().split())
        if compact.startswith("select payload, revision from web_json_store"):
            value = self.payloads.get(params["k"])
            return [] if value is None else [[value, self.revisions[params["k"]]]]
        if compact.startswith("insert into web_json_store"):
            self.payloads[params["k"]] = params["p"]
            self.revisions[params["k"]] = self.revisions.get(params["k"], 0) + 1
            return [[self.revisions[params["k"]]]]
        raise AssertionError(f"unexpected SQL: {compact}")

    def close(self):
        self.closed = True


def _reset_user_cache():
    web_users._STATE_CACHE.update(at=0.0, source=None, users=None, remote_available=None, revision=None)


def test_web_json_store_round_trips_complete_documents(monkeypatch):
    db = _JsonStoreDb()
    monkeypatch.setattr(web_store, "_db", lambda: db)
    payload = {"Alex": {"settings": {"min_rr": 3}}, "__requests__": []}

    assert web_store.read_json_store("missing") == (True, None)
    assert web_store.save_json_store("web_users", payload) is True
    available, restored = web_store.read_json_store("web_users")

    assert available is True
    assert restored == payload
    assert restored is not payload


def test_web_users_prefers_supabase_and_dual_writes_local_copy(monkeypatch, tmp_path):
    local = tmp_path / "web_users.json"
    local.write_text(json.dumps({"Local": {"enabled": True}}), encoding="utf-8")
    remote = {"Remote": {"enabled": True}}
    writes = []
    monkeypatch.setattr(web_users, "_USERS_FILE", str(local))
    monkeypatch.setattr(web_users, "_remote_state_enabled", lambda: True)
    monkeypatch.setattr(web_store, "read_json_store_versioned", lambda key: (True, copy.deepcopy(remote), 4))
    monkeypatch.setattr(
        web_store, "save_json_store_versioned",
        lambda key, value, expected_revision: writes.append((key, copy.deepcopy(value), expected_revision)) or 5,
    )
    _reset_user_cache()

    users = web_users._load()
    users["New"] = {"enabled": True}
    web_users._save(users)

    assert "Remote" in users and "Local" not in users
    assert writes == [("web_users", users, 4)]
    assert json.loads(local.read_text(encoding="utf-8")) == users


def test_web_users_refuses_stale_overwrite_after_database_outage(monkeypatch, tmp_path):
    local = tmp_path / "web_users.json"
    original = {"Local": {"enabled": True}}
    local.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(web_users, "_USERS_FILE", str(local))
    monkeypatch.setattr(web_users, "_remote_state_enabled", lambda: True)
    monkeypatch.setattr(web_store, "read_json_store_versioned", lambda key: (False, None, None))
    monkeypatch.setattr(web_store, "save_json_store_versioned", lambda *args, **kwargs: pytest.fail("stale state reached Supabase"))
    _reset_user_cache()

    users = web_users._load()
    users["Unsafe"] = {"enabled": True}
    with pytest.raises(OSError, match="stale overwrite"):
        web_users._save(users)

    assert json.loads(local.read_text(encoding="utf-8")) == original


def test_web_users_rejects_concurrent_remote_change_before_local_copy(monkeypatch, tmp_path):
    local = tmp_path / "web_users.json"
    local_original = {"Local": {"enabled": True}}
    remote = {"Remote": {"enabled": True}}
    local.write_text(json.dumps(local_original), encoding="utf-8")
    monkeypatch.setattr(web_users, "_USERS_FILE", str(local))
    monkeypatch.setattr(web_users, "_remote_state_enabled", lambda: True)
    monkeypatch.setattr(web_store, "read_json_store_versioned", lambda key: (True, copy.deepcopy(remote), 8))
    monkeypatch.setattr(web_store, "save_json_store_versioned", lambda *args, **kwargs: None)
    _reset_user_cache()

    users = web_users._load()
    users["Stale"] = {"enabled": True}
    with pytest.raises(OSError, match="changed concurrently"):
        web_users._save(users)

    assert json.loads(local.read_text(encoding="utf-8")) == local_original


def test_reference_caches_prefer_supabase(monkeypatch):
    monkeypatch.setattr(web_store, "load_json_store", lambda key: {
        "sector_cache": {"ABC": "Industrials"},
        "name_cache": {"ABC": "ABC Limited"},
    }.get(key))
    monkeypatch.setattr(sector_cache, "_cache", None)

    assert sector_cache.get_sector("ABC") == "Industrials"
    assert build_snapshot._load_name_cache() == {"ABC": "ABC Limited"}


def test_version_history_uses_supabase_when_git_is_unavailable(monkeypatch):
    monkeypatch.setattr("subprocess.check_output", lambda *a, **k: (_ for _ in ()).throw(OSError("no git")))
    monkeypatch.setattr(web_store, "load_json_store", lambda key: {
        "entries": [{"date": "2026-08-12", "version": "abc1234", "summary": "fix hosted fallback"}]
    })

    assert server._version_entries() == [{
        "date": "2026-08-12", "version": "abc1234", "summary": "fix hosted fallback", "category": "Bug fix"
    }]


def test_migration_preserves_differing_remote_document(monkeypatch):
    monkeypatch.setattr(migration, "sources", lambda: {"web_users": lambda: {"local": 1}})
    monkeypatch.setattr(web_store, "load_json_store", lambda key: {"remote": 1})
    monkeypatch.setattr(web_store, "save_json_store", lambda *args: pytest.fail("conflict was overwritten"))

    results, conflicts = migration.migrate(apply=True, overwrite=False)

    assert conflicts == 1
    assert results == [("web_users", "conflict", 1, "remote differs; preserved")]


def test_ionos_package_manifest_excludes_private_and_development_files(tmp_path):
    included = {p.relative_to(build_ionos_package.ROOT).as_posix() for p in build_ionos_package.package_files()}

    assert "wsgi.py" in included
    assert "IONOS_DEPLOYMENT.md" in included
    assert "hvf_web/index.html" in included
    assert "hvf_web/snapshot.json" in included
    assert "docs/guides/_manifest.json" in included
    assert "data/web_users.json" not in included
    assert "data/.web_users.key" not in included
    assert "hvf_web/name_cache.json" not in included
    assert "sector_cache.json" not in included
    assert "test_performance.py" not in included
    assert ".github.zip" not in included
    assert not any("price_cache" in path for path in included)

    output, files = build_ionos_package.build(tmp_path / "ionos.zip")
    assert output.is_file() and files


def test_scanner_actionable_not_history_note_is_prominent_and_documented():
    html = (build_ionos_package.ROOT / "hvf_web" / "index.html").read_text(encoding="utf-8")
    note = "This is a live list of what's actionable <b>right now</b>, not a history"
    assert note in html
    note_tag = html[html.rfind("<p", 0, html.index(note)):html.index("</p>", html.index(note))]
    assert "color:#d29922" in note_tag
    assert 'class="muted"' not in note_tag
    assert "Squeeze History" in note_tag and "Performance" in note_tag

    guide_source = (build_ionos_package.ROOT / "docs" / "_guides_content.js").read_text(encoding="utf-8")
    guide_html = (build_ionos_package.ROOT / "docs" / "guides" / "user-guide-getting-around.html").read_text(encoding="utf-8")
    assert "not a history" in guide_source and "actionable" in guide_source
    assert "not a history" in guide_html and "actionable" in guide_html
