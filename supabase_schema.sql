-- =============================================================================
-- File:         supabase_schema.sql
-- Author:       Alex Hind
-- Created:      2026-05-29 (previous session)
-- Updated:      2026-06-01 — verified against live DB via information_schema
--
-- This file reflects the ACTUAL live schema as of 2026-06-01.
-- Column names verified by querying information_schema.columns directly.
-- Do NOT assume column names — always refer to this file.
--
-- Tables:
--   user_profiles      Per-user risk profiles
--   epic_lookup        IG epic codes (self-populating cache)
--   positions          Currently open CFD positions
--   trade_log          Full trade history
--   daily_pnl          Aggregated P&L per user per day
--   macro_snapshot     Macro gate state per session
--   signal_log         Full signal stack per instrument per scan
--   cot_snapshot       CFTC COT data (weekly refresh)
--   senator_scores     US Senate equity trade performance scores
--   notable_investors  Superinvestors + social picks
--   social_mentions    Trump/Musk/Asklivermore social feed
--   geopolitical_risk  Geopolitical risk flags per instrument
-- =============================================================================


-- ---------------------------------------------------------------------------
-- user_profiles  (3 rows — Owner, Wife, Son)
-- Primary key: id (uuid)
-- Referenced by: positions.user_id, trade_log.user_id, daily_pnl.user_id
-- ---------------------------------------------------------------------------
create table if not exists user_profiles (
    id                uuid primary key default gen_random_uuid(),
    name              text not null,
    ig_account_id     text not null,
    risk_per_trade    numeric not null,
    max_open_pos      integer not null default 5,
    daily_loss_limit  numeric not null,
    paper_trade       boolean not null default false,
    active            boolean not null default true,
    created_at        timestamptz default now()
);


-- ---------------------------------------------------------------------------
-- epic_lookup  (76 rows)
-- ---------------------------------------------------------------------------
create table if not exists epic_lookup (
    ticker       text primary key,
    epic         text not null,
    description  text,
    currency     text,
    market_type  text,
    last_seen    timestamptz default now()
);


-- ---------------------------------------------------------------------------
-- positions  (open trades)
-- ---------------------------------------------------------------------------
create table if not exists positions (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid references user_profiles(id),
    epic            text not null,
    ticker          text not null,
    direction       text not null,
    size            numeric not null,
    open_price      numeric not null,
    stop_loss       numeric not null,
    take_profit     numeric,
    atr_multiplier  numeric,
    deal_id         text,
    paper_trade     boolean not null default false,
    opened_at       timestamptz default now(),
    session         text,
    signal_summary  text
);


-- ---------------------------------------------------------------------------
-- trade_log  (closed trade history)
-- ---------------------------------------------------------------------------
create table if not exists trade_log (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid references user_profiles(id),
    epic            text not null,
    ticker          text not null,
    direction       text not null,
    size            numeric not null,
    open_price      numeric not null,
    close_price     numeric not null,
    stop_loss       numeric not null,
    pnl             numeric not null,
    pnl_pct         numeric,
    paper_trade     boolean not null default false,
    opened_at       timestamptz not null,
    closed_at       timestamptz default now(),
    session         text,
    close_reason    text,
    signal_summary  text
);


-- ---------------------------------------------------------------------------
-- daily_pnl
-- ---------------------------------------------------------------------------
create table if not exists daily_pnl (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid references user_profiles(id),
    trade_date      date not null,
    total_pnl       numeric not null default 0,
    trade_count     integer not null default 0,
    win_count       integer not null default 0,
    loss_count      integer not null default 0,
    daily_loss_hit  boolean default false,
    unique(user_id, trade_date)
);


-- ---------------------------------------------------------------------------
-- macro_snapshot
-- ---------------------------------------------------------------------------
create table if not exists macro_snapshot (
    id               uuid primary key default gen_random_uuid(),
    snapshot_time    timestamptz default now(),
    session          text not null,
    vix              numeric,
    dxy              numeric,
    us2y             numeric,
    us10y            numeric,
    yield_spread     numeric,
    macro_gate_pass  boolean,
    gate_reason      text
);


-- ---------------------------------------------------------------------------
-- signal_log  (every instrument scanned, every session)
-- NOTE: columns are added incrementally via run_schema.py (idempotent ALTER TABLE).
--       Re-run run_schema.py after pulling any new columns.
-- ---------------------------------------------------------------------------
create table if not exists signal_log (
    id                  uuid primary key default gen_random_uuid(),
    session_time        timestamptz default now(),
    session             text not null,
    ticker              text not null,
    epic                text,
    macro_gate_pass     boolean,
    options_bias        text,
    call_put_ratio      numeric,
    iv_rank             numeric,
    gex_bias            text,
    vwap_position       text,
    cot_bias            text,
    bb_squeeze          boolean,
    bb_breakout_dir     text,
    director_signal     boolean,
    activist_signal     boolean,
    senate_signal       boolean,
    senate_senator      text,
    notable_investor    text,
    social_mention      text,
    primary_count       integer default 0,
    confirmation_count  integer,
    direction           text,
    pa_verdict          text,
    trade_triggered     boolean default false,
    signal_summary      text,
    adx_signal          text,
    adx_dir             text,
    obv_signal          text,
    volume_signal       text,
    volume_ratio        numeric,
    hvf_type            text,
    hvf_signal          text,
    hvf_h3_level        numeric,
    hvf_stop_level      numeric,
    hvf_target          numeric,
    hvf_risk_reward     numeric,
    hvf_quality         integer,
    orb_signal          text,
    orb_dir             text,
    week52_signal       text,
    week52_dir          text,
    pa_score            numeric,
    sector_etf          text,
    sector_dir          text
);


