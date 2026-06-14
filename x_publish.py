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


def _post_one(api, client, text: str, png: bytes = None):
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
    client.create_tweet(text=text, media_ids=media_ids)       # v2 create tweet


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
