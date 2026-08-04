---
name: ah-working-orders
description: >
  The engine-managed PRE-ORDER system — "working orders": pending Squeeze entry orders the engine
  holds and manages (WATCHING -> PENDING -> FILLED/CANCELLED/EXPIRED) BEFORE they become live IG
  positions. Use this skill whenever the user asks about pre-orders, working orders, pending /
  watching orders, "what's queued", why an order didn't place or got amended/cancelled, the
  proximity band, the working_orders table, or the daily pre-order report to #arw-claude-orders.
  Source of truth: ig_shim.py (lifecycle), run_schema.py (table), working_orders_report.py (report).
---

# AH Working Orders — the engine-managed pre-order system

A "working order" is a **pending Squeeze entry order**: a set-and-forget order at the exact entry
level (H3 long / L3 short) with the stop and target attached, placed as ONE IG working order so
the trade triggers itself. These are the orders the engine MANAGES BEFORE they become live IG
positions — they are explicitly **NOT positions** (the monitor must never see them in the
positions table or it would falsely log them as closed trades).

## Lifecycle (single source of truth: `ig_shim.py`)

```
WATCHING ─(price enters proximity band)→ PENDING ─(price hits entry)→ FILLED → positions row
   │                                        │
   └── engine-side only, NO capital         ├──(re-signal at new levels)→ AMENDED (not duplicated)
       committed yet                        ├──(direction flips / invalidated)→ CANCELLED
                                            └──(good_till passes)→ EXPIRED
```

- **WATCHING** — the pattern is ready but price is not yet within the proximity band; the engine
  holds it locally, no order on IG, no capital/slot committed.
- **PENDING** — price entered the band; a real working order is placed on IG, awaiting trigger.
- **FILLED** — the entry triggered; `reconcile_working_orders` writes the `positions` row so the
  monitor picks it up, and marks the working order FILLED.
- **CANCELLED / EXPIRED** — invalidated (direction change, etc.) or `good_till` elapsed.

Key behaviours (all in `ig_shim.py`):
- `place_working_order` — places the pending order; a working order **consumes a trade slot** the
  moment it is placed. Includes a dedicated **GBX (pence) conversion** path for US stocks quoted
  on IG UK (reads `baseExchangeRate`).
- `update_working_order` — a **re-signal at new levels AMENDS** the existing order, never
  duplicates it (`_get_pending_working_order` matches PENDING **and** WATCHING rows to dedupe).
- `delete_working_order` — cancels (e.g. on a direction change).
- `get_working_orders` — `GET /workingorders` (v2) — what IG currently holds.
- `reconcile_working_orders` — detects fills, inserts the `positions` row, advances status.
- `_log_working_order_to_db` / `_set_working_order_status` — persist lifecycle to Supabase.

## The data — `working_orders` table (`run_schema.py`)

Per-row: `deal_ref, deal_id, user_id, ticker, epic, direction, size, entry_level, stop_level,
limit_level, otype, hvf_type, status, paper_trade, session, signal_summary, good_till, placed_at,
updated_at, filled_at, fill_deal_id, notes`. Indexed on `status` and on `ticker, placed_at`.
Daily caps count **today's PENDING** rows.

## Daily pre-order report (`working_orders_report.py` → #arw-claude-orders)

Built 2026-06-16 (user request). Posts to the **`SLACK_ORDERS`** webhook (the #arw-claude-orders
channel). Two sections:
1. **Live** under management — **PENDING** (placed on IG, awaiting trigger) and **WATCHING**
   (engine-side, no capital committed yet), each with direction / entry / stop / target / size /
   session / placed time / good-till.
2. **Settled today** — orders that moved to FILLED / CANCELLED / EXPIRED today (with the note).

Run / schedule:
```
python working_orders_report.py            # post to #arw-claude-orders
python working_orders_report.py --dry      # build + print, post NOTHING (local preview)
```
- Workflow: `.github/workflows/trading-working-orders-report.yml` (workflow_dispatch; env
  `SUPABASE_USER`/`SUPABASE_DB_PASSWORD`/`SLACK_ORDERS`).
- Scheduled via cron-job.org (`setup_cronjobs.py` → "Pre-Order Report", **45 21 Mon-Fri**), just
  after the 21:30 Daily Report. GitHub-native `schedule:` is banned (memory: feedback_scheduler).
- Secrets live in GitHub, not local `.env` — the report posts only from Actions
  (memory: secrets_and_x_delivery).

## Non-negotiables when answering

- Working orders are **pre-IG-fill, not positions** — never conflate them with open positions or
  closed trades; the `positions` row appears only on FILL via `reconcile_working_orders`.
- A re-signal **amends**, never duplicates — if you see two orders for one ticker, that's a bug.
- Quote live state from the `working_orders` table or `get_working_orders`, never from memory.
- Levels are GBX (pence) for UK names and for US names quoted on IG UK (post-conversion).

## Pairs with
- **ah-hvf-analysis** — produces the entry/stop/target levels these orders are placed at.
- **ah-signal-stack** — the decision layer that authorises a trade before an order is placed.
