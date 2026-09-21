#!/usr/bin/env python
"""Tell somebody when a scheduled job fails, and tell them again when it recovers.

WHY THIS EXISTS. On 2026-09-21 the owner asked "the scheduled job for Morning Chain has failed - is this
managed without my input?" It was not. That job had failed on 15, 16, 17, 18 and 21 September and
nothing reached anyone. MEASURED the same day: only 5 of 61 workflows carry any failure or alert step,
and the Morning Chain is not one of them.

IT IS NOT A DETECTION PROBLEM. hvf_web/scheduled_jobs.py ALREADY pulls every registered job's run stats
from the GitHub Actions API to back the admin "Scheduled Jobs" tab -- last_status, last_time, failure
counts, the lot. What was missing is that nobody is TOLD. A page you have to visit is exactly the
"nobody reads a green cron page" failure this repository already records against the GH_PAT expiry that
went unnoticed for eight weeks. So this reuses that module rather than writing a second Actions client.

WHY ONE WATCHER RATHER THAN `if: failure()` IN EVERY WORKFLOW. Fifty-six workflows would each need the
step, every future workflow would need someone to remember it, and a workflow that never RAN AT ALL --
the failure mode that hid the expired PAT -- emits no failure event to hang a step on. One watcher over
the registry covers all three cases and cannot be forgotten on a new job.

RECOVERY IS REPORTED TOO (owner 2026-09-19, P-10: "if we have a failed cron job and the next one
succeeds, it would be good to get an email confirmation that we have moved from fail to success").
That needs memory, so the last seen status per job is kept in web_json_store under `cron_watch_state`.

DELIBERATELY NOISY ONLY ON CHANGE. It alerts when a job ENTERS failure and when it LEAVES failure, not
on every pass over a job that is still broken -- an alert that arrives every thirty minutes about a
known failure is one people filter, and then the next real one is filtered too.
"""

import argparse
import logging
import os
import time

log = logging.getLogger("cron_watch")

STATE_KEY = "cron_watch_state"
# GitHub's conclusions that mean "this did not work". 'cancelled' is included deliberately: the
# Morning Chain's 2026-09-19 run was cancelled, and a cancelled scheduled job is a job that did not do
# its work, whatever the reason.
FAIL = {"failure", "cancelled", "timed_out", "startup_failure", "action_required"}
OK = {"success"}


def _age_days(iso: str):
    """Days since an ISO-8601 timestamp, or None when it cannot be read.

    None means "do not judge" rather than "fine": a timestamp we cannot parse must not be silently
    treated as fresh, but nor should it raise and take the whole watch down.
    """
    if not iso:
        return None
    try:
        import datetime as _dt
        t = _dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=_dt.timezone.utc)
        return (_dt.datetime.now(_dt.timezone.utc) - t).total_seconds() / 86400.0
    except Exception:
        return None


def _enabled_titles():
    """Titles of the jobs cron-job.org will actually FIRE, or None if that cannot be established.

    JUDGING A DISABLED JOB IS NOISE, AND NOISE IS HOW AN ALERT DIES. Measured 2026-09-21: 20 of the 43
    jobs on the account are disabled -- the session monitors among them, which CLAUDE.md records as
    deliberately off because WEB_BRIDGE is the only enabled execution source. A first pass without this
    filter reported 17 jobs "failing or stale", 15 of them off by design. An alert that cries wolf
    fifteen times gets filtered, and then the sixteenth one is filtered too.

    None means "could not tell", and the caller then judges everything rather than silently judging
    nothing -- failing towards a noisy alert beats failing towards no alert at all.
    """
    try:
        import os
        import requests
        # CRONJOB_API_KEY is NOT in the environment: it lives in the encrypted Supabase secret store,
        # which db_pool decrypts into os.environ on import. Reading the variable without importing
        # db_pool first finds nothing and silently disables this filter -- which is exactly what
        # happened on the first run here, and is the same trap setup_cronjobs.py --status falls into.
        try:
            import db_pool            # noqa: F401  -- imported for its secret-store bootstrap
        except Exception as exc:
            log.warning("secret-store bootstrap unavailable (%s)", exc)
        key = os.environ.get("CRONJOB_API_KEY", "")
        if not key:
            log.warning("no CRONJOB_API_KEY; cannot tell enabled jobs from disabled ones")
            return None
        r = requests.get("https://api.cron-job.org/jobs",
                         headers={"Authorization": f"Bearer {key}"}, timeout=20)
        r.raise_for_status()
        return {j.get("title") for j in (r.json().get("jobs") or []) if j.get("enabled")}
    except Exception as exc:
        log.warning("could not read cron-job.org enabled state (%s); judging every job", exc)
        return None


def _state() -> dict:
    """Last seen status per job title. {} when unavailable -- a missing state must not stop the alert."""
    try:
        import web_store
        doc = web_store.load_json_store(STATE_KEY) or {}
        return doc.get("statuses") or {}
    except Exception as exc:
        log.warning("could not read %s (%s); treating every job as newly seen", STATE_KEY, exc)
        return {}


