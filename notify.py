# =============================================================================
# File:         notify.py
# Author:       Alex Hind
# Created:      2026-05-30
#
# Description:
# -----------------------------------------------------------------------------
# Slack notification module for the EndToEndTrading system.
# Sends richly formatted messages to the correct Slack channel based on
# the type of event (trade, signal, alert, or weekly digest).
#
# Channels:
#   #claude-trading-trades   — trade opened / closed events with P&L
#   #claude-trading-signals  — session scan summaries and signal breakdowns
#   #claude-trading-alerts   — circuit breakers, macro gate failures, loss limits
#   #claude-trading-weekly   — weekend digest: P&L, senator scores, superinvestors
#   #claude-trading-daily    — end-of-day executive report, UK morning brief
#
# All webhook URLs are loaded from environment variables.
# Messages use Slack Block Kit for structured, readable formatting.
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-05-30  Alex Hind   Initial build. Four channels, Block Kit
#                                 formatting, test harness included.
# 1.1.0   2026-06-05  Alex Hind   Added HVF R:R field to signal_detail Slack
#                                 block. Shows calculated ratio (e.g. 2.73:1)
#                                 on every trade signal message.
# 1.2.0   2026-06-05  Alex Hind   Added fifth channel: #claude-trading-daily
#                                 (SLACK_DAILY) for end-of-day reports and
#                                 morning briefs. Full instrument names in
#                                 session_summary candidate lines.
# 1.2.1   2026-06-06  Alex Hind   Fix session_heartbeat lateness calculation:
#                                 midnight-rollover was incorrectly applied when
#                                 session ran *before* scheduled time (e.g. Cloud
#                                 Routines fired at 13:36 vs scheduled 14:30 →
#                                 showed "1386min late" instead of "on time").
#                                 Guard: only roll midnight when late_mins < -120.
# 1.2.2   2026-06-07  Alex Hind   alert_watchdog_trigger: fix wording — said "GitHub
#                                 Actions cron missed" but scheduling is via cron-job.org.
#                                 Now "Scheduled run for X was missed ... (cron-job.org
#                                 trigger or workflow failure)".
# 1.3.0   2026-06-09  Alex Hind   Full instrument name EVERY time a ticker is shown
#                                 (memory/feedback_instrument_names). New cached fmt()
#                                 -> 'TICKER (Full Name)'; replaces per-call DB lookups
#                                 (one query/process, not one per message). Fixed 5
#                                 alerts that showed the bare ticker: circuit_breaker,
#                                 stop_slippage, position_deterioration, director_cluster,
#                                 weekly_digest. Names cleaned of IG suffixes
#                                 ('CleanSpark Inc (24 Hours)' -> 'CLSK (CleanSpark Inc)').
# 1.4.0   2026-06-10  Alex Hind   Working-order notifications (HVF pending entries →
#                                 IG working orders, user 2026-06-10): working_order_placed,
#                                 working_order_updated, working_order_outcome
#                                 (FILLED announced via trade_opened; CANCELLED/EXPIRED
#                                 surfaced so nothing disappears silently).
# 1.5.0   2026-07-09  Alex Hind   Trade reference (IG deal_id) added to all trade
#                                 Slack blocks: trade_opened, working_order_placed,
#                                 working_order_updated, working_order_outcome.
#                                 trade_closed: Held duration now always shown (was
#                                 conditional — omitted when opened_at was None).
#
# Dependencies:
# -----------------------------------------------------------------------------
#   pip install requests
#
# Environment Variables Required:
# -----------------------------------------------------------------------------
#   SLACK_TRADES    Webhook URL for #claude-trading-trades
#   SLACK_SIGNALS   Webhook URL for #claude-trading-signals
#   SLACK_ALERTS    Webhook URL for #claude-trading-alerts
#   SLACK_WEEKLY    Webhook URL for #claude-trading-weekly
#   SLACK_DAILY     Webhook URL for #claude-trading-daily
# =============================================================================

import os
from db_pool import get_db as _pool_get_db   # resilient session-pooler connection (timeout+retry)
from dotenv import load_dotenv; load_dotenv(override=True)
import logging
import re
import requests
import pg8000.native
from datetime import datetime, timezone


# =============================================================================
# Logging
# =============================================================================

log = logging.getLogger("notify")


# =============================================================================
# Webhook Configuration — loaded from environment variables
# =============================================================================

WEBHOOKS = {
    "trades":   os.environ.get("SLACK_TRADES",   ""),
    "signals":  os.environ.get("SLACK_SIGNALS",  ""),
    "alerts":   os.environ.get("SLACK_ALERTS",   ""),
    "weekly":   os.environ.get("SLACK_WEEKLY",   ""),
    "daily":    os.environ.get("SLACK_DAILY",    ""),
    "orders":   os.environ.get("SLACK_ORDERS",   ""),
    "twitter":  os.environ.get("SLACK_TWITTER",  ""),
}


# =============================================================================
# Internal Helpers
# =============================================================================

