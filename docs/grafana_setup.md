# Grafana Dashboard Setup

## 1. Create a Grafana Cloud account (free)
Go to https://grafana.com → Start for free → create account

## 2. Add Supabase as a data source
In Grafana: Connections → Data sources → Add data source → PostgreSQL

| Field | Value |
|---|---|
| Name | Supabase |
| Host | `aws-0-eu-west-1.pooler.supabase.com:6543` |
| Database | `postgres` |
| User | *(your SUPABASE_USER value from GitHub Secrets)* |
| Password | *(your SUPABASE_DB_PASSWORD value from GitHub Secrets)* |
| SSL Mode | require |
| PostgreSQL version | 15 |

Click **Save & Test** — should show "Database Connection OK"

## 3. Import the dashboard
Dashboards → New → Import → Upload JSON file → select `docs/grafana_dashboard.json`

When prompted for the data source, select the **Supabase** connection you just created.

## 4. Set the refresh interval
Top right of the dashboard → set to **5 minutes**

## Dashboard panels

| Panel | Data source | What it shows |
|---|---|---|
| Macro Gate | macro_snapshot | OPEN / CLOSED with colour coding |
| VIX | macro_snapshot | Current value, threshold colours (green <20, yellow <25, red >35) |
| Yield Spread | macro_snapshot | Current value, red if negative |
| DXY | macro_snapshot | Current dollar index |
| Open Positions | positions + user_profiles | All live trades with entry, stop, target, account |
| VIX 30-day | macro_snapshot | Time series with threshold lines |
| Yield Spread 90-day | macro_snapshot | Curve shape over time |
| Daily P&L by Account | daily_pnl + user_profiles | Bar chart per account last 30 days |
| Weekly Stats | daily_pnl | Total P&L, trades, wins this week |
| Recent Trades | trade_log | Last 50 closed trades, P&L colour coded |
| Signal Effectiveness | signal_log + trade_log | Win rate by options bias |
| Top Senators | senator_scores | Qualified senators ranked by score |
| Today's Signal Log | signal_log | All scanned instruments not yet traded today |

## Quiver Quant API key
Add your Quiver Quant API key as a GitHub Secret: `QUIVER_QUANT_API_KEY`
Get a free key at https://quiverquant.com → API → Sign up
The Weekend Review workflow will refresh senator scores every Saturday at 09:00 UTC.
