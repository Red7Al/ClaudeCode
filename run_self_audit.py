# =============================================================================
# File:         run_self_audit.py
# Author:       Alex Hind
# Created:      2026-06-08
#
# Description:
# -----------------------------------------------------------------------------
# Automated self-audit. Re-verifies the "assumption shipped unverified" bug class
# AGAINST THE LIVE SOURCES — the root cause of every bug found in this system
# (wrong CFTC/EIA/FRED ids, missing ON CONFLICT constraints, schema drift).
#
# This is the automated form of the manual audit that caught: Gold/Silver CFTC
# codes (wrong), EIA refinery series (404), and notable_investors' no-op
# ON CONFLICT. Run it weekly (carried by trading-weekend-review.yml) and after
# any change touching an external id or the DB; it posts a ✅/⚠/❌ report to
# #claude-trading-alerts.
#
# What it checks (all against the live source, never assumptions):
#   1. CFTC codes  — every config.CFTC_CODES entry resolves AND its market name
#                    matches the instrument (catches Gold/Silver-style swaps).
#   2. EIA series  — the series ids the code fetches resolve (needs EIA_API_KEY).
#   3. FRED series — the series ids the code fetches resolve (needs FRED_API_KEY).
#   4. DB constraints — every ON CONFLICT target the code relies on has a real
#                    unique/PK constraint in the live schema.
#
# FAIL vs WARN (important — this tool must not cry wolf):
#   ❌ FAIL  = a DEFINITIVE wrong answer: a code resolves to the wrong market, a
#             series returns no data, or an ON CONFLICT target has no constraint.
#             Exits non-zero so the failure is impossible to miss.
#   ⚠ WARN  = could NOT verify (network timeout, server 5xx, key absent). NOT
#             proof of a bug — surfaced to Slack but does NOT fail the run, so a
#             slow upstream API never turns the weekly job red falsely.
#
# Usage:
#   python run_self_audit.py
#
# Environment Variables Required:
#   SUPABASE_USER, SUPABASE_DB_PASSWORD   (constraint checks)
#   EIA_API_KEY, FRED_API_KEY             (series checks; warn-skip if absent)
#   SLACK_ALERTS                          (report destination)
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-06-08  Alex Hind   Initial build. CFTC name-match, EIA + FRED series
#                                 resolution, and ON CONFLICT constraint existence
#                                 checks. Posts ✅/⚠/❌ to #claude-trading-alerts.
# 1.1.0   2026-06-08  Alex Hind   Robustness: a transient CFTC read-timeout in CI
#                                 was wrongly reported as ❌ and failed the run
#                                 (24/25, code 096742 is correct — the API was just
#                                 slow). Added _get() with retry+backoff and split
#                                 FAIL (definitive wrong answer → exit 1) from WARN
#                                 (could-not-verify network/5xx/missing-key → exit 0,
#                                 still shown in Slack). Header now reflects warns.
# =============================================================================

import os
from dotenv import load_dotenv; load_dotenv(override=True)
import logging
import sys
import time

import requests
import pg8000.native

from config import CFTC_CODES

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("self_audit")

SLACK_URL     = os.environ.get("SLACK_ALERTS", "")
SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"

# Expected CFTC market-name keyword per instrument (verified 2026-06-07).
# The check fails if config's code resolves to a market NOT containing this word.
CFTC_NAME_KEYWORD = {
    "XAUUSD": "GOLD",   "XAGUSD": "SILVER", "OIL": "WTI",
    "GBPUSD": "BRITISH POUND", "AUDUSD": "AUSTRALIAN DOLLAR",
    "USDJPY": "JAPANESE YEN",   "EURUSD": "EURO",
    "SPX500": "S&P 500", "NASDAQ": "NASDAQ", "MSCI_EAFE": "EAFE",
}

# EIA series the code actually fetches (commodity_supply_demand.py).
EIA_SERIES = ["PET.WCRSTUS1.W", "PET.WPULEUS3.W"]

# FRED series the code actually fetches (commodity_macro.py + signals.py).
FRED_SERIES = ["DGS2", "DGS10", "DFII10", "T10YIE", "T5YIE", "T5YIFR", "MANEMP"]

