# ======================================================================================================================
# File:         intraday_signals.py
# Author:       Alex Hind
# Created:      2026-06-01
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Intraday technical signal computation for the US Monitor session.
# Evaluates open positions and candidate instruments mid-session using
# short-timeframe technical indicators.
#
# Signals computed:
#   RSI (14)           Overbought >70, oversold <30
#   MACD (12/26/9)     Crossover direction and momentum
#   VWAP               Price position vs intraday VWAP
#   Volume             Current volume vs 20-day average (confirmation)
#   Price momentum     % move from open, distance from intraday high/low
#   BB position        Where price sits within Bollinger Bands
#
# Used by:
#   US Monitor (18:30 BST) — mid-session position review
#   Can also be called at any session open for additional confirmation
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.50.0  2026-06-27  Alex Hind   (user 2026-06-27) Card "Now" price uses the last NON-NaN close — a forming/holiday NaN bar
#                                 rendered as "Now nan" on the SBUX card. Both _now_v and _cur_sig now dropna() first.
# 1.49.0  2026-06-26  Alex Hind   (user 2026-06-26) FIX dossier/card PNG never uploading: upload_png_to_slack (extracted in
#                                 1.45.0) used `requests` but the module has NO top-level requests import — every call died
#                                 with "name 'requests' is not defined" (seen in social-monitor logs: dossier_XPEV.png).
#                                 Added the import inside the function. This also unbreaks the X-draft card + 3yr PNG uploads
#                                 that route through the same helper.
# 1.48.0  2026-06-24  Alex Hind   (user 2026-06-24) Single source of truth for the instrument NAME: _resolve_name now
#                                 delegates to the new instrument_name.company_name (yfinance-first). Removes the duplicate
#                                 resolver that disagreed with social_monitor/notify (the MSTR -> "Morningstar AU ETF" vs
#                                 "Strategy Inc" split). _RESOLVED_NAMES cache removed (the shared module caches).
# 1.47.0  2026-06-24  Alex Hind   (user 2026-06-24) Tweet punctuation/casing: (B) the hook line now ends with a full stop
#                                 (the JIGI "rounding over" hook had none) — _full_stop() unless it already ends . ! or ?.
#                                 (C) each confirmation in the justification line is now sentence-cased ("ahead of MCD" ->
#                                 "Ahead of MCD") via _just_line, and the competitor angle says WHAT the comparison is on —
#                                 "ahead of MCD on 3-mo price" / "outpacing peer MCD on 3-mo price (+8% vs -3%)" (was the
#                                 ambiguous "ahead of MCD (3mo)").
# 1.46.0  2026-06-24  Alex Hind   (user 2026-06-24) Three fixes:
#                                 (A) FIX full company name vanishing from the lead tweet ($SBUX went out with no
#                                     "(Starbucks Corporation)"): the 280-char fitter ranked the NO-NAME explainer base
#                                     above the named base, so the name was the first thing trimmed. The name is now
#                                     NON-NEGOTIABLE — _bases lists ONLY named variants (name+explainer, then name+desc),
#                                     so confirmations are trimmed first and the explainer next, the $cashtag's adjacent
#                                     full name is ALWAYS kept. Full detail (explainer + every confirmation) still rides
#                                     the long-report thread posted beneath the lead. base_no_name kept only as the
#                                     pathological absolute fallback (line ~2205).
#                                 (C) FIX $LAND card left-edge white space remaining after 1.45: the price fetch was
#                                     capped at 365d for any setup NOT tagged "weekly*", so a daily setup with ~22-month-old
#                                     funnel pivots drew the jaws far left of the price line. Cap is now a single absolute
#                                     safety (1500d) and the start always reaches the oldest pivot regardless of timeframe
#                                     label, so the price line fills to the funnel (user: old pivots are valid HVF points).
#                                 (D) FIX bogus spikes on the 3-yr history (e.g. $JIGI/JII.L popping ~+20% for one week then
#                                     fully reversing — yfinance bad prints on illiquid UK-trust holiday/quarter-end weeks):
#                                     _yf_weekly_3y now median-despikes the weekly series (a bar > 15% from BOTH neighbours
#                                     in the same direction is replaced with the neighbour average) before caching.
# 1.45.0  2026-06-23  Alex Hind   (user 2026-06-23) FIX USDJPY card left-edge white space: weekly setups span 1-2+ yrs but
#                                 the chart fetch capped at 365d, so the funnel jaws drew far left of the price line. Cap is
#                                 now timeframe-aware (1150d weekly / 365d daily) + adaptive month-tick density/format.
#                                 Extracted upload_png_to_slack() (single source for the Slack external-upload flow) and
#                                 deduped the two inline copies in _generate_x_drafts (card + 3yr history).
# 1.44.0  2026-06-23  Alex Hind   (user 2026-06-23) FIX no-graph on FX/index tweets (USDJPY): the card chart download, the
#                                 52w fetch, _yf_weekly_3y and _yf_info now map the ticker via YAHOO_MAP (USDJPY->USDJPY=X,
#                                 JPN225->^N225) — the raw symbol 404'd and the tweet went out with NO chart at all.
# 1.43.0  2026-06-22  Alex Hind   (user 2026-06-22) Tweet head order: INSTRUMENT on the top line, the direction/state tag
#                                 beneath it ("$NKE (NIKE, Inc.) winding tighter" / "BEARISH setup · not triggered yet").
# 1.42.0  2026-06-22  Alex Hind   (user 2026-06-22) Tweet head in WORDS, not icons ("this icon is not clear"): heading reads
#                                 "BEARISH setup · not triggered yet" (state in words, no 📉/📈); the hook's leading ⏳/👀/🚨
#                                 state icon is stripped to plain text.
# 1.41.0  2026-06-22  Alex Hind   (user 2026-06-22) Tweet head: (B) $cashtag and full name kept ADJACENT — the hook comment is
#                                 tagged AFTER ("$NKE (NIKE, Inc.) winding tighter", not "$NKE winding tighter (NIKE, Inc.)").
#                                 (C) the description + plain-English explainer are now ONE paragraph (related; no line break),
#                                 with a full stop added to the description fragment.
# 1.40.0  2026-06-22  Alex Hind   (user 2026-06-22) X-draft selection ordered by action_score (R:R ÷ distance-to-entry) so the
#                                 top-N/market published per market are the highest-R:R, closest-to-trigger setups.
# 1.39.0  2026-06-22  Alex Hind   (user 2026-06-22) (a) FIX missing 3-yr history on the X card: the 3-yr weekly data is now
#                                 fetched once via cached _yf_weekly_3y (with one retry) and shared by the card inset + the
#                                 standalone PNG — it was fetched 3x/publication and silently dropped to yfinance rate-limits.
#                                 (b) Explicit BULL/BEAR at the TOP: card title shows "▼ BEARISH · $TICKER" coloured red/green;
#                                 tweet leads every variant with "📉 BEARISH setup" / "📈 BULLISH setup".
# 1.38.0  2026-06-22  Alex Hind   Analyst stance OVER TIME on the tweet (user 2026-06-22): _analyst_angle adds the current
#                                 buy/hold split, the 3-month drift in the buy count, and the mean target vs spot — a HIGH-
#                                 priority justification so the bull/bear divergence is knitted (e.g. BEARISH HVF on a name
#                                 analysts still rate buy but are cooling on). yfinance only; no $cashtag; US-equity coverage.
# 1.37.0  2026-06-21  Alex Hind   FIX X 403 "max one cashtag": the competitor angle used a $PEER cashtag ($LULU) on top of the
#                                 hook's $TICKER — X rejects 2 cashtags. Peer is now plain text ("ahead of LULU (3mo)"). This
#                                 was the real cause of every NKE/MA live-X 403 since the angle was added (not the daily cap).
# 1.36.0  2026-06-21  Alex Hind   render_3yr_history_card (user 2026-06-21): a standalone 3-YEAR price-history PNG (weekly
#                                 closes, current + funnel levels marked) attached as an EXTRA Slack visual alongside the
#                                 card — Slack only, never on X.
# 1.35.0  2026-06-21  Alex Hind   Card prices to 2 decimal places (user 2026-06-21): Now/Entry/Stop/Target/Support/Resistance
#                                 now :.2f (were :g, e.g. "Support 40.9833" -> "40.98").
# 1.34.0  2026-06-21  Alex Hind   Competitor NEWS narrative (user 2026-06-21): _competitor_news surfaces a recent headline
#                                 (preferring a peer mention) as a SLACK-ONLY block in the X-draft wrapper — never on the X
#                                 tweet/card (may name the publisher; Slack is internal). Free yfinance .news feed.
# 1.33.0  2026-06-21  Alex Hind   Card (user 2026-06-21): "Sup"/"Res" spelled out to "Support"/"Resistance" (spacers tightened
#                                 to fit); added a 3-year weekly-close history inset (top-right, green/red by 3yr return) so
#                                 the long-term trend is visible (e.g. NKE's multi-year path).
# 1.32.0  2026-06-21  Alex Hind   P/E in relation to the market (user 2026-06-21): the tweet justification + the card P/E now
#                                 tag cheap/in-line/rich vs config.MARKET_PE (e.g. "P/E 24.9, rich vs ~21 mkt").
# 1.31.0  2026-06-21  Alex Hind   Competitor angle: raised to HIGH priority (right after pattern quality) + a COMPACT short
#                                 form ("ahead of $LULU (3mo)") so it reliably survives the 280-char trim (the first NKE
#                                 test trimmed the long form out). Verified NKE tweet now carries the $LULU angle.
# 1.30.0  2026-06-21  Alex Hind   Competitor angle on X tweets (user 2026-06-21): _competitor_angle names the top curated peer
#                                 + relative ~3mo performance ('outpacing peer $LULU (+8% vs -3%, 3mo)'); lowest-priority
#                                 tweet confirmation. Names a competitor (not a data source) so X-safe.
# 1.29.0  2026-06-20  Alex Hind   Code-review fix: _levels_changes_line iterates E,S only (target dropped from the fingerprint
#                                 in 1.27.0) — avoids a spurious "Target X -> —" on the one-time E|S|T -> E|S migration.
# 1.28.0  2026-06-20  Alex Hind   (user 2026-06-20) Copy-variety pass — _X_DESC pools expanded 10 -> 18 per key with more
#                                 natural, less templated phrasing (the "Compression building..." repetition looked AI-
#                                 generated). Repetition further cut by the new quality/missed-entry gates reducing volume.
# 1.27.0  2026-06-20  Alex Hind   (user 2026-06-20) ABF duplicate fix — _levels_fp keyed on entry+stop only (target wobble
#                                 from AMP1/IG was flipping the fingerprint every run). Card S/R window 20 -> 60 bars.
#                                 Current price added to the X-draft Slack wrapper context line.
# 1.26.0  2026-06-19  Alex Hind   (user 2026-06-19) P/E and insider-ownership added as lowest-priority tweet confirmations
#                                 (from yfinance .info, cached) — appear only when the 280-char tweet has room; source
#                                 provider never named in the tweet.
# 1.25.0  2026-06-19  Alex Hind   Expected time-to-target in the X-draft Slack wrapper context line (user 2026-06-19) via
#                                 price_action.target_horizon — Slack only; deliberately NOT in the tweet text or on the card.
# 1.24.0  2026-06-19  Alex Hind   X card (user 2026-06-19): top levels line now shows Support/Resistance after R:R (recent
#                                 ~20-bar swing low/high); the squeeze description carries the SECTOR name; and each
#                                 right-edge Entry/Stop/Target label shows its % from the live price (pct_from_current).
# 1.23.0  2026-06-19  Alex Hind   D220 -> D240 (user 2026-06-19): _tf_desc "long-term" key + the card timeframe-label
#                                 example comment now read d240 (long-term daily scan window changed in price_action 1.19.0).
# 1.22.0  2026-06-19  Alex Hind   Dossier (user 2026-06-19, Current #5): _generate_x_drafts collect dict now carries the FULL
#                                 HVF confirmations list ("justifications") so the dossier can show every comment (the
#                                 280-char tweet only fits a few). Rendered by instrument_dossier.
# 1.21.0  2026-06-19  Alex Hind   Publication correctness (user 2026-06-19): changed-detection now keys on published LEVELS
#                                 (entry/stop/target) not confirmations — _levels_fp/_parse_levels_fp/_levels_changes_line
#                                 replace _draft_confirmations_fp. A republished seen-before instrument is tagged "👀 Seen
#                                 before" with the exact level delta (Current #3). Per-market header shows "top N of M
#                                 candidates" (However #3). When nothing is new/changed, the top set is re-shown under a
#                                 "Shared again as there is nothing new to show" banner instead of an empty channel (However #4).
# 1.20.0  2026-06-19  Alex Hind   Quality gate (user 2026-06-19): _generate_x_drafts drops setups below MIN_PUBLISH_QUALITY (70)
#                                 from the X drafts / live-X (dossier collect mode exempt). Draft weight order is now
#                                 R:R-first (price_action.hvf_weight 1.18.0).
# 1.19.0  2026-06-19  Alex Hind   FIX wrong instrument name (user 2026-06-19: AXP tweeted as "AXP Energy Limited"): _resolve_name
#                                 now prefers yfinance (the exact scanned Yahoo ticker) over notify's epic_lookup, which can
#                                 carry a wrong-instrument name. epic_lookup is the fallback only.
# 1.18.0  2026-06-17  Alex Hind   Draft Slack numbering (user 2026-06-17): per-instrument number RESETS per market
#                                 ("X Draft 1/5 …", not global 11/30); the market name + its position "(k of K)" are in
#                                 both the section header and each draft title.
# 1.17.0  2026-06-17  Alex Hind   X drafts: top X_DRAFT_PER_MARKET (=5) per market (was PER_MARKET_TOP_N), and changed_only
#                                 mode — only re-show an instrument when its CONFIRMATIONS change (x_draft_state fingerprint,
#                                 user 2026-06-17). _generate_x_drafts now RETURNS the posted set so the morning report can
#                                 publish the top X_PUBLISH_TOP_N/market of the changed set to live X.
# 1.16.0  2026-06-16  Alex Hind   Complete publication (user 2026-06-16): _generate_x_drafts now posts the long quality report
#                                 (1/n thread) right after each instrument's card + short tweet, via quality_report.
#                                 publish_long_report_for — so card + short + long ALWAYS go together (publications + dossier).
# 1.15.0  2026-06-16  Alex Hind   Tweet spacing (user 2026-06-16): blank line after the hook/company line, and a blank line
#                                 before the confirmations block ("Pattern quality …"); description + explainer stay one
#                                 paragraph. Adds 2 newlines to the 280-fit budget (accounted for in the fitting loop).
# 1.14.0  2026-06-16  Alex Hind   X drafts grouped per market (user 2026-06-16: "top 10 by market"): _generate_x_drafts
#                                 now selects the top PER_MARKET_TOP_N per market (price_action.group_by_market, MARKET_ORDER)
#                                 instead of a global X_DRAFT_TOP_N=20, and posts a per-market section header when the market
#                                 changes. Same per-instrument webhook+card delivery (content/order only). signal_log
#                                 enrichment now covers exactly the posted set.
# 1.13.0  2026-06-15  Alex Hind   X-draft tweet now surfaces a broker-recommendation confirmation (user 2026-06-15) —
#                                 reads analyst_signal/analyst_recommendation from signal_log, gated on the recommendation
#                                 matching the trade side ("Brokers rate it Buy"). Direction-aligned; hold/none excluded.
# 1.12.0  2026-06-15  Alex Hind   (a) X post-card: removed the @EndToEndTrading handle line (user 2026-06-15: remove brand
#                                 text from tweets/reports). (b) _resolve_name strips a trailing standalone "Or"/"Ord"/
#                                 "Ordinary" share-class token ("Helios Towers Or" → "Helios Towers") — the uppercase
#                                 " ORD" split missed the mixed/truncated forms (user 2026-06-15).
# 1.11.0  2026-06-15  Alex Hind   Backlog #9b: a tight-stop draft (hvf_tight_stop_intraday) still posts (user 2026-06-15
#                                 "publish with a caution note") but the Slack wrapper now carries a ⚠️ caution — the R:R is
#                                 inflated by the tiny stop, IG won't hold it intraday, take it MANUALLY with a wider stop /
#                                 smaller size. Caution rides in the Slack block + dossier collect dict, NOT the 280-char
#                                 tweet or the public card. Auto-trading already skips it (ig_shim).
# 1.10.0  2026-06-15  Alex Hind   _generate_x_drafts gains post=False/collect=True (dossier mode): builds the SAME tweet +
#                                 card for each instrument but returns them instead of posting to Slack. Lets
#                                 instrument_dossier.py render one instrument's X artifacts via the exact production path
#                                 (no format drift). Default behaviour (post=True) is unchanged.
# 1.9.1   2026-06-15  Alex Hind   _resolve_name also collapses the spelled-out "Public Limited Company" suffix to "PLC"
#                                 (VOD.L "Vodafone Group Public Limited Company" → "Vodafone Group PLC") — shorter, and
#                                 consistent with the existing plc/p.l.c. normalisation. Feeds tweets, cards and the HVF
#                                 report label. Display only.
# 1.9.0   2026-06-14  Alex Hind   Code-review (perf): _yf_info() memoises yfinance .info per process. The X-card path
#                                 fetched .info 3x per instrument (name, exchange tag, P/E) — each a ~1-2s round-trip;
#                                 now one fetch is shared. No behaviour change (.info is static metadata). [epic_lookup
#                                 persistence was rejected: epic_lookup.epic is NOT NULL and untraded constituents have
#                                 no epic, so a name-only row can't be stored without polluting the trade-epic lookup.]
# 1.8.0   2026-06-14  Alex Hind   Code-review: _weight / _draft_weight now delegate to price_action.hvf_weight() (single
#                                 source of truth for weight order) instead of an inline tuple. Behaviour identical.
# 1.0.0   2026-06-01  Alex Hind   Initial build.
# 1.0.1   2026-06-05  Alex Hind   get_intraday_signals: do not fall back to 5m data when 1h data is unavailable.
#                                 RSI/MACD on 5m bars is 10× more reactive than intended (14 bars = 1.2h instead of 14h)
#                                 — silent wrong-timeframe signals. Now logs a warning and skips RSI/MACD instead.
#                                 Position size fallback 0.5 → 0.0 on exception; same dangerous pattern fixed in
#                                 ig_shim.py 1.0.3.
# 1.0.2   2026-06-08  Alex Hind   compute_rsi: zero-loss period (a pure up-move) returned NaN instead of 100 — so `rsi >
#                                 70` was silently False exactly when overbought mattered most. Now resolves NaN to 100
#                                 (pure rally) / 50 (flat or too few bars). Verified on synthetic series: rally→100.0,
#                                 flat→50.0, normal→58.5.
# 1.1.0   2026-06-10  Alex Hind   HVF setups → IG WORKING ORDERS (user 2026-06-10): US monitor routes HVF signals to a
#                                 pending order at the exact H3 entry (re-signal = amend, never a duplicate; no market
#                                 fall-through), reconciles fills/cancels each pass, and counts open positions + today's
#                                 PENDING working orders in the US slot budget (was trade_log only).
# 1.2.0   2026-06-10  Alex Hind   X (Twitter) draft reports: after each tradeable-HVF Slack post, _generate_x_drafts()
#                                 posts one tweet-ready block per instrument (with HVF chart attached) to SLACK_TWITTER
#                                 channel for review before manual posting to X.
# 1.7.0   2026-06-13  Alex Hind   FIX INSUFFICIENT_FUNDS: the US monitor rescan path sized trades naively with a 0.5
#                                 floor (size=max(0.5, risk/stop)) and NEVER checked margin — DELL needed ~£3,050
#                                 margin on £860 available, rejected every scan. Now routes through the margin-aware
#                                 calculate_position_size (smaller of risk-based and margin-affordable size; profile
#                                 risk_per_trade + stress_mult), matching the session-open and UK/AUS-monitor paths.
#                                 size 0 → skip with a missed-trade alert (no IG rejection). Verified: DELL now sizes
#                                 0.1 (margin ≈ £611) instead of 0.5.
# 1.8.0   2026-06-13  Alex Hind   VWAP confirmation surfaced (user 2026-06-13): tweet gets a short, direction-aligned
#                                 "Above VWAP"/"Below VWAP" tag; the plain-English logic ("price above the day's
#                                 volume-weighted average — buyers paying up, demand aggressive → confirms the long")
#                                 is drawn on the PNG card instead of the tweet. Both read the SAME signal_log.vwap_position
#                                 (added to the X-draft query) so card and tweet always agree. Volume justification now
#                                 names its indicator: "Volume flow backing the move (OBV)". COT figure NOT added — cot_score
#                                 lives in cot_snapshot, not signal_log, and COT applies only to futures-backed instruments.
# 1.9.0   2026-06-13  Alex Hind   X publication overhaul (user 2026-06-13): (a) tweet LEADS with a rotated hook
#                                 ("👀 Watching $MNG") and a rotated squeeze description — _X_HOOKS/_X_DESC cycled by
#                                 batch position + day-of-year so consecutive posts differ; (b) ".L" stripped from the
#                                 cashtag/hashtag (UK names → $MNG, #MNG) in tweet AND card; (c) card levels line gets
#                                 markers ◎ entry · ● stop · ▲ target · ⚖ R:R (DejaVu-safe — colour emoji don't render
#                                 in the card font); (d) 52-week-high gridline drawn on the chart for target context,
#                                 with y-axis reframed to all levels; (e) VWAP caption shows the % (vwap_pct) too.
# 1.10.0  2026-06-13  Alex Hind   (a) P/E ratio added to the card's grey context line ("P/E 14.2" with a diamond
#                                 marker; FORWARD then trailing via yfinance .info; omitted if absent/non-positive).
#                                 (b) COT confirmation tagged "(smart money)" in the tweet — COT commercials are the
#                                 system's smart money; options flow is NOT (mixed institutional + retail), no such tag.
# 1.11.0  2026-06-13  Alex Hind   X-draft count moved to config.X_DRAFT_TOP_N (was hardcoded 20) so it's tuned in one
#                                 place; the signal_log context enrichment now covers all X_DRAFT_TOP_N posted drafts
#                                 (was the first 10), so ranks 11–20 also get their plain-English confirmations.
# 1.12.0  2026-06-13  Alex Hind   FIX: _X_HOOKS keyed by signal only → BEARISH setups got bullish hooks ("📈 breaking
#                                 out" / "Breakout:" on a breakdown, contradicting the direction-correct description).
#                                 Now keyed by (direction, signal): bearish TRIGGERED → "📉 breaking down now" / "Breakdown:".
# 1.13.0  2026-06-13  Alex Hind   Tweet now carries a plain-English PRIMARY-signal explainer line (_X_EXPLAIN, rotated,
#                                 direction+state aware) so a reader with no system knowledge understands the squeeze —
#                                 user 2026-06-13 ("very little explanation of primary/confirmation signals"). Fitting
#                                 prioritises keeping the explainer (then name, then confirmations to taste) within 280.
# 1.14.0  2026-06-13  Alex Hind   Tweet disclaimer: blank line before it + rendered in Unicode bold italic (user
#                                 2026-06-13). Fitting now uses _x_weighted_len (SMP glyphs — emoji hooks + bold-italic
#                                 disclaimer — count as 2, matching X's 280 weighting) instead of len(), so "fits X" is
#                                 honest. NOTE: bold-italic disclaimer is SMP Unicode — not screen-reader friendly.
# 1.15.0  2026-06-13  Alex Hind   (A) tweet hashtags now include market + country (#FTSE100 #UK / #SP500 #USA) via
#                                 _x_market_tags (from the scan index). (B) _resolve_name prefers Yahoo longName and
#                                 normalises the "plc" suffix WITHOUT .title() — fixes acronym mangling (HSBC was "Hsbc").
# 1.16.0  2026-06-13  Alex Hind   Tweet market tag uses the REAL listing exchange for US names (#NASDAQ/#NYSE via yfinance
#                                 exchange code, cached) instead of #SP500; UK keeps its index (#FTSE100/#FTSE250). User 2026-06-13.
# 1.17.0  2026-06-14  Alex Hind   Expanded the tweet phrasing pools (_X_HOOKS/_X_DESC/_X_EXPLAIN) to ~10-12 variants each
#                                 per (direction, signal) so consecutive posts don't read like a template (user 2026-06-14).
# 1.6.1   2026-06-13  Alex Hind   X drafts posted to SLACK_TWITTER in BEST→WORST order (user 2026-06-13): TRIGGERED
#                                 before READY, quality desc, then R:R desc (was quality only). Each draft header
#                                 carries its rank 'N/total (best→worst)' so the order is explicit even if Slack
#                                 interleaves the webhook text with the bot-uploaded chart images.
# 1.6.0   2026-06-13  Alex Hind   X post format (user 2026-06-13): tweet TEXT drops the price line (Now/Entry/Stop/
#                                 Target/R:R) and the HVF timeframe — prices live on the PNG card. Card header gains
#                                 a 52-week High/Low line (1y fetch, ×ig_scale) and drops the timeframe. Confirmations
#                                 stay in clear English (now with more char budget).
# 1.5.2   2026-06-12  Alex Hind   "Now: {price}" leads the levels line in tweets AND on the post card (user 2026-06-12 —
#                                 readers must see distance to the trigger). Card uses its fresh download (× ig_scale to
#                                 match level units); tweet uses the scan's current_price.
# 1.5.1   2026-06-12  Alex Hind   hvf_watch: UK (.L) tradeable setups IG-validated before posting (cap 10/run);
#                                 mismatches demoted to DEVELOPING. Mirrors run_hvf_report 1.3.0.
# 1.5.0   2026-06-12  Alex Hind   X post card renderer extracted to render_x_post_card() — SINGLE SOURCE OF TRUTH used
#                                 by both _generate_x_drafts (Slack) and new generate_x_cards.py (local PNGs for manual
#                                 X posting while SLACK_BOT_TOKEN lacks files:write). New _resolve_name(): full company
#                                 name via epic_lookup then yfinance ("$BRGE.L (BRGE.L)" → "(BlackRock Greater Europe
#                                 Invest)") for tweets and cards. _SIG_LABEL/_tf_desc now module-level.
# 1.4.5   2026-06-11  Alex Hind   _post_hvf_watch: tradeable/developing lists now in WEIGHT order (TRIGGERED first,
#                                 quality desc, R:R desc) before the [:15] cap — was caller order, so the cap could
#                                 silently drop the best setups (user 2026-06-11: all lists in weight order).
# 1.4.4   2026-06-11  Alex Hind   X drafts: fix second chart crash — yfinance returns MultiIndex columns for a single
#                                 ticker so hist["Close"] is a DataFrame; float(DataFrame.median()) raised TypeError and
#                                 every chart failed in run 27370959365. Squeeze to Series once after download.
#                                 Reproduced and fix verified locally against live yfinance before commit.
# 1.4.3   2026-06-11  Alex Hind   X drafts: (a) FIX chart tz bug — pivot dates from the scan are tz-naive while end_dt
#                                 is UTC-aware; comparison raised TypeError and EVERY chart failed in run 27368931212.
#                                 Now localised to UTC first. (b) TRIGGERED setups posted first (breaking out now),
#                                 then READY, quality desc; cap raised 10 → 20 (51 tradeable found 2026-06-11, only
#                                 first 10 posted). (c) Pattern quality ≥60 added to tweet justifications.
# 1.4.2   2026-06-11  Alex Hind   X drafts: ALL aligned confirmations now in the tweet in plain English (was options
#                                 flow + insider only): COT → "Futures positioning bullish (COT report)", ADX → "Strong
#                                 trend in force (ADX)", OBV → "Volume flow backing the move", sector → "Sector (XLK)
#                                 moving the same way", Senate → "US Senate-disclosed buying". Fitting loop trims
#                                 lowest-priority confirmations first to stay ≤280 chars (user 2026-06-11).
# 1.4.1   2026-06-11  Alex Hind   Signal summary: Confs:N now lists WHICH confirmations fired, via signals.conf_names()
#                                 (user 2026-06-11 — 'How is NEUTRAL a confirmation?': the Options/BB/COT fields show
#                                 family STATE, not the counted items).
# 1.4.0   2026-06-11  Alex Hind   (renumbered from duplicate 1.3.1) _generate_x_drafts: post card image now uploaded to
#                                 the SLACK_TWITTER channel via the Slack external upload flow
#                                 (files.getUploadURLExternal → completeUploadExternal; legacy files.upload retired).
#                                 Needs SLACK_BOT_TOKEN (files:write) + SLACK_TWITTER_CHANNEL_ID secrets; until both are
#                                 set the draft text posts with an explicit "chart not attached" note (no silent gap).
# 1.2.5   2026-06-11  Alex Hind   _generate_x_drafts: revert 1.2.3 — SLACK_CLAUDE_TWITTER removed; SLACK_TWITTER is the
#                                 only secret and already points at #claude-twitter (user correction 2026-06-11).
# 1.2.4   2026-06-11  Alex Hind   _generate_x_drafts: chart upgraded to the agreed X post card format (2026-06-10):
#                                 tweet-text header panel (@handle, $TICKER (Name), setup, levels, hashtags, "Not
#                                 financial advice."), red upper jaw / green lower jaw funnel, full-width
#                                 entry/stop/target lines with right-edge labels. Replaces plain chart.
# 1.2.3   2026-06-11  Alex Hind   _generate_x_drafts: also post to SLACK_CLAUDE_TWITTER (#claude-twitter channel) in
#                                 addition to SLACK_TWITTER.
# 1.2.2   2026-06-11  Alex Hind   _generate_x_drafts: always append "Not financial advice." to every tweet (user
#                                 directive 2026-06-11).
# 1.2.1   2026-06-11  Alex Hind   _generate_x_drafts: SLACK_X renamed to SLACK_TWITTER (user directive — no separate
#                                 SLACK_X secret exists).
# 1.3.0   2026-06-10  Alex Hind   HVF watch deduplication: _post_hvf_watch now fingerprints the tradeable+developing
#                                 lists and compares against the last-posted state in hvf_watch_state DB table. Sends
#                                 "No changes in the latest period." when nothing has moved; full update only when
#                                 figures actually change. HVF watch removed from run_us_monitor (was Part 1.5 with a
#                                 30-min gate) — now a standalone US_HVF_WATCH session run every 2 hours via
#                                 trading-us-hvf-watch.yml workflow.
# 1.3.1   2026-06-10  Alex Hind   Fix X-draft funnel chart: upper jaw now drawn through real H1→H2→H3 pivot points,
#                                 lower jaw through L1→L2→L3 — anchored to actual price history dates/levels from the
#                                 signal dict. History window now spans from 14 days before oldest pivot date (not fixed
#                                 180 days). Legend and R:R removed from chart — shown in Slack context block below the
#                                 tweet instead.
# ======================================================================================================================

