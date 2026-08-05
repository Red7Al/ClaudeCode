# ======================================================================================================================
# File:         hvf_web/scheduled_jobs.py
# Author:       Alex Hind
# Created:      2026-07-06
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Backs the admin-only "Scheduled Jobs" tab (user 2026-07-06). Definitions (title, cadence, category) come from the
# authoritative cron registry `setup_cronjobs.JOBS`; run stats (executions, failures, last status/time) come from the
# GitHub Actions API for each job's workflow file. Results are cached (default 30 min) so the tab is snappy and we
# don't hammer the API. Read-only; never raises to the caller (returns whatever it has + an 'error' note).
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-07-06  Alex Hind   Initial build — JOBS + cron-to-human + GitHub Actions run stats, 30-min cache.
# 1.1.0   2026-07-06  Alex Hind   (user 2026-07-06) trading_style per job (Multi-factor momentum / Squeeze / Support).
# ======================================================================================================================

import os
import time
import logging
import subprocess

log = logging.getLogger("hvf_web.scheduled_jobs")

GITHUB_REPO = "Red7Al/ClaudeCode"           # matches setup_cronjobs.GITHUB_REPO / the Actions host
_CACHE_TTL = 1800                            # 30 minutes
_cache = {"ts": 0.0, "data": None}
_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]   # cron wday 1..6,0(Sun) -> label


def _gh_token() -> str:
    """The gh CLI token (the web server runs on the operator's laptop where gh is logged in), falling
    back to GH_PAT / GITHUB_TOKEN env vars. Empty string if none — the caller then serves definitions
    only (no run stats)."""
    for env in ("GH_PAT", "GITHUB_TOKEN"):
        if os.environ.get(env):
            return os.environ[env]
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=8)
        return (out.stdout or "").strip()
    except Exception as e:
        log.warning(f"gh auth token unavailable: {e}")
        return ""


def _load_jobs() -> list:
    """Read the JOBS list from setup_cronjobs.py WITHOUT importing it (the module demands
    CRONJOB_API_KEY at import time). Safe AST literal-eval of the `JOBS = [...]` assignment."""
    import ast
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "setup_cronjobs.py")
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "JOBS" for t in node.targets):
                return [tuple(x) for x in ast.literal_eval(node.value)]
    except Exception as e:
        log.warning(f"could not read JOBS from setup_cronjobs.py: {e}")
    return []


def _cron_human(cron: str) -> str:
    """A readable cadence from a 5-field UTC cron expression. Falls back to the raw cron on anything
    unexpected (never raises).

    Fix (user 2026-07-31, P-15): a concrete minute AND concrete hour are now COMBINED into one clock time
    ("22:15 UTC") instead of being emitted as two disjoint fragments (":15, 22:00 UTC" — which read as the
    nonsensical ":15 22:00 UTC")."""
    try:
        minute, hour, mday, month, wday = cron.split()
    except ValueError:
        return cron

    def _hours(h):
        if h == "*":
            return ""
        if "-" in h and h.replace("-", "").isdigit():
            a, b = h.split("-")
            return f"{int(a):02d}:00-{int(b):02d}:00 UTC"
        if "," in h:
            return ",".join(f"{int(x):02d}:00" for x in h.split(",") if x.isdigit()) + " UTC"
        if h.isdigit():
            return f"{int(h):02d}:00 UTC"
        return f"h{h}"

    # Time-of-day phrase — minute and hour read TOGETHER so a fixed daily time is a single clock value.
    if minute.startswith("*/"):
        when = f"every {minute[2:]} min"
        hrs = _hours(hour)
        if hrs:
            when += f", {hrs}"
    elif minute.isdigit():
        mm = int(minute)
        if hour == "*":
            when = f":{mm:02d} each hour"
        elif hour.isdigit():
            when = f"{int(hour):02d}:{mm:02d} UTC"
        elif "," in hour and all(x.isdigit() for x in hour.split(",")):
            when = ", ".join(f"{int(x):02d}:{mm:02d}" for x in hour.split(",")) + " UTC"
        elif "-" in hour and hour.replace("-", "").isdigit():
            a, b = hour.split("-")
            when = f":{mm:02d} hourly {int(a):02d}:00-{int(b):02d}:00 UTC"
        else:
            when = f"{hour}:{mm:02d} UTC"
    else:
        hrs = _hours(hour)
        when = f"min {minute}" + (f", {hrs}" if hrs else "")

    # weekdays
    def _wday(w):
        if w in ("*", "?"):
            return "every day"
        def lbl(n):
            n = int(n)
            return _DAYS[n - 1] if 1 <= n <= 6 else "Sun"
        if "-" in w and w.replace("-", "").isdigit():
            a, b = w.split("-")
            return f"{lbl(a)}-{lbl(b)}"
        if "," in w:
            return " & ".join(lbl(x) for x in w.split(","))
        if w.isdigit():
            return lbl(w)
        return w
    days = _wday(wday)

    return ", ".join(p for p in (when, days) if p)