def _send(channel: str, blocks: list) -> bool:
    """
    POST a Block Kit message to the specified Slack channel via webhook.
    Returns True on success, False on failure.
    """
    url = WEBHOOKS.get(channel)
    if not url:
        log.warning(f"No webhook URL configured for channel: {channel}")
        return False
    try:
        resp = requests.post(url, json={"blocks": blocks}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Slack send failed ({channel}): {e}")
        return False


def _ts() -> str:
    """Return current UTC time as a formatted string for message footers."""
    return datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")


_NAME_CACHE = None   # ticker -> clean name, loaded once per process


def _clean_name(desc: str) -> str:
    """
    Reduce an IG market description to the bare instrument/company name.
        'CleanSpark Inc (24 Hours)'                       -> 'CleanSpark Inc'
        'Nebius Group NV (24 Hours) - uses old Yandex epic'-> 'Nebius Group NV'
        'GBP/USD'                                          -> 'GBP/USD'
    """
    if not desc:
        return ""
    name = desc.split(" - ")[0]                    # drop editorial " - ..." notes
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name)   # drop trailing "(24 Hours)" etc.
    return name.strip()


def _load_names() -> dict:
    """
    Load and cache the ticker -> clean-name map from epic_lookup. One query per
    process (not one per message), so a session summary listing many instruments
    no longer opens a DB connection per ticker. Falls back to an empty map (so
    fmt() degrades to the bare ticker) if the DB is unreachable.
    """
    global _NAME_CACHE
    if _NAME_CACHE is not None:
        return _NAME_CACHE
    cache = {}
    try:
        conn = _pool_get_db()
        for tk, desc in conn.run("select ticker, description from epic_lookup"):
            cache[tk] = _clean_name(desc)
        conn.close()
    except Exception as e:
        log.warning(f"instrument-name cache load failed (using bare tickers): {e}")
    _NAME_CACHE = cache
    return cache


def fmt(ticker: str) -> str:
    """
    Canonical display label for an instrument: 'TICKER (Full Name)'.

    Use this EVERY time a ticker is shown to a human (Slack, reports) — never
    print the bare ticker. See memory/feedback_instrument_names. Falls back to
    just the ticker when no name is known.
        fmt('CLSK')   -> 'CLSK (CleanSpark Inc)'
        fmt('XAUUSD') -> 'XAUUSD (Spot Gold)'
    """
    if not ticker:
        return ticker or ""
    name = _load_names().get(ticker, "")
    return f"{ticker} ({name})" if name and name != ticker else ticker


def _instrument_name(ticker: str) -> str:
    """Backwards-compatible: clean name only (no ticker prefix). Prefer fmt()."""
    return _load_names().get(ticker, "") or ticker


def should_post_summary(min_hours: int = 2) -> bool:
    """
    Rate-limit the periodic monitor SUMMARY to at most once per `min_hours`.

    Monitoring now runs every 5 min for fresh prices/volume/HVF, but the full
    Slack summary must not spam #signals (user directive 2026-06-09: summaries
    every 2 hours or less). Each monitor run is a fresh process, so there is no
    in-process counter — gate on the wall clock instead: fire only in the first
    run of an aligned hour block (hour % min_hours == 0, minute < 5). Event
    alerts (new trade, closure, circuit breaker, deterioration) are NOT gated.
    """
    now = datetime.now(timezone.utc)
    return now.hour % max(1, min_hours) == 0 and now.minute < 5


def _format_duration(opened_at) -> str:
    """
    Human-readable hold time from an open timestamp to now, e.g. '2h 14m' or
    '1d 3h 5m'. Accepts a datetime or an ISO-8601 string. Returns '' if unknown.
    """
    if not opened_at:
        return ""
    try:
        if isinstance(opened_at, str):
            ts = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        else:
            ts = opened_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        secs = max(0, int((datetime.now(timezone.utc) - ts).total_seconds()))
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        m, _ = divmod(rem, 60)
        parts = []
        if d:
            parts.append(f"{d}d")
        if h:
            parts.append(f"{h}h")
        parts.append(f"{m}m")
        return " ".join(parts)
    except Exception:
        return ""


# =============================================================================
# Trade Notifications → #claude-trading-trades
# Fired when a trade is opened or closed.
# =============================================================================

