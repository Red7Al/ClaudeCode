---
name: ah-data-dictionary
description: >
  The Supabase schema behind the Squeeze scanner: every table, what it holds, which job writes
  it and when, and the traps that have actually bitten. Use this skill whenever the user asks
  what a table contains, where a figure comes from, which job populates something, why a column
  is empty, or how much space the database is using. The structural half is generated from the
  live schema by build_data_dictionary.py -- re-run it after any migration.
---

# AH Data Dictionary — the Supabase schema

Generated from the live database on **2026-09-06**. Total public schema: **397 MB** of the 500 MB free tier.

Re-generate with `python build_data_dictionary.py`. The column lists, sizes and row counts come from the database and cannot drift; the notes are curated and are the part worth reading.

## Read this first

- **`squeeze_history` has no `direction` column.** Direction is `hvf_type`, BULLISH or BEARISH.
- **`instrument_mcap.mcap`**, not `market_cap`, and it is in the instrument's own currency.
- **`instrument_metrics_daily.as_of` is not the bar it describes** — see its entry below. This one has real money attached to it.
- **`web_users` writes fail closed** when Supabase is unreachable, by design.

## Tables, largest first

### `price_history`  <span>(343 MB, ~1,852,828 rows)</span>

**Holds** — Daily OHLCV bars for the whole universe. The largest object in the database by far.

**Written by** — price_store, during the daily price refresh (Morning Chain step 1).

**Watch out** — 4.5-year retention. run_price_history_prune.py exists for it and its VACUUM must NEVER be scheduled. At ~343 MB this is the reason the 500 MB free tier is a live constraint.

| column | type | null |
|---|---|---|
| `ticker` | text | no |
| `bar_date` | date | no |
| `open` | double precision | yes |
| `high` | double precision | yes |
| `low` | double precision | yes |
| `close` | double precision | yes |
| `volume` | bigint | yes |
| `source` | text | no |
| `recorded_at` | timestamp with time zone | no |
| `updated_at` | timestamp with time zone | no |
| `double_checked` | boolean | no |

### `signal_log`  <span>(19 MB, ~54,136 rows)</span>

**Holds** — Historical per-session signal records.

**Written by** — The session monitors.

**Watch out** — DEAD since the session monitors were disabled -- 19 MB of history that nothing has written to since 2026-08-13. A deletion candidate if space is ever needed.

| column | type | null |
|---|---|---|
| `id` | uuid | no |
| `session_time` | timestamp with time zone | yes |
| `session` | text | no |
| `ticker` | text | no |
| `epic` | text | yes |
| `macro_gate_pass` | boolean | yes |
| `options_bias` | text | yes |
| `iv_rank` | numeric | yes |
| `gex_bias` | text | yes |
| `vwap_position` | text | yes |
| `cot_bias` | text | yes |
| `bb_squeeze` | boolean | yes |
| `bb_breakout_dir` | text | yes |
| `director_signal` | boolean | yes |
| `activist_signal` | boolean | yes |
| `senate_signal` | boolean | yes |
| `senate_senator` | text | yes |
| `notable_investor` | text | yes |
| `social_mention` | text | yes |
| `confirmation_count` | integer | yes |
| `trade_triggered` | boolean | yes |
| `signal_summary` | text | yes |
| `call_put_ratio` | numeric | yes |
| `primary_count` | integer | yes |
| `direction` | text | yes |
| `pa_verdict` | text | yes |
| `adx_signal` | text | yes |
| `adx` | numeric | yes |
| `di_plus` | numeric | yes |
| `di_minus` | numeric | yes |
| `obv_signal` | text | yes |
| `obv_trend` | text | yes |
| `volume_signal` | text | yes |
| `volume_ratio` | numeric | yes |
| `hvf_type` | text | yes |
| `hvf_signal` | text | yes |
| `hvf_h3_level` | numeric | yes |
| `hvf_stop_level` | numeric | yes |
| `hvf_target` | numeric | yes |
| `hvf_risk_reward` | numeric | yes |
| `hvf_quality` | integer | yes |
| `adx_dir` | text | yes |
| `orb_signal` | text | yes |
| `orb_dir` | text | yes |
| `week52_signal` | text | yes |
| `week52_dir` | text | yes |
| `pa_score` | numeric | yes |
| `sector_etf` | text | yes |
| `sector_dir` | text | yes |
| `elite_senate_primary` | boolean | yes |
| `elite_senator_name` | text | yes |
| `potus_primary` | boolean | yes |
| `director_cluster_strong` | boolean | yes |
| `vwap_pct` | numeric | yes |
| `analyst_signal` | text | yes |
| `analyst_recommendation` | text | yes |

