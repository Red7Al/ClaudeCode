---
name: ah-three-year-best-settings
description: Optimise, explain and verify the Squeeze Scanner's three-year Best Settings recommendations. Use for three-year Best Settings cards, their evidence thresholds, candidate search coverage, replay validity, or trader-facing presentation; do not use for ordinary one-year card changes.
---

# Three-year Best Settings

Three-year recommendations are a primary trading-analysis result. They must be calculated from the full retained three-year squeeze population, not by replaying a shortlist chosen by a one-year model.

## Required analysis invariants

- Begin with all usable retained rows in the three-year window, deduplicated by ticker and trigger date using the same rule as the annual model.
- Search every supported, enforceable scope and every currently supported signal-filter dimension. Do not reduce the candidate population to annual winners, current-card selections, or a hand-picked sample.
- A bounded pre-ranking stage is permitted only when it ranks candidates generated from the entire three-year grid. Record the grid dimensions, shortlist limit, and why that bound is necessary for browser responsiveness.
- Replay shortlisted candidates using the same wallet, stake, capacity, exit, drawdown, and quarterly-consistency model as the one-year Best Settings cards. Report the effective maximum-open value, not an unachievable requested value.
- Apply the user-approved threshold after optimisation: a three-year recommendation must fund more than 125 trades and retain at least 80% of the best annual card's return. If it does not qualify, do not show it as a trader recommendation.
- If no recommendation qualifies, preserve the calculated result in auditable evidence or diagnostics; do not fill the trader-facing card grid with a non-actionable rejection message.

## Verification before handoff

1. Validate the complete inline JavaScript syntax, not only extracted functions.
2. Test that the three-year path is independent of annual-card selections—for example, changing or removing annual finalists must not change the three-year candidate grid.
3. Validate the selected card's funded trade count, return, drawdown, scope and configuration against its replay input.
4. For deployed changes, build, publish, install and verify the live page. Check that the page script runs in a browser and that an eligible three-year recommendation renders as an actionable card.

## Trader-facing presentation

Show a three-year card only when it answers a decision: the exact settings to apply, the evidence population, historical return/drawdown, and the caveat that it is historical. Keep search rejection, thresholds and diagnostic reasons out of the recommendation grid.
