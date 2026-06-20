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
    f["mcap"] = info.get("marketCap")
    f["industry"] = info.get("industry")
    ins = info.get("heldPercentInsiders")
    f["insider_pct"] = ins * 100 if isinstance(ins, (int, float)) and ins > 0 else None
    # Insider stake as a £/$ VALUE (user 2026-06-14: a % alone misleads on a big-cap —
    # 0.1% of a giant market cap is still a large sum).
    f["insider_value"] = (ins * f["mcap"]) if (f.get("insider_pct") and f.get("mcap")) else None

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
    "The chart adds the why-now. {name} has been squeezing into an ever-narrower range for months.",
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
    return " ".join(parts)


def build_report(r: dict, change_note: str = None) -> tuple:
    """(title, prose_body) — plain English, one fact per short sentence, phrasing varied
    per instrument so each report reads bespoke (user 2026-06-14)."""
    from intraday_signals import _resolve_name
    tk = r["ticker"]
    gbp = tk.endswith(".L")
    name = r.get("name") or _resolve_name(tk)
    disp = tk[:-2] if tk.endswith(".L") else tk
    f = fundamentals(tk)
    s = []

    if f.get("financial"):
        s.append(_pick(_P_FIN_CAVEAT, tk, "cav").format(name=name, industry=(f.get("industry") or "financial services")))
        if f.get("div_streak"):
            s.append(_pick(_P_DIV, tk, "div").format(streak=f["div_streak"]))
        if f.get("roe"):
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
        if f.get("roe"):
            s.append(_pick(_P_ROE, tk, "roe").format(roe=f"{f['roe']*100:.0f}"))
        if f.get("div_streak"):
            s.append(_pick(_P_DIV, tk, "div").format(streak=f["div_streak"]))

    if f.get("target_pct") is not None:
        d = "above" if f["target_pct"] >= 0 else "below"
        rec = f'rate it "{f["analyst_rec"]}" and ' if f.get("analyst_rec") else ""
        pct = f"{abs(f['target_pct']):.0f}"
        if f.get("analyst_n"):
            s.append(_pick(_P_ANALYST, tk, "an").format(n=f["analyst_n"], rec=rec, pct=pct, dir=d))
        else:
            s.append(f"Analysts {rec}on average see the shares worth about {pct}% {d} today's price.")
    if f.get("insider_value") is not None:
        s.append(_pick(_P_INSIDER, tk, "ins").format(value=_money(f["insider_value"], gbp), pct=f"{f['insider_pct']:.1f}"))
    elif f.get("insider_pct") is not None:
        s.append(f"Company insiders own about {f['insider_pct']:.1f}% of the shares.")

    fund  = " ".join(s) if s else f"Limited fundamental data available for {name}."
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
        if f.get("roe"):
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
