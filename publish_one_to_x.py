# ======================================================================================================================
# File:         publish_one_to_x.py
# Author:       Alex Hind
# Created:      2026-06-16
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Publish ONE instrument's COMPLETE publication LIVE to X (Twitter): the lead tweet (short summary text + post-card PNG)
# followed by the long report as a numbered 1/n REPLY THREAD (user 2026-06-16: all three artifacts together on X, never
# short+card alone). Builds the EXACT same artifacts the dossier / daily X drafts produce (no rebuild):
#   - short tweet + card  : intraday_signals._generate_x_drafts(collect)
#   - long 1/n thread     : quality_report.publish_long_report_for(res, post=False)
#   - posted as a thread  : x_publish.publish_thread_to_x  (lead, then replies via in_reply_to)
# After posting, a confirmation (with the tweet link) is sent to #arw-claude-twitter (SLACK_TWITTER).
#
# Runs in GitHub Actions where the X_* secrets live (memory: secrets_and_x_delivery — never post from the local machine;
# a local run with no X_* keys is a safe no-op that prints the publication). Posts NOTHING if the ticker has no
# tradeable HVF funnel.
#
# Usage:   python publish_one_to_x.py NVDA          # build + LIVE-post the full thread to X
#          python publish_one_to_x.py NVDA --dry    # build only, print, post NOTHING
#
# Env (GitHub Secrets): X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET, SLACK_TWITTER (confirmation),
#                       SUPABASE_USER, SUPABASE_DB_PASSWORD (signal_log enrichment; optional)
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.1.0   2026-06-16  Alex Hind   Publish the COMPLETE publication to X (user 2026-06-16): lead tweet + card THEN the
#                                 long 1/n report as a reply thread (publish_thread_to_x). Posts a confirmation with the
#                                 tweet link to #arw-claude-twitter after publishing.
# 1.0.0   2026-06-16  Alex Hind   Initial build: live-publish one instrument (tweet + card) to X via publish_to_x,
#                                 reusing the production _generate_x_drafts(collect) path. --dry for a no-post preview.
# ======================================================================================================================

import io
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("publish_one_to_x")

X_HANDLE = "SqueezeSignals"   # the live X account these post to (for the confirmation link)


def build_publication(ticker: str):
    """Build the full publication for one ticker via the production paths. Returns
    (res, short_tweet, card_png, long_thread_parts) or None if there is no tradeable funnel."""
    from price_action import get_hvf_signal_mtf, get_trend_structure
    from intraday_signals import _generate_x_drafts, _resolve_name
    from quality_report import publish_long_report_for
    res = get_hvf_signal_mtf(ticker, trend_hint=get_trend_structure(ticker))
    if not res.get("hvf_type"):
        return None
    res["ticker"] = ticker
    res.setdefault("name", _resolve_name(ticker))
    drafts = _generate_x_drafts([res], post=False, collect=True) or []   # short tweet + card (no Slack post)
    if not drafts:
        return None
    d = drafts[0]
    long_thread = publish_long_report_for(res, post=False)               # the long 1/n parts (no post)
    return res, d.get("tweet"), d.get("png"), long_thread


def _confirm_to_slack(ticker: str, name: str, lead_id, posted: int, n_parts: int):
    """Confirm a live X publication to #arw-claude-twitter (user 2026-06-16)."""
    import requests
    url = os.environ.get("SLACK_TWITTER", "")
    if not url:
        log.warning("SLACK_TWITTER not set — X publication confirmation not sent.")
        return
    disp = ticker[:-2] if ticker.endswith(".L") else ticker
    link = f"https://x.com/{X_HANDLE}/status/{lead_id}" if lead_id else "(tweet id unavailable)"
    text = (f"✅ *Published to X* — ${disp} ({name})\n"
            f"Lead tweet + card + {n_parts}-part thread  ·  {posted} tweet(s) total\n{link}")
    try:
        requests.post(url, json={"blocks": [{"type": "section",
                      "text": {"type": "mrkdwn", "text": text}}]}, timeout=10)
        log.info(f"X publication confirmation sent to #arw-claude-twitter for {ticker}")
    except Exception as e:
        log.error(f"X publication confirmation failed for {ticker}: {e}")


def main():
    try:                                            # UTF-8 stdout for the emoji/bold-italic text
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

    pub = build_publication(ticker)
    if not pub:
        log.info(f"{ticker}: no tradeable HVF funnel — nothing to publish.")
        sys.exit(2)
    res, tweet, png, thread = pub

    log.info(f"{ticker}: lead tweet {len(tweet)} chars, card {'present' if png else 'MISSING'}, "
             f"{len(thread)}-part thread")
    print("----- LEAD TWEET (+card) -----")
    print(tweet)
    for i, part in enumerate(thread, 1):
        print(f"----- THREAD {i}/{len(thread)} -----")
        print(part)
    print("------------------------------")

    if dry:
        log.info("--dry: nothing posted to X.")
        return

    from x_publish import publish_thread_to_x
    lead_id, posted = publish_thread_to_x(tweet, png, thread)   # lead + card, then replies
    log.info(f"{ticker}: published {posted} tweet(s) to X (lead {lead_id}).")
    if posted < 1:
        log.error("nothing was posted (X keys missing or API error) — see log above.")
        sys.exit(3)
    _confirm_to_slack(ticker, res.get("name", ticker), lead_id, posted, len(thread))


if __name__ == "__main__":
    main()
