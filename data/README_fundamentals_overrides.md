# `fundamentals_overrides.json` — authoritative source overrides

Automated fundamentals come from Yahoo Finance. The reputable providers we trust —
**Bloomberg, Investing.com, S&P Global, Morningstar, FactSet** — take precedence on any conflict
(user 2026-06-19). We don't have live API access to them, so when one of them gives a different,
authoritative figure, record it here and it overrides Yahoo automatically.

`quality_report.fundamentals()` reads this file, applies any override, and `build_report()` cites
the source in the report (e.g. *"Authoritative figures used: target pct per Morningstar — these
override the automated feed."*).

## Format

Keyed by **ticker** (upper-case), then **field**, each with a `value` and the `source`:

```json
{
  "OXY": {
    "target_pct": { "value": 18.0, "source": "Morningstar" },
    "roe":        { "value": 0.21, "source": "S&P Global" }
  },
  "AAPL": {
    "analyst_rec": { "value": "Buy", "source": "FactSet" }
  }
}
```

Field names match the keys produced by `fundamentals()` — e.g. `target_pct`, `roe`, `analyst_rec`,
`analyst_n`, `rev_latest`, `insider_pct`, `top_holder`, `top_holder_pct`, `mcap`, `industry`,
`div_streak`. Use the same units the automated feed uses (`roe` is a fraction, e.g. `0.21` = 21%;
`target_pct` is a percent).

Leave the file as `{}` when there are no overrides.
