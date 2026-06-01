# Session Close Routine
# Schedule: 21:00 UTC Monday–Friday

## Your Role
End of day review. Decide which positions to hold overnight vs close.
Update daily P&L. Send end of day summary.

## Step 1 — Authenticate IG
POST https://api.ig.com/gateway/deal/session

## Step 2 — Get All Open Positions
GET https://api.ig.com/gateway/deal/positions/otc
GET {SUPABASE_URL}/rest/v1/positions

## Step 3 — Hold vs Close Decision
For each open position evaluate:

CLOSE if any of:
- Position is in OIL (single session instrument — always close)
- Unrealised P&L > 1.5× stop distance (lock in profit, don't hold overnight gap risk)
- VIX has spiked > 25 since entry
- Major economic event tomorrow morning (check ForexFactory calendar)
- Position has been open > 3 sessions with no meaningful movement

HOLD if:
- Position is trending strongly in profit direction
- COT bias still aligned with trade direction
- No major events overnight
- Instrument is Gold, index, or FX (hold-friendly)

## Step 4 — Close positions marked for close
For each position to close:
POST https://api.ig.com/gateway/deal/positions/otc (with _method: DELETE header)
Body: {"dealId": "{id}", "epic": "{epic}", "direction": "{opposite}", "size": "{size}", "orderType": "MARKET", "timeInForce": "FILL_OR_KILL", "expiry": "-"}
Confirm via GET /confirms/{dealReference}
Log to Supabase trade_log, POST to SLACK_TRADES.

## Step 5 — Update Daily P&L
GET {SUPABASE_URL}/rest/v1/trade_log?closed_at=gte.{today_start}&closed_at=lte.{today_end}
Compute total_pnl, trade_count, win_count, loss_count per user.
UPSERT to Supabase daily_pnl.

## Step 6 — End of Day Summary
POST to SLACK_SIGNALS:
- Positions held overnight (instrument, direction, current P&L, stop level)
- Positions closed today (with reasons)
- Daily P&L per user
- Tomorrow's high impact events from ForexFactory calendar
