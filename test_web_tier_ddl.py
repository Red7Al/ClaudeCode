"""The website runs no schema DDL; the GitHub jobs do (2026-10-09).

MEASURED: one cold web process issued ~20 create/alter-if-not-exists statements, and
instrument_metrics.ensure_schema ran its ~11 on every call. cgi-bin/app.py sets HVF_WEB_TIER=1."""
import config_store
import instrument_metrics
import web_store


class _Db:
    def __init__(self):
        self.sql = []

    def run(self, sql, **k):
        self.sql.append(sql)
        return []

    def close(self):
        pass


def test_the_ionos_adapter_marks_the_web_tier():
    src = open("cgi-bin/app.py", encoding="utf-8").read()
    assert src.index('os.environ.setdefault("HVF_WEB_TIER", "1")') < src.index("from hvf_web.server import app")


def test_the_web_tier_runs_no_config_or_store_ddl(monkeypatch):
    monkeypatch.setenv("HVF_WEB_TIER", "1")
    monkeypatch.setattr(config_store, "_schema_ready", False)
    monkeypatch.setattr(web_store, "_ready", False)
    db = _Db()
    monkeypatch.setattr("db_pool.get_db", lambda: db)
    config_store._ensure(db)
    web_store._db()
    instrument_metrics._SCHEMA_READY = False
    instrument_metrics.ensure_schema(db)
    assert db.sql == []


def test_a_job_still_runs_the_ddl_once_per_process(monkeypatch):
    monkeypatch.delenv("HVF_WEB_TIER", raising=False)
    monkeypatch.setattr(instrument_metrics, "_SCHEMA_READY", False)
    db = _Db()
    instrument_metrics.ensure_schema(db)
    n = len(db.sql)
    instrument_metrics.ensure_schema(db)
    assert n > 0 and len(db.sql) == n, "the schema runs once per process, not on every call"