### `squeeze_history`  <span>(15 MB, ~35,854 rows)</span>

**Holds** — One row per detected squeeze setup, with entry/stop/target, quality, R:R, the pivot dates and -- once resolved -- outcome and return_pct. THE table behind Performance, Best Settings and the Insights page.

**Written by** — The daily scan (run_hvf_report) writes and refreshes it.

**Watch out** — Direction lives in hvf_type as BULLISH/BEARISH -- there is NO `direction` column. Rows can carry rvol NULL (815 of 30,408 measured 2026-08-17), mostly FX and indices which have no real volume. A ticker can appear more than once for one day across lookback windows, so anything counting trades must de-duplicate on ticker + triggered_date.

| column | type | null |
|---|---|---|
| `id` | bigint | no |
| `ticker` | text | no |
| `market` | text | yes |
| `timeframe` | text | yes |
| `hvf_type` | text | yes |
| `h1_level` | double precision | yes |
| `h2_level` | double precision | yes |
| `h3_level` | double precision | yes |
| `l1_level` | double precision | yes |
| `l2_level` | double precision | yes |
| `l3_level` | double precision | yes |
| `h1_date` | date | yes |
| `h2_date` | date | yes |
| `h3_date` | date | yes |
| `l1_date` | date | yes |
| `l2_date` | date | yes |
| `l3_date` | date | yes |
| `entry_level` | double precision | yes |
| `stop_level` | double precision | yes |
| `target_level` | double precision | yes |
| `quality` | double precision | yes |
| `risk_reward` | double precision | yes |
| `long_trend` | text | yes |
| `first_seen` | date | yes |
| `last_seen` | date | yes |
| `first_signal` | text | yes |
| `ready_date` | date | yes |
| `triggered_date` | date | yes |
| `outcome` | text | yes |
| `outcome_date` | date | yes |
| `return_pct` | double precision | yes |
| `rvol` | double precision | yes |
| `source` | text | yes |
| `recorded_at` | timestamp with time zone | no |
| `refreshed_at` | timestamp with time zone | no |

### `macro_snapshot`  <span>(3496 kB, ~11,941 rows)</span>

**Holds** — Macro indicators.

**Written by** — commodity_macro / FRED pulls.

| column | type | null |
|---|---|---|
| `id` | uuid | no |
| `snapshot_time` | timestamp with time zone | yes |
| `session` | text | no |
| `vix` | numeric | yes |
| `dxy` | numeric | yes |
| `us2y` | numeric | yes |
| `us10y` | numeric | yes |
| `yield_spread` | numeric | yes |
| `macro_gate_pass` | boolean | yes |
| `gate_reason` | text | yes |

### `hvf_scan_log`  <span>(2792 kB, ~14,283 rows)</span>

**Holds** — Per-scan log of what each run examined.

**Written by** — The daily scan.

| column | type | null |
|---|---|---|
| `id` | uuid | no |
| `scan_time` | text | no |
| `ticker` | text | no |
| `index_name` | text | yes |
| `hvf_type` | text | yes |
| `hvf_signal` | text | yes |
| `hvf_timeframe` | text | yes |
| `pattern_quality` | integer | yes |
| `risk_reward` | numeric | yes |
| `entry_level` | numeric | yes |
| `stop_level` | numeric | yes |
| `target` | numeric | yes |
| `recorded_at` | timestamp with time zone | yes |

### `web_json_store`  <span>(2304 kB, ~12 rows)</span>

**Holds** — A key/value JSON store: precomputed winners payloads, Best Settings cards, the sector cache, the metric and coverage audits.

**Written by** — Whichever job owns each key.

**Watch out** — Twelve rows, but 2.3 MB -- the values are large documents.

| column | type | null |
|---|---|---|
| `store_key` | text | no |
| `payload` | jsonb | no |
| `updated_at` | timestamp with time zone | no |
| `revision` | bigint | no |

### `hvf_suppressed_log`  <span>(2280 kB, ~14,884 rows)</span>

**Holds** — Setups the method found and then suppressed, with the reason.

**Written by** — The daily scan.

