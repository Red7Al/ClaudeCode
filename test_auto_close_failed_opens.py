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


# ------------------------------------------------------------------------------------------------------
# The closing-bar capture (user 2026-09-07: "retrieve more data towards the end of the working day")
# ------------------------------------------------------------------------------------------------------

def test_a_bar_from_an_earlier_session_is_never_captured_as_todays(monkeypatch):
    """THE HOLIDAY BUG, found live on 2026-09-07 — US Labor Day.

    market_hours derives a session's CLOCK, not which days it runs, so on a holiday it reports a closing
    window for a market that never opened. New York was shut, Yahoo's newest bar was Friday 2026-09-04,
    and the capture stored it as today's break-bar metrics: a row that says today and means Friday.
    """
    import closing_bar_capture as cbc
    import market_hours
    import pandas as pd

    monkeypatch.setattr(market_hours, "volume_projection_factor", lambda t: 0.8)
    monkeypatch.setattr(market_hours, "session", lambda t: {"tz": "America/New_York",
                                                            "open": "09:30", "close": "16:00"})
    stale = _dt.datetime.now(_dt.timezone.utc).date() - _dt.timedelta(days=3)
    frame = pd.DataFrame({"High": [1.0], "Low": [0.5], "Close": [0.8], "Volume": [1000.0]},
                         index=pd.DatetimeIndex([pd.Timestamp(stale, tz="America/New_York")]))

    class _T:
        def __init__(self, *a, **k): pass
        def history(self, **k): return frame

    monkeypatch.setattr(cbc, "todays_bar", cbc.todays_bar)          # keep the real function under test
    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", _T)

    assert cbc.todays_bar("AAPL") is None, \
        "a bar from an earlier session must never be captured as today's"


