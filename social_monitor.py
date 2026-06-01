# =============================================================================
# File:         social_monitor.py
# Author:       Alex Hind
# Created:      2026-06-01
#
# Description:
# -----------------------------------------------------------------------------
# Monitors X (Twitter) accounts via Nitter RSS feeds for new stock picks.
# Extracts ticker mentions, looks up IG epics, runs price action analysis,
# and alerts via Slack when new actionable picks are found.
#
# No X API subscription required — uses public Nitter RSS feeds.
#
# Tracked accounts:
#   @leopoldasch      — Leopold Aschenbrenner, AI infrastructure picks
#   @LeopoldATracker  — Profitable trading account, AI/crypto/energy picks
#   @Asklivermoe      — Strong equity picker (handle to verify)
#   @realDonaldTrump  — Trump stock mentions (when available)
#
# Nitter instances (tried in order until one works):
#   nitter.net, nitter.poast.org, nitter.cz
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-06-01  Alex Hind   Initial build. RSS parsing, ticker extraction,
#                                 IG epic lookup, price action scan, Slack alert.
# =============================================================================

import os
import re
import logging
import requests
import xml.etree.ElementTree as ET
import pg8000.native
from datetime import datetime, timezone, timedelta

log = logging.getLogger("social_monitor")

SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"
SUPABASE_USER = os.environ["SUPABASE_USER"]
SUPABASE_PASS = os.environ["SUPABASE_DB_PASSWORD"]

# Nitter instances — tried in order
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.poast.org",
    "https://nitter.cz",
    "https://nitter.1d4.us",
]

# Tracked X accounts
TRACKED_ACCOUNTS = [
    {"handle": "leopoldasch",     "name": "Leopold Aschenbrenner", "source": "X/@leopoldasch"},
    {"handle": "LeopoldATracker", "name": "LeopoldATracker",       "source": "X/@LeopoldATracker"},
    {"handle": "Asklivermoe",     "name": "Asklivermoe",           "source": "X/@Asklivermoe"},
]

# Known non-ticker uppercase words to ignore
IGNORE_WORDS = {
    "AI", "US", "UK", "THE", "AND", "FOR", "NOT", "BUT", "WITH", "THIS",
    "THAT", "FROM", "HAVE", "WILL", "YOUR", "THEIR", "THEY", "BEEN",
    "ARE", "WAS", "HAS", "HAD", "CAN", "DID", "HOW", "WHY", "WHAT",
    "WHO", "ALL", "NEW", "TOP", "BIG", "GET", "GOT", "JUST", "NOW",
    "AGI", "API", "GPT", "LLM", "ETF", "IPO", "USD", "GBP", "EUR",
    "CEO", "CFO", "SEC", "IPO", "ATH", "ATL", "EPS", "PE", "ROI",
    "LEAP", "CALL", "PUT", "OTM", "ITM", "ATM", "SPY", "QQQ", "SPX",
    "IMO", "FYI", "TBH", "AFAIK", "LOL", "OMG", "WTF",
}

# Known valid tickers we track
KNOWN_TICKERS = {
    "NBIS", "CRWV", "BE", "IREN", "APLD", "RIOT", "CLSK", "BTDR", "TE",
    "SEI", "SNDK", "IBM", "DELL", "NOW", "NOK", "PLTR", "CRWD", "NVDA",
    "META", "MSFT", "AAPL", "HIVE", "KEEL", "WYFI", "USAR", "DJT",
    "PATH", "OUST", "AMD", "AVGO", "TSLA", "AMZN", "GOOGL",
}


# =============================================================================
# Database helper
# =============================================================================

def get_db():
    return pg8000.native.Connection(
        host=SUPABASE_HOST, port=6543, database="postgres",
        user=SUPABASE_USER, password=SUPABASE_PASS, ssl_context=True
    )


# =============================================================================
# Fetch RSS feed from Nitter
# =============================================================================