def _save_state(statuses: dict) -> bool:
    try:
        import web_store
        return bool(web_store.save_json_store(
            STATE_KEY, {"built_at": time.time(), "statuses": statuses}))
    except Exception as exc:
        log.error("could not save %s: %s", STATE_KEY, exc)
        return False


def _notify(subject: str, body: str) -> None:
    """Slack AND email, independently. Either channel failing must not suppress the other."""
    try:
        import notify
        notify.alert_system_error(session="Scheduled", component="cron-job.org / GitHub Actions",
                                  summary=subject, detail=body)
    except Exception as exc:
        log.error("Slack alert failed: %s", exc)
    try:
        from trade_email import send_simple_email
        if not send_simple_email(subject=subject, text=body):
            log.error("email alert returned False")
    except Exception as exc:
        log.error("email alert failed: %s", exc)


def check(dry_run: bool = False, alert_ok: bool = False, stale_after_days: float = 3.0) -> int:
    """Compare every registered job's last run against what we saw last time.

    Returns the number of jobs CURRENTLY failing, so the watcher's own run goes red while anything is
    broken. A watcher that reports a failure and then exits 0 is one more green tick hiding a problem.
    """
    from hvf_web import scheduled_jobs
    data = scheduled_jobs.get_jobs(force=True)          # force: a 30-minute cache would blunt this
    jobs = data.get("jobs") or []
    if not jobs:
        log.error("no jobs returned (%s); cannot judge anything", data.get("error") or "no error given")
        return 1

    enabled = _enabled_titles()
    previous, now = _state(), {}
    newly_failing, recovered, still_failing, stale = [], [], [], []
    skipped = 0

    for j in jobs:
        title = j.get("raw_title") or j.get("title") or "?"
        # A disabled job is not expected to run or to succeed, so it is not news either way.
        if enabled is not None and title not in enabled:
            skipped += 1
            continue
        status = (j.get("last_status") or "-").lower()
        now[title] = status
        was = (previous.get(title) or "").lower()
        line = f"{title} — {status} at {j.get('last_time') or '?'} ({j.get('workflow') or '?'})"
        if status in FAIL:
            (newly_failing if was not in FAIL else still_failing).append(line)
        elif status in OK and was in FAIL:
            recovered.append(line)
        # A JOB THAT STOPPED FIRING EMITS NO FAILURE AT ALL, and that is the failure mode that hid the
        # expired GH_PAT for EIGHT WEEKS -- the jobs did not fail, they simply never ran, and the page
        # stayed green. Found again 2026-09-21: Trading State Audit is scheduled Mon-Fri and cron-job.org
        # reported its last execution as 2026-09-18, having skipped Friday, while its last GitHub
        # conclusion sat there looking like ordinary news. Age is therefore checked SEPARATELY from
        # status: a stale success is not a success.
        age_days = _age_days(j.get("last_time"))
        if age_days is not None and age_days > stale_after_days:
            stale.append(f"{title} — last ran {age_days:.1f} days ago "
                         f"({j.get('last_time')}), schedule '{j.get('cron') or '?'}'")

    log.info("%d jobs checked (%d skipped as disabled): %d newly failing, %d recovered, "
             "%d still failing, %d stale",
             len(jobs) - skipped, skipped, len(newly_failing), len(recovered),
             len(still_failing), len(stale))
    for line in newly_failing + recovered + still_failing + stale:
        log.info("  %s", line)

    if not dry_run:
        if newly_failing:
            _notify(f"⚠ {len(newly_failing)} scheduled job(s) FAILING",
                    "These have just started failing:\n\n" + "\n".join(newly_failing)
                    + (f"\n\nStill failing from before:\n" + "\n".join(still_failing) if still_failing else ""))
        if recovered:
            _notify(f"✅ {len(recovered)} scheduled job(s) RECOVERED",
                    "Back to success after failing:\n\n" + "\n".join(recovered))
        if stale:
            _notify(f"⚠ {len(stale)} scheduled job(s) have STOPPED RUNNING",
                    "These have not run recently enough for their schedule. A job that stops firing "
                    "emits no failure at all, so nothing else notices — this is the shape that hid an "
                    "expired GH_PAT for eight weeks:\n\n" + "\n".join(stale))
        if alert_ok and not (newly_failing or recovered or still_failing or stale):
            _notify("✅ All scheduled jobs healthy", f"{len(jobs)} jobs, none failing.")
        # Save AFTER alerting: if the alert raises, the next run must still see the transition rather
        # than having quietly recorded it as already reported.
        _save_state(now)

    return len(newly_failing) + len(still_failing) + len(stale)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report only; send nothing, save nothing")
    ap.add_argument("--alert-ok", action="store_true", help="also notify when everything is healthy")
    ap.add_argument("--stale-after-days", type=float, default=3.0,
                    help="flag a job whose last run is older than this (default 3)")
    a = ap.parse_args()
    failing = check(dry_run=a.dry_run, alert_ok=a.alert_ok, stale_after_days=a.stale_after_days)
    if failing:
        log.error("%d job(s) failing or stale", failing)
    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
