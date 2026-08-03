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
# 1.14.0  2026-07-06  Alex Hind   (user 2026-07-06) Morning HVF batch restricted to Config -> X Posts markets
#                                 (get_x_hvf_markets; default FTSE 100 / FTSE 250 / NASDAQ 100 / S&P 500).
# 1.13.0  2026-06-29  Alex Hind   (user 2026-06-29 "limit to 5 per day") publish_tickers_to_x now enforces a daily cap
#                                 (config.X_MAX_PER_DAY=5): counts today's x_publications and stops once the budget is hit.
# 1.12.0  2026-06-24  Alex Hind   (user 2026-06-24) NEW --list-recent[=N] mode: prints the most recent X publications
#                                 (ticker, lead tweet id, time, thread size, x.com URL) from x_publications so the correct
#                                 lead ids can be picked for --delete-leads WITHOUT hand-copying X URLs. Closes the
#                                 "I don't have the tweet ids" friction: list -> delete -> republish are all now driveable
#                                 from the trading-x-publish workflow (the local machine has no X_*/SUPABASE_* creds, so
#                                 listing must run on Actions). Wired into trading-x-publish.yml via the list_recent input.
# 1.11.0  2026-06-23  Alex Hind   (user 2026-06-23) The published-to-X summary now shows each instrument's MARKET (resolved
#                                 from the report UNIVERSE via _market_of).
# 1.10.0  2026-06-22  Alex Hind   (user 2026-06-22) After a batch's tweets are all posted, publish_tickers_to_x posts ONE
#                                 consolidated summary to #arw-claude-twitter — each instrument with HVF status (direction ·
#                                 signal · quality · R:R) + lead-tweet link (_summary_to_slack).
# 1.9.0   2026-06-22  Alex Hind   (user 2026-06-22) Store EVERY thread tweet id at publish (x_publications.thread_ids) so a
#                                 later --delete-leads removes the WHOLE thread precisely (no enumeration; X free tier 403s the
#                                 reply read). delete path now passes the stored ids to delete_thread.
# 1.8.0   2026-06-22  Alex Hind   (user 2026-06-22) build_publication attaches the 3-year history PNG to the X lead alongside
#                                 the card (lead carries [card, 3yr] — X allows up to 4 images).
# 1.7.0   2026-06-22  Alex Hind   (user 2026-06-22) --delete-leads=ID1,ID2,... mode: remove incorrect published threads via
#                                 x_publish.delete_thread (lead + conversation). Irreversible; x_publications rows kept.
# 1.6.0   2026-06-22  Alex Hind   (user 2026-06-22) build_publication now applies the SAME publish gate as the daily report:
#                                 only READY/TRIGGERED + quality>=MIN_PUBLISH_QUALITY + entry within MAX_DEVELOPING_DISTANCE_PCT
#                                 are published. Without it, force-publishing BTRW.L pushed a DEVELOPING weekly setup with the
#                                 entry 36% from price (price already outside the funnel) to X.
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

    # Publish gate (user 2026-06-22) — the single-ticker path must apply the SAME bar as the daily
    # report's categorise, or a non-tradeable setup gets published. Without it, force-publishing
    # BTRW.L pushed its WEEKLY result (DEVELOPING, entry 355.9 vs price 261 — price already BELOW
    # the entry, i.e. outside the funnel) to X. Require: signal READY/TRIGGERED (not DEVELOPING),
    # quality >= MIN_PUBLISH_QUALITY, and entry within MAX_DEVELOPING_DISTANCE_PCT of the live price.
    from config import MAX_DEVELOPING_DISTANCE_PCT, MIN_PUBLISH_QUALITY
    _sig   = res.get("hvf_signal")
    _q     = res.get("pattern_quality") or 0
    _cur   = res.get("current_price")
    _entry = res.get("h3_level")
    _entry_far = (isinstance(_cur, (int, float)) and _cur and isinstance(_entry, (int, float))
                  and abs(_entry / _cur - 1) * 100 > MAX_DEVELOPING_DISTANCE_PCT)
    if _sig not in ("READY", "TRIGGERED") or _q < MIN_PUBLISH_QUALITY or _entry_far:
        _dist = (f"{abs(_entry / _cur - 1) * 100:.0f}%" if _entry_far else "ok")
        log.info(f"{ticker}: not publishable — signal={_sig} quality={_q} entry_dist={_dist} "
                 f"(need READY/TRIGGERED, quality>={MIN_PUBLISH_QUALITY}, entry<={MAX_DEVELOPING_DISTANCE_PCT}%). Skipped.")
        return None
    drafts = _generate_x_drafts([res], post=False, collect=True) or []   # short tweet + card (no Slack post)
    if not drafts:
        return None
    d = drafts[0]
    long_thread = publish_long_report_for(res, post=False)               # the long 1/n parts (no post)
    # Lead images: the card PLUS the standalone 3-year history PNG (user 2026-06-22: "add the 3yr
    # PNG visual back into the X tweet"). Both attach to the lead (X allows up to 4 images).
    lead_media = [d.get("png")]
    try:
        from intraday_signals import render_3yr_history_card
        _png3 = render_3yr_history_card(res)
        if _png3:
            lead_media.append(_png3)
    except Exception as e:
        log.warning(f"{ticker}: 3yr history PNG render failed (lead will carry the card only): {e}")
    lead_media = [m for m in lead_media if m]
    return res, d.get("tweet"), lead_media, long_thread