import os
from db_pool import get_db as _pool_get_db   # resilient session-pooler connection (timeout+retry)
from dotenv import load_dotenv; load_dotenv(override=True)
import logging
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

from config import YAHOO_MAP, DEFAULT_TARGET_RR, PER_MARKET_TOP_N, MARKET_ORDER, X_DRAFT_PER_MARKET, MIN_PUBLISH_QUALITY

log = logging.getLogger("intraday_signals")


# ======================================================================================================================
# RSI
# ======================================================================================================================

def compute_rsi(closes: pd.Series, period: int = 14) -> float:
    """Compute RSI. Returns 0-100. >70 overbought, <30 oversold."""
    delta  = closes.diff()
    gain   = delta.clip(lower=0).rolling(period).mean()
    loss   = (-delta.clip(upper=0)).rolling(period).mean()
    rs     = gain / loss.replace(0, np.nan)
    rsi    = 100 - (100 / (1 + rs))
    val    = rsi.iloc[-1]
    if pd.isna(val):
        # loss == 0 over the period: a pure up-move is max-overbought (RSI 100);
        # a completely flat series (gain also 0, or too few bars) is neutral (50).
        last_gain = gain.iloc[-1]
        val = 100.0 if (pd.notna(last_gain) and last_gain > 0) else 50.0
    return round(float(val), 1)


# ======================================================================================================================
# MACD
# ======================================================================================================================

