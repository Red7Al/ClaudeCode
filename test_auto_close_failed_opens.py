"""Closing a same-day open that failed its volume tests (user 2026-09-04).

Every test here is a SAFETY property. This module closes real positions for real money with no human in
the loop, so the things worth pinning are the ones that stop it doing that wrongly:

  * it never touches a position opened on an earlier day;
  * it never closes on a durable breach, which is a placement-gate defect rather than a reason to sell;
  * it never closes on an absence of evidence;
  * it is off unless switched on, and it refuses a run that suddenly matches everything.

Measured on the live book the day it was written: 14 open positions all failed these tests, and every one
of them had opened days earlier. A rule without the same-day guard would have closed the entire account.
"""
import auto_close_failed_opens as ac


def _pos(ticker, opened, deal=None):
    return {"ticker": ticker, "deal_id": deal or f"D-{ticker}", "opened": opened,
            "direction": "BUY", "size": 0.01}


def _audit(monkeypatch, rows):
    """Stand in for the real audit, returning only rows for the positions it was actually given.

    The double used to return every row whatever it was passed, which made it impossible to tell whether a
    caller had filtered its input -- the closing-window gate filters exactly there, so the double would
    have reported the gate working when it was not.
    """
    import order_filter_audit

    def _fake(user, positions, **k):
        wanted = {p["ticker"] for p in positions}
        return {"rows": [r for r in rows if r["ticker"] in wanted]}

    monkeypatch.setattr(order_filter_audit, "audit_positions", _fake)


# ------------------------------------------------------------------------------------------------------
# The guard that matters most
# ------------------------------------------------------------------------------------------------------

def test_a_position_opened_on_an_earlier_day_is_never_a_candidate(monkeypatch):
    """THE BUG THIS PREVENTS. The volume tests are a check ON THE OPEN, not a rolling re-test. All 14
    live positions failed them on 2026-09-04 and every one had opened earlier; without this guard the
    first run would have closed the whole account."""
    _audit(monkeypatch, [{"ticker": "OLD", "deal_id": "D-OLD", "breaches": ["RVOL 0.5 < 1.8"],
                          "unknown": []}])

    to_close, skipped = ac.candidates("Alex", "2026-09-04",
                                      [_pos("OLD", "2026-08-31"), _pos("OLDER", "2026-08-06")],
                                      require_window=False)

    assert to_close == [] and skipped == [], "nothing opened on the date, so nothing may be considered"


def test_a_volume_test_failure_on_the_opening_day_is_a_candidate(monkeypatch):
    _audit(monkeypatch, [{"ticker": "NEW", "deal_id": "D-NEW",
                          "breaches": ["RVOL 0.6 < 1.8", "ATR expanding required but not met"],
                          "unknown": []}])

    to_close, _ = ac.candidates("Alex", "2026-09-04", [_pos("NEW", "2026-09-04")], require_window=False)

    assert [r["ticker"] for r in to_close] == ["NEW"]
    assert len(to_close[0]["volume_breaches"]) == 2


def test_a_durable_breach_alone_never_closes_a_position(monkeypatch):
    """R:R and instrument value were knowable when the order was placed. If one is breached the defect is
    in the placement gate, and closing the position hides it."""
    _audit(monkeypatch, [{"ticker": "DUR", "deal_id": "D-DUR",
                          "breaches": ["R:R 3.0 < 5.0", "instrument value 1 < 2"], "unknown": []}])

    to_close, skipped = ac.candidates("Alex", "2026-09-04", [_pos("DUR", "2026-09-04")], require_window=False)

    assert to_close == []
    assert skipped and "passed the volume tests" in skipped[0]["why_skipped"]


def test_an_unjudgeable_position_is_left_open(monkeypatch):
    """Closing a real position on missing data is the one mistake here that costs money and cannot be
    undone. The daily capture stored nothing at all for six days in September 2026, so absence is a live
    possibility, not a theoretical one."""
    _audit(monkeypatch, [{"ticker": "NODATA", "deal_id": "D-NODATA", "breaches": [],
                          "unknown": ["no stored metrics for the day it opened"]}])

    to_close, skipped = ac.candidates("Alex", "2026-09-04", [_pos("NODATA", "2026-09-04")], require_window=False)

    assert to_close == []
    assert "unjudgeable" in skipped[0]["why_skipped"]


def test_a_durable_breach_alongside_a_volume_one_is_reported_but_not_the_reason(monkeypatch):
    _audit(monkeypatch, [{"ticker": "BOTH", "deal_id": "D-BOTH",
                          "breaches": ["RVOL 0.5 < 1.8", "R:R 3.0 < 5.0"], "unknown": []}])

    to_close, _ = ac.candidates("Alex", "2026-09-04", [_pos("BOTH", "2026-09-04")], require_window=False)

    assert to_close[0]["volume_breaches"] == ["RVOL 0.5 < 1.8"]
    assert to_close[0]["durable_breaches"] == ["R:R 3.0 < 5.0"], "recorded, so the gate defect stays visible"


# ------------------------------------------------------------------------------------------------------
# The switch and the limit
# ------------------------------------------------------------------------------------------------------

def test_the_volume_test_set_is_exactly_the_break_bar_measures():
    """If a durable criterion ever leaked into this tuple it would silently become a sell trigger."""
    assert set(ac.VOLUME_TESTS) == {"RVOL", "VolumeScore", "above VWAP", "ATR expanding"}
    assert "R:R" not in ac.VOLUME_TESTS and "instrument value" not in ac.VOLUME_TESTS