def fetch_rss(handle: str, max_age_hours: int = 24) -> list:
    """
    Fetch recent posts from a Nitter RSS feed.
    Tries multiple Nitter instances until one responds.
    Returns list of dicts: {title, content, published, url}
    Only returns posts from the last max_age_hours.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    for instance in NITTER_INSTANCES:
        url = f"{instance}/{handle}/rss"
        try:
            resp = requests.get(url, timeout=10,
                                headers={"User-Agent": "EndToEndTrading/1.0"})
            if resp.status_code != 200:
                continue

            root = ET.fromstring(resp.content)
            channel = root.find("channel")
            if channel is None:
                continue

            posts = []
            for item in channel.findall("item"):
                title   = item.findtext("title", "")
                content = item.findtext("description", "")
                pub_str = item.findtext("pubDate", "")
                link    = item.findtext("link", "")

                # Parse date
                try:
                    from email.utils import parsedate_to_datetime
                    pub_dt = parsedate_to_datetime(pub_str)
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pass

                posts.append({
                    "title":     title,
                    "content":   content,
                    "published": pub_str,
                    "url":       link,
                    "handle":    handle,
                })

            log.info(f"@{handle}: {len(posts)} posts in last {max_age_hours}h from {instance}")
            return posts

        except Exception as e:
            log.debug(f"Nitter {instance} failed for @{handle}: {e}")
            continue

    log.warning(f"All Nitter instances failed for @{handle}")
    return []


# =============================================================================
# Extract ticker mentions from post text
# =============================================================================

def extract_tickers(text: str) -> list:
    """
    Extract stock ticker symbols from post text.

    Looks for:
    1. $TICKER format (most reliable)
    2. Known tickers mentioned in plain text
    3. 2-6 uppercase letters not in the ignore list

    Returns deduplicated list of tickers found.
    """
    tickers = set()

    # Strip HTML tags
    clean = re.sub(r"<[^>]+>", " ", text)

    # Pattern 1: $TICKER (cashtag — most reliable)
    cashtags = re.findall(r"\$([A-Z]{1,6})", clean)
    for t in cashtags:
        if t not in IGNORE_WORDS:
            tickers.add(t)

    # Pattern 2: Known tickers in plain text
    words = re.findall(r"\b([A-Z]{2,6})\b", clean)
    for w in words:
        if w in KNOWN_TICKERS:
            tickers.add(w)

    return sorted(tickers)


# =============================================================================
# Check if ticker is already tracked
# =============================================================================

def is_new_ticker(ticker: str, investor_name: str) -> bool:
    """Return True if this ticker hasn't been seen from this investor before."""
    db = get_db()
    try:
        rows = db.run(
            "select id from notable_investors where ticker=:t and investor_name=:inv limit 1",
            t=ticker, inv=investor_name
        )
        return len(rows) == 0
    finally:
        db.close()


# =============================================================================
# Save new ticker to Supabase and look up epic
# =============================================================================

def save_new_pick(ticker: str, investor_name: str, source: str, post_url: str):
    """Save a newly discovered ticker pick to Supabase and look up IG epic."""
    db = get_db()
    try:
        db.run(
            """insert into notable_investors
               (investor_name, ticker, action, source, disclosed_at, notes)
               values (:inv, :t, 'NEW', :src, current_date, :n)
               on conflict do nothing""",
            inv=investor_name, t=ticker, src=source,
            n=f"Discovered via RSS monitoring | {post_url}"
        )
        log.info(f"New pick saved: {ticker} from {investor_name}")
    finally:
        db.close()

    # Look up IG epic if not already cached
    try:
        db = get_db()
        rows = db.run("select epic from epic_lookup where ticker=:t", t=ticker)
        db.close()

        if not rows:
            # Need IG session to look up
            from ig_shim import session, get_db as ig_get_db
            import time
            session.ensure_authenticated()
            data    = session.get("/markets", params={"searchTerm": ticker}, version="1")
            markets = data.get("markets", [])
            if markets:
                epic = markets[0]["epic"]
                name = markets[0].get("instrumentName", "")
                db = get_db()
                db.run(
                    """insert into epic_lookup (ticker, epic, description, currency, market_type)
                       values (:t, :e, :d, 'USD', 'SHARES')
                       on conflict (ticker) do update set epic=excluded.epic, last_seen=now()""",
                    t=ticker, e=epic, d=name
                )
                db.close()
                log.info(f"Epic found: {ticker} -> {epic} ({name})")
            time.sleep(0.3)
    except Exception as e:
        log.warning(f"Epic lookup failed for {ticker}: {e}")


