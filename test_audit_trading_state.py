#!/usr/bin/env python3
# ======================================================================================================================
# File:         test_audit_trading_state.py
# Created:      2026-09-12
#
# Every check here is driven against the BROKEN state it exists to catch, and against the healthy one, because a
# check that has only run against a healthy system has not been shown to detect anything -- which is how 300 green
# closer runs, a sweep that wrote nothing, and a two-week publication outage all went unnoticed.
#
# FIXTURES COME FROM THE TABLE, NOT FROM MEMORY. Profiled 2026-09-12 before these were written: working_orders has
# 482 rows, deal_id is non-null on only 265 (45% NULL) and good_till on 294 (39% NULL); id is unique and never null;
# epic, direction, size and placed_at are never null; six distinct statuses. Inventing fixtures where every deal_id
# was populated is exactly what let a dict keyed on deal_id pass a full test suite the day before.
# ======================================================================================================================

import datetime as dt

import audit_trading_state as ats

NOW = dt.datetime(2026, 9, 12, 21, 0, tzinfo=dt.timezone.utc)
FUTURE = dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc)


def _rec(ticker="SYY", deal_id="O-1", status="PENDING", good_till=FUTURE, placed="2026-09-03"):
    return {"id": 1, "deal_id": deal_id, "ticker": ticker, "status": status, "placed_at": placed,
            "good_till": good_till, "epic": "E1", "direction": "BUY", "size": 0.04}


def _pos(ticker="SYY", deal_id="P-1", opened="2026-09-12"):
    return {"ticker": ticker, "deal_id": deal_id, "name": ticker, "epic": "E1", "opened": opened,
            "direction": "BUY", "size": 0.04, "created": opened}


# ------------------------------------------------------------------------------------------------------
# 1. Status contradictions
# ------------------------------------------------------------------------------------------------------

def test_a_filled_position_still_marked_pending_is_reported():
    """Exactly the state the book was in on 2026-09-11: SYY filled on the 9th, still PENDING two days later,
    which is what made its R:R unreadable and left the closer inert."""
    found = ats.check_status_contradictions([_rec()], live_order_ids=(), positions=[_pos()])

    assert len(found) == 1
    assert "fill reconcile has not run" in found[0]


def test_a_dead_row_still_marked_pending_is_reported():
    found = ats.check_status_contradictions([_rec(ticker="DGE.L", good_till=None)],
                                            live_order_ids=(), positions=[])

    assert len(found) == 1 and "sweep has not run" in found[0]


def test_a_healthy_book_reports_nothing():
    live = ats.check_status_contradictions([_rec()], live_order_ids={"O-1"}, positions=[])
    watching = ats.check_status_contradictions([_rec(deal_id="WATCH-SYF-1", status="WATCHING")],
                                               live_order_ids=(), positions=[])

    assert live == [] and watching == []


# ------------------------------------------------------------------------------------------------------
# 2. Unresolvable rows
# ------------------------------------------------------------------------------------------------------

def test_a_row_with_neither_deal_id_nor_good_till_is_reported():
    """It can never become DEAD -- nothing to ask IG about, no clock to run out -- so it would hold the
    bridge off that instrument for ever. None exist today, which is when to add the check."""
    found = ats.check_unresolvable_rows([_rec(ticker="GHOST", deal_id=None, good_till=None)])

    assert len(found) == 1 and "can never be resolved" in found[0]


def test_a_row_with_a_good_till_but_no_deal_id_is_not_reported():
    assert ats.check_unresolvable_rows([_rec(deal_id=None, good_till=FUTURE)]) == []


# ------------------------------------------------------------------------------------------------------
# 3. Should-have-closed -- the liveness check
# ------------------------------------------------------------------------------------------------------

def _audit(monkeypatch, rows):
    import order_filter_audit

    def _fake(user, positions, **k):
        wanted = {p["ticker"] for p in positions}
        return {"rows": [r for r in rows if r["ticker"] in wanted]}

    monkeypatch.setattr(order_filter_audit, "audit_positions", _fake)


def _closed_at(monkeypatch, when):
    import market_hours
    monkeypatch.setattr(market_hours, "close_utc", lambda tk, day: when)


def test_a_position_that_failed_and_is_still_open_after_its_close_is_reported(monkeypatch):
    """THE ONE THAT MATTERS. This is 0700.HK and SYY: break bar failing, exchange shut, position open,
    closer silent. Four days passed before anyone noticed, by hand."""
    _closed_at(monkeypatch, dt.datetime(2026, 9, 12, 20, 0, tzinfo=dt.timezone.utc))
    _audit(monkeypatch, [{"ticker": "SYY", "breaches": ["ATR expanding required but not met"], "unknown": []}])

    found = ats.check_should_have_closed("Alex", "2026-09-12", [_pos()], now=NOW)

    assert len(found) == 1
    assert "still open — the closer did not act" in found[0]


