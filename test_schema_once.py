"""Each GitHub job is a new process, so "once per process" still ran every module's CREATE/ALTER IF NOT EXISTS on every
job, and each no-op DDL made Supabase reload its schema cache (MEASURED 2026-10-23). schema_once records a statement list
once applied, across all jobs."""
import pytest

import schema_once

NO_TABLE = Exception("{'C': '42P01', 'M': 'relation \"schema_migrations\" does not exist'}")


class _Db:
    def __init__(self, recorded=None):
        self.recorded, self.ddl = recorded, []

    def run(self, sql, **kw):
        if sql.startswith("select name, sql_hash from"):
            if self.recorded is None:
                raise NO_TABLE
            return [[n, h] for n, h in self.recorded.items()]
        if sql.startswith("create table if not exists schema_migrations"):
            self.recorded = {}
            return None
        if sql.startswith("insert into schema_migrations"):
            self.recorded[kw["n"]] = kw["h"]
            return None
        self.ddl.append(sql)


@pytest.fixture(autouse=True)
def _fresh_process(monkeypatch):
    monkeypatch.setattr(schema_once, "_APPLIED", None)


def test_the_next_job_runs_no_ddl(monkeypatch):
    db = _Db()
    assert schema_once.ensure(db, "web_store", ["create table a (x int)", "create index i on a (x)"]) is True
    monkeypatch.setattr(schema_once, "_APPLIED", None)          # a new job: a new process
    db.ddl.clear()
    assert schema_once.ensure(db, "web_store", ["create table a (x int)", "create index i on a (x)"]) is False
    assert db.ddl == []


def test_a_changed_list_runs_again():
    db = _Db()
    schema_once.ensure(db, "web_store", ["create table a (x int)"])
    db.ddl.clear()
    schema_once.ensure(db, "web_store", ["create table a (x int)", "alter table a add column if not exists y int"])
    assert db.ddl == ["create table a (x int)", "alter table a add column if not exists y int"]


def test_a_failure_records_nothing():
    class Failing(_Db):
        def run(self, sql, **kw):
            if sql.startswith("alter"):
                raise RuntimeError("lock timeout")
            return super().run(sql, **kw)

    db = Failing(recorded={})
    with pytest.raises(RuntimeError):
        schema_once.ensure(db, "m", ["alter table a add column if not exists y int"])
    assert db.recorded == {}


def test_run_schema_hashes_a_single_statement_as_before():
    """Records written by run_schema since 2026-10-23 (sha256 of the one statement) must still match."""
    import hashlib
    sql = "ALTER TABLE t ADD COLUMN IF NOT EXISTS c int"
    assert schema_once.sql_hash(sql) == hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]
    assert schema_once.sql_hash([sql]) == schema_once.sql_hash(sql)
