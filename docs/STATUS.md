# Status — the only status document

**Update this file at the end of every session** instead of writing a new handover. Every figure carries
the command that produced it or says it is unproven. Last updated **2026-10-20 (pushed 548b6cc)**.

Earlier handovers are in `docs/archive/HANDOVER-*.md`; everything still open from them is below.

---

## 1. Verify what the owner sees — yourself

`./.venv/Scripts/python.exe verify_live_report.py --login Alex` serves his exact live Scanner Report
locally (port 5062; Chrome refuses 5061). Last run 2026-10-11 on build `e254bae4d9b9`, snapshot
2026-10-10T19:58: **Scanner Report (29)**, tickers and order identical to `scanner_report_rows.js` over
his live payload. Never ask him to check.

`/api/report-check` answers for **his login only** (`_REPORT_CHECK_LOGIN`, 2026-10-11; any other login ->
403, proven live). Never widen it to other logins' data -- ninth complaint. The IG Account tab in the
viewer reads his IG account from THIS machine's IG session: each full load pulls 365 days of history,
and 8 loads in 2 minutes made IG refuse with 403 for ~20 minutes (2026-10-11 17:34 UTC). Load it once.

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

## 2a. Done 2026-10-10/11

| What | Evidence |
|---|---|
| **Scanner Report count = screen**: server applies the viewer's market switches (42 -> 29, all 13 in his switched-off markets). Default order Days since ascending, blanks last. | `ff91938`, `6ab7998`; live payload 29 rows |
| **IG Account "Open over time"** tab: open + closed positions as bars, P&L by the name, named periods, sort by P&L figure; shows the closed-trades note when IG refuses history. | `a76c66f`..`ec88ee8`; verified on deployed page code with his IG data: 1 open + 5,853 closed; 30d = 1 open / 28 closed. CR P-06 Completed |
| **`/api/report-check` limited to the owner's login** (ninth complaint). | `f02423a`; live: other login 403, Alex 200 |
| **IG refusals log IG's errorCode** (was a bare "403 Forbidden"). | `bb1ac19` |
| **Emailed Scanner Report = the screen**: server handlers + the page's own `pass()` via `scanner_report_rows.js`; `build_rows`/`user_rows` removed. | `93ff71f`; old 18 rows vs screen 29 vs new 29 (same order); live run 38165775061: "Scanner report emailed (29 setups)" |
| **Fundamentals precompute**: a rate-limit refusal is not an attempt, missing records first, stop after 5 refusals. | `bd2380b`; run 38309501875 (10-12 21:15): "1705 stored in all" (was 861), no rate-limit refusal. CR Completed |

## 2b. Done 2026-10-12/13

| What | Evidence |
|---|---|
| **Email: Triggered column, newest first**; **Status column removed** (22/22 rows TRIGGERED). | `f20f740`, `ff4a8e8`; 10-13 rows rendered unsent: 7 columns. Confirm on the 10-14 send (CR In Progress) |
| **"Open over time": hide P&L within +/-X; Total per currency; % of the account balance at the period start.** Start balance = IG balance now minus every IG transaction since that day (all 6,260 sum to 588.90 = the balance). Cash balance only. | `88d49ac`, `67486b1`, `4d9e3d8`; live page, his data: 30d +58.78 = +10.97% of 535.73 (09-13); this month +4.21% of 565.41; 12m "no %" (balance 0 before 2026-01-24). CRs Completed |
| **Price refresh: previous-day bar counted per market, retried once** (`Ticker.history(period="5d")`, completed days only), counts in `price_audit_log.notes`; judged per fetch's own day (`450c780`, the evening run crosses midnight). | `2af38b6`, `450c780`. 10-13 03:31: missing 116/1407 (Nikkei 112 = Japan holiday) -- the ~600 UK/Euronext gap did not occur that day |
| **Refresh truncation cause: IG allowance refusal never stopped the cross-check.** Since 10-09 IG answers 403 `exceeded-account-historical-data-allowance` (1,103x on 10-13) with no remaining count; ~3.2 s/instrument vs ~1 s; TRUNCATED 4 of 5 days. Now reads as 0 remaining. | `5cbae62`. Confirm on the 10-13 23:00 and 10-14 03:30 runs: ~1 s/instrument, not truncated |