def trade_opened(
    ticker:         str,
    direction:      str,        # "BUY" or "SELL"
    size:           float,
    entry:          float,
    stop:           float,
    target:         float,
    session:        str,        # e.g. "US_OPEN"
    signal_summary: str,        # e.g. "Options BULLISH | BB breakout | Senate: Tuberville"
    user:           str = "Owner",
    deal_ref:       str = "",   # IG deal reference / deal_id
):
    """Send a trade opened notification to #claude-trading-trades."""
    emoji      = "🟢" if direction == "BUY" else "🔴"
    rr         = round(abs(target - entry) / max(abs(entry - stop), 0.0001), 2)
    title      = fmt(ticker)

    fields = [
        {"type": "mrkdwn", "text": f"*User:*\n{user}"},
        {"type": "mrkdwn", "text": f"*Session:*\n{session}"},
        {"type": "mrkdwn", "text": f"*Direction:*\n{direction}"},
        {"type": "mrkdwn", "text": f"*Size:*\n{size}"},
        {"type": "mrkdwn", "text": f"*Entry:*\n{entry}"},
        {"type": "mrkdwn", "text": f"*Stop:*\n{stop}"},
        {"type": "mrkdwn", "text": f"*Target:*\n{target}"},
        {"type": "mrkdwn", "text": f"*R:R:*\n{rr}:1"},
    ]
    if deal_ref:
        fields.append({"type": "mrkdwn", "text": f"*Trade Ref:*\n`{deal_ref}`"})

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} Trade Opened — {title}"}
        },
        {
            "type": "section",
            "fields": fields
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Signals:* {signal_summary}"}
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _ts()}]
        },
    ]
    _send("trades", blocks)


def trade_closed(
    ticker:       str,
    direction:    str,
    entry:        float,
    close:        float,
    pnl:          float,
    close_reason: str,          # STOP_HIT, TARGET_HIT, MANUAL, CIRCUIT_BREAKER, SYSTEM, UNKNOWN
    user:         str = "Owner",
    opened_at=None,             # open timestamp (datetime/ISO str) — shows hold duration
    rr:           float = None, # R:R ratio calculated from stop/target at entry
):
    """Send a trade closed notification to #claude-trading-trades."""

    pnl_emoji  = "✅" if pnl >= 0 else "❌"
    title      = fmt(ticker)

    # Human-readable labels for each close reason code
    reason_labels = {
        "STOP_HIT":        "🛑 Stop Loss Hit",
        "TARGET_HIT":      "🎯 Take Profit Hit",
        "MANUAL":          "👤 Manual Close",
        "CIRCUIT_BREAKER": "⚡ Circuit Breaker",
        "SYSTEM":          "⚙️ System Close",
        # Not a bug — IG's activity history did not link a reason to this deal id.
        "UNKNOWN":         "❓ Closed (reason unavailable from IG)",
    }
    reason_label = reason_labels.get(close_reason, close_reason)
    duration     = _format_duration(opened_at)

    rr_str = f"{rr:.1f}:1" if rr else "—"
    fields = [
        {"type": "mrkdwn", "text": f"*User:*\n{user}"},
        {"type": "mrkdwn", "text": f"*Direction:*\n{direction}"},
        {"type": "mrkdwn", "text": f"*Entry:*\n{entry}"},
        {"type": "mrkdwn", "text": f"*Close:*\n{close}"},
        {"type": "mrkdwn", "text": f"*P&L:*\n£{pnl:+.2f}"},
        {"type": "mrkdwn", "text": f"*R:R:*\n{rr_str}"},
        {"type": "mrkdwn", "text": f"*Reason:*\n{reason_label}"},
        {"type": "mrkdwn", "text": f"*Held:*\n{duration or '—'}"},
    ]

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{pnl_emoji} Trade Closed — {title}"}
        },
        {
            "type": "section",
            "fields": fields
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _ts()}]
        },
    ]
    _send("trades", blocks)


# =============================================================================
# Working Orders → #claude-trading-trades
# HVF setups are placed as PENDING entry orders at the breakout level (H3) with
# the pattern's stop and target attached (user 2026-06-10). These notifications
# track the order lifecycle: placed → (updated) → filled / cancelled / expired.
# =============================================================================

def working_order_placed(
    ticker:         str,
    direction:      str,        # "BUY" or "SELL"
    size:           float,
    entry:          float,      # pending entry level (HVF H3 breakout)
    stop:           float,
    target:         float,
    otype:          str,        # "STOP" (breakout) or "LIMIT" (pullback)
    good_till:      str,        # expiry of the order, display string
    session:        str,
    signal_summary: str,
    user:           str = "Owner",
    deal_ref:       str = "",   # IG deal reference / deal_id
):
    """Announce a new pending working order (NOT yet a position)."""
    emoji = "🟢" if direction == "BUY" else "🔴"
    rr    = round(abs(target - entry) / max(abs(entry - stop), 0.0001), 2)
    kind  = "breakout entry" if otype == "STOP" else "pullback entry"

    fields = [
        {"type": "mrkdwn", "text": f"*User:*\n{user}"},
        {"type": "mrkdwn", "text": f"*Session:*\n{session}"},
        {"type": "mrkdwn", "text": f"*Size:*\n{size}"},
        {"type": "mrkdwn", "text": f"*Entry (H3):*\n{entry}"},
        {"type": "mrkdwn", "text": f"*Stop:*\n{stop}"},
        {"type": "mrkdwn", "text": f"*Target:*\n{target}"},
        {"type": "mrkdwn", "text": f"*R:R:*\n{rr}:1"},
        {"type": "mrkdwn", "text": f"*Good till:*\n{good_till}"},
    ]
    if deal_ref:
        fields.append({"type": "mrkdwn", "text": f"*Trade Ref:*\n`{deal_ref}`"})

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text",
                     "text": f"{emoji}⏳ Working Order Placed — {fmt(ticker)}"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": (f"Pending *{direction} {otype}* order ({kind}) — fills only "
                              f"when price reaches the entry level. Not yet a position.")}
        },
        {
            "type": "section",
            "fields": fields
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Signals:* {signal_summary}"}
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _ts()}]
        },
    ]
    _send("orders", blocks)