-- ---------------------------------------------------------------------------
-- cot_snapshot  (9 rows — weekly CFTC data)
-- ---------------------------------------------------------------------------
create table if not exists cot_snapshot (
    id                  uuid primary key default gen_random_uuid(),
    report_date         date not null,
    instrument          text not null,
    cftc_code           text,
    comm_net            numeric,
    comm_net_change     numeric,
    noncomm_net         numeric,
    noncomm_net_change  numeric,
    open_interest       numeric,
    pct_comm_long       numeric,
    pct_comm_short      numeric,
    bias                text,
    updated_at          timestamptz default now(),
    managed_money_long  numeric,
    managed_money_short numeric,
    managed_money_net   numeric,
    managed_money_change numeric,
    oi_change           numeric,
    comm_net_pct_rank   numeric,
    mm_net_pct_rank     numeric,
    comm_extreme        text,
    mm_extreme          text,
    price_divergence    text,
    oi_signal           text,
    cot_score           numeric,
    unique(instrument, report_date)
);


-- ---------------------------------------------------------------------------
-- senator_scores  (5 rows)
-- ---------------------------------------------------------------------------
create table if not exists senator_scores (
    id                  uuid primary key default gen_random_uuid(),
    senator_name        text not null unique,
    party               text,
    state               text,
    trade_count         integer not null default 0,
    win_rate            numeric not null default 0,
    avg_excess_return   numeric not null default 0,
    score               numeric not null default 0,
    qualified           boolean not null default false,
    last_updated        timestamptz default now()
);


-- ---------------------------------------------------------------------------
-- notable_investors  (73 rows)
-- ---------------------------------------------------------------------------
create table if not exists notable_investors (
    id              uuid primary key default gen_random_uuid(),
    investor_name   text not null,
    ticker          text not null,
    action          text not null,
    shares          numeric,
    market_value    numeric,
    source          text not null,
    disclosed_at    date not null,
    quarter         text,
    notes           text,
    direction       text,           -- system read (LONG/SHORT/WATCH) at the moment this account flagged the ticker (2026-06-24)
    post_url        text,           -- the account's tweet link for this pick (2026-06-24)
    recorded_at     timestamptz default now()
);
alter table notable_investors add column if not exists direction text;
alter table notable_investors add column if not exists post_url text;


-- ---------------------------------------------------------------------------
-- social_mentions
-- ---------------------------------------------------------------------------
create table if not exists social_mentions (
    id              uuid primary key default gen_random_uuid(),
    author          text not null,
    platform        text not null,
    post_text       text not null,
    tickers_found   text[],
    sentiment       text,
    post_time       timestamptz not null,
    session         text,
    acted_on        boolean default false,
    recorded_at     timestamptz default now()
);


-- ---------------------------------------------------------------------------
-- geopolitical_risk  (from previous session — 0 rows)
-- ---------------------------------------------------------------------------
create table if not exists geopolitical_risk (
    id           uuid primary key default gen_random_uuid(),
    instrument   text not null,
    risk_level   text not null,
    description  text not null,
    active       boolean not null default true,
    source       text,
    created_at   timestamptz default now(),
    updated_at   timestamptz default now()
);


-- ---------------------------------------------------------------------------
-- hvf_scan_log  (daily HVF report — one row per pattern per scan)
-- ---------------------------------------------------------------------------
create table if not exists hvf_scan_log (
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
);


-- ---------------------------------------------------------------------------
-- web_best_settings_history  (one persistent recommendation snapshot/user/day)
-- ---------------------------------------------------------------------------
create table if not exists web_best_settings_history (
    id                bigserial primary key,
    ts                timestamptz not null default now(),
    user_id           text not null,
    snapshot_day      date not null default current_date,
    dataset_generated text,
    data_through      text,
    model_json        jsonb not null,
    options_json      jsonb not null,
    fingerprint       text not null,
    unique (user_id, snapshot_day)
);
create index if not exists idx_web_best_settings_user
    on web_best_settings_history (user_id, snapshot_day desc);


-- ---------------------------------------------------------------------------
-- web_json_store  (small durable stores moved from host-local JSON files)
-- ---------------------------------------------------------------------------
-- Values retain their existing JSON shape so deployments can dual-read/write during migration.
-- snapshot.json and price caches are deliberately excluded: they are rebuildable runtime caches.
create table if not exists web_json_store (
    store_key   text primary key,
    payload     jsonb not null,
    revision    bigint not null default 1,
    updated_at  timestamptz not null default now()
);
alter table web_json_store add column if not exists revision bigint not null default 1;
