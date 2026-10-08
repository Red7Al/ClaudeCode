# Status — the only status document

**Update this file at the end of every session** instead of writing a new handover. Every figure carries
the command that produced it or says it is unproven. Last updated **2026-10-08 ~09:30 UTC**.

Earlier handovers are in `docs/archive/HANDOVER-*.md`; everything still open from them is below.

---

## 1. DECIDE BEFORE 18:30 UTC TODAY — the Scanner Report's row set will change

`ddd7c3b` + `44a940e` are deployed. At the next snapshot publish they move `above_vwap` and
`atr_expanding` from computed-per-request to frozen-at-publish. Those two values gate visibility in
`hvf_web/app.js:424-425`, where `false` HIDES a row, and the owner has both `require_` filters on.

- Not yet active: `/api/status` still reports `generated_utc 2026-10-08T05:11:49`, the snapshot measured
  without the `summary_fields` marker at 07:33.
- Next publish: **Scanner Snapshot Refresh, `30 18 * * *`** (`setup_cronjobs.py:197`).
- MEASURED on the live payload 2026-10-08, 407 `has_signal` rows: `atr_expanding` false on 245,
  `above_vwap` false on 96.
- Back out: `git revert 44a940e ddd7c3b`, commit, deploy. Nothing else depends on them.

## 2. The owner's open complaint — the Scanner Report is not trusted

*"the scanner report from today is different to the report from yesterday - I'd expect to see yesterdays
contents in todays report also - with a different #days as triggered."* He is right. With his limits, 407
signal rows reduce to 16; 80 exclusions come from `atr_expanding`/`above_vwap` recomputed from the latest
bar, so a row vanishes while the squeeze is unchanged. **The fix he wants, not built:** a persistent squeeze
population with the day count incrementing, and the live measures shown as columns rather than hidden
filters. The web report (`app.js:639`, under `<h1>Scanner Report</h1>` at `index.html:719`) and the emailed
report (`run_scanner_report_email.py`) are **different surfaces** — do not reason about one from the other.

## 3. Live state (measured 08:03 UTC)

```
/api/build      fingerprint 5d1035ad279c, module_loaded_at 2026-10-08T08:03:46Z
/api/status     count 1773, generated_utc 2026-10-08T05:11:49
/api/freshness  banner "", stale ["supabase_snapshot_copy"]
CI              2f12a41 green (Offline Python Regression Tests, 07:54 UTC)
```

## 4. Done 2026-10-08 (this session)

| What | Evidence |
|---|---|
| **Failure emails.** 8 of 9 failed runs since 10-06 were `EMAXCONNSESSION` (15-slot pool full); jobs gave up after ~9 s. Actions jobs now wait up to 120 s for a slot; web tier unchanged. | `db_pool._connect`; 4 tests in `test_db_pool.py`, 2 proven red on the old code |
| **`cryptography` missing** in 6 workflows that pass `APP_SECRET_KEY` — the watchdog log showed `No module named 'cryptography'` | `trading-watchdog.yml` and 5 others |
| **Supabase-down fallback.** Probe: real app, unreachable credentials, logged out, all 45 GET routes. `/api/pricebars` returned 500 → now 200 with levels and `unavailable: true`. A web process that fails to connect now skips the DB for 30 s, so `/api/freshness` went 40.3 s → 0.2 s and `/api/records` serves the local snapshot in 0.2 s. After: **0 HTTP 500s, 0 exceptions.** | `db_pool.DatabaseUnavailable`; `test_price_bars.py`, `test_db_pool.py` |
| Docs reduced from 23 to 9 outside `skills_src/`; `SQUEEZE_METHOD.md` was wrong (stated 0.70 convergence and the removed AMP1 re-anchor) — replaced by `docs/METHOD.md`, now test-checked for the tightness ceiling too | `test_hvf_method.py::test_method_doc_states_the_live_tightness_ceiling` |

**Deployed** as `bc5ce54`, live fingerprint `77c33a785809` (worker loaded 08:38:02). Verified live:
`/api/pricebars/AAPL` returns real bars with `"unavailable":false`; watchdog run `37751392722` on `bc5ce54`
logged "loaded 10 secret(s)" where run `37750316715` on the old code logged "No module named
'cryptography'". During the reload, 08:38:01–08:38:25, some API calls got no HTTP response (curl 000);
30 of 30 calls afterwards returned 200. Whether earlier deploys show the same blip is unmeasured.