| column | type | null |
|---|---|---|
| `id` | bigint | no |
| `suppressed_at` | timestamp with time zone | yes |
| `ticker` | text | no |
| `hvf_timeframe` | text | yes |
| `hvf_type` | text | yes |
| `risk_reward` | numeric | yes |
| `violations` | text | yes |

### `hvf_triggers`  <span>(2216 kB, ~1,228 rows)</span>

**Holds** — Live detections since 2026-06-30.

**Written by** — The daily scan.

**Watch out** — Has NO trigger-date column: only recorded_at and the pivot dates.

| column | type | null |
|---|---|---|
| `id` | bigint | no |
| `recorded_at` | timestamp with time zone | no |
| `ticker` | text | no |
| `market` | text | yes |
| `hvf_type` | text | yes |
| `timeframe` | text | yes |
| `quality` | double precision | yes |
| `risk_reward` | double precision | yes |
| `entry_level` | double precision | yes |
| `stop_level` | double precision | yes |
| `target_level` | double precision | yes |
| `current_price` | double precision | yes |
| `h1_level` | double precision | yes |
| `h2_level` | double precision | yes |
| `h3_level` | double precision | yes |
| `l1_level` | double precision | yes |
| `l2_level` | double precision | yes |
| `l3_level` | double precision | yes |
| `h1_date` | date | yes |
| `h2_date` | date | yes |
| `h3_date` | date | yes |
| `l1_date` | date | yes |
| `l2_date` | date | yes |
| `l3_date` | date | yes |
| `long_trend` | text | yes |
| `source` | text | yes |
| `raw` | jsonb | yes |

### `notable_investors`  <span>(1728 kB, ~3,417 rows)</span>

**Holds** — Superinvestor holdings.

**Written by** — analyst_signals.

| column | type | null |
|---|---|---|
| `id` | uuid | no |
| `investor_name` | text | no |
| `ticker` | text | no |
| `action` | text | no |
| `shares` | numeric | yes |
| `market_value` | numeric | yes |
| `source` | text | no |
| `disclosed_at` | date | no |
| `quarter` | text | yes |
| `notes` | text | yes |
| `recorded_at` | timestamp with time zone | yes |
| `direction` | text | yes |
| `post_url` | text | yes |

### `missed_trade_log`  <span>(1152 kB, ~2,089 rows)</span>

**Holds** — Setups that passed detection but were not taken, and why.

**Written by** — The bridge and the order gate.

**Watch out** — The place to look when asked why something was not ordered.

| column | type | null |
|---|---|---|
| `id` | bigint | no |
| `trade_date` | date | no |
| `ticker` | text | no |
| `direction` | text | no |
| `reason_class` | text | no |
| `last_reason` | text | yes |
| `signal_summary` | text | yes |
| `occurrences` | integer | no |
| `first_seen` | timestamp with time zone | yes |
| `last_seen` | timestamp with time zone | yes |

### `data_quality_log`  <span>(736 kB, ~2,333 rows)</span>

**Holds** — Yahoo-vs-IG price comparisons per ticker per audit.

**Written by** — run_data_quality_audit, nightly 22:15 UTC.

| column | type | null |
|---|---|---|
| `id` | bigint | no |
| `audit_date` | date | no |
| `ticker` | text | no |
| `days_compared` | integer | yes |
| `close_max_dev_pct` | numeric | yes |
| `phantom_high_wicks` | integer | yes |
| `phantom_low_wicks` | integer | yes |
| `verdict` | text | yes |
| `detail` | text | yes |
| `created_at` | timestamp with time zone | yes |

### `instrument_metrics_daily`  <span>(576 kB, ~3,208 rows)</span>

**Holds** — The stored break-bar measures (RVOL, above-VWAP, ATR expanding, VolumeScore) per ticker per day.

**Written by** — instrument_metrics.record_daily, inside the daily scan.

**Watch out** — as_of IS NOT THE BAR IT DESCRIBES. The scan runs ~03:30 UTC, before any market opens, so the row written under as_of = today is computed from YESTERDAY'S bar: measured 2026-09-06, 1,761 of the as_of 2026-09-05 rows carry bar_date 2026-09-04. Always compare bar_date against the day you are judging. It also stored NOTHING between 2026-08-29 and 2026-09-04 because its INSERT carried a placeholder with no argument.

