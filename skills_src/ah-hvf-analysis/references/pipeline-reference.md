# Pipeline reference — how an Squeeze setup travels from raw data to a trade and a tweet

All scheduling is cron-job.org → GitHub workflow_dispatch (GitHub-native cron is banned).
Times UTC, Mon–Fri unless stated.

```
Yahoo daily/weekly candles
      │  _sanitise_ohlc (phantom wicks clipped)
      ▼
_find_swing_highs_lows (±5 and ±3 bar pivots)
      ▼
get_hvf_signal × 4 daily lookbacks (220/90/60/30) + weekly path
      │  geometry gates: trend, descending highs (flat-top tol),
      │  ascending lows (flat-base tol), freshness, span,
      │  convergence < 0.70, width ≥ 1%, R:R ≥ 3.0
      ▼
get_hvf_signal_mtf — best state wins (TRIGGERED > READY > DEVELOPING)
      │  check_hvf_invariants — violations alerted + suppressed
      ▼
categorise (run_hvf_report) / hvf_watch (intraday_signals)
      │  UK (.L) tradeable → validate_hvf_with_ig (IG broker candles,
      │  daily cache, allowance-guarded) — fail → demoted DEVELOPING
      ▼
┌──────────────┬───────────────────────┬────────────────────────────┐
│ Slack report │ X drafts              │ Trading                    │
│ #signals     │ #claude-twitter:      │ place_hvf_order_from_sig:  │
│ weight order │ tweet (plain-English  │ IG working order at exact  │
│ TRIGGERED    │ confirmations, quality│ H3/L3 with stop+target     │
│ first        │ ≥60, "Not financial   │ attached; re-signal amends │
│              │ advice.") + post card │ never duplicates; fills/   │
│              │ PNG (files upload)    │ cancels reconciled each    │
│              │                       │ monitor pass               │
└──────────────┴───────────────────────┴────────────────────────────┘
      ▼                                        ▼
Execution guards (ig_shim.open_trade):     Post-trade:
tight-stop 0.5%, spread caps, instrument   _post_trade_review on close
cap 5/day, session caps, half-size         (GOOD/MARGINAL/POOR → #alerts);
INSUFFICIENT_FUNDS retry; blocked trades   missed_trade_log dedupe +
→ alert_missed_trade (classified,          corrective actions + session-
deduped, corrective action)                close digest
```

## Schedules that touch Squeeze

| Job | Cron (UTC) | What |
|---|---|---|
| Squeeze Daily Report | 0 7 Mon–Fri (+ Sat 9) | Full universe scan: FTSE100 + FTSE250 + S&P500 list, 5 timeframes, X drafts for tradeables |
| UK Squeeze Watch | 30 8,10,12,14 | UK_OPEN instrument watch, 2-hourly, dedup fingerprint |
| US Squeeze Watch | 30 14,16,18,20 | US_OPEN instrument watch |
| US/UK/AUS Monitors | */5 in session | Rescan for new entries, route Squeeze to working orders, reconcile fills |
| Data Quality Audit | 15 22 | Yahoo-vs-IG price + identity audit (rotating UK universe) |
| Squeeze regression tests | on push | trading-hvf-tests.yml — every push touching detection code |

## Reconciliation hooks (see memory/reconciliation_register.md)

- Prices: nightly audit (phantom wicks, unit-normalised close deviation)
- Identity: IG instrument name vs Yahoo company name word-overlap (LAND class)
- Pattern levels: IG validation before post/trade
- COT confirmations: staleness >14d neutralised, age always displayed

## Debugging a "why didn't X detect?" question — in this order

1. `python run_data_quality_audit.py X` — is the data clean?
2. Dump pivots: `_find_swing_highs_lows(_get_daily('X', 220), n=5)` — do the swings exist?
3. Walk the geometry gates in parameter-reference order — which condition rejects?
4. Compare against IG candles (`ig_shim.get_prices_df`) — never argue from raw Yahoo wicks.
5. If detection SHOULD change, change it with test_hvf_method.py green + a new frozen
   fixture capturing the case + a shadow-diff across the universe.
