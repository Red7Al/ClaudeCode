---
name: ah-analysis-verification
description: Verify trading-analysis results before they are presented, stored, scheduled, or used for recommendations; and to PROVE a trader-facing figure whenever it is questioned, has moved, or is about to be quoted, by recomputing it with the shipped code rather than explaining it. Use for backtests, optimisers, rankings, performance cards, signals and trader-facing analysis; do not use for ordinary static UI-only changes.
---

# Analysis verification

Never present a computed trading result as "best", "optimal", "verified", or actionable without evidence that the calculation searched the intended population and configuration space.

## Before presenting analysis

- Define the exact population, time window, deduplication rule, exclusions and usable-field requirements. Show the eligible count.
- Search every user-approved, enforceable configuration dimension. Do not reuse a shortlist selected for a different objective unless an independent proof establishes that it preserves the target objective's optimum.
- Separate cheap pre-ranking from the authoritative calculation. A bounded finalist stage is valid only when candidates originate from the full target grid, its bound is recorded, and the final result can be independently recomputed.
- Use the same execution, wallet, capacity, exit and drawdown rules as the trader-facing result. Never report requested settings that the replay did not actually use.
- For each recommendation, retain the selected configuration, population count, return, drawdown, trade count, date coverage, calculation version and data-generation timestamp.

## Prove the number — do not wait to be asked

The requester, 2026-08-31, after a headline moved from 183.6% to 109% and the first answer explained the
change instead of proving it: *"I would expect you to prove the figures without me asking."*

Whenever a trader-facing figure is **questioned, has moved, or is about to be quoted**, recompute it and
show the recomputation. An explanation of *why* a number might have changed is not evidence that the
number is *right*, and offering to prove it later puts the work back on the requester.

**Run the shipped code, never a reimplementation.** Extract the actual functions and the actual search
block out of `hvf_web/app.js` and execute them in Node against the live payload. A reimplementation that
is subtly wrong produces a confident wrong number, which is worse than no number. The pattern that works:

- pull each dependency out by name (`_combReplay`, `_pfExitDate`, `levType`, `_fundedMaxOpen`,
  `_dedupeSameDayRows`) and the search itself as a verbatim slice between two anchors in the source;
- feed it the live endpoint payload, not a fixture;
- reproduce the client's own preprocessing — the three-year block reads `WIN_3Y`, which the client stores
  **already deduped by ticker+day**, so a harness that skips `_dedupeSameDayRows` replays the wrong
  population;
- state the baseline assumptions that change the answer, above all that per-user filters
  (`MARKETS_OFF`, `TRADE_HIDE`) are empty. A signed-in user with markets switched off is replaying a
  different population and can legitimately see a different figure.

Worked example, 2026-08-31: 13,340 payload rows → 11,381 after dedupe → **109.3%**, MCap 100bn+,
R:R >= 5, ATR expanding, 3% stake, max open 33, 532 funded trades of 1,039 eligible, 13/13 positive
quarters. That matched the screen to a tenth of a percent and settled the question in one step.

**Report the evidence alongside the figure, not just the figure.** In that example the return fell from
183.6% to 109.3% while the funded-trade count rose from 122 to 532 and every quarter was positive — the
headline got smaller and the evidence got much stronger. A bare "it went down" would have been true and
badly misleading.

**These figures move on their own.** The winners payload is built from `_sqa_all_rows()` on a rolling
`today - 365 x years` cutoff, so the population changes every day as new triggers land and the oldest
roll out — 11,298 rows on 2026-08-25 became 13,340 by 2026-08-31. The rule about flagging a moved number
before the requester sees it therefore applies to data movement, not only to code changes.

## Repeated analysis

- Any analysis that informs a live recommendation must have a scheduled server-side revalidation after its upstream data refresh. Browser rendering is not a daily validation mechanism.
- Persist each run's result or explicit failure. A missing dataset, incomplete input or failed run must be visible; never silently continue to show a stale result as if it were recalculated.
- Compare the latest result with the prior run and expose material changes in configuration, population or outcome.

## Verification gates

1. Test the calculation against an independent, smaller reference implementation or deterministic fixture.
2. Test negative cases that would expose a reduced population, a skipped dimension, a stale cache or an infeasible configuration.
3. Validate the complete client script if a browser renders the result.
4. For live changes, verify the final user-facing page or API after deployment.

If any gate is unavailable, label the result provisional and state the missing evidence. Do not substitute a narrative explanation for a calculation.
