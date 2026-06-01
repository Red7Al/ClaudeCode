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
        """select ticker, direction, open_price, stop_loss, limit_level,
                  size, session, signal_summary, opened_at, deal_id
           from trade_log
           where date(opened_at) = :d
           order by opened_at""",
        d=today_str
    )
    return [
        {
            "ticker": r[0], "direction": r[1], "open_price": r[2],
            "stop_loss": r[3], "limit_level": r[4], "size": r[5],
            "session": r[6], "signal_summary": r[7],
            "opened_at": r[8], "deal_id": r[9],
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
    """Daily P&L per user."""
    rows = db.run(
        """select user_id, total_pnl, trade_count, win_count, loss_count, daily_loss_hit
           from daily_pnl
           where trade_date = :d""",
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
        """select ticker, session, options_bias, call_put_ratio, bb_breakout_dir,
                  cot_bias, pa_verdict, primary_count, confirmation_count,
                  director_signal, senate_signal, notable_investor, social_mention,
                  trade_triggered, direction
           from signal_log
           where date(scanned_at) = :d
           order by session, ticker""",
        d=today_str
    )
    return [
        {
            "ticker": r[0], "session": r[1], "options_bias": r[2],
            "call_put_ratio": float(r[3]) if r[3] else None,
            "bb_breakout_dir": r[4], "cot_bias": r[5], "pa_verdict": r[6],
            "primary_count": int(r[7] or 0), "confirmation_count": int(r[8] or 0),
            "director_signal": r[9], "senate_signal": r[10],
            "notable_investor": r[11], "social_mention": r[12],
            "trade_triggered": r[13], "direction": r[14],
        }
        for r in (rows or [])
    ]


# ---------------------------------------------------------------------------
# Market move detection
# ---------------------------------------------------------------------------

def get_intraday_moves(tickers: list[str]) -> dict[str, float]:
    """
    Fetch intraday open-to-close % move for each ticker via Yahoo Finance.
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
            row   = hist.iloc[-1]
            pct   = (row["Close"] - row["Open"]) / row["Open"]
            moves[ticker] = round(pct, 4)
        except Exception:
            pass
    return moves


# ---------------------------------------------------------------------------
# Signal gap explanation
# ---------------------------------------------------------------------------

def explain_why_not_traded(sig: dict, pct_move: float) -> str:
    """
    Produce a one-sentence explanation of why the signal stack did not trigger
    for an instrument that had a notable price move.
    """
    reasons = []

    if not sig.get("macro_gate_pass", True):
        return "Macro gate was closed at session open — no instruments evaluated."

    pc = sig.get("primary_count", 0)
    cc = sig.get("confirmation_count", 0)
    options = sig.get("options_bias", "NEUTRAL")
    bb      = sig.get("bb_breakout_dir")
    pa      = sig.get("pa_verdict", "WAIT")
    cpr     = sig.get("call_put_ratio")

    if pc < 2:
        missing = []
        if options == "NEUTRAL":
            cpr_str = f"call/put ratio {cpr:.2f}" if cpr else "data unavailable"
            missing.append(f"options flow neutral ({cpr_str}; threshold: >1.2 bull / <0.8 bear)")
        if not bb:
            missing.append("no Bollinger Band breakout from squeeze")
        reasons.append(f"only {pc}/2 primary signals fired — {' and '.join(missing)}")

    if pc >= 2 and cc < 1:
        reasons.append(f"no confirmation signals present (director buys, senate, superinvestor, COT all neutral)")

    if pc >= 2 and cc >= 1 and pa == "WAIT":
        reasons.append("price action verdict was WAIT — no confirmed trend structure or range breakout at session open")

    if not reasons:
        reasons.append(f"signals ({pc} primaries, {cc} confirmations) did not meet the 2+1 threshold at session open")

    direction_word = "up" if pct_move > 0 else "down"
    pct_str = f"+{pct_move*100:.1f}%" if pct_move > 0 else f"{pct_move*100:.1f}%"
    return (
        f"IBM moved {pct_str} {direction_word} intraday. "
        f"At {sig['session']} scan: {'; '.join(reasons)}."
    ).replace("IBM", sig["ticker"])


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


USER_LABELS = {
    "00000000-0000-0000-0000-000000000001": "Owner",
    "00000000-0000-0000-0000-000000000002": "Wife",
    "00000000-0000-0000-0000-000000000003": "Son (paper)",
}


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
            rr = ""
            try:
                ep = float(t["open_price"] or 0)
                sl = float(t["stop_loss"] or 0)
                lm = float(t["limit_level"] or 0)
                if ep and sl and lm:
                    rr = f"  R:R {round(abs(lm-ep)/max(abs(ep-sl),0.0001),1)}:1"
            except Exception:
                pass
            lines.append(
                f"  {_direction_arrow(t['direction'])}  *{t['ticker']}*  "
                f"Entry {t['open_price']}  Stop {t['stop_loss']}  "
                f"Target {t['limit_level']}  Size {t['size']}{rr}  [{t['session']}]"
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
            label    = USER_LABELS.get(u["user_id"], u["user_id"])
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
