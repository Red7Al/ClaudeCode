# ======================================================================================================================
# File:         working_orders_report.py
# Author:       Alex Hind
# Created:      2026-06-16
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Daily report of the engine-managed PRE-ORDERS — the `working_orders` table (pending HVF entry orders the engine
# manages through WATCHING -> PENDING -> FILLED/CANCELLED/EXPIRED, i.e. orders BEFORE they become live IG positions).
# Posts to the #arw-claude-orders Slack channel via the SLACK_ORDERS webhook (user 2026-06-16).
#
# Two sections:
#   1. LIVE pre-orders currently under management — PENDING (placed on IG, awaiting trigger) and WATCHING (engine-side,
#      proximity band not yet entered, no capital committed).
#   2. TODAY's outcomes — orders that moved to FILLED / CANCELLED / EXPIRED today.
#
# Usage:   python working_orders_report.py          # build + post to #arw-claude-orders
#          python working_orders_report.py --dry     # build + print, post NOTHING (local preview)
#
# Env (GitHub Secrets): SUPABASE_USER, SUPABASE_DB_PASSWORD, SLACK_ORDERS
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.1.1   2026-06-19  Alex Hind   Code-review fix: _done_line use the `note` var it already builds (was recomputing the
#                                 same string inline; dead variable). No behaviour change.
# 1.1.0   2026-06-19  Alex Hind   Current price + % from price + R:R on every WATCHING / Settled line (user 2026-06-19,
#                                 New B #1/#2 + C #3): _levels_str adds "now <px> · entry X (+a%) · stop Y (+b%) ·
#                                 target Z (+c%) · R:R". Live price via _live_price (yfinance fast_info -> last close, cached).
# 1.0.0   2026-06-16  Alex Hind   Initial build (user 2026-06-16): daily pre-order report of the working_orders table
#                                 to #arw-claude-orders (SLACK_ORDERS). Live PENDING/WATCHING + today's FILLED/CANCELLED/
#                                 EXPIRED. --dry for a no-post local preview.
# ======================================================================================================================

import os
import io
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("working_orders_report")

_LIVE_STATUSES = ("PENDING", "WATCHING")
_DONE_STATUSES = ("FILLED", "CANCELLED", "EXPIRED")


def _fmt(v) -> str:
    """Price/level formatter — thousands sep, up to 2 dp, trailing zeros trimmed."""
    if v is None:
        return "—"
    s = f"{float(v):,.2f}".rstrip("0").rstrip(".")
    return s or "0"


def _size(v) -> str:
    return "—" if v is None else f"{float(v):g}"


def _dt(ts) -> str:
    """Short 'dd Mon HH:MM' for a timestamp; '—' if absent."""
    try:
        return ts.strftime("%d %b %H:%M") if ts else "—"
    except Exception:
        return str(ts) if ts else "—"


def _date(ts) -> str:
    try:
        return ts.strftime("%d %b") if ts else "—"
    except Exception:
        return str(ts) if ts else "—"


def _dir_emoji(direction: str) -> str:
    d = (direction or "").upper()
    if d in ("BUY", "LONG", "BULLISH"):
        return "🟢"
    if d in ("SELL", "SHORT", "BEARISH"):
        return "🔴"
    return "▫️"


def fetch_live() -> list:
    """Working orders currently under management (PENDING or WATCHING)."""
    from db_pool import get_db
    db = get_db()
    try:
        return db.run(
            """select status, ticker, direction, hvf_type, size, entry_level, stop_level,
                      limit_level, session, placed_at, good_till, paper_trade, user_id
                 from working_orders
                where status in ('PENDING', 'WATCHING')
                order by status, placed_at desc""")
    finally:
        db.close()


def fetch_today_changes() -> list:
    """Working orders that moved to a terminal state TODAY."""
    from db_pool import get_db
    db = get_db()
    try:
        return db.run(
            """select status, ticker, direction, entry_level, stop_level, limit_level,
                      updated_at, filled_at, paper_trade, notes
                 from working_orders
                where status in ('FILLED', 'CANCELLED', 'EXPIRED')
                  and updated_at::date = current_date
                order by updated_at desc""")
    finally:
        db.close()


_PRICE_CACHE: dict = {}


def _live_price(ticker: str):
    """Current price for a working-order ticker (user 2026-06-19) — cached per run. Tries
    yfinance fast_info, falls back to the last daily close (YAHOO_MAP-aware). None on failure."""
    if ticker in _PRICE_CACHE:
        return _PRICE_CACHE[ticker]
    px = None
    try:
        import yfinance as yf
        fi = yf.Ticker(ticker).fast_info
        px = fi.get("lastPrice") if hasattr(fi, "get") else getattr(fi, "last_price", None)
        px = float(px) if px else None
    except Exception:
        px = None
    if not px:
        try:
            from price_action import _get_daily
            df = _get_daily(ticker, days=5)
            if df is not None and not df.empty:
                px = float(df["Close"].iloc[-1])
        except Exception:
            px = None
    _PRICE_CACHE[ticker] = px
    return px


def _levels_str(ticker, entry, stop, target) -> str:
    """Shared 'now + entry/stop/target with % from price + R:R' line (user 2026-06-19, New B/C):
    every working-order line shows the live price, each level's distance from it, and the R:R."""
    from price_action import pct_from_current
    cur = _live_price(ticker)

    def _wp(lv):
        p = pct_from_current(lv, cur)
        return f" ({p})" if p else ""

    rr_s = ""
    try:
        if entry and stop and target and (float(entry) - float(stop)) != 0:
            rr = abs(float(target) - float(entry)) / abs(float(entry) - float(stop))
            rr_s = f" · R:R {rr:.1f}:1"
    except Exception:
        rr_s = ""
    now = f"now {_fmt(cur)} · " if cur else ""
    return (f"    {now}entry {_fmt(entry)}{_wp(entry)} · stop {_fmt(stop)}{_wp(stop)} · "
            f"target {_fmt(target)}{_wp(target)}{rr_s}")


def _live_line(row) -> str:
    (status, ticker, direction, hvf_type, size, entry, stop, limit_lvl,
     session, placed_at, good_till, paper, _uid) = row
    paper_tag = "  _(paper)_" if paper else ""
    gtd = f" · GTD {_date(good_till)}" if good_till else ""
    sess = f" · {session}" if session else ""
    return (f"{_dir_emoji(direction)} *{ticker}* {(direction or '').upper()}{paper_tag}\n"
            f"{_levels_str(ticker, entry, stop, limit_lvl)}\n"
            f"    size {_size(size)}{sess} · placed {_dt(placed_at)}{gtd}")


def _done_line(row) -> str:
    (status, ticker, direction, entry, stop, limit_lvl,
     updated_at, filled_at, paper, notes) = row
    icon = {"FILLED": "✅", "CANCELLED": "🚫", "EXPIRED": "⌛"}.get(status, "•")
    paper_tag = "  _(paper)_" if paper else ""
    when = _dt(filled_at if status == "FILLED" else updated_at)
    note = f" — {notes}" if notes else ""
    return (f"{icon} *{ticker}* {(direction or '').upper()} {status.lower()} at {when}{paper_tag}\n"
            f"{_levels_str(ticker, entry, stop, limit_lvl)}{note}")


def _chunk(lines, limit=2900) -> list:
    """Pack lines into <=limit-char Slack sections (Slack caps a section at 3000)."""
    out, cur = [], ""
    for ln in lines:
        if cur and len(cur) + 1 + len(ln) > limit:
            out.append(cur); cur = ln
        else:
            cur = (cur + "\n" + ln) if cur else ln
    if cur:
        out.append(cur)
    return out


def build_blocks(live: list, changes: list, when: str) -> list:
    import requests  # noqa: F401  (kept symmetrical with post(); not used here)
    pending = [r for r in live if r[0] == "PENDING"]
    watching = [r for r in live if r[0] == "WATCHING"]

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📋 Pre-Order Report — {when}"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": (f"*{len(live)} live* (engine-managed, pre-IG-fill)  ·  "
                     f"{len(pending)} pending · {len(watching)} watching  |  "
                     f"*{len(changes)} settled today*")}},
        {"type": "divider"},
    ]

    if pending:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": f"*⏳ PENDING — placed on IG, awaiting trigger ({len(pending)})*"}})
        for blk in _chunk([_live_line(r) for r in pending]):
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": blk}})
    if watching:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": f"*👁 WATCHING — engine-side, no capital committed yet ({len(watching)})*"}})
        for blk in _chunk([_live_line(r) for r in watching]):
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": blk}})
    if not live:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": "_No pre-orders currently under management._"}})

    blocks.append({"type": "divider"})
    if changes:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": f"*📦 Settled today ({len(changes)})*"}})
        for blk in _chunk([_done_line(r) for r in changes]):
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": blk}})
    else:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": "_No working orders settled today._"}})

    return blocks


