# ======================================================================================================================
# File:         quality_report.py
# Author:       Alex Hind
# Created:      2026-06-14
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Per-instrument "quality angle" publication for the top HVF setups: a plain-English (common-man, NOT accountant)
# NARRATIVE report — published as a NUMBERED TEXT THREAD (1/n, <=280 weighted chars per part) — plus a short,
# searchable companion tweet. Posted to #arw-claude-twitter via the SLACK_TWITTER webhook (text only). The long
# report is no longer painted into a PNG (user 2026-06-16): the text reads better as a copy-paste thread. The HVF
# chart post-card and the short skim tweet are UNCHANGED.
#
# Fundamentals (yfinance) are SECTOR-AWARE: for financials (banks/insurers/REITs) the usual cash-flow / debt /
# revenue-growth yardsticks are NOT meaningful (balance-sheet driven) and are omitted with a plain reason; we lean on
# dividend record, return on equity, analyst view and insider ownership. Non-financials get the full picture: multi-year
# sales growth, profit trend, spare cash (FCF), net cash/debt, return on equity, dividend record, analyst target,
# insider ownership. The ".L" EPS row is unreliable so PROFIT (Net Income) is used instead of EPS.
#
# Daily change-detection: compares today's HVF entry/stop/target/R:R against the most recent PRIOR hvf_scan_log row for
# the ticker; a fresh report is published when first-seen OR when entry/target/R:R move, with a plain "What's changed"
# line. Unchanged setups are skipped (no duplicate spam).
#
# Usage:   python quality_report.py            # top PER_MARKET_TOP_N (10) per market of today's tradeable HVF setups
#          python quality_report.py 5          # top 5 per market
#          python quality_report.py NVDA MGNS.L
#
# Env (in GitHub Secrets, not local .env): SUPABASE_USER/SUPABASE_DB_PASSWORD, SLACK_TWITTER,
#                                          SLACK_BOT_TOKEN, SLACK_TWITTER_CHANNEL_ID
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.24.0  2026-06-27  Alex Hind   (user 2026-06-27) Suppress the institutional-holder line for UK (.L) tickers — yfinance
#                                 UK holder data is index-ETF noise (HSBC FTSE 250 ETF / Pacer ~0%), not real DTR5 holders.
#                                 Proper UK source = FCA NSM TR-1 filings (BACKLOG). US holder feeds reliable, kept.
# 1.23.0  2026-06-27  Alex Hind   (user 2026-06-27) FIX implausible published figures. (1) Investment trusts / closed-end
#                                 funds (MYI = Murray International Trust / BlackRock MuniYield): _kpi_block suppressed via
#                                 _looks_like_fund — operating KPIs (net margin 94.4%, revenue +444%, P/E 6x) are garbage
#                                 for funds. (2) Per-value sanity guards: net margin kept only -100%..60%, revenue growth
#                                 only |<=200%|. (3) Largest-holder % now Shares/sharesOutstanding (SBUX/Capital World was
#                                 74% from yfinance's bogus pctHeld; true ~9%), implausible >100% dropped. SBUX payout 188%
#                                 confirmed REAL (TTM EPS 1.31 vs $2.48 div) — left as-is.
# 1.22.0  2026-06-26  Alex Hind   (user 2026-06-26, F leftover) _kpi_block now adds insider-holdings change over ~9 months —
#                                 net open-market shares BOUGHT minus SOLD (grants/awards/exercises ignored). Best-effort,
#                                 omitted on any gap. (Market share still omitted — no clean free source.)
# 1.21.0  2026-06-26  Alex Hind   (user 2026-06-26, F) New _kpi_block() "Key numbers" paragraph in the long report:
#                                 P/E (trailing + forward), net margin, return on assets (≈ROIC proxy), revenue growth, free
#                                 cash flow, net debt/EBITDA, buybacks, dividend + its growth rate (+ payout-above-profit
#                                 flag). yfinance-only, best-effort, never names the source (X rule). Two bugs caught in test
#                                 + fixed: per-share dividend was abbreviated to "$0m" by _money (now direct format); the
#                                 div-growth calc included the current PARTIAL year and read "-17%" (now complete years only,
#                                 shown only when actually positive). Market share omitted (not in the feed).
# 1.20.0  2026-06-24  Alex Hind   (user 2026-06-24) Reworded a chart-open variant that didn't read well ("The chart adds the
#                                 why-now.") -> "Now the timing." — same intent (the chart explains why this is a NOW setup)
#                                 in plain English.
# 1.19.0  2026-06-22  Alex Hind   (user 2026-06-22) (E) analyst line reads "Of N analysts rating <TICKER>" (was "rating it").
#                                 (F) a near-flat analyst target now reads "their average price target is roughly in line with
#                                 the current price" (was the confusing "price targets sit about 0% below") — and it's framed as
#                                 the ANALYST target, not the HVF trade target.
# 1.18.0  2026-06-22  Alex Hind   (user 2026-06-22) Long report breaks onto a NEW LINE when the SUBJECT changes — business
#                                 fundamentals | analysts | ownership are now separate paragraphs (grouped s/s_analyst/s_own/
#                                 s_cite joined with blank lines), not one wall of text.
# 1.17.0  2026-06-22  Alex Hind   (user 2026-06-22) FIX confusing distance line: "entry +36.4% away, target -15.1% away"
#                                 (signed % + "away" = contradictory, no prices) -> "It trades around 261p today — entry
#                                 356p (36.4% above), target 221p (15.1% below)". Each level now shows its PRICE + abs%
#                                 + above/below. Removed the now-unused _P_CHART_NOW template.
# 1.16.0  2026-06-22  Alex Hind   (user 2026-06-22) FIX inconsistent analyst sentence ("19 analysts rate it Buy ... buys
#                                 eased from 15 to 13"): the count now comes from the ratings GRID, not numberOfAnalystOpinions
#                                 — "Of N analysts rating it, B say Buy and H Hold (consensus X); targets ~P% above", and the
#                                 trend's end value equals B, so they reconcile. Falls back to the coverage count when no grid.
# 1.15.0  2026-06-22  Alex Hind   (user 2026-06-22) _chart_story comments on a PROLONGED consolidation: when the funnel has
#                                 been forming >=PROLONGED_FUNNEL_WEEKS (8wk, H1->H3) it adds "This range has been coiling for
#                                 about N weeks — a prolonged consolidation". Public-safe wording.
# 1.14.0  2026-06-22  Alex Hind   (user 2026-06-22) Analyst stance OVER TIME in the long report: after the "rate it Buy /
#                                 fair value +N%" sentence, add the 3-month DRIFT in the buy count ("conviction cooling —
#                                 buy ratings eased 24->15") so a cooling Buy knits the bull(analysts)/bear(HVF) divergence.
#                                 From t.recommendations; omitted when steady or coverage absent.
# 1.13.0  2026-06-21  Alex Hind   (user 2026-06-21) Un-suppress the institutional holder — it's valuable validation, always
#                                 shown (a big holder is further validation); kept the "institutional holder" label and
#                                 accurate emphasis (dominant >=20% / notable >=15% / neutral below). Added a 3-year price
#                                 context line to _chart_story (Slack long report + dossier).
# 1.12.0  2026-06-21  Alex Hind   (user 2026-06-21) Fix misleading holder line: the top holder is labelled "largest
#                                 institutional holder" (a fund, NOT a company insider — BlackRock 7.6% was reading as if it
#                                 contradicted "insiders 1.5%"); passive index-fund giants (BlackRock/Vanguard/SSGA/Fidelity)
#                                 below 15% are no longer flagged as a "standout" (they hold ~5-10% of every large cap).
# 1.11.0  2026-06-19  Alex Hind   (user 2026-06-19) build_report cite_sources flag — source citation is Slack/dossier-only,
#                                 NEVER in an X tweet (default False). Large-holder threshold lowered 10 -> 5%; the
#                                 callout now shows the stake rising/falling/steady (institutional_holders pctChange).
# 1.10.0  2026-06-19  Alex Hind   Authoritative data-source overrides (user 2026-06-19): reputable providers (Bloomberg,
#                                 Investing.com, S&P Global, Morningstar, FactSet) override the automated Yahoo figures on
#                                 conflict. fundamentals() applies data/fundamentals_overrides.json (per ticker/field +
#                                 source); build_report cites the source for any overridden figure. TRUSTED_DATA_SOURCES added.
# 1.9.0   2026-06-19  Alex Hind   Large single holder made clear (user 2026-06-19): fundamentals() fetches the top
#                                 institutional holder + %; build_report calls it out when >=LARGE_HOLDER_PCT (10%),
#                                 "dominant/controlling-sized" at >=20% (e.g. a Berkshire-style stake). Flows into the
#                                 dossier narrative too (which reuses build_report).
# 1.8.0   2026-06-19  Alex Hind   _chart_story now states the live price + % distance to entry/target (user 2026-06-19),
#                                 in the same plain-English style, via price_action.pct_from_current.
# 1.7.0   2026-06-19  Alex Hind   _today_top selects risk_reward and sorts via hvf_weight so the long-report ordering is
#                                 R:R-first (user 2026-06-19), matching every other list.
# 1.6.0   2026-06-16  Alex Hind   publish_long_report_for(r): posts ONLY the long 1/n thread for one instrument. Called from
#                                 intraday_signals._generate_x_drafts so the long report ALWAYS accompanies the card +
#                                 short tweet on every publication / dossier (user 2026-06-16: all three or it's incomplete).
# 1.5.0   2026-06-16  Alex Hind   FIX misleading growth claims (user 2026-06-16: "profit up £1.6bn to £11m"): rev_run/ni_run
#                                 are always >=1, so the profit line ("climbing from X to Y") and the sales-growth line
#                                 were shown even on a FALL / a single year. Now: sales growth needs rev_run>=2 (+positive
#                                 CAGR); the profit line shows only when ni_latest > ni_first > 0. Same gate in build_tweet.
# 1.4.0   2026-06-16  Alex Hind   Long report ALSO reads the chart setup (user 2026-06-16: explain why it matters, like the
#                                 colleague's report) — new _chart_story()/_P_CHART_* prose: squeeze -> breakout ->
#                                 reward-vs-risk, led before the fundamentals. PUBLIC-SAFE: never names the in-house
#                                 method (squeeze/coil/range/breakout only — no "HVF", no "funnel").
# 1.3.0   2026-06-16  Alex Hind   Per-market reports (user 2026-06-16: "top 10 by market"): _today_top now returns the
#                                 top PER_MARKET_TOP_N PER market (price_action.group_by_market, MARKET_ORDER) instead of a
#                                 global top-N; publish_quality_reports posts a per-market section header when the market
#                                 changes; CLI numeric arg / default are now per-market counts.
# 1.2.1   2026-06-16  Alex Hind   Skim tweet now says "Full story in the thread" (was "...in the image"): the long
#                                 report is a text thread, not a PNG, so the image pointer no longer applied (user 2026-06-16).
# 1.2.0   2026-06-16  Alex Hind   Long report reverted from PNG to TEXT (user 2026-06-16): the narrative is now a
#                                 numbered X thread (1/n, <=280 weighted chars/part) instead of render_report_card's
#                                 image. New paginate_report_thread() + _split_sentences/_hard_wrap helpers; part 1
#                                 leads with the title, hashtags + "Not financial advice." land on the final part.
#                                 _post() now posts the thread as copy-paste code blocks (PNG upload path removed).
#                                 The HVF chart card and the short skim tweet (build_tweet) are untouched.
# 1.1.0   2026-06-14  Alex Hind   Code-review: _today_top sort now uses price_action.hvf_weight() (single source of truth
#                                 for weight order). Behaviour identical for READY/TRIGGERED rows.
# 1.0.0   2026-06-14  Alex Hind   Initial build (user 2026-06-13/14): sector-aware fundamentals → plain-English prose
#                                 report PNG + skim tweet; daily change-detection vs hvf_scan_log; posts to
#                                 #arw-claude-twitter. 13-17 min stagger reserved for live X (drafts only for now).
# 1.1.0   2026-06-14  Alex Hind   Prose rework (user 2026-06-14): one fact per SHORT sentence (no run-ons); phrasing
#                                 picked PER INSTRUMENT from pools (_pick) so reports read bespoke, not templated;
#                                 insider stake shown as a £/$ VALUE (× market cap), not a misleading bare %; varied,
#                                 clearer financial caveat (fixes the repeated "usual yardsticks" / NatWest grammar);
#                                 "throws off strong cash" → "surplus cash" in the tweet.
# ======================================================================================================================

