"""The web tier re-read ~27,400 squeeze_history rows every 15 minutes whether or not the table had changed
(MEASURED 2026-10-22). It now reads them only when Postgres's write counter for the table has moved."""
from hvf_web import server


class _Db:
    def __init__(self, counter):
        self.counter, self.reads = counter, 0

    def run(self, sql, **kw):
        if "pg_stat_user_tables" in sql:
            if self.counter is None:
                raise RuntimeError("permission denied")
            return [[self.counter]]
        self.reads += 1
        return [["AAA", "US", "1d", "BULL", 70, 3.5, 2.0, "WIN", 4.2, None, None, 10, 9, 13.5]]


def _fresh():
    server._SQA_RAW.update(version=None, raw=None)


def test_an_unchanged_table_is_not_read_again():
    _fresh()
    db = _Db(625981)
    first = server._squeeze_history_raw(db, 10.0)
    assert server._squeeze_history_raw(db, 10.0) == first and db.reads == 1


def test_a_write_to_the_table_is_read():
    _fresh()
    db = _Db(625981)
    server._squeeze_history_raw(db, 10.0)
    db.counter += 4
    server._squeeze_history_raw(db, 10.0)
    assert db.reads == 2


def test_without_the_counter_every_call_reads_as_before():
    _fresh()
    db = _Db(None)
    server._squeeze_history_raw(db, 10.0)
    server._squeeze_history_raw(db, 10.0)
    assert db.reads == 2


def test_the_population_is_the_same_query_as_before():
    src = open("hvf_web/server.py", encoding="utf-8").read()
    assert "and risk_reward >= 3 and risk_reward <= :maxrr" in src
    assert src.count("from squeeze_history where ready_date is not null ") == 1
