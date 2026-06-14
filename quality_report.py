# ======================================================================================================================
# File:         quality_report.py
# Author:       Alex Hind
# Created:      2026-06-14
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Per-instrument "quality angle" publication for the top HVF setups: a plain-English (common-man, NOT accountant)
# NARRATIVE report — rendered as a PNG — plus a short, searchable companion tweet. Posted to #arw-claude-twitter
# (SLACK_TWITTER webhook for text + SLACK_BOT_TOKEN/SLACK_TWITTER_CHANNEL_ID for the PNG), exactly like the HVF X drafts.
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
# Usage:   python quality_report.py            # top 10 of today's tradeable HVF setups
#          python quality_report.py 5          # top 5
#          python quality_report.py NVDA MGNS.L
#
# Env (in GitHub Secrets, not local .env): SUPABASE_USER/SUPABASE_DB_PASSWORD, SLACK_TWITTER,
#                                          SLACK_BOT_TOKEN, SLACK_TWITTER_CHANNEL_ID
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
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
        if f.get("rev_run"):
            s.append(_pick(_P_GROWTH, tk, "grw").format(name=name, ry=f["rev_run"], rev=_money(f.get("rev_latest"), gbp)))
            if f.get("rev_cagr"):
                s.append(_pick(_P_RATE, tk, "rate").format(rate=f"{f['rev_cagr']*100:.0f}"))
        if f.get("ni_run"):
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

    body = " ".join(s) if s else f"Limited fundamental data available for {name}."
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
        if f.get("rev_run"):
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
            f"{summary[0].upper() + summary[1:]}. Full story in the image 👇\n"
            f"#{disp} {tags}{_NFA_DISCLAIMER}")


# ----------------------------------------------------------------------------------------------------------------------
# Report PNG (matplotlib text card — same dark style as the HVF card)
# ----------------------------------------------------------------------------------------------------------------------

def render_report_card(title: str, body: str):
    """Render the prose report to a dark PNG card. 'Not financial advice.' styled like
    the HVF card (grey, italic). Returns PNG bytes (None on failure)."""
    import textwrap
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    try:
        paras = body.split("\n\n")
        wrapped = "\n\n".join(textwrap.fill(p, width=84) for p in paras)
        n_lines = wrapped.count("\n") + 1
        height = max(4.5, 1.5 + 0.30 * n_lines)
        fig = plt.figure(figsize=(11, height))
        fig.patch.set_facecolor("#0d1117")
        fig.text(0.04, 0.93, title, color="#ffffff", fontsize=16, fontweight="bold",
                 va="top", ha="left")
        fig.text(0.04, 0.80, wrapped, color="#c9d1d9", fontsize=12.5, va="top",
                 ha="left", linespacing=1.55)
        fig.text(0.04, 0.05, "Not financial advice.", color="#8b949e", fontsize=10,
                 style="italic", va="bottom", ha="left")
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=140, facecolor="#0d1117")
        plt.close(fig)
        return buf.getvalue()
    except Exception as e:
        log.warning(f"report card render failed: {e}")
        return None


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

def _post(tweet: str, png: bytes, ticker: str, name: str, rank: int, total: int):
    import requests
    slack_url = os.environ.get("SLACK_TWITTER", "")
    if not slack_url:
        log.warning("SLACK_TWITTER not set — quality report not posted (text below):\n" + tweet)
        return
    blocks = [
        {"type": "header", "text": {"type": "plain_text",
                                    "text": f"Quality report {rank}/{total} — {ticker}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Tweet:*\n```{tweet}```"}},
    ]
    bot, chan = os.environ.get("SLACK_BOT_TOKEN", ""), os.environ.get("SLACK_TWITTER_CHANNEL_ID", "")
    if png and not (bot and chan):
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
                       "text": "_Report PNG generated but not attached — bot token/channel not set_"}})
    try:
        requests.post(slack_url, json={"blocks": blocks}, timeout=10)
        log.info(f"quality report posted for {ticker}")
    except Exception as e:
        log.error(f"quality report Slack post failed for {ticker}: {e}")
    if png and bot and chan:
        try:
            hdrs = {"Authorization": f"Bearer {bot}"}
            fname = f"quality_{ticker.replace('.', '_')}.png"
            r1 = requests.post("https://slack.com/api/files.getUploadURLExternal", headers=hdrs,
                               data={"filename": fname, "length": len(png)}, timeout=10).json()
            if not r1.get("ok"):
                raise RuntimeError(f"getUploadURLExternal: {r1.get('error')}")
            requests.post(r1["upload_url"], data=png, timeout=30).raise_for_status()
            r3 = requests.post("https://slack.com/api/files.completeUploadExternal",
                               headers={**hdrs, "Content-Type": "application/json"},
                               json={"files": [{"id": r1["file_id"], "title": f"Quality report — {ticker} ({name})"}],
                                     "channel_id": chan}, timeout=10).json()
            if not r3.get("ok"):
                raise RuntimeError(f"completeUploadExternal: {r3.get('error')}")
            log.info(f"quality report PNG attached for {ticker}")
        except Exception as e:
            log.error(f"quality report PNG upload failed for {ticker}: {e}")


def publish_quality_reports(setups: list, limit: int = 10, changed_only: bool = False):
    """For the top `limit` tradeable setups (weight order), publish a quality report.
    changed_only=False (initial/manual): publish all, with a "what's changed" note where
    levels moved. changed_only=True (daily): publish ONLY names first-seen or whose
    entry/target/R:R moved. `setups` are HVF result dicts (ticker, hvf_type,
    h3_level/stop_level/target, risk_reward, index, name)."""
    from intraday_signals import _resolve_name
    top = setups[:limit]
    total = len(top)
    published = 0
    for rank, r in enumerate(top, 1):
        tk = r.get("ticker", "")
        entry, stop, target, rr = r.get("h3_level"), r.get("stop_level"), r.get("target"), r.get("risk_reward")
        do_pub, note = _changes(tk, entry, stop, target, rr)
        if changed_only and not do_pub:
            log.info(f"{tk}: entry/target/R:R unchanged — quality report skipped")
            continue
        r.setdefault("name", _resolve_name(tk))
        title, body = build_report(r, change_note=note)
        tweet = build_tweet(r)
        png = render_report_card(title, body)
        _post(tweet, png, tk, r["name"], rank, total)
        published += 1
    log.info(f"quality reports: {published}/{total} published")
    return published


# ----------------------------------------------------------------------------------------------------------------------
# Standalone entry — today's top tradeable from hvf_scan_log, re-scanned live for fresh levels + fundamentals
# ----------------------------------------------------------------------------------------------------------------------

def _today_top(limit: int) -> list:
    from db_pool import get_db
    db = get_db()
    try:
        rows = db.run(
            """select distinct on (ticker) ticker, hvf_signal, pattern_quality, index_name
                 from hvf_scan_log
                where scan_time::date = current_date and hvf_signal in ('READY', 'TRIGGERED')
                order by ticker, recorded_at desc""")
    finally:
        db.close()
    from price_action import hvf_weight          # (ticker, hvf_signal, pattern_quality, index_name)
    rows.sort(key=lambda r: hvf_weight(r[1], r[2]))
    return [(r[0], r[3]) for r in rows[:limit]]


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
    if args and args[0].isdigit():
        pairs = _today_top(int(args[0]))
    elif args:
        pairs = [(a, None) for a in args]
    else:
        pairs = _today_top(10)
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
