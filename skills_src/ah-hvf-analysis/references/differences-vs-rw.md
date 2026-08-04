# Honest gap analysis — AH (automated, production) vs RW (manual Francis Hunt ruleset)

Both implement the same core pattern and the same Hunt target formula with the same hard
R:R ≥ 3:1 floor. The differences below are real and deliberate where noted — review them
when results disagree with a manual chartist's read.

## Where we are STRICTER

| Topic | RW (manual) | AH (ours) |
|---|---|---|
| Data trust | Levels read off a chart, `[confirmed]`/`[estimated]` labels | Phantom-wick sanitiser + IG broker validation + nightly audit; UK levels recomputed from IG candles |
| Wrong instrument | Human picks the chart | Epic scoring (KA.D.* required for .L) + nightly identity reconciliation |
| Nonsense output | Human judgement | check_hvf_invariants runtime guard — geometric impossibilities suppressed + alerted |
| Regression safety | None (manual) | 18-case frozen-fixture suite + CI gate on every push |
| Execution | Manual order placement | Working orders at exact levels, spread/tight-stop/cap guards, INSUFFICIENT_FUNDS retry |

## Where we are LOOSER (review candidates)

| Topic | RW (manual) | AH (ours) | Assessment |
|---|---|---|---|
| Funnel tightness | (H3−L3)/AMP1 ≤ **35%** | convergence < **70%** | MATERIAL: we accept funnels twice as wide. Their tighter apex = tighter stops = the high R:R the method is famous for. Candidate: surface tightness% in quality score or report it per setup. |
| Prior trend magnitude | ≥ ~20% prior move preferred | Trend structure classification, no magnitude floor | We can take funnels off modest trends. Candidate: add prior-move % to quality. |
| Swing confirmation | "actual candle turn" required, trendline-only intersections rejected | Pivot = window extreme (±5/±3 bars) | Similar intent, mechanically defined. OK. |

## Methodological differences to be AWARE of

| Topic | RW (manual) | AH (ours) | Note |
|---|---|---|---|
| Stop invalidation | **Weekly close** beyond the pivot; intraday breach = Stage-1 feign | Hard IG stop at L3×0.998 — an intraday breach takes us out | Significant: Hunt's method tolerates feigns; ours pays the stop. Mitigated by the 0.5% minimum stop guard, but a true weekly-close regime would need synthetic stops (monitor-managed, not broker-held). Backlog candidate — discuss risk appetite first: broker-held stops cap disaster risk, synthetic stops honour the method. |
| Trade stages (Feign/Second Chance/Capitulation/Weak Counter/Target) | Explicit lifecycle guidance | Not modelled; set-and-forget via working order + monitors | Post-trade review grades outcomes instead. |
| KLOS (key levels of significance) | Flagged manually | Not implemented | Chart-reading concept; no mechanical definition yet. |
| Catalyst awareness | Earnings/CB/geopolitics checked per setup | ForexFactory calendar blocks entries near high-impact events; no per-setup earnings check | Candidate: earnings-date check before posting a setup (RW's "RR. earnings 30 Jul" example). |
| Direction | Continuation only; "inverted Squeeze" = short continuation | Same, plus the recent-trend override re-classifies post-peak declines (BP case) | Override is bounded (blocked in STRONG_UPTREND). |
| AMP1 source | Exhaustion-candle H1/L1, explicitly NOT 52wk range | Detected H1/L1 pivot levels | Equivalent when pivots are right — which the sanitiser + IG validation now protect. |

## Bottom line

Same method, two philosophies: RW optimises for chartist judgement, AH optimises for
removing human error and data error at scale. The two flagged review items worth a user
decision are (1) the 35%-vs-70% tightness gap and (2) weekly-close vs hard-stop
invalidation. Both are on the backlog — neither should be changed casually: any change
must keep test_hvf_method.py green and ship with a universe shadow-diff.
