# Weekend Review Routine
# Schedule: Saturday 09:00 UTC

## Your Role
Weekly housekeeping, scoring, and digest. No trades placed.

## Step 1 — Senator Scoring
Fetch 3 years of Senate equity purchases from Quiver Quant:
GET https://www.quiverquant.com/home/senatetrading
Parse senator names, tickers, trade dates, and excess returns.

For each senator with >= 5 qualifying trades (equity purchases only):
- win_rate = count(excess_return > 0) / total_trades
- avg_excess_return = mean(excess_return)
- score = win_rate × avg_excess_return
- qualified = score > 0 AND trade_count >= 5

UPSERT all senators to Supabase senator_scores:
POST {SUPABASE_URL}/rest/v1/senator_scores (upsert on senator_name)
Set qualified = true for top scorers.

## Step 2 — COT Data Refresh
Fetch latest COT report from CFTC for all tracked instruments:
GET https://publicreporting.cftc.gov/resource/6dca-aqww.json?$where=cftc_contract_market_code='{code}'&$order=report_date_as_yyyy_mm_dd DESC&$limit=2

For each instrument (Gold=084691, Crude=067651, GBP=096742, AUD=232741, JPY=097741, SPX=13874+):
- Compute comm_net = comm_long - comm_short
- comm_net_change = this_week - last_week
- bias = BULLISH if comm_net > 0 and increasing, BEARISH if < 0 and decreasing, else NEUTRAL

UPSERT to Supabase cot_snapshot.

## Step 3 — Superinvestor Data Refresh (Dataroma)
Fetch latest holdings for top 10 investors from Dataroma:
- Buffett: GET https://www.dataroma.com/m/holdings.php?m=BRK
- Ackman: GET https://www.dataroma.com/m/holdings.php?m=PS
- Burry: GET https://www.dataroma.com/m/holdings.php?m=MSB
- Tepper: GET https://www.dataroma.com/m/holdings.php?m=DAV
- Icahn: GET https://www.dataroma.com/m/holdings.php?m=ICA
- Klarman: GET https://www.dataroma.com/m/holdings.php?m=BAU
- Coleman: GET https://www.dataroma.com/m/holdings.php?m=CHA
- Terry Smith: GET https://www.dataroma.com/m/holdings.php?m=TER
- Peltz: GET https://www.dataroma.com/m/holdings.php?m=NEL
- Pelosi: GET Capitol Trades STOCK Act disclosures for Nancy Pelosi

For each investor, compare current holdings to previous week in Supabase.
New positions (action=NEW) and adds (action=ADD) → INSERT to notable_investors.
Reductions and exits → INSERT to notable_investors with SELL/REDUCED.

## Step 4 — Weekly P&L Summary
GET {SUPABASE_URL}/rest/v1/trade_log?closed_at=gte.{7_days_ago}
Compute per user: total_pnl, trade_count, win_rate, best_trade, worst_trade.

## Step 5 — Weekly Digest to Slack
POST to SLACK_WEEKLY:
- Weekly P&L per user
- Win rates
- Top 5 qualified senators with scores
- Superinvestor position changes this week
- COT bias changes (any flips this week)
- Next week high impact economic events

## Step 6 — Housekeeping
- Archive signal_log entries older than 90 days
- Verify all open positions still exist on IG (reconciliation check)
- Log any discrepancies to SLACK_ALERTS