## 2c. Done 2026-10-18

| What | Evidence |
|---|---|
| **TSCO.L 8 Oct trigger dropped by a bad Yahoo print.** 10 FTSE 100 highs on 10-15 (TSCO 622.5 vs 535.6 hourly) corrected; originals in `price_corrections`. | Read back 10/10. Live report: TSCO.L Open since 10-08. |
| **Finished trades stay on the report** with TARGET/STOPPED + date; email Outcome column; no order on a finished trade. | `77a3112`. Live: 67 rows (34 OPEN, 31 STOPPED, 2 TARGET); email re-sent 15:17 UTC. |
| **255 squeeze_history outcomes re-resolved** -- only where stored bars AND Yahoo hourly agree, no gaps/bad bars. 96 disagreeing rows left as they were. Backup `backups/report_outcomes_2026-10-18.json`. | Read back 255/255. |
| **Wick guard, London only** (`7d86cb1`): Yahoo hourly is a proven referee only there. 42 JP/AU bars the first version changed restored. | Read back 42/42. 6% threshold: catches every proven bad print in 30 days (measured), not proven beyond. |
| **Duplicate index dropped** (62 MB), source lookup rewritten (34.5 s -> 1.1 s, identical rows). | `8f88d33`; DB 450 -> 388 MB. |
| **Universe 1,773 -> 1,999** (+116 Nikkei, +95 ASX 200, +15 Hang Seng; published lists, Yahoo-verified). 4-year history backfilled. | `15e3346`; 221,974 bars for 226 tickers; DB 403 MB. First full scan: Morning Chain 10-19 -- check names/sectors and the report count. |

Dropped-row email (owner 2026-10-18: "Anything like items dropping out of any reports needs a full justification
sent out - via email") BUILT `8364b33`; see 2d for its first comparison (10-20). `instrument_metrics_daily` is never pruned (~5 MB/month);
2,250 instruments waits on a rule for it. API worker on IONOS still serves `51f3e3b17114` after the `7d86cb1`
deploy (static current); harmless -- the web tier never writes prices.

## 2d. Done 2026-10-18/20 -- IG logins

| What | Evidence |
|---|---|
| **Audits log in once per run**, not once per instrument. The 10-17 23:01 price audit sent 1,140 refused logins. | `8aa295a`. Live 10-19: DQA 22:15 and Price History Audit 23:01, 1 login each, 0 refused. |
| **Login retry only when the connection was never made**: up to 3 attempts, 30 s apart. A 4xx, a 5xx, a read timeout or a dropped answer is never resent. | `9665715` |
| **IG login pause.** After IG refuses a login (4xx on POST /session), no job logs in. One test login is made per 115 min; the first accepted one lifts the pause. One email at the start, one at the end. Held in `app_config` key `ig_login_pause:<account>` and in memory. While paused it raises IG's own 401 without contacting IG. | `88a19b2`. Cause: IG refused every login from between 10-16 20:50 (last Closing Window success) and 10-17 23:01 (first refused audit) until 10-19 18:25 (owner had IG restore the account). 10-19: Order Bridge 36 refused logins, Closing Window 77 failed runs x 3. A replay of 10-19's timings gives 7. **Not yet exercised live** -- confirm on the next refusal. |
| **Removed-rows email**: a failed comparison is stated ("unknown"), not counted as 1 removed, and the baseline is kept. | `a167e48` |
| **Test leaks closed** (conftest): IG, Resend and SMTP blocked in every test; the IG pause key and `scanner_report_sent` are in memory. | Live `scanner_report_sent` revision 4 before and after a test run. |

Removed-rows record VERIFIED live 2026-10-21: the 05:07 send stored 69 rows, `compared_with` 2026-10-20T05:22:03, removed 1 (OTIS, triggered 2026-07-22 -- 91 days, past the 90-day rule). Fake test entries "B" and "Silver" removed with the owner's yes 2026-10-21 (revision 5 -> 6, Alex entry byte-identical; neither is a login -- logins: Alex, Carl, Rich, Red7dp, KathrynH). The body itself stays out of the run log (anonymous API read of a job log: 403, measured 10-20; a signed-in stranger: untested).
Secret-decrypt warning: VERIFIED gone (10-13 run 4 warnings; 10-14, 10-20 none) -- CR Completed. Triggered
column / Status removed: the 10-20 send ran `88a19b2`, which contains both; delivered body unread. The order bridge and the Closing Window
still fail a run (red) while logins are paused; Cron Watch emails once.

## 3. Open, measured, not fixed

1. **Website pool use, now measured.** 2 web processes (40 concurrent `/api/report-check?process=1` calls:
   pid 6 x26, pid 19 x14; the second started under load). A 10-request burst opened 4 connections, peak 4
   open (Supavisor log, 2026-10-09 10:33) -- was 9-11. If the host adds processes, each brings 2 more.
2. **Egress.** Run `./.venv/Scripts/python.exe egress_report.py`; ~20 GB/30d was DERIVED, not measured,
   against 5 GB free. The owner declined Supabase Pro. Storage has returned 402 since 2026-08-16 as a result.
3. **`squeeze_history.refresh_daily` re-checks only an 18-month window.** Corrected one-off 2026-10-13 over
   stored bars: 26 of 205 resolved (11 TARGET, 15 STOPPED), 157 still OPEN, 22 older than bar retention
   untouched; originals in `backups/squeeze_history_stale_open_2026-10-13.json`. Readers: Best Settings
   (3y, 99 rows; win rate 25.69 -> 25.70%), Squeeze History tab, `_trade_drivers`. Rows still OPEN past
   the window will freeze again -- re-run the one-off rather than widen the window (~0.4 GB/month).
4. **"secret decrypt failed" in the email run -- fixed 2026-10-13, confirm on the 10-14 send.** Cause CORRECTED:
   the runner has no `WEB_USERS_FERNET_KEY` (not `APP_SECRET_KEY`, as first written), so `_fernet()` makes a
   random key. `/api/config` decrypted his IG credentials only for has_ig_creds/bridge, which the email never
   reads; `screen_rows` now skips that check. Real rows old vs new identical (22).
5. **Morning refresh misses the previous day's bar for ~600 UK/Euronext instruments** (every day 09-29..10-08,
   measured by `recorded_at`); the 10-07 "599" was this. Did not occur 10-13. Cause unproven; the new
   per-market counts in `price_audit_log.notes` measure it daily -- read several mornings before deciding.