def _confirm_to_slack(ticker: str, name: str, lead_id, posted: int, n_parts: int):
    """Confirm a live X publication to #arw-claude-twitter (user 2026-06-16)."""
    import requests
    from notify import slack_enabled
    if not slack_enabled("twitter"):   # per-channel switch (user 2026-08-03)
        return
    url = os.environ.get("SLACK_TWITTER", "")
    if not url:
        log.warning("SLACK_TWITTER not set — X publication confirmation not sent.")
        return
    disp = ticker[:-2] if ticker.endswith(".L") else ticker
    link = f"https://x.com/{X_HANDLE}/status/{lead_id}" if lead_id else "(tweet id unavailable)"
    # Just the tweet link, not the HVF details (user 2026-06-29).
    text = f"✅ *${disp}* — {link}"
    try:
        requests.post(url, json={"blocks": [{"type": "section",
                      "text": {"type": "mrkdwn", "text": text}}]}, timeout=10)
        log.info(f"X publication confirmation sent to #arw-claude-twitter for {ticker}")
    except Exception as e:
        log.error(f"X publication confirmation failed for {ticker}: {e}")


def _market_of(ticker: str) -> str:
    """Short market label (FTSE100 / S&P500 / Commodities / Indices & FX / Crypto) for a ticker,
    resolved from the report UNIVERSE. Function-level import avoids a circular import (run_hvf_report
    imports this module). Returns '' if the ticker isn't in any basket."""
    try:
        from run_hvf_report import UNIVERSE
        from price_action import market_short
        for mkt, tickers in UNIVERSE.items():
            if ticker in tickers:
                return market_short(mkt)
    except Exception:
        pass
    return ""


def _summary_to_slack(rows: list):
    """Post ONE consolidated 'published to X' summary to #arw-claude-twitter once a batch's tweets
    are all out (user 2026-06-22): each instrument with its HVF status — direction · signal ·
    quality · R:R — and a link to its lead tweet. Best-effort; never raises."""
    import requests
    from datetime import datetime, timezone
    from notify import slack_enabled
    if not slack_enabled("twitter"):   # per-channel switch (user 2026-08-03)
        return
    url = os.environ.get("SLACK_TWITTER", "")
    if not url or not rows:
        return
    lines = []
    for r in rows:
        tk   = r.get("ticker", "")
        disp = tk[:-2] if tk.endswith(".L") else tk
        link = f"https://x.com/{X_HANDLE}/status/{r['lead_id']}" if r.get("lead_id") else ""
        name = f" ({r['name']})" if r.get("name") and r.get("name") != tk else ""
        # Just the linked ticker (+ name) — not the HVF details (user 2026-06-29).
        cash = f"<{link}|${disp}>" if link else f"${disp}"
        lines.append(f"• {cash}{name}")
    header = (f"*📋 Published to X — {len(rows)} instrument(s) · "
              f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}*")
    try:
        requests.post(url, json={"blocks": [{"type": "section",
                      "text": {"type": "mrkdwn", "text": header + "\n" + "\n".join(lines)}}]}, timeout=10)
        log.info(f"published-to-X summary posted to #arw-claude-twitter ({len(rows)} instruments)")
    except Exception as e:
        log.error(f"published-to-X summary post failed: {e}")


_DEDUP_HOURS = 48   # don't re-publish the same instrument to X within this window (user 2026-06-16;
                    # raised 12 -> 48 on 2026-07-10: SBUX & co. were re-published too often. The hot-stock
                    # path already excludes names tweeted in the last 72h — this narrows the HVF gap.)
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