def compute_macd(closes: pd.Series,
                 fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """
    Compute MACD line, signal line, and histogram.

    Returns:
        macd_line:    MACD line value
        signal_line:  Signal line value
        histogram:    MACD - Signal (positive = bullish momentum)
        crossover:    BULLISH (MACD crossed above signal),
                      BEARISH (crossed below), or NONE
    """
    ema_fast   = closes.ewm(span=fast,   adjust=False).mean()
    ema_slow   = closes.ewm(span=slow,   adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - signal_line

    # Detect crossover in last 3 bars
    crossover = "NONE"
    if len(histogram) >= 3:
        prev_hist = float(histogram.iloc[-2])
        curr_hist = float(histogram.iloc[-1])
        if prev_hist < 0 and curr_hist > 0:
            crossover = "BULLISH"
        elif prev_hist > 0 and curr_hist < 0:
            crossover = "BEARISH"

    return {
        "macd_line":   round(float(macd_line.iloc[-1]),   4),
        "signal_line": round(float(signal_line.iloc[-1]), 4),
        "histogram":   round(float(histogram.iloc[-1]),   4),
        "crossover":   crossover,
        "momentum":    "BULLISH" if float(histogram.iloc[-1]) > 0 else "BEARISH",
    }


# ======================================================================================================================
# VWAP (intraday)
# ======================================================================================================================

def compute_vwap(hist: pd.DataFrame) -> dict:
    """
    Compute intraday VWAP from 5-minute candles.
    Returns VWAP level and whether price is above or below.
    """
    result = {"vwap": None, "position": None, "pct_from_vwap": None}
    try:
        typical = (hist["High"] + hist["Low"] + hist["Close"]) / 3
        vwap    = (typical * hist["Volume"]).cumsum() / hist["Volume"].cumsum()
        last_vwap  = float(vwap.iloc[-1])
        last_close = float(hist["Close"].iloc[-1])
        pct_from   = (last_close - last_vwap) / last_vwap * 100

        result["vwap"]         = round(last_vwap, 4)
        result["position"]     = "ABOVE" if last_close > last_vwap else "BELOW"
        result["pct_from_vwap"] = round(pct_from, 2)
    except Exception as e:
        log.warning(f"VWAP failed: {e}")
    return result


# ======================================================================================================================
# Volume analysis
# ======================================================================================================================

def compute_volume_signal(ticker: str, hist_5m: pd.DataFrame) -> dict:
    """
    Compare current session volume to 20-day average daily volume.

    HIGH_VOLUME  — today's volume > 1.5× 20-day average (strong conviction)
    NORMAL       — within ±50% of average
    LOW_VOLUME   — below 50% of average (weak conviction)
    """
    result = {"volume_signal": "NORMAL", "volume_ratio": None}
    try:
        yticker = YAHOO_MAP.get(ticker, ticker)
        t       = yf.Ticker(yticker)

        # 20-day average daily volume
        hist_daily = t.history(period="25d", interval="1d")
        if hist_daily.empty:
            return result

        avg_vol     = float(hist_daily["Volume"].mean())
        today_vol   = float(hist_5m["Volume"].sum()) if not hist_5m.empty else 0

        if avg_vol > 0:
            ratio = today_vol / avg_vol
            result["volume_ratio"] = round(ratio, 2)
            if ratio >= 1.5:
                result["volume_signal"] = "HIGH_VOLUME"
            elif ratio < 0.5:
                result["volume_signal"] = "LOW_VOLUME"

    except Exception as e:
        log.warning(f"Volume signal failed for {ticker}: {e}")
    return result


# ======================================================================================================================
# Price momentum
# ======================================================================================================================

def compute_price_momentum(hist_5m: pd.DataFrame) -> dict:
    """
    Intraday price momentum — % from open, distance from high/low.
    """
    result = {
        "pct_from_open":   None,
        "pct_from_high":   None,
        "pct_from_low":    None,
        "intraday_trend":  None,   # UP, DOWN, FLAT
    }
    try:
        if hist_5m.empty or len(hist_5m) < 2:
            return result

        open_price  = float(hist_5m["Open"].iloc[0])
        close_price = float(hist_5m["Close"].iloc[-1])
        high_price  = float(hist_5m["High"].max())
        low_price   = float(hist_5m["Low"].min())

        result["pct_from_open"] = round((close_price - open_price) / open_price * 100, 2)
        result["pct_from_high"] = round((close_price - high_price) / high_price * 100, 2)
        result["pct_from_low"]  = round((close_price - low_price)  / low_price  * 100, 2)

        # Intraday trend from first half vs second half of session
        mid  = len(hist_5m) // 2
        if mid > 0:
            first_half  = float(hist_5m["Close"].iloc[:mid].mean())
            second_half = float(hist_5m["Close"].iloc[mid:].mean())
            if second_half > first_half * 1.002:
                result["intraday_trend"] = "UP"
            elif second_half < first_half * 0.998:
                result["intraday_trend"] = "DOWN"
            else:
                result["intraday_trend"] = "FLAT"

    except Exception as e:
        log.warning(f"Price momentum failed: {e}")
    return result


# ======================================================================================================================
# BB position (intraday)
# ======================================================================================================================

def compute_bb_position(closes: pd.Series, period: int = 20) -> dict:
    """
    Where is price within the Bollinger Bands?
    %B = (price - lower) / (upper - lower)
    %B > 1 = above upper band (overbought intraday)
    %B < 0 = below lower band (oversold intraday)
    """
    result = {"bb_pct_b": None, "bb_position": None}
    try:
        if len(closes) < period:
            return result

        sma   = closes.rolling(period).mean()
        std   = closes.rolling(period).std()
        upper = sma + 2 * std
        lower = sma - 2 * std

        price = float(closes.iloc[-1])
        u     = float(upper.iloc[-1])
        l     = float(lower.iloc[-1])

        if u != l:
            pct_b = (price - l) / (u - l)
            result["bb_pct_b"]   = round(pct_b, 3)
            if pct_b > 1.0:
                result["bb_position"] = "ABOVE_UPPER"
            elif pct_b > 0.8:
                result["bb_position"] = "NEAR_UPPER"
            elif pct_b < 0.0:
                result["bb_position"] = "BELOW_LOWER"
            elif pct_b < 0.2:
                result["bb_position"] = "NEAR_LOWER"
            else:
                result["bb_position"] = "MIDDLE"

    except Exception as e:
        log.warning(f"BB position failed: {e}")
    return result


# ======================================================================================================================
# Master intraday scan — one instrument
# ======================================================================================================================

def scan_intraday(ticker: str) -> dict:
    """
    Run full intraday technical analysis for one instrument.
    Uses 5-minute candles for the current session.

    Returns a comprehensive technical picture used by the US Monitor
    to decide whether to hold, tighten stops, or flag for early exit.
    """
    result = {
        "ticker":          ticker,
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "rsi":             None,
        "rsi_signal":      None,    # OVERBOUGHT / OVERSOLD / NEUTRAL
        "macd":            {},
        "vwap":            {},
        "volume":          {},
        "momentum":        {},
        "bb":              {},
        "overall_signal":  "NEUTRAL",
        "hold_flag":       True,    # False = consider early exit
        "alert":           "",
    }

    yticker = YAHOO_MAP.get(ticker, ticker)

    try:
        t      = yf.Ticker(yticker)
        hist   = t.history(period="1d",  interval="5m")
        hist_h = t.history(period="5d",  interval="1h")

        if hist.empty:
            log.warning(f"No intraday data for {ticker}")
            return result

        closes_5m = hist["Close"]
        if hist_h.empty:
            # 1h data unavailable — do not fall back to 5m data. RSI/MACD
            # computed on 5m bars gives 10× more reactive signals than intended
            # (14-period = 1.2h instead of 14h). Return without RSI/MACD rather
            # than produce signals from the wrong timeframe silently.
            log.warning(f"Intraday 1h data unavailable for {ticker} — RSI/MACD skipped")
            closes_1h = None
        else:
            closes_1h = hist_h["Close"]

        # RSI on 1h for smoother signal
        if closes_1h is not None and len(closes_1h) >= 14:
            rsi = compute_rsi(closes_1h)
            result["rsi"] = rsi
            if rsi >= 75:
                result["rsi_signal"] = "OVERBOUGHT"
            elif rsi <= 25:
                result["rsi_signal"] = "OVERSOLD"
            else:
                result["rsi_signal"] = "NEUTRAL"

        # MACD on 1h
        if closes_1h is not None and len(closes_1h) >= 35:
            result["macd"] = compute_macd(closes_1h)

        # VWAP on 5m (intraday)
        result["vwap"]     = compute_vwap(hist)

        # Volume
        result["volume"]   = compute_volume_signal(ticker, hist)

        # Price momentum
        result["momentum"] = compute_price_momentum(hist)

        # BB position on 5m
        if len(closes_5m) >= 20:
            result["bb"] = compute_bb_position(closes_5m)

        # Overall signal and hold/exit logic
        bull_signals = 0
        bear_signals = 0

        if result["rsi_signal"] == "OVERSOLD":        bull_signals += 1
        if result["rsi_signal"] == "OVERBOUGHT":      bear_signals += 1
        if result["macd"].get("momentum") == "BULLISH": bull_signals += 1
        if result["macd"].get("momentum") == "BEARISH": bear_signals += 1
        if result["vwap"].get("position") == "ABOVE":  bull_signals += 1
        if result["vwap"].get("position") == "BELOW":  bear_signals += 1
        if result["momentum"].get("intraday_trend") == "UP":   bull_signals += 1
        if result["momentum"].get("intraday_trend") == "DOWN": bear_signals += 1

        if bull_signals >= 3:
            result["overall_signal"] = "BULLISH"
        elif bear_signals >= 3:
            result["overall_signal"] = "BEARISH"

        # Hold flag — consider early exit if:
        alerts = []
        if result["rsi"] and result["rsi"] > 78:
            alerts.append(f"RSI extremely overbought ({result['rsi']})")
            result["hold_flag"] = False
        if result["macd"].get("crossover") == "BEARISH":
            alerts.append("MACD bearish crossover")
            result["hold_flag"] = False
        if result["vwap"].get("position") == "BELOW" and result["momentum"].get("intraday_trend") == "DOWN":
            alerts.append("Below VWAP and trending down")
            result["hold_flag"] = False
        if result["bb"].get("bb_position") == "ABOVE_UPPER" and bear_signals >= 2:
            alerts.append("Above upper BB with bearish signals")
            result["hold_flag"] = False
        if result["volume"].get("volume_signal") == "LOW_VOLUME":
            alerts.append("Low volume — weak conviction")

        result["alert"] = " | ".join(alerts) if alerts else ""

    except Exception as e:
        log.error(f"Intraday scan failed for {ticker}: {e}")

    return result


# ======================================================================================================================
# US Monitor — scan all open positions + watch list
# ======================================================================================================================

# Non-equity members of SESSION_INSTRUMENTS["US_OPEN"] — excluded from the HVF
# equity watch (HVF is a stock pattern; these are index / commodity / crypto / FX).
US_NON_EQUITY = {"SPX500", "XAUUSD", "OIL", "BTCUSD", "ETHUSD", "XRPUSD", "SOLUSD", "BNBUSD"}


def hvf_watch_us_equities(open_tickers: set, notify_slack: bool = True) -> list:
    """
    Run the multi-timeframe HVF scan over the US EQUITIES already in our list
    (SESSION_INSTRUMENTS["US_OPEN"] minus index/commodity/crypto) and surface
    tradeable + developing funnels to #signals.

    This is the always-on HVF VISIBILITY layer for the US Monitor so a funnel on
    one of our equities is never silently missed — including when the daily trade
    cap is hit or the macro gate is closed (when Part 2 does not scan/trade).
    Trading still happens in run_us_monitor Part 2 (HVF is a primary in
    scan_instrument). Uses the same rigorous get_hvf_signal_mtf as the daily HVF
    report. Caller gates cadence (every ~30 min) to bound Yahoo load.
    """
    from price_action import get_hvf_signal_mtf, get_trend_structure
    from config import SESSION_INSTRUMENTS, HVF_MIN_RR

    equities = [t for t in SESSION_INSTRUMENTS.get("US_OPEN", [])
                if t not in US_NON_EQUITY and t not in (open_tickers or set())]

    tradeable, developing = [], []
    for ticker in equities:
        try:
            trend = get_trend_structure(ticker)
            hvf   = get_hvf_signal_mtf(ticker, trend_hint=trend)
            if not hvf.get("hvf_type"):
                continue
            hvf["ticker"] = ticker
            sig = hvf.get("hvf_signal", "")
            rr  = hvf.get("risk_reward") or 0
            if sig in ("READY", "TRIGGERED") and rr >= HVF_MIN_RR:
                tradeable.append(hvf)
            elif sig == "DEVELOPING":
                developing.append(hvf)
            time.sleep(0.3)   # polite to Yahoo Finance
        except Exception as e:
            log.warning(f"HVF watch failed for {ticker}: {e}")

    rank = {"TRIGGERED": 3, "READY": 2, "DEVELOPING": 1}
    tradeable.sort(key=lambda r: (rank.get(r.get("hvf_signal", ""), 0),
                                  r.get("pattern_quality", 0)), reverse=True)

    # ── IG validation for UK tradeable setups (user 2026-06-12: IG is the
    # arbiter — Yahoo LSE wicks contain phantom prints). Weight-ordered first
    # so the best setups get the allowance; capped to protect the 10,000/week
    # budget. Mismatches are demoted to DEVELOPING. US tickers skip (clean feed).
    from price_action import validate_hvf_with_ig
    _validated = 0
    _still = []
    for r in tradeable:
        if r.get("ticker", "").endswith(".L") and _validated < 10:
            r = validate_hvf_with_ig(r["ticker"], r)
            _validated += 1
        if r.get("hvf_signal") in ("READY", "TRIGGERED"):
            _still.append(r)
        else:
            developing.append(r)
    tradeable = _still

    developing.sort(key=lambda r: r.get("risk_reward") or 0, reverse=True)
    log.info(f"HVF watch (US equities): {len(equities)} scanned, "
             f"{len(tradeable)} tradeable, {len(developing)} developing")

    if notify_slack and (tradeable or developing):
        _post_hvf_watch(tradeable, developing, HVF_MIN_RR)
        if tradeable:
            _generate_x_drafts(tradeable)
    return tradeable


def _hvf_fingerprint(tradeable: list, developing: list) -> str:
    """
    Stable fingerprint of the current HVF watch state.
    Changes when any instrument is added/removed or its signal, R:R (1dp),
    entry level (2dp), stop or target changes. Developing list is included
    so additions/removals there also trigger a post.
    """
    import hashlib, json

    def _item(r):
        return (
            r.get("ticker", ""),
            r.get("hvf_type", ""),
            r.get("hvf_signal", ""),
            round(r.get("risk_reward") or 0, 1),
            round(r.get("h3_level") or 0, 2),
            round(r.get("stop_level") or 0, 2),
            round(r.get("target") or 0, 2),
        )

    payload = {
        "t": sorted([_item(r) for r in tradeable]),
        "d": sorted([_item(r) for r in developing]),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _hvf_last_fingerprint() -> str:
    """Read the last-posted HVF watch fingerprint from DB. Returns '' on any error."""
    try:
        conn = _pool_get_db()
        rows = conn.run(
            "SELECT fingerprint FROM hvf_watch_state WHERE key = 'us_equities' LIMIT 1"
        )
        conn.close()
        return rows[0][0] if rows else ""
    except Exception as e:
        log.debug(f"hvf_last_fingerprint read failed: {e}")
        return ""


def _hvf_save_fingerprint(fp: str):
    """Upsert the current HVF watch fingerprint into DB."""
    try:
        conn = _pool_get_db()
        conn.run(
            """INSERT INTO hvf_watch_state (key, fingerprint, posted_at)
               VALUES ('us_equities', :fp, now())
               ON CONFLICT (key) DO UPDATE
               SET fingerprint = EXCLUDED.fingerprint,
                   posted_at   = EXCLUDED.posted_at""",
            fp=fp,
        )
        conn.close()
    except Exception as e:
        log.warning(f"hvf_save_fingerprint failed (non-critical): {e}")


def _post_hvf_watch(tradeable: list, developing: list, min_rr: float):
    """
    Post the HVF equity-watch to #claude-trading-signals.

    Deduplication: compute a fingerprint of the current tradeable+developing
    lists. If it matches the last-posted fingerprint stored in hvf_watch_state,
    send a short "No changes" notice instead of repeating the full list.
    The fingerprint changes when any instrument is added/removed or its signal,
    R:R, entry, stop or target changes.
    """
    import requests
    from notify import fmt
    slack_url = os.environ.get("SLACK_SIGNALS", "")
    if not slack_url:
        return

    now_str = datetime.now(timezone.utc).strftime("%d %b %H:%M UTC")

    # ── Deduplication check ───────────────────────────────────────────────────────────────────────────────────────────
    current_fp  = _hvf_fingerprint(tradeable, developing)
    last_fp     = _hvf_last_fingerprint()

    if current_fp == last_fp:
        # Nothing changed — send a lightweight "no change" notice and return
        blocks = [
            {"type": "header",
             "text": {"type": "plain_text", "text": "🌀 HVF Watch — US Equities (US Monitor)"}},
            {"type": "section",
             "text": {"type": "mrkdwn",
                      "text": (f"*No changes in the latest period.*\n"
                               f"_{len(tradeable)} tradeable, {len(developing)} developing — "
                               f"unchanged since last post._")}},
            {"type": "context",
             "elements": [{"type": "mrkdwn",
                            "text": f"Checked {now_str} | no figure changes detected"}]},
        ]
        try:
            requests.post(slack_url, json={"blocks": blocks}, timeout=10)
        except Exception as e:
            log.error(f"HVF watch (no-change) post failed: {e}")
        return

    # ── Full update — something changed ───────────────────────────────────────────────────────────────────────────────
    def _line(r):
        d  = "🟢" if r.get("hvf_type") == "BULLISH" else "🔴"
        s  = {"TRIGGERED": "⚡", "READY": "✅", "DEVELOPING": "👀"}.get(r.get("hvf_signal", ""), "")
        rr = r.get("risk_reward")
        tf = (r.get("hvf_timeframe", "") or "").replace("daily-", "d")
        return (f"{d}{s} *{fmt(r['ticker'])}*  R:R {f'{rr:.1f}:1' if rr else '—'}  "
                f"entry {r.get('h3_level')}  stop {r.get('stop_level')}  "
                f"target {r.get('target')}  [{tf}]")

    # Weight order (user 2026-06-11: all lists in weight order): TRIGGERED before
    # READY, then quality desc, then R:R desc — matters because lists cap at 15.
    from price_action import hvf_weight
    def _weight(r):
        return hvf_weight(r.get("hvf_signal"),
                          r.get("hvf_quality") or r.get("pattern_quality"),
                          r.get("risk_reward"))

    text = ""
    if tradeable:
        text += f"*⚡ Tradeable HVF on our US equities — {len(tradeable)} (R:R ≥ {min_rr:.0f}:1)*\n"
        text += "\n".join(_line(r) for r in sorted(tradeable, key=_weight)[:15]) + "\n\n"
    if developing:
        text += f"*👀 Developing HVF — {len(developing)} (watch, R:R < {min_rr:.0f}:1)*\n"
        text += "\n".join(_line(r) for r in sorted(developing, key=_weight)[:15])

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": "🌀 HVF Watch — US Equities (US Monitor)"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": text.strip()[:2900]}},
        {"type": "context",
         "elements": [{"type": "mrkdwn",
                        "text": "_Trading runs via the monitor signal stack; this is the HVF visibility layer._ | "
                                + now_str}]},
    ]
    try:
        requests.post(slack_url, json={"blocks": blocks}, timeout=10)
        _hvf_save_fingerprint(current_fp)
    except Exception as e:
        log.error(f"HVF watch post failed: {e}")


# Human-readable signal state labels (no pattern name) — shared by the Slack
# draft text and the post card renderer.
_SIG_LABEL = {
    "TRIGGERED": "breaking out",
    "READY":     "coiled, ready",
    "DEVELOPING": "compressing",
}

