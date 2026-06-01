# =============================================================================
# File:         run_daily_report.py
# Author:       Alex Hind
# Created:      2026-06-01
#
# Description:
# -----------------------------------------------------------------------------
# End-of-day executive trading report. Runs at 21:30 UTC Mon–Fri via GitHub
# Actions (after SESSION_CLOSE at 21:00).
#
# Produces one Slack message covering:
#   - Macro environment for the day
#   - Every trade opened (with why signals fired)
#   - Every trade closed (P&L and reason)
#   - Positions held overnight
#   - Notable market moves (>3%) that were NOT traded, with signal gap explained
#   - Daily P&L summary per user
#   - Tomorrow's high-impact economic events
#
# Usage:
#   python run_daily_report.py
#
# Environment Variables Required:
#   SUPABASE_USER, SUPABASE_DB_PASSWORD
#   SLACK_TRADES
# =============================================================================

import os
import logging
import requests
import yfinance as yf
from datetime import datetime, timedelta, timezone

import pg8000.native

from config import SESSION_INSTRUMENTS, YAHOO_MAP

log = logging.getLogger("daily_report")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"
SLACK_URL     = os.environ.get("SLACK_TRADES", "")

NOTABLE_MOVE_THRESHOLD = 0.03   # 3% — flag instruments that moved this much intraday


def get_db():
    return pg8000.native.Connection(
        host=SUPABASE_HOST, port=6543, database="postgres",
        user=os.environ["SUPABASE_USER"],
        password=os.environ["SUPABASE_DB_PASSWORD"],
        ssl_context=True
    )


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def fetch_todays_trades(db, today_str: str) -> list[dict]:
    """All trades opened today, with signal summary."""
    rows = db.run(
        """select ticker, direction, open_price, stop_loss,
                  size, session, signal_summary, opened_at
           from trade_log
           where date(opened_at) = :d
           order by opened_at""",
        d=today_str
    )
    return [
        {
            "ticker": r[0], "direction": r[1], "open_price": r[2],
            "stop_loss": r[3], "size": r[4],
            "session": r[5], "signal_summary": r[6], "opened_at": r[7],
        }
        for r in (rows or [])
    ]


def fetch_todays_closes(db, today_str: str) -> list[dict]:
    """All trades closed today."""
    rows = db.run(
        """select ticker, direction, open_price, close_price, pnl,
                  close_reason, opened_at, closed_at
           from trade_log
           where date(closed_at) = :d
             and close_price is not null
           order by closed_at""",
        d=today_str
    )
    return [
        {
            "ticker": r[0], "direction": r[1], "open_price": r[2],
            "close_price": r[3], "pnl": float(r[4] or 0),
            "close_reason": r[5], "opened_at": r[6], "closed_at": r[7],
        }
        for r in (rows or [])
    ]


def fetch_open_positions(db) -> list[dict]:
    """Currently open positions."""
    rows = db.run(
        """select ticker, direction, open_price, stop_loss, session, opened_at
           from positions
           order by opened_at"""
    )
    return [
        {
            "ticker": r[0], "direction": r[1], "open_price": float(r[2] or 0),
            "stop_loss": r[3], "session": r[4], "opened_at": r[5],
        }
        for r in (rows or [])
    ]


def fetch_daily_pnl(db, today_str: str) -> list[dict]:
    """Daily P&L per user, joined to user_profiles for the name."""
    rows = db.run(
        """select coalesce(up.name, dp.user_id::text),
                  dp.total_pnl, dp.trade_count, dp.win_count, dp.loss_count, dp.daily_loss_hit
           from daily_pnl dp
           left join user_profiles up on up.id = dp.user_id
           where dp.trade_date = :d""",
        d=today_str
    )
    return [
        {
            "user_id": r[0], "total_pnl": float(r[1] or 0),
            "trade_count": int(r[2] or 0), "win_count": int(r[3] or 0),
            "loss_count": int(r[4] or 0), "daily_loss_hit": r[5],
        }
        for r in (rows or [])
    ]


def fetch_macro_snapshot(db, today_str: str) -> dict:
    """Most recent macro snapshot for today."""
    rows = db.run(
        """select vix, dxy, yield_spread, macro_gate_pass, gate_reason, session
           from macro_snapshot
           where date(snapshot_time) = :d
           order by snapshot_time desc limit 1""",
        d=today_str
    )
    if rows:
        return {
            "vix": float(rows[0][0] or 0), "dxy": float(rows[0][1] or 0),
            "yield_spread": float(rows[0][2] or 0), "macro_gate_pass": rows[0][3],
            "gate_reason": rows[0][4], "session": rows[0][5],
        }
    return {}


