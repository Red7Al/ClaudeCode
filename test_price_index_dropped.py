"""idx_price_history_ticker_date stays dropped (owner 2026-10-18: room for more instruments on the free tier).

Two places re-created it on every run -- price_store.ensure_schema and run_schema.MIGRATIONS -- so dropping it
by hand would have been undone the same night. The read it served is now a per-ticker LIMIT 1 on the primary key.
"""
import price_store
import run_schema


def test_nothing_recreates_the_duplicate_index():
    ddl = " ".join(price_store._DDL).lower()
    assert "idx_price_history_ticker_date" not in ddl
    creates = [sql for _n, sql in run_schema.MIGRATIONS
               if "idx_price_history_ticker_date" in sql and "create" in sql.lower()]
    assert creates == []


def test_run_schema_drops_it_without_a_table_lock():
    drops = [sql for _n, sql in run_schema.MIGRATIONS if "idx_price_history_ticker_date" in sql]
    assert drops == ["DROP INDEX CONCURRENTLY IF EXISTS idx_price_history_ticker_date"]


def test_the_source_lookup_no_longer_sorts_every_ticker_newest_first():
    src = open("hvf_web/server.py", encoding="utf-8").read()
    assert "select distinct on (ticker) ticker, source from price_history" not in src
    assert "(select source from price_history p where p.ticker = t.tk order by bar_date desc limit 1)" in src
