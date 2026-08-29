# Backlog

Deferred items (not blocking). Add new items at the top of the relevant section.

## Trading safety

- [ ] **"Markets (User)" looks like a trading switch but is view-only — Commodities is tradeable AND
  invisible** (found 2026-07-17 while confirming FX). Three switches mention markets; only two gate the
  order path, and the names do not tell you which:
  | switch | stored as | enforced where |
  |---|---|---|
  | Markets (**User**) | `web_users.settings.markets_off` | **VIEW ONLY** — read solely by `/api/config` for that user's Scanner/Pre-orders. Never reaches the order path. |
  | Markets (**Admin**) | `app_config.markets_disabled` | order path (`config_store.trade_allowed`) |
  | Configuration → Trading | `app_config.trade_markets` (allow-list) | order path (`config_store.trade_allowed`) |

  Current live state: `markets_off = ['Commodities','FX']`, `markets_disabled = ['Crypto']`,
  allow-list includes `Commodities` but not `FX`. So:
  - **Commodities — hidden from the owner's view, but ALLOWED in the order path.** The 2-hourly bridge
    can place a live IG order in a commodity that never appears in the owner's Scanner or Pre-orders.
    The snapshot carries 25 commodity instruments, 1 currently TRIGGERED.
  - **FX is safe only by accident**: it is blocked because it is missing from the allow-list, NOT by the
    Markets (Admin) disable the owner believed was doing it. 32 FX instruments, 8 currently TRIGGERED —
    adding FX to the allow-list (or clearing the allow-list, since EMPTY means ALLOW ALL) would make all
    of them tradeable and invisible at once.

  A screen warning + renaming the column Enabled -> Visible landed 2026-07-17. Still open, and needs an
  OWNER decision, not a silent fix: either drop Commodities from the allow-list, or switch it off in
  Markets (Admin), or accept that it trades unseen. Longer term, consider making Markets (User) mirror
  into `markets_disabled` for the owner, or renaming these so a view filter cannot be mistaken for a
  kill-switch. NOTE the empty-list trap: `get_trade_filters()` treats an empty `trade_markets` as
  "no restriction", so clearing it opens EVERY market rather than closing them.

## Security

- [ ] **`/api/config` POST "bridge" has no authorisation check** (found 2026-07-17 while gating "exec").
  `api_config()` requires only a LOGIN — any tier. Each branch is expected to gate itself, and most do
  (`markets_disabled`, `x_hvf_markets`, `features`, `engine` all check `is_admin`; `exec` now requires a
  gold subscription). **`bridge` checks nothing**, so any logged-in guest/silver can POST
  `{"bridge": true|false}` and flip `exec_WEB_BRIDGE` — i.e. turn the 2-hourly bridge's live IG
  order placement on or off for the shared trading account.
  NOT fixed because the correct gate is a product decision, unlike `exec`: the bridge toggle is shown in
  **Trading (Squeeze)**, which every logged-in tier can see, so gating it to admin/gold is a deliberate
  behaviour change for existing users rather than a straight bug fix. Decide the intended rule, then add
  the check next to the `exec` one in `hvf_web/server.py::api_config`.

## Data quality