import os
import io
import re
import sys
import datetime
import logging
import random

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("quality_report")

# Stagger window for LIVE X posting (not used for Slack drafts) — user 2026-06-13.
X_STAGGER_MIN, X_STAGGER_MAX = 13 * 60, 17 * 60   # seconds


def x_stagger_seconds() -> int:
    """Random 13–17 min gap between tweets, for when this posts live to X."""
    return random.randint(X_STAGGER_MIN, X_STAGGER_MAX)


# ----------------------------------------------------------------------------------------------------------------------
# Plain-English formatting helpers
# ----------------------------------------------------------------------------------------------------------------------

def _money(x, gbp: bool) -> str:
    s = "£" if gbp else "$"
    if x is None:
        return "n/a"
    sign = "-" if x < 0 else ""
    ax = abs(x)
    return f"{sign}{s}{ax/1e9:.1f}bn" if ax >= 1e9 else f"{sign}{s}{ax/1e6:.0f}m"


def _rising_run(vals_new_first) -> int:
    """Consecutive years (ending at the latest) where each year beat the year before."""
    v = [float(x) for x in vals_new_first][:5]
    o = v[::-1]                       # oldest -> newest
    run = 1
    for i in range(len(o) - 1, 0, -1):
        if o[i] > o[i - 1]:
            run += 1
        else:
            break
    return run


def _yearly_growth(vals_new_first):
    v = [float(x) for x in vals_new_first][:5]
    if len(v) < 2 or v[-1] <= 0:
        return None
    return (v[0] / v[-1]) ** (1 / (len(v) - 1)) - 1


# ----------------------------------------------------------------------------------------------------------------------
# Sector-aware fundamentals (yfinance)
# ----------------------------------------------------------------------------------------------------------------------

# A single holder above this % is surfaced as a notable concentrated stake (user 2026-06-19:
# lowered 10 -> 5); >=20% is still called out as "dominant / controlling-sized" (e.g. Berkshire).
LARGE_HOLDER_PCT = 5.0

# Authoritative external data sources (user 2026-06-19). The automated figures below come from
# Yahoo Finance; these reputable providers are the references that OVERRIDE Yahoo on any conflict.
# We do not have live API access to them, so an authoritative value is supplied manually in
# data/fundamentals_overrides.json:
#     { "OXY": { "target_pct": {"value": 18.0, "source": "Morningstar"}, ... }, ... }
# fundamentals() applies any override (recording the source) and build_report cites it.
TRUSTED_DATA_SOURCES = ["Bloomberg", "Investing.com", "S&P Global", "Morningstar", "FactSet"]
_OVERRIDES_CACHE = None


def _load_source_overrides() -> dict:
    """Authoritative per-ticker overrides from Supabase, with the original JSON as compatibility fallback."""
    global _OVERRIDES_CACHE
    if _OVERRIDES_CACHE is not None:
        return _OVERRIDES_CACHE
    import json
    path = os.path.join(os.path.dirname(__file__), "data", "fundamentals_overrides.json")
    try:
        import web_store
        remote = web_store.load_json_store("fundamentals_overrides")
        if isinstance(remote, dict):
            _OVERRIDES_CACHE = remote
            return _OVERRIDES_CACHE
    except Exception as e:
        log.warning(f"Supabase fundamentals overrides unavailable; using local compatibility copy: {e}")
    try:
        with open(path, encoding="utf-8") as fh:
            _OVERRIDES_CACHE = json.load(fh) or {}
    except Exception:
        _OVERRIDES_CACHE = {}
    return _OVERRIDES_CACHE


