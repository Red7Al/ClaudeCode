"""The winners replays must fetch each ticker's bars from its OWN trigger date, not the whole window
for every ticker in price_history (ChangeRequest P-06, 2026-09-03).

Measured cause: price_history bulk reads are 94.4% of every row this database returns, against a
free-tier egress allowance the account is already over. The two replays fetched 441,305 rows where
252,900 carry every bar a calculation touches -- the rest were discarded by the consumers' own
`>= trigger date` filters after crossing the wire.

The property that matters is not the saving, it is that NOTHING a calculation sees changes. These
tests drive _window_bars against a fake database that answers the VALUES join the way Postgres does,
and compare what the walk receives against what a whole-window fetch would have given it.
"""

import pytest

from hvf_web import server


#: ticker -> [(bar_date, high, low, close)] -- every bar we hold, as a full-window fetch would return.
BARS = {
    "AAA": [("2026-01-05", 11.0, 9.0, 10.0),
            ("2026-06-01", 13.0, 11.0, 12.0),
            ("2026-08-01", 15.0, 13.0, 14.0)],
    "BBB": [("2026-01-05", 21.0, 19.0, 20.0),
            ("2026-07-01", 23.0, 21.0, 22.0)],
}


class FakeDb:
    """Answers the VALUES join as Postgres would: each ticker from its own cutoff, nothing earlier."""

    def __init__(self, log):
        self.log = log

    def run(self, sql, **params):
        self.log.append((sql, params))
        cuts = {params[f"t{i}"]: params[f"d{i}"] for i in range(len(params) // 2)}
        return [(tk, bd, hi, lo, cl)
                for tk, cut in sorted(cuts.items())
                for bd, hi, lo, cl in BARS.get(tk, []) if bd >= cut]

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _clear_slot_cache():
    server._SLBARS.clear()
    yield
    server._SLBARS.clear()


def _rows(*pairs):
    return [{"ticker": tk, "trig_date": td} for tk, td in pairs]


def _install(monkeypatch):
    log = []
    monkeypatch.setattr("db_pool.get_db", lambda: FakeDb(log), raising=False)
    return log


def _cuts_of(params):
    return {params[f"t{i}"]: params[f"d{i}"] for i in range(len(params) // 2)}


def test_each_ticker_is_fetched_from_its_own_earliest_trigger(monkeypatch):
    log = _install(monkeypatch)

    server._window_bars(_rows(("AAA", "2026-08-01"), ("AAA", "2026-06-01"),
                              ("BBB", "2026-07-01")), "y1")

    sql, params = log[0]
    # AAA takes its EARLIEST trigger, not the last one seen: a ticker traded twice needs the forward
    # path of the older trade as well.
    assert _cuts_of(params) == {"AAA": "2026-06-01", "BBB": "2026-07-01"}
    assert "join (values" in sql, "must carry a per-ticker cutoff"
    assert "where bar_date >= " not in sql, "must not fall back to one blanket window for every ticker"


def test_the_walk_sees_exactly_the_bars_a_full_window_fetch_would_have_given(monkeypatch):
    _install(monkeypatch)
    rows = _rows(("AAA", "2026-06-01"), ("BBB", "2026-07-01"))

    narrow = server._window_bars(rows, "y1")

    for r in rows:
        td = r["trig_date"]
        walked = [b for b in narrow.get(r["ticker"], []) if b[0] >= td]
        whole_window = [b for b in BARS[r["ticker"]] if b[0] >= td]
        assert walked == whole_window, f"{r['ticker']} lost bars the calculation would have seen"


def test_a_cached_window_that_does_not_reach_back_far_enough_is_rebuilt(monkeypatch):
    log = _install(monkeypatch)
    server._window_bars(_rows(("AAA", "2026-08-01")), "y1")
    assert len(log) == 1

    # A later caller needs AAA from EARLIER than the cached fetch reached. Serving the cached slot
    # would truncate that trade's forward path and report an exit that never happened -- the same
    # class of fault as serving a 3-year replay from 1-year bars (2026-08-18).
    out = server._window_bars(_rows(("AAA", "2026-01-05")), "y1")

    assert len(log) == 2, "a shallower cache must be rebuilt, never served"
    assert [b[0] for b in out["AAA"]] == ["2026-01-05", "2026-06-01", "2026-08-01"]


def test_a_ticker_absent_from_the_cached_window_forces_a_rebuild(monkeypatch):
    log = _install(monkeypatch)
    server._window_bars(_rows(("AAA", "2026-01-05")), "y1")

    out = server._window_bars(_rows(("AAA", "2026-01-05"), ("BBB", "2026-01-05")), "y1")

    assert len(log) == 2
    assert set(out) == {"AAA", "BBB"}


def test_a_cached_window_that_covers_the_request_is_reused(monkeypatch):
    log = _install(monkeypatch)
    server._window_bars(_rows(("AAA", "2026-01-05")), "y1")

    server._window_bars(_rows(("AAA", "2026-06-01")), "y1")   # asks for less than is cached

    assert len(log) == 1, "a covering cache must not be re-fetched"


def test_one_year_and_three_year_windows_keep_separate_slots(monkeypatch):
    log = _install(monkeypatch)

    server._window_bars(_rows(("AAA", "2026-06-01")), "y1")
    server._window_bars(_rows(("AAA", "2026-06-01")), "y3")

    assert len(log) == 2
    assert set(server._SLBARS) == {"y1", "y3"}
