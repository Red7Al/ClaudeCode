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


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_PRECOMPUTED_DIR", str(tmp_path))
    monkeypatch.setattr(server, "_load_snapshot", lambda: {"generated_utc": GEN})
    server._PRECOMPUTED_CACHE.clear()
    db_reads = []
    monkeypatch.setattr(web_store, "load_json_store", lambda k: db_reads.append(k) or _doc(["from the database"]))
    yield tmp_path, db_reads
    server._PRECOMPUTED_CACHE.clear()


def test_a_current_file_is_served_without_asking_the_database(files):
    d, db_reads = files
    (d / "winners_rows_3y.json").write_text(json.dumps(_doc(["from the file"])), encoding="utf-8")
    assert server._winners_stored(3) == {"rows": ["from the file"]}
    assert server._winners_stored(3) == {"rows": ["from the file"]} and db_reads == []


def test_the_file_is_read_once_until_it_changes(files, monkeypatch):
    d, _ = files
    f = d / "performance_rows_12m.json"
    f.write_text(json.dumps(_doc(["a"])), encoding="utf-8")
    opened = []
    real = open
    monkeypatch.setattr("builtins.open", lambda *a, **k: opened.append(a[0]) or real(*a, **k))
    assert server._perf_stored() == {"rows": ["a"]} and server._perf_stored() == {"rows": ["a"]}
    assert len([p for p in opened if str(p).endswith("performance_rows_12m.json")]) == 1


def test_a_file_from_an_earlier_scan_falls_back_to_the_database(files):
    d, db_reads = files
    (d / "winners_rows_1y.json").write_text(json.dumps(_doc(["old"], dataset="2026-10-21T18:30:00Z")), encoding="utf-8")
    assert server._winners_stored(1) == {"rows": ["from the database"]} and db_reads == ["winners_rows_1y"]


def test_no_file_or_a_damaged_one_reads_the_database(files):
    d, db_reads = files
    assert server._winners_stored(1) == {"rows": ["from the database"]}
    (d / "winners_rows_3y.json").write_text("{not json", encoding="utf-8")
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
    assert json.loads((tmp_path / "winners_rows_1y.json").read_text(encoding="utf-8")) == doc


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