| column | type | null |
|---|---|---|
| `ticker` | text | no |
| `as_of` | date | no |
| `bar_date` | date | yes |
| `rvol` | double precision | yes |
| `rvol_date` | date | yes |
| `above_vwap` | boolean | yes |
| `above_vwap_setup` | boolean | yes |
| `atr_expanding` | boolean | yes |
| `volume_score` | integer | yes |
| `volume_score_max` | integer | yes |
| `wk52_low` | double precision | yes |
| `wk52_high` | double precision | yes |
| `direction` | text | yes |
| `status` | text | yes |
| `source` | text | yes |
| `recorded_at` | timestamp with time zone | yes |
| `mcap` | double precision | yes |

### `instrument_mcap_history`  <span>(392 kB, ~3,277 rows)</span>

**Holds** — Market cap over time, one row per ticker per capture.

**Written by** — mcap_backfill, weekly.

| column | type | null |
|---|---|---|
| `ticker` | text | no |
| `as_of` | date | no |
| `mcap` | double precision | yes |
| `currency` | text | yes |
| `recorded_at` | timestamp with time zone | yes |

### `working_orders`  <span>(336 kB, ~481 rows)</span>

**Holds** — The engine-managed pre-order lifecycle: WATCHING -> PENDING -> FILLED / CANCELLED / EXPIRED.

**Written by** — ig_shim, on every bridge pass.

**Watch out** — Carries NO RVOL, VolumeScore, Quality or R:R columns. Those are resolved at read time from the squeeze_history trigger that caused the order (server._attach_setup_metrics).

| column | type | null |
|---|---|---|
| `id` | bigint | no |
| `deal_ref` | text | yes |
| `deal_id` | text | yes |
| `user_id` | text | no |
| `ticker` | text | no |
| `epic` | text | no |
| `direction` | text | no |
| `size` | numeric | no |
| `entry_level` | numeric | no |
| `stop_level` | numeric | yes |
| `limit_level` | numeric | yes |
| `otype` | text | yes |
| `hvf_type` | text | yes |
| `status` | text | no |
| `paper_trade` | boolean | yes |
| `session` | text | yes |
| `signal_summary` | text | yes |
| `good_till` | timestamp with time zone | yes |
| `placed_at` | timestamp with time zone | yes |
| `updated_at` | timestamp with time zone | yes |
| `filled_at` | timestamp with time zone | yes |
| `fill_deal_id` | text | yes |
| `notes` | text | yes |
| `proximity_pct` | numeric | yes |
| `lwr_owner_login` | text | yes |
| `lwr_account_fingerprint` | text | yes |

### `epic_lookup`  <span>(272 kB, ~1,623 rows)</span>

**Holds** — Ticker -> IG epic, with the IG instrument description.

**Written by** — ig_shim on demand; verified nightly by the data-quality audit.

**Watch out** — The identity check here is what catches an epic mapped to the WRONG COMPANY.

| column | type | null |
|---|---|---|
| `ticker` | text | no |
| `epic` | text | no |
| `description` | text | yes |
| `currency` | text | yes |
| `market_type` | text | yes |
| `last_seen` | timestamp with time zone | yes |

### `web_activity_log`  <span>(264 kB, ~1,086 rows)</span>

**Holds** — Per-user web activity.

**Written by** — hvf_web/server.py.

| column | type | null |
|---|---|---|
| `id` | bigint | no |
| `ts` | timestamp with time zone | no |
| `user_id` | text | no |
| `event` | text | yes |

### `instrument_mcap`  <span>(224 kB, ~1,638 rows)</span>

**Holds** — Current market cap per ticker, one row each, with its source currency.

**Written by** — mcap_backfill, weekly (Sunday 05:00 UTC).

**Watch out** — The column is `mcap`, not market_cap. Values are in the instrument's OWN currency -- fx_rates converts to GBP, and an unconvertible currency yields None rather than a wrong number.

| column | type | null |
|---|---|---|
| `ticker` | text | no |
| `mcap` | double precision | yes |
| `currency` | text | yes |
| `updated_at` | timestamp with time zone | yes |

### `web_batch_activity`  <span>(168 kB, ~564 rows)</span>

**Holds** — Scheduled-job run records behind the Batch Activity tab.

**Written by** — The jobs themselves.

| column | type | null |
|---|---|---|
| `id` | bigint | no |
| `ts` | timestamp with time zone | no |
| `source` | text | yes |
| `event` | text | yes |
| `by_user` | text | yes |