def working_order_updated(
    ticker:    str,
    direction: str,
    old_entry: float, new_entry: float,
    old_stop:  float, new_stop:  float,
    old_target: float, new_target: float,
    session:   str,
    user:      str = "Owner",
    deal_ref:  str = "",    # IG deal reference / deal_id
):
    """Announce that an existing pending order was amended to fresher HVF levels."""
    def _chg(a, b):
        return f"{a} → *{b}*" if a != b else f"{b} (unchanged)"

    fields = [
        {"type": "mrkdwn", "text": f"*User:*\n{user}"},
        {"type": "mrkdwn", "text": f"*Session:*\n{session}"},
        {"type": "mrkdwn", "text": f"*Entry:*\n{_chg(old_entry, new_entry)}"},
        {"type": "mrkdwn", "text": f"*Stop:*\n{_chg(old_stop, new_stop)}"},
        {"type": "mrkdwn", "text": f"*Target:*\n{_chg(old_target, new_target)}"},
    ]
    if deal_ref:
        fields.append({"type": "mrkdwn", "text": f"*Trade Ref:*\n`{deal_ref}`"})

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text",
                     "text": f"🔄 Working Order Updated — {fmt(ticker)}"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": (f"Latest scan re-confirmed the {direction} HVF setup with moved "
                              f"levels — the pending order was amended (not duplicated).")}
        },
        {
            "type": "section",
            "fields": fields
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _ts()}]
        },
    ]
    _send("trades", blocks)


def working_order_outcome(
    ticker:    str,
    direction: str,
    entry:     float,
    outcome:   str,             # "CANCELLED" or "EXPIRED"
    detail:    str = "",
    user:      str = "Owner",
    deal_ref:  str = "",        # IG deal reference / deal_id
):
    """
    Surface a pending order that ended WITHOUT filling (cancelled in IG, or its
    good-till date passed). Fills are announced separately via trade_opened().
    Posting this keeps the order lifecycle fully visible — no silent endings.
    """
    label = {"CANCELLED": "🚫 Working Order Cancelled",
             "EXPIRED":   "⌛ Working Order Expired"}.get(outcome, f"Working order {outcome}")
    ref_line = f"\nTrade ref: `{deal_ref}`" if deal_ref else ""
    text = (f"*{fmt(ticker)}* {direction} pending entry at *{entry}* ended without filling "
            f"({outcome.lower()})." + (f"\n{detail}" if detail else "") + ref_line)
    blocks = [
        {"type": "header",  "text": {"type": "plain_text", "text": f"{label} — {fmt(ticker)}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"User: {user} | {_ts()}"}]},
    ]
    _send("trades", blocks)


def working_order_watching(
    ticker:        str,
    direction:     str,
    entry:         float,
    stop:          float,
    target:        float,
    dist_pct:      float,
    proximity_pct: float,
    session_name:  str = "",
):
    """Price is not yet within the proximity band — order queued as WATCHING.
    No capital committed, NOT visible in IG platform."""
    rr = None
    if stop and target and entry:
        sd = abs(entry - stop); td = abs(target - entry)
        if sd > 0:
            rr = round(td / sd, 1)
    rr_str = f"{rr:.1f}:1" if rr else "—"
    blocks = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": f"👁 Watching (NOT in IG) — {fmt(ticker)}"}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": (f"*{fmt(ticker)}* {direction} setup queued internally. "
                           f"*No IG order has been placed* — this will not appear in the IG platform. "
                           f"Price is *{dist_pct:.1f}%* from entry; order places automatically when within *{proximity_pct}%*.")}},
        {"type": "section",
         "fields": [
             {"type": "mrkdwn", "text": f"*Direction:*\n{direction}"},
             {"type": "mrkdwn", "text": f"*Entry:*\n{entry}"},
             {"type": "mrkdwn", "text": f"*Stop:*\n{stop}"},
             {"type": "mrkdwn", "text": f"*Target:*\n{target}"},
             {"type": "mrkdwn", "text": f"*R:R:*\n{rr_str}"},
             {"type": "mrkdwn", "text": f"*Distance from entry:*\n{dist_pct:.1f}%"},
         ]},
        {"type": "context",
         "elements": [{"type": "mrkdwn",
                       "text": f"Session: {session_name} | {_ts()}"}]},
    ]
    _send("orders", blocks)


