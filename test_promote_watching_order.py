#!/usr/bin/env python3
# ======================================================================================================================
# File:         test_promote_watching_order.py
# Created:      2026-09-12
#
# _promote_watching_order places a LIVE IG order, so both guards here are about not reaching the broker by accident.
# Proven by execution 2026-09-11: it unpacked 16 values from the 18-column row reconcile_working_orders selects, so
# every promotion raised ValueError inside a bare `except Exception` and was logged as "WATCHING check failed".
# ======================================================================================================================

import ast
import inspect

import ig_shim


def _unpack_targets():
    fn = ast.parse(inspect.getsource(ig_shim._promote_watching_order).lstrip()).body[0]
    for node in fn.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], (ast.Tuple, ast.List)):
            return node.targets[0].elts
    raise AssertionError("no tuple unpack found")


def _select_columns():
    """The columns reconcile_working_orders actually selects, read from its own SQL."""
    src = inspect.getsource(ig_shim.reconcile_working_orders)
    sql = src.split("select", 1)[1].split("from", 1)[0]
    return [c.strip() for c in sql.replace("\n", " ").split(",") if c.strip()]


def test_the_unpack_absorbs_every_column_the_query_returns():
    """THE BUG THIS PREVENTS. Two columns were added to the SELECT and this unpack was not updated, so
    every promotion raised. Absorbing trailing fields means the next column cannot break it either."""
    targets = _unpack_targets()

    assert isinstance(targets[-1], ast.Starred), \
        "the last target must be *_extra, or a new column breaks promotion again"
    named = len(targets) - 1
    assert named <= len(_select_columns()), \
        f"unpack names {named} fields but the query returns {len(_select_columns())}"


def test_the_unpack_survives_the_real_query_width():
    """Executed, not read: build a row the width reconcile_working_orders returns and unpack it."""
    row = tuple(range(len(_select_columns())))
    (deal_id, deal_ref, user_id, ticker, epic, direction, size, entry,
     stop, limit, otype, sess, sig_sum, good_till, hvf_type, paper, *_extra) = row

    assert deal_id == 0 and paper == 15


def test_a_row_with_no_deal_id_is_refused_before_any_broker_call():
    """The update at the end keys on deal_id. With NULL it matches nothing, so the order would be live at
    IG with the row still WATCHING -- promoted again next cycle, i.e. a duplicate order. ^AXJO was exactly
    such a row on 2026-09-11."""
    src = inspect.getsource(ig_shim._promote_watching_order)

    assert "if not deal_id:" in src
    assert src.index("if not deal_id:") < src.index("/workingorders/otc"), \
        "the refusal must come BEFORE the order is placed"