- [ ] **`quality` is stored in four tables — review whether all four are needed** (raised 2026-08-29).
  Measured the same day, so the counts are real rather than estimated:

  | table | rows | with quality | newest | written by |
  |---|---|---|---|---|
  | `squeeze_history` | 35,680 | 35,680 | **today** | `squeeze_history.refresh_daily` |
  | `hvf_scan_log` | 12,882 | 12,882 | **today** | `run_hvf_report.py:1090` |
  | `hvf_triggers` | 1,121 | 1,121 | yesterday | `hvf_recorder.py:92` |
  | `signal_log` | 54,136 | 13,896 | **2026-08-06 (23 days stale)** | `signals.py:2062`, `run_diagnostics.py:254` |

  They are not four copies of one number — they are the same measurement over four different
  populations: a 15-month replay, a per-scan log, live detections since 2026-06-30, and session-monitor
  output. That is defensible. Two things are worth deciding anyway:

  - **`signal_log` is dead.** Last written 2026-08-06, because the session monitors are disabled
    (`WEB_BRIDGE` is the only enabled execution source). It still holds 54,136 rows and a `hvf_quality`
    column nobody maintains, plus `vwap_pct` / `vwap_position` / `week52_dir` / `week52_signal` — the
    only stored copies of those metrics anywhere, all frozen in early August. Anyone querying it gets
    stale answers with nothing saying so. Retire it, or label it explicitly as historical.
  - **Whether the four AGREE where they overlap is UNVERIFIED**, and deliberately not claimed. A first
    attempt joined `hvf_triggers.h3_date` to `squeeze_history.triggered_date` and reported 15 of 16
    overlaps differing by more than 5 — that result is void: those are different dates by definition
    (h3 is the pivot forming the ceiling, the trigger is the later break of it). `hvf_triggers` has no
    trigger-date column at all, only `recorded_at` and the pivot dates, so there is no sound join key.
    Establishing one is the first task if agreement matters.

  Related: the same review found the 52-week range and VolumeScore were persisted **nowhere**, now fixed
  by `instrument_metrics.py` (2026-08-29).

- [ ] **Universe tickers that no longer resolve on Yahoo: ANSS, MMC, FI** (user 2026-07-17; found during
  the 15-month backfill for P-24). These three are in `run_hvf_report.py::UNIVERSE` but Yahoo returns
  **HTTP 404 "Quote not found"** for all of them — not a throttle or a transient miss: `yf.download`
  returns 0 rows and `fast_info` raises `KeyError 'exchangeTimezoneName'`. They are the only 3 of 1309
  instruments with **no price history at all**, so they are invisible to the engine, the Scanner and the
  Performance report while still counting toward the universe.
  - `ANSS` — Ansys; plausibly delisted after the Synopsys acquisition, so the fix is likely REMOVAL.
  - `MMC` — Marsh & McLennan, and `FI` — Fiserv: both still trading as far as we know, so these are
    likely a symbol change needing a `config.YAHOO_MAP` entry rather than removal.
  - Do NOT guess replacement symbols — a wrong mapping silently attributes another company's prices to
    the ticker. Confirm each symbol against the exchange/Yahoo first, then either add a `YAHOO_MAP`
    entry or drop it from `UNIVERSE`, and re-run `python price_audit.py --backfill 460 --tickers <list>`.
  - Verify with: `python price_audit.py --backfill 460 --tickers ANSS,MMC,FI` (expect ~315 bars each).
  - A recurring reminder is scheduled until this is closed — see `.claude/` scheduled task
    "backlog-data-quality-reminder".

- [ ] **Instruments with under 15 months of history: MTLN.L, PRN.L, SHAW.L** (user 2026-07-17). Not a
  bug — earliest bars are 2025-08-04 / 2025-10-31 / 2025-10-30, i.e. these listed recently and 15 months
  of history does not exist yet. Nothing to fix; they will age into full coverage. Listed so the P-24
  "all instruments have 15 months" check has a documented set of known exceptions (1303/1309 + these 3
  + the 3 unresolved above = 1309).

## New feature requests — added 2026-06-26 (user batch)

- [ ] **Add NASDAQ-100 instruments to the universe** (user 2026-06-28, "on Monday afternoon" → target
  **2026-06-29 PM**; scope confirmed **NASDAQ-100**). Extend the monitored universe in
  `run_hvf_report.py::UNIVERSE` with the NASDAQ-100 constituents (~100 names). After adding, run a
  snapshot rebuild — the persistent name cache
  (`hvf_web/name_cache.json`) means only the NEW tickers get a name lookup. Verify the new names appear
  with `has_signal` flags and that the build time stays reasonable.

