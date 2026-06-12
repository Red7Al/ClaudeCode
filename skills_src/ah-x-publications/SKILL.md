---
name: ah-x-publications
description: >
  Creates X (Twitter) publications for trading setups in the A&A Trading house style: a
  tweet (≤280 chars, plain-English justifications, mandatory "Not financial advice.") plus
  a dark-theme post-card image showing the price chart with the HVF funnel, entry, stop and
  target. Use this skill whenever the user asks to: create/draft/format a tweet or X post
  for a setup, generate a post card or chart image for social media, review tweet drafts,
  explain the publication format, or adapt the format for a new instrument or pattern type.
  Also trigger for casual phrasings like "make me a tweet for this setup", "where's the
  graph for X?", "post-ready chart for NVDA". The format is user-approved (2026-06-10) and
  every rule below exists because of a specific user correction — do not deviate casually.
---

# AH X Publications — tweet + post card, the approved format

One publication = **two artifacts**: the tweet text and the post-card image. They are
generated together from one setup and must always agree on the numbers. In the A&A
system both come from `intraday_signals.py` (`_generate_x_drafts` for text,
`render_x_post_card` for the image — the single source of truth for the card).

---

## The tweet (≤ 280 characters, hard limit)

Structure, in order:

```
📈 $TICKER (Full Company Name) — Volatility squeeze {breaking out|coiled, ready} {higher|lower}, {timeframe} setup
Now: 182.8  Entry: 178.1  Stop: 173.353  Target: 209.88  R:R 6.7:1
Pattern quality 82/100  ·  Options flow bullish (call/put 1.42)  ·  Futures positioning bullish (COT report)
#StockAlert #TechnicalAnalysis #TICKER #Trading
Not financial advice.
```

Rules (each one is a user directive — violating any is a regression):
1. **"Not financial advice."** on EVERY post, no exceptions (2026-06-11). Appended in code,
   not by author discipline.
2. **Full company name** beside the ticker — "$MONY.L (Mony Group PLC)", never a bare
   ticker (resolve via the broker lookup, then Yahoo; the system's `_resolve_name`).
3. **Plain-English justifications only** (2026-06-11): no raw enums, no "Confs:N" counts,
   no NEUTRAL states. The approved vocabulary:
   - Pattern quality NN/100 (only when ≥ 60 — weak quality is not a selling point)
   - "Options flow bullish (call/put 1.42, implied volatility rank 72%)"
   - "Insider buying on record" · "US Senate-disclosed buying"
   - "Futures positioning bullish (COT report)" · "Strong trend in force (ADX)"
   - "Volume flow backing the move" · "Sector (XLK) moving the same way"
4. **Only direction-aligned justifications** (2026-06-11: "Bearish is not confirmation
   for a buy"): bullish evidence never decorates a short, and vice versa.
5. **Fitting order when over 280 chars**: keep as MANY justifications as possible first
   (drop verbose detail before dropping a justification), then shorten wording, then drop
   the company name. More evidence beats prettier prose.
6. **No pattern name** in public posts — "Volatility squeeze", never "HVF"/"Hunt
   Volatility Funnel" (the method name stays in-house).
7. **Current price leads the levels line** — `Now: 182.8  Entry: …` (2026-06-12) so a
   reader judges distance to the trigger at a glance. Same on the card header.
8. Timeframe in human words: "90-day setup", "6-month setup", "long-term", "weekly".

## The post card (PNG, attached under the tweet)

Dark "X-native" card, 12 × 8.5 in @ 140 dpi, background `#0d1117`. Two zones:

**Header (top ~30%, left-aligned at x=0.05):**
| y | Text | Colour | Size | Style |
|---|---|---|---|---|
| 0.965 | @EndToEndTrading | #1d9bf0 | 13 | bold |
| 0.925 | ▲/▼ $TICKER (Company Name) | #ffffff | 16 | bold |
| 0.885 | Volatility squeeze … — {tf} setup | #c9d1d9 | 13 | |
| 0.845 | Entry / Stop / Target / R:R line | #c9d1d9 | 12 | |
| 0.805 | hashtags | #8b949e | 11 | |
| 0.770 | Not financial advice. | #8b949e | 10 | italic |

**Chart (axes rect [0.05, 0.06, 0.83, 0.62]):**
- Price line `#58a6ff` (1.6 px) with a 7%-alpha fill to the series minimum
- Funnel through the REAL pivot dates/levels: upper jaw H1→H2→H3 dashed red `#f85149`
  with dots; lower jaw L1→L2→L3 dashed green `#3fb950` with dots
- Full-width horizontal lines + right-edge labels: Entry dashed gold `#e3b341`,
  Stop dotted red, Target dotted green — label text matches line colour
- Axis text `#8b949e` size 9, spines `#30363d`, month-day x-format
- History window: from 14 days before the oldest pivot (capped 365d, min 30d)
- Data must be SANITISED before plotting (phantom exchange wicks clipped) and pivots
  must be the detector's, never hand-drawn — the funnel sits on real swing points

## Publication quality gates (before anything is posted)

- Setup must be TRADEABLE (R:R ≥ the configured minimum, 3.0 here) — DEVELOPING
  setups are never published
- Batch is WEIGHT-ORDERED: TRIGGERED (breaking out now) before READY, then quality
  descending — cap ~20 per run so the best always make the cut
- UK setups: levels broker-validated (IG) before publication; numbers shown are broker
  numbers
- A geometric impossibility (negative target, inverted funnel) must be suppressed
  upstream — if a card ever shows one, the pipeline is broken, stop publishing

## Delivery flow

1. Scanner finds tradeable setups → drafts auto-post to the review Slack channel
   (tweet text block + card image attached via the Slack bot file upload)
2. Human reviews in Slack → copies text + saves/attaches image → posts to X manually
3. Local generation any time: `python generate_x_cards.py 10` → PNGs in `x_drafts\`
   (identical renderer to the Slack pipeline)
4. If automating publication later: X API pay-per-use ≈ $0.015 per media post without
   URLs (verified June 2026) — keep URLs out of post text, they cost $0.20/post

## Reference files

- `references/format-spec.md` — exact layout constants, colour table, fitting algorithm
- `references/pipeline.md` — where this lives in the A&A system, commands, Slack secrets