# ── Tweet phrasing rotation (user 2026-06-13) ─────────────────────────────────────────────────────────────────────────
# Vary the opening hook and the plain-English squeeze description so consecutive posts
# don't read identically. Rotated by batch position + day-of-year (_x_rotation_index),
# keeping the MEANING fixed — state (breaking out vs coiled) and direction (higher vs
# lower). "{cash}" is filled with the $cashtag (".L" stripped for UK names).
_X_HOOKS = {
    # Keyed by (direction, signal) — a bearish setup must NOT get a bullish hook
    # (📈 "breaking out" on a breakdown). READY hooks are direction-neutral.
    ("BULLISH", "TRIGGERED"): [
        "🚨 Breakout: {cash}", "⚡ {cash} on the move", "🚨 {cash} just triggered",
        "📈 {cash} breaking out now", "🔥 {cash} is going", "👀 {cash} just cleared resistance",
        "📈 {cash} popping higher", "⚡ Squeeze fired — {cash}", "🚀 {cash} breaking higher",
        "📈 {cash} off the launchpad", "🟢 {cash} triggered long", "⚡ {cash} breaking out"],
    ("BEARISH", "TRIGGERED"): [
        "🚨 Breakdown: {cash}", "⚡ {cash} on the move", "🚨 {cash} just triggered",
        "📉 {cash} breaking down now", "🔻 {cash} rolling over", "👀 {cash} just lost support",
        "📉 {cash} cracking lower", "⚡ Squeeze fired — {cash}", "📉 {cash} breaking down",
        "🔻 {cash} under pressure", "🔴 {cash} triggered short", "⚡ {cash} losing the floor"],
    ("BULLISH", "READY"):     [
        "👀 Watching {cash}", "👀 On the radar: {cash}", "⏳ {cash} coiling up",
        "👀 {cash} setting up", "🧭 {cash} on the watchlist", "⏳ {cash} winding tighter",
        "👀 Keep an eye on {cash}", "🔍 {cash} building a base", "⏳ {cash} loading up",
        "👀 {cash} primed", "🧭 {cash} one to watch", "⏳ {cash} coiling under resistance"],
    ("BEARISH", "READY"):     [
        "👀 Watching {cash}", "👀 On the radar: {cash}", "⏳ {cash} coiling up",
        "👀 {cash} setting up", "🧭 {cash} on the watchlist", "⏳ {cash} winding tighter",
        "👀 Keep an eye on {cash}", "🔍 {cash} rounding over", "⏳ {cash} losing steam",
        "👀 {cash} primed to roll", "🧭 {cash} one to watch", "⏳ {cash} capped at resistance"],
}
_X_DESC = {
    ("BULLISH", "TRIGGERED"): [
        "Volatility squeeze breaking out higher", "Tight coil firing to the upside",
        "Compression giving way — pushing higher", "Squeeze released, momentum turning up",
        "A long range just snapped to the upside", "Coiled tight, now breaking the ceiling",
        "Volatility squeeze resolving higher", "The lid's off — pushing higher",
        "Pressure released to the upside", "Range broken, buyers in control",
        "The ceiling finally gave way", "Broke out of a long, quiet base",
        "Buyers took the level — eyes on follow-through", "Cleared resistance as the squeeze let go",
        "Months of coiling, now a clean break up", "Quiet accumulation, then a push through the top",
        "Stepped out of the range to the upside", "Resolved up after a patient build"],
    ("BULLISH", "READY"):     [
        "Volatility squeeze coiled, ready higher", "Compression building — primed for an upside break",
        "Coiling tight, loaded to the upside", "Range tightening, leaning higher",
        "Winding into a tight spring, bias up", "Pressure building under the ceiling",
        "Squeezing tighter, upside break in view", "Energy coiling for a move higher",
        "Narrowing range, watching for the pop", "Tightening up, ready to run higher",
        "Quiet base, coiled and leaning up", "Buyers absorbing supply under the ceiling",
        "Higher lows pressing into resistance", "A spring winding just below a breakout line",
        "Range contracting, demand quietly building", "Sitting under the lid, coiled to go",
        "Patient base forming, bias to the upside", "Volatility drained — watching for the spark up"],
    ("BEARISH", "TRIGGERED"): [
        "Volatility squeeze breaking down lower", "Tight coil cracking to the downside",
        "Compression giving way — pressing lower", "Squeeze released, momentum turning down",
        "A long range just snapped to the downside", "Coiled tight, now losing the floor",
        "Volatility squeeze resolving lower", "Support's gone — pressing lower",
        "Pressure released to the downside", "Range broken, sellers in control",
        "The floor finally gave way", "Broke down out of a long, quiet base",
        "Sellers took the level — eyes on follow-through", "Lost support as the squeeze let go",
        "Months of coiling, now a clean break down", "Quiet distribution, then a drop through support",
        "Stepped out of the range to the downside", "Resolved down after a patient roll-over"],
    ("BEARISH", "READY"):     [
        "Volatility squeeze coiled, ready lower", "Compression building — primed for a downside break",
        "Coiling tight, loaded to the downside", "Range tightening, leaning lower",
        "Winding into a tight spring, bias down", "Pressure building under support",
        "Squeezing tighter, downside break in view", "Energy coiling for a move lower",
        "Narrowing range, watching for the drop", "Tightening up, ready to roll lower",
        "Quiet top, coiled and leaning down", "Sellers capping every bounce under resistance",
        "Lower highs pressing toward support", "A spring winding just above a breakdown line",
        "Range contracting, supply quietly building", "Sitting on the floor, coiled to drop",
        "Patient top forming, bias to the downside", "Volatility drained — watching for the crack lower"],
}
# Plain-English explanation of the PRIMARY signal (the squeeze) so a reader with no
# system knowledge understands what's happening — added to the tweet body (user
# 2026-06-13: "very little explanation of primary/confirmation signals"). Rotated and
# direction+state aware; kept short (~75 chars) so it coexists with confirmations.
_X_EXPLAIN = {
    ("BULLISH", "TRIGGERED"): [
        "A tight coil just broke out the top — momentum often follows the break.",
        "Range squeezed shut, now releasing upward as buyers clear the ceiling.",
        "Compression resolved to the upside; squeeze breakouts like this can run.",
        "Buyers cleared the ceiling after a long squeeze; watching for follow-through.",
        "After weeks of coiling, price has pushed through the top of the range.",
        "The range got tighter and tighter — and it's just given way to the upside.",
        "Sellers ran out of room and buyers broke it out; these moves can extend.",
        "A long, narrowing range has snapped higher — the kind of move that trends.",
        "Weeks of compression, now a clean break above resistance.",
        "The squeeze has fired upward — energy released after a long build-up.",
    ],
    ("BULLISH", "READY"): [
        "Range is winding tighter; a push above the ceiling is the trigger to watch.",
        "Coiling into a tight squeeze — a break higher would confirm the move.",
        "Energy building in a narrowing range; bias is up on a break above.",
        "Tightening toward a decision point, leaning higher — not triggered yet.",
        "Price is compressing under resistance; a close above it sets it off.",
        "The range keeps narrowing — watching for the break to the upside.",
        "A tight base is forming; an upside break would be the signal.",
        "Pressure's building below the ceiling, ready to release higher.",
        "Still coiling — the higher the lows hold, the closer the upside break.",
        "Wound up tight under resistance; a push through is what to watch.",
    ],
    ("BEARISH", "TRIGGERED"): [
        "A tight coil just broke down through support — moves like this often extend.",
        "Range squeezed shut, now releasing downward as sellers clear the floor.",
        "Compression resolved to the downside; squeeze breakdowns like this can run.",
        "Sellers cleared the floor after a long squeeze; watching for follow-through.",
        "After weeks of coiling, price has cracked below the range.",
        "The range got tighter and tighter — and it's just given way to the downside.",
        "Buyers ran out of room and sellers broke it down; these moves can extend.",
        "A long, narrowing range has snapped lower — the kind of move that trends.",
        "Weeks of compression, now a clean break below support.",
        "The squeeze has fired downward — pressure released after a long build-up.",
    ],
    ("BEARISH", "READY"): [
        "Range is winding tighter; a drop below support is the trigger to watch.",
        "Coiling into a tight squeeze — a break lower would confirm the move.",
        "Energy building in a narrowing range; bias is down on a break below.",
        "Tightening toward a decision point, leaning lower — not triggered yet.",
        "Price is compressing on support; a close below it sets it off.",
        "The range keeps narrowing — watching for the break to the downside.",
        "A tight top is forming; a downside break would be the signal.",
        "Pressure's building above the floor, ready to release lower.",
        "Still coiling — the lower the highs cap, the closer the downside break.",
        "Wound up tight on support; a drop through is what to watch.",
    ],
}


def _x_rotation_index(rank: int) -> int:
    """Rotation offset for tweet phrasing (user 2026-06-13): batch position +
    day-of-year, so consecutive posts in a run differ and the same setup varies
    day to day. Callers take this modulo the template-list length."""
    import time as _t
    return (rank - 1) + _t.gmtime().tm_yday


def _bold_italic(s: str) -> str:
    """Map ASCII letters to Unicode Mathematical Bold Italic so the text shows as
    bold-italic on X (plain tweets have no markdown). NOTE: these are supplementary-
    plane glyphs — screen readers may skip them and X counts each as 2 chars."""
    out = []
    for c in s:
        o = ord(c)
        if 65 <= o <= 90:      out.append(chr(0x1D468 + o - 65))   # A–Z
        elif 97 <= o <= 122:   out.append(chr(0x1D482 + o - 97))   # a–z
        else:                  out.append(c)
    return "".join(out)


def _x_weighted_len(s: str) -> int:
    """Approximate X's weighted character count: supplementary-plane code points
    (emoji hooks, the bold-italic disclaimer) count as 2, everything else as 1 —
    models the 280-char limit far better than len() for our content."""
    return sum(2 if ord(c) > 0xFFFF else 1 for c in s)


# Disclaimer in bold italic, preceded by a blank line (user 2026-06-13). Plain ASCII
# is kept here for readability; rendered to Unicode bold-italic once at import.
_NFA_DISCLAIMER = "\n\n" + _bold_italic("Not financial advice.")


def _competitor_news(ticker: str, peer: str = None):
    """Recent headline for the ticker (user 2026-06-21), PREFERRING one that mentions the peer/
    competitor — the 'why is the rival taking share' narrative. SLACK-ONLY (internal): it may name
    the publisher, and is never put on an X tweet. Returns 'headline — Publisher' or None. Uses the
    free yfinance .news feed (format varies across versions, so resolve fields defensively)."""
    try:
        import yfinance as yf
        items = yf.Ticker(ticker).news or []

        def _fields(it):
            c = it.get("content", it) if isinstance(it, dict) else {}
            title = c.get("title") or ""
            prov = c.get("provider")
            pub = prov.get("displayName") if isinstance(prov, dict) else (c.get("publisher") or "")
            return title.strip(), (pub or "").strip()

        cands = [(_t, _p) for _t, _p in (_fields(i) for i in items) if _t]
        if not cands:
            return None
        if peer:
            pl = peer.lower()
            for _t, _p in cands:
                if pl in _t.lower():
                    return f"{_t} — {_p}" if _p else _t
        _t, _p = cands[0]
        return f"{_t} — {_p}" if _p else _t
    except Exception:
        return None


def _competitor_angle(ticker: str):
    """Curated-peer competitive angle for the tweet (user 2026-06-21). Names the top peer (a
    COMPETITOR — not a data source, so allowed on X) with relative ~3-month performance, e.g.
    'outpacing peer LULU (+8% vs -3%, 3mo)'. Peer is NOT a $cashtag (X allows only one cashtag and
    the hook already uses $TICKER). Returns (full, short) or None. From config's
    curated COMPETITOR_MAP + a price fetch — no news API (a headline layer can come later)."""
    from config import COMPETITOR_MAP
    peers = COMPETITOR_MAP.get((ticker or "").upper())
    if not peers:
        return None
    peer = peers[0]
    try:
        import yfinance as yf
        data = yf.download([ticker, peer], period="3mo", interval="1d",
                           progress=False, auto_adjust=True)["Close"]
        if data is None or data.empty:
            return None

        def _ret(col):
            s = data[col].dropna()
            return (s.iloc[-1] / s.iloc[0] - 1) * 100 if len(s) > 1 else None
        rt, rp = _ret(ticker), _ret(peer)
        if rt is None or rp is None:
            return None
        # NB: peer is written WITHOUT a $ cashtag. X allows only ONE cashtag per post, and the
        # hook already uses $TICKER — a second cashtag ($PEER) makes X reject the tweet with
        # "Posts are limited to a maximum of one cashtag" (the 2026-06-21 NKE 403s). Plain name only.
        peer_disp = peer[:-2] if peer.endswith(".L") else peer
        # Say WHAT the comparison is ON (user 2026-06-24: "ahead of them on what?") — 3-month share
        # price performance. (Capitalisation is applied later in _just_line.)
        full  = f"{'outpacing' if rt > rp else 'lagging'} peer {peer_disp} on 3-mo price ({rt:+.0f}% vs {rp:+.0f}%)"
        short = f"{'ahead of' if rt > rp else 'behind'} {peer_disp} on 3-mo price"   # compact, survives the 280-char trim
        return (full, short)
    except Exception:
        return None


def _analyst_angle(ticker: str, direction: str = ""):
    """Analyst stance OVER TIME for the tweet (user 2026-06-22: "analysts over time ... helps knit
    together the appearance of BULL and BEAR"). Shows the current buy/hold split, how the buy count
    has MOVED over ~3 months, and the mean price target vs spot — so a BEARISH HVF on a name analysts
    still rate 'buy' reads as a genuine, explained divergence (NKE: HVF bear, analysts cooling but
    target still above). Returns (full, short) or None. yfinance only; no source named on X.

    NB: no $cashtag (X allows one, used by the hook). US-equity-centric — returns None when a ticker
    has no analyst coverage (UK .L, commodities, FX)."""
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        rec = tk.recommendations
        if rec is None or len(rec) == 0:
            return None
        rec = rec.reset_index(drop=True)

        def _row(period):
            m = rec[rec["period"] == period]
            return m.iloc[0] if len(m) else None
        cur = _row("0m")
        old = _row("-3m")
        if cur is None:
            cur = rec.iloc[0]
        if old is None:
            old = rec.iloc[-1]   # oldest available as the "3mo ago" baseline

        def _buys(row): return int(row.get("strongBuy", 0) or 0) + int(row.get("buy", 0) or 0)
        def _holds(row): return int(row.get("hold", 0) or 0)
        def _sells(row): return int(row.get("sell", 0) or 0) + int(row.get("strongSell", 0) or 0)
        cur_b, cur_h, cur_s = _buys(cur), _holds(cur), _sells(cur)
        old_b = _buys(old)
        if (cur_b + cur_h + cur_s) == 0:
            return None

        # Direction of the rating drift over the window (buys rising / cooling / steady).
        if   cur_b > old_b: trend = f"strengthening (buys {old_b}→{cur_b}, 3mo)"
        elif cur_b < old_b: trend = f"cooling (buys {old_b}→{cur_b}, 3mo)"
        else:               trend = "steady (3mo)"

        # Mean target vs spot — the bull case in one number.
        info = _yf_info(ticker)
        tgt  = info.get("targetMeanPrice")
        px   = info.get("currentPrice") or info.get("regularMarketPrice")
        tgt_str = ""
        if isinstance(tgt, (int, float)) and isinstance(px, (int, float)) and px:
            tgt_str = f"; avg target {(tgt/px - 1) * 100:+.0f}%"

        full  = f"Analysts {cur_b} buy / {cur_h} hold, {trend}{tgt_str}"
        short = f"Analysts {cur_b}B/{cur_h}H, {'cooling' if cur_b < old_b else ('rising' if cur_b > old_b else 'steady')}"
        return (full, short)
    except Exception:
        return None


def _tf_desc(tf_raw: str) -> str:
    """Human-readable timeframe description for tweets and cards."""
    mapping = {"30d": "30-day", "60d": "60-day", "90d": "90-day",
               "180d": "6-month", "240d": "long-term", "weekly": "weekly"}
    return mapping.get(tf_raw, tf_raw or "multi-month")


_YF_INFO_CACHE: dict = {}    # ticker -> yfinance .info, fetched once per process


def _yf_info(ticker: str) -> dict:
    """
    yfinance .info for a ticker, fetched ONCE per process and memoised.

    .info is a slow (~1-2s) network round-trip, and the X-card path reads it
    three times for the same instrument — full name (_resolve_name), listing
    exchange (_exchange_tag) and P/E (render_x_post_card). Sharing one fetch
    cuts ~2 of every 3 .info calls per card with no behaviour change (.info is
    static company metadata). Returns {} on any failure — every caller already
    treats absent fields as optional.
    """
    if ticker in _YF_INFO_CACHE:
        return _YF_INFO_CACHE[ticker]
    info = {}
    try:
        import yfinance as _yf
        info = _yf.Ticker(YAHOO_MAP.get(ticker, ticker)).info or {}   # map FX/indices (user 2026-06-23)
    except Exception:
        pass
    _YF_INFO_CACHE[ticker] = info
    return info


_YF_3YW_CACHE: dict = {}   # ticker -> 3y weekly history DataFrame, fetched once per process


def _despike_weekly(df, thresh: float = 0.15):
    """Remove ISOLATED bad weekly prints from a yfinance history DataFrame before it is plotted.

    Illiquid UK investment trusts (e.g. $JIGI / JII.L) get spurious yfinance weekly bars — a single
    week pops ~+20% then fully reverses the next, typically on holiday / quarter-end low-liquidity
    weeks. A NAV-tracking trust does not make 20% weekly round-trips, so these are data artifacts that
    made the 3-yr history look wildly volatile (user 2026-06-24). A bar whose Close deviates by more
    than `thresh` from BOTH neighbours in the SAME direction is treated as a spike and its OHLC is
    replaced with the neighbour average. Edge bars (first/last) are left as-is. Never raises — returns
    the input unchanged on any error or when nothing looks spiky."""
    try:
        if df is None or getattr(df, "empty", True) or len(df) < 3:
            return df
        import numpy as _np
        vals = df["Close"].squeeze().astype(float).to_numpy()
        bad = _np.zeros(len(vals), dtype=bool)
        for i in range(1, len(vals) - 1):
            p, c, n = vals[i - 1], vals[i], vals[i + 1]
            if p <= 0 or n <= 0 or c <= 0:
                continue
            dp = c / p - 1.0
            dn = c / n - 1.0
            if (dp > thresh and dn > thresh) or (dp < -thresh and dn < -thresh):
                bad[i] = True
        if not bad.any():
            return df
        out = df.copy()
        idx = out.index
        _price_fields = ("Open", "High", "Low", "Close")
        for pos in _np.where(bad)[0]:
            lbl, plbl, nlbl = idx[pos], idx[pos - 1], idx[pos + 1]
            for col in out.columns:
                field = col[0] if isinstance(col, tuple) else col
                if field in _price_fields:
                    out.loc[lbl, col] = (out.loc[plbl, col] + out.loc[nlbl, col]) / 2.0
        return out
    except Exception:
        return df


def _yf_weekly_3y(ticker: str):
    """3-year WEEKLY history, fetched ONCE per ticker per process with one retry (user 2026-06-22).

    The 3-year price history is needed in three places per publication — the card's 3-yr inset, the
    standalone 3-yr PNG, and the long-report chart story — and was being fetched separately each time.
    Three (plus the detection weekly scan) yfinance round-trips per instrument is a rate-limit source,
    which silently dropped the card's 3-yr inset (best-effort try/except). Caching to one shared fetch
    (only SUCCESSES are cached, so a transient failure is retried by the next caller) restores it.
    Returns a DataFrame (possibly None/empty) — never raises."""
    if ticker in _YF_3YW_CACHE:
        return _YF_3YW_CACHE[ticker]
    df = None
    _yt = YAHOO_MAP.get(ticker, ticker)   # FX/indices need the Yahoo symbol (USDJPY -> USDJPY=X)
    for _attempt in range(2):
        try:
            import yfinance as _yf
            df = _yf.Ticker(_yt).history(period="3y", interval="1wk")
            if df is not None and not df.empty:
                df = _despike_weekly(df)     # strip isolated bad weekly prints (user 2026-06-24)
                _YF_3YW_CACHE[ticker] = df   # cache successes only
                return df
        except Exception:
            df = None
        import time as _t
        _t.sleep(1.0)
    return df



def _resolve_name(ticker: str) -> str:
    """Full instrument name for a ticker — delegates to the SINGLE source of truth
    instrument_name.company_name (user 2026-06-24: "the correct name should only be in one place").
    Falls back to the bare ticker. (Was a separate yfinance-first resolver; consolidated so the
    dossier / tweets / alerts can no longer disagree — e.g. MSTR showing the Morningstar AU ETF.)"""
    from instrument_name import company_name
    return company_name(ticker) or ticker