- [x] **Move price history into the database** — DONE 2026-06-29 (Supabase, ahead of the 3-day target).
  `price_store.py` (golden `price_history` table: PK (ticker,bar_date), source + recorded_at/updated_at,
  bulk UPSERT, read, read-or-fetch-write-through). Charts (`render_x_post_card`, `_render_price_window`)
  read Supabase first, YF on miss. `price_audit.py` + `trading-price-audit.yml` run the daily golden audit
  (re-fetch / compare / correct, logged to `price_audit_log`); `--backfill` for the one-off deep populate.
  Schema also in `run_schema.py`. Source is YF today; IG can be added (source column is free-form).
  ~~(user 2026-06-28, "change to db in 3 days" → target~~
  **~2026-07-01**). Today a transient Yahoo throttle is handled by a lightweight per-ticker disk cache
  (`data/price_cache/<sym>.pkl` for the X card via `intraday_signals.render_x_post_card`;
  `hvf_web/data/price_cache/win_<sym>_<days>.pkl` for the price-window chart via
  `hvf_web/server.py::_render_price_window`). That's resilience only — NOT queryable/auditable. Replace
  with a proper `price_history` (daily OHLC) table in the monitoring SQLite: populate it during the
  snapshot build (`hvf_web/build_snapshot.py`, which already downloads all ~424 instruments through the
  engine), and have the card + price chart read from the DB with Yahoo as the fallback. One source of
  truth for engine + charts + reports. Retire the `.pkl` fallback once the DB read path is verified.

- [ ] **Squeeze site: exact triggered date** (user 2026-06-27, "do date exact tomorrow"). The site's
  "Triggered" column currently shows a PROXY — the last pivot date (l3 for a long / h3 for a short),
  computed client-side in `hvf_web/index.html::augment`. Make it EXACT: in `hvf_web/build_snapshot.py`,
  for each TRIGGERED record fetch the daily history and find the first session AFTER h3_date where the
  close crossed the entry (bull: close >= entry; bear: close <= entry); store `triggered_date` on the
  record; have the UI prefer it over the proxy. Needs a snapshot rebuild (so it needs disk headroom —
  host was 100% full 2026-06-27, freed to ~3GB).

- [ ] **UK holder data from the FCA NSM (TR-1)** — added 2026-06-27. yfinance UK holder data is
  index-ETF noise, so the institutional-holder line is now SUPPRESSED for `.L` tickers (quality_report
  1.24.0). Proper source = FCA National Storage Mechanism DTR5/TR-1 "notification of major holdings"
  (data.fca.org.uk): authoritative, free, only lists >3% holders (the material ones). Verified an
  individual TR-1 artefact (e.g. /artefacts/NSM/RNS/<id>.html) is fetchable (HTTP 200 with a browser
  UA) and parseable (Issuer / shareholder / "% of voting rights"). **Blocker:** no public search API —
  my endpoint guesses 403'd; the FCA directs API requests to email support. Need to (a) discover the
  NSM search backend (reverse-engineer the data.fca.org.uk SPA's network call — browser devtools, or
  the operator pastes a sample search URL), (b) query TR-1s by issuer name/LEI, (c) keep the latest
  TR-1 per holder, parse the resulting %, surface the top holder. investing.com considered but rejected
  (no free API; scraping brittle + ToS). See [[feedback_validate_published_figures]].
