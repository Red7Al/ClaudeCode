# Audit vs the official (publicly documented) Squeeze method

Audited 2026-06-12 against Francis Hunt's publicly documented Squeeze rules. Sources:
- themarketsniper.com — Francis Hunt's own site (Squeeze for lifestyle traders, equity markets)
- ratiopatterntrader.wordpress.com/patterns — independent codification of the pattern
- public consensus write-ups ("The The Squeeze" PDF; "Squeeze Method Under the
  Microscope")

> **Update 2026-06-22 — clean RW cut-over.** Since this audit the engine was rebuilt onto a single
> clean ruleset (`hvf_clean.detect_hvf`): strict swings, a real L3, no flat-top tolerance, no
> Method-A/B override, **tightness ≤ 35%**, **R:R ≥ 3** (~74% stricter, 0 wrong-direction flips).
> This **resolves item 3** (tightness now matches RW's ≤35%), **re-opens item 1** (the
> exhaustion-AMP1 re-anchor was removed), and **bounds item 2** (the override was dropped / now
> defers to the medium-term trend — the ABF fix). Per-item notes below are annotated accordingly.

IMPORTANT: Hunt teaches the precise thresholds (tightness %, stop/weekly-close rules,
the trade stages, KLOS, R:R minimum) only inside his PAID platform. They are NOT in any
free official source. So any specific number for those is an interpretation (ours, or the
colleague's RW skill) — NOT "official". Only the items marked ✓ below are confirmable from
public Hunt material.

## Confirmed FAITHFUL to the official method ✓

| Official rule (public) | Our implementation | Status |
|---|---|---|
| Pattern = three alternating swings: lower highs + higher lows, converging funnel | H1>H2>H3 / L1<L2<L3, convergence gate | ✓ exact |
| Continuation pattern — trade in the direction of the PRIOR trend; choppy/flat markets rejected; pattern starts at the trend's "exhaustion point" | trend gate + exhaustion H1 | ✓ (but see override caveat) |
| Swings must be supported by actual price action (candle turns), not trendline touches | pivots = ±5/±3-bar window extremes (real candle turns) | ✓ |
| Entry = break of the 3rd high (long) / 3rd low (short) | entry at H3 / L3 working order | ✓ exact |
| **Target = distance(H1, L1) measured from the midpoint(H3, L3)** | `Target = (H3+L3)/2 ± (H1−L1)` | ✓ EXACT MATCH — quoted verbatim from Hunt's definition |
| AMP1 = H1−L1, the initial-exhaustion amplitude, full (not discounted) | `initial_range = H1−L1`, full AMP1 | ✓ |

The core mechanics are correct. The target formula in particular is word-for-word Hunt's:
"the distance between the first high and the first low measured from the midpoint between
the third high and the third low."

## Needs ATTENTION

### 0. Three-way AMP1 comparison (official vs RW vs ours)
Hunt's "first low" = the first natural-support PULLBACK low after the exhaustion high —
NOT the 52-week low.
- **RW stated rule**: matches Hunt exactly — "AMP1 = H1−L1, exhaustion candle levels, NOT
  52wk range, full target, no discounting."
- **RW worked examples**: deviate — Gold uses the post-ATH counter-low (correct), but
  Silver and Rolls-Royce substitute the 52-WEEK LOW for L1 (RR flagged "proxy, verify").
  RR: RW L1=868p (52wk) → AMP1 552 → target 1,771; official first-pullback low ≈1,078p
  (30 Mar) → AMP1 342 → target ≈1,607. RW's target is ~164p high purely from the proxy.
- **Ours**: in-window pivots — correct WHEN the window reaches the exhaustion top (RR.L
  weekly already anchors at the 1,420 ATH), but daily windows clip older tops.
Net: definition matches across all three; neither RW's examples nor our daily windows
reliably anchor where Hunt says. RW errs deep (52wk low → inflated AMP1); we err shallow
(in-window → understated AMP1). The official anchor sits between.

### 1. AMP1 exhaustion anchor — ⚠️ RE-OPENED 2026-06-22 (re-anchor removed in the clean cut-over)
Hunt is explicit: AMP1 is dictated by "the extremities of the price action in its INITIAL
EXHAUSTION" — i.e. the actual top of the prior trend (H1) and its first natural-support
pullback low (L1). Our scanner picks H1/L1 as swing pivots inside a FIXED LOOKBACK WINDOW
(220/180/90/60/30 days, + weekly). If the true exhaustion top predates the window, our H1
is a later, lower pivot → AMP1 understated → target too near → R:R understated.
This is exactly the RR.L case: the real exhaustion top is the 1,420 ATH (Feb), which the
daily windows clip; only the weekly path sees it.
- Mitigation already present: the weekly timeframe reaches ~3 years, so a true multi-month
  funnel is caught there.
- Residual risk: daily-timeframe Squeezes on instruments with a >7-month-old exhaustion top
  use a clipped AMP1. The 2026-06-12 `apply_exhaustion_amp1` re-anchor was **removed** in the
  2026-06-22 clean cut-over ("funnel logic in ONE place"): `hvf_clean.detect_hvf` now uses the
  funnel's own in-window pivots on every timeframe. Mitigation reverts to the weekly path's ~3-year
  reach; the daily-window clip is a known, accepted limitation again — the one open detection item.

### 2. Continuation-only vs our recent-trend override
Hunt's Squeeze is strictly a CONTINUATION pattern. Our `recent-trend override` re-classifies a
post-peak decline as a new DOWNTREND and then hunts a bearish funnel (the BP case). That is
a philosophical DEPARTURE — Hunt would treat a fresh reversal as not-yet-an-Squeeze. We bounded
it (disabled in STRONG_UPTREND), but it can manufacture "continuation" setups the purist
method would not recognise. **Decision for the user:** keep (pragmatic, catches real tops)
or restrict further.
**Update 2026-06-22:** the clean ruleset dropped the Method-A/B override, and both detectors'
recent-trend overrides now DEFER to the medium-term trend — a name down ≥10% over ~6 months but
bouncing the last 10 weeks reads as DOWNTREND, not STRONG_UPTREND (the ABF fix, v1.33.0). Rising
recent highs can no longer flip a medium-term downtrend up, so the manufactured-continuation risk
is much reduced.

### 3. Numbers we present that are NOT official
- Funnel tightness: **RESOLVED 2026-06-22** — the clean ruleset adopts RW's **≤ 35%** tightness gate
  (`hvf_clean.detect_hvf`). The old `convergence < 0.70` house tuning (which accepted funnels ~twice
  as wide as RW) is no longer the effective gate.
- R:R floor `3.0`: OUR/refined rule. Public Hunt EXAMPLES show R:R 9.9–13.5; one
  independent trader runs 1.5 R:R at 60% win. 3.0 is a defensible house minimum, not
  "official". Skill text must not imply these are Hunt's published thresholds.

### 4. Weekly-close invalidation (already on backlog #11)
Strongly associated with the method (and in the RW skill) but not in free official docs.
We use a hard intraday broker stop. Genuine risk-appetite decision — synthetic
monitor-managed stops would honour the method; broker stops cap disaster risk.

## Net

The engine implements Hunt's pattern and target mathematics faithfully. After the 2026-06-22 clean
cut-over: (3) tightness now matches RW's ≤35% and (2) the continuation override is dropped / defers
to the medium-term trend — both largely resolved. (1) The AMP1 exhaustion-anchor was **removed** with
the cut-over, so daily windows can again clip an old exhaustion top (the weekly path mitigates) — the
one open detection question. (4) weekly-close vs a hard broker stop remains a risk-appetite decision.
Any change to (1) or (4) still needs the regression suite green and a universe shadow-diff first.