def fundamentals(ticker: str) -> dict:
    """Sector-aware fundamentals. Missing or sector-inappropriate factors come back None."""
    import yfinance as yf
    f = {"sector": None, "financial": False, "gbp": ticker.endswith(".L")}
    try:
        t = yf.Ticker(ticker)
        info = t.info
    except Exception as e:
        log.warning(f"{ticker}: fundamentals fetch failed: {e}")
        return f
    f["sector"] = info.get("sector")
    f["financial"] = info.get("sector") == "Financial Services"

    # Dividend record — consecutive COMPLETE years of increase (exclude current part-year).
    try:
        d = t.dividends
        ann = d.groupby(d.index.year).sum()
        cur = datetime.date.today().year
        ys = [float(v) for y, v in ann.items() if y < cur]
        streak = 1
        for i in range(len(ys) - 1, 0, -1):
            if ys[i] > ys[i - 1]:
                streak += 1
            else:
                break
        f["div_streak"] = streak if streak >= 3 else None
    except Exception:
        f["div_streak"] = None

    roe = info.get("returnOnEquity")
    f["roe"] = roe if isinstance(roe, (int, float)) and 0 < roe <= 0.60 else None   # sane only (buyback-distorted ROE dropped)

    nop, tgt, px = info.get("numberOfAnalystOpinions"), info.get("targetMeanPrice"), info.get("currentPrice")
    f["analyst_n"] = nop
    rk = info.get("recommendationKey")
    f["analyst_rec"] = rk.title() if rk and rk != "none" else None
    f["target_pct"] = ((tgt - px) / px * 100) if (tgt and px) else None

    # Analyst RATINGS GRID (user 2026-06-22) — buy/hold/sell counts + the 3-month DRIFT, ALL from
    # the recommendations grid so the sentence reconciles. Mixing numberOfAnalystOpinions (price-
    # target providers, f["analyst_n"]) with the grid's buy count read as a contradiction
    # ("19 analysts rate it Buy ... buys eased from 15 to 13"). The grid total / buys / holds and
    # the trend now share one source. Best-effort; left None when coverage/history is absent.
    f["analyst_buys"] = f["analyst_holds"] = f["analyst_sells"] = f["analyst_rated"] = f["analyst_trend"] = None
    try:
        rec = t.recommendations
        if rec is not None and len(rec):
            rec = rec.reset_index(drop=True)
            def _r(p):
                m = rec[rec["period"] == p]
                return m.iloc[0] if len(m) else None
            _cur, _old = _r("0m"), _r("-3m")
            if _cur is None: _cur = rec.iloc[0]
            if _old is None: _old = rec.iloc[-1]
            def _b(row): return int(row.get("strongBuy", 0) or 0) + int(row.get("buy", 0) or 0)
            def _h(row): return int(row.get("hold", 0) or 0)
            def _s(row): return int(row.get("sell", 0) or 0) + int(row.get("strongSell", 0) or 0)
            _cb, _ob = _b(_cur), _b(_old)
            f["analyst_buys"]  = _cb
            f["analyst_holds"] = _h(_cur)
            f["analyst_sells"] = _s(_cur)
            f["analyst_rated"] = _cb + _h(_cur) + f["analyst_sells"]   # grid total (raters), reconciles with buys
            if _cb != _ob:
                f["analyst_trend"] = ("cooling", _ob, _cb) if _cb < _ob else ("strengthening", _ob, _cb)
    except Exception:
        f["analyst_buys"] = f["analyst_holds"] = f["analyst_sells"] = f["analyst_rated"] = f["analyst_trend"] = None
    f["mcap"] = info.get("marketCap")
    f["industry"] = info.get("industry")
    ins = info.get("heldPercentInsiders")
    f["insider_pct"] = ins * 100 if isinstance(ins, (int, float)) and ins > 0 else None
    # Insider stake as a £/$ VALUE (user 2026-06-14: a % alone misleads on a big-cap —
    # 0.1% of a giant market cap is still a large sum).
    f["insider_value"] = (ins * f["mcap"]) if (f.get("insider_pct") and f.get("mcap")) else None

    # Largest single holder (user 2026-06-19): a big concentrated stake (e.g. Berkshire
    # owning >20%) is materially relevant and must be made clear. Surface the top
    # institutional holder + its %. yfinance column names vary across versions, so resolve
    # them defensively; fraction vs percent is normalised. Best-effort — never blocks.
    f["top_holder"], f["top_holder_pct"], f["top_holder_change"] = None, None, None
    try:
        # UK (.L) holder data from yfinance is index-ETF NOISE (e.g. HSBC FTSE 250 ETF / Pacer at
        # ~0.0%), not the real DTR5 major holders — so it is suppressed for UK until sourced from the
        # FCA National Storage Mechanism (TR-1 filings; see BACKLOG). US feeds are reliable, kept.
        inst = None if str(ticker).upper().endswith(".L") else t.institutional_holders
        if inst is not None and not inst.empty:
            cols = {str(c).lower(): c for c in inst.columns}
            hcol = next((cols[k] for k in cols if k in ("holder", "holders")), None)
            # %held: prefer an exact pct-held column over the position-change column.
            pcol = next((cols[k] for k in cols if k in ("pctheld", "% out", "pct_held")), None) \
                or next((cols[k] for k in cols if "pct" in k and "change" not in k), None)
            ccol = next((cols[k] for k in cols if "pctchange" in k or "pct_change" in k or k == "% change"), None)
            scol = next((cols[k] for k in cols if k == "shares"), None)
            if hcol is not None:
                top = inst.iloc[0]
                # yfinance's pctHeld is UNRELIABLE — e.g. SBUX/Capital World reported pctHeld=0.74
                # while the true stake is ~9% (Shares 103.3M / sharesOutstanding 1.14bn). Prefer
                # Shares/sharesOutstanding; fall back to pctHeld only when shares data is missing,
                # and DROP an implausible (>100%) result rather than publish "owns 74%".
                shares_out = info.get("sharesOutstanding")
                pct = None
                if scol is not None and shares_out:
                    try:
                        pct = float(top[scol]) / float(shares_out) * 100
                    except (ValueError, TypeError, ZeroDivisionError):
                        pct = None
                if pct is None and pcol is not None:
                    pct = float(top[pcol])
                    if pct <= 1.0:      # some versions report a fraction (0.21), others a percent (21.0)
                        pct *= 100
                if pct is not None and 0 < pct <= 100:
                    f["top_holder"] = str(top[hcol]).strip()
                    f["top_holder_pct"] = pct
                # Direction of the stake (user 2026-06-19): pctChange is the position change
                # this reporting period (fraction; +ve = adding, -ve = trimming).
                if ccol is not None:
                    try:
                        f["top_holder_change"] = float(top[ccol]) * 100
                    except Exception:
                        pass
    except Exception:
        pass

    if not f["financial"]:
        try:
            ismt = t.income_stmt
            if "Total Revenue" in ismt.index:
                rev = ismt.loc["Total Revenue"].dropna()
                if len(rev) >= 2:
                    f["rev_run"], f["rev_cagr"], f["rev_latest"] = _rising_run(rev.values), _yearly_growth(rev.values), float(rev.values[0])
            if "Net Income" in ismt.index:
                ni = ismt.loc["Net Income"].dropna()
                if len(ni) >= 2:
                    f["ni_run"], f["ni_first"], f["ni_latest"] = _rising_run(ni.values), float(ni.values[-1]), float(ni.values[0])
        except Exception:
            pass
        try:
            cf = t.cashflow
            for key, row in (("ocf", "Operating Cash Flow"), ("fcf", "Free Cash Flow")):
                if row in cf.index:
                    v = cf.loc[row].dropna()
                    latest = float(v.values[0])
                    f[key] = {"latest": latest, "run": _rising_run(v.values), "pos": latest > 0}
        except Exception:
            pass
        td, tc, de = info.get("totalDebt"), info.get("totalCash"), info.get("debtToEquity")
        if td is not None and tc is not None:
            f["net_cash"], f["cash"], f["debt"], f["de"] = (tc > td), tc, td, de

    # Authoritative overrides (user 2026-06-19): a reputable provider's figure takes precedence
    # over the automated Yahoo value. Records which fields were overridden + their source so
    # build_report can cite it.
    ov = _load_source_overrides().get(ticker.upper(), {})
    applied = {}
    for field, spec in ov.items():
        if isinstance(spec, dict) and "value" in spec:
            f[field] = spec["value"]
            applied[field] = spec.get("source", "external source")
    if applied:
        f["overrides"] = applied
    return f


# ----------------------------------------------------------------------------------------------------------------------
# Prose report (common-man narrative) + skim tweet
# ----------------------------------------------------------------------------------------------------------------------

import hashlib