# =============================================================================
# Run price action on new picks and alert Slack
# =============================================================================

def alert_new_picks(new_picks: list):
    """
    Run price action on newly discovered tickers and send Slack alert.
    new_picks: list of {ticker, investor_name, source, post_content}
    """
    if not new_picks:
        return

    slack_url = os.environ.get("SLACK_SIGNALS", "")
    if not slack_url:
        return

    lines = ""
    for pick in new_picks:
        ticker   = pick["ticker"]
        investor = pick["investor_name"]

        # Quick price action check
        try:
            from price_action import analyse_price_action
            pa      = analyse_price_action(ticker)
            verdict = pa.get("verdict", "WAIT")
            score   = pa.get("pa_score", 0)
            trend   = pa.get("trend_structure", "—")
            emoji   = "🟢" if verdict == "CONFIRM_LONG" else ("🔴" if verdict == "CONFIRM_SHORT" else "⏸")
            pa_str  = f"{emoji} {verdict} ({score:+.0f}) | {trend}"
        except Exception:
            pa_str = "⚪ PA unavailable"

        lines += f"• *{ticker}* via @{investor} — {pa_str}\n"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🐦 New X Pick Detected"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*New equity mentions found via RSS monitoring:*\n{lines}"}
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn",
                          "text": f"EndToEndTrading | {datetime.now(timezone.utc).strftime('%d %b %H:%M UTC')}"}]
        }
    ]

    try:
        requests.post(slack_url, json={"blocks": blocks}, timeout=10)
        log.info(f"Slack alert sent for {len(new_picks)} new picks")
    except Exception as e:
        log.warning(f"Slack alert failed: {e}")


# =============================================================================
# Main scan — check all tracked accounts for new picks
# =============================================================================

def scan_social_feeds(max_age_hours: int = 24) -> list:
    """
    Scan all tracked X accounts for new stock picks.
    Called by the session open routines and weekend review.

    Returns list of new picks found.
    """
    all_new_picks = []

    for account in TRACKED_ACCOUNTS:
        handle    = account["handle"]
        name      = account["name"]
        source    = account["source"]

        posts = fetch_rss(handle, max_age_hours=max_age_hours)
        if not posts:
            continue

        for post in posts:
            text    = f"{post['title']} {post['content']}"
            tickers = extract_tickers(text)

            for ticker in tickers:
                if is_new_ticker(ticker, name):
                    log.info(f"NEW PICK: {ticker} from @{handle}")
                    save_new_pick(ticker, name, source, post.get("url", ""))
                    all_new_picks.append({
                        "ticker":       ticker,
                        "investor_name": name,
                        "source":       source,
                        "post_content": post["title"][:100],
                    })

    if all_new_picks:
        alert_new_picks(all_new_picks)
        log.info(f"Social scan complete: {len(all_new_picks)} new picks found")
    else:
        log.info("Social scan complete: no new picks")

    return all_new_picks


# =============================================================================
# Entry point — run scan immediately
# Usage: python social_monitor.py
# =============================================================================

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    os.environ.setdefault("SLACK_SIGNALS",
        "https://hooks.slack.com/services/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")

    print("Scanning X accounts for new picks...")
    picks = scan_social_feeds(max_age_hours=48)

    if picks:
        print(f"\n{len(picks)} new picks found:")
        for p in picks:
            print(f"  {p['ticker']} from @{p['investor_name']}: {p['post_content']}")
    else:
        print("No new picks found in last 48 hours.")
