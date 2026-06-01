# Commodity Fundamentals — Decision Framework
# EndToEndTrading System
# Author: Alex Hind
# Created: 2026-05-30

---

## Why Commodity Fundamentals Matter

Commodities differ fundamentally from equities. A stock is valued on earnings,
growth, and cashflow. A commodity is priced by the balance of physical supply
and demand in the real world, influenced by macro forces that operate above
the level of individual companies.

Understanding commodity fundamentals allows the system to:
- Stay in winning trends (fundamentals move slowly and confirm trend continuation)
- Avoid fading moves that are structurally supported
- Explain WHY a trade was entered — not just "signals fired" but the underlying
  economic rationale

---

## Layer 1 — Macro Drivers
*Implemented in: commodity_macro.py*

Commodities respond to four macro forces. These are not short-term signals —
they set the directional bias that all shorter-term signals should align with.

### 1. USD Strength / Weakness
Most commodities are priced in USD. A weaker dollar makes commodities cheaper
for holders of other currencies, increasing demand and prices.

- Weak USD  → bullish commodities (especially Gold, Oil, metals)
- Strong USD → bearish commodities
- Measured by: DXY vs 20-week moving average + 4-week rate of change
- Source: Yahoo Finance (DX-Y.NYB)

### 2. Real Yields (10-Year TIPS)
Real yield = nominal yield minus inflation expectations.
When real yields fall, the opportunity cost of holding non-yielding assets
(Gold, Silver) decreases — making them more attractive vs bonds.

- Falling real yields → bullish Gold and Silver
- Rising real yields  → bearish precious metals
- Measured by: FRED DFII10 (10Y TIPS yield), 4-week change
- Source: FRED API (free)

### 3. Inflation Expectations (5-Year Breakeven)
The market's forward view on inflation, derived from the spread between
nominal and inflation-protected bonds.

- Rising inflation expectations → bullish energy, industrial metals, agriculture
- Falling inflation expectations → bearish commodities broadly
- Measured by: FRED T5YIE (5Y breakeven inflation rate), 4-week change
- Source: FRED API (free)

### 4. Global Growth Cycle
Economic expansion drives demand for industrial commodities (copper, oil, energy).
Contraction often sees a flight to precious metals as a safe haven.

- Expansion  → industrial metals, energy outperform
- Contraction → precious metals, agriculture hold up better
- Proxy 1: US yield curve (10Y-2Y spread) — steepening = expansion
- Proxy 2: US manufacturing employment trend (FRED MANEMP)
- Source: FRED API (free)

### Instrument-Specific Weightings
Different commodities respond differently to each macro driver:

| Instrument | USD | Real Yield | Inflation | Growth |
|---|---|---|---|---|
| Gold (XAUUSD) | 30% | 40% | 20% | 10% |
| Silver (XAGUSD) | 25% | 30% | 20% | 25% |
| Oil (OIL) | 20% | 10% | 35% | 35% |
| Copper | 20% | 5% | 20% | 55% |

---

## Layer 2 — COT (Commitment of Traders)
*Implemented in: cot_analysis.py*

The CFTC publishes weekly positioning data showing what commercial hedgers
and large speculators are doing. This is the most reliable lead indicator
for commodity turning points.

### Commercial Hedgers
Producers (miners, oil companies, farmers) and consumers (refiners, manufacturers)
who hedge their physical exposure. They are "smart money" — they know their
business and are often right at extremes.

- Extreme commercial net-long → major bullish turning point likely
- Extreme commercial net-short → major bearish turning point likely
- Measured by: percentile rank vs 52-week history (>90th = extreme)

### Managed Money (Large Speculators / Hedge Funds)
Trend followers — they are right in the middle of a trend but wrong at extremes.
When Managed Money reaches extreme net-long, the crowd is crowded → reversal risk.

- Managed Money extreme net-long  → contrarian bearish signal
- Managed Money extreme net-short → contrarian bullish signal

### Price vs Positioning Divergence
When price moves one way but smart money positions the other way:

- Price falling, commercials covering (net increasing) → bullish divergence
  Smart money thinks the low is near and is accumulating into weakness
- Price rising, commercials adding shorts (net decreasing) → bearish divergence
  Smart money is distributing into strength

### Open Interest Signal
OI change tells you whether a price move is driven by conviction or exhaustion:

| Price Direction | OI Change | Signal | Meaning |
|---|---|---|---|
| Rising | Rising | REAL_MONEY_BUY | New longs entering — strong |
| Rising | Falling | SHORT_COVERING | Shorts exiting — weaker |
| Falling | Rising | REAL_MONEY_SELL | New shorts entering — strong |
| Falling | Falling | LONG_LIQUIDATION | Longs exiting — may exhaust |