def _published_today_count() -> int:
    """Number of X publications recorded so far today (UTC) — for the daily cap (user 2026-06-29:
    'limit to 5 per day'). Best-effort: on a DB error returns 0 so a flaky DB never blocks publishing."""
    try:
        from db_pool import get_db
        db = get_db()
        try:
            db.run(_PUB_TABLE_SQL)
            rows = db.run("select count(*) from x_publications where published_at >= date_trunc('day', now())")
            return int(rows[0][0]) if rows else 0
        finally:
            db.close()
    except Exception as e:
        log.warning(f"daily-count check failed (proceeding): {e}")
        return 0


def _record_publication(ticker: str, tweet_id, thread_ids=None):
    """Record a live X publication so repeats are de-duplicated, and STORE every tweet id of the
    thread (user 2026-06-22) so a later delete is precise + enumeration-free (X free tier 403s the
    reply-lookup). Never raises."""
    try:
        from db_pool import get_db
        db = get_db()
        try:
            db.run(_PUB_TABLE_SQL)
            db.run("alter table x_publications add column if not exists thread_ids text")
            _tids = ",".join(str(i) for i in (thread_ids or []) if i) or None
            db.run("insert into x_publications (ticker, tweet_id, thread_ids) values (:t, :i, :th)",
                   t=ticker, i=(str(tweet_id) if tweet_id else None), th=_tids)
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
    from config import X_MAX_PER_DAY as _X_DEFAULT
    try:   # DB-first (Config → Engine), config.py fallback (user 2026-07-03)
        from config_store import cfg_num
        X_MAX_PER_DAY = int(cfg_num("x_max_per_day", _X_DEFAULT))
    except Exception:
        X_MAX_PER_DAY = _X_DEFAULT
    published = 0
    _pub_rows = []   # for the end-of-batch summary (user 2026-06-22)
    # Daily cap (user 2026-06-29: "limit to 5 per day"). Count what's already gone out today and only
    # publish up to the remaining budget — the highest-priority candidates come first in `tickers`.
    _already = _published_today_count()
    _budget = max(0, X_MAX_PER_DAY - _already)
    if _budget <= 0:
        log.info(f"daily X cap reached ({_already}/{X_MAX_PER_DAY}) — nothing published.")
        return 0
    if len(tickers) > _budget:
        log.info(f"daily X cap: {_already} already published today; publishing up to {_budget} more "
                 f"(of {len(tickers)} candidates).")
    for i, tk in enumerate(tickers):
        if published >= _budget:
            log.info(f"daily X cap of {X_MAX_PER_DAY} reached — stopping after {published} this run.")
            break
        try:
            pub = build_publication(tk)
        except Exception as e:
            log.warning(f"{tk}: build failed — skipped: {e}"); pub = None
        if not pub:
            log.info(f"{tk}: no tradeable funnel — skipped.")
        else:
            res, tweet, png, thread = pub
            lead_id, n, all_ids = publish_thread_to_x(tweet, png, thread)
            if n >= 1:
                _record_publication(tk, lead_id, all_ids)
                _confirm_to_slack(tk, res.get("name", tk), lead_id, n, len(thread))
                published += 1
                _pub_rows.append({"ticker": tk, "name": res.get("name", tk), "lead_id": lead_id,
                                  "market": res.get("index") or _market_of(tk),
                                  "type": res.get("hvf_type"), "signal": res.get("hvf_signal"),
                                  "quality": res.get("pattern_quality"), "rr": res.get("risk_reward")})
                log.info(f"{tk}: published {n} tweet(s) to X (lead {lead_id}).")
            else:
                log.error(f"{tk}: nothing posted to X.")
        if inter_instrument_delay and i < len(tickers) - 1:
            log.info(f"waiting {inter_instrument_delay}s before the next instrument (avoid overlap)…")
            time.sleep(inter_instrument_delay)
    log.info(f"live X batch complete: published {published}/{len(tickers)} instrument(s).")
    # ONE consolidated summary to #arw-claude-twitter once all the tweets are out (user 2026-06-22).
    _summary_to_slack(_pub_rows)
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

    # ── List-recent mode (user 2026-06-24): print recent publications so the correct LEAD ids can
    #   be chosen for --delete-leads without hand-copying X URLs. Runs on Actions (SUPABASE_* set).
    #     python publish_one_to_x.py --list-recent        (newest 20)
    #     python publish_one_to_x.py --list-recent=40
    _list_arg = next((a for a in sys.argv if a == "--list-recent" or a.startswith("--list-recent=")), None)
    if _list_arg:
        n = 20
        if "=" in _list_arg:
            try:
                n = max(1, int(_list_arg.split("=", 1)[1]))
            except ValueError:
                pass
        try:
            from db_pool import get_db
            db = get_db()
            try:
                rows = db.run(
                    "select ticker, tweet_id, "
                    "to_char(published_at at time zone 'UTC', 'YYYY-MM-DD HH24:MI'), thread_ids "
                    "from x_publications where tweet_id is not null "
                    "order by published_at desc limit :n", n=n)
            finally:
                db.close()
            log.info(f"--list-recent: {len(rows)} publication(s), newest first:")
            for tk, tid, when, tids in rows:
                n_thread = len([s for s in str(tids or "").split(",") if s])
                print(f"{when} UTC  {tk:<10} lead={tid}  thread={n_thread}  "
                      f"https://x.com/{X_HANDLE}/status/{tid}")
        except Exception as e:
            log.error(f"--list-recent failed: {e}")
        return

    # ── Delete mode (user 2026-06-22): remove incorrect published threads by LEAD tweet id ──
    #   python publish_one_to_x.py --delete-leads=ID1,ID2,...
    # Deletes each lead + its conversation (x_publish.delete_thread). Irreversible. The
    # x_publications rows are LEFT as a historical record (re-publish uses --force to bypass dedup).
    _del_arg = next((a for a in sys.argv if a.startswith("--delete-leads=")), None)
    if _del_arg:
        from x_publish import delete_thread
        lead_ids = [s.strip() for s in _del_arg.split("=", 1)[1].split(",") if s.strip()]
        log.info(f"--delete-leads: deleting {len(lead_ids)} thread(s): {lead_ids}")
        # Look up the STORED thread ids per lead (recorded at publish time) so the whole thread is
        # removed precisely — no enumeration (X free tier 403s the reply read). Falls back to lead-only.
        _stored = {}
        try:
            from db_pool import get_db
            db = get_db()
            try:
                for lid in lead_ids:
                    rows = db.run("select thread_ids from x_publications where tweet_id = :i "
                                  "and thread_ids is not null order by published_at desc limit 1", i=lid)
                    if rows and rows[0][0]:
                        _stored[lid] = [s for s in str(rows[0][0]).split(",") if s]
            finally:
                db.close()
        except Exception as e:
            log.warning(f"--delete-leads: stored-id lookup failed ({e}) — deleting leads only")
        total = 0
        for lid in lead_ids:
            total += delete_thread(lid, ids=_stored.get(lid))
        log.info(f"delete complete: {total} tweet(s) deleted across {len(lead_ids)} thread(s).")
        return

    # ── Batch mode: publish today's top-N per market (from hvf_scan_log) to live X ──
    if top_n:
        from quality_report import _today_top
        tks = [tk for tk, _ in _today_top(top_n)]   # top N per market, market order
        # Restrict the morning HVF tweets to the markets chosen in Config -> X Posts (user 2026-07-06;
        # default FTSE 100 / FTSE 250 / NASDAQ 100 / S&P 500). Fail-open on any error.
        try:
            from config_store import get_x_hvf_markets
            from run_hvf_report import UNIVERSE
            _allowed = set(get_x_hvf_markets())
            _mkt_of = {tk: mkt for mkt, lst in UNIVERSE.items() for tk in lst}
            _before = len(tks)
            tks = [tk for tk in tks if _mkt_of.get(tk) in _allowed]
            if _before != len(tks):
                log.info(f"X HVF market filter ({sorted(_allowed)}): {_before} -> {len(tks)} instrument(s).")
        except Exception as e:
            log.warning(f"X HVF market filter skipped ({e}) - publishing all markets.")
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
    lead_id, posted, all_ids = publish_thread_to_x(tweet, png, thread)   # lead + card, then replies
    log.info(f"{ticker}: published {posted} tweet(s) to X (lead {lead_id}).")
    if posted < 1:
        log.error("nothing was posted (X keys missing or API error) — see log above.")
        sys.exit(3)
    if no_register:
        log.info(f"{ticker}: --no-register TEST — NOT recording this publication (dedup table untouched).")
    else:
        _record_publication(ticker, lead_id, all_ids)   # dedup record + thread ids for clean delete
    _confirm_to_slack(ticker, res.get("name", ticker), lead_id, posted, len(thread))


if __name__ == "__main__":
    main()
