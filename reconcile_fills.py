#!/usr/bin/env python3
# ======================================================================================================================
# File:         reconcile_fills.py
# Author:       Alex Hind (via Claude)
# Created:      2026-09-11
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Marks a PENDING working order FILLED once IG shows the position it became. NOTHING ELSE.
#
# WHY THIS EXISTS AS A SEPARATE JOB. ig_shim.reconcile_working_orders() already does this, but it is only called from
# intraday_signals.py and run_session.py -- both session monitors, both disabled -- so nothing has reconciled since
# 2026-09-04 16:57 (measured 2026-09-11: max(filled_at) is 2026-09-03, and 0700.HK and SYY were still PENDING with
# filled_at null days after IG filled them). That function cannot simply be scheduled in its place: alongside the fill
# sync it PROMOTES WATCHING rows, which places live IG orders, and DELETES IG working orders that have drifted outside
# WO_CANCEL_BAND_PCT, and fires trade_opened notifications and emails -- which for an order filled days ago would
# announce it as though it had just happened. Scheduling all of that to fix a status column would be a far larger
# change to live trading than the problem warrants (account owner, 2026-09-11: fill-status only).
#
# WHAT THE STALE STATUS ACTUALLY BROKE. order_filter_audit.placement_setups reads the setup an order was placed from
# out of working_orders rows whose status is FILLED. With the status stuck on PENDING it found nothing, so R:R came
# back empty, so auto_close_failed_opens marked the position "unjudgeable: R:R not recorded" and left it open. That was
# 2 of the 2 positions the auto-closer has ever been able to judge (0700.HK 2026-09-07, SYY 2026-09-09), both of them
# failing their volume tests on a stored break bar. The data was there the whole time -- squeeze_history holds
# 0700.HK ready_date 2026-09-03 rr 3.15, SYY ready_date 2026-08-17 rr 5.58 -- the join just could not reach it.
#
# WHAT THIS WILL NOT DO, deliberately:
#   * it never places an order, and never promotes a WATCHING row (those have no IG order to have filled);
#   * it never deletes or amends anything at IG -- the only IG calls are two reads;
#   * it never marks a row CANCELLED or EXPIRED. A PENDING row that is gone from IG with no matching position is
#     LEFT ALONE and reported. Clearing those out is a separate decision (the 55 phantom rows, register item 70);
#   * it sends no notification and writes no positions row.
#
# MATCHING FAILS CLOSED. A row is only marked FILLED on an unambiguous match: same epic, same direction, size within
# the same tolerance reconcile_working_orders uses, the position opened no earlier than the order was placed, and
# exactly ONE candidate position for exactly ONE candidate order. Anything ambiguous is left PENDING and reported,
# because a wrong fill attribution would feed the wrong setup's R:R into a decision that closes a real position.
#
# filled_at IS IG'S TIMESTAMP, NOT now(). These fills are days old and stamping them with the time this job first
# noticed would be an invented number sitting in a column other reports read.
#
# Usage:  python reconcile_fills.py [--user NAME] [--apply]
#         Dry run unless --apply is passed.
# ======================================================================================================================

import argparse
import logging

log = logging.getLogger("reconcile_fills")

def pair_fills(orders, positions, claimed=(), ig_order_ids=None):
    """(matches, unmatched) for orders that have left IG's working-order book.

    A thin shell over working_order_state.classify, which is the ONLY place allowed to interpret absence from IG.
    This function used to make that judgement itself, which is how three pieces of code came to hold four different
    opinions about one column (see that module's header).
    """
    import working_order_state as wos

    ids = set(ig_order_ids or ())
    states = wos.classify(orders, ids, positions, claimed=claimed)
    matches, unmatched = [], []
    for o in orders:
        state, fill = states.get(str(o.get("deal_id")), (wos.LIVE, None))
        if state == wos.FILLED:
            matches.append({**o, "fill_deal_id": fill.get("deal_id"), "filled_at": fill.get("created"),
                            "fill_size": fill.get("size")})
        else:
            unmatched.append({**o, "why": {wos.DEAD: "no open position matches it",
                                           wos.LIVE: "still live, or the match is ambiguous",
                                           wos.WATCHING: "watching: no IG order was ever placed"}[state]})
    return matches, unmatched


