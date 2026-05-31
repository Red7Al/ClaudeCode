# Price Action Confirmation — Decision Framework
# EndToEndTrading System
# Author: Alex Hind
# Created: 2026-05-30

---

## Why Price Action Confirmation Matters

COT data, macro fundamentals, and supply/demand tell you WHAT to trade
and in WHICH DIRECTION. Price action tells you WHEN to enter.

This is the difference between analysis and execution.

Without price confirmation you risk:
- Catching a falling knife — fundamentals turn bullish but price keeps falling
- Entering too early — the thesis is right but you're stopped out before it plays out
- Fading a trend that has more to run (fighting the tape)

The rule: Even if COT, macro and fundamentals are all bullish, you WAIT
until price confirms the direction before placing a trade.

---

## The Six Price Action Signals

### 1. Breakout from Multi-Month Range
*Most reliable when accompanied by volume expansion*

A commodity or instrument that has traded in a range for 3+ months and then
closes beyond that range is making a structural move. This is not noise —
it's a genuine change in the supply/demand balance.

- Bullish: close above the 60-day highest high
- Bearish: close below the 60-day lowest low
- Strength: breakout on expanding ATR and volume → high conviction
- Weakness: breakout on low volume → watch for failure

Why it matters in commodities:
Commodity prices consolidate while the market digests a fundamental change
(e.g. inventory builds stabilising). The breakout is the market confirming
the new price level is accepted by both buyers and sellers.

Score contribution: ±30

---

### 2. Higher Highs + Higher Lows (Trend Structure)
*The cleanest entry is on the pullback to a higher low — not at the high*

A bullish trend is structurally defined by a series of higher swing highs
and higher swing lows. This is not a pattern — it is the definition of
an uptrend at the weekly level.

- Strong Uptrend:  5+ consecutive HH/HL pairs on weekly chart
- Uptrend:         3-4 HH/HL pairs
- Sideways:        mixed structure
- Downtrend:       3-4 LH/LL pairs
- Strong Downtrend: 5+ LH/LL pairs

Entry timing:
- Best entry in an uptrend = pullback to a higher low with a bounce
- Avoid buying at the high of a swing — wait for the structure to pull back
  and confirm the higher low before entering

Score contribution: ±25

---

### 3. Volatility Compression → Expansion
*Two independent measures: Bollinger Band squeeze + ATR percentile rank*

Markets spend most of their time in low-volatility consolidation, then
release that energy in sharp directional moves. Identifying the compression
before the expansion is the key to good entry timing.

BB Squeeze (signals.py):
- Bollinger Band width at 20-period minimum = compression
- Price closes outside the band = directional release

ATR Compression (price_action.py):
- Current ATR in bottom 20th percentile of 3-month range = compressed
- ATR expanding after compression = breakout underway

Having both measures trigger together significantly increases confidence
that the breakout is real and not a fake-out.

Score contribution: ±10 (timing bonus when both measures confirm)

---

### 4. Moving Average Alignment (20 / 50 / 200 SMA)
*Used as a filter, not a signal on its own*

The 20/50/200 SMA structure tells you whether the trend is aligned across
all time horizons. You want to trade with the trend, not against it.

Full bullish alignment: 20 > 50 > 200 AND price above all three
- This is the safest condition for long entries
- Price has trend support from short, medium, and long-term

Golden Cross (50 SMA crosses above 200 SMA):
- Signals a medium-term trend change to bullish
- Strong in commodities when accompanied by COT confirmation

Price vs 200 SMA:
- Price more than 20% above 200 SMA = extended, wait for pullback
- Price just crossing above 200 SMA = early in a new uptrend = good entry

Score contribution: ±20

---

### 5. Failed Breakdown (Bullish)
*One of the highest-conviction signals in commodity markets*

A failed breakdown occurs when:
1. Price breaks below a key multi-month support level
2. This triggers stop losses and attracts new short sellers
3. Price then recovers back above the support level within 1-3 candles

Why it's powerful:
- All the weak longs have been stopped out (reduced selling pressure)
- Short sellers who entered on the breakdown are now trapped
- Their forced covering drives the next move higher
- It demonstrates that sellers were unable to sustain the breakdown

Examples in commodities:
- Gold falls below $1,800, triggers stops, then closes back above $1,800
  → Short squeeze higher as trapped shorts cover
- Oil breaks below $65 support, recovers next day
  → Bottoming signal — demand absorbed all selling

Score contribution: +15

---

### 6. Failed Breakout (Bearish)
*The mirror image — traps late buyers*

A failed breakout occurs when:
1. Price breaks above key multi-month resistance
2. This attracts momentum buyers and short-cover buying
3. Price then falls back below resistance within 1-3 candles

Why it's powerful:
- Late buyers who chased the breakout are now trapped at the top
- Their forced selling drives the next move lower
- Demonstrates sellers are in control of that price level

Score contribution: -15

---

## Composite Price Action Score

All six signals combine into a score from -100 to +100:

| Score | Verdict | Meaning |
|---|---|---|
| +40 to +100 | CONFIRM_LONG | Price structure confirms a long entry |
| -39 to +39 | WAIT | No clear confirmation — do not enter |
| -40 to -100 | CONFIRM_SHORT | Price structure confirms a short entry |

The verdict is a hard gate on trade execution. If verdict = WAIT,
no trade is placed regardless of how strong the COT or fundamental signals are.

---

## How Price Action Fits the Full Signal Stack

The complete trade decision hierarchy:

```
Layer 1: Macro Gate         VIX, yield curve — must pass
Layer 2: COT Analysis       Smart money positioning + score
Layer 3: Commodity Macro    USD, real yields, inflation, growth
Layer 4: Supply & Demand    Inventories, geopolitical risk
Layer 5: Options Flow       Call/put imbalance, IV rank
Layer 6: Price Action       ← CONFIRMATION GATE — must return CONFIRM_LONG/SHORT
Layer 7: Execution          Enter only if all layers aligned
```

Price action is the last gate before execution. It ensures you are not:
- Entering against the short-term trend
- Catching a falling knife in a downtrend
- Missing the entry timing by entering too early

---

## Trade Explanation Template (updated)

Every trade now includes price action in the signal_summary:

"[INSTRUMENT] [DIRECTION]:
Macro: [USD/yield/inflation/growth context]
COT: score [X], [commercial extreme if any], [divergence if any]
Supply/Demand: [EIA/inventory context if commodity]
Price Action: [verdict] — [breakout/trend/MA/failed break detail]
Entry: [level] | Stop: [ATR x multiplier] | Target: [2:1 or trail]"

Example:
"XAUUSD BUY:
Macro: Real yields falling -0.12% (bullish precious metals), USD mild headwind
COT: +43.4 — bullish divergence (commercials covering into weakness)
Supply/Demand: COMEX data unavailable, no geopolitical premium
Price Action: CONFIRM_LONG — UPTREND (4 HH/HL weekly), price above 50/200 SMA,
              ATR compressed (18th pct), expanding this week
Entry: 3,245 | Stop: 1.5×ATR (11.7pts below) | Target: 2:1 (23.4pts)"
