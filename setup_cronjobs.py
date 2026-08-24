# ======================================================================================================================
# File:         setup_cronjobs.py
# Author:       Alex Hind
# Created:      2026-06-05
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
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
# ----------------------------------------------------------------------------------------------------------------------
# 1.17.0  2026-08-17  Claude      Add the weekly "GH PAT Expiry Check" job (0 7 * * 0 -> trading-pat-check.yml) and a
#                                 --alert flag on --check-token that posts to #alerts through notify (so the global
#                                 per-channel Slack switch still applies). Detection, not just prevention: 1.16.0 stops
#                                 a bad token being WRITTEN, but the token can expire long after it was written and a
#                                 rejected dispatch creates no Actions run, so the failure is an absence rather than a
#                                 red build. That is why 2026-06-10 went unnoticed until 2026-08-17.
# 1.16.0  2026-08-17  Claude      Add --check-token (read-only) and gate --repair/--create-missing on it. The 1.15.0
#                                 repair wrote GH_PAT into six jobs without checking it first; the PAT had expired on
#                                 its 60-day clock, so all six returned HTTP 401 at their next fire (HVF Orders 06:00,
#                                 Order Bridge 08:00, UK HVF Watch 08:30) and the morning's automation silently did
#                                 nothing. Any mode that BAKES the token into a job's auth header now validates it
#                                 first and refuses rather than shipping a dud.
# 1.15.0  2026-08-15  Claude      Add --repair "<titles>" mode. A --status check found FIVE jobs that cron-job.org had
#                                 auto-disabled after GitHub returned an HTTP error (401/404) on dispatch — HVF Orders
#                                 (dead since 2026-07-21), Data Quality Audit and Pre-Order Report (2026-07-27), UK and
#                                 US HVF Watch (2026-06-30) — most likely still pointing at workflow filenames that were
#                                 later renamed or split (cf. the 1.14.0 Price Data Refresh change). Neither --reconcile
#                                 (schedule only) nor --prune (deletes jobs NOT in JOBS) could fix them. --repair PATCHes
#                                 the URL, auth header, body and schedule from JOBS and sets enabled=True. Deliberately
#                                 TITLE-SCOPED: most other OFF jobs on the account are off on purpose, and a blanket
#                                 re-enable would restart live trading automation nobody asked for (user 2026-08-15).
# 1.14.0  2026-08-07  Claude      (ChangeRequest P-08, "the job names look peculiar") "Price Data Refresh" moved off
#                                 trading-price-audit.yml onto its own trading-price-refresh.yml — it was sharing a
#                                 workflow file with "Price History Audit" (different schedules, same script), which
#                                 made the two show byte-identical GitHub Actions run stats in the Scheduled Jobs tab
#                                 (hvf_web/scheduled_jobs.py caches stats per workflow FILE, not per cron-job.org job).
# 1.13.0  2026-08-04  Alex Hind   Move "Price Data Refresh" from 05:00 to 04:30 UTC Mon-Sat, retaining a full hour
#                                 before the 05:30 HVF Daily Report (user 2026-08-04, ToDo P-02).
# 1.12.0  2026-06-19  Alex Hind   Add "HVF Orders" job (0 6 * * 1-6 -> trading-hvf-orders.yml): daily actionable HVF setups
#                                 to #arw-claude-orders, before 07:00 UTC (8am BST) (user 2026-06-19).
# 1.11.0  2026-06-19  Alex Hind   "before 8am" (user 2026-06-19): HVF Daily Report moved to 05:30 UTC Mon-SAT (was 07:00
#                                 Mon-Fri) so all morning publications (report + X drafts + live-X) finish before 07:00
#                                 UTC = 8am BST. Removed the separate 09:00 Sat "HVF Weekend Report" (now covered Mon-Sat).
# 1.10.0  2026-06-16  Alex Hind   Add --prune mode (delete cron-job.org jobs no longer in JOBS, scoped to THIS repo's
#                                 workflow dispatches — needs only CRONJOB_API_KEY). Removed "HVF Quality Reports" +
#                                 "HVF Quality Reports Wknd": the long report now rides with every publication
#                                 (intraday_signals._generate_x_drafts), so a standalone quality job double-posts.
# 1.9.0   2026-06-16  Alex Hind   Add "Pre-Order Report" job (45 21 Mon-Fri → trading-working-orders-report.yml): daily
#                                 report of the engine-managed working_orders to #arw-claude-orders, after the 21:30 Daily
#                                 Report (user 2026-06-16). Deploy with --create-missing.
# 1.0.0   2026-06-05  Alex Hind   Initial build — all session/report cron jobs.
# 1.7.0   2026-06-11  Alex Hind   Add "UK HVF Watch" job (30 8,10,12,14 Mon-Fri → trading-uk-hvf-watch.yml). Mirrors US
#                                 HVF Watch cadence for UK/FTSE250 instruments.
# 1.8.0   2026-06-14  Alex Hind   Add "HVF Quality Reports" (45 7 Mon-Fri) + weekend (45 9 Sat) → trading-quality-reports.yml,
#                                 right after each HVF scan populates hvf_scan_log; runs --daily (publish only changed).
# 1.6.0   2026-06-10  Alex Hind   --create-missing mode: creates only JOBS not yet on cron-job.org (skips existing).
#                                 Safe to re-run. Workflow updated to expose mode input (reconcile / create-missing) and
#                                 pass GH_PAT as GITHUB_TOKEN.
# 1.5.0   2026-06-10  Alex Hind   Add "US HVF Watch" job (30 14,16,18,20 Mon-Fri → trading-us-hvf-watch.yml). HVF watch
#                                 decoupled from 30-min US Monitor; runs 2-hourly with dedup.
# 1.4.0   2026-06-09  Alex Hind   Increased monitoring frequency (user: "best information", GitHub-cron limit no longer
#                                 applies): session monitors */20->*/5, commodity */30->*/10, watchdog */30->*/10,
#                                 social hourly->*/15. Added a --reconcile mode that PATCHes only the schedule of
#                                 EXISTING jobs (preserves each job's stored auth, so retuning frequency needs only
#                                 CRONJOB_API_KEY, no GitHub PAT). Deployed via trading-setup-cronjobs.yml.
# 1.3.0   2026-06-08  Alex Hind   Consolidate JOBS to the authoritative schedule and reflect the live cron-job.org
#                                 account: monitors are now single */N-step jobs (AUS/UK/US Monitor) instead of ~70
#                                 per-slot jobs (which would have created duplicates on re-run). Migrated the watchdog +
#                                 commodity-monitor off GitHub cron onto cron-job.org. Added a proactive "Daily
#                                 Diagnostics" job (07:30 Mon-Fri) so deployment/stack health is checked daily without
#                                 prompting. Matches what is live on the user-owned account (created 2026-06-08).
# 1.2.0   2026-06-07  Alex Hind   Fix create_job payload for the real cron-job.org API — it sent cronExpression/exprType
#                                 and headers as a list, which the API rejects with HTTP 500 (the script had never
#                                 worked). Now builds explicit minutes/hours/mdays/months/wdays arrays via
#                                 _cron_to_schedule() and headers as an object. Verified live: created COT + 2 Sunday
#                                 jobs. Added the Sunday jobs. ⚠ Running the FULL script populates ALL jobs — only do so
#                                 against a sole cron-job.org account, else it double-fires with the old (lost-access)
#                                 account that still holds weekday jobs.
# 1.1.0   2026-06-07  Alex Hind   Add "COT Report" job (Sat 10:00 UTC → trading-cot-report.yml), scheduled after the
#                                 weekend review (09:00) refreshes COT data.
# ======================================================================================================================