### `positions`  <span>(144 kB, ~9 rows)</span>

**Holds** — Engine-side open position records.

**Written by** — ig_shim.

| column | type | null |
|---|---|---|
| `id` | uuid | no |
| `user_id` | uuid | yes |
| `epic` | text | no |
| `ticker` | text | no |
| `direction` | text | no |
| `size` | numeric | no |
| `open_price` | numeric | no |
| `stop_loss` | numeric | no |
| `take_profit` | numeric | yes |
| `atr_multiplier` | numeric | yes |
| `deal_id` | text | yes |
| `paper_trade` | boolean | no |
| `opened_at` | timestamp with time zone | yes |
| `session` | text | yes |
| `signal_summary` | text | yes |
| `lwr_owner_login` | text | yes |
| `lwr_account_fingerprint` | text | yes |

### `trade_log`  <span>(144 kB, ~42 rows)</span>

**Holds** — Executed trades.

**Written by** — ig_shim.

| column | type | null |
|---|---|---|
| `id` | uuid | no |
| `user_id` | uuid | yes |
| `epic` | text | no |
| `ticker` | text | no |
| `direction` | text | no |
| `size` | numeric | no |
| `open_price` | numeric | no |
| `close_price` | numeric | no |
| `stop_loss` | numeric | no |
| `pnl` | numeric | no |
| `pnl_pct` | numeric | yes |
| `paper_trade` | boolean | no |
| `opened_at` | timestamp with time zone | no |
| `closed_at` | timestamp with time zone | yes |
| `session` | text | yes |
| `close_reason` | text | yes |
| `signal_summary` | text | yes |

### `cot_snapshot`  <span>(120 kB, ~79 rows)</span>

**Holds** — Commitment of Traders positioning.

**Written by** — cot_analysis.

| column | type | null |
|---|---|---|
| `id` | uuid | no |
| `report_date` | date | no |
| `instrument` | text | no |
| `cftc_code` | text | yes |
| `comm_net` | numeric | yes |
| `comm_net_change` | numeric | yes |
| `noncomm_net` | numeric | yes |
| `noncomm_net_change` | numeric | yes |
| `open_interest` | numeric | yes |
| `pct_comm_long` | numeric | yes |
| `pct_comm_short` | numeric | yes |
| `bias` | text | yes |
| `updated_at` | timestamp with time zone | yes |
| `managed_money_long` | numeric | yes |
| `managed_money_short` | numeric | yes |
| `managed_money_net` | numeric | yes |
| `managed_money_change` | numeric | yes |
| `oi_change` | numeric | yes |
| `comm_net_pct_rank` | numeric | yes |
| `mm_net_pct_rank` | numeric | yes |
| `comm_extreme` | text | yes |
| `mm_extreme` | text | yes |
| `price_divergence` | text | yes |
| `oi_signal` | text | yes |
| `cot_score` | numeric | yes |

### `x_draft_state`  <span>(112 kB, ~507 rows)</span>

**Holds** — Drafted X posts awaiting review.

**Written by** — intraday_signals.

| column | type | null |
|---|---|---|
| `ticker` | text | no |
| `fingerprint` | text | yes |
| `posted_at` | timestamp with time zone | yes |

### `x_publications`  <span>(104 kB, ~145 rows)</span>

**Holds** — What has been posted to X.

**Written by** — publish_one_to_x.

| column | type | null |
|---|---|---|
| `id` | bigint | no |
| `ticker` | text | no |
| `tweet_id` | text | yes |
| `published_at` | timestamp with time zone | yes |
| `thread_ids` | text | yes |

### `web_best_settings_history`  <span>(96 kB, ~0 rows)</span>

**Holds** — Snapshots of the Best Settings recommendation over time.

**Written by** — The web app when a recommendation is recorded.

| column | type | null |
|---|---|---|
| `id` | bigint | no |
| `ts` | timestamp with time zone | no |
| `user_id` | text | no |
| `snapshot_day` | date | no |
| `dataset_generated` | text | yes |
| `data_through` | text | yes |
| `model_json` | jsonb | no |
| `options_json` | jsonb | no |
| `fingerprint` | text | no |

### `scanner_refresh_progress`  <span>(88 kB, ~50 rows)</span>

**Holds** — Progress of an in-flight rescan, for the UI.

**Written by** — The scanner.

