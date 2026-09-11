#!/usr/bin/env python3
# ======================================================================================================================
# File:         test_working_order_state.py
# Created:      2026-09-11
#
# The two failures this module exists to make impossible, both of which really happened on 2026-09-04:
# a FILLED order read as dead (12 rows, restored by hand), and a live WATCHING row read as dead (19 rows).
# Both came from the same test -- "not at IG means dead" -- so both are pinned here.
# ======================================================================================================================

import datetime as dt

import working_order_state as wos

NOW = dt.datetime(2026, 9, 11, 12, 0, tzinfo=dt.timezone.utc)
FUTURE = dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc)
PAST = dt.datetime(2026, 7, 7, tzinfo=dt.timezone.utc)


def _row(deal_id="O-1", status="PENDING", epic="E1", direction="BUY", size=0.04,
         placed="2026-09-03", good_till=FUTURE):
    return {"deal_id": deal_id, "status": status, "epic": epic, "direction": direction,
            "size": size, "placed_at": placed, "good_till": good_till}


def _pos(deal_id="P-1", epic="E1", direction="BUY", size=0.04, created="2026-09-09"):
    return {"deal_id": deal_id, "epic": epic, "direction": direction, "size": size, "created": created}


def _state(rows, ig_ids=(), positions=(), claimed=()):
    return wos.classify(rows, set(ig_ids), list(positions), now=NOW, claimed=claimed)


# ------------------------------------------------------------------------------------------------------
# The two real incidents
# ------------------------------------------------------------------------------------------------------

def test_a_filled_order_is_not_dead():
    """2026-09-04: the sweep expired 12 filled orders because a fill is also absent from IG's book.
    The note it left said it plainly -- "the sweep could not tell a fill from an expiry"."""
    st = _state([_row()], ig_ids=(), positions=[_pos()])

    assert st["O-1"][0] == wos.FILLED
    assert st["O-1"][1]["deal_id"] == "P-1"


def test_a_live_watching_row_is_not_dead():
    """2026-09-04: 19 live WATCHING rows were expired, some with good-till three weeks out. A WATCHING row
    carries a synthetic WATCH-... id, so it can NEVER appear in an IG list -- absence proves nothing."""
    st = _state([_row(deal_id="WATCH-SYF-1788796932", status="WATCHING", good_till=FUTURE)])

    assert st["WATCH-SYF-1788796932"][0] == wos.WATCHING


def test_a_watching_row_past_its_good_till_is_dead():
    """The one way a watching row can legitimately die: it ran out of time without ever being placed."""
    st = _state([_row(deal_id="WATCH-X-1", status="WATCHING", good_till=PAST)])

    assert st["WATCH-X-1"][0] == wos.DEAD


# ------------------------------------------------------------------------------------------------------
# The remaining states
# ------------------------------------------------------------------------------------------------------

def test_an_order_ig_still_holds_is_live():
    assert _state([_row()], ig_ids={"O-1"}, positions=[_pos()])["O-1"][0] == wos.LIVE


def test_an_order_gone_from_ig_with_no_position_is_dead():
    assert _state([_row()], ig_ids=(), positions=[])["O-1"][0] == wos.DEAD


def test_a_row_with_no_deal_id_is_never_dead_while_its_time_remains():
    """Nothing can be asked of IG about it, so absence is meaningless. DEAD needs positive evidence."""
    assert _state([_row(deal_id=None, good_till=FUTURE)])[""][0] == wos.LIVE
    assert _state([_row(deal_id=None, good_till=PAST)])[""][0] == wos.DEAD


# ------------------------------------------------------------------------------------------------------
# Refusing to guess
# ------------------------------------------------------------------------------------------------------

def test_two_candidate_positions_leave_the_row_alone():
    st = _state([_row()], positions=[_pos(deal_id="P-1"), _pos(deal_id="P-2")])

    assert st["O-1"][0] == wos.LIVE, "ambiguous must never resolve to FILLED or DEAD"


def test_two_rows_wanting_the_same_position_are_both_left_alone():
    st = _state([_row(deal_id="O-1"), _row(deal_id="O-2")], positions=[_pos()])

    assert st["O-1"][0] == wos.LIVE and st["O-2"][0] == wos.LIVE


def test_a_position_older_than_the_order_is_not_its_fill():
    st = _state([_row(placed="2026-09-03")], positions=[_pos(created="2026-08-06")])

    assert st["O-1"][0] == wos.DEAD


def test_a_claimed_position_is_not_reused():
    st = _state([_row()], positions=[_pos(deal_id="P-1")], claimed={"P-1"})

    assert st["O-1"][0] == wos.DEAD


def test_epic_direction_and_size_must_all_agree():
    assert _state([_row()], positions=[_pos(epic="E2")])["O-1"][0] == wos.DEAD
    assert _state([_row()], positions=[_pos(direction="SELL")])["O-1"][0] == wos.DEAD
    assert _state([_row()], positions=[_pos(size=0.10)])["O-1"][0] == wos.DEAD


def test_a_fill_slightly_smaller_than_the_order_still_matches():
    assert _state([_row(size=0.04)], positions=[_pos(size=0.039)])["O-1"][0] == wos.FILLED


# ------------------------------------------------------------------------------------------------------
# The shapes the two callers actually pass
# ------------------------------------------------------------------------------------------------------

def test_a_date_object_and_an_iso_string_compare_correctly():
    """The sweep reads placed_at::date (a date); IG returns an ISO string. Comparing them raises TypeError,
    so the conversion lives in the module rather than in whichever caller remembers it."""
    st = _state([_row(placed=dt.date(2026, 9, 3))], positions=[_pos(created="2026-09-09")])

    assert st["O-1"][0] == wos.FILLED


def test_a_paper_order_is_never_matched_against_a_real_position():
    st = _state([_row(deal_id="PAPER-1")], positions=[_pos()])

    assert st["PAPER-1"][0] == wos.LIVE


# ------------------------------------------------------------------------------------------------------
# Both consumers must actually be scheduled (this repository's signature defect is code nothing calls)
# ------------------------------------------------------------------------------------------------------

def test_the_fill_reconcile_runs_before_the_closer_judges():
    import pathlib
    wf = pathlib.Path(".github/workflows/trading-closing-window.yml").read_text(encoding="utf-8")

    assert "reconcile_fills.py --apply" in wf
    assert wf.index("reconcile_fills.py") < wf.index("auto_close_failed_opens.py"), \
        "a position must be reconciled before it is judged, or its setup cannot be read"


def test_the_sweep_runs_before_the_bridge_builds_its_skip_list():
    """The sweep exists to keep _already_working() honest. Scheduled anywhere else it would drift from
    the thing that needs it; unscheduled -- which it was until 2026-09-11 -- it does nothing at all."""
    import pathlib
    wf = pathlib.Path(".github/workflows/trading-order-bridge.yml").read_text(encoding="utf-8")

    assert "run_working_order_sweep.py --apply" in wf, "the sweep is not scheduled anywhere"
    assert wf.index("run_working_order_sweep.py") < wf.index("hvf_web.order_bridge"), \
        "the skip-list is built from the table the sweep corrects, so the sweep must run first"


def test_both_consumers_ask_working_order_state_rather_than_deciding():
    """The whole point. If either grows its own interpretation of 'absent from IG', the contradiction is
    back -- that is how one column came to have four meanings."""
    import pathlib
    for name in ("reconcile_fills.py", "run_working_order_sweep.py"):
        src = pathlib.Path(name).read_text(encoding="utf-8")
        assert "working_order_state" in src, f"{name} must defer to the shared classifier"
