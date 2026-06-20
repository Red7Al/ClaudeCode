# ======================================================================================================================
# File:         social_monitor.py
# Author:       Alex Hind
# Created:      2026-06-01
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
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
#   @Hedgeye          — Macro/risk research firm, sector and equity calls
#   @pelositracker    — Nancy Pelosi STOCK Act disclosure aggregator
#   @KobeissiLetter   — Market commentary, macro narrative, breakout signals
#   @TheProfInvestor  — Equity picks and trade ideas
#   @theaiportfolios  — AI-sector portfolio picks
#   @BlackPantherCapital — Equity tips and trade setups
#   @pdicarlotrader   — Active trader, equity and options picks
#   @polymarketmoney  — Prediction market signals, event-driven equity tips
#
# Nitter instances (tried in order until one works):
#   nitter.net, nitter.poast.org, nitter.cz
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.6.0   2026-06-19  Alex Hind   Add 6 trusted accounts (user 2026-06-19): EchoAnalysis, VJNCapital, JPATrades,
#                                 DeepValueBagger, DefiWimar, TheStockWhale.
# 1.5.0   2026-06-16  Alex Hind   CONFIRM_LONG mention → dossier read to #arw-claude-signals (user 2026-06-16): when a
#                                 tracked X account flags a ticker and price action confirms long, post the HVF summary +
#                                 technical read (instrument_dossier helpers) to #signals. Never auto-publishes to X.
# 1.4.0   2026-06-16  Alex Hind   X-mentions alert: one blank line between the CONFIRM block and the WAIT block
#                                 (user 2026-06-16) — a visible gap separates long/short-confirm picks from waiting ones.
# 1.3.0   2026-06-15  Alex Hind   Add @TrendSpider to TRACKED_ACCOUNTS (user 2026-06-15).
# 1.2.0   2026-06-11  Alex Hind   RSS alert: sorted CONFIRM first then WAIT by score; handles always prefixed with @;
#                                 removed "New X Pick Detected" header (claude-twitter = published posts only); handle
#                                 stored in new_picks dict for correct display.
# 1.1.0   2026-06-11  Alex Hind   Add 8 new trusted accounts: GlobalMktObserver, DVSignals, Javier Blas, Peter Brandt,
#                                 ZeroHedge, WSJ Markets, BRICSinfo, WatcherGuru.
# 1.0.0   2026-06-01  Alex Hind   Initial build. RSS parsing, ticker extraction, IG epic lookup, price action scan,
#                                 Slack alert.
# ======================================================================================================================