# Phrasing pools — one is picked PER INSTRUMENT (stable per name, different between names)
# so each report reads as if written on its own, not from a template (user 2026-06-14).
# Sentences are kept SHORT — one fact each — to stay readable.
_P_GROWTH = [
    "{name} keeps growing — sales up {ry} years running, now {rev}.",
    "{name} is on a steady run: {ry} straight years of higher sales, reaching {rev}.",
    "Sales at {name} have climbed {ry} years in a row, to {rev}.",
    "{name} has grown sales every year for {ry} years; they now stand at {rev}.",
    "The top line keeps building at {name} — up {ry} years straight, {rev} today.",
]
_P_RATE = ["That's roughly {rate}% a year.", "About {rate}% a year.", "A steady ~{rate}% a year."]
_P_PROFIT = [
    "Profit has grown too, from {ni0} to {ni1}.",
    "Profit followed, climbing from {ni0} to {ni1}.",
    "Earnings rose alongside — {ni0} to {ni1}.",
    "It drops to the bottom line: profit up {ni0} to {ni1}.",
]
_P_CASH = [
    "It generates real surplus cash — {fcf} left over last year after running and reinvesting.",
    "The profit is backed by cash: {fcf} of surplus last year.",
    "Cash generation is strong — {fcf} free after costs and investment.",
    "That profit turns into cash, {fcf} of it spare last year.",
]
_P_NETCASH = [
    "The balance sheet is strong: more cash ({cash}) than debt ({debt}).",
    "Finances are solid — {cash} of cash against just {debt} of debt.",
    "Little borrowing here: {cash} cash versus {debt} debt.",
    "It sits on net cash, {cash} against {debt} of debt.",
]
_P_NETDEBT = [
    "It carries {debt} of debt against {cash} of cash.",
    "There is some borrowing — {debt} of debt, {cash} of cash.",
    "Debt stands at {debt}, with {cash} of cash on hand.",
]
# A return only goes in the POSITIVES list when it is genuinely a positive. Every phrasing below asserts
# strength ("high", "strong", "uses capital well"), so emitting one for a weak return states the opposite
# of the truth. On 2026-09-05 a published tweet read "Returns are high: 0% on shareholders' money" for
# VOD, whose ROE is 0.00109 -- 0.1%. It passed the sanity filter (0 < roe <= 0.60), rounded to "0" at
# :.0f, and was described as high. The filter proved the number was not absurd; nothing checked it was
# good. 10% is the floor for calling a return strong, and it also guarantees the figure never prints 0%.
_ROE_STRONG = 0.10

_P_ROE = [
    "Returns are high: {roe}% on shareholders' money.",
    "It uses capital well, earning {roe}% on shareholders' money.",
    "A strong {roe}% return on what shareholders have put in.",
    "Money is put to work well — {roe}% return on equity.",
]
_P_DIV = [
    "The dividend has risen {streak} years running.",
    "Shareholders get a rising payout — {streak} straight years of dividend growth.",
    "It has lifted its dividend {streak} years in a row.",
    "A {streak}-year run of dividend increases, too.",
]
_P_ANALYST = [
    "Analysts ({n}) {rec}see the shares worth about {pct}% {dir} today's price.",
    "The {n} analysts covering it {rec}peg fair value about {pct}% {dir}.",
    "Broker targets ({n} analysts) {rec}sit about {pct}% {dir} the price.",
]
_P_INSIDER = [
    "Company insiders hold about {value} of stock ({pct}%).",
    "Insiders own roughly {value} worth, about {pct}% of the company.",
    "Management's own holding is around {value} ({pct}%).",
]
_P_FIN_CAVEAT = [
    "{name} is in {industry}, where cash-flow, debt and revenue figures behave very differently — so it's better judged on its record and returns.",
    "As a {industry} business, {name} doesn't fit the usual cash-flow and debt measures; consistency and returns are what count.",
    "For a financial like {name}, the normal cash-flow and balance-sheet yardsticks don't apply cleanly, so the dividend record and returns matter most.",
    "{name} runs a {industry} balance sheet, so conventional cash and debt metrics can mislead — returns and the payout tell the story.",
    "Being a {industry} firm, {name} is best read through its returns and dividend reliability, not cash flow or borrowing.",
]


def _pick(pool, ticker, salt):
    """Stable per-(ticker, salt) choice from a phrasing pool — same for a name every run,
    but different between names, so reports don't look templated."""
    h = int(hashlib.md5(f"{ticker}|{salt}".encode()).hexdigest()[:8], 16)
    return pool[h % len(pool)]


# Chart-setup narrative (user 2026-06-16): the long report ALSO explains WHY the technical
# setup matters — the squeeze/breakout read a colleague would give. PUBLIC-SAFE: it never
# names the in-house method (house rule: "Volatility squeeze", NEVER "HVF"/"Hunt Volatility
# Funnel"); the public vocabulary is squeeze / coil / tightening range / breakout / ceiling
# / floor. Phrasing varies per instrument (_pick), like the fundamentals prose.
_P_CHART_OPEN = [
    "Now the chart. {name} has spent months winding into a tighter and tighter range.",
    "Here's the timing too: on the chart, {name} has coiled for months into a steadily narrowing range.",
    "Now the timing. {name} has been squeezing into an ever-narrower range for months.",
]
_P_CHART_SQUEEZE = [
    "The rallies kept getting capped while the dips held higher, tightening the range to about {pct} of its original width.",
    "Sellers capped each bounce and buyers lifted each dip, so the range is now roughly {pct} of where it started.",
    "Lower highs met rising lows until the range compressed to about {pct} of its original height.",
]
_P_CHART_BREAK_UP = [
    "It has now broken out the top — and a long coil like this letting go is exactly the kind of move that tends to run.",
    "That squeeze has just snapped higher, and when a coil this long releases, the move often runs.",
]
_P_CHART_BREAK_DOWN = [
    "It has now broken down through the floor — and a long coil like this letting go is exactly the kind of move that tends to run.",
    "That squeeze has just given way to the downside, and when a coil this long releases, the move often runs.",
]
_P_CHART_READY_UP = [
    "It is now coiled just under the ceiling near {entry} — a break above there is the trigger.",
    "Price is pressed right under resistance near {entry}; a close above it is the signal.",
]
_P_CHART_READY_DOWN = [
    "It is now coiled just above the floor near {entry} — a break below there is the trigger.",
    "Price is pressed right on support near {entry}; a close below it is the signal.",
]
_P_CHART_RR = [
    "From there the path opens toward {target}, against risk back to {stop} — about {rr}x more reward than risk.",
    "If it plays out it targets {target} while risking back to {stop} — roughly {rr}x the reward for the risk.",
]


def _chart_story(r: dict, name: str, gbp: bool) -> str:
    """Plain-English read of WHY the technical setup matters, for the long report. PUBLIC-SAFE:
    never names the in-house method (squeeze/coil/range/breakout only). One fact per short
    sentence; returns "" if direction is unknown."""
    direction = (r.get("hvf_type") or "").upper()
    signal    = (r.get("hvf_signal") or "").upper()
    if direction not in ("BULLISH", "BEARISH"):
        return ""
    up = direction == "BULLISH"
    tk = r.get("ticker", "")

    def _lvl(v):
        return None if v is None else (f"{v:,.0f}p" if gbp else f"${v:,.2f}")

    entry, stop, target = _lvl(r.get("h3_level")), _lvl(r.get("stop_level")), _lvl(r.get("target"))
    rr, conv = r.get("risk_reward"), r.get("convergence")

    parts = [_pick(_P_CHART_OPEN, tk, "co").format(name=name)]
    if isinstance(conv, (int, float)) and 0 < conv < 1:
        parts.append(_pick(_P_CHART_SQUEEZE, tk, "sq").format(pct=f"{conv * 100:.0f}%"))
    if signal == "TRIGGERED":
        parts.append(_pick(_P_CHART_BREAK_UP if up else _P_CHART_BREAK_DOWN, tk, "br"))
    elif entry:
        parts.append(_pick(_P_CHART_READY_UP if up else _P_CHART_READY_DOWN, tk, "rd").format(entry=entry))
    if rr and target and stop:
        parts.append(_pick(_P_CHART_RR, tk, "rr").format(target=target, stop=stop, rr=f"{rr:.0f}"))
    # Current price + entry/target, each with its PRICE and a clear direction (user 2026-06-22).
    # The old "entry +36.4% away, target -15.1% away" glued a SIGNED % to the word "away" (so
    # "-15.1% away" was self-contradictory) and hid the actual prices. Now each level reads
    # "<price> (<abs%> above/below)" so there is no ambiguity about which number is which.
    cur = r.get("current_price")
    if isinstance(cur, (int, float)) and cur:
        def _dist(level):
            if not isinstance(level, (int, float)) or not level:
                return None
            pct = (level / cur - 1) * 100
            return f"{_lvl(level)} ({abs(pct):.1f}% {'above' if pct >= 0 else 'below'})"
        _e, _t = _dist(r.get("h3_level")), _dist(r.get("target"))
        _seg = []
        if _e: _seg.append(f"entry {_e}")
        if _t: _seg.append(f"target {_t}")
        if _seg:
            parts.append(f"It trades around {_lvl(cur)} today — " + ", ".join(_seg) + ".")
    # Prolonged-consolidation comment (user 2026-06-22: "recognise the prolonged period and make
    # comment"). How long the range has been coiling (H1->H3). Public-safe wording (coil/range only).
    try:
        from price_action import funnel_span_weeks, PROLONGED_FUNNEL_WEEKS
        _wks = funnel_span_weeks(r)
        if _wks and _wks >= PROLONGED_FUNNEL_WEEKS:
            parts.append(f"This range has been coiling for about {_wks} weeks — a prolonged consolidation, "
                         f"so the breakout level has been building for a while.")
    except Exception:
        pass
    # 3-year context (user 2026-06-21: "show the 3yr history, e.g. NKE gradually falling") — the
    # long-term backdrop the funnel sits in. Best-effort; omitted if the fetch fails.
    try:
        import yfinance as _yf
        _h3 = _yf.Ticker(tk).history(period="3y", interval="1wk")["Close"].dropna()
        if len(_h3) > 8:
            _p3 = (float(_h3.iloc[-1]) / float(_h3.iloc[0]) - 1) * 100
            if _p3 <= -15:
                parts.append(f"Zooming out, the shares are down about {abs(_p3):.0f}% over three years — a long, grinding decline.")
            elif _p3 >= 15:
                parts.append(f"Zooming out, the shares are up about {_p3:.0f}% over three years — a strong multi-year uptrend.")
            else:
                parts.append(f"Over three years the shares are roughly flat ({_p3:+.0f}%).")
    except Exception:
        pass
    return " ".join(parts)