# ON CONFLICT targets the code relies on → (table, [columns]). A matching UNIQUE
# or PRIMARY KEY constraint MUST exist or the upsert throws at runtime.
EXPECTED_CONSTRAINTS = [
    ("daily_pnl",          ["user_id", "trade_date"]),
    ("senator_scores",     ["senator_name"]),
    ("cot_snapshot",       ["report_date", "instrument"]),
    ("notable_investors",  ["investor_name", "ticker", "action"]),
    ("positions",          ["deal_id"]),
    ("epic_lookup",        ["ticker"]),
]


def ok(name, detail=""):   return {"status": "ok",   "name": name, "detail": detail}
def warn(name, detail=""): return {"status": "warn", "name": name, "detail": detail}
def fail(name, detail=""): return {"status": "fail", "name": name, "detail": detail}


def _get(url, params, attempts=3, timeout=20):
    """
    GET with retry on transient network errors (read timeout / connection reset).

    A transient failure must NOT be reported as a wrong-id FAILURE — callers
    treat a raised exception as "could not verify" (⚠), never "❌". Returns the
    Response; re-raises only the last exception after all attempts are exhausted.
    """
    last = None
    for i in range(attempts):
        try:
            return requests.get(url, params=params, timeout=timeout)
        except requests.RequestException as e:
            last = e
            if i < attempts - 1:
                time.sleep(2 * (i + 1))   # 2s, then 4s backoff
    raise last


# ---------------------------------------------------------------------------
# 1. CFTC codes — resolve + market-name match
# ---------------------------------------------------------------------------

