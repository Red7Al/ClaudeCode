# ======================================================================================================================
# File:         x_publish.py
# Author:       Alex Hind
# Created:      2026-06-14
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Publish tweets (text + optional image) to X (Twitter) via the OFFICIAL X API v2 using `tweepy`
# (OAuth 1.0a user context). Robust — unlike the unofficial twikit, this does not break when X
# changes its website. A random 13–17 min gap is left between tweets (user 2026-06-13).
#
#   Cost (as of 2026): a pre-Feb-2026 developer account keeps the FREE tier (incl. media upload).
#   New accounts are pay-per-use (~$0.015 per post created). Either way this code is the same.
#
# One-time setup (YOU do this — I never see the keys):
#   1. developer.x.com → create a Project + App. Set the App's User authentication to OAuth 1.0a,
#      permissions = Read and Write.
#   2. Generate: API Key + API Key Secret (consumer), and an Access Token + Access Token Secret
#      (for @EndToEndTrading, with Read+Write).
#   3. Add them as GitHub repo secrets:
#        X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
#
# Usage:
#   python x_publish.py --verify        # confirm the keys authenticate (reads the handle; posts NOTHING)
#   from x_publish import publish_to_x; publish_to_x([(text, png_bytes), ...])   # post, staggered
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-14  Alex Hind   Initial build — cookie-based twikit publisher.
# 2.2.0   2026-06-16  Alex Hind   publish_thread_to_x (user 2026-06-16): LEAD (short+card) posts first with a short delay
#                                 before the long report (A); the long report's 1/n is the MAIN page (reply to the lead)
#                                 and 2/n..n/n are COMMENTS on the 1/n page (each replies to 1/n, not chained) (B).
# 2.1.0   2026-06-16  Alex Hind   publish_thread_to_x(): post a COMPLETE publication as one X thread — lead tweet (short
#                                 text + card) then the long 1/n report as chained replies (in_reply_to). _post_one now
#                                 returns the tweet id and accepts in_reply_to. (user 2026-06-16: all three on X too.)
# 2.0.0   2026-06-14  Alex Hind   SWITCHED to the official X API v2 via tweepy (user 2026-06-14): twikit's login is broken
#                                 (X's Mar-2026 "client transaction" change, no fixed release; 2.3.3 latest still fails).
#                                 OAuth 1.0a keys (4 GitHub secrets), media upload + create_tweet, 13-17 min stagger,
#                                 --verify auth check. Robust vs X site changes; free (grandfathered) or pay-per-use.
# ======================================================================================================================

import os
import sys
import io
import time
import random
import logging
import tempfile

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("x_publish")

STAGGER_MIN, STAGGER_MAX = 13 * 60, 17 * 60   # seconds between tweets (user 2026-06-13)
_KEYS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")


def _clients():
    """Build (API v1.1, Client v2) from the four OAuth 1.0a secrets. Returns (None, None)
    if any key is missing, so a run without credentials is a safe no-op."""
    k = {n: os.environ.get(n) for n in _KEYS}
    missing = [n for n in _KEYS if not k[n]]
    if missing:
        log.warning(f"X API keys missing {missing} — publishing is a no-op.")
        return None, None
    import tweepy
    api = tweepy.API(tweepy.OAuth1UserHandler(
        k["X_API_KEY"], k["X_API_SECRET"], k["X_ACCESS_TOKEN"], k["X_ACCESS_SECRET"]))
    client = tweepy.Client(consumer_key=k["X_API_KEY"], consumer_secret=k["X_API_SECRET"],
                           access_token=k["X_ACCESS_TOKEN"], access_token_secret=k["X_ACCESS_SECRET"])
    return api, client


def _post_one(api, client, text: str, png: bytes = None, in_reply_to: str = None):
    """Post one tweet (optional image, optional reply target). Returns the new tweet id."""
    media_ids = None
    if png:
        fd, path = tempfile.mkstemp(suffix=".png")
        try:
            os.write(fd, png)
            os.close(fd)
            media = api.media_upload(filename=path)          # v1.1 media upload
            media_ids = [media.media_id_string]
        finally:
            try:
                os.remove(path)
            except Exception:
                pass
    kwargs = {"text": text, "media_ids": media_ids}
    if in_reply_to:
        kwargs["in_reply_to_tweet_id"] = in_reply_to
    resp = client.create_tweet(**kwargs)                      # v2 create tweet
    try:
        return resp.data["id"]
    except Exception:
        return None