def fetch_signal_log(db, today_str: str) -> list[dict]:
    """All instruments scanned today — including those that did NOT trigger a trade."""
    rows = db.run(
        """select ticker, session, options_bias, bb_breakout_dir,
                  cot_bias, confirmation_count,
                  director_signal, senate_signal, notable_investor, social_mention,
                  trade_triggered
           from signal_log
           where date(session_time) = :d
           order by session, ticker""",
        d=today_str
    )
    result = []
    for r in (rows or []):
        options_bias    = r[2]
        bb_breakout_dir = r[3]
        # Derive primary count from available columns (primary_count not yet in DB)
        pc = 0
        if options_bias in ("BULLISH", "BEARISH"):
            pc += 1
        if bb_breakout_dir in ("BULLISH", "BEARISH"):
            pc += 1
        result.append({
            "ticker": r[0], "session": r[1],
            "options_bias": options_bias, "bb_breakout_dir": bb_breakout_dir,
            "cot_bias": r[4],
            "primary_count": pc, "confirmation_count": int(r[5] or 0),
            "director_signal": r[6], "senate_signal": r[7],
            "notable_investor": r[8], "social_mention": r[9],
            "trade_triggered": r[10],
        })
    return result


# ---------------------------------------------------------------------------
# Market move detection
# ---------------------------------------------------------------------------

def get_intraday_moves(tickers: list[str]) -> dict[str, float]:
    """
    Fetch the maximum intraday move from open for each ticker via Yahoo Finance.
    Uses High vs Open (up move) or Low vs Open (down move), whichever is larger.
    This catches instruments that moved significantly intraday but gave back gains
    before close — e.g. IBM up 10% intraday that closed up only 2%.
    Returns {ticker: pct_move} — positive = up, negative = down.
    """
    moves = {}
    for ticker in tickers:
        yticker = YAHOO_MAP.get(ticker, ticker)
        try:
            t    = yf.Ticker(yticker)
            hist = t.history(period="1d", interval="1d")
            if hist.empty:
                continue
            row      = hist.iloc[-1]
            open_    = row["Open"]
            if not open_:
                continue
            pct_up   = (row["High"]  - open_) / open_   # best upside intraday
            pct_down = (row["Low"]   - open_) / open_   # worst downside intraday
            # Use whichever was the bigger move (by absolute value)
            pct = pct_up if abs(pct_up) >= abs(pct_down) else pct_down
            moves[ticker] = round(pct, 4)
        except Exception:
            pass
    return moves


# ---------------------------------------------------------------------------
# Signal gap explanation
# ---------------------------------------------------------------------------

def explain_why_not_traded(sig: dict, pct_move: float) -> str:
    """Plain-English explanation of why a notable mover was not traded."""
    ticker  = sig["ticker"]
    session = sig.get("session", "session open")
    pc      = sig.get("primary_count", 0)
    cc      = sig.get("confirmation_count", 0)
    options = sig.get("options_bias", "NEUTRAL")
    bb      = sig.get("bb_breakout_dir")

    direction_word = "up" if pct_move > 0 else "down"
    pct_str = f"+{pct_move*100:.1f}%" if pct_move > 0 else f"{pct_move*100:.1f}%"
    move_clause = f"{ticker} moved {pct_str} {direction_word} today."

    # Gate failure
    if not sig.get("macro_gate_pass", True):
        return f"{move_clause} Not evaluated — macro gate was closed at {session} (VIX or yield curve breached threshold), blocking all trades."

    # Build plain-English signal breakdown
    options_str = {
        "BULLISH": "options flow was bullish (heavy call buying)",
        "BEARISH": "options flow was bearish (heavy put buying)",
        "NEUTRAL": "options flow was neutral — no clear institutional directional conviction in the options market",
    }.get(options, "options flow data was unavailable")

    bb_str = (
        f"price broke out of a volatility squeeze ({bb.lower()} direction)"
        if bb else
        "price showed no Bollinger Band breakout — volatility was compressed but had not yet expanded into a directional move"
    )

    if pc < 2:
        # The most common case — explain what was seen and what was missing
        if pc == 0:
            why = f"At {session} scan, neither required primary signal had fired: {options_str}, and {bb_str}. The system requires both to align before considering a trade."
        else:  # pc == 1
            fired   = options_str if options != "NEUTRAL" else bb_str
            missing = bb_str if options != "NEUTRAL" else options_str
            why = f"At {session} scan, one of two required primary signals was present ({fired}), but the second had not fired ({missing}). Both must agree before the system acts."
        return f"{move_clause} {why}"

    if cc < 1:
        return (
            f"{move_clause} Both primary signals aligned at {session} scan, but no confirmation signals were present "
            f"(no director cluster buys, senate trades, superinvestor positions, or COT bias). "
            f"At least one confirmation is required to size and place a trade."
        )

    return (
        f"{move_clause} Signals at {session} scan: {pc} primaries and {cc} confirmations present, "
        f"but the combined score did not meet the entry threshold at that moment. "
        f"The move may have developed after the scan window closed."
    )


