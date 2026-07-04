# ======================================================================================================================
# File:         hot_stock_tweet.py
# Author:       Alex Hind
# Created:      2026-06-30
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Daily "hot stock, three topics" X tweet (user 2026-06-30). Picks ONE equity from our monitored table with the most
# engagement potential — scored from tracked-account chatter (notable_investors, 7d), recent price momentum and any
# live HVF signal — and posts a single opinionated tweet covering three topics: the chart, the valuation and the
# street. Our own voice throughout: NO articles, accounts or data providers are ever referenced (X-public rule).
#
# Selection score (per equity in UNIVERSE, FX/commodities/indices/crypto excluded):
#   mentions_7d * 3  +  |5-day move %|  +  HVF bonus (TRIGGERED 10 / READY 6, + quality/10)
# Tickers published to X in the last 72h are skipped (variety), and the X_MAX_PER_DAY budget is respected.
#
# Usage:
#   python hot_stock_tweet.py --dry            # build + print, post nothing
#   python hot_stock_tweet.py                  # pick, post to X, record + Slack-confirm (link only)
#   python hot_stock_tweet.py --ticker META    # force the instrument (still dedup/cap checked)
#
# Env: X_API_KEY/X_API_SECRET/X_ACCESS_TOKEN/X_ACCESS_SECRET (Actions only), SUPABASE_USER,
#      SUPABASE_DB_PASSWORD, SLACK_TWITTER.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-30  Alex Hind   Initial build — engagement-scored pick, three-topic opinion tweet, cap/dedup/record.
# ======================================================================================================================

import argparse
import logging
import os

from dotenv import load_dotenv; load_dotenv(override=True)

log = logging.getLogger("hot_stock_tweet")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

RECENT_PUBLISH_HOURS = 72     # don't pick a name we've tweeted about in the last 3 days
MAX_TWEET_CHARS      = 275


def _equity_universe() -> list:
    """Equity tickers only (stocks have the three topics; FX/commodities/indices/crypto don't)."""
    from run_hvf_report import UNIVERSE
    out = []
    for market in ("FTSE 100", "FTSE 250", "NASDAQ 100", "S&P 500"):
        out.extend(UNIVERSE.get(market, []))
    return list(dict.fromkeys(out))      # de-dup, order-preserving


def _db_facts():
    """One Supabase pass: {ticker: mentions_7d}, {ticker: (hvf_signal, quality)}, {ticker} recently tweeted."""
    from db_pool import get_db
    mentions, hvf, recent = {}, {}, set()
    db = get_db()
    try:
        for t, c in (db.run("select ticker, count(*) from notable_investors "
                            "where disclosed_at > now() - interval '7 days' group by ticker") or []):
            mentions[t] = int(c)
        for t, s, q in (db.run("select distinct on (ticker) ticker, hvf_signal, hvf_quality from signal_log "
                               "where session_time > now() - interval '7 days' and hvf_signal in ('TRIGGERED','READY') "
                               "order by ticker, session_time desc") or []):
            hvf[t] = (s, q or 0)
        for (t,) in (db.run(f"select distinct ticker from x_publications "
                            f"where published_at > now() - interval '{RECENT_PUBLISH_HOURS} hours'") or []):
            recent.add(t)
    finally:
        db.close()
    return mentions, hvf, recent


def _week_move_pct(ticker: str):
    """5-trading-day close-to-close move %, or None."""
    try:
        import yfinance as yf
        try:
            from config import YAHOO_MAP
        except Exception:
            YAHOO_MAP = {}
        h = yf.download(YAHOO_MAP.get(ticker, ticker), period="1mo", progress=False, auto_adjust=True)
        if h is None or h.empty:
            return None
        c = h["Close"].squeeze().dropna()
        if len(c) < 6:
            return None
        return (float(c.iloc[-1]) / float(c.iloc[-6]) - 1) * 100
    except Exception:
        return None


def pick_hot_stock(force_ticker: str = None):
    """Choose the equity with the most engagement potential. Returns (ticker, facts dict) or (None, {})."""
    mentions, hvf, recent = _db_facts()
    if force_ticker:
        return force_ticker, {"mentions": mentions.get(force_ticker, 0), "hvf": hvf.get(force_ticker)}
    uni = set(_equity_universe())
    # Only price candidates that have SOME engagement signal — pricing 500+ names would hammer yfinance.
    cands = [t for t in (set(mentions) | set(hvf)) & uni if t not in recent]
    scored = []
    for t in cands:
        mv = _week_move_pct(t)
        sig = hvf.get(t)
        bonus = (10 if sig and sig[0] == "TRIGGERED" else 6 if sig else 0) + ((sig[1] / 10) if sig else 0)
        scored.append((mentions.get(t, 0) * 3 + abs(mv or 0) + bonus, t, mv))
    if not scored:
        return None, {}
    scored.sort(reverse=True)
    _, ticker, mv = scored[0]
    log.info("pick: " + ", ".join(f"{t}={s:.1f}" for s, t, _ in scored[:5]))
    return ticker, {"mentions": mentions.get(ticker, 0), "hvf": hvf.get(ticker), "move": mv}


def _street_net(ticker: str):
    """(upgrades, downgrades) over ~6 months from analyst actions — never named in the tweet."""
    try:
        import yfinance as yf
        import pandas as pd
        try:
            from config import YAHOO_MAP
        except Exception:
            YAHOO_MAP = {}
        ud = yf.Ticker(YAHOO_MAP.get(ticker, ticker)).upgrades_downgrades
        if ud is None or ud.empty:
            return None
        now = pd.Timestamp.now(tz="UTC")
        up = down = 0
        for dt, row in ud.iterrows():
            d = pd.Timestamp(dt)
            d = d.tz_localize("UTC") if d.tzinfo is None else d.tz_convert("UTC")
            if (now - d).days > 183:
                continue
            act = str(row.get("Action", "")).lower()
            up += (act == "up")
            down += (act == "down")
        return (up, down)
    except Exception:
        return None


