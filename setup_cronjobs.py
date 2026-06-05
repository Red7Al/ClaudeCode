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
# =============================================================================

import os
import json
import requests

CRONJOB_API_KEY = os.environ.get("CRONJOB_API_KEY", "")
GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO     = "Red7Al/ClaudeCode"

if not CRONJOB_API_KEY or not GITHUB_TOKEN:
    print("ERROR: Set CRONJOB_API_KEY and GITHUB_TOKEN environment variables")
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
    ("AUS Monitor (1)",     "20 0 * * 1-5",   "trading-aus-monitor.yml"),
    ("AUS Monitor (2)",     "40 0 * * 1-5",   "trading-aus-monitor.yml"),
    ("AUS Monitor (3)",     "0 1 * * 1-5",    "trading-aus-monitor.yml"),
    ("AUS Monitor (4)",     "20 1 * * 1-5",   "trading-aus-monitor.yml"),
    ("AUS Monitor (5)",     "40 1 * * 1-5",   "trading-aus-monitor.yml"),
    ("AUS Monitor (6)",     "0 2 * * 1-5",    "trading-aus-monitor.yml"),
    ("AUS Monitor (7)",     "20 2 * * 1-5",   "trading-aus-monitor.yml"),
    ("AUS Monitor (8)",     "40 2 * * 1-5",   "trading-aus-monitor.yml"),
    ("AUS Monitor (9)",     "0 3 * * 1-5",    "trading-aus-monitor.yml"),
    ("AUS Monitor (10)",    "20 3 * * 1-5",   "trading-aus-monitor.yml"),
    ("AUS Monitor (11)",    "40 3 * * 1-5",   "trading-aus-monitor.yml"),
    ("AUS Monitor (12)",    "0 4 * * 1-5",    "trading-aus-monitor.yml"),
    ("AUS Monitor (13)",    "20 4 * * 1-5",   "trading-aus-monitor.yml"),
    ("AUS Monitor (14)",    "40 4 * * 1-5",   "trading-aus-monitor.yml"),
    ("AUS Monitor (15)",    "0 5 * * 1-5",    "trading-aus-monitor.yml"),
    ("AUS Monitor (16)",    "20 5 * * 1-5",   "trading-aus-monitor.yml"),
    ("AUS Monitor (17)",    "40 5 * * 1-5",   "trading-aus-monitor.yml"),
    ("Commodity Monitor AM","0 4 * * 1-5",    "trading-commodity-monitor.yml"),
    ("Commodity Monitor AM2","30 5 * * 1-5",  "trading-commodity-monitor.yml"),
    # ── Pre-UK ──────────────────────────────────────────────────────────────
    ("HVF Daily Report",    "0 7 * * 1-5",    "trading-hvf-report.yml"),
    # ── UK session ──────────────────────────────────────────────────────────
    ("UK Open",             "0 8 * * 1-5",    "trading-uk-open.yml"),
    ("UK Morning Brief",    "0 9 * * 1,5",    "trading-uk-morning-brief.yml"),
    ("UK Monitor (1)",      "20 8 * * 1-5",   "trading-uk-monitor.yml"),
    ("UK Monitor (2)",      "40 8 * * 1-5",   "trading-uk-monitor.yml"),
    ("UK Monitor (3)",      "0 9 * * 1-5",    "trading-uk-monitor.yml"),
    ("UK Monitor (4)",      "20 9 * * 1-5",   "trading-uk-monitor.yml"),
    ("UK Monitor (5)",      "40 9 * * 1-5",   "trading-uk-monitor.yml"),
    ("UK Monitor (6)",      "0 10 * * 1-5",   "trading-uk-monitor.yml"),
    ("UK Monitor (7)",      "20 10 * * 1-5",  "trading-uk-monitor.yml"),
    ("UK Monitor (8)",      "40 10 * * 1-5",  "trading-uk-monitor.yml"),
    ("UK Monitor (9)",      "0 11 * * 1-5",   "trading-uk-monitor.yml"),
    ("UK Monitor (10)",     "20 11 * * 1-5",  "trading-uk-monitor.yml"),
    ("UK Monitor (11)",     "40 11 * * 1-5",  "trading-uk-monitor.yml"),
    ("UK Monitor (12)",     "0 12 * * 1-5",   "trading-uk-monitor.yml"),
    ("UK Monitor (13)",     "20 12 * * 1-5",  "trading-uk-monitor.yml"),
    ("UK Monitor (14)",     "40 12 * * 1-5",  "trading-uk-monitor.yml"),
    ("UK Monitor (15)",     "0 13 * * 1-5",   "trading-uk-monitor.yml"),
    ("UK Monitor (16)",     "20 13 * * 1-5",  "trading-uk-monitor.yml"),
    ("UK Monitor (17)",     "40 13 * * 1-5",  "trading-uk-monitor.yml"),
    ("UK Monitor (18)",     "0 14 * * 1-5",   "trading-uk-monitor.yml"),
    ("UK Monitor (19)",     "20 14 * * 1-5",  "trading-uk-monitor.yml"),
    ("UK Monitor (20)",     "40 14 * * 1-5",  "trading-uk-monitor.yml"),
    ("UK Monitor (21)",     "0 15 * * 1-5",   "trading-uk-monitor.yml"),
    ("UK Monitor (22)",     "20 15 * * 1-5",  "trading-uk-monitor.yml"),
    ("UK Monitor (23)",     "40 15 * * 1-5",  "trading-uk-monitor.yml"),
    ("UK Monitor (24)",     "0 16 * * 1-5",   "trading-uk-monitor.yml"),
    # ── US session ──────────────────────────────────────────────────────────
    ("US Open",             "30 14 * * 1-5",  "trading-us-open.yml"),
    ("Social Monitor",      "0 * * * 1-5",    "trading-social-monitor.yml"),
    ("US Monitor (1)",      "50 14 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (2)",      "10 15 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (3)",      "30 15 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (4)",      "50 15 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (5)",      "10 16 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (6)",      "30 16 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (7)",      "50 16 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (8)",      "10 17 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (9)",      "30 17 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (10)",     "50 17 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (11)",     "10 18 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (12)",     "30 18 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (13)",     "50 18 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (14)",     "10 19 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (15)",     "30 19 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (16)",     "50 19 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (17)",     "10 20 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (18)",     "30 20 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (19)",     "50 20 * * 1-5",  "trading-us-monitor.yml"),
    ("US Monitor (20)",     "10 21 * * 1-5",  "trading-us-monitor.yml"),
    # ── Close & reports ─────────────────────────────────────────────────────
    ("Commodity Monitor PM","0 21 * * 1-5",   "trading-commodity-monitor.yml"),
    ("Session Close",       "0 21 * * 1-5",   "trading-session-close.yml"),
    ("Daily Report",        "30 21 * * 1-5",  "trading-daily-report.yml"),
    # ── Weekend ─────────────────────────────────────────────────────────────
    ("Weekend Review",      "0 9 * * 6",      "trading-weekend-review.yml"),
    ("HVF Weekend Report",  "0 9 * * 6",      "trading-hvf-report.yml"),
]


def get_existing_jobs():
    resp = requests.get(
        f"{CRONJOB_API}/jobs",
        headers={"Authorization": f"Bearer {CRONJOB_API_KEY}"},
        timeout=15
    )
    resp.raise_for_status()
    return {j["title"]: j["jobId"] for j in resp.json().get("jobs", [])}


def create_job(title: str, cron: str, workflow: str):
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/actions/workflows/{workflow}/dispatches"
    payload = {
        "title": title,
        "url": url,
        "enabled": True,
        "saveResponses": False,
        "schedule": {
            "timezone": "UTC",
            "exprType": 0,           # 0 = standard cron expression
            "cronExpression": cron,
        },
        "requestMethod": 1,          # 1 = POST
        "extendedData": {
            "headers": [
                {"name": "Authorization",        "value": f"Bearer {GITHUB_TOKEN}"},
                {"name": "Accept",               "value": "application/vnd.github+json"},
                {"name": "X-GitHub-Api-Version", "value": "2022-11-28"},
                {"name": "Content-Type",         "value": "application/json"},
            ],
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
    job_id = resp.json().get("jobId")
    return job_id


def main():
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
