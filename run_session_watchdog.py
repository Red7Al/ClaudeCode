# =============================================================================
# File:         run_session_watchdog.py
# Author:       Alex Hind
# Created:      2026-06-03
#
# Description:
# -----------------------------------------------------------------------------
# Watchdog — checks that expected trading sessions have actually produced
# signal data within the expected window. Alerts to Slack #claude-trading-alerts
# if a session ran (macro_snapshot exists) but no signal_log rows were written,
# or if a session is overdue entirely.
#
# Runs every 30 minutes during trading hours (00:00–21:59 UTC) Mon–Fri.
#
# Sessions and their expected signal windows (UTC):
#   AUS_OPEN    00:00   — signal_log expected by 01:00
#   UK_OPEN     08:00   — signal_log expected by 09:30
#   US_OPEN     14:30   — signal_log expected by 16:00
#
# Environment Variables Required:
#   SUPABASE_USER, SUPABASE_DB_PASSWORD, SLACK_ALERTS
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.2   2026-06-06  Alex Hind   Replace the grace=60 workaround (1.0.1b) with a
#                                 proper HH:MM open-time model. check_session now takes
#                                 open_minute_utc and SESSIONS carries an explicit
#                                 minute, so the 14:30 US open is expressed directly
#                                 with a true 30-min grace (deadline 15:00). The old
#                                 model could only express HH:00 opens, forcing a
#                                 fragile grace inflation that broke self-documentation
#                                 and would silently misfire on any schedule change.
# 1.0.1   2026-06-06  Alex Hind   Fix 2 bugs: (a) late_mins added grace_minutes
#                                 twice — deadline already incorporates grace_minutes,
#                                 so `+ grace_minutes` inflated reported lateness by
#                                 30 min (e.g. "1386min late" instead of "6min late");
#                                 (b) US_OPEN deadline anchored to 14:00 UTC + 30min =
#                                 14:30 — fires alert at the exact moment the session
#                                 starts; fixed to 14:00 + 60min = 15:00 (30min grace
#                                 from the 14:30 scheduled open), matching the comment.
# 1.0.0   2026-06-03  Alex Hind   Initial build. Monitors AUS_OPEN, UK_OPEN,
#                                 US_OPEN signal windows and alerts Slack if
#                                 a session is overdue or produced no signals.
# =============================================================================

import os
try:
    from dotenv import load_dotenv; load_dotenv(override=True)
except ImportError:
    pass   # GitHub Actions — env vars injected from secrets, no .env file needed
import logging
import requests
import pg8000.native
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("watchdog")

SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"
SLACK_URL     = os.environ.get("SLACK_ALERTS", "")


def get_db():
    return pg8000.native.Connection(
        host=SUPABASE_HOST, port=6543, database="postgres",
        user=os.environ["SUPABASE_USER"],
        password=os.environ["SUPABASE_DB_PASSWORD"],
        ssl_context=True
    )


def alert(session: str, problem: str, detail: str = ""):
    msg = f"⚠️ *Session Watchdog — {session}*\n{problem}"
    if detail:
        msg += f"\n```{detail}```"
    log.warning(f"WATCHDOG ALERT: {session} — {problem}")
    if SLACK_URL:
        requests.post(SLACK_URL,
                      json={"text": msg},
                      timeout=10)


# GitHub API — used to re-trigger a missed workflow via repository_dispatch
GITHUB_REPO    = "Red7Al/ClaudeCode"
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")

# Map session name → workflow filename (for auto-trigger on miss)
SESSION_WORKFLOW = {
    "AUS_OPEN": "trading-aus-open.yml",
    "UK_OPEN":  "trading-uk-open.yml",
    "US_OPEN":  "trading-us-open.yml",
}


