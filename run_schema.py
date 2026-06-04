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

]


# -----------------------------------------------------------------------------
# Runner
# -----------------------------------------------------------------------------

def main():
    log.info(f"Connecting to {SUPABASE_HOST}...")
    conn = pg8000.native.Connection(
        host=SUPABASE_HOST,
        port=6543,
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
