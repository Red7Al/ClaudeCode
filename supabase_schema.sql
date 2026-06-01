-- =============================================================================
-- File:         supabase_schema.sql
-- Author:       Alex Hind
-- Created:      2026-06-01
--
-- Description:
-- -----------------------------------------------------------------------------
-- Complete Supabase (PostgreSQL) schema for the EndToEndTrading system.
-- Run this once in the Supabase SQL editor:
--   Project dashboard → SQL editor → paste and run.
--
-- Safe to re-run: all statements use CREATE TABLE IF NOT EXISTS and
-- ALTER TABLE ... ADD COLUMN IF NOT EXISTS.
--
-- Table inventory:
--   user_profiles      Per-user risk profiles (id, daily_loss_limit, max_open_pos, paper_trade)
--   epic_lookup        IG epic codes by ticker (self-populating cache)
--   positions          Currently open CFD positions
--   trade_log          Full history of every trade opened and closed
--   daily_pnl          Aggregated P&L per user per calendar day
--   macro_snapshot     Macro gate state captured at each session open
--   signal_log         Full signal stack result for every instrument scanned
--   cot_snapshot       CFTC COT data refreshed weekly
--   senator_scores     Scored US senators (Quiver Quant, refreshed weekly)
--   notable_investors  Superinvestor and notable social picks
--   social_mentions    Trump / Musk / Asklivermore social feed mentions
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. USER_PROFILES — per-user risk profiles
-- ---------------------------------------------------------------------------
-- Column names match ig_shim.py exactly: id, daily_loss_limit, max_open_pos, paper_trade
-- This table was created in the previous session — do not rename or alter columns.
create table if not exists user_profiles (
    id                  uuid primary key default gen_random_uuid(),
    daily_loss_limit    numeric(5,2) not null default 3.0,
    max_open_pos        int not null default 5,
    paper_trade         boolean not null default false
);


