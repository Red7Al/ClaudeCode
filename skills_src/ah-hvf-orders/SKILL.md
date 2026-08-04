---
name: ah-hvf-orders
description: >
  The daily Squeeze ORDERS publication — the actionable, tradeable Squeeze setups posted to
  #arw-claude-orders each morning (Mon–Sat) as candidate orders. Use this skill whenever the
  user asks about the orders publication, "today's orders", the #orders / slack-orders feed, the
  daily candidate-order list, or why a setup is/ isn't in it. Source of truth: run_hvf_orders.py.
  Distinct from ah-working-orders (the engine-managed pre-orders ALREADY placed on IG) and from
  ah-hvf-report (the full #signals analytical report). This is the curated, tradeable-only list.
---

# AH Squeeze Orders — the daily candidate-orders publication

A once-daily Slack publication of the **actionable** Squeeze setups — the ones at/near entry that
clear the tradeability bar — framed as **candidate orders** ("not yet placed"). It is the
"result of running the Squeeze analysis to slack-orders each day" (user 2026-06-19).

**Source of truth:** `run_hvf_orders.py`. When this doc and the code disagree, the code wins.

## What it does

```
scan_universe()  →  categorise()  →  TRADEABLE only  →  group per market  →  post to SLACK_ORDERS
   (run_hvf_report)   (run_hvf_report)   (drops DEVELOPING)   (R:R-first)        (#arw-claude-orders)
```

- Reuses the exact production scan + categorisation from `run_hvf_report` (no separate logic).
- Posts **only TRADEABLE** setups (READY/TRIGGERED with R:R ≥ `MIN_RISK_REWARD`, currently 3:1).
  DEVELOPING / watch setups are intentionally excluded — they live in the #signals daily report.
- Reuses `run_hvf_report._tradeable_line`, so every order carries the **live price, each level's
  % from price, R:R and the expected time-to-target** — identical formatting to the daily report.
- Ordered **R:R-first** (`price_action.hvf_weight`), grouped per market, numbered from 1, with a
  "top N of M candidates" sub-header per market.

## How it differs from the neighbours

| Publication | What it posts | Channel | Source |
|---|---|---|---|
| **Squeeze Orders** (this) | fresh TRADEABLE Squeeze setups — *candidate* orders, not yet placed | #arw-claude-orders | `run_hvf_orders.py` |
| Working Orders (`ah-working-orders`) | engine-managed pre-orders ALREADY on IG (WATCHING→PENDING→FILLED) | #arw-claude-orders | `working_orders_report.py` |
| Squeeze Daily Report (`ah-hvf-report`) | TRADEABLE **+ DEVELOPING**, full analytical view | #arw-claude-signals | `run_hvf_report.py` |

## Schedule

- `trading-hvf-orders.yml` workflow, cron job **"Squeeze Orders" `0 6 * * 1-6`** (06:00 UTC Mon–Sat),
  registered in `setup_cronjobs.py`. Runs before 07:00 UTC (8am BST) with the other morning
  publications. Manually triggerable via `gh workflow run trading-hvf-orders.yml`.
- Env required: `SUPABASE_USER`, `SUPABASE_DB_PASSWORD` (scan), `SLACK_ORDERS` (#orders webhook),
  `SLACK_ALERTS` (failure surfacing).

## Method

Same five-rule The Squeeze method as the rest of the system (see `ah-hvf-analysis` /
`RW-hvf-analysis`): clear prior trend, three converging swings (H1>H2>H3 / L1<L2<L3), ≥30%
compression, full-AMP1 target, R:R ≥ 3:1. "Tradeable" here means READY or TRIGGERED with R:R at
or above the floor — exactly the orders worth acting on today.

## Run it manually

```bash
python run_hvf_orders.py        # scans + posts to #arw-claude-orders (needs SLACK_ORDERS)
```

Local runs without `SLACK_ORDERS` print the blocks to stdout instead (nothing posted).
