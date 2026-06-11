# =============================================================================
# File:         setup_cronjobs.py
# Author:       Alex Hind
# Created:      2026-06-05
#
# Description:
# -----------------------------------------------------------------------------
# One-shot script to create all cron-job.org jobs that trigger GitHub
# Actions workflow_dispatch for the EndToEndTrading system.
#
# Run once after obtaining:
#   CRONJOB_API_KEY  — from cron-job.org → Settings → API
#   GITHUB_TOKEN     — github.com → Settings → Developer settings →
#                      Personal access tokens → Fine-grained token
#                      Permissions: Actions = Read and write
#
# Usage:
#   set CRONJOB_API_KEY=your_key
#   set GITHUB_TOKEN=your_token
#   python setup_cronjobs.py
#
# Safe to re-run — checks for existing jobs before creating.
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-06-05  Alex Hind   Initial build — all session/report cron jobs.
# 1.7.0   2026-06-11  Alex Hind   Add "UK HVF Watch" job (30 8,10,12,14 Mon-Fri
#                                 → trading-uk-hvf-watch.yml). Mirrors US HVF Watch
#                                 cadence for UK/FTSE250 instruments.
# 1.6.0   2026-06-10  Alex Hind   --create-missing mode: creates only JOBS not yet
#                                 on cron-job.org (skips existing). Safe to re-run.
#                                 Workflow updated to expose mode input (reconcile /
#                                 create-missing) and pass GH_PAT as GITHUB_TOKEN.
# 1.5.0   2026-06-10  Alex Hind   Add "US HVF Watch" job (30 14,16,18,20 Mon-Fri
#                                 → trading-us-hvf-watch.yml). HVF watch decoupled
#                                 from 30-min US Monitor; runs 2-hourly with dedup.
# 1.4.0   2026-06-09  Alex Hind   Increased monitoring frequency (user: "best
#                                 information", GitHub-cron limit no longer applies):
#                                 session monitors */20->*/5, commodity */30->*/10,
#                                 watchdog */30->*/10, social hourly->*/15. Added a
#                                 --reconcile mode that PATCHes only the schedule of
#                                 EXISTING jobs (preserves each job's stored auth, so
#                                 retuning frequency needs only CRONJOB_API_KEY, no
#                                 GitHub PAT). Deployed via trading-setup-cronjobs.yml.
# 1.3.0   2026-06-08  Alex Hind   Consolidate JOBS to the authoritative schedule and
#                                 reflect the live cron-job.org account: monitors are
#                                 now single */N-step jobs (AUS/UK/US Monitor) instead
#                                 of ~70 per-slot jobs (which would have created
#                                 duplicates on re-run). Migrated the watchdog +
#                                 commodity-monitor off GitHub cron onto cron-job.org.
#                                 Added a proactive "Daily Diagnostics" job (07:30
#                                 Mon-Fri) so deployment/stack health is checked daily
#                                 without prompting. Matches what is live on the
#                                 user-owned account (created 2026-06-08).
# 1.2.0   2026-06-07  Alex Hind   Fix create_job payload for the real cron-job.org
#                                 API — it sent cronExpression/exprType and headers
#                                 as a list, which the API rejects with HTTP 500 (the
#                                 script had never worked). Now builds explicit
#                                 minutes/hours/mdays/months/wdays arrays via
#                                 _cron_to_schedule() and headers as an object.
#                                 Verified live: created COT + 2 Sunday jobs. Added
#                                 the Sunday jobs. ⚠ Running the FULL script populates
#                                 ALL jobs — only do so against a sole cron-job.org
#                                 account, else it double-fires with the old
#                                 (lost-access) account that still holds weekday jobs.
# 1.1.0   2026-06-07  Alex Hind   Add "COT Report" job (Sat 10:00 UTC →
#                                 trading-cot-report.yml), scheduled after the
#                                 weekend review (09:00) refreshes COT data.
# =============================================================================

import os
import sys
import json
import requests

CRONJOB_API_KEY = os.environ.get("CRONJOB_API_KEY", "")
GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO     = "Red7Al/ClaudeCode"

# CRONJOB_API_KEY is always required. GITHUB_TOKEN is only needed to CREATE jobs
# (it is baked into the new job's auth header); --reconcile (retune schedules of
# existing jobs) does not need it.
if not CRONJOB_API_KEY:
    print("ERROR: Set CRONJOB_API_KEY environment variable")
    raise SystemExit(1)

CRONJOB_API = "https://api.cron-job.org"
GITHUB_API  = "https://api.github.com"

GITHUB_HEADERS = {
    "Authorization":        f"Bearer {GITHUB_TOKEN}",
    "Accept":               "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "Content-Type":         "application/json",
}

GITHUB_BODY = json.dumps({"ref": "main"})

