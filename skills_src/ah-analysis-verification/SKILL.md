---
name: ah-analysis-verification
description: Verify trading-analysis results before they are presented, stored, scheduled, or used for recommendations. Use for backtests, optimisers, rankings, performance cards, signals and trader-facing analysis; do not use for ordinary static UI-only changes.
---

# Analysis verification

Never present a computed trading result as “best”, “optimal”, “verified”, or actionable without evidence that the calculation searched the intended population and configuration space.

## Before presenting analysis

- Define the exact population, time window, deduplication rule, exclusions and usable-field requirements. Show the eligible count.
- Search every user-approved, enforceable configuration dimension. Do not reuse a shortlist selected for a different objective unless an independent proof establishes that it preserves the target objective's optimum.
- Separate cheap pre-ranking from the authoritative calculation. A bounded finalist stage is valid only when candidates originate from the full target grid, its bound is recorded, and the final result can be independently recomputed.
- Use the same execution, wallet, capacity, exit and drawdown rules as the trader-facing result. Never report requested settings that the replay did not actually use.
- For each recommendation, retain the selected configuration, population count, return, drawdown, trade count, date coverage, calculation version and data-generation timestamp.

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