def check_cftc() -> list:
    results = []
    for inst, code in CFTC_CODES.items():
        try:
            r = _get("https://publicreporting.cftc.gov/resource/6dca-aqww.json",
                     {"$where": f"cftc_contract_market_code='{code}'",
                      "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 1})
        except requests.RequestException as e:
            results.append(warn(f"CFTC {inst} ({code})",
                                f"could not verify (network): {str(e)[:60]}"))
            continue
        if r.status_code != 200:
            results.append(warn(f"CFTC {inst} ({code})",
                                f"could not verify (HTTP {r.status_code})"))
            continue
        data = r.json()
        if not data:
            results.append(fail(f"CFTC {inst} ({code})", "no data — invalid code"))
            continue
        name = data[0].get("market_and_exchange_names", "")
        kw   = CFTC_NAME_KEYWORD.get(inst, "")
        if kw and kw.upper() not in name.upper():
            results.append(fail(f"CFTC {inst} ({code})",
                                f"resolves to '{name}' — expected '{kw}'"))
        else:
            results.append(ok(f"CFTC {inst} ({code})", name[:40]))
    return results


# ---------------------------------------------------------------------------
# 2/3. EIA + FRED series resolution
# ---------------------------------------------------------------------------

def check_eia() -> list:
    key = os.environ.get("EIA_API_KEY", "")
    if not key:
        return [warn("EIA series", "EIA_API_KEY not set — skipped")]
    results = []
    for s in EIA_SERIES:
        try:
            r = _get(f"https://api.eia.gov/v2/seriesid/{s}",
                     {"api_key": key, "num": 1, "out": "json"})
        except requests.RequestException as e:
            results.append(warn(f"EIA {s}", f"could not verify (network): {str(e)[:60]}"))
            continue
        if r.status_code == 200 and r.json().get("response", {}).get("data"):
            results.append(ok(f"EIA {s}"))
        else:
            results.append(fail(f"EIA {s}", f"HTTP {r.status_code} / no data"))
    return results


def check_fred() -> list:
    key = os.environ.get("FRED_API_KEY", "")
    if not key:
        return [warn("FRED series", "FRED_API_KEY not set — skipped")]
    results = []
    for s in FRED_SERIES:
        try:
            r = _get("https://api.stlouisfed.org/fred/series/observations",
                     {"series_id": s, "api_key": key, "file_type": "json",
                      "sort_order": "desc", "limit": 1})
        except requests.RequestException as e:
            results.append(warn(f"FRED {s}", f"could not verify (network): {str(e)[:60]}"))
            continue
        if r.status_code == 200 and r.json().get("observations"):
            results.append(ok(f"FRED {s}"))
        else:
            results.append(fail(f"FRED {s}", f"HTTP {r.status_code} / no observations"))
    return results


# ---------------------------------------------------------------------------
# 4. DB ON CONFLICT constraints exist
# ---------------------------------------------------------------------------

def check_constraints() -> list:
    results = []
    try:
        conn = pg8000.native.Connection(
            host=SUPABASE_HOST, port=5432, database="postgres",
            user=os.environ["SUPABASE_USER"],
            password=os.environ["SUPABASE_DB_PASSWORD"], ssl_context=True,
        )
    except Exception as e:
        # cannot reach the DB at all → could not verify (warn, not a wrong-schema fail)
        return [warn("DB constraints", f"could not connect: {str(e)[:80]}")]
    try:
        rows = conn.run(
            """select tc.table_name,
                      string_agg(kcu.column_name, ',' order by kcu.ordinal_position)
               from information_schema.table_constraints tc
               join information_schema.key_column_usage kcu
                 on tc.constraint_name = kcu.constraint_name
                and tc.table_schema    = kcu.table_schema
               where tc.table_schema='public'
                 and tc.constraint_type in ('PRIMARY KEY','UNIQUE')
               group by tc.table_name, tc.constraint_name"""
        )
        # build set of (table, frozenset(cols)) that exist
        existing = {(r[0], frozenset((r[1] or "").split(","))) for r in (rows or [])}
        for table, cols in EXPECTED_CONSTRAINTS:
            want = (table, frozenset(cols))
            if want in existing:
                results.append(ok(f"constraint {table}({','.join(cols)})"))
            else:
                results.append(fail(f"constraint {table}({','.join(cols)})",
                                    "no matching UNIQUE/PK — ON CONFLICT will throw"))
    except Exception as e:
        results.append(fail("DB constraints", str(e)[:80]))
    finally:
        conn.close()
    return results


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def post_report(results: list):
    n_fail = sum(1 for r in results if r["status"] == "fail")
    n_warn = sum(1 for r in results if r["status"] == "warn")
    n_ok   = sum(1 for r in results if r["status"] == "ok")

    if n_fail:
        head = f"❌ Self-audit — {n_fail} FAILURE(S)"
    elif n_warn:
        head = f"⚠️ Self-audit — {n_warn} unverified (network / skipped)"
    else:
        head = "✅ Self-audit clean"

    lines = [f"*{head}*  (✅ {n_ok}  ⚠ {n_warn}  ❌ {n_fail})", ""]
    for r in results:
        icon = {"ok": "✅", "warn": "⚠️", "fail": "❌"}[r["status"]]
        # only spell out warnings/failures in detail to keep the message tight
        if r["status"] == "ok":
            continue
        lines.append(f"{icon} {r['name']} — {r['detail']}")
    if n_fail == 0 and n_warn == 0:
        lines.append("_All external IDs (CFTC/EIA/FRED) and DB constraints verified against live sources._")

    text = "\n".join(lines)
    if SLACK_URL:
        try:
            requests.post(SLACK_URL, json={"text": text}, timeout=10)
        except Exception as e:
            log.error(f"Slack post failed: {e}")
    # Encoding-safe stdout (the Actions runner is UTF-8, but a Windows cp1252
    # console cannot encode the ✅/⚠/❌ glyphs — never let the report crash).
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


def main():
    log.info("Running self-audit (CFTC / EIA / FRED / DB constraints)...")
    results = []
    results += check_cftc()
    results += check_eia()
    results += check_fred()
    results += check_constraints()
    post_report(results)
    n_fail = sum(1 for r in results if r["status"] == "fail")
    n_warn = sum(1 for r in results if r["status"] == "warn")
    if n_fail:
        log.error(f"{n_fail} self-audit check(s) FAILED")
        sys.exit(1)
    log.info(f"Self-audit complete — {len(results)} checks, "
             f"{n_warn} unverified (warn), no failures")


if __name__ == "__main__":
    main()
