# =============================================================================
# File:         run_schema.py
# Author:       Alex Hind
# Created:      2026-06-02
#
# Description:
# -----------------------------------------------------------------------------
# Idempotent schema migration script for the EndToEndTrading Supabase database.
# Safe to re-run at any time — all statements use IF NOT EXISTS / ON CONFLICT.
#
# Usage:
#   python run_schema.py
#
# Or trigger via GitHub Actions: "Run Schema (one-shot)" workflow (manual dispatch).
#
# Environment Variables Required:
#   SUPABASE_USER         Supabase PostgreSQL user (postgres.{project_id})
#   SUPABASE_DB_PASSWORD  Supabase database password
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-06-02  Alex Hind   Initial build. Idempotent migrations for
#                                 signal_log, positions, macro_snapshot,
#                                 hvf_scan_log, and epic_lookup tables.
# =============================================================================

import os
import logging
import pg8000.native

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("run_schema")

SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"

# -----------------------------------------------------------------------------
# Migrations — applied in order, each is idempotent
# -----------------------------------------------------------------------------

MIGRATIONS = [

    # ── signal_log: columns written by signals.py but missing from live table ──
    #
    # signals.py INSERT targets these columns; without them every session scan
    # raises a column-not-found error and the signal row is never written.

    (
        "signal_log: add call_put_ratio",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS call_put_ratio numeric"
    ),
    (
        "signal_log: add primary_count",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS primary_count integer"
    ),
    (
        "signal_log: add direction",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS direction text"
    ),
    (
        "signal_log: add pa_verdict",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS pa_verdict text"
    ),

    # ── signal_log: columns read by run_daily_report.py but missing from table ──
    #
    # Daily report's fetch_signal_log() queries these columns for the
    # "Notable Moves Not Traded" section. Without them the nightly report crashes.

    (
        "signal_log: add adx_signal",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS adx_signal text"
    ),
    (
        "signal_log: add obv_signal",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS obv_signal text"
    ),
    (
        "signal_log: add volume_signal",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS volume_signal text"
    ),
    (
        "signal_log: add volume_ratio",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS volume_ratio numeric"
    ),

    # ── signal_log: HVF columns (added 2026-06-04) ────────────────────────────
    (
        "signal_log: add hvf_type",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS hvf_type text"
    ),
    (
        "signal_log: add hvf_signal",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS hvf_signal text"
    ),
    (
        "signal_log: add hvf_h3_level",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS hvf_h3_level numeric"
    ),
    (
        "signal_log: add hvf_stop_level",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS hvf_stop_level numeric"
    ),
    (
        "signal_log: add hvf_target",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS hvf_target numeric"
    ),
    (
        "signal_log: add hvf_risk_reward",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS hvf_risk_reward numeric"
    ),
    (
        "signal_log: add hvf_quality",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS hvf_quality integer"
    ),

    # ── signal_log: pa_score numeric (added 2026-06-04) ───────────────────────
    (
        "signal_log: add pa_score",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS pa_score numeric"
    ),

    # ── signal_log: new primary signal columns (added 2026-06-04) ─────────────
    # ADX directional primary (distinct from adx_signal which records strength)
    (
        "signal_log: add adx_dir",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS adx_dir text"
    ),
    # Opening Range Breakout
    (
        "signal_log: add orb_signal",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS orb_signal text"
    ),
    (
        "signal_log: add orb_dir",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS orb_dir text"
    ),
    # 52-week high/low breakout
    (
        "signal_log: add week52_signal",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS week52_signal text"
    ),
    (
        "signal_log: add week52_dir",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS week52_dir text"
    ),

    # ── signal_log: sector alignment columns (added 2026-06-04) ───────────────
    (
        "signal_log: add sector_etf",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS sector_etf text"
    ),
    (
        "signal_log: add sector_dir",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS sector_dir text"
    ),

    # ── signal_log: director cluster strong flag (added 2026-06-05) ─────────────
    (
        "signal_log: add director_cluster_strong",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS director_cluster_strong boolean"
    ),

    # ── signal_log: elite senator / POTUS primary columns (added 2026-06-05) ──
    (
        "signal_log: add elite_senate_primary",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS elite_senate_primary boolean"
    ),
    (
        "signal_log: add elite_senator_name",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS elite_senator_name text"
    ),
    (
        "signal_log: add potus_primary",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS potus_primary boolean"
    ),

    # ── senator_scores: add win_rate index for elite query performance ─────────
    (
        "senator_scores: index on win_rate",
        "CREATE INDEX IF NOT EXISTS idx_senator_scores_win_rate ON senator_scores(win_rate DESC)"
    ),

    # ── hvf_scan_log table (added 2026-06-05) ─────────────────────────────────
    (
        "create hvf_scan_log",
        """CREATE TABLE IF NOT EXISTS hvf_scan_log (
            id              uuid primary key default gen_random_uuid(),
            scan_time       text not null,
            ticker          text not null,
            index_name      text,
            hvf_type        text,
            hvf_signal      text,
            hvf_timeframe   text,
            pattern_quality integer,
            risk_reward     numeric,
            entry_level     numeric,
            stop_level      numeric,
            target          numeric,
            recorded_at     timestamptz default now()
        )"""
    ),

]


# -----------------------------------------------------------------------------
# Runner
# -----------------------------------------------------------------------------

def main():
    log.info(f"Connecting to {SUPABASE_HOST}...")
    conn = pg8000.native.Connection(
        host=SUPABASE_HOST,
        port=5432,
        database="postgres",
        user=os.environ["SUPABASE_USER"],
        password=os.environ["SUPABASE_DB_PASSWORD"],
        ssl_context=True
    )

    ok = failed = 0
    for name, sql in MIGRATIONS:
        try:
            conn.run(sql)
            log.info(f"  OK  {name}")
            ok += 1
        except Exception as e:
            log.error(f"FAIL  {name} — {e}")
            failed += 1

    conn.close()
    log.info(f"Schema run complete: {ok} applied, {failed} failed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
