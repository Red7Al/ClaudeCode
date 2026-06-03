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
# Runs every 2 hours via GitHub Actions cron.
#
# Sessions and their expected signal windows (UTC):
#   AUS_OPEN    00:00   — signal_log expected by 01:00
#   UK_OPEN     08:00   — signal_log expected by 09:30
#   US_OPEN     14:30   — signal_log expected by 16:00
#
# Environment Variables Required:
#   SUPABASE_USER, SUPABASE_DB_PASSWORD, SLACK_ALERTS
# =============================================================================

import os
from dotenv import load_dotenv; load_dotenv()
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


def check_session(conn, session_name: str, open_hour_utc: int,
                  grace_minutes: int, today: str) -> bool:
    """
    Returns True (healthy) or False (problem found).

    Checks:
    1. If it's past the grace window for this session today, check for a
       macro_snapshot — if missing, the workflow didn't start at all.
    2. If macro_snapshot exists but no signal_log rows, the scan ran but
       logged nothing (likely a DB schema issue or total scan failure).
    """
    now_utc  = datetime.now(timezone.utc)
    deadline = datetime.now(timezone.utc).replace(
        hour=open_hour_utc, minute=0, second=0, microsecond=0
    ) + timedelta(minutes=grace_minutes)

    # Only check if the deadline has passed today
    if now_utc < deadline:
        log.info(f"{session_name}: not yet due (deadline {deadline.strftime('%H:%M')} UTC)")
        return True

    # Check macro_snapshot
    macro_rows = conn.run(
        """select count(*) from macro_snapshot
           where session = :s
             and date(snapshot_time at time zone 'UTC') = :d""",
        s=session_name, d=today
    )
    macro_count = int(macro_rows[0][0]) if macro_rows else 0

    if macro_count == 0:
        alert(
            session_name,
            f"No macro_snapshot recorded today — GitHub Actions workflow may have failed to start.",
            f"Expected after {open_hour_utc:02d}:00 UTC. Check Actions tab: github.com/Red7Al/ClaudeCode/actions"
        )
        return False

    # macro ran — check signal_log
    sig_rows = conn.run(
        """select count(*) from signal_log
           where session = :s
             and date(session_time at time zone 'UTC') = :d""",
        s=session_name, d=today
    )
    sig_count = int(sig_rows[0][0]) if sig_rows else 0

    if sig_count == 0:
        # Get first error from any recent signal_log to give a hint
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

    # (session_name, open_hour_utc, grace_minutes)
    SESSIONS = [
        ("AUS_OPEN", 0,  90),   # 00:00 UTC open, alert if no data by 01:30
        ("UK_OPEN",  8,  90),   # 08:00 UTC open, alert if no data by 09:30
        ("US_OPEN",  14, 90),   # 14:30 UTC open, alert if no data by 16:00
    ]

    problems = 0
    for session_name, open_hour, grace in SESSIONS:
        ok = check_session(conn, session_name, open_hour, grace, today)
        if not ok:
            problems += 1

    conn.close()

    if problems == 0:
        log.info("All sessions healthy.")
    else:
        log.warning(f"{problems} session problem(s) detected — alerts sent to Slack.")


if __name__ == "__main__":
    main()
