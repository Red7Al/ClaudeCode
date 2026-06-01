# Position Monitor Routine
# Schedule: 02:00 UTC (AUS monitor), 11:00 UTC (UK monitor + US pre-market) — Monday–Friday

## Your Role
Monitor all open positions. Update trailing stops. Close deteriorating positions.
Do NOT open new trades — monitoring only.

## Step 1 — Authenticate IG
POST https://api.ig.com/gateway/deal/session (same as open routines)

## Step 2 — Get All Open Positions
GET https://api.ig.com/gateway/deal/positions/otc
Headers: X-IG-API-KEY, X-SECURITY-TOKEN, CST, Version: 2

Also get from Supabase:
GET {SUPABASE_URL}/rest/v1/positions

## Step 3 — For Each Open Position

### 3a. Check if position still exists on IG
If position in Supabase but NOT in IG positions:
- It was closed externally (stop hit, limit hit)
- Determine close reason via:
  GET https://api.ig.com/gateway/deal/history/activity?dealId={deal_id}&detailed=true
  Look for actionType containing STOP or LIMIT
- Log to Supabase trade_log with correct close_reason
- Delete from Supabase positions
- POST to SLACK_TRADES with close details and reason

### 3b. Update trailing stop for surviving positions
GET current price: GET https://api.ig.com/gateway/deal/markets/{epic}
Extract bid/offer, compute mid price.

Fetch original ATR from Supabase positions (stored in signal_summary or recompute):
GET https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_ticker}?interval=1d&range=30d
Recompute 14-period ATR.
new_stop_distance = ATR × atr_multiplier

For BUY positions:
- new_stop_level = current_price - new_stop_distance
- Only move stop UP (never down) — trailing stop logic
- If new_stop_level > current stop_loss: update stop

For SELL positions:
- new_stop_level = current_price + new_stop_distance
- Only move stop DOWN (never up)
- If new_stop_level < current stop_loss: update stop

Update stop via:
PUT https://api.ig.com/gateway/deal/positions/otc/{dealId}
Headers: X-IG-API-KEY, X-SECURITY-TOKEN, CST, Version: 2
Body: {"stopLevel": {new_stop_level}, "limitLevel": null, "trailingStop": false}

Update Supabase positions table with new stop_loss value.

### 3c. Check macro gate deterioration
Re-run macro gate check (VIX, yield spread).
If macro gate now FAILS and position is open:
- Log warning to SLACK_ALERTS
- Do NOT auto-close — alert owner for manual decision

### 3d. Check daily loss limit
GET {SUPABASE_URL}/rest/v1/daily_pnl?trade_date=eq.{today}
If total_pnl has crossed daily_loss_limit for any user:
- Update daily_pnl set daily_loss_hit = true
- POST to SLACK_ALERTS immediately

## Step 4 — Summary
POST to SLACK_SIGNALS:
- Number of positions monitored
- Any stops updated (old → new)
- Any positions detected as closed
- Any alerts triggered
