# ======================================================================================================================
# File:         publish_one_to_x.py
# Author:       Alex Hind
# Created:      2026-06-16
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Publish ONE instrument's X post (short tweet text + post-card PNG) LIVE to X (Twitter), via the production
# scan -> _generate_x_drafts(collect) path and x_publish.publish_to_x (user 2026-06-16: "try publishing an instrument
# summary (text and PNG) to X"). Builds the EXACT same tweet + card the dossier / daily X drafts produce — no rebuild.
#
# Runs in GitHub Actions where the X_* secrets live (memory: secrets_and_x_delivery — never post from the local machine;
# the local .env has no X_* keys, so a local run is a safe no-op that prints the tweet). Posts NOTHING if the ticker
# has no tradeable HVF funnel.
#
# Usage:   python publish_one_to_x.py NVDA          # build + LIVE-post one instrument to X
#          python publish_one_to_x.py NVDA --dry    # build only, print the tweet, post NOTHING
#
# Env (GitHub Secrets): X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET,
#                       SUPABASE_USER, SUPABASE_DB_PASSWORD (signal_log enrichment; optional)
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-16  Alex Hind   Initial build: live-publish one instrument (tweet + card) to X via publish_to_x,
#                                 reusing the production _generate_x_drafts(collect) path. --dry for a no-post preview.
# ======================================================================================================================

import io
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("publish_one_to_x")


def build_draft(ticker: str):
    """Build the production (tweet, png) for one ticker via the same path the dossier uses.
    Returns the collected draft dict (keys: tweet, png, …) or None if there is no tradeable funnel."""
    from price_action import get_hvf_signal_mtf, get_trend_structure
    from intraday_signals import _generate_x_drafts, _resolve_name
    res = get_hvf_signal_mtf(ticker, trend_hint=get_trend_structure(ticker))
    if not res.get("hvf_type"):
        return None
    res["ticker"] = ticker
    res.setdefault("name", _resolve_name(ticker))
    drafts = _generate_x_drafts([res], post=False, collect=True) or []   # build only; never posts to Slack
    return drafts[0] if drafts else None


def main():
    try:                                            # UTF-8 stdout for the emoji/bold-italic tweet
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass

    dry = "--dry" in sys.argv
    tickers = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not tickers:
        log.error("usage: python publish_one_to_x.py TICKER [--dry]")
        sys.exit(1)
    ticker = tickers[0]

    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except Exception:
        pass

    draft = build_draft(ticker)
    if not draft:
        log.info(f"{ticker}: no tradeable HVF funnel — nothing to publish.")
        sys.exit(2)

    tweet, png = draft["tweet"], draft.get("png")
    log.info(f"{ticker}: tweet {len(tweet)} chars, card {'present' if png else 'MISSING'}")
    print("----- TWEET -----")
    print(tweet)
    print("-----------------")

    if dry:
        log.info("--dry: nothing posted to X.")
        return

    from x_publish import publish_to_x
    n = publish_to_x([(tweet, png)], stagger=False)   # single post → no stagger needed
    log.info(f"{ticker}: published {n} post(s) to X.")
    if n < 1:
        log.error("nothing was posted (X keys missing or API error) — see log above.")
        sys.exit(3)


if __name__ == "__main__":
    main()
