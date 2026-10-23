"""Morning Chain 2026-10-23: run_schema's `ALTER TABLE price_history` queued behind the price-file read, sat past the
socket timeout, and the dead connection failed all 18 later statements -- the chain stopped. A busy table now ends the
wait cleanly (Postgres 55P03, MEASURED: connection stays usable) and the statement is retried.

And each migration now runs ONCE: every no-op DDL made Supabase's REST layer reload its schema cache (MEASURED
2026-10-23), 516 reloads a day = 1.71 GB/30d of egress."""
import pytest

import run_schema

LOCK = Exception("{'S': 'ERROR', 'C': '55P03', 'M': 'canceling statement due to lock timeout'}")
NO_TABLE = Exception("{'S': 'ERROR', 'C': '42P01', 'M': 'relation \"schema_migrations\" does not exist'}")
ALTER = "ALTER TABLE t ADD COLUMN IF NOT EXISTS c int"
INDEX = "CREATE INDEX IF NOT EXISTS i ON t (c)"


class _Conn:
    """A fake connection that keeps schema_migrations like the database would."""

    def __init__(self, busy_for=0, recorded=None):
        self.busy_for, self.ran, self.recorded = busy_for, [], recorded

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
        if sql.startswith("ALTER") and self.busy_for:
            self.busy_for -= 1
            raise LOCK
        self.ran.append(sql)

    def close(self):
        pass


def _main(monkeypatch, conn, reapply=False):
    monkeypatch.setattr(run_schema, "_pool_get_db", lambda: conn)
    monkeypatch.setattr(run_schema, "LOCK_RETRY_WAIT", 0)
    monkeypatch.setattr(run_schema, "MIGRATIONS", [("busy", ALTER), ("after", INDEX)])
    if reapply:
        monkeypatch.setenv("SCHEMA_REAPPLY", "1")
    else:
        monkeypatch.delenv("SCHEMA_REAPPLY", raising=False)
    run_schema.main()


def test_a_busy_table_is_waited_for_and_every_statement_applies(monkeypatch):
    conn = _Conn(busy_for=3)
    _main(monkeypatch, conn)
    assert conn.ran == [f"SET lock_timeout = '{run_schema.LOCK_TIMEOUT}'", ALTER, INDEX]


def test_a_table_busy_for_too_long_still_fails_the_run(monkeypatch):
    conn = _Conn(busy_for=run_schema.LOCK_RETRIES + 1)
    with pytest.raises(SystemExit):
        _main(monkeypatch, conn)
    assert conn.ran[-1] == INDEX                    # the next statement still ran
    assert set(conn.recorded) == {"after"}          # the failed one is not recorded, so the next run tries it again


def test_other_errors_are_not_retried():
    calls = []

    class C:
        def run(self, sql):
            calls.append(sql)
            raise Exception("syntax error")

    with pytest.raises(Exception, match="syntax"):
        run_schema._apply(C(), "x", "ALTER ...", sleep=lambda s: None)
    assert len(calls) == 1


def test_an_applied_migration_issues_no_ddl_on_the_next_run(monkeypatch):
    conn = _Conn()
    _main(monkeypatch, conn)
    conn.ran.clear()
    _main(monkeypatch, conn)
    assert conn.ran == [f"SET lock_timeout = '{run_schema.LOCK_TIMEOUT}'"]


def test_a_changed_statement_runs_again(monkeypatch):
    conn = _Conn()
    _main(monkeypatch, conn)
    conn.recorded["after"] = "an-older-hash"
    conn.ran.clear()
    _main(monkeypatch, conn)
    assert conn.ran[1:] == [INDEX]


def test_reapply_runs_everything(monkeypatch):
    conn = _Conn()
    _main(monkeypatch, conn)
    conn.ran.clear()
    _main(monkeypatch, conn, reapply=True)
    assert conn.ran[1:] == [ALTER, INDEX]
