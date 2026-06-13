# Audit vs the official (publicly documented) HVF method

Audited 2026-06-12 against Francis Hunt's publicly documented HVF rules. Sources:
- themarketsniper.com — Francis Hunt's own site (HVF for lifestyle traders, equity markets)
- ratiopatterntrader.wordpress.com/patterns — independent codification of the pattern
- public consensus write-ups ("The Hunt Volatility Funnel" PDF; "HVF Method Under the
  Microscope")

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

### 1. AMP1 exhaustion anchor — the most important gap
Hunt is explicit: AMP1 is dictated by "the extremities of the price action in its INITIAL
EXHAUSTION" — i.e. the actual top of the prior trend (H1) and its first natural-support
pullback low (L1). Our scanner picks H1/L1 as swing pivots inside a FIXED LOOKBACK WINDOW
(220/180/90/60/30 days, + weekly). If the true exhaustion top predates the window, our H1
is a later, lower pivot → AMP1 understated → target too near → R:R understated.
This is exactly the RR.L case: the real exhaustion top is the 1,420 ATH (Feb), which the
daily windows clip; only the weekly path sees it.
- Mitigation already present: the weekly timeframe reaches ~3 years, so a true multi-month
  funnel is caught there.
- Residual risk: daily-timeframe HVFs on instruments with a >7-month-old exhaustion top
  use a clipped AMP1. **Decision for the user:** add an "exhaustion-anchor" step that, for
  a candidate funnel, looks back to the dominant high/low of the FULL available history
  for H1/L1 (not just the window) when computing AMP1 + target. Detection-behaviour change
  → requires suite-green + universe shadow-diff.

### 2. Continuation-only vs our recent-trend override
Hunt's HVF is strictly a CONTINUATION pattern. Our `recent-trend override` re-classifies a
post-peak decline as a new DOWNTREND and then hunts a bearish funnel (the BP case). That is
a philosophical DEPARTURE — Hunt would treat a fresh reversal as not-yet-an-HVF. We bounded
it (disabled in STRONG_UPTREND), but it can manufacture "continuation" setups the purist
method would not recognise. **Decision for the user:** keep (pragmatic, catches real tops)
or restrict further.

### 3. Numbers we present that are NOT official
- Funnel tightness `convergence < 0.70`: OUR tuning. Hunt publishes no number; the RW skill
  uses ≤35%. 0.70 accepts funnels twice as wide as RW — looser stops, lower realised R:R.
- R:R floor `3.0`: OUR/refined rule. Public Hunt EXAMPLES show R:R 9.9–13.5; one
  independent trader runs 1.5 R:R at 60% win. 3.0 is a defensible house minimum, not
  "official". Skill text must not imply these are Hunt's published thresholds.

### 4. Weekly-close invalidation (already on backlog #11)
Strongly associated with the method (and in the RW skill) but not in free official docs.
We use a hard intraday broker stop. Genuine risk-appetite decision — synthetic
monitor-managed stops would honour the method; broker stops cap disaster risk.

## Net

The engine implements Hunt's pattern and target mathematics faithfully. The attention
items are (1) the AMP1 exhaustion-anchor on daily timeframes [highest value], (2) the
continuation-vs-override philosophy, (3) labelling tightness/R:R honestly as house tunings,
(4) weekly-close stops. Items 1, 2 and 4 are detection/behaviour changes — each needs the
regression suite green and a universe shadow-diff before merging.