def test_a_todays_bar_is_captured_with_its_volume_projected(monkeypatch):
    """The correction divides by the exchange's p05 completion share, so the projection reads HIGH."""
    import closing_bar_capture as cbc
    import market_hours
    import pandas as pd
    from zoneinfo import ZoneInfo

    monkeypatch.setattr(market_hours, "volume_projection_factor", lambda t: 0.5)
    monkeypatch.setattr(market_hours, "session", lambda t: {"tz": "America/New_York",
                                                            "open": "09:30", "close": "16:00"})
    today = _dt.datetime.now(_dt.timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
    frame = pd.DataFrame({"High": [2.0], "Low": [1.0], "Close": [1.5], "Volume": [1000.0]},
                         index=pd.DatetimeIndex([pd.Timestamp(today, tz="America/New_York")]))

    class _T:
        def __init__(self, *a, **k): pass
        def history(self, **k): return frame

    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", _T)

    got = cbc.todays_bar("AAPL")
    assert got is not None and got[0] == today.isoformat()
    assert got[4] == 2000.0, "volume must be divided by the completion share, not passed through raw"


def test_an_exchange_with_no_measured_volume_share_is_not_captured(monkeypatch):
    """No share means no basis for the correction, and an uncorrected volume is what closes a position
    it should not. Refused rather than passed through at face value."""
    import closing_bar_capture as cbc
    import market_hours
    monkeypatch.setattr(market_hours, "volume_projection_factor", lambda t: None)

    assert cbc.todays_bar("^GSPC") is None


def test_the_scheduled_job_is_armed_and_still_carries_its_guards():
    """The closer is LIVE against the real account (owner, 2026-09-07: "--apply").

    Pinned as a test because arming it is the single most consequential switch in this repository and it
    must never change by accident in either direction. If it is deliberately disarmed, this test is the
    place that records the decision.
    """
    import pathlib
    wf = pathlib.Path(".github/workflows/trading-closing-window.yml").read_text(encoding="utf-8")

    assert "auto_close_failed_opens.py --apply" in wf, \
        "the closer is no longer armed -- if that is deliberate, update this test to say so"
    # The capture must run FIRST, or there is no metrics row for today and every position reads
    # unjudgeable: armed but permanently inert, which is this repository's signature defect.
    assert wf.index("closing_bar_capture.py") < wf.index("auto_close_failed_opens.py"), \
        "the break bar must be captured before it is judged"
    # And the fill reconcile before BOTH. Without it the judging step cannot read the setup an order was
    # placed from, so R:R reads absent and the position is skipped as unjudgeable -- which is exactly how
    # the closer spent its first four armed days closing nothing (measured 2026-09-11).
    assert "reconcile_fills.py --apply" in wf, "fills must be reconciled or nothing can be judged"
    assert wf.index("reconcile_fills.py") < wf.index("auto_close_failed_opens.py"), \
        "fills must be reconciled before the positions they produced are judged"
    # A capture failure must not stop the judging step from acting on rows captured on an earlier pass.
    assert "continue-on-error: true" in wf


def test_arming_does_not_bypass_any_of_the_guards():
    """--apply changes ONE thing: whether close_trade is called. Everything that decides WHETHER to close
    is upstream of it and is unaffected."""
    import inspect
    src = inspect.getsource(ac.run)

    assert "if not summary[\"enabled\"]" in src, "the per-user switch must still gate the close"
    assert "MAX_PER_RUN" in src, "the per-pass cap must still apply"
    assert "is_tradeable_now" in src, "IG's own dealability check must still gate the close"
    # The apply check must come AFTER the guards, or the flag would skip them.
    assert src.index("MAX_PER_RUN") < src.index("if not apply"), \
        "the run cap must be evaluated before the dry-run short-circuit"


# ------------------------------------------------------------------------------------------------------
# Which unknowns are allowed to block a close (2026-09-11)
# ------------------------------------------------------------------------------------------------------

def test_a_missing_durable_measure_does_not_block_a_volume_close(monkeypatch):
    """THE BUG THIS PREVENTS, and it had disabled the closer completely.

    `if row["unknown"]` blocked on ANY unknown, so a missing R:R -- a DURABLE measure this mechanism never
    acts on -- kept a position open that had failed its volume tests on a stored break bar. Measured
    2026-09-11: that was 2 of the 2 positions the closer has ever been able to judge. This row is SYY as
    it actually stood at 19:31 UTC on 2026-09-09, inside its closing window, kept open with
    "unjudgeable: R:R not recorded".
    """
    _audit(monkeypatch, [{"ticker": "SYY", "deal_id": "D-SYY",
                          "breaches": ["ATR expanding required but not met"],
                          "unknown": ["R:R not recorded"]}])

    to_close, skipped = ac.candidates("Alex", "2026-09-09", [_pos("SYY", "2026-09-09")],
                                      require_window=False)

    assert [r["ticker"] for r in to_close] == ["SYY"], \
        "a durable unknown says nothing about whether the volume tests failed"
    assert skipped == []


def test_a_missing_break_bar_measure_still_blocks_the_close(monkeypatch):
    """The other half of the same rule. An unknown RVOL is the break bar itself being unavailable, and
    closing a real position on missing volume data is the mistake that costs money."""
    _audit(monkeypatch, [{"ticker": "PARTIAL", "deal_id": "D-PARTIAL",
                          "breaches": ["ATR expanding required but not met"],
                          "unknown": ["RVOL not recorded", "R:R not recorded"]}])

    to_close, skipped = ac.candidates("Alex", "2026-09-04", [_pos("PARTIAL", "2026-09-04")],
                                      require_window=False)

    assert to_close == []
    assert "RVOL not recorded" in skipped[0]["why_skipped"]
    assert "R:R not recorded" not in skipped[0]["why_skipped"], \
        "the reason must name what actually blocked it, not everything that happened to be absent"


def test_a_durable_unknown_is_recorded_against_a_position_it_did_not_block(monkeypatch):
    """The evidence table has to say what was NOT known at the moment a real position was closed."""
    _audit(monkeypatch, [{"ticker": "SYY", "deal_id": "D-SYY",
                          "breaches": ["ATR expanding required but not met"],
                          "unknown": ["R:R not recorded"]}])

    to_close, _ = ac.candidates("Alex", "2026-09-09", [_pos("SYY", "2026-09-09")], require_window=False)

    assert "R:R not recorded" in to_close[0]["durable_breaches"]


def test_the_break_bar_label_list_has_exactly_one_definition():
    """Two copies of this list is how the closer and the order audit end up disagreeing about which
    measures are durable -- which is the disagreement that produced the bug above."""
    import order_filter_audit

    assert ac.VOLUME_TESTS is order_filter_audit.BREAK_BAR_LABELS, \
        "VOLUME_TESTS must BE the audit's list, not a copy that matches it today"


# ------------------------------------------------------------------------------------------------------
# A REFUSED CLOSE MUST BE COUNTED AND MUST GO RED (2026-09-25)
#
# WHY THESE EXIST. MEASURED 2026-09-25 from the live DB: all 7 closes the auto-closer has ever attempted
# were rejected by IG with the identical HTTP 400 validation.mutual-exclusive-value.request, and ZERO have
# ever succeeded. The rejections were invisible -- `closed` counted only successes, `skipped` is set from
# the candidate filter before any close is attempted, so a refused close incremented NOTHING and the pass
# returned {'to_close': 1, 'closed': 0, 'skipped': 0} and exited 0. All 96 Closing Window runs on
# 2026-09-24 reported success while the mechanism was totally broken. The tests above prove the module
# does not close the WRONG thing; these prove it admits when it closed NOTHING.
# ------------------------------------------------------------------------------------------------------

def test_the_exit_code_is_zero_only_when_nothing_was_refused():
    assert ac.exit_code({"closed": 1, "failed": 0}) == 0
    assert ac.exit_code({"to_close": 0, "closed": 0, "failed": 0}) == 0, "a quiet pass is not a failure"


def test_a_refused_close_exits_non_zero():
    """THE DVN CASE. to_close 1, closed 0 -- the rule said close, IG refused, the position is still open."""
    assert ac.exit_code({"to_close": 1, "closed": 0, "failed": 1}) == 1


def test_a_pass_that_died_exits_non_zero():
    assert ac.exit_code({"closed": 0, "failed": 0, "error": "boom"}) == 1


def test_a_position_whose_intent_could_not_be_recorded_exits_non_zero():
    """Recording the intent is what stops a position being closed with no audit trail, so failing to
    record it is a failure of the mechanism, not a quiet skip."""
    assert ac.exit_code({"closed": 0, "failed": 0, "not_recorded": 1}) == 1


def test_the_safety_gates_are_not_failures():
    """A position that closed itself before we got there, and a market IG will not deal, are the gates
    working. Treating them as failures would make the step red every holiday and train it to be ignored --
    the same noise defect fixed in the cron watcher the same day."""
    assert ac.exit_code({"to_close": 2, "closed": 0, "failed": 0, "gone": 1, "not_tradeable": 1}) == 0


def _drive_run(monkeypatch, close_returns, outcome_detail=""):
    """Run the real run() with apply=True against a single candidate, mocking only the outside world.

    Returns the summary. The point is to drive the BROKEN state -- a close that IG refuses -- because a
    check only ever exercised against a healthy system has not been shown to detect anything (this
    module's own words, auto_close_failed_opens.py:100-101).
    """
    import contextlib
    import threading
    import ig_shim
    import market_hours
    import db_pool
    from hvf_web import web_users as _wu

    deal = "DIAAAAR7H4R6QAQ"
    epic = "SB.D.DVN.DAILY.IP"
    recorded = []

    monkeypatch.setattr(_wu, "get_settings", lambda u: {"limits": {ac.SETTING: "1"}})
    monkeypatch.setattr(ig_shim, "session_for", lambda u: object())
    monkeypatch.setattr(ig_shim, "_IG_LOCK", threading.RLock(), raising=False)
    monkeypatch.setattr(ig_shim, "acting_session",
                        lambda u=None: contextlib.nullcontext(), raising=False)
    monkeypatch.setattr(ig_shim, "get_open_positions", lambda: [{
        "market": {"epic": epic, "instrumentName": "Devon Energy"},
        "position": {"dealId": deal, "direction": "BUY", "size": 0.04,
                     "createdDateUTC": "2026-09-24T14:00:00", "level": 30.0, "currency": "USD"}}])
    monkeypatch.setattr(ig_shim, "close_trade", lambda d, reason=None: close_returns)
    monkeypatch.setattr(ig_shim, "last_close_outcome", lambda: outcome_detail, raising=False)
    monkeypatch.setattr(market_hours, "is_tradeable_now", lambda e: True)

    class _DB:
        def run(self, sql, **kw):
            return [["DVN", epic]] if "epic_lookup" in sql else []

        def close(self):
            pass

    monkeypatch.setattr(db_pool, "get_db", lambda *a, **k: _DB())
    monkeypatch.setattr(ac, "ensure_schema", lambda db: None)
    monkeypatch.setattr(ac, "record",
                        lambda db, u, r, p, c, outcome: recorded.append(outcome))
    # The candidate filter and its window gate are covered by the tests above; this drives what happens
    # AFTER a candidate is chosen, so it is supplied directly.
    monkeypatch.setattr(ac, "candidates", lambda user, on_date, positions, **k: (
        [{"ticker": "DVN", "deal_id": deal, "epic": epic, "direction": "BUY", "size": 0.04,
          "volume_breaches": ["VolumeScore 3.0 < 4.0"]}], []))

    summary = ac.run(user="owner", on_date="2026-09-24", apply=True)
    return summary, recorded


def test_a_close_that_ig_refuses_is_counted_as_failed_not_as_nothing(monkeypatch):
    """REPRODUCES 2026-09-24 EXACTLY. close_trade returns False on an HTTP error -- it logs and returns,
    it does not raise -- so the old `if ok:` with no else left every counter at zero."""
    summary, recorded = _drive_run(monkeypatch, close_returns=False,
                                   outcome_detail={"closed": False, "reason": "IG HTTP 400"})
    assert summary["to_close"] == 1
    assert summary["closed"] == 0
    assert summary["failed"] == 1, "a refused close must be counted somewhere"
    assert summary["failures"] and "DVN" in summary["failures"][0]
    assert ac.exit_code(summary) == 1, "the run must go red"
    assert any("not_confirmed" in str(o) for o in recorded), "the outcome must reach the audit table"


def test_a_close_that_succeeds_is_counted_and_exits_zero(monkeypatch):
    """The other half: once the payload is fixed, a real close must report green and count as closed."""
    summary, _ = _drive_run(monkeypatch, close_returns=True)
    assert summary["closed"] == 1
    assert summary["failed"] == 0
    assert ac.exit_code(summary) == 0


def test_every_candidate_lands_in_exactly_one_bucket(monkeypatch):
    """THE INVARIANT. to_close must reconcile against what actually happened to each candidate, so no
    future branch can silently drop one the way the rejection path did."""
    for returns in (True, False):
        summary, _ = _drive_run(monkeypatch, close_returns=returns)
        accounted = (summary["closed"] + summary["failed"] + summary["gone"]
                     + summary["not_tradeable"] + summary["not_recorded"])
        assert accounted == summary["to_close"], f"unaccounted candidate with close_trade={returns}"
