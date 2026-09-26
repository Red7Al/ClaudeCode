"""Stored RVOL/VWAP/ATR must survive a day the writer did not run.

WHY (owner, 2026-09-26): "we should not be waiting on jobs to be run to have data in columns - we have
enough data refreshes to avoid that."

WHAT IT WAS. _stored_metrics demanded `as_of == date.today()` exactly. MEASURED that day: the newest
as_of in instrument_metrics_daily was 2026-09-25 and today was 2026-09-26, so the function returned {}
for all 1,773 instruments even though the table was 98-100% populated. instrument_metrics.latest() is
documented as returning "the most recent stored metrics -- what an order placed days ago can be judged
against", and the equality test threw away exactly what that reader exists to provide.

WHY THERE WAS NO ROW FOR THAT DAY, which is the point. The writer runs only as step 2 of the Morning
Chain (`30 3 * * 1-6`); that morning the chain was killed at its 90-minute cap and all six downstream
jobs were skipped. One hung job emptied four columns across the whole site. The same would happen every
Sunday, every bank holiday, and after any failure of that chain -- so the fix is not to make the chain
more reliable, it is to stop a single missed run from blanking data we already hold.

THE CEILING IS NOT UNLIMITED. Stale numbers presented as current are worse than blanks, so a row older
than _STORED_METRICS_MAX_AGE_DAYS is still refused: past that, the writer has genuinely stopped.
"""

import datetime as dt
import os

import pytest

os.environ.setdefault("CRONJOB_API_KEY", "test-placeholder")

from hvf_web import server


def _snap(gen):
    return {"generated_utc": gen, "records": [{"ticker": "AAA.L"}, {"ticker": "BBB.L"}]}


def _row(days_ago):
    d = dt.date.today() - dt.timedelta(days=days_ago)
    return {"as_of": d, "bar_date": d, "rvol": 1.23, "above_vwap": True,
            "atr_expanding": True, "volume_score": 7, "status": "TRIGGERED", "source": "test"}


def _wire(monkeypatch, rows):
    """Point _stored_metrics at `rows` and clear the cache it keys on generated_utc."""
    import instrument_metrics
    monkeypatch.setattr(instrument_metrics, "latest", lambda tickers, db=None: rows)
    server._STORED_METRICS_CACHE.update(gen=None, data={})


def test_yesterdays_metrics_are_used(monkeypatch):
    """THE DEFECT. The table was 98-100% populated and the screen showed nothing, because the newest row
    was one day old."""
    _wire(monkeypatch, {"AAA.L": _row(1), "BBB.L": _row(1)})
    out = server._stored_metrics(_snap("g1"))
    assert set(out) == {"AAA.L", "BBB.L"}, "a one-day-old row is the normal state, not an error"
    assert out["AAA.L"]["rvol"] == 1.23


def test_todays_metrics_are_still_used(monkeypatch):
    _wire(monkeypatch, {"AAA.L": _row(0)})
    assert set(server._stored_metrics(_snap("g2"))) == {"AAA.L"}


def test_a_weekend_gap_is_survived(monkeypatch):
    """The writer runs Mon-Sat, so on a Sunday the newest row is Saturday's. Two days must be fine."""
    _wire(monkeypatch, {"AAA.L": _row(2)})
    assert set(server._stored_metrics(_snap("g3"))) == {"AAA.L"}


def test_a_row_at_the_ceiling_is_used(monkeypatch):
    _wire(monkeypatch, {"AAA.L": _row(server._STORED_METRICS_MAX_AGE_DAYS)})
    assert set(server._stored_metrics(_snap("g4"))) == {"AAA.L"}


def test_a_row_past_the_ceiling_is_refused(monkeypatch):
    """Past the ceiling the writer has genuinely stopped. Showing month-old RVOL as current would be
    worse than showing nothing, and would hide the outage that caused it."""
    _wire(monkeypatch, {"AAA.L": _row(server._STORED_METRICS_MAX_AGE_DAYS + 1)})
    assert server._stored_metrics(_snap("g5")) == {}


def test_a_future_dated_row_is_refused(monkeypatch):
    """A clock skew or a bad write must not read as fresh."""
    _wire(monkeypatch, {"AAA.L": _row(-3)})
    assert server._stored_metrics(_snap("g6")) == {}


def test_an_unreadable_as_of_is_refused_not_treated_as_fresh(monkeypatch):
    bad = _row(0)
    bad["as_of"] = "not-a-date"
    _wire(monkeypatch, {"AAA.L": bad, "BBB.L": _row(1)})
    out = server._stored_metrics(_snap("g7"))
    assert set(out) == {"BBB.L"}, "one unreadable row must not take the readable ones down with it"


def test_a_missing_as_of_is_refused(monkeypatch):
    bad = _row(0)
    del bad["as_of"]
    _wire(monkeypatch, {"AAA.L": bad})
    assert server._stored_metrics(_snap("g8")) == {}


def test_the_reader_failing_does_not_raise(monkeypatch):
    """A database problem must degrade to recomputing, never to a 500 on the Scanner."""
    import instrument_metrics

    def _boom(tickers, db=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(instrument_metrics, "latest", _boom)
    server._STORED_METRICS_CACHE.update(gen=None, data={})
    assert server._stored_metrics(_snap("g9")) == {}


def test_the_ceiling_is_wider_than_the_writers_own_longest_gap():
    """The writer runs `30 3 * * 1-6`, whose longest legitimate gap is Saturday to Monday. A ceiling at
    or below that would blank the columns on a day nothing was wrong."""
    import cron_spec
    assert cron_spec.max_gap_days("30 3 * * 1-6") == pytest.approx(2.0)
    assert server._STORED_METRICS_MAX_AGE_DAYS > 2.0