# Investment trusts / closed-end funds report meaningless operating KPIs (yfinance gave MYI net margin
# 94.4% / revenue +444%), so the "Key numbers" block is suppressed for them. Name-based (quoteType /
# category / fundFamily are all None for UK trusts); markers chosen NOT to catch operating companies
# like "Northern Trust Corporation" (ends "Corporation", no marker) — user 2026-06-27.
_FUND_NAME_RE = re.compile(
    r"\b(INVESTMENT TRUST|INVESTMENT COMPANY|INVESTMENT FUND|MUNIYIELD|MUNI|UCITS|ICVC|SICAV|"
    r"CLOSED.?END|VCT|ETF|FUND)\b", re.I)


def _looks_like_fund(name: str) -> bool:
    """True if the instrument name is an investment trust / closed-end fund / ETF, for which the
    operating KPIs (margin, revenue growth, P/E, FCF) are meaningless. Conservative: 'Northern Trust
    Corporation' (a bank) is NOT a fund — it lacks a marker and ends 'Corporation', not 'Trust PLC'."""
    if not name:
        return False
    n = name.strip().upper()
    # "TRUST PLC" as a SUBSTRING — yfinance appends share-class suffixes ("MURRAY INCOME TRUST PLC
    # ORD 25"), so endswith() missed real trusts. "Northern Trust Corporation" has no "TRUST PLC".
    return bool(_FUND_NAME_RE.search(n)) or "TRUST PLC" in n


def _kpi_block(ticker: str, gbp: bool) -> str:
    """Compact "Key numbers" KPI paragraph for the long report (user 2026-06-26, F): valuation (P/E),
    profitability (net margin, return on assets ≈ ROIC proxy), revenue growth, free cash flow,
    net debt/EBITDA, buybacks, dividend + its growth rate (+ a payout-above-profit flag). yfinance
    only; best-effort — every KPI is independently guarded, a missing one is just omitted. NEVER names
    the data source (X rule). Returns "" if nothing usable. (Market share intentionally omitted — not
    in the feed; ROIC approximated by return-on-assets.)"""
    try:
        import yfinance as yf
        try:
            from config import YAHOO_MAP
        except Exception:
            YAHOO_MAP = {}
        t = yf.Ticker(YAHOO_MAP.get(ticker, ticker))
        info = t.info or {}
    except Exception:
        return ""

    # Suppress operating KPIs for investment trusts / closed-end funds (user 2026-06-27): they are
    # meaningless and yfinance returns garbage (MYI net margin 94.4% / revenue +444% / P/E 6x).
    if _looks_like_fund(info.get("shortName") or info.get("longName") or ""):
        return ""

    bits = []

    def _pct(v, dp=0):
        return f"{v * 100:.{dp}f}%" if isinstance(v, (int, float)) else None

    pe, fpe = info.get("trailingPE"), info.get("forwardPE")
    if isinstance(pe, (int, float)) and pe > 0:
        _v = f"valued on about {pe:.0f}x earnings"
        if isinstance(fpe, (int, float)) and fpe > 0:
            _v += f" ({fpe:.0f}x forward)"
        bits.append(_v.capitalize() + ".")

    # Sanity guards (user 2026-06-27): drop implausible values that are almost always a data artifact
    # rather than reality — a >60% net margin or a >200% YoY revenue swing on an established name.
    nm, roa = info.get("profitMargins"), info.get("returnOnAssets")
    prof = []
    if isinstance(nm, (int, float)) and -1.0 <= nm <= 0.6:
        prof.append(f"net margin {_pct(nm, 1)}")
    if isinstance(roa, (int, float)) and -1.0 <= roa <= 1.0:
        prof.append(f"return on assets ~{_pct(roa, 0)}")
    if prof:
        bits.append("Profitability: " + ", ".join(prof) + ".")

    rg = info.get("revenueGrowth")
    if isinstance(rg, (int, float)) and abs(rg) <= 2.0:
        bits.append(f"Revenue {'up' if rg >= 0 else 'down'} {_pct(abs(rg), 0)} on the year.")

    fcf = info.get("freeCashflow")
    if isinstance(fcf, (int, float)):
        bits.append(f"Free cash flow {_money(fcf, gbp)}" + (" (negative)" if fcf < 0 else "") + ".")

    td, tc, eb = info.get("totalDebt"), info.get("totalCash"), info.get("ebitda")
    if all(isinstance(x, (int, float)) for x in (td, tc, eb)) and eb:
        bits.append(f"Net debt about {(td - tc) / eb:.1f}x EBITDA.")

    try:
        cf = t.cashflow
        if "Repurchase Of Capital Stock" in cf.index:
            rep = cf.loc["Repurchase Of Capital Stock"].dropna()
            if len(rep) and abs(float(rep.values[0])) > 1e6:
                bits.append(f"Bought back {_money(abs(float(rep.values[0])), gbp)} of stock last year.")
    except Exception:
        pass

    dr = info.get("dividendRate")
    if isinstance(dr, (int, float)) and dr > 0:
        # NB a per-share dividend is a small number — format it directly, NOT via _money (which
        # abbreviates to $Xbn/$Xm and rendered $2.48 as "$0m").
        _d = f"Pays a {'£' if gbp else '$'}{dr:.2f} dividend"
        try:
            import datetime as _dt
            d = t.dividends
            _this_year = _dt.datetime.now(_dt.timezone.utc).year
            # COMPLETE years only — the current year is partial and dragged growth negative.
            ann = [float(v) for y, v in d.groupby(d.index.year).sum().items() if y < _this_year][-4:]
            if len(ann) >= 3 and ann[0] > 0:
                _g = (ann[-1] / ann[0]) ** (1 / (len(ann) - 1)) - 1
                if _g > 0.005:                       # only claim growth when it actually grew
                    _d += f", growing about {_g * 100:.0f}% a year"
        except Exception:
            pass
        pr = info.get("payoutRatio")
        if isinstance(pr, (int, float)) and pr > 1:
            _d += f" (payout ~{pr * 100:.0f}% of earnings — funded beyond profit)"
        bits.append(_d + ".")

    # Insider holdings change over ~9 months (user 2026-06-26, F leftover): net shares insiders
    # BOUGHT minus SOLD on the open market in the window — direction is the sentiment signal. Grants /
    # awards / option exercises are ignored (not open-market conviction). Best-effort; omitted on any gap.
    def _shares_fmt(n):
        for div, suf in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
            if n >= div:
                return f"{n / div:.1f}{suf}"
        return f"{n:.0f}"
    try:
        import datetime as _dt2
        import pandas as _pd
        itx = t.insider_transactions
        if itx is not None and not itx.empty:
            _cols = {c.lower(): c for c in itx.columns}
            date_col   = next((_cols[k] for k in _cols if "date" in k), None)
            shares_col = next((_cols[k] for k in _cols if "share" in k), None)
            type_col   = next((_cols[k] for k in _cols if "transaction" in k or k == "text"), None)
            if date_col and shares_col and type_col:
                cutoff = _dt2.datetime.now() - _dt2.timedelta(days=270)
                net = 0.0
                for _, row in itx.iterrows():
                    try:
                        d = _pd.to_datetime(row[date_col])
                        if _pd.isna(d) or d.to_pydatetime().replace(tzinfo=None) < cutoff:
                            continue
                        sh = abs(float(row[shares_col]))
                        ttype = str(row[type_col]).lower()
                        if any(w in ttype for w in ("purchas", "buy")):
                            net += sh
                        elif any(w in ttype for w in ("sale", "sell", "dispos")):
                            net -= sh
                    except Exception:
                        continue
                if abs(net) >= 1000:
                    bits.append(f"Insiders net {'bought' if net > 0 else 'sold'} "
                                f"~{_shares_fmt(abs(net))} shares over the past 9 months.")
    except Exception:
        pass

    return ("Key numbers — " + " ".join(bits)) if bits else ""