import os
from db_pool import get_db as _pool_get_db   # resilient session-pooler connection (timeout+retry)
from dotenv import load_dotenv; load_dotenv(override=True)
import re
import time
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
    {"handle": "Asklivermore",    "name": "Asklivermore",          "source": "X/@Asklivermore"},
    {"handle": "Hedgeye",           "name": "Hedgeye",             "source": "X/@Hedgeye"},
    {"handle": "pelositracker",     "name": "PelosiTracker",       "source": "X/@pelositracker"},
    {"handle": "KobeissiLetter",    "name": "KobeissiLetter",      "source": "X/@KobeissiLetter"},
    {"handle": "TheProfInvestor",   "name": "TheProfInvestor",     "source": "X/@TheProfInvestor"},
    {"handle": "theaiportfolios",   "name": "TheAIPortfolios",     "source": "X/@theaiportfolios"},
    {"handle": "BlackPantherCapital","name": "BlackPantherCapital","source": "X/@BlackPantherCapital"},
    {"handle": "pdicarlotrader",    "name": "PDiCarloTrader",      "source": "X/@pdicarlotrader"},
    {"handle": "polymarketmoney",   "name": "PolymarketMoney",     "source": "X/@polymarketmoney"},
    {"handle": "GlobalMktObserv",   "name": "GlobalMktObserver",   "source": "X/@GlobalMktObserv"},
    {"handle": "DVSignals",         "name": "DVSignals",           "source": "X/@DVSignals"},
    {"handle": "JavierBlas",        "name": "Javier Blas",         "source": "X/@JavierBlas"},
    {"handle": "PeterLBrandt",      "name": "Peter Brandt",        "source": "X/@PeterLBrandt"},
    {"handle": "ZeroHedge",         "name": "ZeroHedge",           "source": "X/@ZeroHedge"},
    {"handle": "WSJMarkets",        "name": "WSJ Markets",         "source": "X/@WSJMarkets"},
    {"handle": "BRICSinfo",         "name": "BRICSinfo",           "source": "X/@BRICSinfo"},
    {"handle": "WatcherGuru",       "name": "WatcherGuru",         "source": "X/@WatcherGuru"},
    {"handle": "TrendSpider",       "name": "TrendSpider",         "source": "X/@TrendSpider"},
    # Added 2026-06-19 (user) — six more trusted pickers.
    {"handle": "EchoAnalysis",      "name": "Echo Analysis",       "source": "X/@EchoAnalysis"},
    {"handle": "VJNCapital",        "name": "VJN Capital",         "source": "X/@VJNCapital"},
    {"handle": "JPATrades",         "name": "JPA Trades",          "source": "X/@JPATrades"},
    {"handle": "DeepValueBagger",   "name": "Deep Value Bagger",   "source": "X/@DeepValueBagger"},
    {"handle": "DefiWimar",         "name": "Defi Wimar",          "source": "X/@DefiWimar"},
    {"handle": "TheStockWhale",     "name": "The Stock Whale",     "source": "X/@TheStockWhale"},
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


# ======================================================================================================================
# Database helper
# ======================================================================================================================

def get_db():
    """Supabase connection via the shared resilient session-pooler helper (5432, timeout+retry)."""
    from db_pool import get_db as _pool_get_db
    return _pool_get_db()


def db_run(query: str, _attempts: int = 3, **params):
    """
    Run a query on a fresh pooled connection, retrying on transient pooler errors.

    The Supabase transaction pooler (port 6543) intermittently raises
    "unnamed prepared statement does not exist" (SQLSTATE 26000) when it rotates
    the backend mid-statement — it crashed the whole Social Feed Monitor run on
    2026-06-09. This retries such transient failures (26000 / dropped connection)
    on a brand-new connection; real SQL errors are raised immediately. All call
    sites here are SELECTs or idempotent upserts, so a retry is always safe.
    Returns the rows (empty list for non-SELECT statements).
    """
    last = None
    for i in range(_attempts):
        db = get_db()
        try:
            return db.run(query, **params)
        except Exception as e:
            last = e
            msg = str(e).lower()
            transient = ("26000" in msg or "prepared statement" in msg
                         or "connection is closed" in msg or "network error" in msg)
            if transient and i < _attempts - 1:
                time.sleep(0.5 * (i + 1))
                continue
            raise
        finally:
            try:
                db.close()
            except Exception:
                pass
    raise last


# ======================================================================================================================
# Fetch RSS feed from Nitter
# ======================================================================================================================

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


# ======================================================================================================================
# Extract ticker mentions from post text
# ======================================================================================================================

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


# ======================================================================================================================
# Check if ticker is already tracked
# ======================================================================================================================

def is_new_ticker(ticker: str, investor_name: str) -> bool:
    """Return True if this ticker hasn't been seen from this investor before."""
    rows = db_run(
        "select id from notable_investors where ticker=:v_ticker and investor_name=:v_investor limit 1",
        v_ticker=ticker, v_investor=investor_name
    )
    return len(rows) == 0


# ======================================================================================================================
# Save new ticker to Supabase and look up epic
# ======================================================================================================================