| column | type | null |
|---|---|---|
| `refresh_id` | text | no |
| `status` | text | no |
| `stage` | text | no |
| `done` | integer | no |
| `total` | integer | no |
| `markets` | text | yes |
| `worker` | text | yes |
| `queued_for` | timestamp with time zone | yes |
| `requested_at` | timestamp with time zone | no |
| `started_at` | timestamp with time zone | yes |
| `updated_at` | timestamp with time zone | no |
| `completed_at` | timestamp with time zone | yes |
| `generated_utc` | text | yes |
| `error` | text | yes |

### `login_attempts`  <span>(64 kB, ~2 rows)</span>

**Holds** — Throttling state for failed logins.

**Written by** — login_throttle.

| column | type | null |
|---|---|---|
| `ip` | text | no |
| `name` | text | no |
| `attempts` | integer | no |
| `first_attempt` | timestamp with time zone | no |
| `last_attempt` | timestamp with time zone | no |
| `locked_until` | timestamp with time zone | yes |

### `scanner_snapshot_versions`  <span>(64 kB, ~17 rows)</span>

**Holds** — Immutable published snapshot versions.

**Written by** — publish_scanner_snapshot.

**Watch out** — Supabase Storage has been returning 402 Payment Required since 2026-08-16, so publication has been falling back to IONOS.

| column | type | null |
|---|---|---|
| `id` | bigint | no |
| `generated_utc` | timestamp with time zone | no |
| `object_path` | text | no |
| `sha256` | text | no |
| `record_count` | integer | no |
| `byte_count` | bigint | no |
| `schema_version` | integer | no |
| `source` | text | no |
| `published_at` | timestamp with time zone | no |

### `senator_scores`  <span>(64 kB, ~5 rows)</span>

**Holds** — Congressional trading signal scores.

**Written by** — analyst_signals.

| column | type | null |
|---|---|---|
| `id` | uuid | no |
| `senator_name` | text | no |
| `party` | text | yes |
| `state` | text | yes |
| `trade_count` | integer | no |
| `win_rate` | numeric | no |
| `avg_excess_return` | numeric | no |
| `score` | numeric | no |
| `qualified` | boolean | no |
| `last_updated` | timestamp with time zone | yes |

### `app_config`  <span>(64 kB, ~21 rows)</span>

**Holds** — Engine settings editable from Configuration (Admin).

**Written by** — hvf_web/server.py.

| column | type | null |
|---|---|---|
| `key` | text | no |
| `value` | text | no |
| `updated_by` | text | yes |
| `updated_at` | timestamp with time zone | no |

### `social_mentions`  <span>(56 kB, ~0 rows)</span>

**Holds** — Tracked social mentions.

**Written by** — social_monitor.

| column | type | null |
|---|---|---|
| `id` | uuid | no |
| `author` | text | no |
| `platform` | text | no |
| `post_text` | text | no |
| `tickers_found` | ARRAY | yes |
| `sentiment` | text | yes |
| `post_time` | timestamp with time zone | no |
| `session` | text | yes |
| `acted_on` | boolean | yes |
| `recorded_at` | timestamp with time zone | yes |

### `x_publications_archive`  <span>(48 kB, ~25 rows)</span>

**Holds** — Archived X publications.

**Written by** — Archival job.

| column | type | null |
|---|---|---|
| `id` | bigint | no |
| `ticker` | text | no |
| `tweet_id` | text | yes |
| `published_at` | timestamp with time zone | yes |

### `web_ig_account_audit`  <span>(48 kB, ~1 rows)</span>

**Holds** — Append-only record of every IG close attempt.

**Written by** — hvf_web/server.py._append_ig_close_audit.

**Watch out** — Deliberately host-side and independent of Supabase: an audit trail for a live broker action must survive the outage that may have caused the problem.

| column | type | null |
|---|---|---|
| `id` | bigint | no |
| `ts` | timestamp with time zone | no |
| `user_id` | text | no |
| `account_name_enc` | text | yes |
| `account_number_enc` | text | yes |
| `account_number_last3` | text | yes |
| `source` | text | yes |
| `by_user` | text | yes |

### `user_profiles`  <span>(48 kB, ~3 rows)</span>

**Holds** — Engine-side trading profiles.

**Written by** — run_session.

**Watch out** — profiles.name ('Owner') is NOT a web login. trading_limits.login_for_profile maps one to the other by USER ID -- never by guessing from the name.