NOT measured: the **logged-in** experience during an outage — login validates tokens against the user
store, which reads the local copy when Supabase fails (`web_users._load`), but nobody has exercised it.

## 5. Open, measured, not fixed

1. **Who fills the 15-slot pool is unknown.** At 06:30 only 2 Actions jobs ran, yet the pool was full, so
   concurrent jobs alone do not explain it. Pooled backends all report `application_name = Supavisor`, so
   the holder cannot be read back afterwards — it must be sampled while it happens. Jobs now ride out a
   burst of up to 120 s; a longer one still fails.
2. **Egress.** Baseline reset 2026-10-07T19:44:28Z. Run `./.venv/Scripts/python.exe egress_report.py`
   after a full day. DERIVED, not measured: ~20 GB/30d against a 5 GB free allowance. The owner declined
   Supabase Pro.
3. **Supabase Storage returns 402** since 2026-08-16 (`exceed_egress_quota`, latched). The snapshot
   publishes to IONOS instead. Unlatches only if egress is fixed or the plan changes.
4. **The 2026-10-07 03:31 refresh skipped 599 instruments** while reporting success; a 14:02 re-run stored
   them. Cause unknown; the job's status does not detect it.
5. **`INXG.L`** is a DEVELOPING row whose latest bar is 2026-10-05; eight other instruments lack the 10-06
   bar (none carries a signal).
6. **No working failure notification for CI** — its only alert posts to `SLACK_ALERTS`, and Slack is not in
   use. CI sat red nine days unnoticed. (`run_cron_watch._notify` does email.)
7. **`trading-create-env.yml`** uses a `V_` prefix and does not pass `APP_SECRET_KEY`. Unanswered whether
   it should.
8. **Session Watchdog alerts every run** — `No macro_snapshot recorded today` for AUS_OPEN/UK_OPEN, then
   `Auto-trigger failed: 403 Resource not accessible by integration`. 11 of the 12 runs from 07:00 to
   08:40 on 2026-10-08. Each run still ends green, and `alert()` posts only to Slack
   (`run_session_watchdog.py:75-83`), so it does not email. Probable cause, unverified: the session
   monitors are disabled while the watchdog still expects them.
9. **The owner's standing instruction** — store derived values on IONOS, do not re-derive per request
   (archive `HANDOVER-20260928.md` §3.1). `ddd7c3b`/`44a940e` are a first part, gated by §1.

## 6. Waiting on an owner decision (design detail kept in `docs/archive/`)

| Decision | Detail | Recommendation |
|---|---|---|
| Revert or keep the summary freeze | §1 | decide before 18:30 UTC |
| Let Winners Run: observe, live, or off | `archive/LET_WINNERS_RUN_DECISION.md` | observe first; unmanaged-window watchdog before any live use |
| Account isolation | `archive/ACCOUNT_ISOLATION_ARCHITECTURE.md` | finding 6 (revocation silently does nothing) is a five-line fix that should not wait |
| Commodities trade but are hidden from his view; any login can flip the bridge | `BACKLOG.md` — Trading safety, Security | both need a rule from him, then a small fix |
| Security recommendations (headers, sessions) | `archive/SECURITY_RECOMMENDATIONS.md` | headers in `.htaccess` first |

## 7. Complaints on record — read before speaking

Six, all upheld, in `ChangeRequests/20260918.txt`: unverified claims; decisions offloaded as repeated
multiple-choice questions; a standing instruction left unbuilt while self-found faults were worked; a
financially misleading answer about token billing; and *"you do not check your work and I have had
enough"*. The common thread: not reading my own output and code before speaking.

## 8. Traps that cost time

1. The local suite is not CI — use a clean worktree with the placeholder env (see `CLAUDE.md`).
2. `monkeypatch.setattr(module, "get_db", …)` does nothing against a function-local import.
3. `git stash push -- <untracked file>` stashes nothing; check `git stash list` after.
4. Workflow names carry their creation date; GitHub failure emails put it in the subject. It is not the
   failure date.
5. `.claude/worktrees/` shadows greps with stale copies — exclude `.claude` from every sweep.
6. `/api/records` auth is an `X-Auth` header from `localStorage.sq_auth`, not a cookie.
7. Windows console is cp1252 — print ASCII in any probe or you crash on the first `�`.
