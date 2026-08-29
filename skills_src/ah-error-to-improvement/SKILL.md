---
name: ah-error-to-improvement
description: Standing practice for this project — every mistake must produce a durable process change, not just a fix. Use immediately after any error is discovered (a wrong assumption, a broken feature, a misread instruction, a green test that missed a real bug, a deploy that did not take effect) to classify the cause and record the change that prevents recurrence. Also read when starting work, as the accumulated log is the cheapest available list of how this project actually goes wrong.
---

# Turning an error into an improvement

Instruction from the requester, 2026-08-29: *"Make a new skill for every time there is an error on your
part, work out how to improve testing or deployment or specification etc - whatever you need to improve
delivery."*

A fix repairs one instance. The point of this file is the second step: change the *method* so the class
cannot recur, and write it down where the next person inherits it.

## The routine

When an error surfaces:

1. **Say it plainly first.** Name what broke and what it cost, without softening. The requester found it;
   pretending otherwise wastes their time.
2. **Classify the cause** — specification, verification, testing, deployment, or reporting. The fix is
   different for each and picking the wrong class produces a useless remedy.
3. **Write the guard that would have caught it**, and prove it fails against the broken state before
   keeping it. A detector that has never failed is not evidence of anything.
4. **Append an entry below.** One entry per class, extended rather than duplicated.

## Log

### Specification — an ambiguous noun was resolved by guessing (2026-08-28/29)

*"Remove the evidence tab and row count if user not logged in"* was read as the Performance tab. It meant
the evidence **table** inside it. The whole tab was removed from `PUBLIC_TABS` and shipped; it came back
as *"I can no longer see the performance tab when logged out - this is a BUG"*. A second message,
*"skip the cards that cause an issue"*, was saying the same thing again and was also missed.

**Change:** when an instruction names a UI element, resolve the name against the actual UI before acting —
grep the markup for a tab, a panel, a heading with that word. If nothing matches exactly, the term is
ambiguous and the cheapest resolution is one question, not one deploy. And per the requester's own rule of
the same day: **confirm before removing anything user-facing.** Adding is recoverable; removing is what
gets reported back as a bug.

### Specification — options were extracted rather than offered (2026-08-28)

Covered in full by `ah-defect-response`. Summary: explaining a mechanism is not answering; finish with the
options and a recommendation, or the requester has to do the thinking.

### Verification — a guard passed while the bug was present (2026-08-28)

`test_no_gated_endpoint_is_fetched_without_the_token` was written so "the next gated endpoint cannot be
called bare". It then passed twice over a newly gated `/api/positions` that the client still called bare:
first because it detected a gate by the **wording** of the refusal (`"login required"`), then because it
read a **fixed 900-character window** after the route decorator, which a long docstring overflowed.

**Change:** detectors key on behaviour, never on message text; and they read whole structures, never a
fixed slice. Mutation-test every guard — flip the code back to broken and watch the test fail — before
trusting a pass.

### Testing — the harness silently limited what could be tested (2026-08-28)

`run_js` passed `text=True` to `subprocess` with no encoding, so Node's output was decoded with the
Windows locale codec. Any non-ASCII the client returned killed the harness with `UnicodeDecodeError`
instead of failing an assertion — and the client is full of such characters.

**Change:** encoding is always explicit at process boundaries. The same class bit three times in one day:
`setup_cronjobs` reported created jobs as failures because a `→` could not be printed, and a register
parse died on an hourglass emoji. On Windows, assume cp1252 and write ASCII in anything printed.

### Deployment — shipped is not live (ongoing)

IONOS keeps the Flask module resident, so a deploy places correct files on the host while `/api/*` answers
from an older build. A security fix sat inactive for hours this way.

**Change:** never report a server change as done from a successful deploy. Check `/api/build` against the
shipped fingerprint, and verify the *effect* — the endpoint's actual response — not the deploy's exit
code. Static files (`app.js`, `index.html`) do update immediately; server changes wait for a recycle.

### Reporting — a failing command reported success (2026-08-25)

`python -m pytest ... | tail` exited 0 while the suite had not run at all, because the exit code came from
`tail`. The bare `python` on this machine has no pytest.

**Change:** never pipe a command whose exit code matters. Capture to a file, check `$?` on the process
itself, and use the project venv explicitly.

### Verification — measurements taken under invalid conditions (2026-08-25)

Browser timings were taken in a background tab, where Chrome deprioritises the renderer. They were wrong
by an order of magnitude and were reported before being questioned.

**Change:** record the conditions a measurement requires, assert them before and after, and discard the
run if they were not met. State one clean measurement as one, rather than implying a series.

See also `ah-defect-response`, `ah-analysis-verification`, and the reporting standards in `CLAUDE.md`.
