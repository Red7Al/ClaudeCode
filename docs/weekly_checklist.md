# Weekly Trading Checklist — EndToEndTrading System
# Author: Alex Hind
# Created: 2026-05-30
#
# This is the master weekly review checklist.
# Each item maps to a specific signal implemented in the codebase.
# Run every Saturday morning via the weekend_review scheduled task.

---

## 1. POSITIONING (COT + Open Interest)
Goal: Identify crowded trades, turning points, and trend confirmation.

### Managed Money net positions
- Are they at a multi-year extreme (top or bottom 10%)?
  → cot_analysis.py: mm_extreme = EXTREME_LONG / EXTREME_SHORT
  → Uses 3-year (156-week) history for robust percentile ranking
- Are they adding or reducing positions vs last week?
  → cot_analysis.py: managed_money_change (positive = adding longs)

### Commercial hedgers
- Are they taking the opposite extreme to Managed Money?
  → cot_analysis.py: comm_extreme = EXTREME_LONG / EXTREME_SHORT
  → comm_net_pct_rank = percentile vs 3-year history

### Open Interest
- Rising OI + rising price → trend confirmation (real money entering)
  → cot_analysis.py: oi_signal = REAL_MONEY_BUY
- Falling OI + rising price → short covering (weaker signal)
  → cot_analysis.py: oi_signal = SHORT_COVERING
- Rising OI + falling price → real money selling (strong bear signal)
  → cot_analysis.py: oi_signal = REAL_MONEY_SELL
- Falling OI + falling price → long liquidation (may exhaust)
  → cot_analysis.py: oi_signal = LONG_LIQUIDATION

### Divergences
- Price rising while funds reduce longs → bearish divergence
  → cot_analysis.py: price_divergence = BEARISH
- Price falling while funds reduce shorts → bullish divergence
  → cot_analysis.py: price_divergence = BULLISH

### Outcome interpretation
- Positioning at extreme → expect reversal (fade the crowd)
- Positioning expanding with price → expect continuation (trend following)
- Composite COT score -100 to +100: cot_analysis.py: cot_score

---

## 2. MACRO DRIVERS (5 checks)
Goal: Understand the macro wind behind the trade.
Implemented in: commodity_macro.py

### USD Index (DXY)
- Weakening → bullish commodities
- Strengthening → bearish
- Measured: DXY vs 20-week MA + 4-week rate of change
- Source: Yahoo Finance DX-Y.NYB
- Signal: commodity_macro.py: usd.signal

### Real Yields (US 10Y TIPS)
- Falling → bullish gold/silver (lower opportunity cost)
- Rising → bearish precious metals
- Measured: FRED DFII10, 4-week change
- Signal: commodity_macro.py: real_yield.signal

### Inflation Expectations
Two measures:
a) 5Y Breakeven (FRED T5YIE) — near-term inflation view
   → commodity_macro.py: inflation.signal
b) 5Y5Y Forward Breakeven (FRED T5YIFR) — long-run structural inflation
   → commodity_macro.py: five_y5y.signal
   → Rising above 2.5% = persistently elevated → bullish metals/energy
   → Falling below 2.0% = deflation concern → bearish

### Global PMI (ISM Manufacturing)
- Above 50 and rising → EXPANSION → bullish industrial metals, energy
- Below 50 and falling → CONTRACTION → defensive, precious metals outperform
- Source: FRED NAPM (ISM Manufacturing PMI)
- Signal: commodity_macro.py: growth.pmi / growth.signal

### Oil Futures Curve (Contango vs Backwardation)
- Backwardation (front > back) → tight supply → BULLISH oil
- Contango (front < back) → oversupply/weak demand → BEARISH oil
- Measured: WTI front-month vs Brent 6-month spread
- Source: Yahoo Finance CL=F vs BZ=F
- Signal: commodity_supply_demand.py: oil_curve.signal

---

## 3. FUNDAMENTALS (Inventories + Supply/Demand)
Goal: Confirm whether the physical market supports the move.
Implemented in: commodity_supply_demand.py

### Inventory Trends
- Crude oil: EIA weekly (Wednesday release)
  → commodity_supply_demand.py: get_oil_inventory_signal()
  → Draw > 2M barrels = BULLISH, Build > 2M barrels = BEARISH
  → 4+ consecutive draws = STRONG_BULLISH
  Source: EIA API (free, key required: eia.gov/opendata)

- Metals: LME/COMEX registered stocks
  → commodity_supply_demand.py: get_metals_inventory_signal()
  → Currently returns NEUTRAL — requires Nasdaq Data Link subscription
  TODO: Add subscription when available

- Agriculture: USDA WASDE monthly reports
  → Not yet implemented — low priority (outside our instrument list)
  TODO: Add if agriculture instruments added to config.py