_EXCHANGE_TAGS: dict = {}


def _exchange_tag(ticker: str) -> str:
    """Listing-exchange hashtag from yfinance (cached per process): NYQ→#NYSE,
    NMS→#NASDAQ, LSE→#LSE (user 2026-06-13: correct listing exchange)."""
    if ticker in _EXCHANGE_TAGS:
        return _EXCHANGE_TAGS[ticker]
    tag = None
    try:
        ex = (_yf_info(ticker).get("exchange") or "").upper()
        tag = {"NMS": "#NASDAQ", "NGM": "#NASDAQ", "NCM": "#NASDAQ", "NGS": "#NASDAQ",
               "NYQ": "#NYSE", "PCX": "#NYSE", "ASE": "#NYSE", "LSE": "#LSE"}.get(ex)
    except Exception:
        pass
    _EXCHANGE_TAGS[ticker] = tag or ("#LSE" if ticker.endswith(".L") else "#NYSE")
    return _EXCHANGE_TAGS[ticker]


def _x_market_tags(r: dict) -> str:
    """Market + country hashtags (user 2026-06-13). UK names use their index
    (#FTSE100/#FTSE250); US names use the REAL listing exchange (#NASDAQ/#NYSE), not the
    S&P bucket. Country #UK/#USA."""
    idx = r.get("index") or ""
    if idx in ("FTSE 100", "FTSE 250"):
        return ("#FTSE100" if idx == "FTSE 100" else "#FTSE250") + " #UK"
    if (r.get("ticker") or "").endswith(".L"):
        return "#FTSE #UK"
    return f"{_exchange_tag(r.get('ticker') or '')} #USA"


def upload_png_to_slack(png_bytes: bytes, filename: str, title: str,
                        channel_id: str, bot_token: str = "") -> bool:
    """SINGLE SOURCE OF TRUTH for posting a PNG into a Slack channel via the
    current external-upload flow (legacy files.upload was retired 2025):
      1. files.getUploadURLExternal  → one-time upload URL + file id
      2. POST raw bytes to that URL
      3. files.completeUploadExternal → finalise + share into the channel
    Returns True on success; never raises (logged). bot_token defaults to the
    SLACK_BOT_TOKEN env var. Used by _generate_x_drafts (card + 3yr history) and
    social_monitor._run_dossier_to_signals (dossier card)."""
    import requests   # module has no top-level requests import; needed since this fn was extracted
    bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
    if not (png_bytes and bot_token and channel_id):
        return False
    try:
        hdrs = {"Authorization": f"Bearer {bot_token}"}
        r1 = requests.post(
            "https://slack.com/api/files.getUploadURLExternal",
            headers=hdrs,
            data={"filename": filename, "length": len(png_bytes)},
            timeout=10,
        ).json()
        if not r1.get("ok"):
            raise RuntimeError(f"getUploadURLExternal: {r1.get('error')}")
        requests.post(r1["upload_url"], data=png_bytes, timeout=30).raise_for_status()
        r3 = requests.post(
            "https://slack.com/api/files.completeUploadExternal",
            headers={**hdrs, "Content-Type": "application/json"},
            json={"files": [{"id": r1["file_id"], "title": title}],
                  "channel_id": channel_id},
            timeout=10,
        ).json()
        if not r3.get("ok"):
            raise RuntimeError(f"completeUploadExternal: {r3.get('error')}")
        log.info(f"PNG attached to Slack ({len(png_bytes)} bytes -> {channel_id}): {filename}")
        return True
    except Exception as e:
        log.error(f"Slack PNG upload failed ({filename}): {e}")
        return False


def render_x_post_card(r: dict):
    """
    Render the user-approved X post card (2026-06-10) for one tradeable HVF row
    and return PNG bytes (None on failure — logged, never raises).

    SINGLE SOURCE OF TRUTH for the card: used by _generate_x_drafts (Slack
    upload) and generate_x_cards.py (local files when Slack upload is
    unavailable). Card: tweet-text header panel (@EndToEndTrading, $TICKER
    (Name), setup, levels, hashtags, "Not financial advice.") above the price
    chart with red H1→H2→H3 / green L1→L2→L3 funnel and full-width
    entry/stop/target lines with right-edge labels.

    Expects the dict produced by get_hvf_signal_mtf + scan metadata: ticker,
    name, hvf_type, hvf_signal, hvf_timeframe, h3_level, stop_level, target,
    risk_reward, h1..l3 _level/_date.
    """
    import io
    import base64                      # noqa: F401  (callers b64-encode; kept for parity)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import pandas as pd
    import yfinance as _yf
    from datetime import datetime, timezone, timedelta

    ticker    = r.get("ticker", "")
    direction = r.get("hvf_type", "BULLISH")
    signal    = r.get("hvf_signal", "")
    h3        = r.get("h3_level")
    stop      = r.get("stop_level")
    target    = r.get("target")
    rr        = r.get("risk_reward")
    tf_raw    = (r.get("hvf_timeframe", "") or "").replace("daily-", "d")
    name      = r.get("name") or _resolve_name(ticker)
    disp_ticker = ticker[:-2] if ticker.endswith(".L") else ticker   # strip ".L" (user 2026-06-13)

    dir_word  = "higher" if direction == "BULLISH" else "lower"
    rr_str    = f"{rr:.1f}:1" if rr else "—"
    h3_str    = f"{h3:.2f}" if h3 else "—"        # 2dp on the card (user 2026-06-21)
    stop_str  = f"{stop:.2f}" if stop else "—"
    tgt_str   = f"{target:.2f}" if target else "—"
    sig_desc  = _SIG_LABEL.get(signal, signal.lower())

    try:
        end_dt   = datetime.now(timezone.utc)
        # Fetch from 14 days before the oldest pivot so H1/L1 are visible.
        # Fall back to 90 days when no pivot dates are present.
        _oldest_pivot_date = min(
            (pd.Timestamp(r[k]) for k in
             ("h1_date", "h2_date", "h3_date", "l1_date", "l2_date", "l3_date")
             if r.get(k)),
            default=None
        )
        if _oldest_pivot_date is not None:
            # Pivot dates from the scan are tz-naive; end_dt is UTC-aware.
            # Localise before any comparison — naive vs aware raises
            # TypeError (every chart failed in run 27368931212).
            if _oldest_pivot_date.tzinfo is None:
                _oldest_pivot_date = _oldest_pivot_date.tz_localize("UTC")
            start_dt = _oldest_pivot_date - timedelta(days=14)
            # Always fetch back to the OLDEST pivot so the price line reaches the funnel jaws — old
            # pivots are valid HVF points (user 2026-06-24), so a price line that stops short of them
            # leaves empty axis on the left ($LAND: a daily setup with ~22-month-old pivots). The old
            # timeframe-gated cap (1150d weekly / 365d else) truncated any non-"weekly"-tagged setup to
            # 365d while still plotting 22-month-old pivots -> left-edge white space. Now a SINGLE
            # absolute safety cap (1500d ~ 4y, daily downloads are cheap) bounds huge fetches without
            # cutting before the oldest pivot. Minimum 30 days.
            _ABS_CAP_DAYS = 1500
            start_dt = max(start_dt, end_dt - timedelta(days=_ABS_CAP_DAYS))
            start_dt = min(start_dt, end_dt - timedelta(days=30))
        else:
            start_dt = end_dt - timedelta(days=90)
        # Map to the Yahoo symbol (user 2026-06-23): FX/indices (USDJPY -> USDJPY=X, JPN225 -> ^N225)
        # 404 on the raw ticker, which left those tweets with NO chart at all.
        _yt = YAHOO_MAP.get(ticker, ticker)
        hist = _yf.download(_yt, start=start_dt.strftime("%Y-%m-%d"),
                            end=end_dt.strftime("%Y-%m-%d"),
                            progress=False, auto_adjust=True)
        if hist is None or hist.empty:
            return None

        # yfinance returns MultiIndex columns for a single ticker, so
        # hist["Close"] is a DataFrame — squeeze to a Series ONCE here.
        # float(DataFrame.median()) raised TypeError and every chart
        # failed in run 27370959365.
        close = hist["Close"].squeeze()

        # Standard trader support/resistance (user 2026-06-19; window widened to 60 bars
        # 2026-06-20): the swing low is support, the swing high is resistance, over the last ~60
        # sessions. A 20-bar window on a tight coil gave an absurd ~1% S/R band ("Sup 286 / Res
        # 288.5"); 60 bars reflects real structural levels. Raw chart units; ×ig_scale to display.
        sup_raw = res_raw = None
        try:
            _rc = hist.tail(60)
            sup_raw = float(_rc["Low"].squeeze().min())
            res_raw = float(_rc["High"].squeeze().max())
        except Exception:
            pass

        # ig_scale normalisation
        ig_scale = 1.0
        if h3:
            yf_med = float(close.median())
            if yf_med > 0 and h3 / yf_med > 5:
                ig_scale = h3 / yf_med

        def _s(v):
            return v / ig_scale if v else None

        h3_p   = _s(h3)
        stop_p = _s(stop)
        targ_p = _s(target)
        h1_p   = _s(r.get("h1_level"))
        h2_p   = _s(r.get("h2_level"))
        l1_p   = _s(r.get("l1_level"))
        l2_p   = _s(r.get("l2_level"))
        l3_p   = _s(r.get("l3_level") or stop)   # l3 = stop base

        # Convert pivot date strings to datetime for plotting
        def _pdt(key):
            ds = r.get(key)
            if not ds:
                return None
            try:
                return pd.Timestamp(ds)
            except Exception:
                return None

        h1_dt = _pdt("h1_date"); h2_dt = _pdt("h2_date"); h3_dt = _pdt("h3_date")
        l1_dt = _pdt("l1_date"); l2_dt = _pdt("l2_date"); l3_dt = _pdt("l3_date")

        dates = hist.index

        # ── Agreed X post card format (user-approved 2026-06-10) ──────────────────────────────────────────────────────
        # Combined card: tweet-text header panel above the chart.
        fig = plt.figure(figsize=(12, 8.5))
        fig.patch.set_facecolor("#0d1117")
        ax = fig.add_axes([0.05, 0.06, 0.83, 0.62])
        ax.set_facecolor("#0d1117")

        dir_arrow = "▲" if direction == "BULLISH" else "▼"
        # Explicit BULL/BEAR at the top (user 2026-06-22) — the arrow alone wasn't clear enough.
        dir_label = "BULLISH" if direction == "BULLISH" else "BEARISH"
        dir_color = "#3fb950" if direction == "BULLISH" else "#f85149"
        # 52-week high/low for the card (user 2026-06-13) — prices live on the
        # PNG, not in the tweet. Dedicated 1y fetch; ×ig_scale to match the
        # level units (same convention as "Now").
        wk52_str = ""
        wk52_high_raw = None   # chart-unit 52w high, for the gridline (user 2026-06-13)
        try:
            _tk  = _yf.Ticker(YAHOO_MAP.get(ticker, ticker))
            _y52 = _tk.history(period="1y", interval="1d")
            if _y52 is not None and not _y52.empty:
                wk52_high_raw = float(_y52["High"].max())
                wk52_str = (f"52w High: {wk52_high_raw * ig_scale:g}   "
                            f"52w Low: {float(_y52['Low'].min()) * ig_scale:g}")
            # P/E ratio (user 2026-06-13) — FORWARD first (price ÷ projected EPS, the
            # growth/breakout framing), falling back to trailing. yfinance .info can be
            # slow/flaky so isolate it; absent or non-positive (no/neg earnings) → omitted.
            # "◆" is a DejaVu-safe marker (colour emoji don't render here).
            try:
                _info = _yf_info(ticker)
                _pe = _info.get("forwardPE") or _info.get("trailingPE")
                if isinstance(_pe, (int, float)) and _pe > 0:
                    from config import MARKET_PE   # P/E vs broad market (user 2026-06-21)
                    _pv = "rich" if _pe > MARKET_PE * 1.15 else ("cheap" if _pe < MARKET_PE * 0.85 else "in-line")
                    wk52_str = (wk52_str + "   " if wk52_str else "") + f"◆ P/E {_pe:.1f} ({_pv} vs ~{MARKET_PE:g} mkt)"
            except Exception:
                pass
        except Exception:
            pass

        # Sector name on the card (user 2026-06-19) — from yfinance .info (cached);
        # appended to the squeeze description. Omitted if unavailable.
        _sector = ""
        try:
            _sector = (_yf_info(ticker).get("sector") or "").strip()
        except Exception:
            pass
        _desc_line = f"Volatility squeeze {sig_desc} {dir_word}" + (f"   ·   {_sector}" if _sector else "")

        # Timeframe label (e.g. d240) deliberately NOT shown (user 2026-06-13).
        # No @EndToEndTrading handle on the card (user 2026-06-15: remove the brand text).
        hdr_lines = [
            (0.925, f"{dir_arrow} {dir_label}  ·  ${disp_ticker} ({name})",
                                         dir_color, 16, "bold",   "normal"),
            (0.888, _desc_line,
                                         "#c9d1d9", 13, "normal", "normal"),
            # Levels line (y=0.852) is drawn separately below with per-marker colours.
            (0.818, wk52_str,            "#8b949e", 11, "normal", "normal"),
            (0.784, f"#StockAlert #TechnicalAnalysis #{disp_ticker} #Trading",
                                         "#8b949e", 11, "normal", "normal"),
            (0.750, "Not financial advice.",
                                         "#8b949e", 10, "normal", "italic"),
        ]
        for hy, htxt, hcol, hsize, hweight, hstyle in hdr_lines:
            fig.text(0.05, hy, htxt, color=hcol, fontsize=hsize,
                     fontweight=hweight, style=hstyle,
                     ha="left", va="top")

        # ── Levels line with colour-coded markers (user 2026-06-13) ───────────────────────────────────────────────────
        # Drawn as sequential segments so each marker carries its own colour: ◎ entry
        # gold, ● stop red, ▲ target green, ⚖ R:R neutral (DejaVu-safe glyphs — colour
        # emoji don't render in the card font). Segment widths are measured via the Agg
        # renderer to place them left-to-right (one fig.text can only be a single colour).
        _now_v = f"{float(close.dropna().iloc[-1]) * ig_scale:.2f}"   # last non-NaN (user 2026-06-27: SBUX now=nan)
        # Support/resistance after R:R on the top line (user 2026-06-19): support green,
        # resistance red. '—' when the recent-swing fetch failed.
        _sup_v = f"{sup_raw * ig_scale:.2f}" if sup_raw else "—"
        _res_v = f"{res_raw * ig_scale:.2f}" if res_raw else "—"
        _level_segs = [
            (f"Now {_now_v}    ",   "#c9d1d9"),
            (f"◎ Entry {h3_str}",   "#e3b341"),   # gold — matches the entry line
            ("    ",                "#c9d1d9"),
            (f"● Stop {stop_str}",  "#f85149"),   # red
            ("    ",                "#c9d1d9"),
            (f"▲ Target {tgt_str}", "#3fb950"),   # green
            ("    ",                "#c9d1d9"),
            (f"⚖ R:R {rr_str}",     "#c9d1d9"),
            ("    ",                "#c9d1d9"),
            (f"Support {_sup_v}",   "#3fb950"),   # support green (user 2026-06-21: full word)
            ("    ",                "#c9d1d9"),
            (f"Resistance {_res_v}", "#f85149"),  # resistance red (full word)
        ]
        _lx = 0.05
        _renderer = fig.canvas.get_renderer()
        _figw = fig.bbox.width
        for _seg_txt, _seg_col in _level_segs:
            _t = fig.text(_lx, 0.852, _seg_txt, color=_seg_col, fontsize=12,
                          ha="left", va="top")
            _lx += _t.get_window_extent(renderer=_renderer).width / _figw

        # Price line + fill
        ax.plot(dates, close, color="#58a6ff", linewidth=1.6, zorder=3)
        ax.fill_between(dates, close, float(close.min()), alpha=0.07,
                        color="#58a6ff", zorder=2)

        # ── Funnel: upper jaw H1→H2→H3 (red), lower jaw L1→L2→L3 (green) ──────────────────────────────────────────────
        upper_pts = [(dt, lv) for dt, lv in
                     [(h1_dt, h1_p), (h2_dt, h2_p), (h3_dt, h3_p)]
                     if dt is not None and lv is not None]
        lower_pts = [(dt, lv) for dt, lv in
                     [(l1_dt, l1_p), (l2_dt, l2_p), (l3_dt, l3_p)]
                     if dt is not None and lv is not None]

        if len(upper_pts) >= 2:
            ux, uy = zip(*upper_pts)
            ax.plot(ux, uy, color="#f85149", linewidth=1.4,
                    linestyle="--", alpha=0.9, zorder=4)
            ax.scatter(ux, uy, color="#f85149", s=26, zorder=5, alpha=0.95)

        if len(lower_pts) >= 2:
            lx, ly = zip(*lower_pts)
            ax.plot(lx, ly, color="#3fb950", linewidth=1.4,
                    linestyle="--", alpha=0.9, zorder=4)
            ax.scatter(lx, ly, color="#3fb950", s=26, zorder=5, alpha=0.95)

        # ── Entry / stop / target: full-width lines + right-edge labels ───────────────────────────────────────────────
        # Right-edge labels show the % from the live price (user 2026-06-19) — the numeric
        # level is already on the top line, so showing the % here adds info without clipping
        # the figure edge. Falls back to the value when the % can't be computed.
        from price_action import pct_from_current
        _cur_sig = float(close.dropna().iloc[-1]) * ig_scale   # last non-NaN (user 2026-06-27: SBUX now=nan)
        def _rl(name, p, val_str):
            s = pct_from_current(p, _cur_sig)
            return f"{name} {s}" if s else f"{name} {val_str}"
        trans = ax.get_yaxis_transform()
        if h3_p:
            ax.axhline(h3_p, color="#e3b341", linewidth=1.2,
                       linestyle="--", alpha=0.9, zorder=4)
            ax.text(1.01, h3_p, _rl("Entry", h3, h3_str), transform=trans,
                    color="#e3b341", fontsize=9, va="center")
        if stop_p:
            ax.axhline(stop_p, color="#f85149", linewidth=1.0,
                       linestyle=":", alpha=0.9, zorder=4)
            ax.text(1.01, stop_p, _rl("Stop", stop, stop_str), transform=trans,
                    color="#f85149", fontsize=9, va="center")
        if targ_p:
            ax.axhline(targ_p, color="#3fb950", linewidth=1.0,
                       linestyle=":", alpha=0.9, zorder=4)
            ax.text(1.01, targ_p, _rl("Target", target, tgt_str), transform=trans,
                    color="#3fb950", fontsize=9, va="center")

        # ── 52-week high gridline (user 2026-06-13): context for the target — shows
        # whether the target has room before the year's high or breaks to new highs.
        # Drawn in chart units (wk52_high_raw); label in signal units (× ig_scale) to
        # match the Entry/Stop/Target labels. Skipped if implausibly far above the
        # action so it can't squash the price line.
        _ylevels = [v for v in (float(close.min()), float(close.max()),
                                stop_p, targ_p, h3_p) if v]
        _data_max = max([float(close.max())] + [v for v in (targ_p, h3_p) if v])
        if wk52_high_raw is not None and wk52_high_raw <= _data_max * 1.6:
            ax.axhline(wk52_high_raw, color="#a371f7", linewidth=1.0,
                       linestyle=(0, (4, 3)), alpha=0.85, zorder=4)
            ax.text(1.01, wk52_high_raw, f"52w High {wk52_high_raw * ig_scale:g}",
                    transform=trans, color="#a371f7", fontsize=9, va="center")
            _ylevels.append(wk52_high_raw)
        # Frame the y-axis to all relevant levels so neither the target nor the 52w
        # line is clipped and the price action isn't squashed.
        if _ylevels:
            _ymin, _ymax = min(_ylevels), max(_ylevels)
            _pad = (_ymax - _ymin) * 0.06 or 1.0
            ax.set_ylim(_ymin - _pad, _ymax + _pad)

        # ── 3-year history inset (user 2026-06-21: "show the 3yr price history ... gradually
        # falling") — a small weekly-close sparkline so the long-term trend is visible at a glance.
        # Top-right of the chart (empty space in a downtrend); green if up over 3yr, red if down.
        try:
            _h3 = _yf_weekly_3y(ticker)
            _c3 = _h3["Close"].dropna() if _h3 is not None and not _h3.empty else None
            if _c3 is not None and len(_c3) > 8:
                _pct3 = (float(_c3.iloc[-1]) / float(_c3.iloc[0]) - 1) * 100
                _axin = fig.add_axes([0.655, 0.595, 0.21, 0.072])
                _axin.set_facecolor("#0d1117")
                _axin.plot(range(len(_c3)), _c3.values,
                           color="#3fb950" if _pct3 >= 0 else "#f85149", linewidth=1.1)
                _axin.set_title(f"3-yr history  {_pct3:+.0f}%", color="#8b949e", fontsize=8, pad=2)
                _axin.set_xticks([]); _axin.set_yticks([])
                for _sp in _axin.spines.values():
                    _sp.set_edgecolor("#30363d")
        except Exception:
            pass

        # Adaptive tick density: a multi-month/year window (weekly setups) with a
        # 1-month locator crowds 24+ labels. Scale the interval to the span so a
        # ~2-year weekly chart shows readable quarterly-ish ticks (user 2026-06-23).
        _span_days = max(1, (dates[-1] - dates[0]).days)
        _mo_interval = 1 if _span_days <= 200 else (2 if _span_days <= 450 else 3)
        ax.xaxis.set_major_formatter(
            mdates.DateFormatter("%b %d" if _span_days <= 200 else "%b %y"))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=_mo_interval))
        plt.setp(ax.get_xticklabels(), rotation=0,
                 color="#8b949e", fontsize=9)
        plt.setp(ax.get_yticklabels(), color="#8b949e", fontsize=9)
        for spine in ax.spines.values():
            spine.set_edgecolor("#30363d")
        ax.tick_params(colors="#8b949e")

        # ── VWAP logic caption (user 2026-06-13): the plain-English "why it confirms"
        # lives HERE on the card (the tweet keeps just the short "Above/Below VWAP").
        # Shown only when the day's VWAP position aligns with the trade direction —
        # sourced from signal_log.vwap_position via the caller; absent → no caption.
        _vwap_pos = (r.get("vwap_position") or "").upper()
        _vwap_pct = r.get("vwap_pct")
        _pct_txt  = f", {_vwap_pct:+.1f}%" if isinstance(_vwap_pct, (int, float)) else ""
        _vwap_logic = ""
        if direction == "BULLISH" and _vwap_pos == "ABOVE":
            _vwap_logic = (f"VWAP: price above the day's volume-weighted average{_pct_txt} — "
                           "buyers paying up, demand aggressive → confirms the long")
        elif direction == "BEARISH" and _vwap_pos == "BELOW":
            _vwap_logic = (f"VWAP: price below the day's volume-weighted average{_pct_txt} — "
                           "sellers pressing, demand weak → confirms the short")
        if _vwap_logic:
            fig.text(0.05, 0.015, _vwap_logic, color="#8b949e", fontsize=8.5,
                     style="italic", ha="left", va="bottom")

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=140, facecolor="#0d1117")
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        log.warning(f"X post card render failed for {ticker}: {e}")
        return None