-- ---------------------------------------------------------------------------
-- 2. EPIC_LOOKUP — IG instrument codes
-- ---------------------------------------------------------------------------
create table if not exists epic_lookup (
    ticker          text primary key,
    epic            text not null,
    description     text,                  -- human-readable name from IG API
    market_status   text,                  -- TRADEABLE / CLOSED / OFFLINE
    min_deal_size   numeric(10,4),
    last_verified   timestamptz,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- Core epics seeded from config.py (verified against IG account HTIRV 2026-05-30)
insert into epic_lookup (ticker, epic, description) values
    ('GBPUSD',  'CS.D.GBPUSD.TODAY.IP',     'GBP/USD Spot'),
    ('AUDUSD',  'CS.D.AUDUSD.TODAY.IP',     'AUD/USD Spot'),
    ('USDJPY',  'CS.D.USDJPY.TODAY.IP',     'USD/JPY Spot'),
    ('EURUSD',  'CS.D.EURUSD.TODAY.IP',     'EUR/USD Spot'),
    ('XAUUSD',  'CS.D.USCGC.TODAY.IP',     'Spot Gold'),
    ('XAGUSD',  'CS.D.USCSI.TODAY.IP',     'Spot Silver'),
    ('OIL',     'CC.D.CL.USS.IP',           'US Crude Oil (WTI)'),
    ('SPX500',  'IX.D.SPTR500.IFD.IP',      'S&P 500'),
    ('NASDAQ',  'IX.D.NASDAQ.IFD.IP',       'NASDAQ 100'),
    ('UK100',   'IX.D.FTSE.IFD.IP',         'FTSE 100'),
    ('JPN225',  'IX.D.NIKKEI.IFD.IP',       'Nikkei 225'),
    ('HK50',    'IX.D.HSIF.IFD.IP',         'Hang Seng'),
    ('DAX',     'IX.D.DAX.IFD.IP',          'DAX 40'),
    ('NVDA',    'UA.D.NVDA.DAILY.IP',       'NVIDIA Corp (24 Hours)'),
    ('AAPL',    'UA.D.AAPL.DAILY.IP',       'Apple Inc (24 Hours)'),
    ('MSFT',    'UA.D.MSFT.DAILY.IP',       'Microsoft Corp (24 Hours)'),
    ('META',    'UB.D.FB.DAILY.IP',         'Meta Platforms (24 Hours)'),
    ('AMZN',    'UA.D.AMZN.DAILY.IP',       'Amazon.com Inc (24 Hours)'),
    ('GOOGL',   'UB.D.GOOGL.DAILY.IP',      'Alphabet Inc Class A (24 Hours)'),
    ('AMD',     'UA.D.AMD.DAILY.IP',        'Advanced Micro Devices (24 Hours)'),
    ('TSLA',    'UD.D.TSLA.DAILY.IP',       'Tesla Inc (24 Hours)'),
    ('PLTR',    'SE.D.PLTRUS.DAILY.IP',     'Palantir Technologies (24 Hours)'),
    ('IBM',     'SD.D.IBM.DAILY.IP',        'IBM Corp (24 Hours)'),
    ('DELL',    'SB.D.DELLUS.DAILY.IP',     'Dell Technologies (24 Hours)'),
    ('NOW',     'UA.D.NOW.DAILY.IP',        'ServiceNow (24 Hours)'),
    ('CRWD',    'UA.D.CRWD.DAILY.IP',       'CrowdStrike (24 Hours)'),
    ('INTC',    'UB.D.INTC.DAILY.IP',       'Intel Corp (24 Hours)'),
    ('MU',      'UA.D.MU.DAILY.IP',         'Micron Technology (24 Hours)'),
    ('BP',      'KA.D.BP.DAILY.IP',         'BP PLC'),
    ('LLOY',    'KA.D.LLOY.DAILY.IP',       'Lloyds Banking Group'),
    ('BTCUSD',  'CS.D.BITCOIN.TODAY.IP',    'Bitcoin/USD Spot'),
    ('ETHUSD',  'CS.D.ETHUSD.TODAY.IP',     'Ethereum/USD Spot')
on conflict (ticker) do nothing;


-- ---------------------------------------------------------------------------
-- 3. POSITIONS — currently open trades
-- ---------------------------------------------------------------------------
create table if not exists positions (
    id              bigserial primary key,
    user_id         uuid not null references users(user_id),
    deal_id         text not null unique,          -- IG deal reference
    ticker          text not null,
    epic            text not null,
    direction       text not null check (direction in ('BUY','SELL')),
    size            numeric(12,4) not null,
    open_price      numeric(16,6) not null,
    stop_loss       numeric(16,6),
    limit_level     numeric(16,6),
    stop_distance   numeric(12,6),
    limit_distance  numeric(12,6),
    session         text,                          -- AUS_OPEN / UK_OPEN / US_OPEN
    signal_summary  text,
    paper_trade     boolean not null default false,
    opened_at       timestamptz not null default now()
);

create index if not exists idx_positions_user_id  on positions(user_id);
create index if not exists idx_positions_ticker    on positions(ticker);
create index if not exists idx_positions_opened_at on positions(opened_at);


-- ---------------------------------------------------------------------------
-- 4. TRADE_LOG — full history (open + close details)
-- ---------------------------------------------------------------------------
create table if not exists trade_log (
    id              bigserial primary key,
    user_id         uuid not null references users(user_id),
    deal_id         text not null,
    ticker          text not null,
    epic            text,
    direction       text not null check (direction in ('BUY','SELL')),
    size            numeric(12,4) not null,
    open_price      numeric(16,6) not null,
    close_price     numeric(16,6),
    stop_loss       numeric(16,6),
    limit_level     numeric(16,6),
    stop_distance   numeric(12,6),
    limit_distance  numeric(12,6),
    pnl             numeric(12,2),                 -- GBP profit/loss
    close_reason    text,                          -- STOP_HIT / TARGET_HIT / SESSION_CLOSE / MANUAL / CIRCUIT_BREAKER
    session         text,                          -- session that opened the trade
    signal_summary  text,                          -- abbreviated signal string
    paper_trade     boolean not null default false,

    -- Entry context (captured at open)
    vix_at_entry    numeric(8,2),
    dxy_at_entry    numeric(10,4),
    yield_spread_at_entry numeric(8,4),
    macro_regime_at_entry text,

    -- Exit metrics
    max_favourable_excursion numeric(12,6),        -- furthest price moved in our favour
    max_adverse_excursion    numeric(12,6),        -- furthest price moved against us
    hold_duration_minutes    int,

    opened_at       timestamptz not null default now(),
    closed_at       timestamptz
);

create index if not exists idx_trade_log_user_id   on trade_log(user_id);
create index if not exists idx_trade_log_ticker    on trade_log(ticker);
create index if not exists idx_trade_log_opened_at on trade_log(opened_at);
create index if not exists idx_trade_log_closed_at on trade_log(closed_at);
create index if not exists idx_trade_log_session   on trade_log(session);


-- ---------------------------------------------------------------------------
-- 5. DAILY_PNL — aggregated per user per day
-- ---------------------------------------------------------------------------
create table if not exists daily_pnl (
    id              bigserial primary key,
    user_id         uuid not null references users(user_id),
    trade_date      date not null,
    total_pnl       numeric(12,2) not null default 0,
    trade_count     int not null default 0,
    win_count       int not null default 0,
    loss_count      int not null default 0,
    daily_loss_hit  boolean not null default false,
    updated_at      timestamptz not null default now(),
    unique (user_id, trade_date)
);

create index if not exists idx_daily_pnl_user_date on daily_pnl(user_id, trade_date);


-- ---------------------------------------------------------------------------
-- 6. MACRO_SNAPSHOT — macro gate state per session run
-- ---------------------------------------------------------------------------
create table if not exists macro_snapshot (
    id              bigserial primary key,
    session         text not null,
    vix             numeric(8,2),
    dxy             numeric(10,4),
    us2y            numeric(8,4),
    us10y           numeric(8,4),
    yield_spread    numeric(8,4),
    macro_gate_pass boolean,
    gate_reason     text,
    snapshot_time   timestamptz not null default now()
);

create index if not exists idx_macro_snapshot_time on macro_snapshot(snapshot_time desc);


-- ---------------------------------------------------------------------------
-- 7. SIGNAL_LOG — full signal stack result per instrument per session
-- ---------------------------------------------------------------------------
create table if not exists signal_log (
    id                  bigserial primary key,
    session             text not null,
    ticker              text not null,

    -- Macro
    macro_gate_pass     boolean,

    -- Primary signals
    options_bias        text,                      -- BULLISH / BEARISH / NEUTRAL
    call_put_ratio      numeric(8,4),
    iv_rank             int,
    bb_squeeze          boolean,
    bb_breakout_dir     text,                      -- BULLISH / BEARISH / null

    -- Confirmation signals
    gex_bias            text,                      -- PINNED / TRENDING / NEUTRAL
    vwap_position       text,                      -- ABOVE / BELOW
    cot_bias            text,                      -- BULLISH / BEARISH / NEUTRAL
    director_signal     boolean,
    activist_signal     boolean,
    senate_signal       boolean,
    senate_senator      text,
    notable_investor    text,
    social_mention      text,

    -- Price action
    pa_verdict          text,                      -- CONFIRM_LONG / CONFIRM_SHORT / WAIT

    -- Decision
    primary_count       int,
    confirmation_count  int,
    direction           text,                      -- BUY / SELL / null
    trade_triggered     boolean not null default false,

    scanned_at          timestamptz not null default now()
);

create index if not exists idx_signal_log_session    on signal_log(session);
create index if not exists idx_signal_log_ticker     on signal_log(ticker);
create index if not exists idx_signal_log_scanned_at on signal_log(scanned_at desc);
create index if not exists idx_signal_log_triggered  on signal_log(trade_triggered);


-- ---------------------------------------------------------------------------
-- 8. COT_SNAPSHOT — CFTC Commitment of Traders, refreshed weekly
-- ---------------------------------------------------------------------------
create table if not exists cot_snapshot (
    id                  bigserial primary key,
    instrument          text not null,
    report_date         date not null,
    comm_net            numeric(14,0),             -- commercial net long/short
    comm_net_change     numeric(14,0),             -- week-on-week change
    bias                text,                      -- BULLISH / BEARISH / NEUTRAL
    cot_score           numeric(8,4),
    comm_extreme        text,                      -- EXTREME_LONG / EXTREME_SHORT / NORMAL
    mm_extreme          text,
    price_divergence    text,                      -- DIVERGING / NONE
    oi_signal           text,                      -- BULLISH / BEARISH / NEUTRAL
    created_at          timestamptz not null default now(),
    unique (instrument, report_date)
);

create index if not exists idx_cot_instrument_date on cot_snapshot(instrument, report_date desc);


-- ---------------------------------------------------------------------------
-- 9. SENATOR_SCORES — US Senate equity trade performance (weekly refresh)
-- ---------------------------------------------------------------------------
create table if not exists senator_scores (
    id              bigserial primary key,
    senator_name    text not null unique,
    score           numeric(10,6),                -- win_rate × avg_excess_return
    win_rate        numeric(6,4),
    avg_excess_return numeric(8,4),
    trade_count     int,
    qualified       boolean not null default false, -- min 5 trades required
    last_updated    timestamptz not null default now()
);


-- ---------------------------------------------------------------------------
-- 10. NOTABLE_INVESTORS — superinvestors and social picks
-- ---------------------------------------------------------------------------
create table if not exists notable_investors (
    id              bigserial primary key,
    investor_name   text not null,                 -- 'Berkshire Hathaway', 'Donald Trump', etc.
    source          text,                          -- '13F', 'OGE', 'Capitol Trades', 'Social'
    action          text,                          -- BUY / NEW / ADD / SELL / TRIM
    ticker          text not null,
    shares          numeric(16,0),
    value_usd       numeric(18,2),
    disclosed_at    date,
    recorded_at     timestamptz not null default now()
);

create index if not exists idx_notable_investors_ticker on notable_investors(ticker);
create index if not exists idx_notable_investors_date   on notable_investors(disclosed_at desc);


-- ---------------------------------------------------------------------------
-- 11. SOCIAL_MENTIONS — Trump / Musk / Asklivermore social feed picks
-- ---------------------------------------------------------------------------
create table if not exists social_mentions (
    id              bigserial primary key,
    author          text not null,                 -- 'Trump', 'Musk', 'Asklivermore', etc.
    platform        text,                          -- 'TruthSocial', 'X', 'Twitter'
    post_text       text,
    tickers_found   text[],                        -- array of tickers mentioned
    sentiment       text,                          -- BULLISH / BEARISH / NEUTRAL
    post_time       timestamptz not null,
    recorded_at     timestamptz not null default now()
);

create index if not exists idx_social_mentions_tickers  on social_mentions using gin(tickers_found);
create index if not exists idx_social_mentions_post_time on social_mentions(post_time desc);
create index if not exists idx_social_mentions_author   on social_mentions(author);


-- =============================================================================
-- MIGRATION 001 — Add columns added on 2026-06-01
-- (signal_log extended with primary_count, call_put_ratio, direction, pa_verdict)
-- Already included in the CREATE TABLE above.
-- This section is retained for documentation only — no action needed on a fresh install.
-- =============================================================================

-- alter table signal_log add column if not exists call_put_ratio   numeric(8,4);
-- alter table signal_log add column if not exists primary_count    integer default 0;
-- alter table signal_log add column if not exists direction        text;
-- alter table signal_log add column if not exists pa_verdict       text;