def working_order_watching_promoted(
    ticker:       str,
    direction:    str,
    entry:        float,
    stop:         float,
    target:       float,
    deal_id:      str,
    session_name: str = "",
):
    """A WATCHING order has entered the proximity band and been placed on IG."""
    blocks = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": f"✅ Order placed — {fmt(ticker)} (was watching)"}},
        {"type": "section",
         "fields": [
             {"type": "mrkdwn", "text": f"*Direction:*\n{direction}"},
             {"type": "mrkdwn", "text": f"*Entry:*\n{entry}"},
             {"type": "mrkdwn", "text": f"*Stop:*\n{stop}"},
             {"type": "mrkdwn", "text": f"*Target:*\n{target}"},
         ]},
        {"type": "context",
         "elements": [{"type": "mrkdwn",
                       "text": f"Price entered {entry} proximity band — order now live. "
                               f"Deal: `{deal_id}` | {session_name} | {_ts()}"}]},
    ]
    _send("orders", blocks)


def working_order_cancelled_proximity(
    ticker:        str,
    direction:     str,
    entry:         float,
    current_price: float,
    dist_pct:      float,
    threshold_pct: float,
    deal_id:       str,
):
    """A PENDING working order was cancelled because price moved outside the band."""
    blocks = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": f"🚫 Working order cancelled — {fmt(ticker)}"}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": (f"*{fmt(ticker)}* {direction} order at *{entry}* cancelled.\n"
                           f"Price is now *{current_price}* — *{dist_pct:.1f}%* from entry "
                           f"(threshold {threshold_pct}%). No capital was lost.")}},
        {"type": "context",
         "elements": [{"type": "mrkdwn",
                       "text": f"Deal: `{deal_id}` | {_ts()}"}]},
    ]
    _send("orders", blocks)


# =============================================================================
# Signal Summaries → #claude-trading-signals
# Fired at the end of each session scan.
# =============================================================================

def session_summary(
    session_name: str,
    macro:        dict,     # from get_macro_gate() in signals.py
    scanned:      int,
    candidates:   list      # list of signal dicts with trade_signal=True
):
    """Send a session scan summary to #claude-trading-signals."""

    gate_emoji = "✅" if macro.get("macro_gate_pass") else "❌"

    # Build candidate lines — one per instrument with a trade signal
    if candidates:
        candidate_lines = ""
        for c in candidates:
            dir_emoji  = "🟢" if c.get("direction") == "BUY" else "🔴"
            name_str   = fmt(c["ticker"])
            candidate_lines += (
                f"{dir_emoji} *{name_str}* {c['direction']} — "
                f"{c['primary_count']} primaries, {c['confirmation_count']} confirmations\n"
            )
    else:
        candidate_lines = "_No trade candidates this session_"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📊 Session Scan — {session_name}"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Macro Gate:*\n{gate_emoji} {macro.get('gate_reason', '—')}"},
                {"type": "mrkdwn", "text": f"*VIX:*\n{macro.get('vix', '—')}"},
                {"type": "mrkdwn", "text": f"*DXY:*\n{macro.get('dxy', '—')}"},
                {"type": "mrkdwn", "text": f"*Yield Spread:*\n{macro.get('yield_spread', '—')}%"},
            ]
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Instruments scanned:* {scanned}\n*Trade candidates:*\n{candidate_lines}"
            }
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _ts()}]
        },
    ]
    _send("signals", blocks)


def signal_detail(ticker: str, signal: dict):
    """
    Send a full signal breakdown for a specific instrument.
    Useful for understanding exactly which signals fired on a candidate trade.
    """
    title     = fmt(ticker)
    checks = {
        "Options flow":   signal.get("options_bias", "—"),
        "BB squeeze":     "Yes" if signal.get("bb_squeeze") else "No",
        "BB breakout":    signal.get("bb_breakout_dir") or "—",
        "GEX":            signal.get("gex_bias", "—"),
        "VWAP":           signal.get("vwap_position", "—"),
        "COT bias":       signal.get("cot_bias", "—"),
        "Director buys":  "✅" if signal.get("director_signal") else "—",
        "Activist (13D)": "✅" if signal.get("activist_signal") else "—",
        "Senate signal":  signal.get("senate_senator") or "—",
        "Superinvestor":  signal.get("notable_investor") or "—",
        "Social mention": signal.get("social_mention") or "—",
    }
    lines = "\n".join(f"• *{k}:* {v}" for k, v in checks.items())

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🔍 Signal Detail — {title}"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": lines}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*ATR:*\n{signal.get('atr', '—')}"},
                {"type": "mrkdwn", "text": f"*Stop distance:*\n{signal.get('stop_distance', '—')}"},
                {"type": "mrkdwn", "text": f"*Direction:*\n{signal.get('direction', '—')}"},
                {"type": "mrkdwn", "text": f"*Trade triggered:*\n{'Yes' if signal.get('trade_signal') else 'No'}"},
                {"type": "mrkdwn", "text": f"*HVF R:R:*\n{'{:.2f}:1'.format(signal.get('hvf_risk_reward')) if signal.get('hvf_risk_reward') else '—'}"},
            ]
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _ts()}]
        },
    ]
    _send("signals", blocks)


