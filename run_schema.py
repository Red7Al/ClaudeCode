# ======================================================================================================================
# File:         run_schema.py
# Author:       Alex Hind
# Created:      2026-06-02
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
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
# ----------------------------------------------------------------------------------------------------------------------
# 1.3.0   2026-06-13  Alex Hind   signal_log: add vwap_pct (numeric) — % distance from intraday VWAP, shown on the X
#                                 post card (user 2026-06-13). Idempotent ADD COLUMN IF NOT EXISTS.
# 1.4.0   2026-06-13  Alex Hind   hvf_suppressed_log table — invariant-rejected HVF results logged for reporting
#                                 (user 2026-06-13), replacing the Slack alert for that bad-data class.
# 1.0.0   2026-06-02  Alex Hind   Initial build. Idempotent migrations for signal_log, positions, macro_snapshot,
#                                 hvf_scan_log, and epic_lookup tables.
# 1.2.0   2026-06-11  Alex Hind   missed_trade_log table — dedupes TRADEABLE SIGNAL NOT PLACED alerts: one row per (day,
#                                 ticker, direction, reason class), repeats bump occurrences.
# 1.1.0   2026-06-10  Alex Hind   working_orders table — pending HVF entry orders placed on IG (entry at H3 with
#                                 stop+target). Kept separate from positions: a pending order is NOT a position; putting
#                                 it in positions would make run_monitor falsely record it as closed. Status lifecycle
#                                 PENDING → FILLED/CANCELLED/EXPIRED.
# ======================================================================================================================

import os
from db_pool import get_db as _pool_get_db   # resilient session-pooler connection (timeout+retry)
import logging
import pg8000.native

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("run_schema")

SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"

