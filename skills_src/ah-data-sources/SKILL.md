---
name: ah-data-sources
description: >
  Where a VALUE actually comes from in this project — which of the four stores holds it, which job
  writes it, and which lookup paths are fatal on the IONOS web host. Use before answering "where does
  this come from", before adding a field, before making a web endpoint read something, and whenever an
  endpoint returns HTTP 500 after ~120 seconds. Complements ah-data-dictionary, which documents the
  Supabase TABLES; this one documents the RESOLUTION ORDER and the three stores that are not Supabase.
---

# AH Data Sources — where a value actually comes from

`ah-data-dictionary` answers "what is in table X". This skill answers the question that wasted an
afternoon on 2026-09-20: **"where does this particular value come from, and can the web host reach it?"**

Everything below was measured on 2026-09-20 unless marked otherwise. Re-measure before trusting a count.

## 1. There are FOUR stores, not one

| store | what lives there | reachable from the IONOS web host? |
|---|---|---|
| **Supabase tables** (43) | the durable record: `price_history`, `squeeze_history`, `working_orders`, … | yes, via the session pooler |
| **`web_json_store`** (15 keys) | precomputed payloads and caches, one JSON blob per key | yes, it is a Supabase table |
| **`hvf_web/snapshot.json`** | the published scan: one record per instrument, ~1,773 records | yes, it is a LOCAL FILE on the host — free to read |
| **in-process dicts** | `_NAME_CACHE`, `_PNG_CACHE`, `_SLBARS`, `_PB_CACHE`, `_WK52_CACHE` | yes, but they die with the worker |

Reading the snapshot costs nothing. Reading Supabase costs egress against a 5 GB monthly allowance shared
across the whole organisation — see `egress_report.py`. **Prefer the snapshot when the value is on it.**

### `web_json_store` keys (measured 2026-09-20)

    best_settings_cards              broker_by_ticker          fundamentals_by_ticker
    best_settings_full_grid_audit    current_instrument_metric_audit
    field_coverage_audit             fundamentals_overrides    lwr_last_pass
    name_cache                       performance_rows_12m      sector_cache
    version_history                  web_users                 winners_rows_1y   winners_rows_3y

Written by scheduled jobs (`run_winners_precompute.py`, `run_best_settings_cards.py`, the Fundamentals
Precompute job, `build_snapshot.py`). Read by the web tier through `web_store.load_json_store(key)`.
A payload is served only if its `dataset` matches the live snapshot's `generated_utc` — a mismatch makes
the page slow, never wrong.

## 2. The snapshot record — check here FIRST

Fields on each record (no leading underscore) as at 2026-09-20:

    above_vwap, atr_expanding, broker, current_price, direction, entry, h1_date, h3_date,
    has_signal, insider_pct, l3_date, location, market, months_to_go, name, pe, quality,
    rr, rules, sector, status, stop, target, ticker, timeframe, tweet

**Measured coverage on the live snapshot (1,773 records):** `name` 1,773; `tweet` **0**; `exchange` does
not exist as a field; `_card` present on 261 of the 1,421-record local copy and stripped from the public
`/api/records` payload.

Two traps, both hit on 2026-09-20:

- **`_card` and the record are different objects.** `_card` holds only levels — `h1..h3`, `l1..l3`,
  `stop_level`, `target`, `risk_reward`, `current_price`, `hvf_signal`, `hvf_timeframe`, `hvf_type`.
  The company name, sector, tweet and quality live on the RECORD. Checking `_card` and concluding "the
  value is not stored" is wrong reasoning even when the conclusion happens to hold.
- **A field existing does not mean it is populated.** `tweet` is declared on every record and is empty
  on all 1,773. Count it, do not assume it.

## 3. Resolution order for values that bite

### Company name — TWO independent paths, and they do not share a cache

1. **The snapshot's `name`** — populated for 1,773 of 1,773. `build_snapshot.py` resolves it once in the
   scanner (GitHub Actions) via `_load_name_cache()`/`_save_name_cache()`, which read and write BOTH the
   durable `web_json_store` key `name_cache` and the local `hvf_web/name_cache.json`.
2. **`instrument_name.company_name()`** — curated names, then the FX-pair rule, then a **per-process**
   `_NAME_CACHE` dict (instrument_name.py:29), then **yfinance**, then `epic_lookup`.

**Path 2 never reads the durable `name_cache` that path 1 maintains.** So a fresh process always reaches
for yfinance, which is fatal on IONOS (§4). On the web tier, take the name from the snapshot record.

### Lead tweet text
Not stored anywhere. The `tweet` field exists but is empty; `_generate_x_drafts` builds it at publish
time inside `intraday_signals`, which is numpy-bearing. To show it on the web it must first be WRITTEN
where numpy works.

### Sector, fundamentals, broker
`sector_cache`, `fundamentals_by_ticker`, `broker_by_ticker` in `web_json_store`, written by the nightly
Fundamentals Precompute job (cron-job.org id 8469938, 21:15 UTC). `/api/fundamentals` and `/api/broker`
answer in under 2s from these — they were 500-at-120s until they stopped computing on the request path.

### Price bars
`price_history`, read through `db_pool` directly — **not** through `price_store`, which imports pandas at
module level. `server._price_bars` exists for exactly this reason.

## 4. The IONOS constraint — numpy is FATAL, and it is not catchable

`import numpy` on the web host dies with SIGSYS: the seccomp filter kills any process calling `mbind(2)`,
which the bundled OpenBLAS does. **It kills the PROCESS — `try/except` cannot catch it.** The symptom is
always the same: **HTTP 500 after ~120 seconds** (the gateway timeout), never a fast error.

Modules that import a numpy-bearing package AT MODULE LEVEL, so merely importing them is fatal:

| module | line | pulls in |
|---|---|---|
| `intraday_signals.py` | 336-338 | numpy, pandas, yfinance |
| `price_action.py` | 261-263 | numpy, pandas, yfinance |
| `price_store.py` | 30 | pandas |

Safe at import but **fatal on CALL**, because they lazily import one of the above:

- `quality_report.publish_long_report_for` → `from intraday_signals import …` (lines 745, 893, 958, 981)
  for four pure-text helpers.
- `instrument_name.company_name` → `_yf_info()` → `import yfinance` (instrument_name.py:40) whenever the
  per-process cache misses.

**Import-time testing is not enough.** Prove it for the CALL:

```python
import sys, quality_report
quality_report.publish_long_report_for(card, post=False)
print([m for m in ('numpy','pandas','matplotlib','yfinance') if m in sys.modules])
```

To find WHICH line pulls it in, wrap `builtins.__import__` and print the stack the first time a watched
module is imported. That is how `intraday_signals.py:336` was identified rather than guessed.

## 5. The rule this skill exists to enforce

**Before making a web endpoint compute a value, check whether a job already stored it.** The pattern that
has worked every time is: resolve it where numpy works (GitHub Actions), store it (`web_json_store` or a
snapshot field), and have the web tier READ it. That fixed the price chart, fundamentals and broker.

**And before adding a new store, search for an existing one.** `name_cache` already exists and is
maintained, yet `company_name` re-derives names from yfinance — a durable cache nothing reads. That is
this repository's signature defect wearing a different hat.