def render_3yr_history_card(r: dict):
    """Standalone 3-YEAR price-history PNG (user 2026-06-21) — an EXTRA visual for the SLACK
    publication (not used on X). Weekly closes over ~3 years so the long-term trend is obvious,
    with the current price marked and (if in range) the funnel entry/target for context. Returns
    PNG bytes, or None on failure."""
    ticker = r.get("ticker", "")
    name   = r.get("name") or _resolve_name(ticker)
    disp   = ticker[:-2] if ticker.endswith(".L") else ticker
    try:
        import io
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        _h = _yf_weekly_3y(ticker)   # shared cached fetch (user 2026-06-22)
        c = _h["Close"].dropna() if _h is not None and not _h.empty else None
        if c is None or len(c) < 8:
            return None
        pct = (float(c.iloc[-1]) / float(c.iloc[0]) - 1) * 100
        col = "#3fb950" if pct >= 0 else "#f85149"
        fig, ax = plt.subplots(figsize=(10, 4.4))
        fig.patch.set_facecolor("#0d1117"); ax.set_facecolor("#0d1117")
        ax.plot(c.index, c.values, color=col, linewidth=1.8, zorder=3)
        ax.fill_between(c.index, c.values, float(c.min()), alpha=0.08, color=col, zorder=2)
        # current price + (in-range) funnel entry/target context lines
        ax.axhline(float(c.iloc[-1]), color="#c9d1d9", lw=0.9, ls="--", alpha=0.7)
        _lo, _hi = float(c.min()), float(c.max())
        for _lvl, _lc, _lab in ((r.get("h3_level"), "#e3b341", "entry"),
                                (r.get("target"), "#58a6ff", "target")):
            if isinstance(_lvl, (int, float)) and _lo <= _lvl <= _hi:
                ax.axhline(_lvl, color=_lc, lw=0.9, ls=":", alpha=0.7)
                ax.text(c.index[0], _lvl, f" {_lab} {_lvl:.2f}", color=_lc, fontsize=8, va="bottom")
        ax.set_title(f"3-Year Price History  —  ${disp} ({name})   {pct:+.0f}%",
                     color="#ffffff", fontsize=13, weight="bold")
        ax.xaxis.set_major_locator(mdates.YearLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        plt.setp(ax.get_xticklabels(), color="#8b949e", fontsize=9)
        plt.setp(ax.get_yticklabels(), color="#8b949e", fontsize=9)
        for sp in ax.spines.values():
            sp.set_edgecolor("#30363d")
        ax.tick_params(colors="#8b949e")
        fig.text(0.5, 0.01, f"Current {float(c.iloc[-1]):.2f}  ·  3-yr range {_lo:.2f}–{_hi:.2f}",
                 color="#8b949e", fontsize=9, ha="center")
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=140, facecolor="#0d1117", bbox_inches="tight")
        plt.close(fig); buf.seek(0)
        return buf.read()
    except Exception as e:
        log.warning(f"3yr history card render failed for {ticker}: {e}")
        return None


# ── X-draft changed-detection (user 2026-06-17) — only re-show an instrument when its
# CONFIRMATIONS change. Fingerprint = direction + signal + the set of confirmation labels with
# numbers stripped (so a value wiggle e.g. call/put 1.42->1.40 does NOT reshow, but an added /
# removed / flipped confirmation, or a direction/state change, does). State lives in the
# x_draft_state table (created defensively so it works without a separate schema run).
_X_DRAFT_STATE_SQL = ("create table if not exists x_draft_state "
                      "(ticker text primary key, fingerprint text, posted_at timestamptz default now())")


def _levels_fp(h3, stop, target=None) -> str:
    """Changed-detection fingerprint, keyed on ENTRY + STOP only (user 2026-06-20). These are
    the funnel's pivot-derived levels (H3 / L3-based) and are STABLE run-to-run. The TARGET is
    deliberately EXCLUDED: the AMP1 exhaustion re-anchor + IG validation make it wobble slightly
    on every scan, which was flipping the old E|S|T fingerprint and republishing the same setup
    each run (the ABF duplicate). The user's rule is "republish only when the ENTRY changes".
    Stored verbatim (not hashed) so previous levels can be parsed back to show the delta."""
    def _f(v):
        return f"{v:g}" if isinstance(v, (int, float)) else "—"
    return f"E{_f(h3)}|S{_f(stop)}"


def _parse_levels_fp(fp: str) -> dict:
    """Parse a _levels_fp string back to {'E':.., 'S':.., 'T':..} display strings."""
    out = {}
    for part in (fp or "").split("|"):
        if part and part[0] in "EST":
            out[part[0]] = part[1:]
    return out


def _levels_changes_line(prev_fp: str, h3, stop, target) -> str:
    """Human 'what moved since last publication' line (user 2026-06-19) for a
    seen-before instrument being republished because a level changed."""
    prev = _parse_levels_fp(prev_fp)
    cur  = _parse_levels_fp(_levels_fp(h3, stop, target))
    labels = {"E": "Entry", "S": "Stop"}
    # Only E and S are in the fingerprint now (target excluded — it wobbles). Iterating "T" here
    # would show a spurious "Target X -> —" on the one-time migration from the old E|S|T format.
    chips = [f"{labels[k]} {prev.get(k, '—')} → {cur.get(k, '—')}"
             for k in ("E", "S") if prev.get(k, "—") != cur.get(k, "—")]
    return "  ·  ".join(chips)


def _draft_fp_last(ticker: str) -> str:
    try:
        conn = _pool_get_db()
        conn.run(_X_DRAFT_STATE_SQL)
        rows = conn.run("select fingerprint from x_draft_state where ticker = :t", t=ticker)
        conn.close()
        return rows[0][0] if rows else ""
    except Exception as e:
        log.debug(f"draft fp read failed for {ticker}: {e}")
        return ""


def _draft_fp_save(ticker: str, fp: str):
    try:
        conn = _pool_get_db()
        conn.run(_X_DRAFT_STATE_SQL)
        conn.run("insert into x_draft_state (ticker, fingerprint, posted_at) values (:t, :f, now()) "
                 "on conflict (ticker) do update set fingerprint = excluded.fingerprint, "
                 "posted_at = excluded.posted_at", t=ticker, f=fp)
        conn.close()
    except Exception as e:
        log.warning(f"draft fp save failed for {ticker}: {e}")