# =============================================================================
# Alerts → #claude-trading-alerts
# Fired for risk events, system blocks, and conditions requiring attention.
# =============================================================================

def alert_stop_slippage(
    ticker:          str,
    direction:       str,
    open_price:      float,
    stop_level:      float,
    close_price:     float,
    size:            float,
    expected_loss:   float,
    actual_loss:     float,
    slippage_ratio:  float,
    original_reason: str,
):
    """
    Alert when a position closes significantly worse than its stop level.

    Stop slippage happens when IG cannot fill the stop at the set price —
    usually because price gapped through it (pre-market hours, news event,
    thin liquidity) or because IG closed the position for system reasons.

    Example: IBM stop at 32362, closed at 32258 = 4.8× the stop distance.
    Expected loss £4.37, actual loss £21.12.

    Posted to #claude-trading-alerts — this is actionable: review whether
    the instrument should be traded at that time of day / market conditions.
    """
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text",
                     "text": f"⚠️ Stop Slippage — {fmt(ticker)} {direction}"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Entry:*\n{open_price}"},
                {"type": "mrkdwn", "text": f"*Stop set at:*\n{stop_level}"},
                {"type": "mrkdwn", "text": f"*Actual close:*\n{close_price}"},
                {"type": "mrkdwn", "text": f"*Size:*\n{size}"},
                {"type": "mrkdwn",
                 "text": f"*Expected loss:*\n£{expected_loss:.2f}"},
                {"type": "mrkdwn",
                 "text": f"*Actual loss:*\n£{actual_loss:.2f}  ({slippage_ratio:.1f}× stop distance)"},
            ]
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*IG close reason:* `{original_reason}`\n"
                    f"The position closed {slippage_ratio:.1f}× further from entry than the stop was set. "
                    f"Likely cause: price gapped through the stop (pre-market / thin liquidity / news). "
                    f"Review whether {fmt(ticker)} should be traded outside regular market hours."
                )
            }
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _ts()}]
        },
    ]
    _send("alerts", blocks)


def alert_circuit_breaker(user: str, ticker: str, reason: str):
    """Notify that a circuit breaker has blocked a trade."""
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "⚡ Circuit Breaker Triggered"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*User:*\n{user}"},
                {"type": "mrkdwn", "text": f"*Instrument:*\n{fmt(ticker)}"},
                {"type": "mrkdwn", "text": f"*Reason:*\n{reason}"},
            ]
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _ts()}]
        },
    ]
    _send("alerts", blocks)


def alert_missed_trade(ticker: str, direction: str, reason: str, signal_summary: str = ""):
    """
    A TRADEABLE signal fired but the trade was NOT placed — surface it loudly to
    #alerts so a missed opportunity is never silent. Names the instrument, side,
    the exact block reason (cap / spread / funds / epic / market hours / rejection)
    and the signals that fired, so the user can raise a cap, add funds, or trade it
    manually. (User directive 2026-06-09: "if we get strong signals but can't trade
    due to a cap — MAKE ME AWARE WITH CLEAR ALERT".)
    """
    fields = [
        {"type": "mrkdwn", "text": f"*Instrument:*\n{fmt(ticker)}"},
        {"type": "mrkdwn", "text": f"*Direction:*\n{direction}"},
        {"type": "mrkdwn", "text": f"*Why NOT placed:*\n{reason}"},
    ]
    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": "⚠️ TRADEABLE SIGNAL NOT PLACED"}},
        {"type": "section", "fields": fields},
    ]
    if signal_summary:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": f"*Signals that fired:*\n{signal_summary}"}})
    blocks.append({"type": "context",
                   "elements": [{"type": "mrkdwn",
                                 "text": "_Signal was valid — blocked at execution. Raise the cap / add funds / "
                                         "review spread, or trade manually._ | " + _ts()}]})
    _send("alerts", blocks)


def alert_daily_loss_limit(user: str, total_pnl: float, limit_pct: float):
    """Notify that a user has hit their daily loss limit — no more trades today."""
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🚨 Daily Loss Limit Hit"}
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{user}* has reached the daily loss limit.\n"
                    f"*Total P&L today:* £{total_pnl:+.2f}\n"
                    f"*Limit:* {limit_pct}%\n"
                    f"No further trades will be placed for this user today."
                )
            }
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _ts()}]
        },
    ]
    _send("alerts", blocks)


def alert_macro_gate_failed(reason: str, vix: float, yield_spread: float, session: str):
    """Notify that the macro gate has failed — no trades this session."""
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🚧 Macro Gate Failed — No Trades This Session"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Session:*\n{session}"},
                {"type": "mrkdwn", "text": f"*VIX:*\n{vix}"},
                {"type": "mrkdwn", "text": f"*Yield Spread:*\n{yield_spread}%"},
                {"type": "mrkdwn", "text": f"*Reason:*\n{reason}"},
            ]
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _ts()}]
        },
    ]
    _send("alerts", blocks)


