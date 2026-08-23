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
# 1.7.0   2026-08-17  Claude      Index hygiene (user: "apply the fixes if the database will let you"). Drop five
#                                 IDENTICAL duplicate indexes found by the Supabase performance linter and confirmed
#                                 against 87 days of pg_stat_user_indexes — a duplicate can never be chosen twice by
#                                 the planner but is maintained on every write. Add the one missing FK covering index
#                                 on scanner_snapshot_current(version_id). Supabase-managed schemas (auth/storage/
#                                 realtime) deliberately untouched. Ongoing drift is reported by the new weekly
#                                 run_db_index_audit.py rather than fixed blind.
# 1.6.0   2026-06-16  Alex Hind   x_publications table — dedup record for live X publications (publish_one_to_x skips
#                                 re-publishing a ticker within 12h). user 2026-06-16: duplicate publications.
# 1.5.0   2026-06-15  Alex Hind   signal_log: add analyst_signal + analyst_recommendation (broker recommendation, so the
#                                 X-draft tweet can surface it as a confirmation — user 2026-06-15).
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
        "signal_log: add analyst_signal (broker recommendation — user 2026-06-15)",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS analyst_signal text"
    ),
    (
        "signal_log: add analyst_recommendation (broker consensus key)",
        "ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS analyst_recommendation text"
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
            notes           text,
            proximity_pct   numeric
        )"""
    ),
    (
        # Per-user Pre-order proximity band (user 2026-08-03, P-75): idempotent add for existing tables —
        # records the placement band a WATCHING row was queued under so reconcile promotes it consistently.
        "working_orders: add proximity_pct",
        "ALTER TABLE working_orders ADD COLUMN IF NOT EXISTS proximity_pct numeric"
    ),
    (
        # Owner-scoped Let Winners Run binding (user 2026-08-22). ig_shim created these at RUNTIME, with a
        # DDL statement on the order-placement path, and they were declared in no schema file at all -- so a
        # database rebuilt from the schema of record had no column the working-order INSERT names, and that
        # INSERT sits inside a "never raises" handler that only logs. The failure mode was a live IG order
        # with no working_orders row. Declared here so the runtime DDL is a redundant safety net, not the
        # only thing standing between a placed order and its record.
        "working_orders: add Let Winners Run owner binding",
        "ALTER TABLE working_orders ADD COLUMN IF NOT EXISTS lwr_owner_login text"
    ),
    (
        "working_orders: add Let Winners Run account fingerprint",
        "ALTER TABLE working_orders ADD COLUMN IF NOT EXISTS lwr_account_fingerprint text"
    ),
    (
        "positions: add Let Winners Run owner binding",
        "ALTER TABLE positions ADD COLUMN IF NOT EXISTS lwr_owner_login text"
    ),
    (
        "positions: add Let Winners Run account fingerprint",
        "ALTER TABLE positions ADD COLUMN IF NOT EXISTS lwr_account_fingerprint text"
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
    # ── x_publications — dedup record for LIVE X (Twitter) publications (added 2026-06-16) ──
    # publish_one_to_x.py records each live X publication here and skips re-publishing the same
    # ticker within 12h (user 2026-06-16: duplicate publications). The module also creates this
    # table defensively, so it works even if this migration hasn't been run.
    (
        "create x_publications",
        """CREATE TABLE IF NOT EXISTS x_publications (
            id           bigserial primary key,
            ticker       text not null,
            tweet_id     text,
            published_at timestamptz default now()
        )"""
    ),
    (
        "x_publications: index on ticker + time",
        "CREATE INDEX IF NOT EXISTS idx_x_pub_ticker_time ON x_publications(ticker, published_at DESC)"
    ),

    # ── price_history — golden daily OHLCV per instrument (added 2026-06-29) ──
    # The authoritative price store. price_store.py + price_audit.py also create these defensively, so
    # they work even if this migration hasn't been run. (ticker, bar_date) PK serves the charts'
    # single-instrument range scans AND the audit's per-day cross-instrument scans; source + recorded_at
    # / updated_at make every price traceable.
    (
        "create price_history",
        """CREATE TABLE IF NOT EXISTS price_history (
            ticker      text             NOT NULL,
            bar_date    date             NOT NULL,
            open        double precision,
            high        double precision,
            low         double precision,
            close       double precision,
            volume         bigint,
            source         text             NOT NULL,
            double_checked boolean          NOT NULL DEFAULT false,
            recorded_at    timestamptz      NOT NULL DEFAULT now(),
            updated_at     timestamptz      NOT NULL DEFAULT now(),
            PRIMARY KEY (ticker, bar_date)
        )"""
    ),
    (
        "price_history: add double_checked (legacy tables)",
        "ALTER TABLE price_history ADD COLUMN IF NOT EXISTS double_checked boolean NOT NULL DEFAULT false"
    ),
    (
        "price_history: index on ticker + date desc",
        "CREATE INDEX IF NOT EXISTS idx_price_history_ticker_date ON price_history(ticker, bar_date DESC)"
    ),
    (
        "price_history: index on bar_date",
        "CREATE INDEX IF NOT EXISTS idx_price_history_bar_date ON price_history(bar_date)"
    ),
    (
        "create price_audit_log",
        """CREATE TABLE IF NOT EXISTS price_audit_log (
            id              bigserial primary key,
            run_at          timestamptz not null default now(),
            mode            text        not null,
            source          text        not null,
            tickers_checked int         not null,
            bars_written    int         not null,
            discrepancies   int         not null,
            max_drift_pct   double precision,
            duration_s      double precision,
            notes           text
        )"""
    ),

    # Scanner publication metadata. The large immutable JSON objects live in a PRIVATE Supabase Storage
    # bucket; these two tables provide an atomic current-version pointer and an auditable version history.
    (
        "create scanner_snapshot_versions",
        """CREATE TABLE IF NOT EXISTS scanner_snapshot_versions (
            id              bigserial primary key,
            generated_utc   timestamptz not null,
            object_path     text not null unique,
            sha256          text not null,
            record_count    integer not null check (record_count >= 0),
            byte_count      bigint not null check (byte_count > 0),
            schema_version  integer not null default 1,
            source          text not null default 'unknown',
            published_at    timestamptz not null default now()
        )"""
    ),
    (
        "scanner snapshot: index generated time",
        "CREATE INDEX IF NOT EXISTS idx_scanner_snapshot_generated ON scanner_snapshot_versions(generated_utc DESC)"
    ),
    (
        "create scanner_snapshot_current",
        """CREATE TABLE IF NOT EXISTS scanner_snapshot_current (
            singleton   boolean primary key default true check (singleton),
            version_id  bigint not null references scanner_snapshot_versions(id),
            updated_at  timestamptz not null default now()
        )"""
    ),
    # ── Index hygiene, 2026-08-17 (user: "apply the fixes if the database will let you") ──────────────
    # Supabase's performance linter found five pairs of IDENTICAL indexes. A duplicate earns nothing:
    # the planner can only use one, while EVERY insert and update has to maintain both. Verified over
    # 87 days of pg_stat_user_indexes before dropping, and in each pair the one dropped is the one with
    # ZERO scans (where both were unused the better-named survivor was kept). None of these is created
    # anywhere in this file, so re-running the migration will not resurrect them.
    #
    # Deliberately NOT touched: everything in the auth/storage/realtime schemas. Those are Supabase's
    # own, several are also flagged unused, and they are not ours to manage. Nor the remaining unused
    # PUBLIC indexes (idx_senator_scores_win_rate, idx_scanner_snapshot_generated, idx_positions_epic,
    # idx_social_mentions_author) -- ~56 kB between them, two of them created by this very file, and
    # "unused for 87 days" is not the same as "never needed" for a rarely-hit query path. They are
    # reported weekly by run_db_index_audit.py instead of being guessed at.
    (
        "index hygiene: drop duplicate index signal_log(ticker)",          # keeps idx_signal_log_ticker
        "DROP INDEX IF EXISTS idx_signal_ticker"                            # 2456 kB, the largest of the set
    ),
    (
        "index hygiene: drop duplicate index positions(user_id)",          # keeps idx_positions_user_id
        "DROP INDEX IF EXISTS idx_positions_user"
    ),
    (
        "index hygiene: drop duplicate index trade_log(user_id)",          # keeps idx_trade_log_user_id
        "DROP INDEX IF EXISTS idx_trade_log_user"
    ),
    (
        "index hygiene: drop duplicate index trade_log(closed_at)",        # keeps idx_trade_log_closed_at
        "DROP INDEX IF EXISTS idx_trade_log_closed"
    ),
    (
        "index hygiene: drop duplicate index social_mentions(tickers_found)",  # keeps idx_social_mentions_tickers
        "DROP INDEX IF EXISTS idx_social_tickers"
    ),
    (
        # Found by run_db_index_audit.py, not by Supabase's linter: idx_daily_pnl_user_date is byte-identical
        # to the index backing the UNIQUE(user_id, trade_date) constraint. The constraint index has to stay --
        # it enforces correctness, not just speed -- so the plain one is the redundant half.
        "index hygiene: drop duplicate index daily_pnl(user_id, trade_date)",
        "DROP INDEX IF EXISTS idx_daily_pnl_user_date"
    ),
    (
        # The linter's one MISSING index: a foreign key with no covering index makes the referenced-side
        # delete/update scan the whole child table, and cascades take a stronger lock while they do it.
        "index hygiene: cover scanner_snapshot_current.version_id FK",
        "CREATE INDEX IF NOT EXISTS idx_scanner_snapshot_current_version "
        "ON scanner_snapshot_current(version_id)"
    ),
    # ── Brute-force protection for /api/login (user 2026-08-18, SECURITY_RECOMMENDATIONS HIGH #1) ─────
    # api_login accepted unlimited password guesses: no per-IP cap, no per-account cap, no lockout, no
    # alert. Logins are guessable names (Alex, Owner, Rich) rather than emails, the site is now on a
    # stable public domain instead of a random tunnel hostname, and a successful guess reaches the order
    # path -- not just data.
    #
    # The counter has to be PERSISTED, not held in memory. The 2026-07-28 advice ("a small in-memory
    # counter is enough for a single-process app") is void: under CGI every request is a fresh Python
    # process, so an in-memory counter is created and destroyed inside one attempt. It counts to one
    # forever and looks like protection while providing none.
    (
        "create login_attempts (persisted brute-force counter)",
        """CREATE TABLE IF NOT EXISTS login_attempts (
            ip             text        not null,
            name           text        not null,
            attempts       integer     not null default 0,
            first_attempt  timestamptz not null default now(),
            last_attempt   timestamptz not null default now(),
            locked_until   timestamptz,
            primary key (ip, name)
        )"""
    ),
    (
        # Sweeping expired locks and counting a burst across many usernames from one IP both scan by
        # time, not by the primary key.
        "login_attempts: index last_attempt",
        "CREATE INDEX IF NOT EXISTS idx_login_attempts_last ON login_attempts(last_attempt DESC)"
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