def _generate_x_drafts(tradeable: list, post: bool = True, collect: bool = False,
                       changed_only: bool = False):
    """
    Post one tweet-ready draft per tradeable instrument to #claude-x-drafts
    (SLACK_TWITTER env var).

    post=False / collect=True: build the SAME tweet text + card PNG for each
    instrument (the exact production path — no format drift) but do NOT post to
    Slack; return a list of dicts
    [{ticker, tweet, png, rank, total, name, direction, signal, rr_str, quality,
      tf_raw, sig_desc}]. Used by instrument_dossier.py to render one
    instrument's X artifacts locally. Both flags can be combined.

    Tweet format (≤280 chars — no pattern name, describe the setup naturally):
        📈 $TICKER (Company) — Volatility squeeze breaking {direction}, {tf} setup
        Entry: {h3}  Stop: {stop}  Target: {target}  R:R {rr}:1
        #StockAlert #TechnicalAnalysis #{TICKER} #Trading

    Chart: 90-day price history with the converging funnel drawn explicitly
    (upper + lower boundary lines narrowing to the breakout point, then
    entry/stop/target projected to the right).
    """
    import requests
    import io
    import base64
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import yfinance as _yf
    from datetime import datetime, timezone, timedelta
    from notify import fmt

    slack_url = os.environ.get("SLACK_TWITTER", "")
    if post and not slack_url:
        log.warning("SLACK_TWITTER not set — X draft reports skipped")
        return

    # Quality gate (user 2026-06-19): don't PUBLISH sub-threshold setups in the X drafts / live-X.
    # The on-demand dossier (collect mode) is exempt — it renders whatever ticker is requested.
    if not collect:
        tradeable = [r for r in tradeable
                     if (r.get("hvf_quality") or r.get("pattern_quality") or 0) >= MIN_PUBLISH_QUALITY]

    # ── Per-market draft selection ───────────────────────────────────────────────────────────────────────────────────
    # Weight order within each market (R:R-first now, then signal, then quality — hvf_weight),
    # grouped by market in MARKET_ORDER, capped at X_DRAFT_PER_MARKET each (user 2026-06-17:
    # X drafts are top-5/market, separate from the analytical report's PER_MARKET_TOP_N). Drafts
    # post in this order with a per-market header so the channel reads as grouped sections.
    # Order by "most relevant for action now" (user 2026-06-22): R:R ÷ distance-to-entry, DESC —
    # so the top-N/market published per market are the highest-R:R, closest-to-trigger setups.
    from price_action import action_score, group_by_market
    _groups  = group_by_market(sorted(tradeable, key=action_score, reverse=True),
                               n=X_DRAFT_PER_MARKET, market_order=MARKET_ORDER)
    _ordered = [r for _, rows in _groups for r in rows]
    # Per-market draft numbering (user 2026-06-17): each market's instruments number from 1
    # (reset when the market changes), with the market named + numbered "(k of K)" in the title.
    from price_action import market_short
    _market_no    = {m: i + 1 for i, (m, _) in enumerate(_groups)}   # market position (k of K)
    _n_markets    = len(_groups)
    _total   = len(_ordered)
    # However #3 (user 2026-06-19): the per-market header shows "top N of M candidates",
    # where M is the FULL candidate count for that market BEFORE the X_DRAFT_PER_MARKET cap.
    _market_total: dict = {}
    for _r in tradeable:
        _mk = _r.get("index") or "?"
        _market_total[_mk] = _market_total.get(_mk, 0) + 1

    # ── Batch fetch latest signal context per ticker from signal_log ──────────────────────────────────────────────────
    # Enriches tweet with options flow / director buy confirmation when available.
    # Fails silently — absence of this data never blocks the draft post.
    _sig_ctx: dict = {}   # ticker → {options_bias, call_put_ratio, director_signal}
    try:
        tickers_in = _ordered   # enrich exactly the drafts we'll post (per-market grouped, user 2026-06-16)
        ticker_list = [r.get("ticker", "") for r in tickers_in if r.get("ticker")]
        if ticker_list:
            placeholders = ", ".join(f"'{t}'" for t in ticker_list)
            conn = _pool_get_db()
            rows = conn.run(f"""
                SELECT DISTINCT ON (ticker)
                       ticker, options_bias, call_put_ratio, iv_rank, director_signal,
                       cot_bias, adx_signal, obv_signal, sector_etf, sector_dir,
                       senate_signal, senate_senator, vwap_position, vwap_pct,
                       analyst_signal, analyst_recommendation
                FROM signal_log
                WHERE ticker IN ({placeholders})
                ORDER BY ticker, session_time DESC
            """)
            conn.close()
            for row in rows:
                _sig_ctx[row[0]] = {
                    "options_bias":    row[1],
                    "call_put_ratio":  row[2],
                    "iv_rank":         row[3],
                    "director_signal": row[4],
                    "cot_bias":        row[5],
                    "adx_signal":      row[6],
                    "obv_signal":      row[7],
                    "sector_etf":      row[8],
                    "sector_dir":      row[9],
                    "senate_signal":   row[10],
                    "senate_senator":  row[11],
                    "vwap_position":   row[12],
                    "vwap_pct":        row[13],
                    "analyst_signal":  row[14],
                    "analyst_recommendation": row[15],
                }
    except Exception as e:
        log.debug(f"X drafts: signal_log lookup failed (non-critical): {e}")

    # _ordered / _total were computed above as a per-market grouped selection (user 2026-06-16:
    # "top 10 by market"): within each market TRIGGERED first, then quality desc, then R:R desc;
    # markets in MARKET_ORDER. Each draft carries its global rank (below) and each market group
    # gets a header, so the channel reads as grouped per-market sections.
    # ── Changed-detection on published LEVELS (user 2026-06-19, Current #3) ───────────────────────────────────────────
    # changed_only (morning report): republish a seen-before instrument ONLY when a level
    # (entry/stop/target) moved since its last publication; show the delta + a "seen before"
    # tag. If NOTHING is new or changed, fall back to re-showing the top set with a
    # "nothing new" banner (However #4) rather than posting an empty channel.
    _change_state: dict = {}   # ticker -> (seen_before, changed, prev_fp, cur_fp)
    for r in _ordered:
        _t   = r.get("ticker", "")
        _cfp = _levels_fp(r.get("h3_level"), r.get("stop_level"), r.get("target"))
        _prev = _draft_fp_last(_t) if changed_only else ""
        _change_state[_t] = (bool(_prev), (_prev != _cfp), _prev, _cfp)
    _nothing_new = False
    if changed_only:
        _nothing_new = not any((not _change_state[r.get("ticker", "")][0])
                               or _change_state[r.get("ticker", "")][1] for r in _ordered)
    # Rows we will actually post: in changed mode, only new/changed instruments — unless
    # nothing is new, in which case re-show everything (the top set) with the banner.
    if changed_only and not _nothing_new:
        _publish = [r for r in _ordered
                    if (not _change_state[r.get("ticker", "")][0])
                    or _change_state[r.get("ticker", "")][1]]
    else:
        _publish = list(_ordered)
    # Numerator of "top N of M candidates" — what is actually being shown, per market.
    _shown_count: dict = {}
    for r in _publish:
        _mk = r.get("index") or "?"
        _shown_count[_mk] = _shown_count.get(_mk, 0) + 1
    # One global "nothing new" banner (However #4) before any market section.
    if post and _nothing_new and slack_url:
        try:
            requests.post(slack_url, json={"blocks": [
                {"type": "divider"},
                {"type": "header", "text": {"type": "plain_text",
                                            "text": "🔁 Shared again as there is nothing new to show"}},
            ]}, timeout=10)
        except Exception as e:
            log.debug(f"X draft nothing-new banner post failed: {e}")

    _last_market = None   # emit one section header per market when posting
    _mkt_idx = 0          # per-market instrument counter (resets when the market changes)
    _collected: list = []   # populated only when collect=True (dossier mode)
    _posted: list = []      # instruments actually posted (returned; morning report picks top-2/market for live X)
    for _rank, r in enumerate(_publish, 1):
        ticker    = r.get("ticker", "")
        direction = r.get("hvf_type", "BULLISH")
        signal    = r.get("hvf_signal", "")
        h3        = r.get("h3_level")
        stop      = r.get("stop_level")
        target    = r.get("target")
        rr        = r.get("risk_reward")
        quality   = r.get("hvf_quality") or r.get("pattern_quality") or ""
        tf_raw    = (r.get("hvf_timeframe", "") or "").replace("daily-", "d")
        name      = r.get("name") or _resolve_name(ticker)

        dir_emoji  = "📈" if direction == "BULLISH" else "📉"
        dir_word   = "higher" if direction == "BULLISH" else "lower"
        rr_str     = f"{rr:.1f}:1" if rr else "—"
        h3_str     = f"{h3:g}" if h3 else "—"
        stop_str   = f"{stop:g}" if stop else "—"
        tgt_str    = f"{target:g}" if target else "—"
        sig_desc   = _SIG_LABEL.get(signal, signal.lower())
        tf_desc    = _tf_desc(tf_raw)
        # Display ticker: strip ".L" from UK names for the cashtag/hashtag (user 2026-06-13).
        disp_ticker = ticker[:-2] if ticker.endswith(".L") else ticker
        # Rotated hook + description (user 2026-06-13) — see _X_HOOKS / _X_DESC. Falls
        # back gracefully if a state/direction combo isn't templated.
        _rot   = _x_rotation_index(_rank)
        _hooks = _X_HOOKS.get((direction, signal)) or _X_HOOKS[("BULLISH", "READY")]
        hook   = _hooks[_rot % len(_hooks)].format(cash=f"${disp_ticker}")
        _descs = _X_DESC.get((direction, signal), [f"Volatility squeeze {sig_desc} {dir_word}"])
        description = _descs[_rot % len(_descs)]
        # Plain-English primary-signal explanation (user 2026-06-13) — rotated; empty
        # for any non-templated state (then the explainer line is simply omitted).
        _expls  = _X_EXPLAIN.get((direction, signal), [""])
        explain = _expls[_rot % len(_expls)]

        # ── Justification line from signal_log — every aligned confirmation in
        # plain English (user 2026-06-11: confirmations must be understandable;
        # no Confs:N counts or NEUTRAL states in tweets). Priority order below;
        # the fitting loop trims from the end until the tweet fits 280 chars.
        ctx    = _sig_ctx.get(ticker, {})
        obs_b  = ctx.get("options_bias") or ""
        cpr    = ctx.get("call_put_ratio")
        ivr    = ctx.get("iv_rank")
        dir_s  = ctx.get("director_signal")
        cot_b  = ctx.get("cot_bias") or ""
        adx_s  = ctx.get("adx_signal") or ""
        obv_s  = ctx.get("obv_signal") or ""
        sec_d  = ctx.get("sector_dir") or ""
        sec_e  = ctx.get("sector_etf") or ""
        sen_s  = ctx.get("senate_signal")
        vwap_p = (ctx.get("vwap_position") or "").upper()
        anal_b = ctx.get("analyst_signal") or ""            # BULLISH / BEARISH / NEUTRAL
        anal_r = ctx.get("analyst_recommendation") or ""    # strong_buy / buy / hold / sell …

        from signals import bias_aligned as _aligned_fn
        def _aligned(bias: str) -> bool:
            return _aligned_fn(bias, direction)   # canonical rule (signals.py)

        justifications = []   # (full, short) — priority order, trimmed from the end
        # Pattern quality first — it scores THE setup being posted (0–100: pivot
        # clarity, funnel symmetry, volume profile). Only shown when strong.
        if quality and isinstance(quality, (int, float)) and quality >= 60:
            justifications.append((f"Pattern quality {quality:.0f}/100",
                                   f"Quality {quality:.0f}/100"))
        # Direct-competitor angle (user 2026-06-21: "do this for all X tweets") — HIGH priority
        # (right after pattern quality) so it reliably survives the 280-char trim, ahead of the
        # generic P/E / insider-ownership filler. Curated peer + relative 3mo performance.
        try:
            _ca = _competitor_angle(ticker)
            if _ca:
                justifications.append(_ca)
        except Exception:
            pass
        # Analyst stance OVER TIME (user 2026-06-22) — HIGH priority so it survives the trim. The
        # buy/hold split + 3-month drift + mean target knits the bull/bear divergence (e.g. a
        # BEARISH HVF on a name analysts still rate buy but are cooling on). yfinance; US-equity only.
        try:
            _aa = _analyst_angle(ticker, direction)
            if _aa:
                justifications.append(_aa)
        except Exception:
            pass
        if obs_b and obs_b != "NEUTRAL" and _aligned(obs_b):
            bits_full, bits_short = [], []
            if cpr is not None:
                bits_full.append(f"call/put {float(cpr):.2f}")
                bits_short.append(f"call/put {float(cpr):.2f}")
            if ivr is not None:
                bits_full.append(f"implied volatility rank {float(ivr):.0f}%")
            detail_full  = f" ({', '.join(bits_full)})"  if bits_full  else ""
            detail_short = f" ({', '.join(bits_short)})" if bits_short else ""
            justifications.append((
                f"Options flow {obs_b.lower()}{detail_full}",
                f"Options flow {obs_b.lower()}{detail_short}",
            ))
        if dir_s:
            justifications.append(("Insider buying on record",
                                   "Insider buying on record"))
        if sen_s and direction == "BULLISH":
            justifications.append(("US Senate-disclosed buying",
                                   "Senate buying"))
        if cot_b and cot_b != "NEUTRAL" and _aligned(cot_b):
            # COT commercials = the system's "smart money" (hedgers in the physical) —
            # labelled as such (user 2026-06-13). Options flow is NOT smart money (mixed
            # institutional + retail sentiment), so only COT carries this tag.
            justifications.append((f"Futures positioning {cot_b.lower()} (COT report, smart money)",
                                   f"COT {cot_b.lower()} (smart money)"))
        # Broker recommendations (user 2026-06-15: confirmations weren't showing broker
        # recommendations — e.g. LGEN). Gate on the RECOMMENDATION matching the trade side
        # (buy → long, sell → short) — not the composite analyst signal, which is often
        # NEUTRAL even on a "buy". "hold"/"none" is not a confirmation.
        _anal_r = (anal_r or "").lower()
        if (direction == "BULLISH" and _anal_r in ("strong_buy",  "buy")) or \
           (direction == "BEARISH" and _anal_r in ("strong_sell", "sell")):
            _rec = _anal_r.replace("_", " ").title()
            justifications.append((f"Brokers rate it {_rec}", f"Brokers: {_rec}"))
        # VWAP — short and direction-aligned only (user 2026-06-13). The detailed
        # plain-English "why it confirms" lives on the PNG card; the tweet carries
        # just the terse tag. ABOVE confirms a long, BELOW confirms a short, so the
        # word always matches the trade side and never decorates the wrong way.
        if (direction == "BULLISH" and vwap_p == "ABOVE") or \
           (direction == "BEARISH" and vwap_p == "BELOW"):
            _vw = "Above VWAP" if direction == "BULLISH" else "Below VWAP"
            justifications.append((_vw, _vw))
        if adx_s == "STRONG_TREND":
            justifications.append(("Strong trend in force (ADX)",
                                   "Strong trend (ADX)"))
        if (direction == "BULLISH" and obv_s in ("BULLISH_DIVERGENCE", "CONFIRMING_BULLISH")) or \
           (direction == "BEARISH" and obv_s in ("BEARISH_DIVERGENCE", "CONFIRMING_BEARISH")):
            justifications.append(("Volume flow backing the move (OBV)",
                                   "Volume backing (OBV)"))
        if sec_d and _aligned(sec_d):
            justifications.append((f"Sector ({sec_e}) moving the same way",
                                   "Sector aligned"))
        # Valuation + ownership context (user 2026-06-19) — LOWEST priority, appended last so
        # the fitting loop includes them only when the 280-char tweet has room (they never crowd
        # out the higher-priority signals above). From yfinance .info (cached; the card already
        # fetched it). NB the source provider is NEVER named in the tweet (user 2026-06-19).
        try:
            _info_x = _yf_info(ticker)
            _pe = _info_x.get("forwardPE") or _info_x.get("trailingPE")
            if isinstance(_pe, (int, float)) and 0 < _pe < 200:
                # P/E relative to the broad market (user 2026-06-21): tag cheap/in-line/rich vs
                # config.MARKET_PE so the number has context, not just a bare multiple.
                from config import MARKET_PE
                _val = "rich" if _pe > MARKET_PE * 1.15 else ("cheap" if _pe < MARKET_PE * 0.85 else "in-line")
                justifications.append((f"P/E {_pe:.1f}, {_val} vs ~{MARKET_PE:g} mkt",
                                       f"P/E {_pe:.1f} ({_val})"))
            _ins = _info_x.get("heldPercentInsiders")
            if isinstance(_ins, (int, float)) and _ins > 0:
                justifications.append((f"Insider ownership {_ins * 100:.0f}%",
                                       f"Insiders {_ins * 100:.0f}%"))
        except Exception:
            pass

        def _just_line(use_full: bool, n: int) -> str:
            idx = 0 if use_full else 1
            # Each confirmation reads as its own sentence — capitalise the first letter (user
            # 2026-06-24: "ahead of MCD" should be "Ahead of MCD"). Leaves digits/$cashtags as-is.
            def _cap(s: str) -> str:
                return s[0].upper() + s[1:] if s else s
            return "  ·  ".join(_cap(j[idx]) for j in justifications[:n])

        # Changed-detection (user 2026-06-19, Current #3): the _publish set was already
        # filtered to new/changed instruments above. Here we just pull this ticker's state
        # to (a) tag a republished instrument "seen before" and (b) show what level moved.
        _seen, _changed, _prev_fp, _fp = _change_state.get(
            ticker, (False, True, "", _levels_fp(h3, stop, target)))
        _changes_line = _levels_changes_line(_prev_fp, h3, stop, target) if (_seen and _changed) else ""

        # ── Tweet text — try progressively shorter versions to fit 280 chars ──
        # Lead with the rotated HOOK, then the rotated description (user 2026-06-13).
        # Prices (Now/Entry/Stop/Target/R:R) and the HVF timeframe are NOT in the
        # tweet text — they live on the attached PNG card. The tweet is hook +
        # description + clear-English confirmations only.
        # Base variants in PRIORITY order: the company name is NON-NEGOTIABLE (user 2026-06-24:
        # "$SBUX" went out with no "(Starbucks Corporation)"). It stays adjacent to the $cashtag on
        # EVERY primary variant, so to fit 280 we trim CONFIRMATIONS first (the n_just loop below),
        # then the explainer (base_with_name) — never the name. (This reverses the old "explanation
        # matters more than the long name" rule, which let the fitter silently strip the name on any
        # longer tweet.) Whatever the lead can't fit — the explainer and the dropped confirmations —
        # still rides the long-report thread that publishes beneath the lead, so no detail is lost.
        # The nameless base_no_name survives only as the pathological absolute fallback below (used
        # only if even name+desc cannot fit 280).
        # Blank line after the hook/company line (user 2026-06-16). The description and the
        # explainer stay together as ONE paragraph; the blank line before the confirmations
        # block is added in _build below.
        # Direction AND state at the very top, in WORDS (user 2026-06-22: the ⏳/👀 hook icon wasn't
        # clear — "use words"). The heading states both; the hook's leading state icon is stripped to
        # plain text so the tweet reads "BEARISH · not triggered yet / $NKE (NIKE, Inc.) winding tighter".
        import re as _re
        _state = "triggered now" if signal == "TRIGGERED" else "not triggered yet"
        _dir_word = "BEARISH" if direction == "BEARISH" else "BULLISH"
        _dir_tag = f"{_dir_word} setup · {_state}"
        # Keep the $cashtag and the full name ADJACENT (user 2026-06-22): the rotated hook puts a
        # comment around the cashtag — insert the name right AFTER the cashtag so it reads
        # "$NKE (NIKE, Inc.) winding tighter". Then strip the leading state ICON to plain words.
        hook_named = hook.replace(f"${disp_ticker}", f"${disp_ticker} ({name})", 1)
        hook_named = _re.sub(r"^[^\w$#]+", "", hook_named)     # drop the leading ⏳/👀/🚨 icon → words
        hook_plain = _re.sub(r"^[^\w$#]+", "", hook)
        # End the hook line with a full stop so it reads as a sentence (user 2026-06-24: the JIGI
        # "rounding over" hook had no punctuation). Skip if it already ends in . ! or ?.
        def _full_stop(s: str) -> str:
            s = s.rstrip()
            return s if (s and s[-1] in ".!?") else f"{s}."
        hook_named = _full_stop(hook_named)
        hook_plain = _full_stop(hook_plain)
        # Description + explainer are RELATED → one paragraph (user 2026-06-22): no line break
        # between them, and the description (a fragment) gets a full stop so they read as sentences.
        _desc = description.rstrip()
        if _desc and _desc[-1] not in ".!?":
            _desc += "."
        _desc_block = f"{_desc} {explain}".strip() if explain else _desc
        # Instrument on the TOP line, the direction/state tag beneath it (user 2026-06-22).
        base_name_expl = f"{hook_named}\n{_dir_tag}\n\n{_desc_block}\n"   # name + explainer (preferred)
        base_with_name = f"{hook_named}\n{_dir_tag}\n\n{_desc}\n"         # name, explainer dropped to fit
        base_no_name   = f"{hook_plain}\n{_dir_tag}\n\n{_desc}\n"         # ABSOLUTE fallback only (no name)
        # "Not financial advice." — always appended (2026-06-11); now preceded by a
        # blank line and rendered in bold italic (user 2026-06-13).
        disclaimer = _NFA_DISCLAIMER
        # Market + country hashtags (user 2026-06-13): e.g. #FTSE100 #UK, #SP500 #USA.
        _mkt = _x_market_tags(r)
        tags_long  = f"#StockAlert #TechnicalAnalysis #{disp_ticker} {_mkt} #Trading"
        tags_short = f"#StockAlert #TechnicalAnalysis #{disp_ticker} {_mkt}"

        def _build(base, just, tags):
            # Blank line before the confirmations block (e.g. "Pattern quality …") — user 2026-06-16.
            return base + (f"\n{just}\n" if just else "") + tags + disclaimer

        # Within each base, keep as MANY confirmations as possible (n_just descending),
        # full wording before short. The explainer-bearing bases come first so the
        # primary-signal explanation is preferred over extra confirmations / the name.
        # Name-bearing variants ONLY (user 2026-06-24): keep name+explainer if it fits, else
        # name+desc — never a nameless primary variant. The nameless base_no_name is the absolute
        # fallback below, reached only if even name+desc cannot fit 280.
        _bases = ([base_name_expl] if explain else []) + [base_with_name]
        tweet = None
        for base in _bases:
            for n_just in range(len(justifications), -1, -1):
                for use_full in (True, False):
                    just = _just_line(use_full, n_just)
                    for tags in (tags_long, tags_short):
                        candidate = _build(base, just, tags)
                        if _x_weighted_len(candidate) <= 280:   # X-weighted, not len()
                            tweet = candidate
                            break
                    if tweet:
                        break
                if tweet:
                    break
            if tweet:
                break
        if not tweet:
            # Absolute fallback — no justification, short tags
            tweet = base_no_name + tags_short + disclaimer

        # ── Chart: rendered by the shared card renderer (single source of truth) ──────────────────────────────────────
        chart_b64 = None
        # Hand the card the same VWAP position the tweet used, so the card's
        # plain-English VWAP logic and the tweet's short tag always agree
        # (one signal_log source — user 2026-06-13).
        if ctx.get("vwap_position"):
            r["vwap_position"] = ctx.get("vwap_position")
            r["vwap_pct"]      = ctx.get("vwap_pct")
        png = render_x_post_card(r)
        if png:
            chart_b64 = base64.b64encode(png).decode()

        # Tight-stop caution (backlog #9b, user 2026-06-15 — "publish with a caution
        # note"): a funnel whose stop is < TIGHT_STOP_MIN_PCT of price is not auto-traded
        # (structurally untradeable at IG intraday), and its R:R looks inflated BECAUSE the
        # stop is tiny. The draft still posts so you can take a MANUAL trade — with a wider
        # stop / smaller size. This guidance is for you (the manual trader), so it rides in
        # the Slack wrapper, NOT in the 280-char tweet or on the public card.
        caution = ""
        if r.get("tight_stop_intraday"):
            caution = (f"⚠️ Tight stop ({r.get('stop_pct')}% of price) — the R:R is inflated by the "
                       f"tiny stop and IG won't hold it intraday. For a manual trade use a wider stop "
                       f"/ smaller size. Not auto-traded.")

        # Dossier (collect) mode: capture the same artifacts, skip the Slack post.
        if collect:
            _collected.append({
                "ticker": ticker, "tweet": tweet, "png": png,
                "rank": _rank, "total": _total, "name": name,
                "direction": direction, "signal": signal, "rr_str": rr_str,
                "quality": quality, "tf_raw": tf_raw, "sig_desc": sig_desc,
                "caution": caution,
                # All HVF confirmations in full wording (user 2026-06-19, Current #5):
                # the dossier shows every comment; the 280-char tweet only fits a few.
                "justifications": [j[0] for j in justifications],
            })
        if not post:
            continue

        # ── Per-market section header (user 2026-06-16: "top 10 by market") ───────────────────────────────────────────
        # One divider+header message when the market changes, so the channel reads as grouped
        # per-market sections. Same webhook/mechanism as the draft text — content only.
        _mkt = r.get("index") or "?"
        if _mkt != _last_market:
            _last_market = _mkt
            _mkt_idx = 0                       # new market → reset the per-market numbering
            try:
                requests.post(slack_url, json={"blocks": [
                    {"type": "divider"},
                    {"type": "header", "text": {"type": "plain_text",
                                                "text": f"📊 {market_short(_mkt)} ({_market_no.get(_mkt, 1)} of {_n_markets}) "
                                                        f"— top {_shown_count.get(_mkt, 0)} of {_market_total.get(_mkt, 0)} candidates"}},
                ]}, timeout=10)
            except Exception as e:
                log.debug(f"X draft market header post failed for {_mkt}: {e}")
        _mkt_idx += 1                          # 1-based position within this market

        # ── Post to SLACK_TWITTER channel ─────────────────────────────────────────────────────────────────────────────
        dir_label = "Bullish" if direction == "BULLISH" else "Bearish"
        _seen_tag = "  👀 Seen before" if _seen else ""
        # Expected time-to-target (user 2026-06-19) — in the Slack draft wrapper only, NEVER in
        # the tweet text or on the card (those are the public X artifacts).
        from price_action import target_horizon
        _tgt_horizon = target_horizon(r)
        blocks = [
            {"type": "header",
             "text": {"type": "plain_text",
                      "text": f"X Draft {_mkt_idx}/{_shown_count.get(_mkt, _mkt_idx)} · "
                              f"{market_short(_mkt)} ({_market_no.get(_mkt, 1)} of {_n_markets}) — "
                              f"{fmt(ticker)} {dir_label} · {sig_desc.title()}{_seen_tag}"}},
            {"type": "section",
             "text": {"type": "mrkdwn",
                      "text": f"*Tweet ({len(tweet)} chars):*\n```{tweet}```"}},
            {"type": "context",
             "elements": [{"type": "mrkdwn",
                            "text": ((f"Now {r.get('current_price'):g}  |  " if isinstance(r.get('current_price'), (int, float)) else "")
                                     + f"R:R {rr_str}"
                                     + (f" · {_tgt_horizon} to target" if _tgt_horizon else "")
                                     + f"  |  Quality: {quality or '—'}/100  |  "
                                     f"{tf_raw or '—'}  |  "
                                     + datetime.now(timezone.utc).strftime("%d %b %H:%M UTC"))}]},
        ]
        if caution:
            blocks.insert(2, {"type": "section",
                              "text": {"type": "mrkdwn", "text": caution}})
        # Competitor news (user 2026-06-21) — SLACK-ONLY narrative ("why the rival is taking
        # share"); a recent headline preferring a competitor mention. Never on the X tweet/card.
        try:
            from config import COMPETITOR_MAP
            _peers = COMPETITOR_MAP.get((ticker or "").upper())
            _news = _competitor_news(ticker, _peers[0] if _peers else None)
            if _news:
                blocks.insert(2, {"type": "context",
                                  "elements": [{"type": "mrkdwn", "text": f"📰 {_news}"}]})
        except Exception:
            pass
        # Seen-before delta (user 2026-06-19, Current #3): when a previously-published
        # instrument is republished because a level moved, show exactly what moved.
        if _changes_line:
            blocks.insert(2, {"type": "section",
                              "text": {"type": "mrkdwn",
                                       "text": f"👀 *Seen before* — changed since last publication:  {_changes_line}"}})
        bot_token  = os.environ.get("SLACK_BOT_TOKEN", "")
        channel_id = os.environ.get("SLACK_TWITTER_CHANNEL_ID", "")
        if chart_b64 and not (bot_token and channel_id):
            blocks.insert(2, {
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": "_Chart generated but not attached — "
                                 "SLACK_BOT_TOKEN / SLACK_TWITTER_CHANNEL_ID not set_"}
            })

        try:
            requests.post(slack_url, json={"blocks": blocks}, timeout=10)
            log.info(f"X draft posted to SLACK_TWITTER for {ticker} ({len(tweet)} chars)")
        except Exception as e:
            log.error(f"X draft Slack post failed (SLACK_TWITTER) for {ticker}: {e}")

        # ── Attach the post card image + 3yr history via the shared Slack upload helper ───────────────────────────────
        # (Both used to be inline copies of the external-upload flow; deduped into
        # upload_png_to_slack 2026-06-23.)
        if chart_b64 and bot_token and channel_id:
            upload_png_to_slack(base64.b64decode(chart_b64),
                                f"x_post_{ticker.replace('.', '_')}.png",
                                f"X post card — {ticker} ({name})",
                                channel_id, bot_token)

        # Standalone 3-YEAR history PNG (user 2026-06-21) — an EXTRA Slack visual alongside the
        # card (Slack only; never attached to an X tweet).
        if bot_token and channel_id:
            try:
                _hist_png = render_3yr_history_card(r)
            except Exception as e:
                _hist_png = None
                log.error(f"3yr history PNG render failed for {ticker}: {e}")
            if _hist_png:
                upload_png_to_slack(_hist_png,
                                    f"hist3yr_{ticker.replace('.', '_')}.png",
                                    f"3-year history — {ticker}",
                                    channel_id, bot_token)

        # ── Component C: the long quality report (1/n thread) MUST accompany every published
        # instrument (user 2026-06-16). A publication = card + short tweet + long thread; short
        # +PNG without the long report is incomplete. Posted right after the card, same channel.
        try:
            from quality_report import publish_long_report_for
            publish_long_report_for(r)
        except Exception as e:
            log.error(f"long quality report failed for {ticker}: {e}")

        # Record the post: morning report picks top-X_PUBLISH_TOP_N/market of these for live X;
        # changed-detection saves the confirmations fingerprint (user 2026-06-17).
        if post:
            _posted.append(r)
            if changed_only:
                _draft_fp_save(ticker, _fp)

    if collect:
        return _collected
    return _posted


