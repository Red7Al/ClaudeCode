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

## ⛔ Delivery & secrets — DO NOT BREAK (user 2026-06-13)

This flow already works. Change ONLY the ORDER and the text/PNG CONTENT. Never swap the
delivery mechanism or "work around" it.

- **Delivery (the working format):** `_generate_x_drafts(tradeable)` posts **one Slack
  message per instrument = tweet TEXT + that instrument's card PNG**, in WEIGHT order
  (TRIGGERED first → quality desc → R:R), to #arw-claude-twitter. This is what runs in
  production and what we must preserve.
- **Secrets are in GitHub Secrets, NOT local `.env`.** Text posts via the `SLACK_TWITTER`
  webhook; the PNG uploads via `SLACK_BOT_TOKEN` + `SLACK_TWITTER_CHANNEL_ID` (webhooks
  CANNOT upload images — only the bot token can). Those three are GitHub-only, so the
  publication runs in GitHub Actions, not locally.
- **Never:** try to post from the local environment; ask the user to put secrets in
  `.env`; or replace the text+PNG flow with a text-only Slack-MCP send (the MCP connector
  can't attach images, so it is NOT a substitute). See memory `secrets_and_x_delivery`.

---

## A COMPLETE publication = three artifacts, ALWAYS together (user 2026-06-16)

A publication for an instrument is INCOMPLETE unless all three go out, in this fixed order:
1. **Short tweet text** (`_generate_x_drafts` — ≤280, the hook + plain-English confirmations).
2. **The post-card PNG** (`render_x_post_card` — price chart + funnel + entry/stop/target).
3. **The long fundamentals thread** (ah-quality-report `paginate_report_thread` — a numbered
   1/n TEXT thread: a public-safe chart read THEN the fundamentals; NOT a PNG, since 2026-06-16).

This is **enforced in code**: `_generate_x_drafts` posts (1)+(2) then calls
`quality_report.publish_long_report_for(r)` for (3) — so every publishing path (the daily HVF
report, the UK/US HVF watches, and the instrument dossier) emits all three. Never reorder, never
drop one, never turn the long text back into an image.

## Per-market grouping (user 2026-06-16: "top 10 by market")

X drafts post the top `PER_MARKET_TOP_N` (config, = 10) PER market, GROUPED — a per-market section
header (`📊 FTSE100 — top 10 HVF`) precedes each market's instruments, markets in `MARKET_ORDER`.
Selection + order come from `price_action.group_by_market` (the single source of truth, shared with
the daily HVF report and the quality reports); the per-instrument webhook+card delivery is unchanged.

## Live publishing to X — `publish_one_to_x.py` + `trading-x-publish.yml`

Drafts post to #arw-claude-twitter for review. To push a REAL tweet to the **@SqueezeSignals** X
account, use the official X API path (NOT the Slack flow):
- `publish_one_to_x.py TICKER` builds the SAME short tweet + card AND the long 1/n report
  (`_generate_x_drafts` collect + `quality_report.publish_long_report_for`), then posts the
  COMPLETE publication as ONE X thread via `x_publish.publish_thread_to_x`. All three on X, never
  short+card alone (user 2026-06-16). `--dry` previews without posting.
- **Thread structure (user 2026-06-16):** LEAD = short tweet + card, posted FIRST; then a
  `lead_delay` (12s — media needs time to process) before the long report; the long report's
  **1/n is the MAIN page** (reply to the lead) and **2/n..n/n are COMMENTS on 1/n** (each replies
  to 1/n, NOT chained), spaced by `inter_delay` (5s) so X threads them in order.
- **Dedup (user 2026-06-16: duplicate publications):** a ticker is skipped if it was published to
  X within 12h (recorded in the `x_publications` table); pass `--force` (workflow `-f force=true`)
  to override. Repeated manual runs no longer create duplicate threads.
- After posting, a confirmation with the tweet link is sent to #arw-claude-twitter (`SLACK_TWITTER`
  — must be in the workflow env).
- Actions only — the four `X_*` secrets (`X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`,
  `X_ACCESS_SECRET`) live in GitHub, never local (memory: secrets_and_x_delivery):
  `gh workflow run trading-x-publish.yml -f ticker=AXP` (or `-f dry=true` / `-f force=true`).
- The free X tier returns **402 Payment Required**; a paid / pay-per-use plan with billing is
  required (~$0.015 per tweet — a publication is 1 lead + N reply tweets). Verified 2026-06-16.
- `trading-x-verify.yml` (`x_publish.py --verify`) checks auth without posting.

---

## The tweet (≤ 280 characters, hard limit)

Structure, in order:

```
👀 Watching $TICKER (Full Company Name)                       ← rotated HOOK leads (line 1)
Volatility squeeze {breaking out|coiled, ready} {higher|lower}  ← rotated description (line 2)
A tight coil just broke out the top — momentum often follows.   ← rotated plain-English explainer (line 3)
Pattern quality 82/100  ·  Above VWAP  ·  Options flow bullish (call/put 1.42)
#StockAlert #TechnicalAnalysis #TICKER #Trading
Not financial advice.

(UK names: ".L" stripped → $MNG / #MNG. Prices and timeframe are NOT in the tweet —
 they live on the PNG card. Hook + description both rotate so consecutive posts differ.)
```

Rules (each one is a user directive — violating any is a regression):
1. **"Not financial advice."** on EVERY post, no exceptions (2026-06-11). Appended in code,
   not by author discipline.
