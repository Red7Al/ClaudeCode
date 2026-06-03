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
#
# All webhook URLs are loaded from environment variables.
# Messages use Slack Block Kit for structured, readable formatting.
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-05-30  Alex Hind   Initial build. Four channels, Block Kit
#                                 formatting, test harness included.
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
# =============================================================================

import os
from dotenv import load_dotenv; load_dotenv(override=True)
import logging
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
    "trades":  os.environ.get("SLACK_TRADES",  ""),
    "signals": os.environ.get("SLACK_SIGNALS", ""),
    "alerts":  os.environ.get("SLACK_ALERTS",  ""),
    "weekly":  os.environ.get("SLACK_WEEKLY",  ""),
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


def _instrument_name(ticker: str) -> str:
    """
    Look up the human-readable instrument name from Supabase epic_lookup.
    Returns the description if found, or just the ticker if not.
    e.g. 'IBM' -> 'IBM Corp (24 Hours)'
         'NBIS' -> 'Nebius Group NV (24 Hours)'
    """
    try:
        conn = pg8000.native.Connection(
            host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
            database="postgres",
            user=os.environ["SUPABASE_USER"],
            password=os.environ["SUPABASE_DB_PASSWORD"],
            ssl_context=True
        )
        rows = conn.run(
            "select description from epic_lookup where ticker = :t limit 1",
            t=ticker
        )
        conn.close()
        if rows and rows[0][0]:
            return rows[0][0]
    except Exception:
        pass
    return ticker


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
    user:           str = "Owner"
):
    """Send a trade opened notification to #claude-trading-trades."""
    emoji      = "🟢" if direction == "BUY" else "🔴"
    rr         = round(abs(target - entry) / max(abs(entry - stop), 0.0001), 2)
    inst_name  = _instrument_name(ticker)
    title      = f"{ticker} — {inst_name}" if inst_name != ticker else ticker

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} Trade Opened — {title}"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*User:*\n{user}"},
                {"type": "mrkdwn", "text": f"*Session:*\n{session}"},
                {"type": "mrkdwn", "text": f"*Direction:*\n{direction}"},
                {"type": "mrkdwn", "text": f"*Size:*\n{size}"},
                {"type": "mrkdwn", "text": f"*Entry:*\n{entry}"},
                {"type": "mrkdwn", "text": f"*Stop:*\n{stop}"},
                {"type": "mrkdwn", "text": f"*Target:*\n{target}"},
                {"type": "mrkdwn", "text": f"*R:R:*\n{rr}:1"},
            ]
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
    user:         str = "Owner"
):
    """Send a trade closed notification to #claude-trading-trades."""

    pnl_emoji  = "✅" if pnl >= 0 else "❌"
    inst_name  = _instrument_name(ticker)
    title      = f"{ticker} — {inst_name}" if inst_name != ticker else ticker

    # Human-readable labels for each close reason code
    reason_labels = {
        "STOP_HIT":        "🛑 Stop Loss Hit",
        "TARGET_HIT":      "🎯 Take Profit Hit",
        "MANUAL":          "👤 Manual Close",
        "CIRCUIT_BREAKER": "⚡ Circuit Breaker",
        "SYSTEM":          "⚙️ System Close",
        "UNKNOWN":         "❓ Unknown",
    }
    reason_label = reason_labels.get(close_reason, close_reason)

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{pnl_emoji} Trade Closed — {title}"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*User:*\n{user}"},
                {"type": "mrkdwn", "text": f"*Direction:*\n{direction}"},
                {"type": "mrkdwn", "text": f"*Entry:*\n{entry}"},
                {"type": "mrkdwn", "text": f"*Close:*\n{close}"},
                {"type": "mrkdwn", "text": f"*P&L:*\n£{pnl:+.2f}"},
                {"type": "mrkdwn", "text": f"*Reason:*\n{reason_label}"},
            ]
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _ts()}]
        },
    ]
    _send("trades", blocks)


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
            dir_emoji = "🟢" if c.get("direction") == "BUY" else "🔴"
            candidate_lines += (
                f"{dir_emoji} *{c['ticker']}* {c['direction']} — "
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
    inst_name = _instrument_name(ticker)
    title     = f"{ticker} — {inst_name}" if inst_name != ticker else ticker
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
                {"type": "mrkdwn", "text": f"*Instrument:*\n{ticker}"},
                {"type": "mrkdwn", "text": f"*Reason:*\n{reason}"},
            ]
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _ts()}]
        },
    ]
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
        f"• *{i['investor']}* {i['action']} {i['ticker']}"
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
