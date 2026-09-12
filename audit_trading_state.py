#!/usr/bin/env python3
# ======================================================================================================================
# File:         audit_trading_state.py
# Created:      2026-09-12
#
# Asserts that the trading jobs had an EFFECT, not that they exited zero.
#
# WHY. Every defect found in this repository over the last week looked healthy from the outside. The auto-closer ran
# 300 green passes in four days and closed nothing. The sweep ran once, logged "marked 4 row(s) EXPIRED" and wrote
# none of them. Scanner Snapshot Publish reported success through a two-week publication outage. In each case the job
# ran, exited zero, and did not do its job -- and nothing anywhere compared the outcome against what should have
# happened. A green tick is a statement about a process, not about the account.
#
# So this checks outcomes against rules, and needs nothing new to be recorded to do it:
#
#   1. STATUS CONTRADICTIONS. Every PENDING/WATCHING row is classified against IG by working_order_state. A row IG
#      shows as filled while we still call it PENDING means the fill reconcile did not run; a row that is DEAD while
#      we still call it PENDING means the sweep did not. Both silently mislead the Order Bridge, whose skip-list is
#      built from exactly these rows.
#
#   2. UNRESOLVABLE ROWS. A PENDING/WATCHING row with neither deal_id nor good_till can never be classified DEAD --
#      there is nothing to ask IG about and no clock to run out -- so it would sit in the skip-list for ever, keeping
#      the bridge off that instrument permanently. None exist today (measured 2026-09-12: 0 live, 188 DELETED), which
#      is exactly when to add the check.
#
#   3. SHOULD-HAVE-CLOSED. A position opened today, whose own exchange has closed, still open, whose stored break bar
#      FAILS the volume tests with no missing break-bar measure -- that is the auto-closer's rule saying close and the
#      account saying open. It is scoped to ONE day so it reports a miss once rather than re-reporting history for
#      ever, and it needs no new logging: it re-derives the verdict from the same functions the closer uses.
#
# Legitimate reasons a position in (3) stayed open -- MAX_PER_RUN, the per-user switch off, IG refusing to deal at
# that instant -- are all worth a look when they happen, so they are reported rather than filtered out.
#
# Usage:  python audit_trading_state.py [--date YYYY-MM-DD] [--user NAME] [--quiet]
#         Exits non-zero when anything is found, so the run goes red as well as posting.
# ======================================================================================================================

import argparse
import datetime as _dt
import logging

log = logging.getLogger("trading_audit")


def _today_utc():
    return _dt.datetime.now(_dt.timezone.utc).date()


def _positions_from_ig(user):
    """IG's open positions, with the ticker resolved through epic_lookup."""
    import ig_shim
    from db_pool import get_db
    db = get_db()
    try:
        epic2tk = {str(r[1]): r[0] for r in (db.run("select ticker, epic from epic_lookup") or []) if r[1]}
    finally:
        db.close()
    with ig_shim._IG_LOCK, ig_shim.acting_session(user):
        raw = ig_shim.get_open_positions() or []
    out = []
    for p in raw:
        mk, pd = (p.get("market") or {}), (p.get("position") or {})
        tk = epic2tk.get(str(mk.get("epic") or ""))
        if not tk:
            continue
        out.append({"ticker": tk, "deal_id": pd.get("dealId"), "name": mk.get("instrumentName"),
                    "epic": str(mk.get("epic") or ""),
                    "opened": str(pd.get("createdDateUTC") or pd.get("createdDate") or "")[:10],
                    "direction": pd.get("direction"), "size": pd.get("size"),
                    "created": str(pd.get("createdDateUTC") or pd.get("createdDate") or "")[:10]})
    return out


def open_working_rows():
    """Every PENDING/WATCHING row, in the shape the checks and working_order_state expect."""
    from db_pool import get_db
    db = get_db()
    try:
        rows = db.run("select id, deal_id, ticker, status, placed_at::date, good_till, epic, direction, size "
                      "from working_orders where status in ('PENDING','WATCHING') order by ticker") or []
    finally:
        db.close()
    return [{"id": r[0], "deal_id": r[1], "ticker": r[2], "status": r[3], "placed_at": r[4],
             "good_till": r[5], "epic": r[6], "direction": r[7], "size": r[8]} for r in rows]


