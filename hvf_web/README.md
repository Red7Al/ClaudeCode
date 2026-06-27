# HVF Scanner website

A filterable web view of every HVF setup, with the X post-card, a date-reactive price chart, the
fixed 3-year history, the RW Rule 1-5 verdicts, the X tweet text, and broker analysis.

## Run

From the **EndToEndTrading repo root** (so the engine modules import):

```bash
# 1. Build the data snapshot (scans the universe — a few minutes). Re-run to refresh.
python -m hvf_web.build_snapshot

# 2. Start the site
python -m hvf_web.server          # -> http://127.0.0.1:5057
```

`run.bat` does both in sequence.

## Share with a colleague (ngrok)

```bash
ngrok http 5057
```

ngrok prints a public `https://<id>.ngrok-free.app` URL — send that to the colleague. The site is
read-only; anyone with the URL can view it, so only share deliberately.

## Filters

direction (bull/bear), location (UK/US/FX), market, sector, HVF status, quality range, R:R range,
months-to-go range, P/E range, insider-% range, and a "setup within last N days" window that also
drives the price chart. Dark/light toggle, top-right. Click any row for the detail panel.

## Notes

- The server **auto-refreshes the snapshot every 12 hours** (and on startup if it's missing or
  already >12h old), clearing the PNG/tweet caches each time. You can still force a refresh by
  re-running `build_snapshot`. The header shows the snapshot time.
- The detail panel's **On X** card is fetched live per instrument: our latest publication link
  (x_publications) plus every tracked account that posted about it (notable_investors). Needs the
  Supabase env vars (`SUPABASE_USER` / `SUPABASE_DB_PASSWORD`).
- The 3-year chart is always 3 years (never affected by the date filter); the "Price — last N days"
  chart re-renders as the date window changes.
- PNGs are cached per ticker for the process lifetime; restart the server after a fresh snapshot.
