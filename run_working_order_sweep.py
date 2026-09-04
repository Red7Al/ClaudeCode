#!/usr/bin/env python3
# ======================================================================================================================
# File:         run_working_order_sweep.py
# Author:       Alex Hind
# Created:      2026-09-03
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Marks working_orders rows that IG is no longer holding as EXPIRED, so the Order Bridge stops treating them as live.
#
# WHY. The bridge builds a skip-list from every working_orders row with status PENDING or WATCHING and silently
# `continue`s past any candidate on that list -- no log line, no record. Measured 2026-09-03: the skip-list held 95
# tickers while IG was holding SIX working orders, so 89 were phantom, and six of that day's 29 bridge candidates
# (CDNS, CVSG.L, NKE, PHP.L, SCHO, SMIN.L) were being skipped because of an order that does not exist. That is lost
# trading, not a reporting fault.
#
# The rows went stale because ig_shim.reconcile_working_orders() -- which is what clears them -- is called from
# intraday_signals.py and run_session.py only, and both session monitors are switched off. Nothing has reconciled
# working orders since.
#
# WHY THIS IS NOT reconcile_working_orders(). That function is the right long-term answer and should be scheduled, but
# it also CANCELS live orders at IG when price has moved more than WO_CANCEL_BAND_PCT from entry (ig_shim ~2961). This
# script deliberately does less: it reads IG to learn which orders are real, and then writes ONLY to our own database.
# It never sends anything to the broker. That makes it safe to run at any time to unblock the bridge, and keeps the
# decision to cancel a real order separate and explicit.
#
# SAFETY.
#   * Read-only against IG. The single IG call is get_working_orders().
#   * Refuses to sweep if IG cannot be read -- an empty read must never be taken as "nothing is live".
#   * Dry run by default. --apply is required to write.
#   * Only touches rows whose deal_id IG does not return. A row IG still holds is never modified.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-09-03  Alex Hind   Initial build.
# ======================================================================================================================

import argparse
import logging
import sys

log = logging.getLogger("wo_sweep")

STATUSES = ("PENDING", "WATCHING")


def live_deal_ids(owner):
    """Deal ids IG is actually holding. Raises if IG cannot be read -- silence must not read as 'none'."""
    import ig_shim
    with ig_shim._IG_LOCK, ig_shim.acting_session(owner):
        orders = ig_shim.get_working_orders()
    if orders is None:
        raise RuntimeError("IG returned no answer for working orders")
    out = set()
    for w in orders:
        od = w.get("workingOrderData") or {}
        if od.get("dealId"):
            out.add(str(od["dealId"]))
    return out


def sweep(apply_changes=False, owner=None):
    from db_pool import get_db
    from hvf_web import server

    owner = owner or server._OWNER
    try:
        live = live_deal_ids(owner)
    except Exception as ex:
        log.error("could not read IG working orders (%s); refusing to sweep -- an unreadable account "
                  "must never be treated as an empty one", ex)
        return 1
    log.info("IG is holding %d working order(s)", len(live))

    db = get_db()
    try:
        rows = db.run("select deal_id, ticker, status, placed_at::date, good_till "
                      "from working_orders where status in ('PENDING','WATCHING') "
                      "order by ticker") or []
    finally:
        db.close()

    stale = [r for r in rows if str(r[0] or "") not in live]
    log.info("%d row(s) marked %s; %d of them are not at IG", len(rows), "/".join(STATUSES), len(stale))
    for deal_id, tk, st, placed, gt in stale:
        log.info("   %-10s %-9s placed %s  good-till %s", tk, st, placed, str(gt or "")[:10])

    if not stale:
        log.info("nothing to sweep")
        return 0
    if not apply_changes:
        log.info("DRY RUN - nothing written. Re-run with --apply to mark these %d row(s) EXPIRED.", len(stale))
        return 0

    db = get_db()
    try:
        ids = [str(r[0]) for r in stale]
        db.run("update working_orders set status = 'EXPIRED', "
               "notes = coalesce(notes, '') || ' | swept 2026-09-03: IG is not holding this order', "
               "updated_at = now() where deal_id = any(:ids) and status in ('PENDING','WATCHING')",
               ids=ids)
    finally:
        db.close()
    log.info("marked %d row(s) EXPIRED; the bridge skip-list should now match IG", len(stale))
    return 0


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    ap.add_argument("--owner", default="", help="act as this web user (default: the configured owner)")
    a = ap.parse_args()
    return sweep(apply_changes=a.apply, owner=a.owner or None)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(".env")
    sys.exit(main())
