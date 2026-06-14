---
name: ah-quality-report
description: >
  Produces the per-instrument "quality angle" publication for HVF setups — a plain-English
  (common-man, NOT accountant) narrative fundamentals report rendered as a PNG, plus a short
  searchable companion tweet, posted to #arw-claude-twitter. Use whenever the user asks for a
  quality/fundamentals report or tweet for an instrument, to change what the report covers or
  how it reads, to tune the daily change-detection, or to wire it to X. Source of truth:
  quality_report.py. Pairs with the HVF chart publication (ah-x-publications).
---

# AH Quality Report — fundamentals "quality angle" per instrument

Two artifacts per instrument, generated together from `quality_report.py`:
1. a **narrative report PNG** (`render_report_card`) — the full read, and
2. a **short skim tweet** (`build_tweet`) — key terms as text so X search indexes it.

On X they post as ONE tweet (text + image via twikit `create_tweet`); in the #arw-claude-twitter
review channel the webhook text + the bot-uploaded PNG land as two adjacent items.

## Hard rules (each came from a specific user correction — don't regress)
- **Common-man English, not accountant.** No jargon: never "CAGR", "ROE", "OCF/FCF", "D/E".
  Say "about 12% a year", "return on shareholders' money", "spare cash", "more cash than debt".
- **One fact per SHORT sentence.** No run-ons, no comma-strung lines over three rows.
- **Read bespoke, not templated.** Each report picks its phrasing PER INSTRUMENT from pools
  (`_pick` + `_P_*`), so no two reports repeat the same wording (the "usual yardsticks" problem).
  Add MORE pool variants whenever they start to feel repetitive.
- **Sector-aware.** For financials (banks/insurers/REITs-style, balance-sheet driven) the usual
  cash-flow / debt / revenue-growth measures are MEANINGLESS — omit them and say why, in a
  *varied* caveat. Lean on dividend record, return on equity, analyst view, insider stake.
- **Numbers must be real and not misleading.** Multi-year track record (e.g. "sales up 4 years
  running"), not a single quarter. Use **Net Income** for profit (the ".L" EPS row is broken).
  Drop a buyback-distorted ROE (only show 0–60%). Skip/contextualise negative FCF.
- **Insider stake as a £/$ VALUE**, not a bare % (0.1% of a mega-cap is still a large sum):
  `heldPercentInsiders × marketCap`. No "skin in the game" hype on a marginal %.
- **"Not financial advice."** on the PNG, styled like the HVF card (grey, italic). Hashtags lean:
  `#TICKER #EXCHANGE #COUNTRY` (real listing exchange; see ah-x-publications), never spammy.

## Daily change-detection
`quality_report.py --daily` compares each setup's entry/target/R:R to the most recent PRIOR
`hvf_scan_log` row and re-publishes ONLY first-seen or moved setups, with a plain
"What's changed: target 6231→6000; R:R 12.6:1→5.0:1" line. Unchanged names are skipped (no spam).

## Publishing & schedule
- Posts to #arw-claude-twitter (`SLACK_TWITTER` webhook + `SLACK_BOT_TOKEN`/`SLACK_TWITTER_CHANNEL_ID`
  PNG upload) — runs in GitHub Actions where the secrets live (never locally; see memory
  `secrets_and_x_delivery`).
- Scheduled via cron-job.org (`setup_cronjobs.py` → `trading-quality-reports.yml`): 07:45 Mon–Fri
  and 09:45 Sat, just after each HVF scan populates `hvf_scan_log`, in `--daily` mode.
- Live X posting is via `x_publish.py` (twikit, cookie auth, 13–17 min stagger) once cookies are
  provided as a secret — see that module's header.

## Source of truth
`quality_report.py` — fundamentals engine (`fundamentals`), prose (`build_report` + `_P_*`/`_pick`),
PNG (`render_report_card`), tweet (`build_tweet`), change-detection (`_changes`), publish (`_post`).
