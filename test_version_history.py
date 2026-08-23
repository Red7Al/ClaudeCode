"""Version history must show the commits that are actually deployed.

WHY. _version_entries builds the list from `git log`, which ALWAYS fails on IONOS because the deployment
package deliberately excludes .git. It then fell back to the Supabase copy, and only read the packaged
file "if not source" — i.e. only when Supabase was empty. But the Supabase copy is a one-off seed:
migrate_runtime_state_to_supabase.py wrote it once and nothing has ever updated it, so it sat at 958
entries ending 2026-08-18 (verified directly against web_json_store). Once that seed existed the packaged
file could never be reached, and the tab froze on 18/8 — which is exactly what the user reported.

The packaged file is generated from git at package time, so it describes the build being deployed.
"""

import pytest

from hvf_web import server


def _entries(monkeypatch, stored, packaged):
    """Drive _version_entries down its fallback path with both sources controlled."""
    monkeypatch.setattr(server, "_VERSION_FLOOR", "2026-01-01")

    class _FakeStore:
        @staticmethod
        def load_json_store(_key):
            return {"entries": stored}

    import sys
    monkeypatch.setitem(sys.modules, "web_store", _FakeStore)
    monkeypatch.setattr(server, "_read_json_entries", lambda _p: list(packaged))
    # Force the git path to fail, which is the permanent condition on the deployed host.
    import subprocess
    monkeypatch.setattr(subprocess, "check_output",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no .git in the deployed package")))
    return server._version_entries()


STALE = [{"date": "2026-08-18", "version": "aaa1111", "summary": "seeded once, never updated"}]
FRESH = [{"date": "2026-08-18", "version": "aaa1111", "summary": "seeded once, never updated"},
         {"date": "2026-08-23", "version": "bbb2222", "summary": "the build actually deployed"}]


def test_the_packaged_file_wins_when_it_is_fresher(monkeypatch):
    """THE REGRESSION. Before the fix the 18/8 Supabase seed shadowed the packaged file entirely."""
    got = _entries(monkeypatch, stored=STALE, packaged=FRESH)

    assert max(e["date"] for e in got) == "2026-08-23"
    assert any(e["version"] == "bbb2222" for e in got)


def test_supabase_still_wins_when_it_is_the_fresher_one(monkeypatch):
    """The fix must not simply invert the old bug."""
    got = _entries(monkeypatch, stored=FRESH, packaged=STALE)

    assert max(e["date"] for e in got) == "2026-08-23"


def test_either_source_alone_is_used(monkeypatch):
    assert _entries(monkeypatch, stored=[], packaged=FRESH)
    assert _entries(monkeypatch, stored=FRESH, packaged=[])
    assert _entries(monkeypatch, stored=[], packaged=[]) == []


def test_entries_are_categorised_and_floored(monkeypatch):
    got = _entries(monkeypatch, stored=[], packaged=FRESH + [
        {"date": "2025-01-01", "version": "old", "summary": "before the project started"}])

    assert all(e.get("category") for e in got)
    assert all(e["date"] > "2026-01-01" for e in got), "the version floor must still apply"


def test_preferring_supabase_first_reproduces_the_frozen_tab(monkeypatch):
    """A guard that has never failed proves nothing: run the old selection rule."""
    stored, packaged = STALE, FRESH
    source = stored or packaged          # the old "if not source" shape
    assert max(e["date"] for e in source) == "2026-08-18", \
        "the reconstruction must freeze on the stale seed, or this test proves nothing"

    got = _entries(monkeypatch, stored=stored, packaged=packaged)
    assert max(e["date"] for e in got) == "2026-08-23"


def test_the_package_ships_the_file_the_fallback_reads():
    """The whole fix depends on the generated history landing at the path _version_entries reads.

    That path is the REPO-ROOT data/ directory, NOT hvf_web/data/ — a distinction that already cost one
    ineffective fix, which wrote the file to hvf_web/data/version_history.json where nothing reads it.
    package_files() excludes data/ entirely, so the packager must add it explicitly.
    """
    import os
    from pathlib import Path

    import build_ionos_package
    from hvf_web import server as _srv

    want = os.path.relpath(_srv._VERSION_FILE, build_ionos_package.ROOT).replace(os.sep, "/")
    text = Path(build_ionos_package.__file__).read_text(encoding="utf-8")

    assert want == "data/version_history.json", "the server's version file moved; update the packager"
    assert hasattr(build_ionos_package, "version_history_entries")
    assert f'zipfile.ZipInfo("{want}")' in text, (
        "the packager must add the generated history at the exact path _version_entries reads")
    assert not build_ionos_package.include_path(Path("data/version_history.json")), (
        "data/ is expected to be excluded from package_files(), which is why the explicit add is needed")
