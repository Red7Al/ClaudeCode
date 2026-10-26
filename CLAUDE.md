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
- **Record his decisions in the repository the moment he makes them — unprompted** (owner, 2026-10-21).
  An architectural or standing rule goes into `CLAUDE.md`; what was decided and its state into
  `docs/STATUS.md`; the work into a `ChangeRequests/` line. Then commit, push and deploy in the same turn.
  Agent memory is per-machine and is never the only record of an owner decision.
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
./.venv/Scripts/python.exe -m pytest -q -m "not live_state"     # 1,457 passed, 17 skipped -- clean worktree, CI env, 2026-10-08
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

**Verify what the owner sees yourself — never ask him to check.** `./.venv/Scripts/python.exe
verify_live_report.py --login Alex` fetches his exact live payload (`/api/report-check`, key `REPORT_CHECK_KEY`)
and serves the deployed page code locally on port 5062; open it in the browser and read the Scanner Report.
It refuses to run if the live build differs from your checkout, and refuses every write.

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
- **Read files, not the database — owner architectural decision, 2026-10-21.** Price data changes only at
  the twice-daily refresh (03:30 Morning Chain, 18:30 scan), so it is read from the database ONCE per refresh
  into a file, and every later step, job and the website reads that file. Only two things read the
  database: the step that builds the file, and live trading state (working orders, positions, IG login
  pause, sent-email record, user settings). A file is used only when its price-data version matches the
  database's; otherwise fall back to the database. A step moves only when its output from the file is
  identical, row for row, to its output from the database. New code that reads `price_history` or any
  refresh-derived table outside the file builder needs the owner's approval. **The files must be proven
  correct or they are useless** (owner, 2026-10-21): every section of every build is compared with the
  database, and a file that fails, or whose price data has changed since it was built, is not used. Egress was 4.84x the free
  limit when this was decided; progress is in `docs/STATUS.md`. **Owner 2026-10-23 (egress 4.59x): "move this live asap"** -- every job
  that has the file reads it (`PRICE_BARS=on`); new readers still go through shadow first.
- **Scanner output and data never go to GitHub — owner, 2026-10-21.** The repository is PUBLIC. No
  workflow may save scanner output, summaries, price data, database backups or secrets as a GitHub
  artifact: an artifact's listing is public (measured, HTTP 200 anonymously) and, per GitHub's
  documentation, any signed-in GitHub user can download it. Files live on IONOS; jobs fetch them over SSH.
  **Git is for code, never operational files** (owner, 2026-10-21: "stopping use of git for operational
  files is very important"): no workflow commits or pushes to the repository, and no output, report, query
  result or data file is tracked (`test_git_is_for_code.py`).
- **numpy is fatal on the IONOS host** (SIGSYS, uncatchable, shows as HTTP 500 after ~120 s). Compute in
  Actions, store it, have the web tier read it — see `skills_src/ah-data-sources`.
- **Never "optimise" `_winLedger`'s sort** — the replay compounds; `localeCompare` → `<` moved the wallet
  by £1,037.
- **The weekly backup does not dump `price_history`'s rows** (owner, 2026-10-23: "yes") -- its definition only. The
  rows' backup is the verified price bars file on IONOS (`/home/www/data/price_bars`). Most of every dump's egress.
- **Every Python package a job installs is pinned exactly in `constraints.txt`** (2026-10-26), applied by
  `requirements.txt` and by every direct `pip install` (`test_scramp_pin.py`). An unpinned upstream release --
  scramp 1.5.0, PyPI 2026-10-25 17:43Z -- broke every database login from 18:30 that day. Upgrade by changing
  `constraints.txt` and passing the suite, never by letting a job pick up "latest".
- **Secrets** live in Supabase `app_secrets`, GitHub and `.env`. Keep `.env` complete as a cold backup.
  The owner provides none in chat and does not expect to be asked.