6. **`trading-create-env.yml`** uses a `V_` prefix and does not pass `APP_SECRET_KEY`. Unanswered.
7. **NO INDEPENDENT PRICE CHECK HAS RUN SINCE 2026-10-09.** IG refuses price history (403
   `exceeded-account-historical-data-allowance`); every `price_audit_log` run since logs "IG fixes 0;
   double-checked 0" (runs 111-116 read 2026-10-18). Reported to the owner 10-13 only as a refresh-speed
   fault -- his complaint 2026-10-18. The Yahoo re-check compares Yahoo with Yahoo, closes only, so a bad
   Yahoo high/low passes it: 10 FTSE 100 highs on 2026-10-15 (TSCO.L 622.5 vs 535.6 real) closed TSCO.L's
   8 Oct trigger as TARGET. Since `77a3112` the store checks wicks against Yahoo's hourly bars. When IG
   verification resumes is unknown.

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

Ten, all upheld, in `ChangeRequests/20260918.txt`. The tenth (2026-10-18) carries the FIRST FORMAL WARNING
-- "don't make assumptions - this will lead to immediate termination": a loop count reported as rows written,
and an unmeasured 6% constant committed into the price guard. The ninth (2026-10-11): the agent designed a route that
would have exposed ANY login's IG positions and history to the check key, and got approval on a feature name
that hid it. Before proposing any tool, state whose data it reads and who can reach it. The common thread:
not reading my own output and code first.

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
10. `ChangeRequests/20260918.txt` mixes LF and CRLF lines: edit with `splitlines(keepends=True)`, never
    `split("
")` -- that merged lines and put a status on the wrong item (2026-10-12).
11. The verifier's red "not current" banner is computed from the LOCAL snapshot; the live `/api/freshness`
    banner was empty both times (2026-10-12/13). Check the live route before reporting it.