def run_us_monitor(notify_slack: bool = True) -> list:
    """
    Mid-session US Monitor — runs every 15 minutes during the US session.

    Two jobs:
    1. Watch all OPEN POSITIONS — flag deterioration (RSI, MACD, VWAP, volume).
    2. Re-scan ALL SESSION INSTRUMENTS for NEW entries — signals can fire at
       any point in the session, not just at the open. If IBM's BB breakout
       happens at 14:45, this catches it and places a trade.
    """
    import os
    import requests
    import pg8000.native
    from signals import scan_instrument, get_macro_gate
    from ig_shim import (open_trade, get_account_balance,
                         place_hvf_order_from_sig, reconcile_working_orders,
                         calculate_position_size, get_epic)
    from config import SESSION_INSTRUMENTS, MAX_TRADES_PER_SESSION, SESSION_TRADE_CAPS
    from notify import fmt, should_post_summary   # name fmt + 2h summary gate

    results = []

    # ── Reconcile pending HVF working orders first ────────────────────────────────────────────────────────────────────
    # A fill inserts the position row (so the DB fetch below sees it and Part 1
    # monitors it); cancels/expiries are surfaced to Slack — nothing ends silently.
    try:
        wo_sum = reconcile_working_orders()
        if wo_sum["filled"] or wo_sum["cancelled"] or wo_sum["expired"]:
            log.info(f"US Monitor: working orders — filled {wo_sum['filled']}, "
                     f"cancelled {wo_sum['cancelled']}, expired {wo_sum['expired']}")
    except Exception as e:
        log.warning(f"US Monitor: working-order reconcile failed: {e}")

    # ── DB connection ─────────────────────────────────────────────────────────────────────────────────────────────────
    try:
        conn = _pool_get_db()
        pos_rows = conn.run(
            "select ticker, direction, open_price, stop_loss, deal_id from positions"
        )
        # Trades already placed today: closed (trade_log) + still open (positions,
        # previously missing from the count) + pending working orders placed today.
        today_count = conn.run(
            """select
                 (select count(*) from trade_log
                    where session like 'US%' and date(opened_at) = current_date)
               + (select count(*) from positions
                    where session like 'US%' and date(opened_at) = current_date)
               + (select count(*) from working_orders
                    where session like 'US%' and status = 'PENDING'
                      and date(placed_at) = current_date)"""
        )
        conn.close()
    except Exception as e:
        log.error(f"Could not fetch positions: {e}")
        return results

    open_tickers    = {r[0] for r in pos_rows}
    trades_today    = int(today_count[0][0]) if today_count else 0   # US-session trades today
    slots_remaining = max(0, SESSION_TRADE_CAPS.get("US", MAX_TRADES_PER_SESSION) - trades_today)

    # ── Part 1: Monitor existing positions ────────────────────────────────────────────────────────────────────────────
    if not pos_rows:
        log.info("US Monitor: no open positions — skipping position review")

    position_alerts = []
    for row in pos_rows:
        ticker, direction, open_price, stop_loss, deal_id = row
        log.info(f"Scanning intraday: {ticker}")

        scan = scan_intraday(ticker)
        scan["direction"]   = direction
        scan["open_price"]  = float(open_price or 0)
        scan["stop_loss"]   = float(stop_loss  or 0)
        scan["deal_id"]     = deal_id
        results.append(scan)

        # Flag positions that may need attention
        if not scan["hold_flag"] and scan["alert"]:
            position_alerts.append(scan)

    # ── Part 2: Re-scan session instruments for NEW entries ───────────────────────────────────────────────────────────
    # NOTE: HVF watch (visibility layer) is now a separate workflow
    # (trading-us-hvf-watch.yml, session US_HVF_WATCH) running every 2 hours.
    new_trades_placed = 0
    if slots_remaining > 0:
        log.info(f"US Monitor: scanning for new entries ({slots_remaining} slot(s) remaining today)")
        try:
            macro = get_macro_gate("US_MONITOR")
            if macro.get("macro_gate_pass"):
                candidates = [t for t in SESSION_INSTRUMENTS.get("US_OPEN", [])
                              if t not in open_tickers]
                for ticker in candidates:
                    if new_trades_placed >= slots_remaining:
                        break
                    try:
                        sig = scan_instrument(ticker, "US_MONITOR", macro)
                        if sig.get("trade_signal"):
                            from run_session import get_user_profile
                            profile = get_user_profile()

                            # HVF setups → pending working order at the pattern's
                            # exact entry/stop/target (re-signal = amend, never a
                            # duplicate). No fall-through to a market order.
                            _hvf_dir_ok = ((sig.get("hvf_type") == "BULLISH" and sig["direction"] == "BUY") or
                                           (sig.get("hvf_type") == "BEARISH" and sig["direction"] == "SELL"))
                            if _hvf_dir_ok and sig.get("hvf_signal") in ("READY", "TRIGGERED") and \
                                    sig.get("hvf_h3_level") and sig.get("hvf_stop_level") and sig.get("hvf_target"):
                                wo = place_hvf_order_from_sig(
                                    sig, profile, "US_MONITOR",
                                    macro.get("stress_size_multiplier", 1.0))
                                if wo and not wo.get("updated"):
                                    new_trades_placed += 1
                                continue

                            stop_dist  = sig.get("stop_distance", 0)
                            # Margin-AWARE sizing (user 2026-06-13 — DELL was
                            # rejected INSUFFICIENT_FUNDS because this path floored
                            # size at 0.5 and never checked margin). Route through
                            # calculate_position_size, which takes the SMALLER of
                            # the risk-based size and the margin-affordable size,
                            # and returns 0 only when even the minimum deal can't
                            # be margined. No 0.5 floor.
                            try:
                                bal         = get_account_balance()
                                stress_mult = macro.get("stress_size_multiplier", 1.0)
                                risk_amount = bal["available"] * profile["risk_per_trade"] * stress_mult
                                epic        = get_epic(ticker)
                                if epic and stop_dist > 0:
                                    size, stop_dist = calculate_position_size(
                                        epic, stop_dist, risk_amount,
                                        available_funds=bal["available"])
                                else:
                                    size = 0.0
                            except Exception as e:
                                log.warning(f"US Monitor sizing failed for {ticker}: {e}")
                                size = 0.0   # skip on error — never floor to 0.5
                            limit_dist = round(stop_dist * DEFAULT_TARGET_RR, 4)
                            if size <= 0:
                                try:
                                    from notify import alert_missed_trade
                                    alert_missed_trade(
                                        ticker, sig.get("direction", "?"),
                                        "Available margin too small for even the minimum deal size on this "
                                        "instrument — no position placed (no INSUFFICIENT_FUNDS rejection).",
                                        sig.get("signal_summary", ""))
                                except Exception:
                                    pass
                                continue
                            from signals import conf_names
                            _confs = conf_names(sig)
                            signal_str = (
                                f"Options:{sig.get('options_bias','—')} "
                                f"BB:{sig.get('bb_breakout_dir','—')} "
                                f"COT:{sig.get('cot_bias','—')} "
                                f"PA:{sig.get('pa_verdict','—')} "
                                f"Confs:{sig.get('confirmation_count',0)}"
                                + (f" ({_confs})" if _confs else "") +
                                f" [intraday rescan]"
                            )
                            result = open_trade(
                                user_id=profile["user_id"],
                                ticker=ticker,
                                direction=sig["direction"],
                                size=size,
                                stop_distance=stop_dist,
                                limit_distance=limit_dist,
                                session_name="US_MONITOR",
                                signal_summary=signal_str,
                                paper_trade=profile["paper_trade"]
                            )
                            if result:
                                log.info(f"US Monitor NEW TRADE: {ticker} {sig['direction']}")
                                new_trades_placed += 1
                                try:
                                    from trade_email import send_trade_email
                                    send_trade_email(ticker, sig["direction"], sig, result,
                                                     size=size, session_name="US_MONITOR")
                                except Exception as e:
                                    log.warning(f"Trade email failed for {ticker}: {e}")
                    except Exception as e:
                        log.warning(f"Monitor scan failed for {ticker}: {e}")
            else:
                log.info(f"US Monitor: macro gate closed — {macro.get('gate_reason')} — no new entries")
        except Exception as e:
            log.error(f"US Monitor new-entry scan failed: {e}")
    else:
        log.info("US Monitor: daily trade limit reached — no new entries scanned")

    # Send Slack alert for flagged positions
    if position_alerts and notify_slack:
        slack_url = os.environ.get("SLACK_ALERTS", "")
        if slack_url:
            lines = ""
            for s in position_alerts:
                rsi_str  = f"RSI:{s['rsi']}" if s.get("rsi") else ""
                macd_str = f"MACD:{s['macd'].get('momentum','')}" if s.get("macd") else ""
                vwap_str = f"VWAP:{s['vwap'].get('position','')}" if s.get("vwap") else ""
                lines += (
                    f"• *{fmt(s['ticker'])}* {s['direction']} — ⚠️ {s['alert']}\n"
                    f"  {rsi_str}  {macd_str}  {vwap_str}\n"
                )

            blocks = [
                {"type": "header",
                 "text": {"type": "plain_text", "text": "⚠️ US Monitor — Position Alert"}},
                {"type": "section",
                 "text": {"type": "mrkdwn",
                          "text": f"*{len(position_alerts)} position(s) flagged for review:*\n{lines}"}},
                {"type": "context",
                 "elements": [{"type": "mrkdwn",
                                "text": datetime.now(timezone.utc).strftime("%d %b %H:%M UTC")}]}
            ]
            requests.post(slack_url, json={"blocks": blocks}, timeout=10)
            log.info(f"US Monitor alert sent for {len(position_alerts)} positions")

    # Periodic session summary to #signals — gated to <= every 2h (monitoring runs
    # every 5 min, but the full review must not spam the channel). The position
    # alerts above are immediate and NOT gated. See user directive 2026-06-09.
    if notify_slack and should_post_summary():
        slack_url = os.environ.get("SLACK_SIGNALS", "")
        if slack_url:
            lines = ""
            for s in results:
                rsi   = s.get("rsi", "—")
                macd  = s.get("macd", {}).get("momentum", "—")
                vwap  = s.get("vwap", {}).get("position", "—")
                trend = s.get("momentum", {}).get("intraday_trend", "—")
                vol   = s.get("volume", {}).get("volume_signal", "—")
                flag  = "⚠️" if not s["hold_flag"] else "✅"
                lines += f"{flag} *{fmt(s['ticker'])}* | RSI:{rsi} | MACD:{macd} | VWAP:{vwap} | Trend:{trend} | Vol:{vol}\n"

            blocks = [
                {"type": "header",
                 "text": {"type": "plain_text", "text": "📊 US Monitor — Mid-Session Review"}},
                {"type": "section",
                 "text": {"type": "mrkdwn", "text": lines or "_No open positions_"}},
                {"type": "context",
                 "elements": [{"type": "mrkdwn",
                                "text": datetime.now(timezone.utc).strftime("%d %b %H:%M UTC")}]}
            ]
            requests.post(slack_url, json={"blocks": blocks}, timeout=10)

    return results


# ======================================================================================================================
# Entry point
# Usage: python intraday_signals.py
# ======================================================================================================================

if __name__ == "__main__":
    import logging, os
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    os.environ.setdefault("SUPABASE_USER", os.environ.get("SUPABASE_USER", ""))
    os.environ.setdefault("SUPABASE_DB_PASSWORD", os.environ.get("SUPABASE_DB_PASSWORD", ""))

    results = run_us_monitor(notify_slack=False)
    for r in results:
        print(f"\n{r['ticker']} {r['direction']}:")
        print(f"  RSI:    {r.get('rsi')} ({r.get('rsi_signal')})")
        print(f"  MACD:   {r['macd'].get('momentum')} | crossover={r['macd'].get('crossover')}")
        print(f"  VWAP:   {r['vwap'].get('position')} ({r['vwap'].get('pct_from_vwap')}%)")
        print(f"  Volume: {r['volume'].get('volume_signal')} ({r['volume'].get('volume_ratio')}x avg)")
        print(f"  Trend:  {r['momentum'].get('intraday_trend')}")
        print(f"  Hold:   {r['hold_flag']}")
        if r["alert"]:
            print(f"  ALERT:  {r['alert']}")
