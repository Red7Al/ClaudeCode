#!/usr/bin/env python3
# ======================================================================================================================
# File:         test_reconcile_fills.py
# Created:      2026-09-11
#
# The matching rule is the whole risk in this job. A wrong fill attribution writes the wrong setup's R:R into
# working_orders, and auto_close_failed_opens then judges a real position on it. Every test here is about refusing to
# guess: where the evidence is ambiguous the row must stay PENDING, because leaving it alone costs nothing and
# attributing it wrongly feeds a live close decision.
# ======================================================================================================================

import inspect

import reconcile_fills as rf


def _order(ticker, deal="O-1", epic="E1", direction="BUY", size=0.04, placed="2026-09-03"):
    return {"deal_id": deal, "ticker": ticker, "epic": epic, "direction": direction,
            "size": size, "placed_at": placed}


def _position(deal="P-1", epic="E1", direction="BUY", size=0.04, created="2026-09-09"):
    return {"deal_id": deal, "epic": epic, "direction": direction, "size": size, "created": created}


# ------------------------------------------------------------------------------------------------------
# The match it is supposed to make
# ------------------------------------------------------------------------------------------------------

def test_an_unambiguous_fill_is_matched():
    """SYY as it actually stood on 2026-09-11: order placed 2026-09-03, position opened 2026-09-09, still
    PENDING in working_orders days later, which is what left its R:R unreadable."""
    matched, unmatched = rf.pair_fills([_order("SYY")], [_position()])

    assert unmatched == []
    assert matched[0]["ticker"] == "SYY"
    assert matched[0]["fill_deal_id"] == "P-1"
    assert matched[0]["filled_at"] == "2026-09-09", "IG's own timestamp, not the time this job noticed"


def test_a_fill_slightly_smaller_than_the_order_is_still_the_same_fill():
    matched, _ = rf.pair_fills([_order("X", size=0.04)], [_position(size=0.039)])

    assert len(matched) == 1


# ------------------------------------------------------------------------------------------------------
# Everything it must refuse
# ------------------------------------------------------------------------------------------------------

def test_a_position_that_predates_the_order_is_never_its_fill():
    """THE BUG THIS PREVENTS. The owner holds several positions per epic over time; without the date test
    an order placed today would be matched to a position opened weeks ago and inherit its setup."""
    matched, unmatched = rf.pair_fills([_order("X", placed="2026-09-03")],
                                       [_position(created="2026-08-06")])

    assert matched == []
    assert "no open position matches it" in unmatched[0]["why"]


def test_two_candidate_positions_leave_the_order_pending():
    matched, unmatched = rf.pair_fills([_order("X")], [_position(deal="P-1"), _position(deal="P-2")])

    assert matched == []
    assert "2 open positions match it" in unmatched[0]["why"]


def test_two_orders_wanting_the_same_position_are_both_left_pending():
    """Neither can be attributed, so neither is. Picking one would be a guess with a live consequence."""
    matched, unmatched = rf.pair_fills([_order("X", deal="O-1"), _order("Y", deal="O-2")],
                                       [_position()])

    assert matched == []
    assert len(unmatched) == 2
    assert all("another pending order" in u["why"] for u in unmatched)


def test_a_position_already_claimed_by_another_row_is_not_reused():
    matched, unmatched = rf.pair_fills([_order("X")], [_position(deal="P-1")], claimed=["P-1"])

    assert matched == []
    assert "no open position matches it" in unmatched[0]["why"]


def test_direction_and_epic_must_both_agree():
    assert rf.pair_fills([_order("X")], [_position(direction="SELL")])[0] == []
    assert rf.pair_fills([_order("X")], [_position(epic="E2")])[0] == []


def test_a_size_that_is_not_close_is_not_a_fill():
    assert rf.pair_fills([_order("X", size=0.04)], [_position(size=0.10)])[0] == []


# ------------------------------------------------------------------------------------------------------
# The boundaries of the job itself -- what the account owner agreed to on 2026-09-11
# ------------------------------------------------------------------------------------------------------

def test_it_never_places_cancels_or_amends_anything_at_ig():
    """The whole reason this exists instead of scheduling ig_shim.reconcile_working_orders, which promotes
    WATCHING rows into live IG orders and deletes out-of-band ones. If this job ever grows one of those
    calls it has stopped being the thing that was approved.

    Parsed, not grepped. The first version of this test searched the source text and failed on the module
    header, which merely NAMES the calls it is explaining the absence of -- a test that cannot tell an
    explanation from a call would eventually be silenced rather than believed.
    """
    import ast

    tree = ast.parse(inspect.getsource(rf))
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for forbidden in ("place_working_order", "delete_working_order", "close_trade", "open_trade",
                      "update_stop", "_promote_watching_order", "trade_opened", "send_trade_email"):
        assert forbidden not in called, f"{forbidden} must never be reachable from this job"
    # The only IG calls permitted are the two reads. get_db is the database connection, not IG.
    allowed = {"get_working_orders", "get_open_positions", "session_for", "acting_session", "get_db"}
    assert {c for c in called if c.startswith("get_") or c.endswith("_order")} <= allowed


def test_it_only_ever_moves_a_row_from_pending_to_filled():
    """It must not mark anything CANCELLED or EXPIRED: clearing the phantom rows is a separate decision."""
    src = inspect.getsource(rf)

    assert "'CANCELLED'" not in src and "'EXPIRED'" not in src
    assert "status = 'PENDING'" in inspect.getsource(rf._mark_filled), \
        "the update must be conditional on the row still being PENDING"


def test_a_dry_run_writes_nothing():
    src = inspect.getsource(rf.run)

    assert src.index("if not apply") < src.index("_mark_filled"), \
        "the dry-run short-circuit must come before any write"
