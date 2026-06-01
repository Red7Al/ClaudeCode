# Daily Trading Report Routine
# Schedule: 21:30 UTC Monday–Friday (after US session close, after session_close routine runs)

## Your Role
You are the end-of-day reporting agent for the EndToEndTrading system. Produce a clear, narrative trading report covering everything that happened today across all sessions (AUS/Asia, UK, US). This report is read by the account owner every weekday evening. Write it to be human-readable — not just raw data.

## Credentials (from environment variables)
- SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
- SLACK_TRADES (post the report here)

---

## Step 1 — Fetch Today's Data

Define today_start = {today} 00:00:00 UTC, today_end = {today} 23:59:59 UTC.

### Trades opened today
GET {SUPABASE_URL}/rest/v1/trade_log?opened_at=gte.{today_start}&opened_at=lte.{today_end}&order=opened_at.asc
Headers: apikey: {SUPABASE_SERVICE_ROLE_KEY}, Authorization: Bearer {SUPABASE_SERVICE_ROLE_KEY}

Fields to extract per trade:
- user_id, ticker, direction (BUY/SELL), size, entry_price, stop_loss, limit_level
- signal_summary (JSON blob with full signal breakdown)
- opened_at, session (AUS_OPEN / UK_OPEN / US_OPEN)
- paper_trade (true/false)

### Trades closed today
GET {SUPABASE_URL}/rest/v1/trade_log?closed_at=gte.{today_start}&closed_at=lte.{today_end}&order=closed_at.asc
Fields to extract per closed trade:
- ticker, direction, entry_price, close_price, pnl_gbp, close_reason (STOP_HIT / LIMIT_HIT / MANUAL / HOLD_CLOSE / DETERIORATION)
- opened_at (to compute hold duration)

### Positions still open (held overnight)
GET {SUPABASE_URL}/rest/v1/positions?order=opened_at.asc
Fields: user_id, ticker, direction, entry_price, current_price, unrealised_pnl, stop_loss

### Daily P&L per user
GET {SUPABASE_URL}/rest/v1/daily_pnl?trade_date=eq.{today}
Fields: user_id, total_pnl, trade_count, win_count, loss_count, daily_loss_hit

### Macro snapshot (most recent from today)
GET {SUPABASE_URL}/rest/v1/macro_snapshot?snapshot_time=gte.{today_start}&order=snapshot_time.desc&limit=1
Fields: vix, dxy, yield_spread_2y10y, macro_gate_pass

---

## Step 2 — Compose the Report

Write the report in plain English. Use this structure:

---

**EndToEndTrading — Daily Report {date}**

**Macro Environment**
State the VIX, DXY, and yield spread (2Y–10Y). Was the macro gate open or closed at each session? One sentence on what the macro environment meant for risk appetite today.

**Sessions**

For each session that ran today (AUS open ~00:00 UTC, UK open ~08:00 UTC, US open ~14:30 UTC):

*[Session Name] — [time UTC]*
- Instruments scanned: list them
- Macro gate: PASS or FAIL (with VIX/spread at that point if gate failed)
- Trades opened: for each trade, one sentence covering instrument, direction, why the signal fired (draw from signal_summary — mention which primary signals aligned and which confirmation signals were present), entry price, stop, and target
- If no trades: one sentence explaining what was evaluated but why nothing qualified (gate failure, insufficient signals, spread too wide, etc.)

**Trades Closed Today**
For each trade closed: instrument, direction, entry → exit, P&L in GBP, hold duration, and close reason. One sentence on whether the exit was clean or cut short.

**Open Positions (Held Overnight)**
For each: instrument, direction, entry price, current price, unrealised P&L, stop level. One sentence on the thesis still in play.

**Daily P&L Summary**
| User   | Trades | Wins | Losses | P&L (GBP) |
|--------|--------|------|--------|-----------|
| Owner  | ...    | ...  | ...    | ...       |
| Wife   | ...    | ...  | ...    | ...       |
| Son*   | ...    | ...  | ...    | ...       |
*Son = paper trades only

**Circuit Breakers / Alerts**
List any that fired today: daily loss limits hit, macro gate blocks, spread rejections, deal rejections from IG, positions closed by deterioration. If none: "No circuit breakers triggered today."

**Tomorrow**
Fetch ForexFactory calendar for tomorrow:
GET https://nfs.faireconomy.media/ff_calendar_thisweek.json
List any HIGH impact events with their time (UTC) and currency affected. One sentence on which open positions or session instruments are exposed to those events.

---

## Step 3 — Deliver Report

POST the full report text to SLACK_TRADES.

If no trades were opened or closed today (fully flat day): still post the report with macro environment, what was scanned, and why no trades fired. A flat day with a clear explanation is useful — do not skip the report.

If daily_loss_hit = true for any user: prefix the report with a bold alert line:
"⚠ CIRCUIT BREAKER HIT — {user} daily loss limit reached. No further trades for {user} today."
