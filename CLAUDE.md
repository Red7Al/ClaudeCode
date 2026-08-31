# EndToEndTrading — working notes

Everything here was verified on 2026-08-25 by running it, not by reading about it. Where a fact is
inferred rather than measured, it says so.

## What this is

A squeeze/HVF continuation scanner and trading system. It scans ~1,773 instruments, publishes a snapshot,
serves a Flask web app at <https://www.squeezescanner.cloud>, and can place orders against IG.

- **The method** — `docs/SQUEEZE_METHOD.md` (detection rules and thresholds), `docs/DECISIONS_AND_WEIGHTING.md`
  (how a detected setup is ordered, gated and published).
- **Operating it** — `docs/OPS_RUNBOOK.md`. Read §2 before your first deploy.
- **Deployment detail** — `IONOS_DEPLOYMENT.md`.
- **Working rules** — `AGENTS.md`. Short, and the destructive-change rule is not optional.

## Running the tests

```bash
./.venv/Scripts/python.exe -m pytest -q -m "not live_state"     # 466 pass, 2026-08-25
```

Three traps, all of which have bitten:

1. **Use the venv.** The bare `python` on this machine is a system 3.14 with no pytest.
2. **Never pipe pytest to `tail` and trust the exit code** — the code comes from `tail`, so a failing
   suite reports success. Check `$?` on pytest itself.
3. **`-m "not live_state"`** deselects tests needing live runtime state (the Supabase user store, a built
   `hvf_web/snapshot.json`). CI also needs placeholder env vars; see `.github/workflows/trading-hvf-tests.yml`.

The two harnesses that catch what source-reading cannot:

- `test_js_behaviour.py` — **executes** extracted client JavaScript in Node and asserts on the result.
  Most other client-side checks only assert on source text, which is how a real bug once shipped green.
- `test_backtest_integrity.py` — truncation invariance, exit-within-traded-range, perfect-foresight
  bound, trail monotonicity, target floor. Run it against any new replay before quoting its number.

## Committing

```bash
PATH="/c/Users/eahin/AppData/Roaming/Python/Python314/Scripts:$PATH" git commit -F msg.txt
```

`pre-commit` is installed but **not on PATH**; without that prefix the hook aborts. The hook runs gitleaks
always, and the HVF regression suite only when `price_action.py`, `config.py` or `test_hvf_method.py`
changed.

**Commit in the foreground, and never edit files while a commit is in flight.** pre-commit stashes
unstaged changes and resets the tree; concurrent edits make the restore conflict and it discards unstaged
work it did not stash. This destroyed uncommitted user edits once. The repo is under OneDrive, so OneDrive
version history is the only recovery path.

## Deploying

```bash
ASSUME_YES=1 ./deploy_ionos.sh
```

**A push never updates the website.** Deploy is a separate step.

**Static files always update; the Python API often does not.** IONOS is shared hosting and keeps the
Flask module resident behind a CGI wrapper with no way to restart it. A deploy can place every correct
file on the host and leave `/api/*` answering from a module loaded hours earlier. The script compares
`/api/build` against the fingerprint it shipped and warns on a mismatch — a warning, not a failure, since
the static release *is* live. Always check `/api/build` before believing a `server.py` change took effect.

## Scheduling

`setup_cronjobs.py::JOBS` is the authoritative registry; cron-job.org fires `workflow_dispatch`.
**GitHub-native `schedule:` blocks are banned** — never add one.

## Change control

`ChangeRequests/*.txt` is the live worklist and the admin **Change Requests** tab parses it, so the file
is the record the user watches. Full process in `skills_src/ah-change-control/SKILL.md`.

- Mark an item `In Progress` the moment you pick it up, `Completed` only when verified. Never batch.
- Two formats. Numbered blocks use a `Status:` line. `* P-nn …` lines carry a **trailing** marker, and
  `_CR_TAIL` in `hvf_web/server.py` is **end-anchored** — a marker anywhere but last reads as Not Started.
- **Validate with the real parser** (`hvf_web.server._cr_parse`), never a `"[Completed]" in line` check.
- These files are **deliberately not tracked in git** (`.gitignore`). They reach the site through the
  deploy, which selects files by walking the working directory. Do not add them to git.

## The defect this repository keeps producing

**Correct, tested code that nothing ever calls.** Eight instances were found in one week: a sector
backfill that was never scheduled, a deep price-history backfill nobody ran, a Scanner Report email built
on request that has never executed once, a market-cap backfill run exactly once on 2026-08-01 and never
since (463 of 1,728 tickers still have no market cap, measured 2026-08-25).

A green test proves the function works, not that anything calls it. When you add or review anything with
an entry point, ask *what invokes this, and how would I know if it stopped?* — then check the effect in
live data, not the code path.

To sweep: list every script with a `__main__` block and grep the workflows, `setup_cronjobs.py::JOBS`,
shell scripts and cross-imports for each name. Several are legitimately manual — one-off migrations,
on-demand tools, and `run_price_history_prune.py`, whose VACUUM **must never be scheduled**.

## Reporting standards

The engagement before this one ended over unverified claims, so this is not decoration:

- State what was **measured** and what was **inferred**, in the same breath. Most wrong claims are
  inferences delivered in the voice of measurements.
- Never use a summary word broader than what you tested.
- If the user describes what they **see**, verify what renders — not what the source says.
- If a change moves a number the user reads off a report, measure old vs new and tell them *before*
  shipping.
- Browser timings taken in a **background tab are worthless** — Chrome deprioritises the renderer there.
  Confirm `document.visibilityState === "visible"` before and after any measurement, and discard the rest.
- When evidence contradicts the user's premise, stop and say so. Do not act and footnote it.

## Constraints worth knowing before you change anything

- **`WEB_BRIDGE` is the only enabled execution source** (session monitors are off). Treat bridge changes
  as production trading changes.
- **Never "optimise" `_winLedger`'s sort.** `localeCompare` → `<` is 11% faster and moved the reported
  wallet by £1,037: the replay compounds, so the order *is* the answer.
- **Supabase is on the 500 MB free tier** (399 MB at 2026-08-23). A `DELETE` frees nothing measurable.
- **A green Scanner Snapshot Publish run does not mean Supabase was published.** The publish step is
  `continue-on-error`, so its *conclusion* reads success while `outcome` holds the failure; the run
  passes if the IONOS fallback alone worked. From 2026-08-16 that hid a two-week publication outage,
  and on 2026-08-31 a worker restart pulled the stale remote over the host's only newer copy and the
  live site lost 352 instruments. `load_snapshot` now refuses a remote snapshot older than the local
  one. Ask `scanner_snapshot_store.current_metadata()` for the real state, never the run status.
  Full detail and the restore procedure: `docs/OPS_RUNBOOK.md` §4.
- **Secrets** live in Supabase and `.env`. Keep `.env` complete as a cold backup; do not prune it.
