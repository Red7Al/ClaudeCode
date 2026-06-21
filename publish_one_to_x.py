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
# 1.5.0   2026-06-21  Alex Hind   --slack-only (user 2026-06-21): post the Slack publication ONLY (card + 3yr PNG + long
#                                 report), skip X entirely. Wired into trading-x-publish.yml (slack_only input).
# 1.4.0   2026-06-21  Alex Hind   --no-register TEST mode (user 2026-06-21): posts the full Slack draft (card+tweet+long
#                                 report) AND live X, bypasses the dedup guard, and does NOT record to x_publications — for
#                                 test publications that shouldn't count as "published". Wired into trading-x-publish.yml.
# 1.3.0   2026-06-17  Alex Hind   Batch mode (user 2026-06-17): publish_tickers_to_x() posts several instruments to X spaced
#                                 by _INTER_INSTRUMENT_DELAY (60s) so threads don't overlap; --top-per-market=N publishes
#                                 today's top-N/market tradeable. Used by the morning report for the top-2/market live X.
# 1.2.0   2026-06-16  Alex Hind   Dedup (user 2026-06-16: duplicate publications): skip if the ticker was published to X
#                                 within 12h (x_publications table) unless --force; record each publication after posting.
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


_DEDUP_HOURS = 12   # don't re-publish the same instrument to X within this window (user 2026-06-16)
_PUB_TABLE_SQL = ("create table if not exists x_publications "
                  "(id bigserial primary key, ticker text not null, tweet_id text, "
                  "published_at timestamptz default now())")


def _recently_published(ticker: str) -> bool:
    """True if `ticker` was published to X within the last _DEDUP_HOURS hours (dedup, user
    2026-06-16: duplicate publications). Best-effort — on any DB error returns False so a flaky
    DB never blocks a publish."""
    try:
        from db_pool import get_db
        db = get_db()
        try:
            db.run(_PUB_TABLE_SQL)
            rows = db.run(f"select 1 from x_publications where ticker = :t "
                          f"and published_at > now() - interval '{_DEDUP_HOURS} hours' limit 1", t=ticker)
            return bool(rows)
        finally:
            db.close()
    except Exception as e:
        log.warning(f"dedup check failed for {ticker} (proceeding): {e}")
        return False


def _record_publication(ticker: str, tweet_id):
    """Record a live X publication so repeats are de-duplicated. Never raises."""
    try:
        from db_pool import get_db
        db = get_db()
        try:
            db.run(_PUB_TABLE_SQL)
            db.run("insert into x_publications (ticker, tweet_id) values (:t, :i)",
                   t=ticker, i=(str(tweet_id) if tweet_id else None))
        finally:
            db.close()
    except Exception as e:
        log.warning(f"failed to record X publication for {ticker}: {e}")


_INTER_INSTRUMENT_DELAY = 60   # seconds between instruments in a batch, so threads don't overlap (user 2026-06-17)


def publish_tickers_to_x(tickers, inter_instrument_delay: int = _INTER_INSTRUMENT_DELAY) -> int:
    """Publish each ticker's COMPLETE publication (lead + card + long 1/n thread) to live X,
    spaced by `inter_instrument_delay` so the threads do NOT overlap on the timeline (user
    2026-06-17). The caller has already chosen the set (e.g. top-2/market of the changed drafts),
    so NO 12h dedup is applied here; each is recorded + confirmed. Returns the count published."""
    import time
    from x_publish import publish_thread_to_x
    published = 0
    for i, tk in enumerate(tickers):
        try:
            pub = build_publication(tk)
        except Exception as e:
            log.warning(f"{tk}: build failed — skipped: {e}"); pub = None
        if not pub:
            log.info(f"{tk}: no tradeable funnel — skipped.")
        else:
            res, tweet, png, thread = pub
            lead_id, n = publish_thread_to_x(tweet, png, thread)
            if n >= 1:
                _record_publication(tk, lead_id)
                _confirm_to_slack(tk, res.get("name", tk), lead_id, n, len(thread))
                published += 1
                log.info(f"{tk}: published {n} tweet(s) to X (lead {lead_id}).")
            else:
                log.error(f"{tk}: nothing posted to X.")
        if inter_instrument_delay and i < len(tickers) - 1:
            log.info(f"waiting {inter_instrument_delay}s before the next instrument (avoid overlap)…")
            time.sleep(inter_instrument_delay)
    log.info(f"live X batch complete: published {published}/{len(tickers)} instrument(s).")
    return published


def _top_per_market_arg() -> int:
    """Parse --top-per-market=N (equals form, unambiguous). Returns N or 0."""
    for a in sys.argv[1:]:
        if a.startswith("--top-per-market="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                return 0
    return 0


def main():
    try:                                            # UTF-8 stdout for the emoji/bold-italic text
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass

    dry   = "--dry" in sys.argv
    slack_only  = "--slack-only" in sys.argv         # post the Slack draft ONLY, no X (user 2026-06-21)
    no_register = "--no-register" in sys.argv or slack_only  # never record a test/slack-only post
    force = "--force" in sys.argv or no_register     # a test post bypasses the dedup guard too
    top_n = _top_per_market_arg()                   # batch: today's top-N/market tradeable

    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except Exception:
        pass

    # ── Batch mode: publish today's top-N per market (from hvf_scan_log) to live X ──
    if top_n:
        from quality_report import _today_top
        tks = [tk for tk, _ in _today_top(top_n)]   # top N per market, market order
        if not tks:
            log.info("No tradeable setups today — nothing to publish.")
            return
        log.info(f"--top-per-market={top_n}: {len(tks)} instrument(s) to X: {tks}")
        if dry:
            return
        publish_tickers_to_x(tks)
        return

    tickers = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not tickers:
        log.error("usage: python publish_one_to_x.py TICKER [--dry] [--force]  |  --top-per-market=N")
        sys.exit(1)
    ticker = tickers[0]

    # Dedup: don't re-publish the same instrument within the window (user 2026-06-16) unless forced.
    if not (dry or force) and _recently_published(ticker):
        log.info(f"{ticker}: already published to X within {_DEDUP_HOURS}h — skipping "
                 f"(re-run with --force to override).")
        return

    pub = build_publication(ticker)
    if not pub:
        log.info(f"{ticker}: no tradeable HVF funnel — nothing to publish.")
        sys.exit(2)
    res, tweet, png, thread = pub

    # TEST mode (user 2026-06-21): also post the FULL Slack draft (card + tweet + long report) to
    # #claude-twitter so the test shows BOTH the Slack and X publications, without registering.
    if no_register and not dry:
        try:
            from intraday_signals import _generate_x_drafts
            _generate_x_drafts([res], post=True, changed_only=False)   # changed_only=False -> no fp registration
            log.info(f"{ticker}: TEST Slack draft posted (not registered).")
        except Exception as e:
            log.error(f"{ticker}: TEST Slack draft failed: {e}")

    # --slack-only (user 2026-06-21): Slack draft (card + 3yr PNG + long report) is done above;
    # stop here — do NOT post to X.
    if slack_only and not dry:
        log.info(f"{ticker}: --slack-only — Slack publication done, skipping X.")
        return

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
    if no_register:
        log.info(f"{ticker}: --no-register TEST — NOT recording this publication (dedup table untouched).")
    else:
        _record_publication(ticker, lead_id)        # dedup record (user 2026-06-16)
    _confirm_to_slack(ticker, res.get("name", ticker), lead_id, posted, len(thread))


if __name__ == "__main__":
    main()
