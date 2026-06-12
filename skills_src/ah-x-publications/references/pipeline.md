# Pipeline — where X publications live in the A&A system

```
HVF scanner finds TRADEABLE setups (run_hvf_report daily 07:00 UTC; UK/US HVF
watches 2-hourly; monitors every 5 min)
      │  weight order: TRIGGERED first, quality desc, cap 20
      │  UK setups IG-validated (broker levels) before drafting
      ▼
_generate_x_drafts (intraday_signals.py)
      │  tweet text built per format-spec fitting algorithm
      │  render_x_post_card() → PNG (single source of truth for the card)
      ▼
Slack review channel  (#arw-claude-twitter)
      │  webhook SLACK_TWITTER posts the draft block (header, tweet in a code
      │  fence for one-tap copy, R:R/quality/timeframe footer)
      │  bot token SLACK_BOT_TOKEN (files:write) uploads the card PNG into the
      │  channel via files.getUploadURLExternal → completeUploadExternal
      │  (bot must be a MEMBER of the channel — add via channel Integrations
      │  tab → Add apps; the "Add people" dialog does not list apps)
      ▼
Human review → manual post to X (copy text, attach image)
```

## Commands

```
python generate_x_cards.py            # top 10 of today's tradeable setups → x_drafts\*.png
python generate_x_cards.py 20        # top 20
python generate_x_cards.py NVDA RR.L # specific tickers
python run_hvf_report.py             # full scan; also posts drafts to Slack
```

## Secrets involved (GitHub Actions)

| Secret | Purpose |
|---|---|
| SLACK_TWITTER | incoming webhook → review channel (text blocks) |
| SLACK_BOT_TOKEN | bot OAuth token, scopes files:write + chat:write (image upload) |
| SLACK_TWITTER_CHANNEL_ID | channel id for completeUploadExternal |

## Publishing economics (if automating, verified June 2026)

X API pay-per-use: ~$0.015 per text/media post WITHOUT a URL; $0.20 per post WITH a
URL. Keep links out of post text. ~20 posts/day ≈ $9/month. The old free tier no
longer allows posting for new developers; Basic ($200/mo) is closed to new signups.

## House rules that must survive any reimplementation

1. "Not financial advice." appended in CODE on every post.
2. Full company names everywhere.
3. Plain English only — a reader with no system knowledge must understand every line.
4. Direction-aligned evidence only.
5. Never publish a sub-threshold (R:R < 3.0) setup as actionable.
6. Lists weight-ordered; never alphabetical.