def post(blocks: list):
    import requests
    from notify import slack_enabled
    if not slack_enabled("orders"):   # per-channel switch (user 2026-08-03)
        log.info("Slack channel 'orders' disabled — pre-order report not posted")
        return False
    url = os.environ.get("SLACK_ORDERS", "")
    if not url:
        log.warning("SLACK_ORDERS not set — pre-order report not posted (this is expected on a local run).")
        return False
    try:
        requests.post(url, json={"blocks": blocks}, timeout=10)
        log.info("pre-order report posted to #arw-claude-orders")
        return True
    except Exception as e:
        log.error(f"pre-order report Slack post failed: {e}")
        return False


def main():
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except Exception:
        pass

    import datetime
    when = datetime.datetime.now(datetime.timezone.utc).strftime("%d %b %Y %H:%M UTC")
    live = fetch_live()
    changes = fetch_today_changes()
    log.info(f"{len(live)} live working order(s), {len(changes)} settled today")
    blocks = build_blocks(live, changes, when)

    if "--dry" in sys.argv:
        import json
        print(json.dumps(blocks, indent=2, default=str))
        log.info("--dry: nothing posted.")
        return
    post(blocks)
    try:   # record this run in the web app's Batch Activity (user 2026-08-11, P-12)
        from web_store import append_batch
        append_batch("cron-job.org", f"Pre-order report — {len(live)} live / {len(changes)} settled today", by="cron")
    except Exception:
        pass


if __name__ == "__main__":
    main()
