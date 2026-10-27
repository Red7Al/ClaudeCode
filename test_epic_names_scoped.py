"""Positions and orders are named from epic_lookup for ONLY the epics in hand (2026-10-27). Six readers each read the
whole table -- 336,368 rows in 153 calls over 17 h, the biggest query by rows (pg_stat_statements) -- to name a
handful of positions."""
import re
from pathlib import Path

import ig_shim


class _DB:
    def __init__(self, rows):
        self.rows, self.asked = rows, []

    def run(self, sql, **kw):
        self.asked.append((sql, kw))
        return self.rows

    def close(self):
        pass


def test_asks_only_for_the_epics_in_hand(monkeypatch):
    db = _DB([("VOD.L", "EPIC1"), ("AAPL", "EPIC2")])
    monkeypatch.setattr("db_pool.get_db", lambda: db)
    assert ig_shim.tickers_for_epics(["EPIC2", None, "EPIC1", "EPIC2", ""]) == {"EPIC1": "VOD.L", "EPIC2": "AAPL"}
    assert db.asked == [("select ticker, epic from epic_lookup where epic = any(:e) order by ctid",
                         {"e": ["EPIC1", "EPIC2"]})]


def test_a_shared_epic_keeps_the_last_row_as_the_whole_table_read_did(monkeypatch):
    """Rows come back in ctid (table) order; the whole-table read's dict kept the last one for a shared epic."""
    monkeypatch.setattr("db_pool.get_db", lambda: _DB([("FIRST", "SHARED"), ("LAST", "SHARED")]))
    assert ig_shim.tickers_for_epics(["SHARED"]) == {"SHARED": "LAST"}


def test_nothing_held_asks_nothing(monkeypatch):
    def boom():
        raise AssertionError("no epics, so no database call")
    monkeypatch.setattr("db_pool.get_db", boom)
    assert ig_shim.tickers_for_epics([]) == {}
    assert ig_shim.tickers_for_epics(None) == {}


def test_no_code_reads_the_whole_epic_map():
    root = Path(__file__).parent
    unscoped = re.compile(r"""select ticker, epic from epic_lookup["']""")
    found = [f"{p.relative_to(root)}:{n}" for p in [*root.glob("*.py"), *root.glob("hvf_web/*.py")]
             if not p.name.startswith("test_")
             for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if unscoped.search(line)]
    assert found == []