# ----------------------------------------------------------------------------------------------------------------------
# Migrations — applied in order, each is idempotent
# ----------------------------------------------------------------------------------------------------------------------

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
    (
        # user 2026-06-13: % distance from intraday VWAP, shown on the X post card.
        "signal_log: add vwap_pct",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS vwap_pct numeric"
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

    # ── signal_log: HVF columns (added 2026-06-04) ────────────────────────────────────────────────────────────────────
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

    # ── signal_log: pa_score numeric (added 2026-06-04) ───────────────────────────────────────────────────────────────
    (
        "signal_log: add pa_score",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS pa_score numeric"
    ),

    # ── signal_log: new primary signal columns (added 2026-06-04) ─────────────────────────────────────────────────────
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

    # ── signal_log: sector alignment columns (added 2026-06-04) ───────────────────────────────────────────────────────
    (
        "signal_log: add sector_etf",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS sector_etf text"
    ),
    (
        "signal_log: add sector_dir",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS sector_dir text"
    ),

    # ── signal_log: director cluster strong flag (added 2026-06-05) ───────────────────────────────────────────────────
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

    # ── senator_scores: add win_rate index for elite query performance ────────────────────────────────────────────────
    (
        "senator_scores: index on win_rate",
        "CREATE INDEX IF NOT EXISTS idx_senator_scores_win_rate ON senator_scores(win_rate DESC)"
    ),

    # ── hvf_scan_log table (added 2026-06-05) ─────────────────────────────────────────────────────────────────────────
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

    # ── working_orders table (added 2026-06-10) ───────────────────────────────────────────────────────────────────────
    # Pending HVF entry orders on IG. NOT positions — run_monitor must never see
    # these in the positions table or it would falsely log them as closed trades.
    # Lifecycle: PENDING → FILLED (reconcile inserts the positions row) /
    #            CANCELLED / EXPIRED. Caps count today's PENDING rows.
    (
        "create working_orders",
        """CREATE TABLE IF NOT EXISTS working_orders (
            id              bigserial primary key,
            deal_ref        text,
            deal_id         text unique,
            user_id         text not null,
            ticker          text not null,
            epic            text not null,
            direction       text not null,
            size            numeric not null,
            entry_level     numeric not null,
            stop_level      numeric,
            limit_level     numeric,
            otype           text,
            hvf_type        text,
            status          text not null default 'PENDING',
            paper_trade     boolean default false,
            session         text,
            signal_summary  text,
            good_till       timestamptz,
            placed_at       timestamptz default now(),
            updated_at      timestamptz default now(),
            filled_at       timestamptz,
            fill_deal_id    text,
            notes           text
        )"""
    ),
    (
        "working_orders: index on status",
        "CREATE INDEX IF NOT EXISTS idx_working_orders_status ON working_orders(status)"
    ),
    (
        "working_orders: index on ticker + placed_at",
        "CREATE INDEX IF NOT EXISTS idx_working_orders_ticker_placed ON working_orders(ticker, placed_at DESC)"
    ),
    # ── hvf_watch_state — stores last-posted HVF watch fingerprint for dedup ──
    (
        "create hvf_watch_state",
        """CREATE TABLE IF NOT EXISTS hvf_watch_state (
            id          bigserial primary key,
            key         text not null unique,
            fingerprint text not null,
            posted_at   timestamptz default now()
        )"""
    ),
    # ── data_quality_log — nightly Yahoo-vs-IG price audit (user 2026-06-12) ──
    (
        "create data_quality_log",
        """CREATE TABLE IF NOT EXISTS data_quality_log (
            id                  bigserial primary key,
            audit_date          date not null default current_date,
            ticker              text not null,
            days_compared       integer,
            close_max_dev_pct   numeric,
            phantom_high_wicks  integer,
            phantom_low_wicks   integer,
            verdict             text,
            detail              text,
            created_at          timestamptz default now(),
            unique (audit_date, ticker)
        )"""
    ),
    # ── hvf_suppressed_log — HVF results the runtime invariant guard rejected (user
    #    2026-06-13). Bad-data setups (e.g. absurd R:R from near-zero risk) that were
    #    never posted/traded; logged here for periodic reporting INSTEAD of a Slack
    #    #alerts ping (which was just noise). ──
    (
        "create hvf_suppressed_log",
        """CREATE TABLE IF NOT EXISTS hvf_suppressed_log (
            id            bigserial primary key,
            suppressed_at timestamptz default now(),
            ticker        text not null,
            hvf_timeframe text,
            hvf_type      text,
            risk_reward   numeric,
            violations    text
        )"""
    ),
    # ── ig_validation_log — daily cache of IG pivot validation results so the
    #    2-hourly watches reuse one fetch per ticker per day (allowance budget) ──
    (
        "create ig_validation_log",
        """CREATE TABLE IF NOT EXISTS ig_validation_log (
            id            bigserial primary key,
            trade_date    date not null default current_date,
            ticker        text not null,
            ig_validated  boolean,
            mismatch      text,
            entry_level   numeric,
            stop_level    numeric,
            target        numeric,
            risk_reward   numeric,
            created_at    timestamptz default now(),
            unique (trade_date, ticker)
        )"""
    ),
    # ── missed_trade_log — dedupes TRADEABLE-SIGNAL-NOT-PLACED alerts ─────────────────────────────────────────────────
    # One row per (day, ticker, direction, reason class). First occurrence posts
    # a full #alerts message with corrective action; repeats only bump the
    # counter. Session close posts one summary of the day's rows.
    (
        "create missed_trade_log",
        """CREATE TABLE IF NOT EXISTS missed_trade_log (
            id             bigserial primary key,
            trade_date     date not null default current_date,
            ticker         text not null,
            direction      text not null,
            reason_class   text not null,
            last_reason    text,
            signal_summary text,
            occurrences    integer not null default 1,
            first_seen     timestamptz default now(),
            last_seen      timestamptz default now(),
            unique (trade_date, ticker, direction, reason_class)
        )"""
    ),

]


# ----------------------------------------------------------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------------------------------------------------------

def main():
    log.info(f"Connecting to {SUPABASE_HOST}...")
    conn = _pool_get_db()

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
