#!/usr/bin/env python3
# ======================================================================================================================
# File:         auto_close_failed_opens.py
# Author:       Alex Hind (via Claude)
# Created:      2026-09-04
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Closes a position the bridge opened TODAY when it fails the volume tests measured on its opening bar (user 2026-09-04:
# "the system closed the tx after an auto open - closed due to failing volume tests on the day the tx is opened").
#
# WHY IT CAN EXIST NOW. RVOL, VolumeScore, above-VWAP and ATR describe the BREAK bar. An order reaches IG a median of 8
# days before its break, so nothing can test them at placement -- measured 2026-09-03, and written up in
# docs/ORDER_TIMING_AND_RVOL.md. The fill IS the break, so the opening day is the first and only moment these become
# knowable. This closes the loop the placement gate cannot.
#
# SCOPE, deliberately narrow:
#   * SAME DAY ONLY. A position opened on an earlier day is never touched. The rule is a check on the open, not a rolling
#     re-test of the book -- on 2026-09-04 every one of the 14 open positions failed these tests, and a sweep would have
#     closed the lot on criteria that had never once been enforceable.
#   * VOLUME TESTS ONLY. R:R, Quality and instrument value were all knowable when the order was placed. If one of those
#     is breached the defect is in the placement gate, and closing the position hides it. They are reported, never acted on.
#   * The metrics are READ from instrument_metrics_daily, never recomputed (user: "it should not need to be recomputed -
#     it should be stored"). A missing row means UNJUDGEABLE, and an unjudgeable position is LEFT OPEN: closing a real
#     position on an absence of evidence is the one mistake here that costs money and cannot be undone.
#
# SAFETY:
#   * OFF by default. Requires the acting user's `auto_close_failed_opens` limit to be 1.
#   * Dry run unless --apply is passed.
#   * Re-reads the account through the user's own IG session immediately before closing, so a stale read cannot close
#     something else, and every attempt is recorded whether or not it succeeds.
#   * Never closes more than MAX_PER_RUN in one pass -- a criteria change that suddenly matches everything should trip
#     a limit and be looked at, not empty the account.
#
# Usage:  python auto_close_failed_opens.py [--user NAME] [--date YYYY-MM-DD] [--apply]
# ======================================================================================================================

import argparse
import datetime as _dt
import logging

log = logging.getLogger("auto_close")

# The break-bar measures. Anything outside this set is reported and never acted on. Imported rather than
# restated: order_filter_audit owns this list and a second copy is how two screens end up disagreeing.
from order_filter_audit import BREAK_BAR_LABELS as VOLUME_TESTS

# audit_positions adds this when the break bar has no stored row at all. It names no individual measure,
# so a label test alone would read it as durable -- but it means EVERY volume test is missing, which is
# exactly the case that must keep a position open.
NO_BREAK_BAR = "no stored metrics for the day it opened"

# A criteria change that matches everything must trip a limit rather than empty the account.
MAX_PER_RUN = 10

SETTING = "auto_close_failed_opens"       # per-user; 1 = on, anything else = off


def today_utc():
    """The date the same-day rule is measured against.

    UTC, because IG stamps createdDateUTC in UTC and this is compared against it. The local-date call is
    LOCAL: in BST the two differ between 00:00 and 01:00, and a position opened at 23:30 UTC would be
    invisible to a run in that hour. It fails safe -- nothing is closed rather than the wrong thing -- and
    the scheduled runner is UTC anyway, but a same-day rule that only holds in one timezone is not a rule.
    Extracted so it can be asserted rather than trusted.
    """
    return _dt.datetime.now(_dt.timezone.utc).date()


def closing_window_state(positions, now=None, minutes=None):
    """{ticker: in_window} for each position's own exchange (user 2026-09-06).

    THE WINDOW IS PER INSTRUMENT, NOT PER RUN. The book spans Sydney to New York, so there is no single
    moment at which "the market" is closing; a run at 15:00 UTC is in Frankfurt's last half hour and nine
    hours past Tokyo's. market_hours derives each exchange's close from the exchange itself.
    """
    import market_hours
    mins = minutes if minutes is not None else market_hours.CLOSING_WINDOW_MINUTES
    return {p["ticker"]: market_hours.in_closing_window(p["ticker"], now=now, minutes=mins)
            for p in positions if p.get("ticker")}


def _volume_breaches(row):
    """Only the breaches that are volume tests. Durable ones are carried separately and never acted on."""
    return [b for b in (row.get("breaches") or []) if any(t in b for t in VOLUME_TESTS)]


def _durable_breaches(row):
    return [b for b in (row.get("breaches") or []) if not any(t in b for t in VOLUME_TESTS)]


def _blocking_unknowns(row):
    """The unknowns that genuinely make a position unjudgeable: the BREAK-BAR ones, and only those.

    A durable unknown -- R:R, Quality, instrument value -- cannot make a volume verdict unjudgeable,
    because this mechanism never acts on a durable breach in the first place (an R:R miss is a
    placement-gate defect, reported and left open). Its absence therefore has no bearing on whether the
    volume tests failed.

    FOUND 2026-09-11, and it had disabled the closer completely. The test was `if row["unknown"]`, so ANY
    unknown blocked the close. Both of the two positions the closer has ever been able to judge -- 0700.HK
    on 2026-09-07 and SYY on 2026-09-09 -- were kept open with "unjudgeable: R:R not recorded" while
    sitting on a stored break bar that failed its volume tests (SYY: volume_score 4, above_vwap_setup
    False, atr_expanding False). The R:R was missing only because working_orders had not been reconciled,
    so nothing about the actual decision was unknown. The pending-order path already draws this exact
    distinction, at order_filter_audit._durable_only; this is the same rule from the other side.
    """
    return [u for u in (row.get("unknown") or [])
            if NO_BREAK_BAR in u or any(t in u for t in VOLUME_TESTS)]


def candidates(user, on_date, positions, now=None, require_window=True, window_minutes=None):
    """Positions opened ON on_date whose volume tests failed. Returns (to_close, skipped).

    THE CLOSING-WINDOW GATE (user 2026-09-06). A position is only considered while its own exchange is in
    its final `window_minutes` -- the only span in which the day's volume is substantially known AND the
    position can still be traded. Outside it the position is skipped with the reason recorded, never
    closed: before the window the bar is too incomplete to judge, and after it the market has gone.

    `require_window=False` exists for dry runs and tests, so the rest of the pipeline can be exercised at
    any hour. It must never be used to place a real close -- run() only accepts it without --apply.
    """
    import order_filter_audit
    todays = [p for p in positions if str(p.get("opened") or "")[:10] == str(on_date)]
    if not todays:
        return [], []
    in_window = closing_window_state(todays, now=now, minutes=window_minutes)
    to_close, skipped = [], []
    if require_window:
        outside = [p for p in todays if not in_window.get(p["ticker"])]
        for p in outside:
            skipped.append({**p, "why_skipped": "not in its exchange's closing window"})
        todays = [p for p in todays if in_window.get(p["ticker"])]
        if not todays:
            return [], skipped
    audit = order_filter_audit.audit_positions(user, todays)
    # audit_positions rebuilds its rows from the fields it cares about, so the epic does not survive it.
    # Carried back by deal id -- the only key that is unique per position -- because the close needs it to
    # ask IG whether the market is dealable, and a missing epic there fails closed and silently keeps
    # every position open.
    epics = {str(p.get("deal_id")): p.get("epic") for p in todays if p.get("deal_id")}
    for row in audit.get("rows", []):
        row = {**row, "epic": epics.get(str(row.get("deal_id"))) or ""}
        vol = _volume_breaches(row)
        dur = _durable_breaches(row)
        blocking = _blocking_unknowns(row)
        if blocking:
            # Unjudgeable is NOT a reason to close a real position. It is a reason to look at why the
            # daily capture did not run: that table silently stored nothing for six days in Sept 2026.
            skipped.append({**row, "why_skipped": "unjudgeable: " + "; ".join(blocking)})
        elif vol:
            # A durable unknown does not stop the close, but it IS recorded against it: the evidence table
            # has to say what was and was not known at the moment a real position was closed.
            unknown_dur = [u for u in (row.get("unknown") or []) if u not in blocking]
            to_close.append({**row, "volume_breaches": vol, "durable_breaches": dur + unknown_dur})
        else:
            skipped.append({**row, "why_skipped": "passed the volume tests"})
    return to_close, skipped


def ensure_schema(db):
    db.run("""create table if not exists auto_closed_positions (
                 deal_id       text primary key,
                 ticker        text,
                 user_name     text,
                 opened_on     date,
                 closed_at     timestamptz default now(),
                 direction     text,
                 size          double precision,
                 volume_breaches text,
                 durable_breaches text,
                 profit        double precision,
                 currency      text,
                 outcome       text)""")
    # `create table if not exists` does NOTHING when the table already exists, so a column added later
    # never appears and every read of it fails. Add it explicitly, as instrument_metrics learned to.
    for column, ddl in (("name", "text"),):
        try:
            db.run(f"alter table auto_closed_positions add column if not exists {column} {ddl}")
        except Exception as exc:
            log.debug("could not ensure column %s: %s", column, exc)


def record(db, user, row, profit, currency, outcome):
    """One durable row per attempt, successful or not. This table is the evidence base for whether the
    criteria are right (user 2026-09-04: "if it turns out that we do not keep tx open then we may need to
    readdress the success criteria") -- so it stores WHY each was closed and what it realised, not just
    the fact of it."""
    db.run("""insert into auto_closed_positions
                (deal_id, ticker, name, user_name, opened_on, closed_at, direction, size,
                 volume_breaches, durable_breaches, profit, currency, outcome)
              values (:d,:t,:nm,:u,:o, now(), :dir,:sz,:vb,:db,:p,:c,:oc)
              on conflict (deal_id) do update set closed_at = now(), outcome = :oc, profit = :p""",
           d=str(row.get("deal_id")), t=row.get("ticker"), nm=row.get("name"), u=str(user), o=row.get("opened") or None,
           dir=row.get("direction"), sz=row.get("size"),
           vb="; ".join(row.get("volume_breaches") or []),
           db="; ".join(row.get("durable_breaches") or []),
           p=profit, c=currency, oc=outcome)


def run(user=None, on_date=None, apply=False, require_window=True, now=None, window_minutes=None):
    """One pass. Returns a summary; never raises, so a scheduled caller cannot be brought down by it.

    `require_window=False` is a DRY-RUN AID ONLY and is refused alongside apply=True: closing a position
    outside its closing window is the one thing the window exists to prevent.
    """
    import datetime as dt
    if apply and not require_window:
        raise ValueError("require_window=False cannot be combined with apply=True: the closing-window "
                         "gate is the safety property, not a convenience")
    summary = {"date": None, "opened_today": 0, "to_close": 0, "closed": 0, "skipped": 0,
               "enabled": False, "applied": bool(apply), "rows": []}
    try:
        from hvf_web import server, web_users as _wu
        import ig_shim
        from db_pool import get_db
        user = user or server._OWNER
        on_date = str(on_date or today_utc())
        summary["date"] = on_date
        limits = (_wu.get_settings(user) or {}).get("limits") or {}
        summary["enabled"] = str(limits.get(SETTING) or "") in ("1", "True", "true")
        if ig_shim.session_for(user) is None:
            log.warning("no IG credentials for %s; nothing to do", user)
            return summary

        db = get_db()
        try:
            epic2tk = {str(r[1]): r[0] for r in (db.run("select ticker, epic from epic_lookup") or []) if r[1]}
        finally:
            db.close()

        with ig_shim._IG_LOCK, ig_shim.acting_session(user):
            raw = ig_shim.get_open_positions() or []
        positions, priced = [], {}
        for p in raw:
            mk, pd = (p.get("market") or {}), (p.get("position") or {})
            tk = epic2tk.get(str(mk.get("epic") or ""))
            if not tk:
                continue
            deal = pd.get("dealId")
            positions.append({"ticker": tk, "deal_id": deal, "name": mk.get("instrumentName"),
                              # Carried so the close can ask IG whether this specific market is dealable
                              # right now -- the one authority that covers holidays and suspensions,
                              # which no derived timetable can.
                              "epic": str(mk.get("epic") or ""),
                              "opened": str(pd.get("createdDateUTC") or pd.get("createdDate") or "")[:10],
                              "direction": pd.get("direction"), "size": pd.get("size")})
            priced[deal] = (_profit(pd, mk), pd.get("currency"))
        summary["opened_today"] = sum(1 for p in positions if p["opened"] == on_date)

        to_close, skipped = candidates(user, on_date, positions, now=now,
                                       require_window=require_window, window_minutes=window_minutes)
        summary["to_close"], summary["skipped"] = len(to_close), len(skipped)
        summary["rows"] = [{"ticker": r["ticker"], "deal_id": r.get("deal_id"),
                            "why": "; ".join(r.get("volume_breaches") or [])} for r in to_close]
        for r in skipped:
            log.info("  keeping %s: %s", r.get("ticker"), r.get("why_skipped"))
        for r in to_close:
            log.info("  would close %s (%s): %s", r.get("ticker"), r.get("deal_id"),
                     "; ".join(r.get("volume_breaches") or []))

        if not to_close:
            return summary
        if len(to_close) > MAX_PER_RUN:
            log.error("REFUSING to act: %d positions matched, limit is %d. A rule that suddenly matches "
                      "this many is a rule to look at, not to execute.", len(to_close), MAX_PER_RUN)
            return summary
        if not summary["enabled"]:
            log.warning("auto-close is OFF for %s (set the %s limit to 1 to enable); reporting only",
                        user, SETTING)
            return summary
        if not apply:
            log.info("DRY RUN - nothing closed. Re-run with --apply.")
            return summary

        db = get_db()
        try:
            ensure_schema(db)
            with ig_shim._IG_LOCK, ig_shim.acting_session(user):
                live = {str((p.get("position") or {}).get("dealId") or ""): p
                        for p in (ig_shim.get_open_positions() or [])}
                for r in to_close:
                    deal = str(r.get("deal_id") or "")
                    if deal not in live:                     # re-read: never close on a stale view
                        record(db, user, r, None, None, "gone_before_close")
                        continue
                    # IG's own verdict on whether this market is dealable, asked immediately before the
                    # close. The derived timetable models neither public holidays nor half-days nor an IG
                    # suspension, and on any of them it would report a closing window for a market that is
                    # not there. This is the check that covers all three, and it fails closed.
                    import market_hours
                    if not market_hours.is_tradeable_now(r.get("epic") or ""):
                        log.warning("IG will not deal %s right now; leaving it open", r.get("ticker"))
                        record(db, user, r, None, None, "not_tradeable_at_close")
                        continue
                    profit, currency = priced.get(deal, (None, None))
                    # Written BEFORE the close, updated after. If the broker call succeeds and the audit
                    # write then fails, the old order left a position closed with no record of why --
                    # the worst possible audit outcome. Same shape as the ig-cancel-orders path, which
                    # records "submitted" before it asks IG for anything.
                    try:
                        record(db, user, r, profit, currency, "submitted")
                    except Exception as exc:
                        log.error("could not record the intent to close %s (%s); NOT closing it",
                                  r.get("ticker"), exc)
                        continue
                    try:
                        # The same call the confirmed web close uses, with its own reason so this
                        # mechanism is distinguishable from a manual close in IG's own history.
                        ok = bool(ig_shim.close_trade(deal, reason="AUTO_VOLUME_TEST_FAILED"))
                        detail = ig_shim.last_close_outcome() if hasattr(ig_shim, "last_close_outcome") else ""
                    except Exception as exc:
                        log.error("close failed for %s: %s", r.get("ticker"), exc)
                        record(db, user, r, profit, currency, f"failed: {exc}"[:200])
                        continue
                    try:
                        record(db, user, r, profit, currency,
                               ("closed" if ok else "not_confirmed") + (f" ({detail})" if detail else ""))
                    except Exception as exc:
                        # The position IS closed at this point. Losing the outcome must be loud.
                        log.error("CLOSED %s but could not record the outcome: %s", r.get("ticker"), exc)
                    if ok:
                        summary["closed"] += 1
                        log.info("closed %s (%s): %s", r.get("ticker"), deal,
                                 "; ".join(r.get("volume_breaches") or []))
        finally:
            db.close()
    except Exception as exc:
        log.error("auto-close pass failed: %s", exc)
    return summary


def _profit(pd, mk):
    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    d = str(pd.get("direction", "")).upper()
    size, lvl = num(pd.get("size")), num(pd.get("level") or pd.get("openLevel"))
    cs = num(pd.get("contractSize")) or 1.0
    close = num(mk.get("bid")) if d == "BUY" else num(mk.get("offer"))
    if lvl and close is not None and size is not None:
        pts = (close - lvl) if d == "BUY" else (lvl - close)
        return round(pts * size * cs, 2)
    return None


if __name__ == "__main__":
    from dotenv import load_dotenv; load_dotenv(override=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Close same-day opens that failed their volume tests.")
    ap.add_argument("--user", help="acting user (default: the account owner)")
    ap.add_argument("--date", help="the opening date to check (default: today)")
    ap.add_argument("--apply", action="store_true", help="actually close; otherwise dry run")
    a = ap.parse_args()
    print(run(user=a.user, on_date=a.date, apply=a.apply))
