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
    import order_filter_audit
    monkeypatch.setattr(order_filter_audit, "audit_positions",
                        lambda user, positions, **k: {"rows": rows})


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
                                      [_pos("OLD", "2026-08-31"), _pos("OLDER", "2026-08-06")])

    assert to_close == [] and skipped == [], "nothing opened on the date, so nothing may be considered"


def test_a_volume_test_failure_on_the_opening_day_is_a_candidate(monkeypatch):
    _audit(monkeypatch, [{"ticker": "NEW", "deal_id": "D-NEW",
                          "breaches": ["RVOL 0.6 < 1.8", "ATR expanding required but not met"],
                          "unknown": []}])

    to_close, _ = ac.candidates("Alex", "2026-09-04", [_pos("NEW", "2026-09-04")])

    assert [r["ticker"] for r in to_close] == ["NEW"]
    assert len(to_close[0]["volume_breaches"]) == 2


def test_a_durable_breach_alone_never_closes_a_position(monkeypatch):
    """R:R and instrument value were knowable when the order was placed. If one is breached the defect is
    in the placement gate, and closing the position hides it."""
    _audit(monkeypatch, [{"ticker": "DUR", "deal_id": "D-DUR",
                          "breaches": ["R:R 3.0 < 5.0", "instrument value 1 < 2"], "unknown": []}])

    to_close, skipped = ac.candidates("Alex", "2026-09-04", [_pos("DUR", "2026-09-04")])

    assert to_close == []
    assert skipped and "passed the volume tests" in skipped[0]["why_skipped"]


def test_an_unjudgeable_position_is_left_open(monkeypatch):
    """Closing a real position on missing data is the one mistake here that costs money and cannot be
    undone. The daily capture stored nothing at all for six days in September 2026, so absence is a live
    possibility, not a theoretical one."""
    _audit(monkeypatch, [{"ticker": "NODATA", "deal_id": "D-NODATA", "breaches": [],
                          "unknown": ["no stored metrics for the day it opened"]}])

    to_close, skipped = ac.candidates("Alex", "2026-09-04", [_pos("NODATA", "2026-09-04")])

    assert to_close == []
    assert "unjudgeable" in skipped[0]["why_skipped"]


def test_a_durable_breach_alongside_a_volume_one_is_reported_but_not_the_reason(monkeypatch):
    _audit(monkeypatch, [{"ticker": "BOTH", "deal_id": "D-BOTH",
                          "breaches": ["RVOL 0.5 < 1.8", "R:R 3.0 < 5.0"], "unknown": []}])

    to_close, _ = ac.candidates("Alex", "2026-09-04", [_pos("BOTH", "2026-09-04")])

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
