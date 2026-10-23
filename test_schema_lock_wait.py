"""Morning Chain 2026-10-23: run_schema's `ALTER TABLE price_history` queued behind the price-file read, sat past the
socket timeout, and the dead connection failed all 18 later statements -- the chain stopped. A busy table now ends the
wait cleanly (Postgres 55P03, MEASURED: connection stays usable) and the statement is retried."""
import pytest

import run_schema

LOCK = Exception("{'S': 'ERROR', 'C': '55P03', 'M': 'canceling statement due to lock timeout'}")


class _Conn:
    def __init__(self, busy_for):
        self.busy_for, self.ran = busy_for, []

    def run(self, sql):
        if sql.startswith("ALTER") and self.busy_for:
            self.busy_for -= 1
            raise LOCK
        self.ran.append(sql)

    def close(self):
        pass


def _main(monkeypatch, conn):
    monkeypatch.setattr(run_schema, "_pool_get_db", lambda: conn)
    monkeypatch.setattr(run_schema, "LOCK_RETRY_WAIT", 0)
    monkeypatch.setattr(run_schema, "MIGRATIONS", [("busy", "ALTER TABLE t ADD COLUMN IF NOT EXISTS c int"),
                                                   ("after", "CREATE INDEX IF NOT EXISTS i ON t (c)")])
    run_schema.main()


def test_a_busy_table_is_waited_for_and_every_statement_applies(monkeypatch):
    conn = _Conn(busy_for=3)
    _main(monkeypatch, conn)
    assert conn.ran[0] == f"SET lock_timeout = '{run_schema.LOCK_TIMEOUT}'"
    assert conn.ran[1:] == ["ALTER TABLE t ADD COLUMN IF NOT EXISTS c int", "CREATE INDEX IF NOT EXISTS i ON t (c)"]


def test_a_table_busy_for_too_long_still_fails_the_run(monkeypatch):
    conn = _Conn(busy_for=run_schema.LOCK_RETRIES + 1)
    with pytest.raises(SystemExit):
        _main(monkeypatch, conn)
    assert conn.ran[-1] == "CREATE INDEX IF NOT EXISTS i ON t (c)"   # the next statement still ran


def test_other_errors_are_not_retried():
    calls = []

    class C:
        def run(self, sql):
            calls.append(sql)
            raise Exception("syntax error")

    with pytest.raises(Exception, match="syntax"):
        run_schema._apply(C(), "x", "ALTER ...", sleep=lambda s: None)
    assert len(calls) == 1
