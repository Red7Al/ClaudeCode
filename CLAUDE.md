# EndToEndTrading — start here

A squeeze (HVF) continuation scanner and trading system. It scans 1,773 instruments, publishes a snapshot,
serves a Flask web app at <https://www.squeezescanner.cloud>, and places orders on IG.

## Read in this order

1. **This file** — the rules. None of them is optional; each exists because skipping it cost real money or
   time.
2. **`docs/STATUS.md`** — what is live, what is broken, what the owner is owed. The only status document;
   update it at the end of every session instead of writing a new handover.
3. Then only what the task needs:

| Task | Read |
|---|---|
| Detection, thresholds, ranking, orders | `docs/METHOD.md` |
| Any numeric filter, RVOL, a Best Settings figure | `docs/ORDER_TIMING_AND_RVOL.md` |
| Deploying, health checks, failed jobs, backfills, email, DB size | `docs/OPS_RUNBOOK.md` |
| The IONOS host itself | `IONOS_DEPLOYMENT.md` |
| Deferred ideas | `BACKLOG.md` (the LIVE worklist is `ChangeRequests/`, below) |
| A specific procedure | `skills_src/ah-*/SKILL.md` — tables (`ah-data-dictionary`), where a value comes from (`ah-data-sources`), web layout (`ah-web-formatting`), change control, deploy, X publications |

`docs/archive/` holds superseded documents. They are history, not instructions — do not act on them.

## How the owner works — read before you speak

The engagement before this one ended over unverified claims, and six complaints are upheld in
`ChangeRequests/20260918.txt`. The common thread: **not reading my own output and my own code before
speaking.** Every one had a single cheap command that would have prevented it.

- **Proof for every statement.** Attach the command and its result. Label MEASURED / DERIVED / unproven.
  "Do not infer, assume, presume or guess" (owner) — if it is measurable, measure it.
- **Never type an identifier you have not read from output** — run ID, deal ID, table, line number.
- **Code is not evidence about the live system.** A declared migration is intent; only the database says
  whether it ran. If the owner describes what he SEES, verify what renders.
- **Check the apparatus before trusting the measurement** — the exit code is pytest's, not `tail`'s; the
  stash actually happened; the probe's conditions match production.
- **A correction needs the same proof as the claim it corrects.**
- **When evidence contradicts the owner's premise, stop and say so.** Do not act and footnote it.
- **Brevity.** The answer and the one number behind it. No menus of options — one recommendation.
- **Own the work.** Options and a recommendation come from you. "I can't" needs evidence: check `~/.ssh`,
  `gh`, `.ionos.env`, the cron-job.org API before handing anything back. The owner deploys nothing.
- **Commit anything successfully tested.** Do not leave verified work waiting to be offered.
- **If a change moves a number he reads, measure old vs new and tell him BEFORE shipping.**
- **His outstanding instructions are the queue.** Faults you find yourself are parked unless they block
  them.

## Before you write code

Each rule exists because skipping it produced a real defect in September 2026.

1. **Enumerate before touching shared state.** Grep every reader and writer of the table/column/file
   (`update <t>`, `insert into <t>`) and say what you found. "Absent from IG's working-order book" once
   meant three things in three modules; the sweep expired 85 rows, 31 of them live. Only
   `working_order_state.classify` may interpret it now.
2. **Profile the data first.** `working_orders.deal_id` is NULL on 45% of rows and `good_till` on 39% —
   never key on either; `id` is the key.
3. **Fixtures come from the table, not from memory.** An invented fixture certifies the assumption.
4. **Prove every new test fails against the old code** — stash the fix, watch it go red, restore. A
   surviving mutation means the test is wrong.
5. **Run it and assert the effect** — rows, rendered screen, account — not the exit code.
6. **Destructive changes need explicit confirmation.** State exactly what will be removed and wait.
   Preserve original information in a compatible field unless removal is approved.
7. **Operational work is complete only at the live consumer** — build, publish, install, verify the
   generated timestamp. A green intermediate step or a fallback artifact is not completion.

## The two defects this repository keeps producing

- **Correct, tested code that nothing calls.** A green test proves the function works, not that anything
  invokes it. Ask *what calls this, and how would I know if it stopped?* — then check the effect in live
  data. Sweep: every `__main__` script against the workflows, `setup_cronjobs.py::JOBS`, shell scripts
  and imports. Legitimately manual: one-off migrations, `egress_report.py`, and
  `run_price_history_prune.py`, whose VACUUM **must never be scheduled**.
- **One fact, several pieces of code each deciding what it means.** Before inferring what a shared column
  means, check whether something already infers it.

## Running the tests