2. **Full company name** beside the ticker — "$MNG (Mony Group PLC)", never a bare
   ticker (resolve via the broker lookup, then Yahoo; the system's `_resolve_name`).
   **Strip ".L"** from the cashtag/hashtag for UK names (2026-06-13): $MNG / #MNG, not
   $MNG.L (X cashtags don't allow the dot anyway).
3. **Plain-English justifications only** (2026-06-11): no raw enums, no "Confs:N" counts,
   no NEUTRAL states. The approved vocabulary:
   - Pattern quality NN/100 (only when ≥ 60 — weak quality is not a selling point)
   - "Options flow bullish (call/put 1.42, implied volatility rank 72%)"
   - "Insider buying on record" · "US Senate-disclosed buying"
   - "Futures positioning bullish (COT report, smart money)" — COT commercials are the
     system's smart money; options flow is NOT (mixed institutional + retail), so only
     COT carries the "(smart money)" tag (2026-06-13). · "Strong trend in force (ADX)"
   - "Volume flow backing the move (OBV)" · "Sector (XLK) moving the same way"
   - "Above VWAP" / "Below VWAP" — short tag ONLY (direction-aligned: ABOVE→long,
     BELOW→short). The plain-English VWAP reasoning is drawn on the PNG card, NOT the
     tweet (2026-06-13). No VWAP % in the tweet — `signal_log` stores position, not %.
4. **Only direction-aligned justifications** (2026-06-11: "Bearish is not confirmation
   for a buy"): bullish evidence never decorates a short, and vice versa.
5. **Fitting order when over 280 chars**: keep as MANY justifications as possible first
   (drop verbose detail before dropping a justification), then shorten wording, then drop
   the company name. More evidence beats prettier prose.
6. **No pattern name** in public posts — "Volatility squeeze", never "HVF"/"Hunt
   Volatility Funnel" (the method name stays in-house).
7. **No prices in the tweet text** (2026-06-13) — Now/Entry/Stop/Target/R:R live on the PNG
   card, not the tweet. The tweet is description + clear-English confirmations only.
8. **No HVF timeframe** (e.g. d220) in the tweet OR card (2026-06-13).
9. **Lead with a rotated hook** (2026-06-13) — line 1 is a hook (`👀 Watching $MNG`,
   `🚨 Breakout: $MNG`, …), not raw numbers. Hooks live in `_X_HOOKS` keyed by
   (direction, signal) — a BEARISH setup gets a "Breakdown:" / "📉 breaking down" hook,
   never a bullish one (2026-06-13 fix).
10. **Rotate the phrasing** (2026-06-13) — hook, description AND the explainer cycle
    through `_X_HOOKS`/`_X_DESC`/`_X_EXPLAIN` by batch position + day-of-year so
    consecutive posts don't read identically. Meaning is fixed (state + direction);
    only wording varies. Emoji hooks render fine in tweet text (X/Slack), not the card.
11. **Plain-English primary-signal explainer** (2026-06-13) — line 3 is a rotated,
    direction+state-aware sentence explaining the squeeze so a reader with no system
    knowledge follows it (`_X_EXPLAIN`). The fitting keeps it ahead of extra
    confirmations and the company name, but always inside 280 chars.

## The post card (PNG, attached under the tweet)

Dark "X-native" card, 12 × 8.5 in @ 140 dpi, background `#0d1117`. Two zones:

**Header (top ~30%, left-aligned at x=0.05):**
| y | Text | Colour | Size | Style |
|---|---|---|---|---|
| 0.965 | @EndToEndTrading | #1d9bf0 | 13 | bold |
| 0.925 | ▲/▼ $TICKER (Company Name) — ".L" stripped | #ffffff | 16 | bold |
| 0.888 | Volatility squeeze … (NO timeframe) | #c9d1d9 | 13 | |
| 0.852 | Now · ◎ Entry · ● Stop · ▲ Target · ⚖ R:R — colour-coded markers (see below) | mixed | 12 | |
| 0.818 | 52w High / 52w Low (1y fetch) + ◆ P/E (forward→trailing, omitted if absent/≤0) | #8b949e | 11 | |
| 0.784 | hashtags | #8b949e | 11 | |
| 0.750 | Not financial advice. | #8b949e | 10 | italic |

**Levels line markers (y=0.852, drawn as colour-coded segments, 2026-06-13):** colour
emoji don't render in matplotlib's font, so use DejaVu-safe glyphs, each in its own
colour — `◎ Entry` gold `#e3b341`, `● Stop` red `#f85149`, `▲ Target` green `#3fb950`,
`⚖ R:R` neutral `#c9d1d9`. Rendered segment-by-segment (one `fig.text` per segment,
widths measured via the Agg renderer) because a single text call is one colour.

**Chart (axes rect [0.05, 0.06, 0.83, 0.62]):**
- Price line `#58a6ff` (1.6 px) with a 7%-alpha fill to the series minimum
- Funnel through the REAL pivot dates/levels: upper jaw H1→H2→H3 dashed red `#f85149`
  with dots; lower jaw L1→L2→L3 dashed green `#3fb950` with dots
- Full-width horizontal lines + right-edge labels: Entry dashed gold `#e3b341`,
  Stop dotted red, Target dotted green — label text matches line colour
- **52-week-high gridline** (purple `#a371f7`, dashed, 2026-06-13): target context —
  shows headroom to the year's high or a break to new highs. Skipped if implausibly far
  above the action; y-axis is reframed to all levels so nothing is clipped.
- Axis text `#8b949e` size 9, spines `#30363d`, month-day x-format
- **VWAP logic caption** (bottom, `fig.text` x=0.05 y=0.015, italic `#8b949e` size 8.5),
  shown only when the day's VWAP position aligns with the trade direction (2026-06-13):
  "VWAP: price above the day's volume-weighted average — buyers paying up, demand
  aggressive → confirms the long" (bearish: "below … — sellers pressing, demand weak →
  confirms the short"). Sourced from `signal_log.vwap_position` — the same value as the
  tweet's short tag, so card and tweet always agree. Absent position → no caption.
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
