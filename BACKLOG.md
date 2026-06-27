# Backlog

Deferred items (not blocking). Add new items at the top of the relevant section.

## New feature requests — added 2026-06-26 (user batch)

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
  `SLACK_RW_HVF` webhook (user-chosen extra channel; secret already set 2026-06-22) alongside
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
  334, i.e. a genuinely resolved funnel, not the MSTR-class bug. NaN current_price (transient yfinance)
  is safely ignored by the gate rather than dropped. Suite green (21/21). Original item below.
- [ ] ~~**(J) Sanity-gate nonsensical TRIGGERED setups**~~ — ARM showed weekly TRIGGERED with Entry 134.25
  vs Now 347.71 (entry -61% BELOW price). A real TRIGGERED long has price AT/just above the H3 entry;
  entry far below price means a STALE funnel (triggered long ago) or a current-price/level source
  mismatch (same class as the MSTR wrong-instrument bug). Add a gate: reject/flag any TRIGGERED long
  whose entry is more than ~X% below the live price (and the mirror for shorts). Relatedly verify the
  weekly current-price and the funnel levels come from the SAME instrument/units. See J diagnosis.

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
  impulse of only **12.5%**, so the gate rejected a colleague-validated funnel AND the MTF then
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
