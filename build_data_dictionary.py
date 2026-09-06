#!/usr/bin/env python3
# ======================================================================================================================
# File:         build_data_dictionary.py
# Author:       Alex Hind (via Claude)
# Created:      2026-09-06
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Generates the AH Data Dictionary skill from the LIVE Supabase schema (P-25, requested 2026-08-29:
# "Create a data dictionary for the database tables, as a skill").
#
# WHY GENERATED RATHER THAN WRITTEN. A hand-written dictionary is accurate on the day it is written and
# wrong from the first migration afterwards, and nothing tells the reader which they are looking at. The
# structural half -- tables, columns, types, sizes, row counts -- is read from the database every time
# this runs, so it cannot drift. Re-run it after any schema change.
#
# WHAT A GENERATOR CANNOT PRODUCE is the half that matters: why a table exists, what writes it, how often,
# and the traps. `\d` already prints the columns. So NOTES below carries the curated part, and the output
# marks any table that has no note -- an undocumented table shows up as a gap rather than passing as
# documented because it has a column list.
#
# Usage:
#   python build_data_dictionary.py            # regenerate skills_src/ah-data-dictionary/
#   python build_data_dictionary.py --check    # non-zero exit if the skill is out of date
# ======================================================================================================================

import argparse
import datetime as _dt
import logging
import pathlib
import sys

log = logging.getLogger("data_dictionary")

ROOT = pathlib.Path(__file__).resolve().parent
SKILL_DIR = ROOT / "skills_src" / "ah-data-dictionary"

# (what it holds, who writes it and when, what to watch out for). Only the WHY: the columns, sizes and
# row counts come from the database.
NOTES = {
    "price_history": (
        "Daily OHLCV bars for the whole universe. The largest object in the database by far.",
        "price_store, during the daily price refresh (Morning Chain step 1).",
        "4.5-year retention. run_price_history_prune.py exists for it and its VACUUM must NEVER be "
        "scheduled. At ~343 MB this is the reason the 500 MB free tier is a live constraint."),
    "squeeze_history": (
        "One row per detected squeeze setup, with entry/stop/target, quality, R:R, the pivot dates and "
        "-- once resolved -- outcome and return_pct. THE table behind Performance, Best Settings and the "
        "Insights page.",
        "The daily scan (run_hvf_report) writes and refreshes it.",
        "Direction lives in hvf_type as BULLISH/BEARISH -- there is NO `direction` column. Rows can "
        "carry rvol NULL (815 of 30,408 measured 2026-08-17), mostly FX and indices which have no real "
        "volume. A ticker can appear more than once for one day across lookback windows, so anything "
        "counting trades must de-duplicate on ticker + triggered_date."),
    "instrument_metrics_daily": (
        "The stored break-bar measures (RVOL, above-VWAP, ATR expanding, VolumeScore) per ticker per day.",
        "instrument_metrics.record_daily, inside the daily scan.",
        "as_of IS NOT THE BAR IT DESCRIBES. The scan runs ~03:30 UTC, before any market opens, so the row "
        "written under as_of = today is computed from YESTERDAY'S bar: measured 2026-09-06, 1,761 of the "
        "as_of 2026-09-05 rows carry bar_date 2026-09-04. Always compare bar_date against the day you are "
        "judging. It also stored NOTHING between 2026-08-29 and 2026-09-04 because its INSERT carried a "
        "placeholder with no argument."),
    "instrument_mcap": (
        "Current market cap per ticker, one row each, with its source currency.",
        "mcap_backfill, weekly (Sunday 05:00 UTC).",
        "The column is `mcap`, not market_cap. Values are in the instrument's OWN currency -- fx_rates "
        "converts to GBP, and an unconvertible currency yields None rather than a wrong number."),
    "instrument_mcap_history": ("Market cap over time, one row per ticker per capture.",
                                "mcap_backfill, weekly.", ""),
    "working_orders": (
        "The engine-managed pre-order lifecycle: WATCHING -> PENDING -> FILLED / CANCELLED / EXPIRED.",
        "ig_shim, on every bridge pass.",
        "Carries NO RVOL, VolumeScore, Quality or R:R columns. Those are resolved at read time from the "
        "squeeze_history trigger that caused the order (server._attach_setup_metrics)."),
    "epic_lookup": ("Ticker -> IG epic, with the IG instrument description.",
                    "ig_shim on demand; verified nightly by the data-quality audit.",
                    "The identity check here is what catches an epic mapped to the WRONG COMPANY."),
    "web_users": ("Logins, roles, per-user settings and encrypted per-user secrets.",
                  "hvf_web/web_users.py.",
                  "Supabase-primary with a local compatibility copy. Writes FAIL CLOSED when Supabase is "
                  "unreachable rather than risk a stale overwrite. Keyed by LOGIN -- there is no user id."),
    "web_json_store": ("A key/value JSON store: precomputed winners payloads, Best Settings cards, the "
                       "sector cache, the metric and coverage audits.",
                       "Whichever job owns each key.",
                       "Twelve rows, but 2.3 MB -- the values are large documents."),
    "app_secrets": ("Encrypted secret store, read into the environment at import by db_pool.",
                    "migrate_secrets_to_supabase / set_secret.",
                    "Primary source of credentials. .env is kept as a complete cold backup."),
    "app_config": ("Engine settings editable from Configuration (Admin).", "hvf_web/server.py.", ""),
    "signal_log": ("Historical per-session signal records.", "The session monitors.",
                   "DEAD since the session monitors were disabled -- 19 MB of history that nothing has "
                   "written to since 2026-08-13. A deletion candidate if space is ever needed."),
    "hvf_scan_log": ("Per-scan log of what each run examined.", "The daily scan.", ""),
    "hvf_triggers": ("Live detections since 2026-06-30.", "The daily scan.",
                     "Has NO trigger-date column: only recorded_at and the pivot dates."),
    "hvf_suppressed_log": ("Setups the method found and then suppressed, with the reason.",
                           "The daily scan.", ""),
    "fx_rates": ("Currency -> GBP conversion rates.", "mcap_backfill refreshes them on its weekly run.",
                 "A missing rate must yield None, never 1.0: a silent 1.0 would report a foreign market "
                 "cap as though it were sterling."),
    "positions": ("Engine-side open position records.", "ig_shim.", ""),
    "trade_log": ("Executed trades.", "ig_shim.", ""),
    "daily_pnl": ("Daily profit and loss.", "The session summary job.", ""),
    "missed_trade_log": ("Setups that passed detection but were not taken, and why.",
                         "The bridge and the order gate.",
                         "The place to look when asked why something was not ordered."),
    "data_quality_log": ("Yahoo-vs-IG price comparisons per ticker per audit.",
                         "run_data_quality_audit, nightly 22:15 UTC.", ""),
    "web_activity_log": ("Per-user web activity.", "hvf_web/server.py.", ""),
    "web_batch_activity": ("Scheduled-job run records behind the Batch Activity tab.",
                           "The jobs themselves.", ""),
    "web_ig_account_audit": ("Append-only record of every IG close attempt.",
                             "hvf_web/server.py._append_ig_close_audit.",
                             "Deliberately host-side and independent of Supabase: an audit trail for a "
                             "live broker action must survive the outage that may have caused the problem."),
    "scanner_snapshot_current": ("Pointer to the published snapshot now in play.",
                                 "publish_scanner_snapshot.", ""),
    "scanner_snapshot_versions": ("Immutable published snapshot versions.", "publish_scanner_snapshot.",
                                  "Supabase Storage has been returning 402 Payment Required since "
                                  "2026-08-16, so publication has been falling back to IONOS."),
    "scanner_refresh_progress": ("Progress of an in-flight rescan, for the UI.", "The scanner.", ""),
    "x_publications": ("What has been posted to X.", "publish_one_to_x.", ""),
    "x_draft_state": ("Drafted X posts awaiting review.", "intraday_signals.", ""),
    "login_attempts": ("Throttling state for failed logins.", "login_throttle.", ""),
    "user_profiles": ("Engine-side trading profiles.", "run_session.",
                      "profiles.name ('Owner') is NOT a web login. trading_limits.login_for_profile maps "
                      "one to the other by USER ID -- never by guessing from the name."),
    "web_best_settings_history": ("Snapshots of the Best Settings recommendation over time.",
                                  "The web app when a recommendation is recorded.", ""),
    "macro_snapshot": ("Macro indicators.", "commodity_macro / FRED pulls.", ""),
    "cot_snapshot": ("Commitment of Traders positioning.", "cot_analysis.", ""),
    "notable_investors": ("Superinvestor holdings.", "analyst_signals.", ""),
    "senator_scores": ("Congressional trading signal scores.", "analyst_signals.", ""),
    "social_mentions": ("Tracked social mentions.", "social_monitor.", ""),
    "geopolitical_risk": ("Geopolitical risk scores.", "commodity_macro.", ""),
    "ig_validation_log": ("IG price-validation results.", "run_data_quality_audit.", ""),
    "price_audit_log": ("Price-history audit results.", "price_audit.", ""),
    "hvf_watch_state": ("Per-market watch cursor.", "The HVF watch jobs.", ""),
    "x_publications_archive": ("Archived X publications.", "Archival job.", ""),
    "x_draft_state_archive": ("Archived X drafts.", "Archival job.", ""),
}


def introspect() -> dict:
    """Tables, sizes, row counts and columns, straight from the live database."""
    from db_pool import get_db
    db = get_db()
    try:
        tables = db.run(
            "select c.relname, pg_size_pretty(pg_total_relation_size(c.oid)), "
            "coalesce(s.n_live_tup, 0)::bigint, pg_total_relation_size(c.oid) "
            "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "left join pg_stat_user_tables s on s.relid = c.oid "
            "where n.nspname = 'public' and c.relkind = 'r' "
            "order by pg_total_relation_size(c.oid) desc") or []
        cols = db.run(
            "select table_name, column_name, data_type, is_nullable "
            "from information_schema.columns where table_schema = 'public' "
            "order by table_name, ordinal_position") or []
        total = db.run("select pg_size_pretty(sum(pg_total_relation_size(c.oid))) from pg_class c "
                       "join pg_namespace n on n.oid = c.relnamespace "
                       "where n.nspname = 'public' and c.relkind = 'r'")[0][0]
    finally:
        db.close()
    by_table = {}
    for t, c, dt, nullable in cols:
        by_table.setdefault(t, []).append((c, dt, nullable == "YES"))
    return {"tables": tables, "columns": by_table, "total": total}


def render(schema: dict) -> str:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    tables, cols, total = schema["tables"], schema["columns"], schema["total"]
    undocumented = [t[0] for t in tables if t[0] not in NOTES]

    out = [
        "---",
        "name: ah-data-dictionary",
        "description: >",
        "  The Supabase schema behind the Squeeze scanner: every table, what it holds, which job writes",
        "  it and when, and the traps that have actually bitten. Use this skill whenever the user asks",
        "  what a table contains, where a figure comes from, which job populates something, why a column",
        "  is empty, or how much space the database is using. The structural half is generated from the",
        "  live schema by build_data_dictionary.py -- re-run it after any migration.",
        "---",
        "",
        "# AH Data Dictionary — the Supabase schema",
        "",
        f"Generated from the live database on **{stamp}**. Total public schema: **{total}** of the "
        "500 MB free tier.",
        "",
        "Re-generate with `python build_data_dictionary.py`. The column lists, sizes and row counts come "
        "from the database and cannot drift; the notes are curated and are the part worth reading.",
        "",
        "## Read this first",
        "",
        "- **`squeeze_history` has no `direction` column.** Direction is `hvf_type`, BULLISH or BEARISH.",
        "- **`instrument_mcap.mcap`**, not `market_cap`, and it is in the instrument's own currency.",
        "- **`instrument_metrics_daily.as_of` is not the bar it describes** — see its entry below. This "
        "one has real money attached to it.",
        "- **`web_users` writes fail closed** when Supabase is unreachable, by design.",
        "",
        "## Tables, largest first",
        "",
    ]
    for name, size, rows, _ in tables:
        note = NOTES.get(name)
        out.append(f"### `{name}`  <span>({size}, ~{rows:,} rows)</span>")
        out.append("")
        if note:
            holds, writer, trap = note
            out.append(f"**Holds** — {holds}")
            out.append("")
            out.append(f"**Written by** — {writer}")
            if trap:
                out.append("")
                out.append(f"**Watch out** — {trap}")
        else:
            out.append("**UNDOCUMENTED.** No curated note exists for this table. A column list is not "
                       "documentation: add an entry to NOTES in `build_data_dictionary.py` saying what it "
                       "holds, what writes it and when.")
        out.append("")
        cl = cols.get(name) or []
        if cl:
            out.append("| column | type | null |")
            out.append("|---|---|---|")
            for c, dt, nullable in cl:
                out.append(f"| `{c}` | {dt} | {'yes' if nullable else 'no'} |")
            out.append("")

    if undocumented:
        out.insert(len(out), "")
        out.append("## Undocumented tables")
        out.append("")
        out.append("These exist in the database with no curated note. Each is a gap, not a table that "
                   "happens to need no explanation:")
        out.append("")
        for t in undocumented:
            out.append(f"- `{t}`")
        out.append("")
    return "\n".join(out) + "\n"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Generate the data-dictionary skill from the live schema.")
    ap.add_argument("--check", action="store_true", help="exit non-zero if the skill is out of date")
    a = ap.parse_args()

    body = render(introspect())
    target = SKILL_DIR / "SKILL.md"
    if a.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        # The generated date line changes every run, so compare everything else.
        strip = lambda t: "\n".join(l for l in t.splitlines() if not l.startswith("Generated from the live"))
        if strip(current) != strip(body):
            log.error("the data dictionary is out of date. Run: python build_data_dictionary.py")
            return 1
        log.info("the data dictionary matches the live schema.")
        return 0

    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    log.info("wrote %s (%d tables, %.0f KB)", target, len(NOTES), len(body) / 1024)
    return 0


if __name__ == "__main__":
    sys.exit(main())
