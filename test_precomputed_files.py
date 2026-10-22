"""Precomputed payloads as files on IONOS (owner 2026-10-22: "the best settings can be in summary files - can't
they?"). MEASURED 2026-10-22: winners_rows_3y 7.27 MB, performance_rows_12m 3.29 MB, winners_rows_1y 2.67 MB as
JSON text, each fetched whole from Supabase on EVERY /api/winners and /api/performance request."""
import json
import time

import pytest
import yaml

import web_store
from hvf_web import server

GEN = "2026-10-22T05:10:00Z"


def _doc(rows, dataset=GEN, built_at=None):
    return {"dataset": dataset, "built_at": built_at or time.time(), "payload": {"rows": rows}}


def _file(d, key, doc, revision=5):
    (d / f"{key}.json").write_text(json.dumps({"store_key": key, "revision": revision, "doc": doc}), encoding="utf-8")


def _clear():
    for c in (server._REVISION_CACHE, server._STORED_DOC_CACHE, server._PRECOMPUTED_CACHE):
        c.clear()


@pytest.fixture
def files(tmp_path, monkeypatch):
    """The database is at revision 5; a file is used only when it carries that revision (CLAUDE.md: a file is
    used only when its version matches the database's)."""
    monkeypatch.setattr(server, "_PRECOMPUTED_DIR", str(tmp_path))
    monkeypatch.setattr(server, "_load_snapshot", lambda: {"generated_utc": GEN})
    _clear()
    db_reads = []
    monkeypatch.setattr(web_store, "read_json_store_revision", lambda k: 5)
    monkeypatch.setattr(web_store, "load_json_store", lambda k: db_reads.append(k) or _doc(["from the database"]))
    yield tmp_path, db_reads
    _clear()


def test_a_current_file_is_served_without_asking_the_database(files):
    d, db_reads = files
    _file(d, "winners_rows_3y", _doc(["from the file"]))
    assert server._winners_stored(3) == {"rows": ["from the file"]}
    assert server._winners_stored(3) == {"rows": ["from the file"]} and db_reads == []


def test_the_file_is_read_once_until_it_changes(files, monkeypatch):
    d, _ = files
    _file(d, "performance_rows_12m", _doc(["a"]))
    opened = []
    real = open
    monkeypatch.setattr("builtins.open", lambda *a, **k: opened.append(a[0]) or real(*a, **k))
    assert server._perf_stored() == {"rows": ["a"]} and server._perf_stored() == {"rows": ["a"]}
    assert len([p for p in opened if str(p).endswith("performance_rows_12m.json")]) == 1


def test_a_file_behind_the_database_falls_back_to_the_database(files):
    d, db_reads = files
    _file(d, "winners_rows_1y", _doc(["old"], dataset="2026-10-21T18:30:00Z"), revision=4)
    assert server._winners_stored(1) == {"rows": ["from the database"]} and db_reads == ["winners_rows_1y"]


def test_an_out_of_date_stored_copy_is_fetched_once_per_revision_not_per_request(files, monkeypatch):
    """Between a new scan and the next precompute both copies are out of date. The 7.27 MB document used to be
    fetched from Supabase on EVERY request in that window; now once per revision (efficiency review 2026-10-22)."""
    d, db_reads = files
    monkeypatch.setattr(web_store, "load_json_store",
                        lambda k: db_reads.append(k) or _doc(["old"], dataset="2026-10-21T18:30:00Z"))
    assert server._winners_stored(3) is None and server._winners_stored(3) is None
    assert db_reads == ["winners_rows_3y"]


def test_no_file_or_a_damaged_one_reads_the_database(files):
    d, db_reads = files
    assert server._winners_stored(1) == {"rows": ["from the database"]}
    (d / "winners_rows_3y.json").write_text("{not json", encoding="utf-8")
    server._PRECOMPUTED_CACHE.clear()
    assert server._winners_stored(3) == {"rows": ["from the database"]}
    assert db_reads == ["winners_rows_1y", "winners_rows_3y"]


def test_a_stale_database_copy_still_means_build_live(files, monkeypatch):
    monkeypatch.setattr(web_store, "load_json_store", lambda k: _doc(["x"], built_at=time.time() - 30 * 3600))
    assert server._winners_stored(1) is None


class _Db:
    def __init__(self, ok=True):
        self.ok = ok

    def run(self, sql, **kw):
        return [(4,)] if self.ok else []

    def close(self):
        pass


def test_a_job_that_asks_writes_the_stored_document_as_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(web_store, "_db", lambda: _Db())
    monkeypatch.setenv("PRECOMPUTED_OUT", str(tmp_path))
    doc = _doc(["r1", "r2"])
    assert web_store.save_json_store("winners_rows_1y", doc) is True
    assert json.loads((tmp_path / "winners_rows_1y.json").read_text(encoding="utf-8")) == {
        "store_key": "winners_rows_1y", "revision": 4, "doc": doc}


