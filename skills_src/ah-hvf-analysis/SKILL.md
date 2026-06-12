---
name: ah-hvf-analysis
description: >
  The A&A Trading implementation of the Hunt Volatility Funnel (HVF) method — the AUTOMATED
  production version running in the EndToEndTrading system (distinct from the manual
  RW-hvf-analysis reference skill). Use this skill whenever the user mentions HVF, Hunt
  Volatility Funnel, volatility squeeze, funnel patterns, or asks to: scan for HVF setups,
  check a ticker's HVF status, explain why a pattern did or didn't detect, verify entry/stop/
  target levels, generate X post cards, or change anything in the detection pipeline. Also
  trigger for casual phrasings like "any setups today?", "why isn't X showing a funnel?",
  "is the scanner right about Y?". This skill encodes the exact production parameters
  (price_action.py), the data-quality safeguards, the IG validation layer, and the
  regression-test contract that protects them.
---

# AH HVF Analysis — the A&A production implementation

Everything here describes code that RUNS — `price_action.py` is the single source of truth.
When this document and the code disagree, the code wins and this document must be fixed.

---

## The pattern (shared with the classic method)

A prior directional trend, then volatility compresses into a funnel: three descending highs
(H1 > H2 > H3) interleaved with three ascending lows (L1 < L2 < L3). Entry on the breakout
through H3 (bullish) / L3 (bearish), stop beyond the opposite third pivot, target by Hunt's
full-AMP1 formula. Continuation pattern only — never applied to reversals directly (but see
the recent-trend override below, which re-classifies a post-peak decline as the new trend).

```
AMP1   = H1 − L1                      (initial_range in code)
Mid    = (H3 + L3) ÷ 2
Target = Mid + AMP1 (long) / Mid − AMP1 (short)     ← full AMP1, never discounted
Entry  = H3 (buy-stop)   / L3 (sell-stop)           ← pending working order at the exact level
Stop   = L3 × 0.998      / H3 × 1.002               ← 0.2% beyond the third pivot
R:R    = |Target − Entry| ÷ |Entry − Stop|          ← computed from ENTRY, never current price
```

## Production detection rules (exact, from price_action.py)

1. **Data sanitised first** — `_sanitise_ohlc` clips phantom exchange wicks (> 1.5× the 20-bar
   rolling median bar range beyond the bar body) before any pivot detection. Yahoo's LSE feed
   prints fake extremes (RR.L: fake 1,420 high vs IG's real 1,345.9). Raw wicks are never trusted.
2. **Swing pivots** — local extremes over ±5-bar and ±3-bar windows (`_find_swing_highs_lows`).
   Hammer/capitulation bottoms ARE valid pivots (a close-position filter that excluded them was
   removed 2026-06-12 — it left RR.L with one swing low in 220 bars).
3. **Trend gate** — a clear trend must exist (`get_trend_structure`), with a recent-trend
   override: last-3-highs strictly declining ≥5%, or price ≥7% below a dominant peak with ≥2
   lower highs (and long-term trend not STRONG_UPTREND), flips the effective trend to DOWNTREND.
4. **Descending highs with flat-top tolerance** — H1 > H2 × 1.005 AND H3 ≤ H2 × 1.005.
   A flat ceiling against rising lows is converging pressure (the RR.L 1,328/1,330/1,337 case).
5. **Ascending lows with flat-base tolerance** — L2 ≥ L1 × 0.995; L3 ≥ L2 × 0.995 and < H3.
   L3 may be synthetic (midpoint or current price fallback) — flagged `l3_synthetic`.
6. **Freshness & span** — H3 within 60 daily bars (40 weekly); pattern spans ≥10 daily bars
   (≥4 weekly).
7. **Convergence** — (H3−L3) ÷ (H1−L1) must be < 0.70, and the remaining funnel width
   (H3−L3) must be ≥ 1% of price (kills degenerate near-zero-risk patterns).
8. **R:R gate** — `HVF_MIN_RR` (aliased to `MIN_RISK_REWARD`, currently **3.0**) applies BEFORE
   TRIGGERED is assigned. Below it → DEVELOPING (watchlist), never tradeable. Single source of
   truth in config.py — never hardcode.
9. **States** — TRIGGERED (price past entry) > READY (pattern complete, waiting) > DEVELOPING
   (structure valid, R:R or compression not there). Multi-timeframe scan: daily-220 / daily-180 / daily-90 /
   daily-60 / daily-30 / weekly; best state wins, quality breaks ties.
10. **Quality 0–100** — convergence (≤50 pts) + freshness (≤30) + trend strength (10/20) +
    funnel symmetry (≤10).

## What the automation adds beyond the manual method

- **IG broker validation** (`validate_hvf_with_ig`): every UK (.L) tradeable setup is
  corroborated pivot-by-pivot (±1.5%) against IG candles before posting/trading; pass →
  levels recomputed from broker data; fail → demoted with the mismatch named. Budgeted
  against the 10,000-points/week IG allowance with a daily result cache.
- **Wrong-instrument protection**: epic resolution requires UK tickers to map to KA.D.* epics
  (the LAND→Gladstone Land incident); nightly Yahoo-vs-IG audit cross-checks prices AND
  instrument identity.
- **Runtime invariants** (`check_hvf_invariants`): any emitted pattern violating its own
  geometry (negative target, inverted funnel, stop on the wrong side…) is alerted and
  suppressed — it can never reach Slack or a trade.
- **Execution guards**: stop < 0.5% of price on instruments ≥500pt blocked (noise-stop class);
  spread must be < 0.5% of mid and < 0.5× stop distance; per-instrument cap 5/day; session caps.
- **Set-and-forget via working orders**: entry/stop/target placed as one pending IG working
  order at the exact H3/L3; re-signals amend, never duplicate.
- **The regression contract**: `test_hvf_method.py` (18 cases, frozen fixtures) + CI gate.
  ANY change to detection or data handling must keep the suite green.

## How to run things

```
python run_hvf_report.py                      # full universe scan (FTSE100+250+S&P500, 5 TFs)
python generate_x_cards.py 10                 # post-card PNGs for today's top setups
python run_data_quality_audit.py RR.L         # Yahoo-vs-IG audit for specific tickers
python test_hvf_method.py                     # the regression suite (must stay green)
python -c "from price_action import get_hvf_signal_mtf, get_trend_structure; \
  print(get_hvf_signal_mtf('RR.L', trend_hint=get_trend_structure('RR.L')))"   # one ticker
```

## Non-negotiables when answering HVF questions

- Quote levels from the live scanner or IG data — never from memory. UK levels are GBX (pence).
- Always show full instrument names, lists in weight order, plain-English mechanisms.
- R:R below 3.0 is a watchlist item, full stop. Never present it as tradeable.
- If a result looks wrong (nonsense number, missing pattern a human can see), suspect IN ORDER:
  (1) phantom data → run the audit, (2) pivot detection → dump swing pivots, (3) geometry gates
  → check each rule above, (4) only then the method itself. This ordering found all three
  real bugs on 2026-06-12.

## Reference files

- `references/parameter-reference.md` — every constant with its code location and history
- `references/pipeline-reference.md` — end-to-end flow: scan → validate → post → order → review
- `references/differences-vs-rw.md` — honest gap analysis vs the manual RW-hvf-analysis ruleset