def alert_system_error(session: str, component: str, summary: str, detail: str = ""):
    """
    Notify that a system-level error occurred during a session.
    Used for DB failures, scan errors, and unexpected zero-result scans.
    Posts to #claude-trading-alerts.
    """
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🔧 System Error — Action Required"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Session:*\n{session}"},
                {"type": "mrkdwn", "text": f"*Component:*\n{component}"},
                {"type": "mrkdwn", "text": f"*Summary:*\n{summary}"},
            ]
        },
    ]
    if detail:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Detail:*\n```{detail[:500]}```"}
        })
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": _ts()}]
    })
    _send("alerts", blocks)


def session_heartbeat(
    session_name:       str,
    scheduled_utc:      str,       # e.g. "14:30"
    actual_utc:         str,       # e.g. "18:34"  (when the workflow actually ran)
    instruments_scanned: int,
    trades_placed:      int,
    gate_pass:          bool,
    gate_reason:        str,
    market_stress:      str = "NORMAL",     # NORMAL / STRESS / HIGH_STRESS
    spx_change_pct:     float = None,
):
    """
    Heartbeat posted to #claude-trading-signals at the END of every session,
    regardless of whether trades were placed.

    Purpose: gives you positive confirmation the session DID run, the exact
    time it ran (vs scheduled), and a one-line health status. Silence in Slack
    means the watchdog is already re-triggering — not that the system is stuck.
    """
    late_mins = None
    try:
        from datetime import datetime, timezone
        sch_h, sch_m  = int(scheduled_utc.split(":")[0]), int(scheduled_utc.split(":")[1])
        act_h, act_m  = int(actual_utc.split(":")[0]),   int(actual_utc.split(":")[1])
        sch_total = sch_h * 60 + sch_m
        act_total = act_h * 60 + act_m
        late_mins = act_total - sch_total
        # Handle midnight rollover in both directions:
        #   Late  rollover: scheduled 23:30, ran 00:15 → late_mins = -855 → +1440 → +585 (late)
        #   Early rollover: scheduled 00:00, ran 23:55 → late_mins = +1435 → -1440 → -5 (early)
        # Clamp: if still negative after rollover adjustments, ran early → treat as on time.
        if late_mins < -120:
            late_mins += 1440   # rolled midnight going forward (e.g. sched 23:30 → ran 00:15)
        elif late_mins > 1320:  # > 22 hours late is implausible — must have run previous day
            late_mins -= 1440
        if late_mins < 0:
            late_mins = 0       # ran early (any direction) → on time
    except Exception:
        pass

    timing_str = actual_utc
    if late_mins is not None and late_mins > 5:
        timing_str = f"{actual_utc} ⚠ {late_mins}min late"
    elif late_mins is not None:
        timing_str = f"{actual_utc} ✓ on time"

    stress_emoji = {"NORMAL": "🟢", "STRESS": "🟡", "HIGH_STRESS": "🔴"}.get(market_stress, "⚪")
    stress_str   = f"{stress_emoji} {market_stress}"
    if spx_change_pct is not None:
        stress_str += f" (SPX {spx_change_pct:+.2f}%)"

    gate_emoji = "✅" if gate_pass else "🚫"

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*⏱ {session_name} heartbeat*  |  {timing_str}\n"
                    f"{gate_emoji} Gate: {gate_reason[:80]}  |  "
                    f"Market: {stress_str}  |  "
                    f"Scanned: {instruments_scanned}  |  "
                    f"Trades: {trades_placed}"
                )
            }
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _ts()}]
        },
    ]
    _send("signals", blocks)


def alert_director_cluster_developing(session: str, clusters: list):
    """
    Alert that one or more tickers have 3+ insider Form 4 purchases in 30 days
    but no full trade signal yet. These are DEVELOPING candidates — insiders are
    accumulating before the technical setup has confirmed. Watch for price action
    to form a tradeable entry.

    clusters: list of dicts with keys: ticker, director_count, director_detail
    Posts to #claude-trading-signals.
    """
    if not clusters:
        return
    lines = []
    for c in clusters:
        ticker  = c.get("ticker", "")
        count   = c.get("director_count", 0)
        detail  = c.get("director_detail", "")
        lines.append(f"• *{fmt(ticker)}* — {count} insider buys (Form 4)\n  _{detail}_")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text",
                     "text": f"👥 Director Cluster — Developing Candidates ({session})"}
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{len(clusters)} ticker(s)* with 3+ insider purchases in 30 days "
                    f"— no trade signal yet.\n"
                    f"Watch for technical setup (HVF, BB breakout, ORB) to confirm entry.\n\n"
                    + "\n".join(lines)
                )
            }
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn",
                          "text": f"_Not traded until price action confirms._ | {_ts()}"}]
        },
    ]
    _send("signals", blocks)