# ---------------------------------------------------------------------------
# Jobs to create
# Each entry: (title, cron_expression, workflow_file)
# cron_expression uses UTC (cron-job.org default is UTC)
# ---------------------------------------------------------------------------
JOBS = [
    # ── Asia / AUS session ──────────────────────────────────────────────────
    ("AUS Open",            "0 0 * * 1-5",    "trading-aus-open.yml"),
    ("AUS Monitor",          "*/5 0-6 * * 1-5", "trading-aus-monitor.yml"),
    ("Commodity Monitor AM", "*/10 4-8 * * 1-5", "trading-commodity-monitor.yml"),
    # ── Pre-UK ──────────────────────────────────────────────────────────────
    ("HVF Daily Report",    "0 7 * * 1-5",    "trading-hvf-report.yml"),
    # ── UK session ──────────────────────────────────────────────────────────
    ("UK Open",             "0 8 * * 1-5",    "trading-uk-open.yml"),
    ("UK Morning Brief",    "0 9 * * 1,5",    "trading-uk-morning-brief.yml"),
    ("UK Monitor",           "*/5 8-16 * * 1-5","trading-uk-monitor.yml"),
    # ── US session ──────────────────────────────────────────────────────────
    ("US Open",              "30 14 * * 1-5",    "trading-us-open.yml"),
    ("US Monitor",           "*/5 14-21 * * 1-5","trading-us-monitor.yml"),
    ("US HVF Watch",         "30 14,16,18,20 * * 1-5", "trading-us-hvf-watch.yml"),
    ("UK HVF Watch",         "30 8,10,12,14 * * 1-5",  "trading-uk-hvf-watch.yml"),
    ("Social Monitor",       "*/15 7-22 * * 1-5",   "trading-social-monitor.yml"),
    # ── Close & reports ─────────────────────────────────────────────────────
    ("Commodity Monitor PM", "*/10 21-23 * * 1-5","trading-commodity-monitor.yml"),
    ("Session Close",        "0 21 * * 1-5",     "trading-session-close.yml"),
    ("Daily Report",         "30 21 * * 1-5",    "trading-daily-report.yml"),
    # ── Safety net + proactive self-checks ──────────────────────────────────
    ("Session Watchdog",     "*/10 0-21 * * 1-5","trading-watchdog.yml"),     # migrated off GitHub cron 2026-06-08
    ("Daily Diagnostics",    "30 7 * * 1-5",     "trading-diagnostics.yml"),  # proactive daily health check -> #alerts
    # ── Weekend ─────────────────────────────────────────────────────────────
    ("Weekend Review",      "0 9 * * 6",      "trading-weekend-review.yml"),
    ("HVF Weekend Report",  "0 9 * * 6",      "trading-hvf-report.yml"),
    ("COT Report",          "0 10 * * 6",     "trading-cot-report.yml"),  # after weekend review refreshes COT (09:00)
    # ── Sunday commodity pre-open (created on the new cron-job.org account 2026-06-07) ──
    ("Sunday Readiness Check",         "30 20 * * 0", "trading-sunday-readiness.yml"),
    ("Sunday Pre-Open Commodity Scan", "0 22 * * 0",  "trading-premarket-brief.yml"),
]


def get_existing_jobs():
    resp = requests.get(
        f"{CRONJOB_API}/jobs",
        headers={"Authorization": f"Bearer {CRONJOB_API_KEY}"},
        timeout=15
    )
    resp.raise_for_status()
    return {j["title"]: j["jobId"] for j in resp.json().get("jobs", [])}


def _cron_to_schedule(cron: str) -> dict:
    """
    Convert a 5-field cron expression to cron-job.org's schedule object.

    cron-job.org's API does NOT accept a raw cron string — it needs explicit
    integer arrays for minutes/hours/mdays/months/wdays ([-1] means "every").
    Supports single values, '*', comma lists, ranges (a-b) and steps (*/n).
    wdays use 0=Sunday..6=Saturday (same as cron). Verified against the live
    cron-job.org API 2026-06-07 (the old cronExpression payload returned HTTP 500).
    """
    minute, hour, mday, month, wday = cron.split()

    def parse(field: str, lo: int, hi: int) -> list:
        if field == "*":
            return [-1]
        vals = set()
        for part in field.split(","):
            step = 1
            base = part
            if "/" in part:
                base, s = part.split("/"); step = int(s)
            if base == "*":
                start, end = lo, hi
            elif "-" in base:
                a, b = base.split("-"); start, end = int(a), int(b)
            else:
                start = end = int(base)
            vals.update(range(start, end + 1, step))
        return sorted(vals)

    return {
        "timezone":  "UTC",
        "expiresAt": 0,
        "minutes":   parse(minute, 0, 59),
        "hours":     parse(hour,   0, 23),
        "mdays":     parse(mday,   1, 31),
        "months":    parse(month,  1, 12),
        "wdays":     parse(wday,   0, 6),
    }


