# Let Winners Run — decision brief

_Prepared 2026-08-23 for ChangeRequests 20260821 items 3 and 20. Everything here was read from the code
or measured; nothing is estimated. The purpose is to make your part of this a short decision rather than
a working session._

---

## 1. What it does

Normally an IG working order carries a take-profit, and the position closes automatically at its target.

With Let Winners Run enabled **for a user who opted in**, their orders are placed with **no take-profit**,
so IG does not close at target. A manager then runs on the Order Bridge schedule and, per position:

| Phase | Condition | Action |
|---|---|---|
| **2 — target lock** | price reaches the target | move the stop **to** the target |
| **3 — handover** | price reaches target × (1 + uplift), default +5% | attach IG's own trailing stop, `trail` behind |

It never widens a stop and never closes a position.

**Measured over 3,851 historical trades:** closing at target averages **+3.16%** per trade; this averages
**+3.40%**. The 2026-08-22 simulation over 3,212 resolved eligible trades found zero target-lock breaches
and, on the £10,000 / 5% / 20-position model, a best result at a 4% trail of **+£387.10** against
sell-at-target. That is daily-bar historical evidence — appropriate to a multi-day hold, not a claim
about intraday execution.

---

## 2. The risk that actually matters

**Removing the take-profit creates a dependency on the manager continuing to run.**

With a take-profit, the broker protects the gain even if every one of our systems is down. Without one,
the gain is protected only while the Order Bridge keeps executing. If the bridge fails, the workflow
breaks, IG credentials lapse, or the manager throws for a user, an open winner has **no target-lock and
no trailing stop** — and can round-trip from profit to its original stop.

The bridge runs `0 6-22/2 * * 1-5` — **every two hours, 06:00–22:00, weekdays**. So:

- Worst case within a running day: **~2 hours** unmanaged.
- Overnight and at weekends: **no management at all**, while positions stay open.

Nothing in the current design detects "the manager has not run recently, so positions are unprotected".
That gap is the strongest argument for taking this slowly, and it is a real gap rather than a theoretical
one — this repository has produced six cases of a mechanism that existed but was never invoked.

---

## 3. What is already guarded

All verified in code and covered by 31 tests in `test_trailing_stop.py`:

- **Two independent off switches**, both `False` as shipped: `LIVE_LET_WINNERS_RUN_ENABLED` gates the live
  path, `LWR_OBSERVE_ONLY` gates observation. Neither can be reached by configuration.
- **Per user, always.** The switch and the trail percentage come from that login's own Configuration, so
  the figure a user modelled against is the figure IG is given. An app-wide override was deliberately
  removed.
- **The invariant is enforced, not trusted.** A trail must satisfy `trail < uplift / (1 + uplift)`. A 25%
  trail at a 5% uplift would put the stop 21% *below* the target — inverting the guarantee — and is
  rejected rather than accepted as a preference.
- **Owner-scoped sessions.** Every quote read and every mutation happens inside that owner's own IG
  session, re-entered immediately before the call, so one login's settings cannot move another's stop.
- **Account fingerprint binding.** A position records a hash of the account that opened it; a mismatch
  skips rather than acts.
- **Fails closed everywhere.** Unknown owner, unreadable settings, absent binding, bad trail, session
  failure — all mean "do nothing".

---

## 4. What is NOT verified

**The live path has never executed.** It has been safety-disabled since it was written, so every
assurance above comes from tests and historical replay — not from IG.

Specifically unproven against the real broker: how IG responds to `attach_trailing_stop` and
`update_stop` on a live position; owner-scoped session re-entry with a second real account; and the
end-to-end behaviour of a position placed with no take-profit.

---

## 5. Proposed path — observe first

`LWR_OBSERVE_ONLY` (added 2026-08-23) runs the **complete** decision path against the real account — same
owner-scoped sessions, same live quotes, same target and handover arithmetic — and records what it
**would** have done. It calls no mutating IG endpoint. Tests assert that it stays silent even when a
position qualifies for action, which is the only reason to trust it.

Suggested sequence:

1. **Observe.** Set `LWR_OBSERVE_ONLY = True`, deploy, leave for a week of live trading. Every decision
   is logged as `let-winners-run WOULD …` with the position, the prices and the levels.
2. **Review.** Compare what it would have done against what the positions actually did. That is real
   evidence from your account, at zero risk.
3. **Then decide**, with two questions answered that cannot be answered today: does it fire when you
   expect, and are the levels right?

If you would rather not wait, the smaller step is to enable it live for **one** user with **one** open
position and a conservative trail, and watch a single cycle.

---

## 6. What I need from you

Only this:

- [ ] **Observe, go live, or leave it off?** My recommendation is observe.
- [ ] If observing: confirm I may deploy with `LWR_OBSERVE_ONLY = True`. It cannot place, amend or close
      anything, so this is a low-risk change — but it is your account and your call.
- [ ] Before any live enablement: a decision on the unmanaged-window risk in §2. The options are to accept
      it, to shorten the bridge interval, or to add a watchdog that alerts when positions are open with no
      management pass in N hours. **I would want the watchdog first**, and I can build it independently.

Nothing here is urgent. The feature is off, the historical report still works, and the current behaviour —
closing at target — is the safe default.