# Which trading STYLE a job serves (user 2026-07-06). The session monitors/opens place multi-factor
# momentum trades; the HVF jobs run the squeeze pipeline; the rest are support/ops (reports, health).
_MOMENTUM_JOBS = {"AUS Open", "AUS Monitor", "Commodity Monitor AM", "Commodity Monitor PM",
                  "UK Open", "UK Monitor", "US Open", "US Monitor",
                  # user 2026-07-11: these serve the multi-factor momentum pipeline too
                  "Sunday Readiness Check", "Sunday Pre-Open Commodity Scan", "COT Report"}
_SQUEEZE_JOBS = {"HVF Daily Report", "HVF Orders", "UK HVF Watch", "US HVF Watch"}

_DISPLAY_NAMES = {
    "HVF Daily Report": "Squeeze Daily Report",
    "HVF Orders": "Squeeze Orders",
    "UK HVF Watch": "UK Squeeze Watch",
    "US HVF Watch": "US Squeeze Watch",
}


def _trading_style(title: str) -> str:
    if title in _MOMENTUM_JOBS:
        return "Multi-factor momentum"
    if title in _SQUEEZE_JOBS:
        return "Squeeze"
    return "Support / ops"


def _display_name(title: str) -> str:
    return _DISPLAY_NAMES.get(title, title)


# What each scheduled job is FOR (user 2026-07-31, P-15 — "determine the purpose of each job"). Keyed by
# the JOBS title; jobs without an explicit entry fall back to a category-derived sentence in _purpose().
_PURPOSE = {
    "AUS Open": "Opens the Australian (ASX) session — scans for multi-factor momentum setups and places the session's opening trades.",
    "AUS Monitor": "Every 5 min through Sydney hours, manages open ASX-session positions (trailing stops / exits).",
    "Commodity Monitor AM": "Morning intraday scan of commodities (metals & energy) for momentum signals.",
    "Price Data Refresh": "Re-fetches and upserts the universe's daily price history so the HVF report that follows runs on fresh data.",
    "HVF Daily Report": "Runs the daily Squeeze (HVF) scan across the whole universe and publishes the shortlist / report.",
    "HVF Orders": "Places the day's qualifying Squeeze setups on IG as working orders (entry, stop and target attached).",
    "UK Open": "Opens the UK / Europe (LSE) session — momentum scan plus the session's opening trades.",
    "UK Morning Brief": "Emails / posts the UK morning market brief (Mondays & Fridays).",
    "UK Monitor": "Every 5 min through London hours, manages open UK-session positions.",
    "US Open": "Opens the US (NYSE / NASDAQ) session — momentum scan plus the session's opening trades.",
    "US Monitor": "Every 5 min through US hours, manages open US-session positions.",
    "US HVF Watch": "Four intraday checks of US Squeeze setups approaching their trigger, alerting as they near entry.",
    "UK HVF Watch": "Four intraday checks of UK Squeeze setups approaching their trigger, alerting as they near entry.",
    "Social Monitor": "Every 15 min, watches X / social feeds for relevant market signals and mentions.",
    "Commodity Monitor PM": "Evening intraday scan of commodities for momentum signals.",
    "Session Close": "End-of-session housekeeping — settles the day's session positions and records realised P&L.",
    "Daily Report": "End-of-day performance and activity report for the account.",
    "Pre-Order Report": "Summarises the working orders queued for the next session so they can be reviewed before the open.",
    "Data Quality Audit": "Nightly Yahoo-vs-IG price / data integrity audit; flags gaps and mismatches.",
    "Price History Audit": "Nightly re-fetch and completeness check of the universe's price history.",
    "Session Watchdog": "Every 10 min, health-checks that the session jobs and the IG order bridge are alive; alerts on failure.",
    "Daily Diagnostics": "Morning system diagnostics — data freshness, connectivity and credentials.",
    "Weekend Review": "Saturday review of the week's trades and overall system performance.",
    "COT Report": "Saturday Commitments-of-Traders report on commodities positioning.",
    "Sunday Readiness Check": "Sunday pre-week readiness — verifies data, credentials and schedules before Monday's open.",
    "Sunday Pre-Open Commodity Scan": "Sunday pre-market commodities scan / brief ahead of the week's open.",
}


