# Format specification — exact constants for reproducing the publication

Source of truth in code: `intraday_signals.py` — `render_x_post_card()` (card) and the
tweet-building section of `_generate_x_drafts()`. This file lets a colleague reproduce
the format outside the A&A codebase.

## Tweet assembly algorithm

Inputs per setup: ticker, company name, direction (BULLISH/BEARISH), state
(TRIGGERED → "breaking out" / READY → "coiled, ready"), timeframe label, entry, stop,
target, R:R, pattern quality, and the aligned-confirmation context.

```
base_with_name = "{emoji} ${ticker} ({name}) — Volatility squeeze {state} {higher|lower}, {tf} setup\n"
                 "Entry: {entry}  Stop: {stop}  Target: {target}  R:R {rr}:1\n"
base_no_name   = same without "({name})" and without the word "setup"
justifications = ordered list of (full, short) phrasings:
    1. Pattern quality {q}/100                      (only if q ≥ 60)
    2. Options flow {bias} (call/put {x.xx}, implied volatility rank {n}%)   | short: drop IV rank
    3. Insider buying on record
    4. US Senate-disclosed buying                   | short: Senate buying    (longs only)
    5. Futures positioning {bias} (COT report)      | short: COT {bias}
    6. Strong trend in force (ADX)                  | short: Strong trend (ADX)
    7. Volume flow backing the move                 | short: Volume backing move
    8. Sector ({ETF}) moving the same way           | short: Sector aligned
tags_long  = "#StockAlert #TechnicalAnalysis #{ticker} #Trading"
tags_short = drop "#Trading"
disclaimer = "\nNot financial advice."

Fitting loop (first candidate ≤ 280 chars wins):
  for base in (with_name, no_name):
    for n in (len(justifications) … 0):          # MOST justifications first
      for wording in (full, short):
        for tags in (long, short):
          candidate = base + join(" · ", first n justifications) + "\n" + tags + disclaimer
Absolute fallback: base_no_name + tags_short + disclaimer
```

Alignment rule: a justification appears ONLY if it argues the trade's direction —
buying evidence (insider/senate) on longs only; COT/sector/OBV bias must equal the
trade side; ADX requires the dominant DI to match the side.

## Card layout constants

```
figure        12 × 8.5 in, dpi 140, facecolor #0d1117 (also savefig facecolor)
axes rect     [0.05, 0.06, 0.83, 0.62], facecolor #0d1117
header        fig.text at x=0.05, va="top", rows as in SKILL.md table
price         line #58a6ff width 1.6, fill_between to series min alpha 0.07
upper jaw     H1→H2→H3 dashed (--) #f85149 width 1.4 alpha 0.9 + scatter s=26
lower jaw     L1→L2→L3 dashed (--) #3fb950 width 1.4 alpha 0.9 + scatter s=26
entry line    axhline dashed #e3b341 width 1.2 + right label "Entry {v}" size 9
stop line     axhline dotted #f85149 width 1.0 + right label "Stop {v}"
target line   axhline dotted #3fb950 width 1.0 + right label "Target {v}"
labels        ax.text(1.01, level, …, transform=ax.get_yaxis_transform(), va="center")
x axis        DateFormatter "%b %d", MonthLocator(1), ticks #8b949e size 9
spines        #30363d;  tick colour #8b949e
direction     ▲ (bullish) / ▼ (bearish) prefix on the title row
```

## Data requirements

- OHLC history from 14 days before the OLDEST pivot date (cap 365 d, floor 30 d)
- Phantom-wick sanitisation BEFORE plotting (clip extremes beyond 1.5× the 20-bar
  median range from the bar body) — fake exchange prints otherwise distort the funnel
- Pivot levels/dates come from the detector — the funnel must sit on real swing points
- Scale normalisation: if quoted levels are >5× the chart's median close (GBX vs GBP
  style mismatches), divide levels by the snapped power-of-ten ratio before plotting
- For UK instruments, prefer broker-validated levels (the numbers a reader can trade)

## Worked example (MONY.L, 12 Jun 2026 — 227 chars)

```
📈 $MONY.L (Mony Group PLC) — Volatility squeeze breaking out higher, d220 setup
Entry: 178.1  Stop: 173.353  Target: 209.88  R:R 6.7:1
Pattern quality 82/100
#StockAlert #TechnicalAnalysis #MONY.L #Trading
Not financial advice.
```
