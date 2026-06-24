# Backlog

Deferred items (not blocking). Add new items at the top of the relevant section.

## Trades failing to be placed — added 2026-06-24

- [ ] **Working-order size=0 still pages individually** (e.g. *IREN (IREN Limited) BUY — "[working
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