def build_report(r: dict, change_note: str = None, cite_sources: bool = False) -> tuple:
    """(title, prose_body) — plain English, one fact per short sentence, phrasing varied
    per instrument so each report reads bespoke (user 2026-06-14).

    cite_sources: include the authoritative-source citation for overridden figures. Default
    FALSE because this body is published to X, and the source of info must NOT appear in an X
    tweet (user 2026-06-19). Slack/dossier callers pass True."""
    from intraday_signals import _resolve_name
    tk = r["ticker"]
    gbp = tk.endswith(".L")
    name = r.get("name") or _resolve_name(tk)
    disp = tk[:-2] if tk.endswith(".L") else tk
    f = fundamentals(tk)
    s = []            # business fundamentals (growth / profit / cash / balance sheet / ROE / dividend)
    s_analyst = []    # analyst ratings + targets + 3-month drift
    s_own = []        # insider + institutional ownership
    s_cite = []       # source citations (Slack/dossier only)
    # Grouped so the long report breaks onto a NEW LINE when the SUBJECT changes (user 2026-06-22):
    # fundamentals | analysts | ownership read as separate paragraphs, not one wall of text.

    if f.get("financial"):
        s.append(_pick(_P_FIN_CAVEAT, tk, "cav").format(name=name, industry=(f.get("industry") or "financial services")))
        if f.get("div_streak"):
            s.append(_pick(_P_DIV, tk, "div").format(streak=f["div_streak"]))
        if (f.get("roe") or 0) >= _ROE_STRONG:
            s.append(_pick(_P_ROE, tk, "roe").format(roe=f"{f['roe']*100:.0f}"))
    else:
        # Only claim growth when sales genuinely rose for >=2 consecutive years. rev_run is
        # always >=1 (it counts the latest year as 1), so the old truthy check sold "up 1
        # years running" — and even a fall — as growth (user 2026-06-16).
        if f.get("rev_run") and f["rev_run"] >= 2:
            s.append(_pick(_P_GROWTH, tk, "grw").format(name=name, ry=f["rev_run"], rev=_money(f.get("rev_latest"), gbp)))
            if f.get("rev_cagr") and f["rev_cagr"] > 0:
                s.append(_pick(_P_RATE, tk, "rate").format(rate=f"{f['rev_cagr']*100:.0f}"))
        # "Profit climbing from X to Y" must be TRUE end to end. ni_run was always >=1, so a
        # FALL (e.g. £1.6bn -> £11m) was being rendered as a climb (user 2026-06-16). Show the
        # line only when profit actually grew over the span.
        if f.get("ni_first") is not None and f.get("ni_latest") is not None \
                and f["ni_first"] > 0 and f["ni_latest"] > f["ni_first"]:
            s.append(_pick(_P_PROFIT, tk, "pft").format(ni0=_money(f["ni_first"], gbp), ni1=_money(f["ni_latest"], gbp)))
        if f.get("fcf") and f["fcf"]["pos"]:
            s.append(_pick(_P_CASH, tk, "cash").format(fcf=_money(f["fcf"]["latest"], gbp)))
        if f.get("net_cash"):
            s.append(_pick(_P_NETCASH, tk, "bs").format(cash=_money(f["cash"], gbp), debt=_money(f["debt"], gbp)))
        elif f.get("debt") is not None:
            s.append(_pick(_P_NETDEBT, tk, "bs").format(cash=_money(f["cash"], gbp), debt=_money(f["debt"], gbp)))
        if (f.get("roe") or 0) >= _ROE_STRONG:
            s.append(_pick(_P_ROE, tk, "roe").format(roe=f"{f['roe']*100:.0f}"))
        if f.get("div_streak"):
            s.append(_pick(_P_DIV, tk, "div").format(streak=f["div_streak"]))

    if f.get("target_pct") is not None or f.get("analyst_rated"):
        rec = f.get("analyst_rec")
        buys, holds, sells, total = (f.get("analyst_buys"), f.get("analyst_holds"),
                                     f.get("analyst_sells"), f.get("analyst_rated"))
        tp  = f.get("target_pct")
        from evidence_alignment import analyst_stance, contextualise, relationship
        _setup_direction = (r.get("hvf_type") or "").upper()
        _analyst_stance = analyst_stance(buys=buys, holds=holds, sells=sells,
                                          recommendation=rec, target_pct=tp)
        _relationship = relationship(_setup_direction, _analyst_stance)
        # The ANALYST consensus price target vs the current price (user 2026-06-22, F): a near-zero
        # figure means analysts see the shares ~fairly valued (little up/downside) — it is NOT the
        # HVF trade target being met. Phrase it as "their average price target" and, when ~flat, say
        # "roughly in line with the current price" rather than the confusing "about 0% below".
        def _target_clause():
            if tp is None:
                return ""
            if abs(tp) < 1.5:
                return "their average price target is roughly in line with the current price"
            return f"their average price target is about {abs(tp):.0f}% {'above' if tp >= 0 else 'below'} the current price"
        # Prefer the ratings GRID so the count reconciles with the over-time trend below (user
        # 2026-06-22: "19 analysts rate it Buy ... from 15 to 13 makes little sense"). total/buys/
        # holds + the trend all come from the same grid; "rating {disp}" names the instrument (E).
        if total:
            _head = f"Of {total} analysts rating {disp}, {buys} say Buy"
            if holds:
                _head += f" and {holds} Hold"
            if rec:
                _head += f" (consensus {rec})"
            _tc = _target_clause()
            if _tc:
                _head += f"; {_tc}"
            s_analyst.append(contextualise(_head + ".", _setup_direction, _analyst_stance))
        elif tp is not None:
            # No ratings grid available — fall back to consensus + target.
            _recph = f'rate {disp} "{rec}" and ' if rec else ""
            s_analyst.append(contextualise(
                f"Analysts {_recph}{_target_clause()}.", _setup_direction, _analyst_stance))
        # Over-time drift (user 2026-06-22) — knits the bull/bear divergence. The end value {_cb}
        # equals {buys} in the grid headline above, so the two sentences now reconcile.
        _at = f.get("analyst_trend")
        if _at:
            _word, _ob, _cb = _at
            if _word == "cooling":
                _lead = "That opposing analyst view" if _relationship == "opposes" else "Analyst conviction"
                s_analyst.append(f"{_lead} has been cooling — buy ratings eased from {_ob} to {_cb} over the past three months.")
            else:
                _lead = "That opposing analyst view" if _relationship == "opposes" else "Analyst conviction"
                _effect = (f" The conflict with the {_setup_direction.lower()} chart thesis is therefore getting stronger."
                           if _relationship == "opposes" else "")
                s_analyst.append(f"{_lead} is building — buy ratings rose from {_ob} to {_cb} over the past three months.{_effect}")
    if f.get("insider_value") is not None:
        s_own.append(_pick(_P_INSIDER, tk, "ins").format(value=_money(f["insider_value"], gbp), pct=f"{f['insider_pct']:.1f}"))
    elif f.get("insider_pct") is not None:
        s_own.append(f"Company insiders own about {f['insider_pct']:.1f}% of the shares.")
    # Largest INSTITUTIONAL holder, made clear (user 2026-06-19; clarified 2026-06-21). This is a
    # FUND, not a company insider — so it's labelled "institutional holder", never confused with the
    # "Company insiders" (officers/directors) line above (user 2026-06-21: BlackRock 7.6% was reading
    # as if it contradicted "insiders 1.5%"). And passive index-fund giants (BlackRock, Vanguard,
    # State Street, Fidelity/Geode) hold ~5-10% of nearly EVERY large cap — that's normal, not a
    # standout — so they're only surfaced if the stake is unusually large (>=15%). A concentrated
    # ACTIVE holder (e.g. Berkshire) is always surfaced; "dominant/controlling-sized" at >=20%.
    # ALWAYS show the largest institutional holder (user 2026-06-21: institutional ownership is
    # valuable validation — do NOT suppress; a big holder is further validation). It's a FUND, not
    # a company insider, so labelled "institutional holder" (never confused with the insiders line
    # above). Emphasis only where it's genuinely a concentrated ACTIVE stake (>=20% dominant /
    # >=15% notable); a normal index-fund level (e.g. BlackRock 7.6%) is just stated with direction.
    thp = f.get("top_holder_pct")
    th  = (f.get("top_holder") or "")
    if th and isinstance(thp, (int, float)) and thp >= LARGE_HOLDER_PCT:
        emph = (" — a dominant, controlling-sized stake" if thp >= 20
                else (" — a notably concentrated stake" if thp >= 15 else ""))
        chg = f.get("top_holder_change")   # rising/falling direction this period (user 2026-06-19)
        if isinstance(chg, (int, float)) and abs(chg) >= 0.5:
            emph += f", {'rising' if chg > 0 else 'falling'} ({chg:+.1f}% this period)"
        elif isinstance(chg, (int, float)):
            emph += ", holding steady"
        s_own.append(f"The largest institutional holder, {th}, owns about {thp:.1f}%{emph}.")

    # Cite authoritative sources for any overridden figure (user 2026-06-19) — only when an
    # override was actually applied (so the citation is always truthful) AND only on Slack/
    # dossier surfaces: the source of info must NEVER appear in an X tweet (user 2026-06-19, G).
    if cite_sources and f.get("overrides"):
        cites = ", ".join(f"{fld.replace('_', ' ')} per {src}" for fld, src in f["overrides"].items())
        s_cite.append(f"(Authoritative figures used: {cites} — these override the automated feed.)")

    # Each subject group is its own paragraph; blank line BETWEEN groups so the subject change is
    # visible (user 2026-06-22). Order: business fundamentals → analysts → ownership → citations.
    # KPI "Key numbers" block (user 2026-06-26, F) — its own paragraph after the fundamentals
    # narrative. Public-safe (never names the data source). Best-effort: "" when no KPIs resolve.
    _kpis = _kpi_block(tk, gbp)
    _text_groups = [" ".join(s), " ".join(s_analyst), _kpis, " ".join(s_own), " ".join(s_cite)]
    _groups = [g for g in _text_groups if g and g.strip()]
    fund  = "\n\n".join(_groups) if _groups else f"Limited fundamental data available for {name}."
    chart = _chart_story(r, name, gbp)                       # public-safe "why the setup matters"
    body  = (chart + "\n\n" + fund) if chart else fund       # lead with the chart why-now, then the quality angle
    if change_note:
        body += f"\n\nWhat's changed since the last report: {change_note}"
    return f"${disp} ({name}) — the quality angle", body


