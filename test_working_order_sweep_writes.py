#!/usr/bin/env python3
# The sweep reported "marked 4 row(s) EXPIRED" while writing nothing (2026-09-11): it built its id list
# with str(deal_id), four rows carried NULL, and it searched for the literal string 'None'. It then logged
# the size of its own to-do list rather than what changed, so the failure was invisible.

import inspect

import run_working_order_sweep as sw


def test_the_update_targets_the_primary_key():
    src = inspect.getsource(sw.sweep)

    assert 'int(rec["id"])' in src, "rows must be identified by id; deal_id is nullable"
    assert "where id = any(:ids)" in src
    assert "where deal_id = any(:ids)" not in src


def test_it_counts_what_changed_rather_than_what_it_intended():
    src = inspect.getsource(sw.sweep)

    assert "select count(*) from working_orders where id = any(:ids) and status = 'EXPIRED'" in src, \
        "the report must be a re-read, not the length of the to-do list"
    assert "if done != len(stale)" in src, "a partial write must be an error, not a success line"


def test_the_row_is_selected_with_its_id():
    src = inspect.getsource(sw.sweep)

    assert "select id, deal_id" in src