```bash
./.venv/Scripts/python.exe -m pytest -q -m "not live_state"     # 1,455 passed, 17 skipped -- clean worktree, CI env, 2026-10-08
```

- **Use the venv.** Bare `python` is a system 3.14 with no pytest.
- **Never read pytest's exit code through a pipe.** Use `${PIPESTATUS[0]}`.
- **The local suite is not CI.** Locally there is a `.env` and a `hvf_web/snapshot.json`; CI has neither.
  Before pushing, run in a clean worktree with the placeholder env vars from
  `.github/workflows/trading-hvf-tests.yml`. A single test file passing proves nothing about import-order
  bugs.
- **`monkeypatch.setattr(module, "get_db", …)` does nothing** when the target does `from db_pool import
  get_db` inside the function. Patch `"db_pool.get_db"`.
- `test_js_behaviour.py` EXECUTES client JavaScript in Node; `test_backtest_integrity.py` checks any replay
  before you quote its number.

## Committing

```bash
PATH="/c/Users/eahin/AppData/Roaming/Python/Python314/Scripts:$PATH" git commit -F msg.txt
```

`pre-commit` is not on PATH without that prefix. **Commit in the foreground and never edit files while a
commit is in flight** — pre-commit stashes and restores the tree, and has destroyed uncommitted owner
edits. The repo is under OneDrive; its version history is the only recovery path. `a.claude.tmp`,
`msg*.txt` and `.codegraph/` are scratch — never commit or mention them.

## Deploying

```bash
ASSUME_YES=1 ./deploy_ionos.sh
curl -s https://www.squeezescanner.cloud/api/build
```

**A push never updates the website.** Static files always update on deploy; the Python API often does not —
IONOS keeps the Flask module resident and nothing can restart it. **Commit before deploying**: the
fingerprint is derived from HEAD, so an uncommitted `server.py` change reports "API worker is current"
while the old module runs. Confirm the changed behaviour itself, not the fingerprint line.

## Scheduling

`setup_cronjobs.py::JOBS` is the registry; cron-job.org fires `workflow_dispatch`. **GitHub-native
`schedule:` blocks are banned.** You deploy cron changes yourself (`trading-setup-cronjobs.yml`). When a
workflow file changes, put today's date in its `name:`.

## Change control

`ChangeRequests/*.txt` is the live worklist; the admin Change Requests tab parses it. These files are
**deliberately untracked** and reach the site by deploy — never add them to git.

- Mark an item `[In Progress]` the moment you pick it up, `[Completed]` only when verified. Never batch.
- The marker must be the **last token** on the line — `_CR_TAIL` is end-anchored; anywhere else reads as
  Not Started. Validate with `hvf_web.server._cr_status`, never a substring check.

## Constraints

- **`WEB_BRIDGE` is the only enabled execution source.** Bridge changes are production trading changes.
- **AUS Open, UK Open and US Open stay DISABLED on cron-job.org — owner decision, settled 2026-10-08.**
  Last runs 2026-08-06. Do not re-enable them, do not "fix" anything that reports them missing, and do not
  raise them again. Session Watchdog skips any session whose cron-job.org job is disabled.
- **Alerts go by EMAIL. Slack is no longer used** (owner, 2026-10-08). An alert that only posts to a
  `SLACK_*` webhook reaches nobody. `run_cron_watch._notify` emails.
- **Supabase free tier**: 500 MB database, 5 GB/month egress, **15-client session pool**. A `DELETE`
  frees nothing. Storage has returned 402 since 2026-08-16 (egress quota); the snapshot publishes to
  IONOS instead.
- **If Supabase refuses reads or writes, the site must keep serving from IONOS without an error** (owner,
  2026-10-08). The web tier fails fast on a DB error so it can fall back; only GitHub Actions jobs wait
  for a free pool slot (`db_pool._pool_full_patience`).
- **A green Scanner Snapshot Publish run does not mean Supabase was published** — the step is
  `continue-on-error`. Ask `scanner_snapshot_store.current_metadata()`. Restore procedure:
  `docs/OPS_RUNBOOK.md` §4.
- **numpy is fatal on the IONOS host** (SIGSYS, uncatchable, shows as HTTP 500 after ~120 s). Compute in
  Actions, store it, have the web tier read it — see `skills_src/ah-data-sources`.
- **Never "optimise" `_winLedger`'s sort** — the replay compounds; `localeCompare` → `<` moved the wallet
  by £1,037.
- **Secrets** live in Supabase `app_secrets`, GitHub and `.env`. Keep `.env` complete as a cold backup.
  The owner provides none in chat and does not expect to be asked.
