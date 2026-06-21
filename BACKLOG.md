# Backlog

Deferred items (not blocking). Add new items at the top of the relevant section.

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

- [ ] **PYPL (PayPal)** — IG lists *two* "PayPal Holdings Inc" lines (US + a foreign listing).
  Decide which (likely the US DFB) and pin it.
- [ ] **MSTR (Strategy / ex‑MicroStrategy)** — Yahoo renamed it "Strategy Inc"; IG still shows
  "MicroStrategy Inc", so the name‑matcher can't link the rename. Pin manually (verify the IG
  "MicroStrategy" epic) or add a rename alias.
- [ ] **SYM, MP, FLY, FTAI, LUNR, QBTS, RGTI** — IG search didn't surface a plain‑equity epic
  (Symbotic, MP Materials, Firefly Aerospace, FTAI Aviation, Intuitive Machines, D‑Wave, Rigetti).
  Confirm whether IG lists each; pin if so, otherwise drop from the scan universe.
- [ ] **SPCH, SPCL, SPCU, MSTY, NVDS** — these are **leveraged/inverse ETFs**, not equities (e.g.
  "Defiance Daily 2X …", "YieldMax …", "Tradr 1.5X Short NVDA"). Decide whether to remove them
  from the scan universe entirely (they shouldn't be traded as shares).
- [ ] **GLD** — cached a JSE "NewGold Issuer" ETF vs Yahoo "SPDR Gold Shares" (US GLD). Decide
  intended instrument and pin.
- [ ] **DJT** — cached the Dow index ("Wall Street") vs Yahoo "Trump Media & Technology Group".
  Decide intended instrument and pin.

Done in the same effort (for reference): AXP→American Express `SA.D.AXP.DAILY.IP`,
AXP.AX→AXP Energy `AB.D.AKKAU.DAILY.IP`, SPGI, BMY, TGT, WDC, BSX, SMR, COHR, BKSY, HMC pinned;
`get_epic` now validates identity on every lookup (`ig_shim.py` 1.14.0, commit 83d08e5).