def save_new_pick(ticker: str, investor_name: str, source: str, post_url: str):
    """Save a newly discovered ticker pick to Supabase and look up IG epic."""
    db_run(
        """insert into notable_investors
           (investor_name, ticker, action, source, disclosed_at, notes)
           values (:v_investor, :v_ticker, 'NEW', :v_source, current_date, :v_notes)
           on conflict do nothing""",
        v_investor=investor_name, v_ticker=ticker, v_source=source,
        v_notes=f"Discovered via RSS monitoring | {post_url}"
    )
    log.info(f"New pick saved: {ticker} from {investor_name}")

    # Look up IG epic if not already cached
    try:
        rows = db_run("select epic from epic_lookup where ticker=:t", t=ticker)

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
                db_run(
                    """insert into epic_lookup (ticker, epic, description, currency, market_type)
                       values (:v_ticker, :v_epic, :v_name, 'USD', 'SHARES')
                       on conflict (ticker) do update set epic=excluded.epic, last_seen=now()""",
                    v_ticker=ticker, v_epic=epic, v_name=name
                )
                log.info(f"Epic found: {ticker} -> {epic} ({name})")
            time.sleep(0.3)
    except Exception as e:
        log.warning(f"Epic lookup failed for {ticker}: {e}")


# ======================================================================================================================
# Run price action on new picks and alert Slack
# ======================================================================================================================

def get_company_name(ticker: str) -> str:
    """
    Look up the full company name for a ticker.
    1. Tries epic_lookup.description (IG name, stripped of suffix)
    2. Falls back to Yahoo Finance shortName
    Returns empty string if both fail.
    """
    # Try epic_lookup first
    try:
        conn = _pool_get_db()
        rows = conn.run(
            "select description from epic_lookup where ticker = :t limit 1", t=ticker
        )
        conn.close()
        if rows and rows[0][0]:
            name = rows[0][0]
            # Strip IG suffix patterns like "(24 Hours)", "(Daily)", etc.
            import re
            name = re.sub(r'\s*\(.*?(Hours|Daily|DFB|Spot).*?\)\s*$', '', name, flags=re.IGNORECASE).strip()
            if name and name != ticker:
                return name
    except Exception:
        pass

    # Fallback: Yahoo Finance
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        name = info.get("shortName") or info.get("longName", "")
        if name and name != ticker:
            return name
    except Exception:
        pass

    return ""


def _run_dossier_to_signals(ticker: str, name: str, slack_url: str):
    """A tracked X account flagged this ticker CONFIRM_LONG → run the system's dossier read and
    post it to #arw-claude-signals so the operator gets the full picture (user 2026-06-16). Posts
    the HVF summary + the technical read (reusing the dossier's own helpers); never auto-publishes
    to X. Never raises — a failure must not break the mentions alert."""
    try:
        from price_action import get_hvf_signal_mtf, get_trend_structure
        from instrument_dossier import _hvf_summary, _technical_block
        r = get_hvf_signal_mtf(ticker, trend_hint=get_trend_structure(ticker))
        r["ticker"] = ticker
        summary = _hvf_summary(ticker, name, r)
        blocks = [{"type": "section", "text": {"type": "mrkdwn",
                   "text": f"🎯 *CONFIRM_LONG flagged by a tracked X account — dossier for "
                           f"{ticker} ({name})*\n```{summary[:2900]}```"}}]
        try:
            tech = _technical_block(ticker)
            if tech:
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"```{tech[:2900]}```"}})
        except Exception:
            pass
        requests.post(slack_url, json={"blocks": blocks}, timeout=15)
        log.info(f"dossier read posted to #arw-claude-signals for {ticker} (CONFIRM_LONG)")
    except Exception as e:
        log.warning(f"dossier-to-signals failed for {ticker}: {e}")