def build_tweet(r: dict) -> str:
    """Short, searchable skim tweet (key terms as text for X search). The full report
    rides on the attached PNG. Reuses the hashtag helpers from intraday_signals."""
    from intraday_signals import _resolve_name, _x_market_tags, _NFA_DISCLAIMER
    tk = r["ticker"]
    disp = tk[:-2] if tk.endswith(".L") else tk
    name = (r.get("name") or _resolve_name(tk)).split(" (")[0]
    f = fundamentals(tk)
    bits = []
    if f.get("financial"):
        if f.get("div_streak"):
            bits.append(f"dividend raised {f['div_streak']} years")
        if (f.get("roe") or 0) >= _ROE_STRONG:
            bits.append(f"{f['roe']*100:.0f}% return on equity")
    else:
        if f.get("rev_run") and f["rev_run"] >= 2:
            bits.append(f"sales up {f['rev_run']} years running")
        if f.get("fcf") and f["fcf"]["pos"]:
            bits.append("strong surplus cash")
        if f.get("net_cash"):
            bits.append("more cash than debt")
        if f.get("div_streak"):
            bits.append(f"dividend raised {f['div_streak']} years")
    if f.get("target_pct") is not None and f["target_pct"] > 0:
        from evidence_alignment import analyst_stance, relationship
        _stance = analyst_stance(buys=f.get("analyst_buys"), holds=f.get("analyst_holds"),
                                  sells=f.get("analyst_sells"), recommendation=f.get("analyst_rec"),
                                  target_pct=f.get("target_pct"))
        if relationship(r.get("hvf_type"), _stance) == "opposes":
            bits.append(f"{str(r.get('hvf_type') or '').lower()} chart conflicts with analysts' ~{f['target_pct']:.0f}% upside")
        else:
            bits.append(f"analysts see ~{f['target_pct']:.0f}% upside")
    summary = ", ".join(bits[:4]) if bits else "fundamental quality screen"
    tags = _x_market_tags(r)
    return (f"👀 ${disp} ({name}) — the quality angle\n"
            f"{summary[0].upper() + summary[1:]}. Full story in the thread 👇\n"
            f"#{disp} {tags}{_NFA_DISCLAIMER}")


# ----------------------------------------------------------------------------------------------------------------------
# Report TEXT thread (numbered 1/n parts, <=280 weighted chars each) — user 2026-06-16.
# The long narrative is published as copy-paste tweet text, not a PNG. The HVF chart card
# and the short skim tweet are unchanged; only the long report moved from image to thread.
# ----------------------------------------------------------------------------------------------------------------------

import re

_THREAD_LIMIT = 280            # X hard limit per tweet (weighted; see intraday_signals._x_weighted_len)
_THREAD_MARKER_RESERVE = 8     # room kept for a trailing " (nn/nn)" marker


def _split_sentences(text: str) -> list:
    r"""Split prose into sentence-sized atoms, preserving terminal punctuation. Paragraph
    breaks (\n\n — e.g. the 'What's changed' note) are honoured as boundaries."""
    out = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        for sent in re.split(r"(?<=[.!?])\s+", para):
            sent = sent.strip()
            if sent:
                out.append(sent)
    return out


def _hard_wrap(text: str, width: int) -> list:
    """Last-resort word wrap for a lone sentence longer than one tweet (weighted width)."""
    from intraday_signals import _x_weighted_len
    words, lines, cur = text.split(), [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if _x_weighted_len(cand) <= width:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [text]


def paginate_report_thread(title: str, body: str, tail: str) -> list:
    """Lay the long quality-report narrative out as a numbered X thread (user 2026-06-16).

    Returns a list of strings, each <= 280 WEIGHTED chars INCLUDING its trailing "(i/n)"
    marker. Part 1 leads with the title/hook; `tail` (market hashtags + "Not financial
    advice.") is appended to the last part, or becomes its own final part if it will not
    fit. Sentences are never split mid-word (an over-long lone sentence is hard-wrapped
    only as a fallback)."""
    from intraday_signals import _x_weighted_len
    budget = _THREAD_LIMIT - _THREAD_MARKER_RESERVE
    parts, cur, title_pending = [], (title or ""), bool(title)
    for sent in _split_sentences(body):
        sep = "\n\n" if title_pending else (" " if cur else "")
        candidate = f"{cur}{sep}{sent}" if cur else sent
        if _x_weighted_len(candidate) <= budget:
            cur, title_pending = candidate, False
        else:
            if cur:
                parts.append(cur)
                cur, title_pending = "", False
            if _x_weighted_len(sent) > budget:                 # one sentence won't fit a tweet
                chunks = _hard_wrap(sent, budget)
                parts.extend(chunks[:-1])
                cur = chunks[-1]
            else:
                cur = sent
    if cur:
        parts.append(cur)
    if not parts:
        parts = [title or ""]
    if tail:
        joined = f"{parts[-1]}\n\n{tail}"
        if _x_weighted_len(joined) <= budget:
            parts[-1] = joined
        else:
            parts.append(tail)
    n = len(parts)
    return [f"{p} ({i}/{n})" for i, p in enumerate(parts, 1)]


# ----------------------------------------------------------------------------------------------------------------------
# Daily change-detection vs the most recent PRIOR hvf_scan_log row
# ----------------------------------------------------------------------------------------------------------------------

def _changes(ticker: str, entry, stop, target, rr) -> tuple:
    """Compare to the latest prior hvf_scan_log row. Returns (publish: bool, note: str|None).
    First-seen → publish (no note). entry/target/R:R moved → publish (+ plain note).
    Unchanged → skip."""
    def _moved(a, b, tol=0.005):
        if a is None or b is None:
            return a != b
        return abs(float(a) - float(b)) > tol * max(1.0, abs(float(b)))
    try:
        from db_pool import get_db
        db = get_db()
        rows = db.run(
            """select entry_level, stop_level, target, risk_reward
                 from hvf_scan_log
                where ticker = :t and scan_time::date < current_date
                order by scan_time desc limit 1""", t=ticker)
        db.close()
    except Exception as e:
        log.debug(f"{ticker}: change lookup failed: {e}")
        return True, None
    if not rows:
        return True, None                      # first time we've reported this name
    pe, ps, pt, prr = rows[0]
    changes = []
    if _moved(entry, pe):   changes.append(f"entry {pe:g}→{entry:g}")
    if _moved(target, pt):  changes.append(f"target {pt:g}→{target:g}")
    if _moved(rr, prr):     changes.append(f"R:R {float(prr):.1f}:1→{float(rr):.1f}:1")
    if not changes:
        return False, None                     # nothing material moved — don't re-publish
    return True, "; ".join(changes) + " (re-checked today)."


# ----------------------------------------------------------------------------------------------------------------------
# Publish to #arw-claude-twitter (text via webhook + PNG via bot-token upload) — same flow as _generate_x_drafts
# ----------------------------------------------------------------------------------------------------------------------

def _post(tweet: str, thread: list, ticker: str, name: str, rank: int, total: int):
    """Post the skim tweet + the numbered narrative thread to #arw-claude-twitter as
    copy-paste-ready code blocks (one fence per tweet). Text-only via the SLACK_TWITTER
    webhook — the long report is no longer a PNG (user 2026-06-16)."""
    import requests
    from notify import slack_enabled
    if not slack_enabled("twitter"):   # per-channel switch (user 2026-08-03)
        log.info("Slack channel 'twitter' disabled — quality report not posted")
        return
    slack_url = os.environ.get("SLACK_TWITTER", "")
    if not slack_url:
        log.warning("SLACK_TWITTER not set — quality report not posted. Skim tweet + thread below:\n"
                    + tweet + "\n\n" + "\n\n".join(thread))
        return
    blocks = [
        {"type": "header", "text": {"type": "plain_text",
                                    "text": f"Quality report {rank}/{total} — {ticker} ({name})"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Skim tweet:*\n```{tweet}```"}},
        {"type": "section", "text": {"type": "mrkdwn",
                                     "text": f"*Thread — {len(thread)} part(s), copy each block:*"}},
    ]
    for part in thread:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"```{part}```"}})
    try:
        requests.post(slack_url, json={"blocks": blocks}, timeout=10)
        log.info(f"quality report posted for {ticker} ({len(thread)}-part thread)")
    except Exception as e:
        log.error(f"quality report Slack post failed for {ticker}: {e}")


