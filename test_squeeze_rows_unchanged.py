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


# The NOW-price fallback asked price_history for ~1,400 tickers' latest close on every 15-minute rebuild
# (~2.4 s each). It now asks again only when the price data's version has changed.
class _CloseDb:
    reads = 0

    def run(self, sql, **kw):
        _CloseDb.reads += 1
        return [[tk, 1.5] for tk in kw["tks"]]

    def close(self):
        pass


def _closes(monkeypatch, version):
    monkeypatch.setattr("db_pool.get_db", lambda: _CloseDb())
    monkeypatch.setattr(server, "_price_data_version", lambda db: version)
    server._NOW_CLOSE.update(key=None, last={})
    _CloseDb.reads = 0


def test_unchanged_prices_are_not_asked_again(monkeypatch):
    _closes(monkeypatch, "2026-10-21:9000")
    first = server._latest_closes(["AAA", "BBB"])
    assert server._latest_closes(["AAA", "BBB"]) == first == {"AAA": 1.5, "BBB": 1.5}
    assert _CloseDb.reads == 1


def test_new_prices_or_other_tickers_are_asked(monkeypatch):
    _closes(monkeypatch, "2026-10-21:9000")
    server._latest_closes(["AAA"])
    server._latest_closes(["AAA", "BBB"])
    monkeypatch.setattr(server, "_price_data_version", lambda db: "2026-10-22:9100")
    server._latest_closes(["AAA", "BBB"])
    assert _CloseDb.reads == 3


def test_without_a_version_every_call_asks(monkeypatch):
    _closes(monkeypatch, "")
    server._latest_closes(["AAA"])
    server._latest_closes(["AAA"])
    assert _CloseDb.reads == 2