def publish_to_x(items, stagger: bool = True) -> int:
    """Post a batch to X. `items` = list of (tweet_text, png_bytes | None). Posts via the
    official API with a random 13–17 min gap between tweets. Returns count posted.
    No-op (returns 0) if the X_* keys are not set."""
    api, client = _clients()
    if client is None:
        return 0
    posted = 0
    for i, (text, png) in enumerate(items):
        try:
            _post_one(api, client, text, png)
            posted += 1
            log.info(f"posted tweet {i + 1}/{len(items)} to X")
        except Exception as e:
            log.error(f"X post {i + 1} failed: {e}")
        if stagger and i < len(items) - 1:
            wait = random.randint(STAGGER_MIN, STAGGER_MAX)
            log.info(f"staggering {wait // 60} min before next tweet…")
            time.sleep(wait)
    log.info(f"X publish complete: {posted}/{len(items)} posted")
    return posted


def publish_thread_to_x(lead_text: str, lead_png: bytes, parts, lead_delay: int = 6,
                        stagger: bool = False) -> tuple:
    """Post a COMPLETE publication to X (user 2026-06-16):
      1. LEAD = short tweet + card. It MUST publish before the long report (user A); if it
         fails, the long report is NOT posted.
      2. a short `lead_delay` pause so the short + card is fully live first (user A).
      3. the long report's 1/n part = the MAIN page, posted as a reply to the lead.
      4. parts 2/n..n/n = COMMENTS on the 1/n main page — each replies to the 1/n tweet
         (NOT chained to each other), so they hang off the main page (user B).
    Returns (lead_tweet_id, posted_count). No-op -> (None, 0) when the X_* keys are missing."""
    api, client = _clients()
    if client is None:
        return None, 0
    # 1) LEAD — short text + card. Must succeed before anything else goes out.
    try:
        lead_id = _post_one(api, client, lead_text, lead_png)
    except Exception as e:
        log.error(f"X lead (short+card) post failed — long report NOT posted: {e}")
        return None, 0
    log.info(f"posted X lead tweet {lead_id} (+card)")
    posted = 1
    parts = parts or []
    if not parts:
        return lead_id, posted
    # 2) brief pause so the short + card is fully published before the long report (user A)
    if lead_delay:
        time.sleep(lead_delay)
    # 3) 1/n = MAIN page of the long report, replying to the lead
    try:
        main_id = _post_one(api, client, parts[0], None, in_reply_to=lead_id)
        posted += 1
        log.info(f"posted X long-report MAIN page 1/{len(parts)} ({main_id})")
    except Exception as e:
        log.error(f"X long-report main page (1/{len(parts)}) failed: {e}")
        return lead_id, posted
    # 4) 2/n..n/n = COMMENTS on the 1/n main page — each replies to the main page (user B)
    for i, part in enumerate(parts[1:], start=2):
        try:
            _post_one(api, client, part, None, in_reply_to=main_id)
            posted += 1
            log.info(f"posted X comment {i}/{len(parts)} on the 1/{len(parts)} main page")
        except Exception as e:
            log.error(f"X comment {i}/{len(parts)} failed: {e}")
            continue
        if stagger and i < len(parts):
            time.sleep(random.randint(STAGGER_MIN, STAGGER_MAX))
    log.info(f"X publication complete: {posted} tweet(s) (lead + 1/{len(parts)} main + {posted - 2} comment(s))")
    return lead_id, posted


def verify() -> bool:
    """Confirm the keys authenticate (reads the logged-in handle). Posts nothing."""
    api, client = _clients()
    if client is None:
        return False
    try:
        me = client.get_me()
        log.info(f"X API OK — authenticated as @{me.data.username}")
        return True
    except Exception as e:
        log.error(f"X API auth check failed: {e}")
        return False


def main():
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass
    if "--verify" in sys.argv:
        sys.exit(0 if verify() else 1)
    log.info("x_publish is a library. Run with --verify to test auth, or import publish_to_x().")


if __name__ == "__main__":
    main()