def test_nothing_is_reported_before_the_exchange_has_closed(monkeypatch):
    """Mid-session there is no miss yet -- the closer's window may not have arrived."""
    _closed_at(monkeypatch, dt.datetime(2026, 9, 12, 23, 0, tzinfo=dt.timezone.utc))
    _audit(monkeypatch, [{"ticker": "SYY", "breaches": ["ATR expanding required but not met"], "unknown": []}])

    assert ats.check_should_have_closed("Alex", "2026-09-12", [_pos()], now=NOW) == []


def test_a_position_with_no_stored_break_bar_at_all_is_not_reported(monkeypatch):
    """Unjudgeable is the closer behaving correctly, not a miss. MRO.L and VOD.L are in this state."""
    _closed_at(monkeypatch, dt.datetime(2026, 9, 12, 20, 0, tzinfo=dt.timezone.utc))
    _audit(monkeypatch, [{"ticker": "SYY", "breaches": [],
                          "unknown": ["RVOL not recorded", "no stored metrics for the day it opened"]}])

    assert ats.check_should_have_closed("Alex", "2026-09-12", [_pos()], now=NOW) == []


def test_a_failing_test_alongside_a_missing_one_is_not_reported(monkeypatch):
    """The case the previous test does NOT reach: a real volume breach AND a missing break-bar measure.
    The closer leaves this open because it cannot judge the full rule, so it is not a miss -- and only a
    fixture carrying BOTH exercises the `not blocking` half of the condition. Mutation testing found that
    the earlier fixture, having no breach at all, proved nothing about it."""
    _closed_at(monkeypatch, dt.datetime(2026, 9, 12, 20, 0, tzinfo=dt.timezone.utc))
    _audit(monkeypatch, [{"ticker": "SYY", "breaches": ["RVOL 0.4 < 1.4"],
                          "unknown": ["above VWAP not recorded"]}])

    assert ats.check_should_have_closed("Alex", "2026-09-12", [_pos()], now=NOW) == []


def test_a_position_that_passed_is_not_reported(monkeypatch):
    _closed_at(monkeypatch, dt.datetime(2026, 9, 12, 20, 0, tzinfo=dt.timezone.utc))
    _audit(monkeypatch, [{"ticker": "SYY", "breaches": ["R:R 3.0 < 5.0"], "unknown": []}])

    assert ats.check_should_have_closed("Alex", "2026-09-12", [_pos()], now=NOW) == [], \
        "a durable breach is not this mechanism's business"


def test_a_position_opened_on_another_day_is_out_of_scope(monkeypatch):
    """Scoped to one day so a miss is reported once rather than re-reported for ever."""
    _closed_at(monkeypatch, dt.datetime(2026, 9, 12, 20, 0, tzinfo=dt.timezone.utc))
    _audit(monkeypatch, [{"ticker": "SYY", "breaches": ["RVOL 0.4 < 1.4"], "unknown": []}])

    assert ats.check_should_have_closed("Alex", "2026-09-12", [_pos(opened="2026-09-07")], now=NOW) == []


# ------------------------------------------------------------------------------------------------------
# A broken check must be loud, never silently empty
# ------------------------------------------------------------------------------------------------------

def test_the_audit_failing_outright_is_reported_not_swallowed(monkeypatch):
    """The failure mode this whole file exists to prevent: something goes wrong and the audit says 'all
    clear'."""
    monkeypatch.setattr(ats, "_positions_from_ig", lambda user: (_ for _ in ()).throw(RuntimeError("IG down")))

    res = ats.run(on_date="2026-09-12", user="Alex", alert=False)

    assert res["findings"] and "AUDIT FAILED" in res["findings"][0]


def test_ONE_check_raising_does_not_make_the_others_report_all_clear(monkeypatch):
    """The previous test kills the audit before any check runs, so it says nothing about the per-check
    handler -- mutation testing showed that swallowing a check exception went undetected. Here the data
    loads fine and a single check throws: that check must produce a finding, not an empty list."""
    monkeypatch.setattr(ats, "_positions_from_ig", lambda user: [_pos()])
    monkeypatch.setattr(ats, "open_working_rows", lambda: [])
    monkeypatch.setattr(ats, "check_unresolvable_rows",
                        lambda recs: (_ for _ in ()).throw(RuntimeError("column vanished")))
    import ig_shim
    monkeypatch.setattr(ig_shim, "get_working_orders", lambda: [])

    res = ats.run(on_date="2026-09-12", user="Alex", alert=False)

    assert any("CHECK FAILED (unresolvable rows)" in f for f in res["findings"]), \
        f"a check that threw must be a finding, got {res['findings']}"


def test_findings_make_the_run_exit_non_zero():
    """Posting is the signal, but the run must go red too -- a silent green tick is what hid all of this."""
    import inspect
    src = inspect.getsource(ats)

    assert 'sys.exit(1 if res["findings"] else 0)' in src