### Supply Disruptions
- OPEC+ production decisions → geopolitical_risk table (manual entry)
- Weather (El Niño/La Niña) → geopolitical_risk table (manual entry)
- Geopolitical risks → geopolitical_risk table in Supabase
  → commodity_supply_demand.py: get_geopolitical_risk()
  → Risk levels: HIGH (stop ×1.5, size ×0.5) / MEDIUM / LOW

### Demand Signals
- Baltic Dry Index (BDI)
  → commodity_supply_demand.py: get_demand_signals().bdi_signal
  → Rising BDI (+10% 4wk) = BULLISH commodities broadly
  → Source: Yahoo Finance (BDI coverage variable)

- China demand proxy (Copper price trend)
  → commodity_supply_demand.py: get_demand_signals().copper_signal
  → Copper +3% 4wk = BULLISH industrial metals
  → Source: Yahoo Finance HG=F

- Industrial production / credit impulse
  → Partially covered via PMI (see Macro section)
  → China credit impulse: not yet automated — review manually via PBOC data
  TODO: Add PBOC credit impulse when data source identified

---

## 4. PRICE ACTION (Confirmation Gate)
Goal: Avoid catching falling knives. Wait for price to confirm direction.
Implemented in: price_action.py

HARD RULE: Even if all macro, COT, and fundamental signals are aligned,
NO TRADE is placed until price_action verdict = CONFIRM_LONG or CONFIRM_SHORT.

### Trend Direction
- Higher highs + higher lows? → price_action.py: trend_structure
  → STRONG_UPTREND / UPTREND / SIDEWAYS / DOWNTREND / STRONG_DOWNTREND
- Above 50-day and 200-day MA? → price_action.py: ma_signal
  → FULL_BULL / PARTIAL_BULL / NEUTRAL / PARTIAL_BEAR / FULL_BEAR
- Price vs 200 SMA % → price_action.py: price_vs_200

### Breakouts
- Multi-month range break → price_action.py: range_breakout
  → Uses 60-day high/low as range definition
- Failed breakdown (bullish) → price_action.py: failed_break = FAILED_BREAKDOWN
  → Price breaks below support, recovers → trapped short sellers
- Failed breakout (bearish) → price_action.py: failed_break = FAILED_BREAKOUT

### Volatility
- Low volatility compression → price_action.py: atr_compressed (True/False)
  → ATR in bottom 20th percentile of 3-month range
- Also: signals.py BB squeeze (Bollinger Band width at period minimum)
- Expansion confirmation → price_action.py: atr_expanding

### Volume / OI confirmation
- Breakout + rising OI = real money (REAL_MONEY_BUY in COT layer)
- Breakout + falling OI = short covering (SHORT_COVERING — weaker signal)
- Note: Volume confirmation for commodities via OI signal, not volume directly

### Composite price action score
- Score +40 to +100 → CONFIRM_LONG → entry allowed
- Score -40 to -100 → CONFIRM_SHORT → entry allowed
- Score -39 to +39 → WAIT → no entry regardless of other signals

---

## SIGNAL STACK SUMMARY

| # | Layer | File | Gate? |
|---|---|---|---|
| 1 | Economic calendar | signals.py | Hard stop — no trades |
| 2 | Macro gate (VIX + yield curve) | signals.py | Hard stop if fails |
| 3 | COT analysis | cot_analysis.py | Confirmation signal |
| 4 | Commodity macro (USD/yields/inflation/PMI) | commodity_macro.py | Confirmation signal |
| 5 | Supply & demand + oil curve + BDI | commodity_supply_demand.py | Confirmation signal |
| 6 | Options flow (call/put imbalance, IV rank) | signals.py | Primary signal |
| 7 | BB squeeze / price action confirmation | signals.py + price_action.py | Hard gate |
| 8 | Director buys, senate, superinvestors | signals.py | Confirmation signal |
| 9 | Execution | ig_shim.py | — |

Trade fires when:
- Calendar clear AND macro gate passes
- Primary signals >= 2 (options flow + BB breakout)
- Confirmation signals >= 1
- Price action verdict = CONFIRM_LONG or CONFIRM_SHORT

---

## WHAT TO CHECK MANUALLY EACH WEEKEND

These items are not fully automated — human review recommended:

1. **OPEC+ news** — any production decision or meeting scheduled?
   → Update geopolitical_risk table in Supabase if relevant

2. **Geopolitical events** — Middle East, Russia, shipping disruptions?
   → Update geopolitical_risk table with risk level and stop/size adjustments

3. **China credit data** — any PBOC announcements?
   → Note any major credit impulse changes for industrial metals bias

4. **Seasonal patterns** — any known seasonal patterns active?
   (e.g. Gold tends to rally in Q1, Oil tends to be weak in Q2)
   → Manual awareness — not yet automated

5. **Quiver Quant senator scores** — verify qualified senator list is current
   → Run weekend_review routine (Saturday 10:00 BST)

6. **Earnings calendar for individual equities** — any NVDA/AAPL/META earnings?
   → No new positions in individual equities within 3 days of earnings
   → Not yet automated — manual check recommended
