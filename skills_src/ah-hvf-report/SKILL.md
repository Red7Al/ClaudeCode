---
name: ah-hvf-report
description: >
  Writes a narrative HVF chart report for one instrument in the A&A house style — "What
  today's chart is telling you" — walking the chart left to right from the prior trend
  through the funnel pivots to today's candle, with confidence-labelled levels, BOTH
  risk/reward framings (system stop vs full-funnel stop), weekly-close scenario analysis
  and catalyst awareness. Use whenever the user asks for an "HVF report", "chart read",
  "what is X's chart telling us", a setup write-up for an instrument, or a colleague-style
  pattern report. Numbers must come from the live scanner + IG broker validation, never
  memory. Pairs with the post-card image from ah-x-publications.
---

# AH HVF Report — narrative chart read for one instrument

The report is PROSE walking the chart, not a data dump. A reader with no system
knowledge must follow it. Every number is sourced live and labelled.

## Section template (in order)

**1. Headline:** `## TICKER (Full Company Name) — HVF Report, {weekday} {date}`
then bold line: **What today's chart is telling you:**

**2. Today's tape (one short paragraph):** close, % move, day high, driver if known —
from IG broker candles, labelled `[confirmed — IG broker candles]`.

**3. The chart, left to right (2–3 paragraphs):**
- The prior trend: where the run started (level + date), where it exhausted — H1 with
  date, corroborating sources
- The funnel legs: the sell-off low (L1), the ceiling behaviour (descending or flat —
  say which, with the pivot dates/levels), the rising lows sequence
- Compression: state how tight the funnel is vs its original height
- Today's candle relative to the trigger — name it "the critical one" only if it is

**4. Weekly-close verdict (when the report lands on/after a Friday close):** state
plainly whether the weekly candle closed above or below the trigger and what that means
in stage terms (a fade off the trigger is "a test of the electric fence, not a
breakout" — the setup stays READY; an intraday breach that closed back inside is a
Stage-1 feign, not an invalidation).

**5. Scenario table** — two to three rows: breakout confirmed (with BOTH R:R framings,
see rule below), and the pullback/tightening case. Columns: Scenario | Levels | R:R |
Verdict. Verdicts judged against the 3:1 hard minimum.

**6. The dual-R:R honesty paragraph:** explain WHY the two framings differ (stop anchor:
newest higher low vs the funnel's third low; AMP1 anchor: in-window range vs full
exhaustion range) and close with: *"Verify these levels on your weekly chart before
placing any order."*

**7. Catalyst awareness:** earnings date, central-bank events, and the prior-trend
driver — with the reassessment warning if the driving news could reverse.

**8. Footer:** data sources (IG arbiter, validation status) + "Not financial advice."

## Hard rules

1. **Live numbers only**: run the scanner (`get_hvf_signal_mtf` + `validate_hvf_with_ig`
   for UK) and pull today's candle from IG (`get_prices_df`) at write time. Never reuse
   a previous report's levels.
2. **Confidence labels on every level**: `[confirmed — IG broker candles]`,
   `[confirmed — IG-validated pivot]`, `[system-computed]`, `[estimated]`. Never present
   an estimated level as fact.
3. **Both R:R framings always** — the system's tight-stop R:R alone overstates the
   method; the full-funnel stop alone understates the system. Show both, explain the gap.
4. **Plain English** — no Confs:N, no enums, no internal jargon. "HVF"/"funnel" is fine
   in reports (in-house document), unlike public X posts.
5. **Full company name** in the headline; GBX (pence) for UK levels.
6. **Weekly close is the decision boundary** for trigger/invalidation language — quote
   the actual weekly close when available.
7. Attach the post-card image (ah-x-publications renderer) when the user wants a visual.
8. If the scanner and a human chart read disagree, arbitrate with IG candles and say so
   in the report — never argue from raw Yahoo wicks (phantom prints).

## Worked example

`references/example-rrl-2026-06-12.md` — the Rolls-Royce report that defined this
format (flat-top funnel, weekly-close fade, dual R:R framings, peace-deal catalyst).