def alert_new_picks(new_picks: list):
    """
    Run price action on newly discovered tickers and send Slack alert.
    new_picks: list of {ticker, investor_name, source, post_content}
    A CONFIRM_LONG mention also triggers a dossier read to #arw-claude-signals (user 2026-06-16).
    """
    if not new_picks:
        return

    # RSS mention alerts go to #signals. SLACK_TWITTER is reserved for content
    # published TO Twitter/X — do not use it for monitoring notifications.
    slack_url = os.environ.get("SLACK_SIGNALS", "")
    if not slack_url:
        return

    # Build pick list with PA verdict for sorting
    enriched = []
    confirm_longs = []   # CONFIRM_LONG mentions → run the dossier into #signals (user 2026-06-16)
    for pick in new_picks:
        ticker   = pick["ticker"]
        # Use stored handle; fallback to investor_name. Always ensure @ prefix.
        raw_handle = pick.get("handle") or pick.get("investor_name", "")
        handle   = raw_handle if raw_handle.startswith("@") else f"@{raw_handle}"
        company  = get_company_name(ticker)
        label    = f"{ticker} ({company})" if company else ticker

        try:
            from price_action import analyse_price_action
            pa      = analyse_price_action(ticker)
            verdict = pa.get("verdict", "WAIT")
            score   = pa.get("pa_score", 0)
            trend   = pa.get("trend_structure", "—")
        except Exception:
            verdict, score, trend = "WAIT", 0, "—"

        emoji  = "🟢" if verdict == "CONFIRM_LONG" else ("🔴" if verdict == "CONFIRM_SHORT" else "⏸")
        pa_str = f"{emoji} {verdict} ({score:+.0f}) | {trend}"
        if verdict == "CONFIRM_LONG":
            confirm_longs.append((ticker, company or ticker))

        # Sort weight: CONFIRM first, then by score descending
        sort_key = (0 if "CONFIRM" in verdict else 1, -score)
        enriched.append((sort_key, label, handle, pa_str))

    enriched.sort(key=lambda x: x[0])
    # One blank line between the CONFIRM block and the WAIT block (user 2026-06-16).
    # sort_key[0]: 0 = CONFIRM_LONG/SHORT, 1 = WAIT — insert the gap at that boundary.
    lines = ""
    prev_group = None
    for sort_key, label, handle, pa_str in enriched:
        group = sort_key[0]
        if prev_group is not None and group != prev_group:
            lines += "\n"
        lines += f"• *{label}* via {handle} — {pa_str}\n"
        prev_group = group

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🐦 New X mentions ({datetime.now(timezone.utc).strftime('%d %b %H:%M UTC')}):*\n{lines}"}
        },
    ]

    try:
        requests.post(slack_url, json={"blocks": blocks}, timeout=10)
        log.info(f"Slack alert sent for {len(new_picks)} new picks")
    except Exception as e:
        log.warning(f"Slack alert failed: {e}")

    # A CONFIRM_LONG from a tracked account is a strong cue — run the dossier read into
    # #arw-claude-signals for each (user 2026-06-16).
    for _tk, _nm in confirm_longs:
        _run_dossier_to_signals(_tk, _nm, slack_url)


# ======================================================================================================================
# Main scan — check all tracked accounts for new picks
# ======================================================================================================================

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
                        "handle":       handle,
                        "source":       source,
                        "post_content": post["title"][:100],
                    })

    if all_new_picks:
        alert_new_picks(all_new_picks)
        log.info(f"Social scan complete: {len(all_new_picks)} new picks found")
    else:
        log.info("Social scan complete: no new picks")

    return all_new_picks


# ======================================================================================================================
# Entry point — run scan immediately
# Usage: python social_monitor.py
# ======================================================================================================================

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    # SLACK_SIGNALS must be set as an environment variable — never hardcode webhook URLs

    print("Scanning X accounts for new picks...")
    picks = scan_social_feeds(max_age_hours=48)

    if picks:
        print(f"\n{len(picks)} new picks found:")
        for p in picks:
            print(f"  {p['ticker']} from @{p['investor_name']}: {p['post_content']}")
    else:
        print("No new picks found in last 48 hours.")
