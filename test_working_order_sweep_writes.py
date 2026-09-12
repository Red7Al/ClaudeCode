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
    """The read moved to open_rows() on 2026-09-12 so a test could call the REAL query instead of a
    copy of it (the tenant tests re-wrote this SQL inline and a deleted owner filter stayed green).
    Asserted on the shape the caller relies on rather than on the text: sweep() indexes r[0] as the
    primary key and r[9] as the owner, so the column order is load-bearing."""
    src = inspect.getsource(sw.open_rows)
    assert "select id, deal_id" in src
    assert "user_id = any(:ids)" in src, "the sweep must only ever judge one account's rows"

    # The contract sweep() depends on: id first, user_id last. A live_state test proves the filter
    # actually filters; this proves the positions the caller reads by index have not shifted.
    cols = [c.strip() for c in src.split("select ", 1)[1].split("from ", 1)[0].replace('"', "")
            .replace("\n", " ").split(",")]
    assert cols[0] == "id"
    assert cols[-1] == "user_id"