def _purpose(title: str) -> str:
    if title in _PURPOSE:
        return _PURPOSE[title]
    # Fallback: a sentence from the category so a newly-added job is never blank.
    cat = _category(title).lower()
    return f"{title} — {cat} job." if cat != "other" else title


def _category(title: str) -> str:
    t = title.lower()
    if "monitor" in t:
        return "Session monitors"
    if "open" in t or "close" in t:
        return "Session open/close"
    if "hvf" in t:
        return "HVF"
    if "order" in t:
        return "Orders"
    if any(k in t for k in ("watch", "diagnostic", "audit", "readiness", "quality")):
        return "Health & watch"
    if "social" in t:
        return "Social"
    if any(k in t for k in ("report", "brief", "review", "cot")):
        return "Reports"
    return "Other"


def _fetch_runs(session, workflow_file: str) -> dict:
    """GitHub Actions run stats for one workflow file. {executions, failures, last_status, last_time}.
    executions = all-time total_count; failures counted over the last 100 runs (a representative
    recent sample — labelled as such in the UI)."""
    import requests
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{workflow_file}/runs?per_page=100"
    r = session.get(url, timeout=12)
    r.raise_for_status()
    j = r.json()
    runs = j.get("workflow_runs", []) or []
    fail_concl = {"failure", "timed_out", "startup_failure"}
    failures = sum(1 for x in runs if (x.get("conclusion") or "") in fail_concl)
    last = runs[0] if runs else {}
    duration_s = None
    started, finished = last.get("run_started_at"), last.get("updated_at")
    if started and finished:
        try:
            from datetime import datetime
            a = datetime.fromisoformat(started.replace("Z", "+00:00"))
            b = datetime.fromisoformat(finished.replace("Z", "+00:00"))
            duration_s = max(0, int((b - a).total_seconds()))
        except Exception:
            duration_s = None
    return {
        "executions": j.get("total_count", len(runs)),
        "failures": failures,
        "failures_window": len(runs),                       # how many runs the failure count covers
        "last_status": (last.get("conclusion") or last.get("status") or "-"),
        "last_time": last.get("created_at"),
        "last_duration_s": duration_s,
    }


def get_jobs(force: bool = False) -> dict:
    """The scheduled-job registry enriched with run stats. Cached for _CACHE_TTL. Returns
    {generated_utc, jobs:[...], stats_source, error?}. Never raises."""
    now = time.time()
    if not force and _cache["data"] and (now - _cache["ts"] < _CACHE_TTL):
        return _cache["data"]

    JOBS = _load_jobs()
    if not JOBS:
        return {"jobs": [], "error": "job registry (setup_cronjobs.JOBS) unreadable"}

    jobs = [{"title": _display_name(ti), "raw_title": ti, "cron": cr, "workflow": wf,
             "frequency": _cron_human(cr), "category": _category(ti),
             "trading_style": _trading_style(ti), "purpose": _purpose(ti)}
            for (ti, cr, wf) in JOBS]

    error = None
    token = _gh_token()
    if token:
        try:
            import requests
            s = requests.Session()
            s.headers.update({"Authorization": f"Bearer {token}",
                              "Accept": "application/vnd.github+json",
                              "X-GitHub-Api-Version": "2022-11-28"})
            stats_cache = {}                                  # per unique workflow file
            for jb in jobs:
                wf = jb["workflow"]
                if wf not in stats_cache:
                    try:
                        stats_cache[wf] = _fetch_runs(s, wf)
                    except Exception as e:
                        stats_cache[wf] = {"error": str(e)}
                jb.update(stats_cache[wf])
        except Exception as e:
            error = f"run stats unavailable: {e}"
    else:
        error = "no GitHub token on this host — showing definitions only"

    from datetime import datetime, timezone
    data = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "jobs": jobs, "stats_source": bool(token), "error": error}
    _cache.update(ts=now, data=data)
    return data