# ---------------------------------------------------------------------------
# Calendar — tomorrow's events
# ---------------------------------------------------------------------------

def get_tomorrows_events() -> list[dict]:
    """Fetch tomorrow's high-impact ForexFactory events."""
    events = []
    try:
        resp = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=10
        )
        if resp.status_code != 200:
            return events
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        for ev in resp.json():
            if ev.get("impact", "").upper() != "HIGH":
                continue
            if ev.get("date", "") == tomorrow:
                events.append({
                    "title":    ev.get("title", ""),
                    "time":     ev.get("time", ""),
                    "currency": ev.get("country", ""),
                })
    except Exception as e:
        log.warning(f"Calendar fetch failed: {e}")
    return events


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

def _pnl_str(pnl: float) -> str:
    return f"+£{pnl:.2f}" if pnl >= 0 else f"-£{abs(pnl):.2f}"


def _direction_arrow(direction: str) -> str:
    return "▲ LONG" if direction == "BUY" else "▼ SHORT"


def _hold_duration(opened_at) -> str:
    if not opened_at:
        return ""
    try:
        if isinstance(opened_at, str):
            opened_at = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - opened_at.replace(tzinfo=timezone.utc)
        h = int(delta.total_seconds() // 3600)
        m = int((delta.total_seconds() % 3600) // 60)
        return f"{h}h {m}m" if h else f"{m}m"
    except Exception:
        return ""


USER_LABELS = {}  # name comes directly from user_profiles.name via join


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def build_report(
    today_str: str,
    macro: dict,
    trades_opened: list,
    trades_closed: list,
    open_positions: list,
    daily_pnl: list,
    signal_log: list,
    market_moves: dict,
    tomorrow_events: list,
) -> str:

    lines = []

    # ── Header ───────────────────────────────────────────────────────────────
    lines.append(f"*EndToEndTrading — Daily Report  {today_str}*")
    lines.append("─" * 48)

    # ── Macro ─────────────────────────────────────────────────────────────────
    gate_word = "OPEN" if macro.get("macro_gate_pass") else "CLOSED"
    lines.append(f"*Macro Environment*")
    lines.append(
        f"VIX {macro.get('vix','—')}  |  DXY {macro.get('dxy','—')}  |  "
        f"Yield spread {macro.get('yield_spread','—')}% (2Y–10Y)  |  Gate: {gate_word}"
    )
    if not macro.get("macro_gate_pass"):
        lines.append(f"⚠ Gate closed: {macro.get('gate_reason','')}")
    lines.append("")

    # ── Trades Opened ─────────────────────────────────────────────────────────
    lines.append(f"*Trades Opened Today — {len(trades_opened)}*")
    if trades_opened:
        for t in trades_opened:
            lines.append(
                f"  {_direction_arrow(t['direction'])}  *{t['ticker']}*  "
                f"Entry {t['open_price']}  Stop {t['stop_loss']}  "
                f"Size {t['size']}  [{t['session']}]"
            )
            if t.get("signal_summary"):
                lines.append(f"  _Signals: {t['signal_summary']}_")
    else:
        lines.append("  No trades opened today.")
    lines.append("")

    # ── Trades Closed ─────────────────────────────────────────────────────────
    lines.append(f"*Trades Closed Today — {len(trades_closed)}*")
    if trades_closed:
        for t in trades_closed:
            pnl_str   = _pnl_str(t["pnl"])
            reason_map = {
                "STOP_HIT":               "Stop hit",
                "TARGET_HIT":             "Target hit",
                "SESSION_CLOSE_OIL":      "Session close (oil, always intraday)",
                "SESSION_CLOSE_PROFIT_LOCK": "Profit lock at 1.5× risk",
                "SESSION_CLOSE":          "Session close",
                "MANUAL":                 "Manual close",
                "CIRCUIT_BREAKER":        "Circuit breaker",
            }
            reason_str = reason_map.get(t["close_reason"], t["close_reason"])
            lines.append(
                f"  {_direction_arrow(t['direction'])}  *{t['ticker']}*  "
                f"{t['open_price']} → {t['close_price']}  *{pnl_str}*  — {reason_str}"
            )
    else:
        lines.append("  No trades closed today.")
    lines.append("")

    # ── Open Positions (Overnight) ────────────────────────────────────────────
    lines.append(f"*Positions Held Overnight — {len(open_positions)}*")
    if open_positions:
        for p in open_positions:
            current = market_moves.get(p["ticker"])
            unreal  = ""
            if current is not None:
                move_str = f"+{current*100:.1f}%" if current > 0 else f"{current*100:.1f}%"
                unreal   = f"  (today's move: {move_str})"
            lines.append(
                f"  {_direction_arrow(p['direction'])}  *{p['ticker']}*  "
                f"Entry {p['open_price']}  Stop {p['stop_loss']}"
                f"  Held {_hold_duration(p['opened_at'])}{unreal}"
            )
    else:
        lines.append("  No open positions. Fully flat overnight.")
    lines.append("")

    # ── Notable Moves NOT Traded ──────────────────────────────────────────────
    all_scanned_tickers = {s["ticker"] for s in signal_log}
    traded_tickers      = {t["ticker"] for t in trades_opened}

    notable_gaps = []
    for ticker, pct in market_moves.items():
        if abs(pct) >= NOTABLE_MOVE_THRESHOLD and ticker in all_scanned_tickers and ticker not in traded_tickers:
            sig_entries = [s for s in signal_log if s["ticker"] == ticker]
            sig = sig_entries[-1] if sig_entries else {}
            explanation = explain_why_not_traded(sig, pct) if sig else (
                f"{ticker} moved {pct*100:+.1f}% but was not in today's scan list."
            )
            notable_gaps.append((ticker, pct, explanation))

    # Also flag instruments in the scan list that moved significantly even if not in YAHOO_MAP moves
    notable_gaps.sort(key=lambda x: abs(x[1]), reverse=True)

    lines.append(f"*Notable Moves Not Traded — {len(notable_gaps)}*")
    if notable_gaps:
        for ticker, pct, explanation in notable_gaps:
            pct_str = f"+{pct*100:.1f}%" if pct > 0 else f"{pct*100:.1f}%"
            lines.append(f"  *{ticker}* {pct_str}  —  {explanation}")
    else:
        lines.append("  No scanned instruments had notable moves outside traded positions.")
    lines.append("")

    # ── Daily P&L ─────────────────────────────────────────────────────────────
    lines.append("*Daily P&L*")
    if daily_pnl:
        total = sum(u["total_pnl"] for u in daily_pnl)
        for u in daily_pnl:
            label    = u["user_id"]  # name comes from user_profiles join
            loss_flag = "  ⚠ LIMIT HIT" if u.get("daily_loss_hit") else ""
            win_rate  = (
                f"{u['win_count']}/{u['trade_count']} wins"
                if u["trade_count"] > 0 else "no trades"
            )
            lines.append(
                f"  {label}: *{_pnl_str(u['total_pnl'])}*  "
                f"({win_rate}){loss_flag}"
            )
        lines.append(f"  *Day total: {_pnl_str(total)}*")
    else:
        lines.append("  P&L data not yet available.")
    lines.append("")

    # ── Tomorrow's Calendar ───────────────────────────────────────────────────
    lines.append("*Tomorrow — High Impact Events*")
    if tomorrow_events:
        for ev in tomorrow_events:
            lines.append(f"  {ev['time']} UTC  {ev['currency']}  {ev['title']}")
        # Flag exposure from open positions
        if open_positions:
            exposed = [p["ticker"] for p in open_positions]
            lines.append(f"  _Overnight positions exposed: {', '.join(exposed)}_")
    else:
        lines.append("  No high-impact events scheduled.")
    lines.append("")
    lines.append(f"_Generated {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}_")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info(f"Generating daily report for {today_str}")

    db = get_db()
    try:
        macro          = fetch_macro_snapshot(db, today_str)
        trades_opened  = fetch_todays_trades(db, today_str)
        trades_closed  = fetch_todays_closes(db, today_str)
        open_positions = fetch_open_positions(db)
        daily_pnl      = fetch_daily_pnl(db, today_str)
        signal_log     = fetch_signal_log(db, today_str)
    finally:
        db.close()

    # All instruments scanned today + open positions → get price moves
    all_tickers = list({
        s["ticker"] for s in signal_log
    } | {
        p["ticker"] for p in open_positions
    })
    market_moves    = get_intraday_moves(all_tickers)
    tomorrow_events = get_tomorrows_events()

    report = build_report(
        today_str, macro, trades_opened, trades_closed,
        open_positions, daily_pnl, signal_log, market_moves, tomorrow_events
    )

    log.info("Report built. Posting to Slack...")

    if SLACK_URL:
        resp = requests.post(
            SLACK_URL,
            json={"text": report},
            timeout=10
        )
        if resp.status_code == 200:
            log.info("Report posted to Slack.")
        else:
            log.error(f"Slack post failed: {resp.status_code} {resp.text}")
    else:
        # Fallback — print to stdout (visible in GitHub Actions logs)
        print(report)


if __name__ == "__main__":
    main()
