---
name: ah-x-writing-style
description: >
  The house VOICE for X (Twitter) publications — how the copy should READ, distinct from its
  layout/rules (those live in ah-x-publications). Use whenever writing, drafting, reviewing,
  rotating or EXPANDING the wording of an X post / tweet / thread, or adding variants to the
  rotation pools (_X_HOOKS / _X_DESC / _X_EXPLAIN in intraday_signals.py, or the _P_* prose
  pools in quality_report.py). Enforces deliberate variation in tone, structure, pacing and
  vocabulary plus a natural human rhythm, so no two posts read alike or feel template-generated.
---

# AH X Writing Style — vary the voice, sound human

This is the VOICE layer for everything published to X. It governs *how the words read*; it never
overrides the hard rules in **ah-x-publications** (NFA on every post, no method name / "HVF", no
prices in the short tweet, plain-English direction-aligned confirmations, the complete-publication
order). Vary the wording — never the facts, numbers, direction, or the disclaimer.

## The core rule

**Vary tone, structure, pacing and vocabulary in EVERY output.** Two posts in a row must not read
as if stamped from the same mould.

## Rotate the style

Move between these from post to post — never settle into one:
**concise · analytical · conversational · punchy · narrative · headline-driven · metaphorical ·
data-centric.** A breakout might be punchy one day, a quiet coil narrative the next.

## Anti-template rules (hard)

- **Never reuse a sentence structure** from a previous post.
- **Never start two posts with the same phrase or word.** (Rotate the opener every time.)
- **Avoid template-like patterns** — no fixed "X did Y because Z" skeleton repeated across names.
- Each post must feel **written by a human with subtle personality shifts**, not assembled.

## Add subtle human variation

- occasional **short fragments** — a beat. Like that.
- **varied punctuation** — em dashes, the odd ellipsis, a colon where it earns its place.
- **natural emphasis** (word choice and rhythm, not ALL CAPS or exclamation spam).
- **slight shifts in perspective** (the trader's eye one time, the chart's story the next).
- the **occasional rhetorical question** — used sparingly, never as a crutch.

Introduce human-like RHYTHM, **never errors**: spelling, grammar, tickers, levels and the
confirmations stay correct and on-message. Personality is in the cadence, not in mistakes.

## How this lands in the system (practical)

The live short tweet is assembled from rotated pools, so variation is achieved by **growing and
diversifying the pools**, not by hand-editing each post:
- `intraday_signals.py` — `_X_HOOKS` (line-1 hooks), `_X_DESC` (the squeeze description),
  `_X_EXPLAIN` (the plain-English explainer). Keyed by (direction, signal); rotated by batch
  position + day-of-year. **Add more variants** in different styles whenever they start to feel
  same-y, keeping each meaning fixed (state + direction) and within 280.
- `quality_report.py` — the `_P_*` prose pools + `_chart_story` pools for the long thread. Same
  idea: more variants, more styles, one fact per short sentence, public-safe vocabulary only.
- When you draft or review a post by hand (e.g. an instrument dossier), apply all of the above
  directly, and check it doesn't echo the last few posts' opener or shape.

## Quick self-check before publishing

1. Does the opener differ from recent posts?  2. Is the sentence shape fresh?  3. Which style is
this (and is it different from last time)?  4. Any template skeleton creeping in?  5. Does it read
like a person — fragments, rhythm, a little personality — with zero factual/grammatical errors?

## Pairs with
- **ah-x-publications** — format, hard rules, the complete-publication flow (voice never breaks these).
- **ah-quality-report** — the long 1/n thread whose prose pools this voice also governs.
