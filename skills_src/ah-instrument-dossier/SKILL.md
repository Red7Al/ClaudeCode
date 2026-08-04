---
name: ah-instrument-dossier
description: >
  Produce the COMPLETE publication pack for ONE instrument — pass in a ticker (e.g. RR.L,
  NVDA) and get everything the system would put out for it: the Squeeze analysis (all
  timeframes, entry/stop/target, R:R, quality), the exact X tweet text + post-card PNG, the
  Slack X-draft block, and the trade-open investment-case email (HTML + chart PNGs). Use
  whenever the user asks for "everything on <ticker>", "the full pack/dossier for <ticker>",
  "show me the tweet AND email AND chart for X", or wants to review one instrument's outputs
  before publishing. Everything renders through the production code paths (no rebuilds). A
  LOCAL run writes artifacts to a folder for review and posts NOTHING; the Instrument Dossier
  GitHub Action (which has the secrets) ALSO posts the tweet + card to #arw-claude-twitter.
---

# AH Instrument Dossier — all Slack/email/X outputs for one instrument

One ticker in → every artifact out, via `instrument_dossier.py` (the production module).
The point is fidelity: the tweet, card, email and Slack block are produced by the SAME
functions the live system uses, so what you review is what would publish.

## Hard rules (read first)
- **Posting happens in Actions, never from local.** A local `python instrument_dossier.py`
  run writes artifacts only — SLACK_TWITTER is never set locally, so it cannot post (memory:
  secrets_and_x_delivery, feedback_scheduler — the local machine is switchable off). To get the
  tweet + card into #arw-claude-twitter, run the **Instrument Dossier** GitHub Action (it has
  the secrets and posts via the SAME `_generate_x_drafts` path). Never substitute an MCP send.
- **Numbers are live, never from memory.** Squeeze levels/R:R/quality come from the live scanner
  (`get_hvf_signal_mtf`); the card/tweet come from `render_x_post_card` / `_generate_x_drafts`.
- **Keep it simple** (memory: feedback_keep_it_simple). Do not swap the mechanism; if the user
  wants a tweak, change the content/order, not the renderer.
- **Always show the full instrument name** next to the ticker (memory: feedback_instrument_names).

## Run it
```
python instrument_dossier.py RR.L          # one instrument  (LOCAL: artifacts only, no post)
python instrument_dossier.py NVDA HIK.L    # several
```
To also post the tweet + card to **#arw-claude-twitter**, run the Action (it has the secrets):
```
gh workflow run trading-instrument-dossier.yml -f ticker="RR.L"
```
The run uploads the same artifacts as a downloadable bundle, and the tweet/card land in Slack.
Output → `dossier\<TICKER>_<UTC-stamp>\`:
- `summary.txt`  — Squeeze analysis + a manifest of what was produced (also printed to console)
- `tweet.txt`    — the X tweet text, copy-paste ready (no length prefix — memory: feedback_tweet_presentation)
- `card.png`     — the X post-card image (52w high/low, levels, P/E, confirmations)
- `slack.txt`    — the X-draft Slack block as #arw-claude-twitter receives it
- `email.html`   — the investment-case email, charts inlined so it opens standalone in a browser
- `email_chart_N.png` — the email's chart attachments (price+funnel overlay, volume, schematic)

## What is LIVE vs PREVIEW
- **Squeeze analysis and X (tweet + card): fully live** — re-scanned now from yfinance, IG-arbitrated
  data sanitising applied (phantom-wick clipping). This is the authoritative read.
- **Email/Slack trade confirmations (options flow, COT, directors, VWAP, …): from the latest
  `signal_log` row** for the ticker — i.e. what the LAST session computed. Re-computing them
  live needs the full signal stack (`signals.scan_instrument`) and the API keys that live in
  GitHub Secrets, so a live re-compute is an Actions job, not a local one. The email is clearly
  labelled "PREVIEW — no trade placed" and uses the Squeeze levels as a synthetic trade.

## Presenting the result to the user
- Paste the tweet as a clean copy-paste block (no char-count line); caveats go AFTER the block.
- Lead with the Squeeze one-liner: direction, signal state, R:R, quality, best timeframe + "Also on".
- Point to the saved PNG paths so the user can drop them straight into X / review the email.
- If there is **no qualifying funnel**, say so plainly — only `summary.txt` is written, no
  tweet/card/email (there is nothing to publish).

## Reconciliation
The dossier must agree with the daily Squeeze report and the live X drafts for the same instrument
(same levels, same R:R, same direction). If they differ, the scanner or a stale `signal_log`
row is the cause — investigate before publishing (memory: reconciliation_register).

## Pairs with
- **ah-x-publications** — the tweet/card format spec (this skill renders it for one name).
- **ah-hvf-report** — the prose chart read (this skill is the data/artifact pack).
- **ah-deploy** — when the user then wants it posted live (Actions, with secrets).
