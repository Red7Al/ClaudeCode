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
import logging

log = logging.getLogger("auto_close")

# The break-bar measures. Anything outside this set is reported and never acted on.
VOLUME_TESTS = ("RVOL", "VolumeScore", "above VWAP", "ATR expanding")

# A criteria change that matches everything must trip a limit rather than empty the account.
MAX_PER_RUN = 10

SETTING = "auto_close_failed_opens"       # per-user; 1 = on, anything else = off


def _volume_breaches(row):
    """Only the breaches that are volume tests. Durable ones are carried separately and never acted on."""
    return [b for b in (row.get("breaches") or []) if any(t in b for t in VOLUME_TESTS)]


def _durable_breaches(row):
    return [b for b in (row.get("breaches") or []) if not any(t in b for t in VOLUME_TESTS)]


def candidates(user, on_date, positions):
    """Positions opened ON on_date whose volume tests failed. Returns (to_close, skipped)."""
    import order_filter_audit
    todays = [p for p in positions if str(p.get("opened") or "")[:10] == str(on_date)]
    if not todays:
        return [], []
    audit = order_filter_audit.audit_positions(user, todays)
    to_close, skipped = [], []
    for row in audit.get("rows", []):
        vol = _volume_breaches(row)
        dur = _durable_breaches(row)
        if row.get("unknown"):
            # Unjudgeable is NOT a reason to close a real position. It is a reason to look at why the
            # daily capture did not run: that table silently stored nothing for six days in Sept 2026.
            skipped.append({**row, "why_skipped": "unjudgeable: " + "; ".join(row["unknown"])})
        elif vol:
            to_close.append({**row, "volume_breaches": vol, "durable_breaches": dur})
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


def record(db, user, row, profit, currency, outcome):
    """One durable row per attempt, successful or not. This table is the evidence base for whether the
    criteria are right (user 2026-09-04: "if it turns out that we do not keep tx open then we may need to
    readdress the success criteria") -- so it stores WHY each was closed and what it realised, not just
    the fact of it."""
    db.run("""insert into auto_closed_positions
                (deal_id, ticker, user_name, opened_on, closed_at, direction, size,
                 volume_breaches, durable_breaches, profit, currency, outcome)
              values (:d,:t,:u,:o, now(), :dir,:sz,:vb,:db,:p,:c,:oc)
              on conflict (deal_id) do update set closed_at = now(), outcome = :oc, profit = :p""",
           d=str(row.get("deal_id")), t=row.get("ticker"), u=str(user), o=row.get("opened") or None,
           dir=row.get("direction"), sz=row.get("size"),
           vb="; ".join(row.get("volume_breaches") or []),
           db="; ".join(row.get("durable_breaches") or []),
           p=profit, c=currency, oc=outcome)


def run(user=None, on_date=None, apply=False):
    """One pass. Returns a summary; never raises, so a scheduled caller cannot be brought down by it."""
    import datetime as dt
    summary = {"date": None, "opened_today": 0, "to_close": 0, "closed": 0, "skipped": 0,
               "enabled": False, "applied": bool(apply), "rows": []}
    try:
        from hvf_web import server, web_users as _wu
        import ig_shim
        from db_pool import get_db
        user = user or server._OWNER
        on_date = str(on_date or dt.date.today())
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
            positions.append({"ticker": tk, "deal_id": deal,
                              "opened": str(pd.get("createdDateUTC") or pd.get("createdDate") or "")[:10],
                              "direction": pd.get("direction"), "size": pd.get("size")})
            priced[deal] = (_profit(pd, mk), pd.get("currency"))
        summary["opened_today"] = sum(1 for p in positions if p["opened"] == on_date)

        to_close, skipped = candidates(user, on_date, positions)
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
                    profit, currency = priced.get(deal, (None, None))
                    try:
                        # The same call the confirmed web close uses, with its own reason so this
                        # mechanism is distinguishable from a manual close in IG's own history.
                        ok = bool(ig_shim.close_trade(deal, reason="AUTO_VOLUME_TEST_FAILED"))
                        detail = ig_shim.last_close_outcome() if hasattr(ig_shim, "last_close_outcome") else ""
                    except Exception as exc:
                        log.error("close failed for %s: %s", r.get("ticker"), exc)
                        record(db, user, r, profit, currency, f"failed: {exc}"[:200])
                        continue
                    record(db, user, r, profit, currency,
                           ("closed" if ok else "not_confirmed") + (f" ({detail})" if detail else ""))
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
