# ======================================================================================================================
# File:         x_publish.py
# Author:       Alex Hind
# Created:      2026-06-14
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Publish tweets (text + optional image) to X (Twitter) for free via the unofficial `twikit` web client — NO paid API.
# Posting is COOKIE-BASED: the publisher never logs in with a password and never solves a CAPTCHA. You generate a
# twikit session ONCE yourself (locally) and store the cookies as the GitHub secret TWIKIT_COOKIES; this module loads
# them and posts. A random 13–17 min gap is left between tweets (user 2026-06-13).
#
#   ⚠️ twikit drives X's UNOFFICIAL web API. Automated posting can get the account rate-limited, flagged or BANNED
#      under X's Terms of Service. Use at your own risk on @EndToEndTrading.
#
# One-time cookie generation (YOU run this locally — it needs your X password, which the bot must never handle):
#     python -c "import asyncio; from twikit import Client; \
#       c=Client('en-US'); \
#       asyncio.run(c.login(auth_info_1='<x_username>', auth_info_2='<x_email>', password='<password>')); \
#       c.save_cookies('cookies.json'); print('saved')"
#   Then put the CONTENTS of cookies.json into the GitHub secret TWIKIT_COOKIES.
#
# Usage:
#   python x_publish.py --verify         # confirm the cookies authenticate (reads the logged-in handle; posts NOTHING)
#   from x_publish import publish_to_x; publish_to_x([(text, png_bytes), ...])   # actually post, staggered
#
# Env (GitHub Secrets): TWIKIT_COOKIES  (JSON contents of a twikit cookies.json)
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-14  Alex Hind   Initial build (user 2026-06-14): cookie-based twikit publisher with 13-17 min stagger
#                                 and a --verify auth check. No password handling, no CAPTCHA solving — cookies only.
# ======================================================================================================================

import os
import sys
import io
import time
import random
import asyncio
import logging
import tempfile

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("x_publish")

STAGGER_MIN, STAGGER_MAX = 13 * 60, 17 * 60   # seconds between tweets (user 2026-06-13)


def _stagger_seconds() -> int:
    return random.randint(STAGGER_MIN, STAGGER_MAX)


def _load_client():
    """Build a twikit Client authenticated from the TWIKIT_COOKIES secret (no password).
    Returns None when cookies are absent (so a run without auth is a safe no-op)."""
    cookies = os.environ.get("TWIKIT_COOKIES")
    if not cookies:
        log.warning("TWIKIT_COOKIES not set — X publishing is a no-op (cookies required).")
        return None
    from twikit import Client
    client = Client("en-US")
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        os.write(fd, cookies.encode("utf-8"))
        os.close(fd)
        client.load_cookies(path)
    finally:
        try:
            os.remove(path)
        except Exception:
            pass
    return client


async def _post_one(client, text: str, png: bytes = None):
    media_ids = []
    if png:
        fd, path = tempfile.mkstemp(suffix=".png")
        try:
            os.write(fd, png)
            os.close(fd)
            media_ids = [await client.upload_media(path)]
        finally:
            try:
                os.remove(path)
            except Exception:
                pass
    await client.create_tweet(text=text, media_ids=media_ids or None)


async def _publish(items, stagger: bool):
    client = _load_client()
    if client is None:
        return 0
    posted = 0
    for i, (text, png) in enumerate(items):
        try:
            await _post_one(client, text, png)
            posted += 1
            log.info(f"posted tweet {i + 1}/{len(items)} to X")
        except Exception as e:
            log.error(f"X post {i + 1} failed: {e}")
        if stagger and i < len(items) - 1:
            wait = _stagger_seconds()
            log.info(f"staggering {wait // 60} min before next tweet…")
            await asyncio.sleep(wait)
    log.info(f"X publish complete: {posted}/{len(items)} posted")
    return posted


def publish_to_x(items, stagger: bool = True) -> int:
    """Post a batch to X. `items` = list of (tweet_text, png_bytes | None). Posts via the
    saved twikit cookies, with a random 13–17 min gap between tweets. Returns count posted.
    No-op (returns 0) if TWIKIT_COOKIES is not set."""
    return asyncio.run(_publish(items, stagger))


async def _verify():
    client = _load_client()
    if client is None:
        return False
    try:
        me = await client.user()
        log.info(f"twikit cookies OK — authenticated as @{getattr(me, 'screen_name', '?')}")
        return True
    except Exception as e:
        log.error(f"twikit auth check failed: {e}")
        return False


def main():
    try:                                            # UTF-8 stdout for the script (not on import)
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass
    if "--verify" in sys.argv:
        ok = asyncio.run(_verify())
        sys.exit(0 if ok else 1)
    log.info("x_publish is a library. Run with --verify to test auth, or import publish_to_x().")


if __name__ == "__main__":
    main()
