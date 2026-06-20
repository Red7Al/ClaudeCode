# Backlog

Deferred items (not blocking). Add new items at the top of the relevant section.

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