def create_job(title: str, cron: str, workflow: str):
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/actions/workflows/{workflow}/dispatches"
    payload = {
        "url":           url,
        "enabled":       True,
        "title":         title,
        "saveResponses": False,
        "schedule":      _cron_to_schedule(cron),
        "requestMethod": 1,          # 1 = POST
        "extendedData": {
            # cron-job.org expects headers as an OBJECT, not a list of name/value
            "headers": {
                "Authorization":        f"Bearer {GITHUB_TOKEN}",
                "Accept":               "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type":         "application/json",
            },
            "body": '{"ref":"main"}',
        },
    }
    resp = requests.put(
        f"{CRONJOB_API}/jobs",
        headers={"Authorization": f"Bearer {CRONJOB_API_KEY}",
                 "Content-Type": "application/json"},
        json={"job": payload},
        timeout=15
    )
    resp.raise_for_status()
    return resp.json().get("jobId")


def patch_schedule(job_id: int, cron: str):
    """
    Update ONLY the schedule of an existing job (PATCH = partial update).

    Leaves the URL and stored auth header untouched, so retuning frequency needs
    only CRONJOB_API_KEY — never the GitHub PAT (which would be required to
    recreate the job's auth header). This is how we safely change live cadence.
    """
    resp = requests.patch(
        f"{CRONJOB_API}/jobs/{job_id}",
        headers={"Authorization": f"Bearer {CRONJOB_API_KEY}",
                 "Content-Type": "application/json"},
        json={"job": {"schedule": _cron_to_schedule(cron)}},
        timeout=15
    )
    resp.raise_for_status()


def reconcile_schedules():
    """
    Retune EXISTING jobs' schedules to match JOBS. Creates nothing and touches no
    auth — only the schedule. Idempotent (PATCH-ing the same value is harmless).
    """
    existing = get_existing_jobs()
    print(f"Existing jobs on account: {len(existing)}")
    updated = missing = failed = 0
    for title, cron, workflow in JOBS:
        job_id = existing.get(title)
        if not job_id:
            print(f"  MISSING (not on account — use create mode): {title}")
            missing += 1
            continue
        try:
            patch_schedule(job_id, cron)
            print(f"  UPDATED: {title}  ->  [{cron}]  (id={job_id})")
            updated += 1
        except Exception as e:
            print(f"  FAIL: {title} — {e}")
            failed += 1
    print()
    print(f"Reconcile done: {updated} updated, {missing} missing, {failed} failed")
    if failed:
        raise SystemExit(1)


def create_missing_jobs():
    """
    Create only JOBS entries not yet on cron-job.org. Skips existing jobs.
    Safe to re-run. Requires CRONJOB_API_KEY + GITHUB_TOKEN (PAT).
    """
    if not GITHUB_TOKEN:
        print("ERROR: --create-missing needs GITHUB_TOKEN (PAT). "
              "Set GH_PAT secret or pass GITHUB_TOKEN env var.")
        raise SystemExit(1)

    existing = get_existing_jobs()
    print(f"Existing jobs on account: {len(existing)}")

    created = skipped = failed = 0
    for title, cron, workflow in JOBS:
        if title in existing:
            print(f"  SKIP (exists): {title}")
            skipped += 1
            continue
        try:
            job_id = create_job(title, cron, workflow)
            print(f"  CREATED: {title}  [{cron}]  → {workflow}  (id={job_id})")
            created += 1
        except Exception as e:
            print(f"  FAIL: {title} — {e}")
            failed += 1

    print()
    print(f"Done: {created} created, {skipped} skipped, {failed} failed")
    if failed:
        raise SystemExit(1)


def main():
    # --reconcile: only retune schedules of existing jobs (no GitHub PAT needed).
    if "--reconcile" in sys.argv:
        print("Reconciling cron-job.org SCHEDULES to setup_cronjobs.JOBS (frequency retune)...")
        print(f"GitHub repo: {GITHUB_REPO}")
        print()
        reconcile_schedules()
        return

    # --create-missing: create any JOBS not yet on cron-job.org. Skips existing.
    if "--create-missing" in sys.argv:
        print("Creating missing cron-job.org jobs...")
        print(f"GitHub repo: {GITHUB_REPO}")
        print()
        create_missing_jobs()
        return

    if not GITHUB_TOKEN:
        print("ERROR: create mode needs GITHUB_TOKEN. To only retune existing "
              "schedules, run: python setup_cronjobs.py --reconcile")
        raise SystemExit(1)
    print(f"Setting up {len(JOBS)} cron-job.org jobs for EndToEndTrading...")
    print(f"GitHub repo: {GITHUB_REPO}")
    print()

    existing = get_existing_jobs()
    print(f"Existing jobs on account: {len(existing)}")

    created = skipped = failed = 0
    for title, cron, workflow in JOBS:
        if title in existing:
            print(f"  SKIP (exists): {title}")
            skipped += 1
            continue
        try:
            job_id = create_job(title, cron, workflow)
            print(f"  CREATED: {title}  [{cron}]  → {workflow}  (id={job_id})")
            created += 1
        except Exception as e:
            print(f"  FAIL: {title} — {e}")
            failed += 1

    print()
    print(f"Done: {created} created, {skipped} skipped, {failed} failed")
    if failed == 0:
        print("All jobs active. EndToEndTrading is now fully scheduled remotely.")


if __name__ == "__main__":
    main()