def test_no_file_without_the_setting_or_when_the_save_failed(tmp_path, monkeypatch):
    monkeypatch.delenv("PRECOMPUTED_OUT", raising=False)
    monkeypatch.setattr(web_store, "_db", lambda: _Db())
    web_store.save_json_store("winners_rows_1y", _doc([]))
    monkeypatch.setenv("PRECOMPUTED_OUT", str(tmp_path))
    monkeypatch.setattr(web_store, "_db", lambda: _Db(ok=False))
    web_store.save_json_store_versioned("winners_rows_3y", _doc([]), expected_revision=7)
    assert list(tmp_path.iterdir()) == []


def _steps(path, job):
    return yaml.safe_load(open(path, encoding="utf-8"))["jobs"][job]["steps"]


def test_both_writers_write_files_and_keep_them_on_ionos():
    for path, job, writers in ((".github/workflows/trading-scanner-snapshot.yml", "publish",
                                ["Warm the precomputed winners payloads", "Precompute the public Best Settings cards"]),
                               (".github/workflows/trading-winners-precompute.yml", "precompute",
                                ["Build and store the winners payloads"])):
        try:
            steps = _steps(path, job)
        except KeyError:
            steps = next(iter(yaml.safe_load(open(path, encoding="utf-8"))["jobs"].values()))["steps"]
        names = [s.get("name") or "" for s in steps]
        keep = names.index("Keep the precomputed payloads on IONOS")
        for w in writers:
            assert names.index(w) < keep and steps[names.index(w)]["env"]["PRECOMPUTED_OUT"] == "${{ runner.temp }}/precomputed"
        assert steps[keep]["run"] == "bash .github/scripts/keep_precomputed_on_ionos.sh"
        assert steps[keep]["continue-on-error"] is True and steps[keep]["env"]["PRECOMPUTED_OUT"] == "${{ runner.temp }}/precomputed"


# ── documents with no scan date: thread text, fundamentals -- proven by revision ─────────────────────
@pytest.fixture
def rev(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_PRECOMPUTED_DIR", str(tmp_path))
    for c in (server._REVISION_CACHE, server._STORED_DOC_CACHE, server._PRECOMPUTED_CACHE):
        c.clear()
    state = {"rev": 5, "probes": 0, "fetches": 0, "down": False}

    def probe(key):
        state["probes"] += 1
        if state["down"]:
            raise RuntimeError("supabase refused")
        return state["rev"]

    def fetch(key):
        state["fetches"] += 1
        return {"records": {"ABC": ["from the database"]}}
    monkeypatch.setattr(web_store, "read_json_store_revision", probe)
    monkeypatch.setattr(web_store, "load_json_store", fetch)
    yield tmp_path, state
    for c in (server._REVISION_CACHE, server._STORED_DOC_CACHE, server._PRECOMPUTED_CACHE):
        c.clear()


def _write(d, key, revision, doc):
    (d / f"{key}.json").write_text(json.dumps({"store_key": key, "revision": revision, "doc": doc}), encoding="utf-8")


def test_a_file_with_the_databases_revision_is_served_and_the_document_never_fetched(rev):
    d, st = rev
    _write(d, "thread_by_ticker", 5, {"records": {"ABC": ["from the file"]}})
    assert server._thread_stored("ABC") == ["from the file"]
    assert server._thread_stored("ABC") == ["from the file"]
    assert st["fetches"] == 0 and st["probes"] == 1, "one probe per _REVISION_TTL, not per request"


def test_a_file_behind_the_database_is_not_used_and_the_document_is_fetched_once(rev):
    d, st = rev
    _write(d, "thread_by_ticker", 4, {"records": {"ABC": ["old file"]}})
    assert server._thread_stored("ABC") == ["from the database"]
    server._REVISION_CACHE.clear()
    assert server._thread_stored("ABC") == ["from the database"] and st["fetches"] == 1


def test_when_supabase_refuses_the_file_is_served(rev):
    d, st = rev
    _write(d, "thread_by_ticker", 4, {"records": {"ABC": ["from the file"]}})
    st["down"] = True
    assert server._thread_stored("ABC") == ["from the file"] and st["fetches"] == 0


def test_a_refused_probe_is_not_asked_again_on_every_request(rev):
    """During an outage every instrument view used to try the database again (efficiency review 2026-10-22)."""
    d, st = rev
    _write(d, "thread_by_ticker", 4, {"records": {"ABC": ["from the file"]}})
    st["down"] = True
    assert [server._thread_stored("ABC") for _ in range(3)] == [["from the file"]] * 3
    assert st["probes"] == 1 and st["fetches"] == 0


def test_both_nightly_writers_keep_their_files_on_ionos():
    for path, step in ((".github/workflows/trading-fundamentals-precompute.yml", "Build and store the fundamentals + broker panels"),
                       (".github/workflows/trading-thread-precompute.yml", "Build and store the thread text")):
        steps = next(iter(yaml.safe_load(open(path, encoding="utf-8"))["jobs"].values()))["steps"]
        names = [x.get("name") or "" for x in steps]
        keep = names.index("Keep the precomputed payloads on IONOS")
        assert names.index(step) < keep and steps[names.index(step)]["env"]["PRECOMPUTED_OUT"] == "${{ runner.temp }}/precomputed"
        assert steps[keep]["env"]["IONOS_DIR"] == "${{ secrets.IONOS_DIR }}"