def test_it_refuses_a_run_that_matches_more_than_the_limit():
    """A criteria change that suddenly matches everything should trip a limit and be looked at."""
    assert ac.MAX_PER_RUN <= 10, "a cap that lets a whole book through is not a cap"


def test_the_same_day_rule_is_measured_in_utc():
    """IG stamps createdDateUTC in UTC. date.today() is LOCAL, and in BST the two differ between 00:00
    and 01:00 -- a position opened 23:30 UTC would be invisible to a run in that hour."""
    import datetime as dt

    assert ac.today_utc() == dt.datetime.now(dt.timezone.utc).date()


def test_the_module_never_uses_a_local_date_for_the_same_day_decision():
    import pathlib
    src = pathlib.Path("auto_close_failed_opens.py").read_text(encoding="utf-8")

    assert "date.today()" not in src, "the same-day comparison must not be made against a local date"


# ------------------------------------------------------------------------------------------------------
# The closing-window gate (user 2026-09-06)
# ------------------------------------------------------------------------------------------------------
import datetime as _dt                                                    # noqa: E402

import pytest                                                             # noqa: E402

UTC = _dt.timezone.utc


def _at_london_close(minutes_before):
    """A real instant relative to a real exchange close, derived rather than written down."""
    import market_hours
    close = market_hours.close_utc("BP.L", _dt.date(2026, 7, 15))
    return close - _dt.timedelta(minutes=minutes_before)


def test_a_position_outside_its_closing_window_is_skipped_not_closed(monkeypatch):
    """The window is the safety property: before it the bar is too incomplete to judge, after it the
    market has gone. Neither is a moment to sell."""
    _audit(monkeypatch, [{"ticker": "BP.L", "deal_id": "D-BP.L", "breaches": ["RVOL 0.5 < 1.8"],
                          "unknown": []}])

    to_close, skipped = ac.candidates("Alex", "2026-07-15", [_pos("BP.L", "2026-07-15")],
                                      now=_at_london_close(240))

    assert to_close == [], "four hours before the close is not the closing window"
    assert skipped and "closing window" in skipped[0]["why_skipped"]


def test_a_position_inside_its_closing_window_is_considered(monkeypatch):
    _audit(monkeypatch, [{"ticker": "BP.L", "deal_id": "D-BP.L", "breaches": ["RVOL 0.5 < 1.8"],
                          "unknown": []}])

    to_close, _ = ac.candidates("Alex", "2026-07-15", [_pos("BP.L", "2026-07-15")],
                                now=_at_london_close(10))

    assert [r["ticker"] for r in to_close] == ["BP.L"]


def test_the_window_is_each_exchange_s_own_not_one_clock_for_the_book(monkeypatch):
    """The book spans Sydney to New York, so there is no single moment at which "the market" is closing.

    At ten minutes before London's close, Tokyo shut hours ago and New York has hours to run.
    """
    _audit(monkeypatch, [{"ticker": t, "deal_id": f"D-{t}", "breaches": ["RVOL 0.5 < 1.8"], "unknown": []}
                         for t in ("BP.L", "7203.T", "AAPL")])

    to_close, skipped = ac.candidates(
        "Alex", "2026-07-15",
        [_pos("BP.L", "2026-07-15"), _pos("7203.T", "2026-07-15"), _pos("AAPL", "2026-07-15")],
        now=_at_london_close(10))

    assert [r["ticker"] for r in to_close] == ["BP.L"]
    assert {s["ticker"] for s in skipped} == {"7203.T", "AAPL"}


def test_a_continuously_traded_instrument_is_never_in_a_window(monkeypatch):
    """FX and crypto have no closing bar, so the evidence this rule waits for never arrives."""
    _audit(monkeypatch, [{"ticker": "GBPUSD", "deal_id": "D-GBPUSD", "breaches": ["RVOL 0.5 < 1.8"],
                          "unknown": []}])
    for hour in range(0, 24, 3):
        to_close, _ = ac.candidates("Alex", "2026-07-15", [_pos("GBPUSD", "2026-07-15")],
                                    now=_dt.datetime(2026, 7, 15, hour, tzinfo=UTC))
        assert to_close == [], f"a continuous instrument was judged closeable at {hour}:00"


def test_applying_without_the_window_gate_is_refused():
    """require_window=False is a dry-run aid. Combining it with apply would close a position at a moment
    the window exists to forbid, so the combination is rejected rather than documented."""
    with pytest.raises(ValueError, match="closing-window"):
        ac.run(user="Alex", apply=True, require_window=False)


def test_the_epic_is_carried_so_the_close_can_ask_ig_if_the_market_is_dealable(monkeypatch):
    """A missing epic makes is_tradeable_now fail closed, which would silently keep every position open --
    a safe failure, but an invisible one. audit_positions rebuilds its rows, so it has to be merged back."""
    _audit(monkeypatch, [{"ticker": "BP.L", "deal_id": "D-BP.L", "breaches": ["RVOL 0.5 < 1.8"],
                          "unknown": []}])
    pos = {**_pos("BP.L", "2026-07-15"), "epic": "KA.D.BP.DAILY.IP"}

    to_close, _ = ac.candidates("Alex", "2026-07-15", [pos], now=_at_london_close(10))

    assert to_close[0]["epic"] == "KA.D.BP.DAILY.IP"
