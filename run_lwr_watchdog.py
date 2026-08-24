# ======================================================================================================================
# File:         run_lwr_watchdog.py
# Author:       Claude
# Created:      2026-08-23
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Watchdog for Let Winners Run. Alerts to Slack #alerts when positions are relying on the stop manager and
# the manager has not run recently.
#
# WHY THIS EXISTS. Let Winners Run places orders with NO take-profit, so IG will not close them at target.
# The gain is then protected only while run_let_winners_run() keeps executing. With a take-profit the
# broker holds it even if every system of ours is down; without one, a bridge that quietly stops leaves an
# open winner able to round-trip from profit all the way back to its original stop.
#
# The Order Bridge runs `0 6-22/2 * * 1-5` -- every two hours, weekdays only -- so positions are already
# unmanaged overnight and at weekends by design. Nothing detected a longer gap, and this repository has
# produced six separate cases of a mechanism that existed but was never invoked. This is the precondition
# for ever enabling the live path (docs/LET_WINNERS_RUN_DECISION.md).
#
# DELIBERATELY INDEPENDENT OF THE BRIDGE. It runs as its own scheduled job, because a watchdog living
# inside the thing it watches is useless in the exact case that matters: the bridge failing.
#
# It reads only the heartbeat that run_let_winners_run() writes (`lwr_last_pass`), so it needs no IG
# credentials and cannot touch a position. `managed` in that heartbeat is what keeps it quiet rather than
# noisy: a stale pass with nothing to manage is not a problem, a stale pass with open positions is.
#
# Environment Variables Required:
#   SUPABASE_USER, SUPABASE_DB_PASSWORD, SLACK_ALERTS
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-08-23  Claude      Initial build.
# ======================================================================================================================

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

log = logging.getLogger("lwr_watchdog")

SLACK_URL = os.environ.get("SLACK_ALERTS", "")

# The bridge runs every 2 hours on weekdays, so a gap beyond ~5 hours during the week means passes are
# being missed rather than merely spaced. Overnight and weekend gaps are expected and are handled by
# --max-age rather than by pretending they are faults.
DEFAULT_MAX_AGE_HOURS = 5.0


def read_heartbeat() -> dict | None:
    """The last completed management pass, or None if the manager has never run."""
    try:
        import web_store
        doc = web_store.load_json_store("lwr_last_pass")
        return doc if isinstance(doc, dict) else None
    except Exception as exc:
        log.error("could not read the let-winners-run heartbeat: %s", exc)
        return None


def assess(doc: dict | None, max_age_hours: float, now: datetime | None = None) -> dict:
    """Decide whether to alert. Pure, so the decision is testable without Slack or a database."""
    now = now or datetime.now(timezone.utc)
    if not doc:
        # Never run. With the feature off, positions keep their take-profit and nothing is at risk, so
        # this must stay silent rather than alert forever on a system that simply is not using it.
        return {"alert": False, "reason": "the manager has never run; nothing depends on it"}
    try:
        at = datetime.fromisoformat(str(doc.get("at")))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return {"alert": True, "reason": "the heartbeat has no usable timestamp", "age_hours": None,
                "managed": doc.get("managed")}
    age = (now - at).total_seconds() / 3600.0
    managed = int(doc.get("managed") or 0)
    if age <= max_age_hours:
        return {"alert": False, "reason": "recent pass", "age_hours": round(age, 1), "managed": managed}
    if managed <= 0:
        # Stale, but the last pass had no positions bound to it. Nothing is unprotected.
        return {"alert": False, "reason": "stale but nothing was being managed",
                "age_hours": round(age, 1), "managed": 0}
    return {"alert": True, "age_hours": round(age, 1), "managed": managed,
            "mode": doc.get("mode"), "at": doc.get("at"),
            "reason": f"{managed} position(s) rely on the stop manager and it has not run for "
                      f"{age:.1f} hours"}


def post(text: str) -> bool:
    """Slack #alerts, honouring the per-channel off switch (every direct poster must check it)."""
    try:
        from notify import slack_enabled
        if not (SLACK_URL and slack_enabled("alerts")):
            log.info("Slack alerts are off; not posting.")
            return False
        import requests
        r = requests.post(SLACK_URL, json={"text": text}, timeout=20)
        return r.status_code < 300
    except Exception as exc:
        log.error("could not post the alert: %s", exc)
        return False


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-age", type=float, default=DEFAULT_MAX_AGE_HOURS,
                    help=f"hours before a pass counts as missed (default {DEFAULT_MAX_AGE_HOURS})")
    ap.add_argument("--dry-run", action="store_true", help="assess and report; post nothing")
    a = ap.parse_args()

    verdict = assess(read_heartbeat(), a.max_age)
    if not verdict["alert"]:
        log.info("OK - %s", verdict["reason"])
        return 0

    text = (":rotating_light: *Let Winners Run: stop management has stopped*\n"
            f"{verdict['reason']}.\n"
            f"Last pass: {verdict.get('at') or 'unknown'} ({verdict.get('mode') or 'unknown'} mode). "
            f"Those positions were placed with NO take-profit, so IG will not close them at target and "
            f"an open winner can give back its gain. Check the Order Bridge workflow.")
    log.error(text.replace("\n", " "))
    if a.dry_run:
        log.info("dry run - not posted")
        return 1
    post(text)
    return 1


if __name__ == "__main__":
    sys.exit(main())