def alert_position_deterioration(session: str, ticker: str, direction: str, reasons: str):
    """
    Alert that an open position is showing intraday deterioration signals.
    Posted to #claude-trading-alerts so the user can decide whether to tighten
    the stop or exit early. Does NOT close the position automatically.
    """
    dir_emoji = "📈" if direction == "BUY" else "📉"
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"⚠️ Position Deterioration — {fmt(ticker)}"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Ticker:*\n{dir_emoji} {fmt(ticker)} {direction}"},
                {"type": "mrkdwn", "text": f"*Session:*\n{session}"},
            ]
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Intraday signals turning against position:*\n{reasons}"
            }
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn",
                          "text": f"_Position not closed automatically — review stop level._ | {_ts()}"}]
        },
    ]
    _send("alerts", blocks)


def alert_watchdog_trigger(session_name: str, workflow: str, late_minutes: int):
    """Alert that watchdog auto-triggered a missed session."""
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🔁 Watchdog Auto-Triggered — {session_name}"}
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Scheduled run for {session_name} was missed by ~{late_minutes} min "
                    f"(cron-job.org trigger or workflow failure).\n"
                    f"Watchdog fired `{workflow}` automatically.\n"
                    f"_No action needed — session is recovering now._"
                )
            }
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _ts()}]
        },
    ]
    _send("alerts", blocks)


def alert_calendar_block(event: str, session: str):
    """Notify that trading has been paused due to an imminent high-impact economic event."""
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📅 Trading Paused — High Impact Event"}
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Session:* {session}\n"
                    f"*Event:* {event}\n"
                    f"No new positions within 30 minutes of a high-impact event."
                )
            }
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _ts()}]
        },
    ]
    _send("alerts", blocks)


# =============================================================================
# Weekly Digest → #claude-trading-weekly
# Fired by the weekend review routine every Saturday morning.
# =============================================================================

def weekly_digest(stats: dict, top_senators: list, superinvestor_changes: list):
    """
    Send the weekly performance and intelligence digest.

    Args:
        stats:                  dict with keys: total_pnl, trade_count, win_rate, best_trade
        top_senators:           list of dicts with keys: name, score, win_rate, trade_count
        superinvestor_changes:  list of dicts with keys: investor, action, ticker
    """

    # Format senator table (top 5)
    senator_lines = "\n".join(
        f"• *{s['name']}* — score {s['score']:.4f} "
        f"(win rate {s['win_rate'] * 100:.1f}%, {s['trade_count']} trades)"
        for s in top_senators[:5]
    ) or "_No qualified senators scored yet_"

    # Format superinvestor changes (top 5)
    investor_lines = "\n".join(
        f"• *{i['investor']}* {i['action']} {fmt(i['ticker'])}"
        for i in superinvestor_changes[:5]
    ) or "_No changes this week_"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📈 Weekly Trading Digest"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Total P&L:*\n£{stats.get('total_pnl', 0):+.2f}"},
                {"type": "mrkdwn", "text": f"*Trades:*\n{stats.get('trade_count', 0)}"},
                {"type": "mrkdwn", "text": f"*Win Rate:*\n{stats.get('win_rate', 0):.1f}%"},
                {"type": "mrkdwn", "text": f"*Best Trade:*\n{stats.get('best_trade', '—')}"},
            ]
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Top Qualified Senators:*\n{senator_lines}"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Superinvestor Changes This Week:*\n{investor_lines}"}
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _ts()}]
        },
    ]
    _send("weekly", blocks)


# =============================================================================
# Entry point — send test messages to all four channels
# Usage: python notify.py
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Sending test messages to all Slack channels...")

    trade_opened(
        "NVDA", "BUY", 2.5, 135.40, 123.69, 158.20,
        "US_OPEN", "Options BULLISH | BB breakout | Senate: Tuberville"
    )

    trade_closed("NVDA", "BUY", 135.40, 152.80, 43.50, "TARGET_HIT")

    session_summary(
        "US_OPEN",
        {
            "macro_gate_pass": True,
            "gate_reason":     "All macro conditions normal",
            "vix":             15.32,
            "dxy":             98.91,
            "yield_spread":    0.46
        },
        scanned=12,
        candidates=[
            {"ticker": "NVDA",   "direction": "BUY", "primary_count": 2, "confirmation_count": 3},
            {"ticker": "XAUUSD", "direction": "BUY", "primary_count": 2, "confirmation_count": 2},
        ]
    )

    alert_circuit_breaker("Owner", "TSLA", "Spread too wide: 2.3% of mid price")

    alert_macro_gate_failed("VIX too high: 38.2", 38.2, -0.45, "UK_OPEN")

    weekly_digest(
        stats={
            "total_pnl":   312.50,
            "trade_count": 8,
            "win_rate":    62.5,
            "best_trade":  "NVDA +£143.20"
        },
        top_senators=[
            {"name": "Tommy Tuberville", "score": 0.0412, "win_rate": 0.68, "trade_count": 22}
        ],
        superinvestor_changes=[
            {"investor": "Pershing Square", "action": "NEW", "ticker": "GOOGL"}
        ]
    )

    print("Done — check your Slack channels.")
