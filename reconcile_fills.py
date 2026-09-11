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

# The same tolerance ig_shim.reconcile_working_orders matches on, so the two cannot disagree about what counts as the
# same size. A fill can differ slightly from the requested size; 0.011 covers the smallest dealable increments.
SIZE_ABS_TOLERANCE = 0.011
SIZE_PCT_TOLERANCE = 0.05


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _size_matches(order_size, position_size):
    a, b = _num(order_size), _num(position_size)
    if a is None or b is None:
        return False
    return abs(b - a) <= max(SIZE_ABS_TOLERANCE, abs(a) * SIZE_PCT_TOLERANCE)


def pair_fills(orders, positions, claimed=()):
    """(matches, unmatched) for PENDING orders that have left IG's working-order book.

    `orders` is [{deal_id, ticker, epic, direction, size, placed_at}], `positions` is
    [{deal_id, epic, direction, size, created}]. `claimed` holds position deal ids already recorded as some other
    row's fill.

    Pure, so the matching rule can be tested without IG or a database -- which is the only part of this worth
    getting wrong, and the part a live test cannot safely explore.
    """
    claimed = set(str(c) for c in (claimed or []) if c)
    matches, unmatched = [], []
    # Candidate positions per order, then the reverse, so a position two orders could claim is given to neither.
    cands = {}
    for o in orders:
        ok = []
        for p in positions:
            if str(p.get("deal_id") or "") in claimed:
                continue
            if str(p.get("epic") or "") != str(o.get("epic") or ""):
                continue
            if str(p.get("direction") or "") != str(o.get("direction") or ""):
                continue
            if not _size_matches(o.get("size"), p.get("size")):
                continue
            # A position that existed before the order was placed cannot be that order's fill.
            placed, created = o.get("placed_at"), p.get("created")
            if placed and created and created < placed:
                continue
            ok.append(p)
        cands[str(o.get("deal_id"))] = ok
    # A position wanted by more than one order is ambiguous for ALL of them.
    wanted = {}
    for did, ok in cands.items():
        for p in ok:
            wanted.setdefault(str(p.get("deal_id")), []).append(did)
    for o in orders:
        did = str(o.get("deal_id"))
        ok = cands.get(did) or []
        if len(ok) != 1:
            unmatched.append({**o, "why": ("no open position matches it" if not ok
                                           else f"{len(ok)} open positions match it")})
            continue
        p = ok[0]
        if len(wanted.get(str(p.get("deal_id")), [])) != 1:
            unmatched.append({**o, "why": "another pending order matches the same position"})
            continue
        matches.append({**o, "fill_deal_id": p.get("deal_id"), "filled_at": p.get("created"),
                        "fill_size": p.get("size")})
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
            rows = db.run("""select deal_id, ticker, epic, direction, size, placed_at
                             from working_orders
                             where status = 'PENDING' and deal_id is not null""") or []
            claimed = {str(r[0]) for r in (db.run(
                "select fill_deal_id from working_orders where fill_deal_id is not null") or [])}
        finally:
            db.close()

        # PAPER rows have no IG order and can never be reconciled against one.
        orders = [{"deal_id": r[0], "ticker": r[1], "epic": r[2], "direction": r[3],
                   "size": r[4], "placed_at": r[5]}
                  for r in rows if not str(r[0]).startswith("PAPER-")]
        summary["pending"] = len(orders)
        if not orders:
            return summary

        with ig_shim._IG_LOCK, ig_shim.acting_session(user):
            live_ids = {str((wo.get("workingOrderData") or {}).get("dealId") or "")
                        for wo in (ig_shim.get_working_orders() or [])}
            raw = ig_shim.get_open_positions() or []

        gone = [o for o in orders if str(o["deal_id"]) not in live_ids]
        summary["still_at_ig"] = len(orders) - len(gone)
        if not gone:
            return summary

        positions = []
        for p in raw:
            mk, pd = (p.get("market") or {}), (p.get("position") or {})
            positions.append({"deal_id": pd.get("dealId"), "epic": str(mk.get("epic") or ""),
                              "direction": pd.get("direction"), "size": pd.get("size"),
                              "created": str(pd.get("createdDateUTC") or pd.get("createdDate") or "")[:19]})

        # placed_at is a datetime and createdDateUTC an ISO string; compare on the date both agree about.
        for o in gone:
            o["placed_at"] = str(o["placed_at"] or "")[:10]
        for p in positions:
            p["created"] = (p["created"] or "")[:10]

        matched, unmatched = pair_fills(gone, positions, claimed=claimed)
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