| column | type | null |
|---|---|---|
| `id` | uuid | no |
| `name` | text | no |
| `ig_account_id` | text | no |
| `risk_per_trade` | numeric | no |
| `max_open_pos` | integer | no |
| `daily_loss_limit` | numeric | no |
| `paper_trade` | boolean | no |
| `active` | boolean | no |
| `created_at` | timestamp with time zone | yes |

### `daily_pnl`  <span>(48 kB, ~15 rows)</span>

**Holds** — Daily profit and loss.

**Written by** — The session summary job.

| column | type | null |
|---|---|---|
| `id` | uuid | no |
| `user_id` | uuid | yes |
| `trade_date` | date | no |
| `total_pnl` | numeric | no |
| `trade_count` | integer | no |
| `win_count` | integer | no |
| `loss_count` | integer | no |
| `daily_loss_hit` | boolean | yes |

### `ig_validation_log`  <span>(48 kB, ~1 rows)</span>

**Holds** — IG price-validation results.

**Written by** — run_data_quality_audit.

| column | type | null |
|---|---|---|
| `id` | bigint | no |
| `trade_date` | date | no |
| `ticker` | text | no |
| `ig_validated` | boolean | yes |
| `mismatch` | text | yes |
| `entry_level` | numeric | yes |
| `stop_level` | numeric | yes |
| `target` | numeric | yes |
| `risk_reward` | numeric | yes |
| `created_at` | timestamp with time zone | yes |

### `hvf_watch_state`  <span>(48 kB, ~1 rows)</span>

**Holds** — Per-market watch cursor.

**Written by** — The HVF watch jobs.

| column | type | null |
|---|---|---|
| `id` | bigint | no |
| `key` | text | no |
| `fingerprint` | text | no |
| `posted_at` | timestamp with time zone | yes |

### `scanner_snapshot_current`  <span>(40 kB, ~1 rows)</span>

**Holds** — Pointer to the published snapshot now in play.

**Written by** — publish_scanner_snapshot.

| column | type | null |
|---|---|---|
| `singleton` | boolean | no |
| `version_id` | bigint | no |
| `updated_at` | timestamp with time zone | no |

### `fx_rates`  <span>(32 kB, ~9 rows)</span>

**Holds** — Currency -> GBP conversion rates.

**Written by** — mcap_backfill refreshes them on its weekly run.

**Watch out** — A missing rate must yield None, never 1.0: a silent 1.0 would report a foreign market cap as though it were sterling.

| column | type | null |
|---|---|---|
| `currency` | text | no |
| `rate_to_gbp` | double precision | no |
| `as_of` | timestamp with time zone | yes |

### `price_audit_log`  <span>(32 kB, ~47 rows)</span>

**Holds** — Price-history audit results.

**Written by** — price_audit.

| column | type | null |
|---|---|---|
| `id` | bigint | no |
| `run_at` | timestamp with time zone | no |
| `mode` | text | no |
| `source` | text | no |
| `tickers_checked` | integer | no |
| `bars_written` | integer | no |
| `discrepancies` | integer | no |
| `max_drift_pct` | double precision | yes |
| `duration_s` | double precision | yes |
| `notes` | text | yes |

### `x_draft_state_archive`  <span>(32 kB, ~32 rows)</span>

**Holds** — Archived X drafts.

**Written by** — Archival job.

| column | type | null |
|---|---|---|
| `ticker` | text | no |
| `fingerprint` | text | yes |
| `posted_at` | timestamp with time zone | yes |

### `app_secrets`  <span>(32 kB, ~12 rows)</span>

**Holds** — Encrypted secret store, read into the environment at import by db_pool.

**Written by** — migrate_secrets_to_supabase / set_secret.

**Watch out** — Primary source of credentials. .env is kept as a complete cold backup.

| column | type | null |
|---|---|---|
| `key` | text | no |
| `ciphertext` | text | no |
| `updated_by` | text | yes |
| `updated_at` | timestamp with time zone | yes |

### `geopolitical_risk`  <span>(24 kB, ~0 rows)</span>

**Holds** — Geopolitical risk scores.

**Written by** — commodity_macro.

| column | type | null |
|---|---|---|
| `id` | uuid | no |
| `instrument` | text | no |
| `risk_level` | text | no |
| `description` | text | no |
| `active` | boolean | no |
| `source` | text | yes |
| `created_at` | timestamp with time zone | yes |
| `updated_at` | timestamp with time zone | yes |