import os
import sys
import json
import requests

CRONJOB_API_KEY = os.environ.get("CRONJOB_API_KEY", "")
GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO     = "Red7Al/ClaudeCode"

# CRONJOB_API_KEY is required for every mode that TOUCHES cron-job.org. GITHUB_TOKEN is only
# needed to CREATE or REPAIR jobs (it is baked into the job's auth header); --reconcile (retune
# schedules of existing jobs) and --prune (delete this-repo jobs no longer in JOBS) do not need it.
# --check-token is the sole exception in the other direction: it never calls cron-job.org at all,
# only GitHub, so requiring the cron key would stop the workflow validating a freshly rotated PAT.
if not CRONJOB_API_KEY and "--check-token" not in sys.argv:
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

# ----------------------------------------------------------------------------------------------------------------------
# Jobs to create
# Each entry: (title, cron_expression, workflow_file)
# cron_expression uses UTC (cron-job.org default is UTC)
# ----------------------------------------------------------------------------------------------------------------------
JOBS = [
    # ── Asia / AUS session ────────────────────────────────────────────────────────────────────────────────────────────
    ("AUS Open",            "0 0 * * 1-5",    "trading-aus-open.yml"),
    ("AUS Monitor",          "*/5 0-6 * * 1-5", "trading-aus-monitor.yml"),
    ("AUS HVF Watch",        "30 0,2,4 * * 1-5", "trading-aus-hvf-watch.yml"),
    ("Commodity Monitor AM", "*/10 4-8 * * 1-5", "trading-commodity-monitor.yml"),
    # ── Pre-UK ────────────────────────────────────────────────────────────────────────────────────────────────────────
    ("Price Data Refresh",  "30 4 * * 1-6",   "trading-price-refresh.yml"),  # 04:30 UTC Mon-Sat — refresh price_history BEFORE the 05:30 HVF Daily Report, which needs current bars (user 2026-08-04, ToDo P-02). Own workflow file since 2026-08-07 (ChangeRequest P-08 "job names look peculiar") — was sharing trading-price-audit.yml with the 23:00 "Price History Audit" job, which made the two show byte-identical run stats in the Scheduled Jobs tab (hvf_web/scheduled_jobs.py caches GitHub Actions stats per WORKFLOW FILE); splitting the file gives each job its own genuine history. Same script (re-fetch trailing window + upsert; idempotent).
    ("HVF Daily Report",    "30 5 * * 1-6",   "trading-hvf-report.yml"),  # 05:30 UTC Mon-Sat -> all publications (report + X drafts + live-X) done before 07:00 UTC (8am BST) (user 2026-06-19)
    ("Best Settings Full-grid Audit", "45 5 * * 1-6", "trading-best-settings-audit.yml"),
    # Precompute the /api/winners payloads (user 2026-08-23). One window costs about 33 seconds to build
    # and Best Settings requests two on every visit, so a cold worker made the first visitor wait for both.
    # 05:50, after the 04:30 price refresh and the 05:30 report, so it builds from the day's refreshed
    # history. The server ignores a stored payload built from a different scan or older than a day, so a
    # missed run degrades to the live build rather than serving stale figures.
    ("Winners Precompute",  "50 5 * * 1-6",   "trading-winners-precompute.yml"),
    ("HVF Orders",          "0 6 * * 1-6",    "trading-hvf-orders.yml"),  # 06:00 UTC Mon-Sat -> actionable HVF setups to #arw-claude-orders, before 07:00 UTC (8am BST) (user 2026-06-19)
    # Snapshot pre-orders -> IG working orders. Restored to a scheduler 2026-08-15 (user: "order bridge
    # must run"): it used to be a 2-hourly background thread in hvf_web/server.py started from __main__,
    # so it died silently when the site moved to IONOS (CGI/WSGI never runs __main__). One scheduled job
    # is deliberately the SINGLE owner — IONOS_DEPLOYMENT.md warns that a loop in every WSGI worker would
    # risk duplicate placement. 06:00-22:00 UTC Mon-Fri at the original 2h cadence (user choice: no
    # overnight AUS cover; those setups wait for the 06:00 pass).
    ("Order Bridge",        "0 6-22/2 * * 1-5", "trading-order-bridge.yml"),
    # "HVF Quality Reports" removed 2026-06-16: the long quality report now rides with EVERY
    # publication (intraday_signals._generate_x_drafts -> quality_report.publish_long_report_for),
    # so a separate quality-only job would double-post AND is an incomplete publication on its own
    # (no card/short tweet). Removed here and deleted from cron-job.org via --prune.
    # ── UK session ────────────────────────────────────────────────────────────────────────────────────────────────────
    ("UK Open",             "0 8 * * 1-5",    "trading-uk-open.yml"),
    ("UK Morning Brief",    "0 9 * * 1,5",    "trading-uk-morning-brief.yml"),
    ("UK Monitor",           "*/5 8-16 * * 1-5","trading-uk-monitor.yml"),
    # ── US session ────────────────────────────────────────────────────────────────────────────────────────────────────
    ("US Open",              "30 14 * * 1-5",    "trading-us-open.yml"),
    ("US Monitor",           "*/5 14-21 * * 1-5","trading-us-monitor.yml"),
    ("US HVF Watch",         "30 14,16,18,20 * * 1-5", "trading-us-hvf-watch.yml"),
    ("UK HVF Watch",         "30 8,10,12,14 * * 1-5",  "trading-uk-hvf-watch.yml"),
    ("Scanner Snapshot Refresh", "30 18 * * *", "trading-scanner-snapshot.yml"),  # external full rebuild + private Supabase publication; IONOS remains the thin web tier
    ("Social Monitor",       "*/15 7-22 * * 1-5",   "trading-social-monitor.yml"),
    # ── Close & reports ───────────────────────────────────────────────────────────────────────────────────────────────
    ("Commodity Monitor PM", "*/10 21-23 * * 1-5","trading-commodity-monitor.yml"),
    ("Session Close",        "0 21 * * 1-5",     "trading-session-close.yml"),
    ("Daily Report",         "30 21 * * 1-5",    "trading-daily-report.yml"),
    ("Pre-Order Report",     "45 21 * * 1-5",    "trading-working-orders-report.yml"),  # engine-managed working_orders -> #arw-claude-orders (user 2026-06-16)
    ("Data Quality Audit",   "15 22 * * 1-5",    "trading-data-quality.yml"),  # Yahoo-vs-IG nightly audit (2026-06-12)
    ("Price History Audit",  "0 23 * * 1-6",     "trading-price-audit.yml"),  # golden-dataset audit: YF refetch + IG-as-truth correction of the trailing 7d (user 2026-07-13). Mon-Sat 23:00 UTC, after Data Quality Audit; self-throttles on the shared IG allowance.
    ("Supabase Database Backup", "30 23 * * *", "supabase-backup.yml"),  # daily read-only logical backup; artifact retained 90 days (user 2026-08-06, P-25)
    # ── Safety net + proactive self-checks ────────────────────────────────────────────────────────────────────────────
    ("Session Watchdog",     "*/10 0-21 * * 1-5","trading-watchdog.yml"),     # migrated off GitHub cron 2026-06-08
    ("Daily Diagnostics",    "30 7 * * 1-5",     "trading-diagnostics.yml"),  # proactive daily health check -> #alerts
    # ── Weekend ───────────────────────────────────────────────────────────────────────────────────────────────────────
    ("Weekend Review",      "0 9 * * 6",      "trading-weekend-review.yml"),
    # "HVF Weekend Report" removed 2026-06-19 — the HVF Daily Report now runs Mon-SAT at 05:30 UTC
    # (covers Saturday), so a separate 09:00 Sat report is redundant.
    # "HVF Quality Reports Wknd" removed 2026-06-16 — long report now rides with publications (see above).
    ("COT Report",          "0 10 * * 6",     "trading-cot-report.yml"),  # after weekend review refreshes COT (09:00)
    # ── Sunday commodity pre-open (created on the new cron-job.org account 2026-06-07) ──
    ("Sunday Readiness Check",         "30 20 * * 0", "trading-sunday-readiness.yml"),
    ("Sunday Pre-Open Commodity Scan", "0 22 * * 0",  "trading-premarket-brief.yml"),
    # ── Housekeeping ──────────────────────────────────────────────────────────────────────────────────
    # Weekly PAT health check (2026-08-17). Every job above dispatches GitHub Actions with GH_PAT baked
    # into its Authorization header, so when that token expires they ALL stop — silently, because a
    # failed dispatch produces no Actions run to notice the absence of. The 2026-06-10 expiry went
    # unseen for eight weeks. Sunday 07:00 UTC: a fortnight of Mondays' warning before anything is due
    # to trade, and it fires on the quietest day so a red alert means the token and nothing else.
    ("GH PAT Expiry Check",            "0 7 * * 0",   "trading-pat-check.yml"),
    # Weekly index-health review (2026-08-17, user: "also review this weekly to see what is required").
    # Read-only: duplicate indexes, unused indexes, uncovered foreign keys. Silent on a clean week.
    # 08:00 Sunday, an hour behind the PAT check so the two never contend for the same runner minute.
    # Let Winners Run removes the IG take-profit, so an open winner is protected only while the stop
    # manager keeps running. Its own job, NOT part of the Order Bridge: a watchdog inside the thing it
    # watches is useless when that thing is what failed. Hourly on weekdays, covering the two-hourly
    # bridge; silent unless positions were actually relying on a pass that did not happen.
    ("Let Winners Run Watchdog", "20 7-21 * * 1-5", "trading-lwr-watchdog.yml"),
    ("DB Index Audit",                 "0 8 * * 0",   "trading-db-index-audit.yml"),
    # Resolve ticker -> GICS sector for anything new in the universe (user 2026-08-22). sector_cache
    # shipped its backfill on 2026-07-17 but nothing ever scheduled it, so the cache only grew when
    # somebody remembered: the full S&P 500 expansion left 288 of 402 new constituents with no sector,
    # against 98% coverage for the instruments already there. 06:00 Sunday, clear of the 08:00 index
    # audit. Additive, resumable and skips already-resolved tickers, so a normal week is cheap.
    ("Sector Cache Backfill",          "0 6 * * 0",   "trading-sector-backfill.yml"),
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


def _job_detail(job_id: int) -> dict:
    """Full detail for one job (the list endpoint omits the target URL)."""
    resp = requests.get(
        f"{CRONJOB_API}/jobs/{job_id}",
        headers={"Authorization": f"Bearer {CRONJOB_API_KEY}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("jobDetails", {})


def delete_job(job_id: int):
    resp = requests.delete(
        f"{CRONJOB_API}/jobs/{job_id}",
        headers={"Authorization": f"Bearer {CRONJOB_API_KEY}"},
        timeout=15,
    )
    resp.raise_for_status()


def prune_jobs():
    """Delete cron-job.org jobs this script no longer manages — SAFELY scoped (added 2026-06-16).

    A job is pruned ONLY when BOTH hold: (1) its title is not in JOBS, and (2) its target URL
    points at THIS repo's workflow dispatches (`/repos/<repo>/actions/workflows/...`). So it can
    only ever remove jobs this script created for this repo that are no longer wanted (e.g. the
    retired "HVF Quality Reports"); unrelated cron-job.org jobs are never touched. Needs only
    CRONJOB_API_KEY (no GitHub PAT). The URL is read from each candidate's detail endpoint.
    """
    managed = {t for t, _, _ in JOBS}
    marker = f"/repos/{GITHUB_REPO}/actions/workflows/"
    resp = requests.get(
        f"{CRONJOB_API}/jobs",
        headers={"Authorization": f"Bearer {CRONJOB_API_KEY}"},
        timeout=15,
    )
    resp.raise_for_status()
    jobs = resp.json().get("jobs", [])
    print(f"Existing jobs on account: {len(jobs)}")
    deleted = kept = skipped = failed = 0
    for j in jobs:
        title = j.get("title", "")
        if title in managed:
            kept += 1
            continue
        try:
            url = _job_detail(j["jobId"]).get("url", "") or ""
        except Exception as e:
            print(f"  SKIP (detail fetch failed): {title} — {e}")
            skipped += 1
            continue
        if marker not in url:
            print(f"  SKIP (not this repo's dispatch): {title}")
            skipped += 1
            continue
        try:
            delete_job(j["jobId"])
            print(f"  PRUNED: {title}  (id={j['jobId']})  -> {url}")
            deleted += 1
        except Exception as e:
            print(f"  FAIL pruning {title} — {e}")
            failed += 1
    print()
    print(f"Prune done: {deleted} deleted, {kept} kept (managed), {skipped} skipped, {failed} failed")
    if failed:
        raise SystemExit(1)


# cron-job.org lastStatus codes (from their API docs).
_STATUS_LABELS = {0: "not yet run", 1: "OK", 2: "failed: DNS", 3: "failed: could not connect",
                  4: "failed: HTTP error (e.g. 401/404 from GitHub)", 5: "failed: timeout",
                  6: "failed: too much response data", 7: "failed: invalid URL",
                  8: "failed: internal error", 9: "failed: unknown"}


def report_status():
    """Diagnostic (2026-07-25, P-02): list every job with enabled state + last-execution status, and the
    exact HTTP status for any that failed. A '4 / HTTP 401' on the audit jobs confirms an expired embedded
    GitHub PAT (dispatch rejected, so no Actions run is created). Needs only CRONJOB_API_KEY."""
    import datetime as _dt
    resp = requests.get(f"{CRONJOB_API}/jobs",
                        headers={"Authorization": f"Bearer {CRONJOB_API_KEY}"}, timeout=15)
    resp.raise_for_status()
    jobs = resp.json().get("jobs", [])
    print(f"Existing jobs on account: {len(jobs)}\n")
    for j in sorted(jobs, key=lambda x: x.get("title", "")):
        jid, title = j.get("jobId"), j.get("title", "")
        d = j if ("lastStatus" in j and "enabled" in j) else (lambda: (lambda x: x)(_safe_detail(jid)))()
        enabled = d.get("enabled")
        ls = d.get("lastStatus")
        le = d.get("lastExecution")
        letxt = "—"
        if le:
            try:
                letxt = _dt.datetime.utcfromtimestamp(int(le)).strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                letxt = str(le)
        http = ""
        if ls not in (None, 0, 1):                       # failed — pull the exact HTTP status from history
            try:
                h = requests.get(f"{CRONJOB_API}/jobs/{jid}/history",
                                 headers={"Authorization": f"Bearer {CRONJOB_API_KEY}"}, timeout=15)
                hist = (h.json() or {}).get("history", []) if h.ok else []
                if hist:
                    http = f"  HTTP {hist[0].get('httpStatus')}"
            except Exception:
                pass
        flag = "ON " if enabled else "OFF"
        print(f"  [{flag}] last={_STATUS_LABELS.get(ls, ls)}{http} @ {letxt}  |  {title} (id={jid})")


def _safe_detail(job_id):
    try:
        return _job_detail(job_id)
    except Exception:
        return {}


def check_github_token() -> bool:
    """Can GITHUB_TOKEN actually dispatch a workflow? Read-only -- asks GitHub for the workflow list.

    Added 2026-08-17 after a real failure. --repair and --create-missing BAKE this token into each
    cron-job.org job's Authorization header, so an expired one is written into every job they touch and
    the jobs then fail silently at their next scheduled time -- which is exactly what happened: GH_PAT had
    quietly expired on its 60-day clock, six jobs were repaired with it, and every one returned HTTP 401
    hours later with nothing to show for it until someone read the cron-job.org status page.

    Cheap to check, expensive to skip: a bad token is invisible until the next scheduled fire.

    LIMIT: this proves the token is live and can SEE the repo's Actions (read scope). Dispatching
    additionally needs Actions: WRITE, which cannot be probed without actually firing a workflow --
    and firing one here would place real orders. So a 200 rules out the expiry/revocation case that
    bit us, not a mis-scoped PAT. If jobs still 403 after this passes, the PAT is read-only.
    """
    if not GITHUB_TOKEN:
        print("  GITHUB_TOKEN is empty — nothing to check.")
        return False
    try:
        resp = requests.get(f"{GITHUB_API}/repos/{GITHUB_REPO}/actions/workflows",
                            headers=GITHUB_HEADERS, timeout=15)
    except Exception as exc:
        print(f"  token check could not reach GitHub: {exc}")
        return False
    if resp.status_code == 200:
        print(f"  token OK — can read {GITHUB_REPO} workflows (HTTP 200)")
        return True
    if resp.status_code == 401:
        print("  token REJECTED (HTTP 401) — expired, revoked, or not a valid PAT.")
    elif resp.status_code == 403:
        print("  token lacks permission (HTTP 403) — needs Actions: Read and write on this repo.")
    elif resp.status_code == 404:
        print(f"  token cannot see {GITHUB_REPO} (HTTP 404) — check the repository is selected on the PAT.")
    else:
        print(f"  token check returned HTTP {resp.status_code}")
    return False


def repair_jobs(titles):
    """Re-point and re-enable SPECIFIC jobs by title, then switch them back on.

    cron-job.org disables a job after repeated delivery failures. Five jobs on this account died with a
    GitHub HTTP error (401/404) between 2026-06-30 and 2026-07-27 and stayed off ever since - most likely
    still pointing at a workflow filename that was later renamed or split (see the Price Data Refresh note
    on the JOBS entry above for exactly that kind of change). This rewrites the job's URL, auth header,
    request body and schedule from JOBS, and sets enabled=True.

    TITLE-SCOPED ON PURPOSE - this must never become a blanket sweep. Most of the OFF jobs on this account
    (the UK/US/AUS Open and Monitor jobs, Session Close, Daily Report, the Sunday jobs) are off
    DELIBERATELY, and re-enabling them wholesale would restart live trading automation nobody asked for.
    Name the jobs you mean. Needs CRONJOB_API_KEY + GITHUB_TOKEN (the PAT is re-embedded in the header).
    """
    wanted = [t.strip() for t in titles if t.strip()]
    by_title = {title: (cron, workflow) for title, cron, workflow in JOBS}
    unknown = [t for t in wanted if t not in by_title]
    if unknown:
        print(f"  ERROR: not in JOBS, refusing to guess: {unknown}")
        return 1
    existing = get_existing_jobs()
    repaired = failed = 0
    for title in wanted:
        cron, workflow = by_title[title]
        job_id = existing.get(title)
        if not job_id:
            print(f"  SKIP (not on cron-job.org - use --create-missing): {title}")
            continue
        url = f"{GITHUB_API}/repos/{GITHUB_REPO}/actions/workflows/{workflow}/dispatches"
        payload = {
            "url":      url,
            "enabled":  True,
            "schedule": _cron_to_schedule(cron),
            "requestMethod": 1,
            "extendedData": {
                "headers": {
                    "Authorization":        f"Bearer {GITHUB_TOKEN}",
                    "Accept":               "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "Content-Type":         "application/json",
                },
                "body": '{"ref":"main"}',
            },
        }
        try:
            resp = requests.patch(
                f"{CRONJOB_API}/jobs/{job_id}",
                headers={"Authorization": f"Bearer {CRONJOB_API_KEY}",
                         "Content-Type": "application/json"},
                json={"job": payload},
                timeout=15,
            )
            resp.raise_for_status()
            print(f"  REPAIRED + ENABLED: {title}  [{cron}]  -> {workflow}  (id={job_id})")
            repaired += 1
        except Exception as e:
            print(f"  FAIL: {title} - {e}")
            failed += 1
    print()
    print(f"Done: {repaired} repaired, {failed} failed")
    return 1 if failed else 0


def main():
    # --status: read-only diagnostic of every job's enabled/last-execution state (no GitHub PAT needed).
    if "--status" in sys.argv:
        print("cron-job.org job status:\n")
        report_status()
        return

    # --reconcile: only retune schedules of existing jobs (no GitHub PAT needed).
    if "--reconcile" in sys.argv:
        print("Reconciling cron-job.org SCHEDULES to setup_cronjobs.JOBS (frequency retune)...")
        print(f"GitHub repo: {GITHUB_REPO}")
        print()
        reconcile_schedules()
        return

    # --check-token: read-only PAT health check. Run after every GH_PAT rotation, BEFORE any mode
    # that embeds the token. Needs no cron-job.org key. With --alert it also posts to #alerts on
    # failure, which is how the weekly "GH PAT Expiry Check" job earns its place: the 2026-06-10
    # expiry went unnoticed for EIGHT WEEKS, by which time Data Quality Audit and Pre-Order Report
    # had been dead since 27 July and US HVF Watch since 30 June. Nobody reads a green cron page.
    if "--check-token" in sys.argv:
        print("Checking GITHUB_TOKEN can reach the repo...")
        ok = check_github_token()
        if not ok and "--alert" in sys.argv:
            try:
                import notify
                notify.alert_system_error(
                    session="Scheduled",
                    component="GH_PAT (GitHub personal access token)",
                    summary="GH_PAT can no longer reach the repo. Every cron-job.org job dispatches "
                            "GitHub Actions with this token, so they will fail silently at their next "
                            "scheduled run until it is renewed.",
                    detail="Renew at https://github.com/settings/personal-access-tokens (fine-grained, "
                           "Red7Al/ClaudeCode, Actions: Read and write), save it as the GH_PAT repo "
                           "secret, then run trading-setup-cronjobs.yml with mode=check-token to "
                           "confirm and mode=repair to re-point the jobs.")
            except Exception as exc:                       # never let alerting mask the exit code
                print(f"  (could not post the alert: {exc})")
        raise SystemExit(0 if ok else 1)

    # --create-missing: create any JOBS not yet on cron-job.org. Skips existing.
    if "--create-missing" in sys.argv:
        print("Creating missing cron-job.org jobs...")
        print("Validating GITHUB_TOKEN before embedding it...")
        if not check_github_token():
            print("REFUSING to create: the new job would carry a bad token and fail at its first run.")
            raise SystemExit(1)
        print(f"GitHub repo: {GITHUB_REPO}")
        print()
        create_missing_jobs()
        return

    # --repair "Job A,Job B": re-point + re-enable named jobs that cron-job.org disabled after delivery
    # failures. Title-scoped deliberately - see repair_jobs(). Needs CRONJOB_API_KEY + GITHUB_TOKEN.
    if "--repair" in sys.argv:
        idx = sys.argv.index("--repair")
        names = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else ""
        titles = [t for t in names.split(",") if t.strip()]
        if not titles:
            print('ERROR: --repair needs a comma-separated list of job titles, e.g.\n'
                  '  python setup_cronjobs.py --repair "Data Quality Audit,HVF Orders"')
            raise SystemExit(1)
        if not GITHUB_TOKEN:
            print("ERROR: --repair rewrites the job's auth header, so it needs GITHUB_TOKEN.")
            raise SystemExit(1)
        print("Validating GITHUB_TOKEN before embedding it...")
        if not check_github_token():
            print("REFUSING to repair: a bad token would be written into every job named, and they would\n"
                  "fail silently at their next scheduled run. Renew the PAT and try again.")
            raise SystemExit(1)
        print(f"Repairing {len(titles)} cron-job.org job(s)...")
        print(f"GitHub repo: {GITHUB_REPO}")
        print()
        raise SystemExit(repair_jobs(titles))

    # --prune: delete this-repo jobs no longer in JOBS (safely scoped). Needs only CRONJOB_API_KEY.
    if "--prune" in sys.argv:
        print("Pruning cron-job.org jobs not in setup_cronjobs.JOBS (this repo's dispatches only)...")
        print(f"GitHub repo: {GITHUB_REPO}")
        print()
        prune_jobs()
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