def publish_quality_reports(setups: list, limit: int = 10, changed_only: bool = False):
    """For the top `limit` tradeable setups (weight order), publish a quality report.
    changed_only=False (initial/manual): publish all, with a "what's changed" note where
    levels moved. changed_only=True (daily): publish ONLY names first-seen or whose
    entry/target/R:R moved. `setups` are HVF result dicts (ticker, hvf_type,
    h3_level/stop_level/target, risk_reward, index, name)."""
    import requests
    from notify import slack_enabled
    if not slack_enabled("twitter"):   # per-channel switch (user 2026-08-03)
        log.info("Slack channel 'twitter' disabled — quality reports not published")
        return
    from intraday_signals import _resolve_name, _x_market_tags, _NFA_DISCLAIMER
    from price_action import market_short
    from config import PER_MARKET_TOP_N
    slack_url = os.environ.get("SLACK_TWITTER", "")
    top = setups[:limit]
    total = len(top)
    published = 0
    last_market = None
    for rank, r in enumerate(top, 1):
        tk = r.get("ticker", "")
        entry, stop, target, rr = r.get("h3_level"), r.get("stop_level"), r.get("target"), r.get("risk_reward")
        do_pub, note = _changes(tk, entry, stop, target, rr)
        if changed_only and not do_pub:
            log.info(f"{tk}: entry/target/R:R unchanged — quality report skipped")
            continue
        # Per-market section header (user 2026-06-16: "top 10 by market") — one divider+header
        # per market group, only when actually posting (SLACK_TWITTER set) and the market changes.
        mkt = r.get("index")
        if slack_url and mkt and mkt != last_market:
            last_market = mkt
            try:
                requests.post(slack_url, json={"blocks": [
                    {"type": "divider"},
                    {"type": "header", "text": {"type": "plain_text",
                                                "text": f"📊 {market_short(mkt)} — quality reports (top {PER_MARKET_TOP_N})"}},
                ]}, timeout=10)
            except Exception as e:
                log.debug(f"quality report market header failed for {mkt}: {e}")
        r.setdefault("name", _resolve_name(tk))
        title, body = build_report(r, change_note=note)
        tweet = build_tweet(r)
        disp = tk[:-2] if tk.endswith(".L") else tk
        tail = f"#{disp} {_x_market_tags(r)}{_NFA_DISCLAIMER}"   # hashtags + "Not financial advice." on the last part
        thread = paginate_report_thread(title, body, tail)
        _post(tweet, thread, tk, r["name"], rank, total)
        published += 1
    log.info(f"quality reports: {published}/{total} published")
    return published


def publish_long_report_for(r: dict, post: bool = True) -> list:
    """Post ONLY the long quality report (1/n thread) for ONE instrument to #arw-claude-twitter.

    A complete publication = card PNG + short tweet + this long thread (user 2026-06-16); this is
    the hook that pairs the long report with every short+PNG publication and dossier run (called
    from intraday_signals._generate_x_drafts). The SHORT summary (component B) is the X draft, so
    here we send the THREAD only — no skim tweet. Returns the thread parts (also when post=False)."""
    import requests
    from intraday_signals import _resolve_name, _x_market_tags, _NFA_DISCLAIMER
    tk = r.get("ticker", "")
    if not tk:
        return []
    r.setdefault("name", _resolve_name(tk))
    title, body = build_report(r)
    disp = tk[:-2] if tk.endswith(".L") else tk
    tail = f"#{disp} {_x_market_tags(r)}{_NFA_DISCLAIMER}"
    thread = paginate_report_thread(title, body, tail)
    if not post:
        return thread
    from notify import slack_enabled
    if not slack_enabled("twitter"):   # per-channel switch (user 2026-08-03)
        log.info(f"Slack channel 'twitter' disabled — long quality report not posted for {tk}")
        return thread
    slack_url = os.environ.get("SLACK_TWITTER", "")
    if not slack_url:
        log.warning(f"SLACK_TWITTER not set — long quality report not posted for {tk}")
        return thread
    blocks = [{"type": "section", "text": {"type": "mrkdwn",
               "text": f"*Long report — {tk} ({r['name']}) — {len(thread)} part(s), copy each block:*"}}]
    for part in thread:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"```{part}```"}})
    try:
        requests.post(slack_url, json={"blocks": blocks}, timeout=10)
        log.info(f"long quality report posted for {tk} ({len(thread)}-part thread)")
    except Exception as e:
        log.error(f"long quality report Slack post failed for {tk}: {e}")
    return thread


# ----------------------------------------------------------------------------------------------------------------------
# Standalone entry — today's top tradeable from hvf_scan_log, re-scanned live for fresh levels + fundamentals
# ----------------------------------------------------------------------------------------------------------------------

def _today_top(per_market: int) -> list:
    """Today's tradeable HVF setups, top `per_market` PER market (user 2026-06-16: "top 10
    by market"), grouped and ordered by MARKET_ORDER. Returns [(ticker, index_name)]."""
    from db_pool import get_db
    db = get_db()
    try:
        rows = db.run(
            """select distinct on (ticker) ticker, hvf_signal, pattern_quality, index_name, risk_reward
                 from hvf_scan_log
                where scan_time::date = current_date and hvf_signal in ('READY', 'TRIGGERED')
                order by ticker, recorded_at desc""")
    finally:
        db.close()
    from price_action import hvf_weight, group_by_market   # row = (ticker, signal, quality, index, risk_reward)
    from config import MARKET_ORDER
    rows.sort(key=lambda r: hvf_weight(r[1], r[2], r[4]))   # R:R-first (user 2026-06-19)
    groups = group_by_market(rows, n=per_market, market_of=lambda r: r[3], market_order=MARKET_ORDER)
    return [(r[0], r[3]) for _, mrows in groups for r in mrows]


def main():
    try:                                            # UTF-8 stdout for the script (not on import)
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass
    from dotenv import load_dotenv
    load_dotenv(override=True)
    args = sys.argv[1:]
    daily = "--daily" in args                       # daily run: only publish changed setups
    args = [a for a in args if a != "--daily"]
    from config import PER_MARKET_TOP_N
    if args and args[0].isdigit():
        pairs = _today_top(int(args[0]))      # numeric arg = count PER market (user 2026-06-16)
    elif args:
        pairs = [(a, None) for a in args]
    else:
        pairs = _today_top(PER_MARKET_TOP_N)
    if not pairs:
        log.info("No tradeable setups today — nothing to publish.")
        return

    from price_action import get_hvf_signal_mtf, get_trend_structure
    setups = []
    for tk, idx in pairs:
        try:
            res = get_hvf_signal_mtf(tk, trend_hint=get_trend_structure(tk))
            if res.get("hvf_type"):
                res["ticker"] = tk
                res["index"] = idx
                setups.append(res)
        except Exception as e:
            log.warning(f"{tk}: re-scan failed: {e}")
    publish_quality_reports(setups, limit=len(setups), changed_only=daily)


if __name__ == "__main__":
    main()