def build_tweet(ticker: str, facts: dict) -> str:
    """Compose the three-topic opinion tweet. Data-driven wording, our voice, no sources named."""
    from instrument_name import company_name
    disp = ticker[:-2] if ticker.endswith(".L") else ticker
    name = company_name(ticker) or ""
    mv = facts.get("move")
    if mv is None:
        mv = _week_move_pct(ticker)
    sig = facts.get("hvf")
    mentions = facts.get("mentions", 0)

    # 1) Chart
    if sig:
        chart = ("fresh breakout from a long squeeze — momentum's job to hold it"
                 if sig[0] == "TRIGGERED" else "coiled in a tightening squeeze — a decisive move is close")
    elif isinstance(mv, (int, float)) and abs(mv) >= 4:
        chart = f"{'up' if mv > 0 else 'down'} {abs(mv):.0f}% in a week and {'holding the gains' if mv > 0 else 'still looking heavy'}"
    else:
        chart = "quiet tape, tight range — the kind that doesn't stay quiet"

    # 2) Value (yfinance .info, phrased as our read)
    value = "valuation is the debate — growth has to do the talking"
    try:
        import yfinance as yf
        try:
            from config import YAHOO_MAP
        except Exception:
            YAHOO_MAP = {}
        info = yf.Ticker(YAHOO_MAP.get(ticker, ticker)).info or {}
        pe = info.get("forwardPE") or info.get("trailingPE")
        gr = info.get("earningsGrowth")
        if isinstance(pe, (int, float)) and pe > 0:
            if isinstance(gr, (int, float)):
                tone = ("cheap for that growth" if pe / max(gr * 100, 1) < 1.2 and gr > 0
                        else "priced for perfection" if pe > 35 else "fair, not a bargain")
                value = f"~{pe:.0f}x forward with {gr * 100:+.0f}% earnings growth — {tone}"
            else:
                value = f"~{pe:.0f}x forward — {'demanding' if pe > 35 else 'undemanding'} multiple"
    except Exception:
        pass

    # 3) Street + chatter (generic — no accounts/providers named)
    street = "opinion is split — which is usually where the opportunity lives"
    net = _street_net(ticker)
    if net and (net[0] or net[1]):
        up, down = net
        street = (f"upgrades outnumber downgrades {up}-{down} over 6mo" if up > down
                  else f"{down} downgrades vs {up} upgrades in 6mo — sentiment washed out?" if down > up
                  else f"street evenly split at {up}-{down} over 6mo")
    if mentions >= 3:
        street += "; chatter is building"

    q = ("Overheating or just warming up?" if (isinstance(mv, (int, float)) and mv > 0)
         else "Overreaction or fair repricing?")
    nm = f" ({name})" if name and len(name) <= 32 else ""
    tweet = (f"${disp}{nm} — three things on my mind:\n"
             f"1) Chart: {chart}.\n"
             f"2) Value: {value}.\n"
             f"3) Street: {street}.\n"
             f"{q} NFA")
    # Trim to fit — the closing question is the engagement hook, so it goes LAST:
    # 1) drop the company name, 2) drop the chatter suffix, 3) only then drop the question.
    if len(tweet) > MAX_TWEET_CHARS and nm:
        tweet = tweet.replace(nm, "", 1)
    if len(tweet) > MAX_TWEET_CHARS:
        tweet = tweet.replace("; chatter is building", "", 1)
    if len(tweet) > MAX_TWEET_CHARS:
        tweet = tweet.replace(f"\n{q} NFA", "\nNFA")
    return tweet


def main():
    ap = argparse.ArgumentParser(description="Daily hot-stock three-topic X tweet.")
    ap.add_argument("--dry", action="store_true", help="build + print only; post nothing")
    ap.add_argument("--ticker", type=str, help="force the instrument (skips the engagement pick)")
    a = ap.parse_args()

    from publish_one_to_x import _published_today_count, _record_publication, _confirm_to_slack
    from config import X_MAX_PER_DAY as _X_DEFAULT
    try:
        from config_store import cfg_num
        X_MAX_PER_DAY = int(cfg_num("x_max_per_day", _X_DEFAULT))
    except Exception:
        X_MAX_PER_DAY = _X_DEFAULT
    if not a.dry and _published_today_count() >= X_MAX_PER_DAY:
        log.info(f"daily X cap ({X_MAX_PER_DAY}) already reached — hot-stock tweet skipped.")
        return

    ticker, facts = pick_hot_stock(a.ticker)
    if not ticker:
        log.info("no candidate with engagement signals today — nothing to tweet.")
        return
    tweet = build_tweet(ticker, facts)
    log.info(f"hot stock: {ticker} | {len(tweet)} chars")
    print(tweet.encode("ascii", "replace").decode())     # temp/console output only (ASCII-safe)
    if a.dry:
        return

    from x_publish import publish_thread_to_x
    lead_id, n, all_ids = publish_thread_to_x(tweet, None, [])
    if n >= 1:
        _record_publication(ticker, lead_id, all_ids)
        _confirm_to_slack(ticker, ticker, lead_id, n, 0)
        log.info(f"hot-stock tweet posted (lead {lead_id}).")
    else:
        log.error("hot-stock tweet: nothing posted.")


if __name__ == "__main__":
    main()