def check_status_contradictions(recs, live_order_ids, positions):
    """Rows whose stored status disagrees with what IG says they are.

    Takes data rather than fetching it, so the broken states this exists to catch can actually be driven in a
    test. A check that has only ever been run against a healthy system has not been shown to detect anything.
    """
    import working_order_state as wos

    out = []
    for rec, (state, fill) in zip(recs, wos.classify(recs, set(live_order_ids or ()), list(positions or ()))):
        if state == wos.FILLED:
            out.append(f"{rec['ticker']} is still {rec['status']} but IG holds the position it became "
                       f"({fill.get('deal_id')}) — the fill reconcile has not run")
        elif state == wos.DEAD:
            out.append(f"{rec['ticker']} is still {rec['status']} but IG has neither the order nor a "
                       f"matching position — the sweep has not run")
    return out


def check_unresolvable_rows(recs):
    """Rows that can never be resolved either way, so they would block the bridge for ever."""
    return [f"{r['ticker']} ({r['status']}, placed {r['placed_at']}) has neither deal_id nor good_till — "
            f"it can never be resolved and will sit in the bridge skip-list permanently"
            for r in recs if not r.get("deal_id") and not r.get("good_till")]


def check_should_have_closed(user, on_date, positions, now=None):
    """Positions the auto-closer's own rule says should have closed today, and which are still open."""
    import auto_close_failed_opens as ac
    import market_hours
    import order_filter_audit

    now = now or _dt.datetime.now(_dt.timezone.utc)
    todays = [p for p in positions if p["opened"] == str(on_date)]
    if not todays:
        return []
    # Only judge instruments whose own exchange has finished for the day -- before that, nothing has been missed.
    done = []
    for p in todays:
        close = market_hours.close_utc(p["ticker"], _dt.date.fromisoformat(str(on_date)))
        if close and now > close:
            done.append(p)
    if not done:
        return []
    audit = order_filter_audit.audit_positions(user, done)
    out = []
    for row in audit.get("rows", []):
        vol = ac._volume_breaches(row)
        blocking = ac._blocking_unknowns(row)
        if vol and not blocking:
            out.append(f"{row.get('ticker')} opened {on_date}, its exchange has closed, it FAILED "
                       f"({'; '.join(vol)}) and it is still open — the closer did not act")
    return out


def run(on_date=None, user=None, alert=True, now=None):
    """Returns {"findings": [...], "checked": {...}}. Never raises."""
    findings, checked = [], {}
    try:
        import ig_shim
        from hvf_web import server
        user = user or server._OWNER
        on_date = on_date or _today_utc()
        positions = _positions_from_ig(user)
        recs = open_working_rows()
        with ig_shim._IG_LOCK, ig_shim.acting_session(user):
            live = {str((wo.get("workingOrderData") or {}).get("dealId") or "")
                    for wo in (ig_shim.get_working_orders() or [])}
        checked["open_positions"] = len(positions)
        checked["working_rows"] = len(recs)
        checked["opened_on_date"] = sum(1 for p in positions if p["opened"] == str(on_date))

        for name, fn in (("status contradictions", lambda: check_status_contradictions(recs, live, positions)),
                         ("unresolvable rows", lambda: check_unresolvable_rows(recs)),
                         ("should have closed", lambda: check_should_have_closed(user, on_date, positions, now=now))):
            try:
                got = fn() or []
            except Exception as exc:                 # a failing check must be loud, never silently empty
                got = [f"CHECK FAILED ({name}): {exc}"]
            checked[name] = len(got)
            findings.extend(got)
    except Exception as exc:
        findings.append(f"AUDIT FAILED before it could check anything: {exc}")

    for f in findings:
        log.error("  %s", f)
    if findings and alert:
        try:
            import notify                            # notify._send gates on slack_enabled; never post directly
            notify.alert_system_error(
                session=f"Trading state audit {on_date}", component="working_orders / auto-closer",
                summary=f"{len(findings)} finding(s): the jobs ran but the account does not match the rules",
                detail="\n".join(findings))
        except Exception as exc:
            log.error("could not post the audit alert: %s", exc)
    return {"findings": findings, "checked": checked}


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv; load_dotenv(override=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Assert the trading jobs had an effect.")
    ap.add_argument("--date", help="the day to judge (default: today UTC)")
    ap.add_argument("--user", help="acting user (default: the account owner)")
    ap.add_argument("--quiet", action="store_true", help="do not post to Slack")
    a = ap.parse_args()
    res = run(on_date=a.date, user=a.user, alert=not a.quiet)
    print(res["checked"])
    print(f"{len(res['findings'])} finding(s)")
    sys.exit(1 if res["findings"] else 0)
