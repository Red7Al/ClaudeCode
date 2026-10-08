# Squeeze Scanner website

A filterable web view of every Squeeze setup, with the X post-card, a date-reactive price chart, the
fixed 3-year history, the RW Rule 1-5 verdicts, the X tweet text, and broker analysis.

## Live site

**https://www.squeezescanner.cloud/** — hosted on IONOS and served through CGI/WSGI. Releases are built
with `python build_ionos_package.py` and extracted into the domain directory; see `IONOS_DEPLOYMENT.md`.
A push to `main` updates the GitHub Actions automation immediately, but the website only changes on the
next package deploy.

The laptop + ngrok public share was retired on 2026-08-15.

## Run a local development instance

From the **EndToEndTrading repo root** (so the engine modules import):

```bash
# 1. Build the data snapshot (scans the universe — a few minutes). Re-run to refresh.
python -m hvf_web.build_snapshot

# 2. Start the site
python -m hvf_web.server          # -> http://127.0.0.1:5057
```

`run.bat` does both in sequence. This is for development only — it is not how the live site runs.

## Filters

direction (bull/bear), location (UK/US/FX), market, sector, Squeeze status, quality range, R:R range,
months-to-go range, P/E range, insider-% range, and a "setup within last N days" window that also
drives the price chart. Dark/light toggle, top-right. Click any row for the detail panel.

## Notes

- **Snapshot rebuilds do not happen in the web tier.** The production build is the nightly
  `Scanner Snapshot Refresh` job (18:30 UTC, `trading-scanner-snapshot.yml`), which publishes to the
  private `scanner-artifacts` Supabase Storage bucket; the site verifies that object and keeps one local
  last-known-good copy. The legacy in-process 12-hour rebuild loop is opt-in for isolated development
  only, behind `HVF_ENABLE_LOCAL_SNAPSHOT_REBUILD=1`. You can always force a local refresh by re-running
  `build_snapshot`. The header shows the snapshot time.
- The detail panel's **On X** card is fetched live per instrument: our latest publication link
  (x_publications) plus every tracked account that posted about it (notable_investors). Needs the
  Supabase env vars (`SUPABASE_USER` / `SUPABASE_DB_PASSWORD`).
- The 3-year chart is always 3 years (never affected by the date filter); the "Price — last N days"
  chart re-renders as the date window changes.
- PNGs are cached per ticker for the process lifetime; restart the server after a fresh snapshot.
