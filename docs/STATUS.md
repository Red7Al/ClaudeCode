# Status — the only status document

**Update this file at the end of every session** instead of writing a new handover. Every figure carries
the command that produced it or says it is unproven. Last updated **2026-10-09 ~10:40 UTC**.

Earlier handovers are in `docs/archive/HANDOVER-*.md`; everything still open from them is below.

---

## 1. Verify what the owner sees — yourself

`./.venv/Scripts/python.exe verify_live_report.py --login Alex` serves his exact live Scanner Report
locally (port 5062; Chrome refuses 5061). Last run 2026-10-09 on build `cf0ed3e60eef`, snapshot
2026-10-09T05:59: **Scanner Report (28)**, 0 rows failing his filters on trigger values, 0 Days-since
errors, ARM absent. Never ask him to check.

## 2. Done 2026-10-08/09

| What | Evidence |
|---|---|
| **Failure emails.** 8 of 9 failures 10-06/08 were `EMAXCONNSESSION`; jobs gave up after ~9 s. Actions jobs now wait up to 120 s. | Live: five jobs rode out the 2026-10-08 14:30-14:31 burst ("pool full, waiting", all success). 235 runs since 14:25, 0 failures. |
| **Cause of the bursts: the website**, not the jobs. Supavisor log: 15/min at 06:29 and 12/min at 07:28 from 217.160.134.11 (IONOS outbound IP, read on the host). 10 simultaneous site requests opened 9 connections. | Web tier capped at 2 shared connections per process, idle closed after 10 s (`db_pool._web_get`). |
| **Scanner Report = open triggered setups judged on their trigger date, up to 90 days** (owner's rule and cap). RVOL/VWAP/ATR shown at trigger and now. Unvetted rows hidden in an outage. Days since in whole days. | `2b3a05b`. 50 rows recomputed from raw prices, 0 mismatches; 1,399 open setups, none past stop/target; 11 guards mutation-tested. |
| **Summary freeze reverted** before it changed the report (`ddd7c3b`). Per-trigger VolumeScore store kept and fixed (it had never held a row: duplicate key). | `abac9a4`, `29faef5`; `volscore_features` 15,175 rows. |
| **Supabase down → site still serves.** 0 HTTP 500s across 45 GET routes; logged-in records 200, triggered rows hidden with a stated reason. | `bc5ce54`, `2b3a05b` |
| Closing Window seed-write failure; Session Watchdog skips disabled sessions; `cryptography` in 6 workflows | `e7048ee`, `bc5ce54` |
| Docs 23 → 9; `docs/METHOD.md` test-checked against the code | `bc5ce54` |
| Live verification access: `/api/report-check` (key `REPORT_CHECK_KEY`) + `verify_live_report.py` | `fb89f39`, `7e88d4c` |

## 3. Open, measured, not fixed

1. **Website pool use, now measured.** 2 web processes (40 concurrent `/api/report-check?process=1` calls:
   pid 6 x26, pid 19 x14; the second started under load). A 10-request burst opened 4 connections, peak 4
   open (Supavisor log, 2026-10-09 10:33) -- was 9-11. If the host adds processes, each brings 2 more.
2. **Egress.** Run `./.venv/Scripts/python.exe egress_report.py`; ~20 GB/30d was DERIVED, not measured,
   against 5 GB free. The owner declined Supabase Pro. Storage has returned 402 since 2026-08-16 as a result.
3. **`squeeze_history.refresh_daily` re-checks only an 18-month window** (`squeeze_history.py:489`); 205
   older rows keep a stale `OPEN`. The report ignores them (refreshed within 4 days only); other readers
   of `outcome` have not been checked.
4. **The emailed Scanner Report** (`run_scanner_report_email.py`) still uses the old logic, and it IS sent every morning (Morning Chain job 3; corrected 2026-10-09 -- an earlier "not scheduled" was wrong). Sent to the
   owner at 06:01:58 on 2026-10-09. Align it with the web report after the summary-file work.
5. **The 2026-10-07 03:31 refresh skipped 599 instruments** while reporting success. Cause unknown.
6. **`trading-create-env.yml`** uses a `V_` prefix and does not pass `APP_SECRET_KEY`. Unanswered.

Closed by the owner: alerts — "I have had alerts from email anyway"; GitHub emails every failed run.

## 4. Owner rules settled this session — do not reopen

- Judge RVOL, VolumeScore, VWAP and ATR **at the trigger date**; a setup stays until target or stop,
  **up to 90 days**.
- **My Pre-orders is not part of the delivery**; he acts only from the trigger date.
- **AUS/UK/US Open stay disabled.** Alerts are email; Slack is not used.
- No unverified figure is ever shown — not even with "disregard".

## 5. Waiting on an owner decision (detail in `docs/archive/`)

| Decision | Detail | Recommendation |
|---|---|---|
| Let Winners Run: observe, live, or off | `archive/LET_WINNERS_RUN_DECISION.md` | observe first |
| Account isolation | `archive/ACCOUNT_ISOLATION_ARCHITECTURE.md` | finding 6 (revocation does nothing) is a five-line fix |
| Commodities trade but are hidden; any login can flip the bridge | `BACKLOG.md` Trading safety, Security | a rule from him, then a small fix |
| Security headers / sessions | `archive/SECURITY_RECOMMENDATIONS.md` | headers in `.htaccess` first |

## 6. Complaints on record — read before speaking

Seven, all upheld, in `ChangeRequests/20260918.txt`. The seventh (2026-10-09): an unverified recompute was
shown and then dismissed with "disregard". The common thread: not reading my own output and code first.

## 7. Traps that cost time

1. The local suite is not CI — clean worktree, placeholder env (see `CLAUDE.md`).
2. `monkeypatch.setattr(module, "get_db", …)` does nothing against a function-local import.
3. `git stash push` can fail silently (untracked files, "not uptodate"); check `git stash list` every time.
4. pg8000 parameters in date arithmetic need a cast: `current_date - :n` fails ("date >= integer").
5. `volume_score` bars are `(bar_date, high, low, close, volume)` — read the contract before calling.
6. Chrome refuses port 5061. IONOS keeps the old module after a deploy: wait for `/api/build`.
7. Workflow names carry their creation date; GitHub failure emails put it in the subject.
8. `.claude/worktrees/` shadows greps — exclude `.claude` from every sweep.
9. Windows console is cp1252 — print ASCII in any probe.