- [ ] **(I) Weekly sector report to X** — turn the monthly sector report (@TheProfInvestor,
  https://x.com/TheProfInvestor/status/2070168387687424030) into our own WEEKLY X reports. Needs:
  ingest the source (read the tweet/thread), pick the sector rotation angle, render a weekly summary
  card + thread in house style (Slack/X rule: never name the source on the public tweet).
- [x] **(F) DONE 2026-06-26 (quality_report 1.21.0 + 1.22.0)** — _kpi_block "Key numbers" paragraph: P/E, net
  margin, ROA(~ROIC), revenue growth, FCF, net debt/EBITDA, buybacks, dividend + growth + payout flag, AND
  (1.22.0) insider Δ over ~9 months — net open-market shares bought minus sold (verified live: NKE +61.8k,
  AAPL -625.9k, TSLA -205.3k). **Market share dropped** — no clean free data source (ROIC remains the ROA
  proxy). All requested KPIs now covered except market share. Original item below.
- [ ] ~~**(F) More KPIs on the X tweet / report**~~ — add: ROIC, FCF, FCF growth, insider-holdings change
  over 9 months, revenue growth, debt up/down, Net Debt/EBITDA, buybacks, net margin, P/E, dividend
  growth rate, sector competitors, market share, + other KPIs. Source mostly yfinance + the existing
  fundamentals path (quality_report). Keep the 280-char tweet lean — most KPIs go in the threaded
  long report; only the strongest 1-2 ride the tweet. Decide the priority/wording per KPI.
- [x] **(E) DONE 2026-06-26 (bounce_monitor 1.0.0)** — URGENT email when an instrument SOLD in the last
  `BOUNCE_LOOKBACK_HOURS` (48h) bounces back to `>= sold_level*(1+BOUNCE_ALERT_PCT)` (2%, user choice).
  Decision (user 2026-06-26): EndToEndTrading + TradingViewWebhook share ONE IG account, so it reads
  `/history/activity` here and sees sells from either system. Pure logic (recent_sells / is_bounce /
  spam-guard, one alert per sell per bounce) unit-tested in `test_bounce_monitor.py` (20/20); URGENT
  mail via `trade_email.send_simple_email`; entrypoint `run_bounce_alert.py` + workflow
  `trading-bounce-alert.yml` (workflow_dispatch — wire a cron-job.org schedule, suggest every 15 min).
  **TODO on first live run:** validate the IG `/history/activity` record shape against `_sold_from_activity`
  (direction/level/epic/date) — modelled on `ig_shim.get_close_reason` but not yet seen against a real
  recent SELL. Original item below.
- [ ] ~~**(E) Urgent EMAIL alert on a bounce in a recently-sold instrument**~~ — if an instrument we SOLD
  within the last 48h bounces (e.g. Japan 225), send an URGENT email (not just Slack). Needs: a
  recently-closed-trades lookup (last 48h, SELL side), a "bounce" definition (e.g. price back above
  the exit by X% / N bars), and an email path flagged URGENT. Guard against spam (one alert per
  instrument per bounce).
- [x] **(C) DONE 2026-06-26 (run_hvf_report 1.27.0)** — post_to_slack also posts the report to the
  dedicated secondary signals webhook (user-chosen extra channel; secret already set 2026-06-22) alongside
  SLACK_SIGNALS; webhooks are independent (a missing/failing one doesn't stop the others). Secret
  wired into trading-hvf-report.yml.
- [x] **(D) DONE 2026-06-26 (run_hvf_report 1.26.0)** — hides a market's DEVELOPING watch list when it
  already has > DEVELOPING_HIDE_IF_TRADEABLE_OVER (10) tradeable setups; header notes hidden markets.
- [x] **(J) DONE 2026-06-26 (price_action 1.35.0 / config 1.25.0)** — `get_hvf_signal_mtf` now drops a
  TRIGGERED candidate whose live price ran past the target or more than `STALE_TRIGGER_MAX_PCT` (20%)
  beyond the entry, BEFORE picking best, so a fresh lower-timeframe setup can still win; if none
  survive the instrument is suppressed + logged to `hvf_suppressed_log`. Verified: ARM (weekly entry
  134 vs price 334) now returns empty; USDJPY/ABF.L TRIGGERED retained (within 20%). Kept OUT of
  `check_hvf_invariants` (that stays pure-geometry). Investigation showed ARM's levels were NOT a
  unit/instrument mismatch — yfinance confirmed ARM really was ~134 in Feb 2026 and has since run to
  334, i.e. a genuinely resolved squeeze, not the MSTR-class bug. NaN current_price (transient yfinance)
  is safely ignored by the gate rather than dropped. Suite green (21/21). Original item below.
- [ ] ~~**(J) Sanity-gate nonsensical TRIGGERED setups**~~ — ARM showed weekly TRIGGERED with Entry 134.25
  vs Now 347.71 (entry -61% BELOW price). A real TRIGGERED long has price AT/just above the H3 entry;
  entry far below price means a STALE squeeze (triggered long ago) or a current-price/level source
  mismatch (same class as the MSTR wrong-instrument bug). Add a gate: reject/flag any TRIGGERED long
  whose entry is more than ~X% below the live price (and the mirror for shorts). Relatedly verify the
  weekly current-price and the squeeze levels come from the SAME instrument/units. See J diagnosis.

## Trades failing to be placed — added 2026-06-24

- [x] **DONE 2026-06-26 (ig_shim 1.18.0).** Working-order size=0 now classifies via LAST_SIZE_SKIP —
  ACCOUNT_TOO_SMALL is summarised daily (with the funding gap), not paged. Original item below.
- [ ] ~~**Working-order size=0 still pages individually**~~ (e.g. *IREN (IREN Limited) BUY — "[working
  order] calculated size is 0 — balance too small for IG min deal size, or margin/epic problem"*).
  This is the THIRD size-zero path. The 2026-06-24 ACCOUNT_TOO_SMALL work only covered run_session's
  two paths (session-open + monitor-rescan); the working-order path was missed
  (`ig_shim.py::place_hvf_order_from_sig`, the `if size <= 0` alert ~line 2521). Its hardcoded reason
  contains "calculated size is 0" → `_classify_missed_trade` returns **SIZE_ZERO (paged)**, not the
  silenced **ACCOUNT_TOO_SMALL (daily summary)** — so unaffordable working orders still flood #alerts.
  **Fix (low-risk, consistent):** that path already calls `calculate_position_size`, so
  `ig_shim.LAST_SIZE_SKIP[epic]` is populated — classify the alert from it exactly like
  `run_session._size_skip_reason()`: `ACCOUNT_TOO_SMALL` → funding-gap reason (silenced + summarised),
  otherwise the generic SIZE_ZERO. Then IREN-type skips roll into the once-a-day summary like the
  other paths. Same root cause as the crypto skips — account too small (user chose to fund), see
  the ACCOUNT_TOO_SMALL handling in notify.py/run_session.py/ig_shim.py.

## Publication enrichment — added 2026-06-20

- [ ] **Competitor / news context on X tweets** (user 2026-06-20: "for $NKE look at articles
  mentioning direct competition e.g. Lululemon — do this for all X tweets going forward"). Scope:
  (1) a per-ticker competitor map (NKE→LULU/ADDYY, etc.) — curated dict or a sector-peer lookup;
  (2) a recent-headlines fetch (news API) to find a current competitive angle; (3) surface a
  one-line "vs LULU" angle as a tweet confirmation candidate. Constraints: it's a new external
  data source (needs an API key/rate budget); per the Slack/X rule the angle may appear on X but
  **the source/provider name must never be in the tweet**; keep it lowest-priority so it only
  shows when the 280-char tweet has room. Decide news source + competitor-map source before build.

- [ ] **Prior-trend magnitude gate — DISABLED 2026-06-20, needs a calibration DECISION.** Built
  (config.MIN_PRIOR_TREND_PCT, shared price_action._prior_trend_pct, daily+weekly), then disabled
  because at **20%** it broke CI regression case 10: the **HIK.L frozen known-good** has a prior
  impulse of only **12.5%**, so the gate rejected a colleague-validated squeeze AND the MTF then
  surfaced a wrong-direction bearish override. This is the crux of the 20% question: RW/Hunt's
  "~20%" is an interpretation (per the official-method audit), and a validated real setup (HIK.L)
  sits at 12.5%. **Decision needed before re-enabling:** (a) lower the threshold (≈10-12% would keep
  HIK.L; what does it cost in junk-cut?), (b) keep 20% and re-baseline the frozen fixtures
  deliberately + handle the bearish-override side-effect, or (c) make it a quality-score input, not
  a hard reject. Code is commented-out at both gate sites (get_hvf_signal + _run_hvf_on_hist),
  re-enable both together. ALWAYS run the FULL `python test_hvf_method.py` (not --quick) for any
  detection change — the frozen fixtures only run there + in CI.

- [ ] **R:R sanity** — the displayed R:R reaches 20-67:1 on very tight coils (sub-1% stops). The
  daily report now flags "R:R inflated by the tiny stop" when tight_stop_intraday; consider also
  capping the value used for ordering/ranking so a tiny-stop artifact doesn't dominate hvf_weight.

## Epic resolution (`epic_lookup`) — deferred 2026-06-19

Context: a wrong‑instrument audit (28 cached tickers mapped to the wrong IG instrument) was
repaired — 11 tickers pinned to correct unique epics, the rest **purged → safe/refused** by the
new `get_epic` identity guard (so they cannot be wrong‑traded). The items below are the leftovers
that need a human decision or a manual IG lookup. None is a trade risk while purged (the guard
refuses an unverified ticker rather than trade it).

IG /markets candidates retrieved 2026-06-24 via `run_data_quality_audit.py --lookup-epic`. The 9
below are now **PINNED + verified** (ig_shim 1.17.0 `_EPIC_VERIFIED_OVERRIDES` + get_epic Step 0);
`--verify-epic` confirmed each resolves TRADEABLE to the right IG company on 2026-06-24. No further
action — they are tradeable.

PINNED 2026-06-24 (done):
- [x] **PYPL** → `UC.D.PYPLVUS.DAILY.IP` "PayPal Holdings Inc (24 Hours)" (US DFB). NB a "PYPL"
  ticker search returns the *YieldMax PYPL ETF* (SI.D.PYPYUS) — the right epic only surfaces on a
  "PayPal" name search, so PYPL MUST be pinned (auto-resolve can't find it).
- [x] **MSTR** → `UC.D.MSTR.DAILY.IP` "Strategy Inc (24 Hours)". IG has adopted the rename, so the
  name-matcher should link now; pin to be safe (avoid the AB.D.MSTRAU Morningstar ETF).
- [x] **SYM** → `UD.D.SVFCUS.DAILY.IP` "Symbotic Inc" (epic body ≠ ticker → must pin).
- [x] **FLY** → `UB.D.FLYUS.DAILY.IP` "Firefly Aerospace Inc".
- [x] **FTAI** → `SC.D.FTAIUS.DAILY.IP` "Fortress Transportation and Infrastructure Investors LLC"
  (= FTAI Aviation, pre-rename). Distinct from UB.D.FIPUS "FTAI Infrastructure" (FIP).
- [x] **LUNR** → `UB.D.LUNRUS.DAILY.IP` "Intuitive Machines Inc".
- [x] **QBTS** → `SH.D.XPOAUUS.DAILY.IP` "D Wave Quantum Inc" (ticker search returns only the
  Defiance 2X Short QBTS ETF → must pin to the name-search epic).
- [x] **RGTI** → `SG.D.SNIIUS.DAILY.IP` "Rigetti Computing Inc" (ticker search returns only the
  Defiance 2X Short RGTI ETF → must pin).
- [x] **GLD** → `SI.D.GLDUS.DAILY.IP` "SPDR Gold Shares (24 Hours)" (US). Avoid AR.D.GLDSJ NewGold
  (JSE) and AG.D.GLDSP (EDITS_ONLY).

STILL UNRESOLVED:
- [ ] **DJT (Trump Media)** — neither "DJT" nor "TrumpMedia" returns it; IG may list it as
  "Trump Media & Technology". Needs a multi-word search (the lookup tool currently splits on spaces).
- [ ] **MP (MP Materials)** — "MP" returns MPLX / Mpact / Mpac / MP Evans, not MP Materials. Needs a
  multi-word "MP Materials" search. Drop from universe if IG genuinely doesn't list it.
- [ ] **SPCH, SPCL, SPCU, MSTY, NVDS** — **leveraged/inverse ETFs**, not equities (Defiance 2X /
  YieldMax / Tradr 1.5X Short NVDA). Recommend REMOVE from the scan universe (shouldn't trade as
  shares). Same class as the leveraged-ETF tickers above that returned only Defiance/YieldMax lines.

Done in the same effort (for reference): AXP→American Express `SA.D.AXP.DAILY.IP`,
AXP.AX→AXP Energy `AB.D.AKKAU.DAILY.IP`, SPGI, BMY, TGT, WDC, BSX, SMR, COHR, BKSY, HMC pinned;
`get_epic` now validates identity on every lookup (`ig_shim.py` 1.14.0, commit 83d08e5).
