---
name: ah-change-control
description: >
  How to work the ChangeRequests/*.txt lists that drive this project and the web app's
  "Change Requests" tab. Use whenever processing a ToDo / change-request file, marking an
  item [In Progress] or [Completed], or when the user says "none are showing as in progress",
  "you're not respecting change control", "update the status", or asks about the priority
  scheme (P-01…P-100). Encodes the ONE rule that silently breaks the tab: the status marker
  must be the LAST token on the line, because the parser's regex is end-anchored.
---

# AH Change Control — working the ChangeRequests lists

The `ChangeRequests/*.txt` files are the project's live worklist. The admin **Change Requests**
tab parses them (hvf_web/server.py) into per-requirement rows with a status, so the file IS the
source of truth the user watches. Respect the process below or the tab and the file disagree.

## 1. Status markers — placement is load-bearing

The parser (`_cr_status` in hvf_web/server.py) decides a line's status two ways:

- **Lead tag** at the very start: `[x]`/`[X]`=Completed, `[~]`=In Progress, `[-]`=Cancelled,
  `[?]`=Requested. Rarely used on these `* P-nn …` lines.
- **Tail marker** — the usual one — via `_CR_TAIL`, which is **END-ANCHORED**:

  ```
  \[(completed|in[\s-]?progress|not[\s-]?started|cancelled|canceled|requested|deferred)\]\s*(?:\([^)]*\)\s*)?$
  ```

  So the status is recognised **only when the line ENDS with `[Status]`**, optionally followed by
  ONE clean `(...)` group and nothing else. Anything else → the item silently reads **Not Started**.

**THE RULE: the `[Status]` marker must be the LAST token on the line.** Put the requirement text and
any note BEFORE it.

- ❌ WRONG — marker mid-line, note trailing (and note has nested `)`):
  `* P-02 [Completed] Do the thing (Claude: fixed server.py:394 (line noted) etc.)`
  → parses as **Not Started**. This is the mistake the user caught on 2026-07-24: a whole batch of
  `[Completed]`/`[In Progress]` sat right after the P-number, so every item showed as Not Started.
- ✅ RIGHT — note first, marker strictly last, no parentheses competing with the anchor:
  `* P-02 Do the thing -- Claude 2026-07-24: fixed server.py:394, JS fallback, panel text. [Completed]`

  Prefer `--` / `:` over parentheses in the note. If you must use a trailing `(...)`, it has to be a
  SINGLE group with no inner `)` and nothing after it.

Valid statuses: Completed, In Progress, Not Started (the default), Cancelled, Requested, Deferred.

## 2. Update LIVE, never batch (see memory [[cr-status-live]])

The user watches the tab and expects it to track what you're doing:

1. The **moment** you pick an item up — before touching any code — append ` [In Progress]` (at the
   line end). Do this even for a one-line fix.
2. When it is built AND verified, change it to ` [Completed]`.

One item at a time. A batch of `[Completed]` edits at the end means the tab shows nothing In Progress
the whole time work is happening — which reads as no progress and breaks trust.

## 3. Validate with the REAL parser, not a string check

Never assume `"[Completed]" in line` means it parsed. After editing, confirm each line resolves to the
intended status by replicating `_CR_TAIL` / `_cr_status` (or importing them) and asserting the result:

```python
import re
TAIL = re.compile(r"\[(completed|in[\s-]?progress|not[\s-]?started|cancelled|canceled|requested|deferred)\]\s*(?:\([^)]*\)\s*)?$", re.I)
# ...replicate _cr_status; assert cr_status(line) == "In Progress"
```

## 4. Priority scheme (guideline legend at the top of the file — NOT actions)

`P-01` bugs · `P-02` queries · `P-06` move/remove/change · `P-10` new functionality · `P-20` docs ·
`P-25` mobile. Lower number = higher priority. Items may carry a note like `[DEFERRED]` or an inline
`(Claude yyyy-mm-dd: …)` decision — read those before starting; some items are explicitly deferred or
blocked on external data.

## 5. Prioritisation flag & other columns (derived, don't hand-maintain)

- **Prioritised** = the line carries a `P-<n>` tag or sits under an "Explicitly prioritised work"
  heading (`_cr_prioritised`). Derived — nothing to keep in sync.
- **Working Area / Scope** come from the section headings and structure of the file.

## 6. Related

- Memory [[cr-status-live]] — the live-update discipline + this format rule (the short version).
- [[commit-workflow]] — commit foreground, never edit files mid-commit.
- Deploying scheduled/cron changes made against these lists → skill `ah-deploy` + memory
  [[deploy-cron-tasks]].