def _mark_filled(db, deal_id, fill_deal_id, filled_at):
    """FILLED plus the fill's identity and IG's own timestamp. Touches no other column."""
    db.run("""update working_orders
                 set status = 'FILLED', updated_at = now(),
                     filled_at = coalesce(:v_when, now()),
                     fill_deal_id = coalesce(:v_fill, fill_deal_id),
                     notes = coalesce(notes || ' | ', '') ||
                             'fill reconciled by reconcile_fills ' || to_char(now(), 'YYYY-MM-DD')
               where deal_id = :v_deal and status = 'PENDING'""",
           v_when=filled_at, v_fill=str(fill_deal_id), v_deal=str(deal_id))


def run(user=None, apply=False):
    """One pass. Returns a summary; never raises, so a scheduled caller cannot be brought down by it."""
    summary = {"pending": 0, "still_at_ig": 0, "matched": 0, "filled": 0, "unmatched": [], "rows": []}
    try:
        from hvf_web import server
        from db_pool import get_db
        import ig_shim
        user = user or server._OWNER
        if ig_shim.session_for(user) is None:
            log.warning("no IG credentials for %s; nothing to do", user)
            return summary

        db = get_db()
        try:
            rows = db.run("""select deal_id, ticker, epic, direction, size, placed_at, status, good_till
                             from working_orders
                             where status = 'PENDING' and deal_id is not null""") or []
            claimed = {str(r[0]) for r in (db.run(
                "select fill_deal_id from working_orders where fill_deal_id is not null") or [])}
        finally:
            db.close()

        orders = [{"deal_id": r[0], "ticker": r[1], "epic": r[2], "direction": r[3],
                   "size": r[4], "placed_at": r[5], "status": r[6], "good_till": r[7]}
                  for r in rows]
        summary["pending"] = len(orders)
        if not orders:
            return summary

        with ig_shim._IG_LOCK, ig_shim.acting_session(user):
            live_ids = {str((wo.get("workingOrderData") or {}).get("dealId") or "")
                        for wo in (ig_shim.get_working_orders() or [])}
            raw = ig_shim.get_open_positions() or []
        summary["still_at_ig"] = sum(1 for o in orders if str(o["deal_id"]) in live_ids)

        positions = []
        for p in raw:
            mk, pd = (p.get("market") or {}), (p.get("position") or {})
            positions.append({"deal_id": pd.get("dealId"), "epic": str(mk.get("epic") or ""),
                              "direction": pd.get("direction"), "size": pd.get("size"),
                              "created": str(pd.get("createdDateUTC") or pd.get("createdDate") or "")[:19]})

        # Which rows are LIVE, FILLED or DEAD is not decided here -- see working_order_state. Date shapes are
        # normalised there too, so nothing in this file needs to know that IG returns strings and pg returns dates.
        matched, unmatched = pair_fills(orders, positions, claimed=claimed, ig_order_ids=live_ids)
        summary["matched"] = len(matched)
        summary["unmatched"] = [{"ticker": u["ticker"], "why": u["why"]} for u in unmatched]
        summary["rows"] = [{"ticker": m["ticker"], "deal_id": m["deal_id"],
                            "fill_deal_id": m["fill_deal_id"], "filled_at": m["filled_at"]}
                           for m in matched]
        for u in unmatched:
            log.info("  leaving %s PENDING: %s", u.get("ticker"), u.get("why"))
        for m in matched:
            log.info("  %s -> FILLED (order %s became position %s, opened %s)",
                     m.get("ticker"), m.get("deal_id"), m.get("fill_deal_id"), m.get("filled_at"))

        if not matched:
            return summary
        if not apply:
            log.info("DRY RUN - nothing written. Re-run with --apply.")
            return summary

        db = get_db()
        try:
            for m in matched:
                try:
                    _mark_filled(db, m["deal_id"], m["fill_deal_id"], m["filled_at"])
                    summary["filled"] += 1
                except Exception as exc:
                    log.error("could not mark %s FILLED: %s", m.get("ticker"), exc)
        finally:
            db.close()
    except Exception as exc:
        log.error("fill reconciliation failed: %s", exc)
    return summary


if __name__ == "__main__":
    from dotenv import load_dotenv; load_dotenv(override=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Mark PENDING working orders FILLED once IG shows the position.")
    ap.add_argument("--user", help="acting user (default: the account owner)")
    ap.add_argument("--apply", action="store_true", help="actually write; otherwise dry run")
    a = ap.parse_args()
    print(run(user=a.user, apply=a.apply))
