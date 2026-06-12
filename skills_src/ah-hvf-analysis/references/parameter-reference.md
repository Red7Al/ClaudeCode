# Parameter reference — every constant, its code location, and why it has that value

The code is the source of truth. Verify against the file before quoting — this table is a map,
not a cache. All in `price_action.py` unless stated.

## Geometry

| Parameter | Value | Where | Why |
|---|---|---|---|
| Swing pivot windows | n=5 and n=3 (both searched) | `_find_swing_highs_lows`, `get_hvf_signal` | 11-bar pivots filter noise; 7-bar catches tighter funnels |
| Descending-highs rule | H1 > H2×1.005 and H3 ≤ H2×1.005 | Condition 1 (daily + weekly paths) | Flat-top tolerance, mirror of flat-base; RR.L 2026-06-12 |
| Ascending-lows rule | L2 ≥ L1×0.995; L3 ≥ L2×0.995, L3 < H3 | Conditions 4 & L3 selection | Flat bases converge too (BP April 2026 case) |
| H3 freshness | ≤ 60 daily bars / ≤ 40 weekly bars | Condition 2 | Stale apexes are dead setups |
| Pattern span | ≥ 10 daily bars / ≥ 4 weekly | Condition 3 | Sub-2-week "funnels" are noise |
| Convergence | (H3−L3)/(H1−L1) < 0.70 | Condition 5 | Less strict than RW's 35% tightness — see differences file |
| Min funnel width | H3−L3 ≥ 1% of price | Condition 5 | Kills degenerate near-zero-risk patterns (infinite R:R) |
| Recent-trend override | last 3 highs strictly declining ≥5%, OR ≥7% below dominant peak with ≥2 lower highs (blocked when long trend = STRONG_UPTREND) | get_hvf_signal | BP-style post-peak reversals; the 220-day trend masks tops |

## Levels & risk

| Parameter | Value | Where | Why |
|---|---|---|---|
| Entry | H3 (long) / L3 (short), pending stop order | get_hvf_signal + ig_shim working orders | Breakout confirmation; no market-order fall-through |
| Stop | L3×0.998 / H3×1.002 | get_hvf_signal | 0.2% beyond the third pivot — strictly pivot-based, never MAs |
| Target | (H3+L3)/2 ± (H1−L1) | get_hvf_signal | Hunt's FULL AMP1 formula, never discounted |
| R:R basis | from ENTRY level, not current price | get_hvf_signal (1.5.x fix) | Current-price R:R lies when price sits near the stop (Lloyds 443:1 case) |
| R:R minimum | HVF_MIN_RR = MIN_RISK_REWARD = 3.0 | config.py (~line 550) | Aliased so HVF and general trading can never drift; raised 2.0→2.5→3.0 |
| R:R gate order | applied BEFORE TRIGGERED | get_hvf_signal + weekly path | A broken-out low-R:R pattern is DEVELOPING, not TRIGGERED (NVDA 0.09 bug 2026-06-04) |
| Synthetic L3 | flagged `l3_synthetic` | L3 fallback branches | Midpoint/current-price L3 has no candle — IG validation must skip it |

## Data quality

| Parameter | Value | Where | Why |
|---|---|---|---|
| Wick sanitiser | clip wick > 1.5 × 20-bar rolling median range beyond bar body | `_sanitise_ohlc` (all fetches) | Yahoo LSE phantom prints: RR.L fake 1,420 high / 990 low (IG truth 1,345.9 / 1,163.4) |
| IG validation tolerance | pivot level within 1.5% of IG candle on pivot date (whole week for weekly pivots) | `validate_hvf_with_ig` | Broker data is the arbiter (user 2026-06-12) |
| IG validation scope | UK (.L) tradeable setups only, weight-ordered, cap 15/run (report) 10/run (watch) | run_hvf_report.categorise, intraday_signals | US Yahoo feed verified clean; allowance budget |
| IG allowance reserve | skip validation below 1,500 of 10,000/week | validate_hvf_with_ig | Verified live 2026-06-12 |
| Validation cache | one IG fetch per ticker per day | ig_validation_log table | 2-hourly watches would burn ~5k points/day uncached |
| Epic resolution | UK tickers MUST resolve to KA.D.* epics; scored selection, refusal alerted | ig_shim.get_epic | LAND→Gladstone Land (wrong company) incident 2026-06-12 |

## Execution guards (ig_shim.py / config.py)

| Parameter | Value | Why |
|---|---|---|
| Tight-stop guard | stop ≥ 0.5% of price on instruments ≥500pt | SNDK 0.24% stop died to tick noise in 3 minutes |
| Spread guards | spread < 0.5% of mid AND < 0.5× stop distance; 15 retries × 20s | A trade opening 1.8R offside cannot win |
| Per-instrument cap | 5 trades/day (raised from 2, user 2026-06-12) | Concentration limit |
| Session caps | AUS 3 / UK 3 / US 4 | Early sessions must not starve later ones |
| INSUFFICIENT_FUNDS | one half-size retry, then alert | Margin race between sizing and submission |

## Quality score (0–100)

`(1 − convergence) × 50` + `max(0, 30 − bars_since_h3)` + `20 if STRONG trend else 10`
+ `funnel symmetry × 10`. Used for ranking only — never gates a trade.