def trigger_workflow(workflow_file: str, session_name: str):
    """
    Trigger a GitHub Actions workflow via the REST API.
    Requires GITHUB_TOKEN secret in the watchdog workflow env.
    Called when a session is found to be missing — fires the session instead
    of just alerting, so trading resumes automatically.
    """
    if not GITHUB_TOKEN:
        log.warning(f"GITHUB_TOKEN not set — cannot auto-trigger {workflow_file}")
        return False
    try:
        resp = requests.post(
            f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{workflow_file}/dispatches",
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept":        "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"ref": "main"},
            timeout=15
        )
        if resp.status_code == 204:
            log.info(f"Auto-triggered {workflow_file} for missed {session_name}")
            return True
        else:
            log.warning(f"Auto-trigger failed: {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        log.warning(f"Auto-trigger request failed: {e}")
        return False


def check_session(conn, session_name: str, open_hour_utc: int,
                  open_minute_utc: int, grace_minutes: int, today: str) -> bool:
    """
    Returns True (healthy) or False (problem found).

    Checks:
    1. If it's past the grace window for this session today, check for a
       macro_snapshot — if missing, the workflow didn't start at all.
       → Auto-triggers the session workflow to recover.
    2. If macro_snapshot exists but no signal_log rows, the scan ran but
       logged nothing (likely a DB schema issue or total scan failure).

    The session open time is expressed as HH:MM (open_hour_utc, open_minute_utc)
    so sessions that don't start on the hour (e.g. US_OPEN at 14:30) get an
    accurate deadline. grace_minutes is then a true grace window past the open.
    """
    now_utc  = datetime.now(timezone.utc)
    deadline = datetime.now(timezone.utc).replace(
        hour=open_hour_utc, minute=open_minute_utc, second=0, microsecond=0
    ) + timedelta(minutes=grace_minutes)

    # Only check if the deadline has passed today
    if now_utc < deadline:
        log.info(f"{session_name}: not yet due (deadline {deadline.strftime('%H:%M')} UTC)")
        return True

    # Check macro_snapshot (positional params — see signal_log INSERT note)
    macro_rows = conn.run(
        """select count(*) from macro_snapshot
           where session = $1
             and date(snapshot_time at time zone 'UTC') = $2""",
        [session_name, today]
    )
    macro_count = int(macro_rows[0][0]) if macro_rows else 0

    if macro_count == 0:
        alert(
            session_name,
            f"No macro_snapshot recorded today — GitHub Actions cron missed. "
            f"Auto-triggering {session_name} now...",
            f"Expected after {open_hour_utc:02d}:{open_minute_utc:02d} UTC + {grace_minutes}min grace. "
            f"Actual time: {now_utc.strftime('%H:%M')} UTC"
        )
        # Auto-trigger the missed session instead of just alerting
        wf = SESSION_WORKFLOW.get(session_name)
        if wf:
            triggered = trigger_workflow(wf, session_name)
            if triggered:
                log.info(f"✅ {session_name} auto-triggered successfully")
                # Notify Slack so user knows recovery is in progress
                try:
                    from notify import alert_watchdog_trigger
                    late_mins = int((now_utc - deadline).total_seconds() / 60)
                    alert_watchdog_trigger(session_name, wf, late_mins)
                except Exception:
                    pass
            else:
                log.warning(f"⚠ {session_name} auto-trigger failed — manual intervention needed")
        return False

    # macro ran — check signal_log
    sig_rows = conn.run(
        """select count(*) from signal_log
           where session = $1
             and date(session_time at time zone 'UTC') = $2""",
        [session_name, today]
    )
    sig_count = int(sig_rows[0][0]) if sig_rows else 0

    if sig_count == 0:
        hint = "signal_log INSERT failing — check schema or DB connection"
        alert(
            session_name,
            f"Macro gate ran ✅ but 0 instruments logged to signal_log. "
            f"Trades cannot fire — all signal INSERTs are failing.",
            hint
        )
        return False

    log.info(f"{session_name}: healthy — {macro_count} macro snapshot(s), "
             f"{sig_count} signal row(s)")
    return True


def main():
    now   = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    log.info(f"Watchdog running at {now.strftime('%H:%M')} UTC for {today}")

    conn = get_db()

    # (session_name, open_hour_utc, open_minute_utc, grace_minutes)
    # Grace window = 30 min after scheduled open — catch misses quickly.
    # GitHub cron can run slightly late; 30 min gives it time to fire naturally
    # before the watchdog triggers a manual re-run. Open time is HH:MM so the
    # 14:30 US open gets an accurate 15:00 deadline with a real 30-min grace.
    SESSIONS = [
        ("AUS_OPEN", 0,  0,  30),   # 00:00 UTC open → alert + auto-trigger if no data by 00:30
        ("UK_OPEN",  8,  0,  30),   # 08:00 UTC open → alert + auto-trigger if no data by 08:30
        ("US_OPEN",  14, 30, 30),   # 14:30 UTC open → alert + auto-trigger if no data by 15:00
    ]

    problems = 0
    for session_name, open_hour, open_minute, grace in SESSIONS:
        ok = check_session(conn, session_name, open_hour, open_minute, grace, today)
        if not ok:
            problems += 1

    conn.close()

    if problems == 0:
        log.info("All sessions healthy.")
    else:
        log.warning(f"{problems} session problem(s) detected — alerts sent, auto-triggers attempted.")


if __name__ == "__main__":
    main()