---

## Layer 3 — Supply & Demand Fundamentals
*Implemented in: commodity_supply_demand.py*

This is where commodities differ most from equities. Physical supply and demand
balances drive prices over weeks and months. Fundamentals are slow-moving and
confirm trend continuation.

### Key Principle
**Falling inventories + rising demand → strong bullish signal**
**Rising inventories + weak demand  → bearish signal**

### Oil (EIA Weekly Petroleum Status Report)
Published every Wednesday by the US Energy Information Administration.
The most important weekly data release for crude oil traders.

- Crude oil inventories (Cushing, OK and total US)
- Gasoline and distillate stocks
- Refinery utilisation rate
- Production levels

Signal logic:
- Draw > 2M barrels → bullish
- Build > 2M barrels → bearish
- Trend of 4+ consecutive draws → strong bullish
- Trend of 4+ consecutive builds → strong bearish

Source: EIA API (free, key required)

### Precious Metals (COMEX/LME Warehouse Stocks)
- COMEX registered Gold and Silver stocks
- LME warehouse stocks for industrial metals (Copper, Aluminium, Zinc)
- Central bank Gold purchases/sales (monthly, World Gold Council)

Falling registered stocks = physical tightness = bullish
Rising stocks = surplus = bearish

Source: Quandl/Nasdaq Data Link (some free), COMEX reports

### Geopolitical Risk Factor
Commodities with concentrated production are highly sensitive to disruption:

- Oil: Middle East tensions, OPEC+ production decisions, Russia supply
- Natural Gas: Russia-Europe pipeline, LNG terminal capacity
- Metals: Chile/Peru mining strikes (copper), South Africa (platinum, palladium)
- Agriculture: La Niña → drought in Australia, South America → grain price spike
  El Niño → flooding in Southeast Asia → palm oil disruption

System approach: Score geopolitical risk as a binary amplifier.
If a known disruption risk is active for an instrument → widen stop distance,
reduce position size (more uncertainty), but do not block the trade direction.

---

## How These Layers Combine in a Trade Decision

Example — XAUUSD BUY decision:

| Layer | Signal | Contribution |
|---|---|---|
| Macro: USD | BEARISH for commodities | Supportive |
| Macro: Real yields | BULLISH (falling) | Strong support |
| Macro: Inflation | NEUTRAL | No contribution |
| Macro: Growth | NEUTRAL | No contribution |
| COT: Bias | NEUTRAL (commercials covering) | Mild support |
| COT: Score | +43.4 | Positive |
| COT: Divergence | BULLISH (price down, commercials buying) | Strong support |
| COT: OI | REAL_MONEY_SELL | Caution — some selling still present |
| Supply/Demand | Inventory draw at COMEX | Supportive |
| Options flow | BULLISH | Primary signal |
| BB Squeeze | BULLISH breakout | Primary signal |
| Senate signal | None | — |
| Trade fires? | YES — 2 primaries + multiple confirmations | |

The system can explain this trade in plain English:
"Gold entered long: Real yields falling (supporting non-yield assets),
commercials reducing shorts into weakness (bullish divergence), options
market buying calls. COT score +43.4. Stop at 1.5× ATR below entry."

---

## Trade Explanation Template

Every trade logged to Supabase includes a signal_summary field.
For commodity trades, this should include:

"[INSTRUMENT] [DIRECTION]: [Macro context]. [COT summary].
[Supply/demand context if available]. [Technical signals].
Stop: [ATR × multiplier]. R:R: [ratio]:1."

Example:
"XAUUSD BUY: Real yields falling (-0.12% 4wk), USD mildly strong headwind.
COT +43.4 — commercials covering (bullish divergence), MM neutral.
Options BULLISH (ratio 2.1), BB breakout confirmed.
Stop: 1.5×ATR (11.7pts). R:R: 2:1."

---

## Data Sources Summary

| Data | Source | Cost | Frequency |
|---|---|---|---|
| USD (DXY) | Yahoo Finance | Free | Real-time |
| Real yields (TIPS) | FRED API | Free | Daily |
| Inflation breakeven | FRED API | Free | Daily |
| Yield curve | FRED API | Free | Daily |
| Manufacturing PMI proxy | FRED API | Free | Monthly |
| COT positioning | CFTC API | Free | Weekly (Friday) |
| Oil inventories | EIA API | Free (key needed) | Weekly (Wednesday) |
| COMEX/LME stocks | Nasdaq Data Link | Partial free | Daily |
| Geopolitical risk | News sentiment | Manual review | As needed |
| OPEC+ decisions | Official releases | Free | Monthly |
