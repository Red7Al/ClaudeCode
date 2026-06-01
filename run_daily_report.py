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
        """select ticker, session, options_bias, bb_breakout_dir, bb_squeeze,
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
        bb_squeeze      = r[4]
        pc = 0
        if options_bias in ("BULLISH", "BEARISH"):
            pc += 1
        if bb_breakout_dir in ("BULLISH", "BEARISH"):
            pc += 1
        result.append({
            "ticker": r[0], "session": r[1],
            "options_bias": options_bias, "bb_breakout_dir": bb_breakout_dir,
            "bb_squeeze": bb_squeeze,
            "cot_bias": r[5],
            "primary_count": pc, "confirmation_count": int(r[6] or 0),
            "director_signal": r[7], "senate_signal": r[8],
            "notable_investor": r[9], "social_mention": r[10],
            "trade_triggered": r[11],
        })
    return result


# ---------------------------------------------------------------------------
# Market move detection
# ---------------------------------------------------------------------------

def get_intraday_move_and_volume(tickers: list[str]) -> dict[str, dict]:
    """
    Fetch intraday max move and volume vs 20-day average for each ticker.
    Returns {ticker: {pct_move, volume_ratio, volume_signal}}.
    """
    data = {}
    for ticker in tickers:
        yticker = YAHOO_MAP.get(ticker, ticker)
        try:
            t    = yf.Ticker(yticker)
            hist = t.history(period="1d", interval="1d")
            if hist.empty:
                continue
            row    = hist.iloc[-1]
            open_  = row["Open"]
            if not open_:
                continue
            pct_up   = (row["High"] - open_) / open_
            pct_down = (row["Low"]  - open_) / open_
            pct      = pct_up if abs(pct_up) >= abs(pct_down) else pct_down

            # Volume vs 20-day average
            hist20 = t.history(period="25d", interval="1d")
            vol_ratio = None
            vol_signal = "NORMAL"
            if not hist20.empty and len(hist20) > 1:
                avg_vol = float(hist20["Volume"].iloc[:-1].mean())
                today_vol = float(row["Volume"])
                if avg_vol > 0:
                    vol_ratio = round(today_vol / avg_vol, 2)
                    if vol_ratio >= 1.5:
                        vol_signal = "HIGH"
                    elif vol_ratio < 0.5:
                        vol_signal = "LOW"

            data[ticker] = {
                "pct_move":     round(pct, 4),
                "volume_ratio": vol_ratio,
                "volume_signal": vol_signal,
            }
        except Exception:
            pass
    return data


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

def group_summary(category: str, sample_sig: dict) -> str:
    """
    Produce ONE shared plain-English explanation for a group of instruments
    that all share the same reason for not being traded. Called once per group.
    Also explains how close the signals were to qualifying.
    """
    session = sample_sig.get("session", "US_OPEN scan")
    pc      = sample_sig.get("primary_count", 0)
    cc      = sample_sig.get("confirmation_count", 0)
    options = sample_sig.get("options_bias", "NEUTRAL")
    bb      = sample_sig.get("bb_breakout_dir")
    squeeze = sample_sig.get("bb_squeeze", False)

    options_str = {
        "BULLISH": "options flow was bullish — institutional call buying was above the 1.2× threshold, indicating directional conviction",
        "BEARISH": "options flow was bearish — put buying was dominant, indicating downside conviction",
        "NEUTRAL": "options flow was neutral — call and put volumes were balanced, with no clear institutional directional bias",
    }.get(options, "options flow data was unavailable")

    if bb:
        bb_str = f"price had broken out of a volatility squeeze ({bb.lower()} direction) — this signal fired"
    elif squeeze:
        bb_str = (
            "price was in a Bollinger Band squeeze (volatility compressed, bands at their narrowest) "
            "but the breakout had not yet fired — this is the signal closest to triggering. "
            "A squeeze is the setup; the breakout is the signal. Both must be present."
        )
    else:
        bb_str = (
            "there was no Bollinger Band squeeze or breakout — volatility was not sufficiently compressed "
            "to suggest an imminent directional move"
        )

    if not sample_sig.get("macro_gate_pass", True):
        return f"Not evaluated — macro gate was closed at {session} (VIX or yield curve threshold breached), blocking all instruments."

    if pc == 0:
        proximity = "Neither required signal had fired."
        return (
            f"At {session}: {options_str}, and {bb_str}. "
            f"{proximity} The system requires both to align before placing any trade."
        )

    if pc == 1:
        if options != "NEUTRAL" and not bb:
            fired   = options_str
            missing = bb_str
        else:
            fired   = bb_str
            missing = options_str
        proximity = (
            "One signal short of the required two. "
            + ("The volatility squeeze was in place — the breakout was the only missing piece." if squeeze and not bb
               else "")
        )
        return (
            f"At {session}: {fired}. However, {missing}. "
            f"{proximity} Both primary signals must agree before the system acts."
        )

    if pc >= 2 and cc < 1:
        return (
            f"At {session}: both primary signals were present, but no confirmation signals fired "
            f"(no director cluster buys, senate purchases, superinvestor activity, or COT bias aligned). "
            f"At least one confirmation is required. These instruments were the closest to triggering a trade today."
        )

    return (
        f"At {session}: {pc} primary and {cc} confirmation signal(s) were present, "
        f"but the combined threshold was not met at the scan window. "
        f"The move may have developed after all 15-minute scan windows had closed."
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

    # Build list of (ticker, move_data, reason_category, explanation)
    notable_gaps = []
    for ticker, move_data in market_moves.items():
        pct = move_data["pct_move"]
        if abs(pct) < NOTABLE_MOVE_THRESHOLD or ticker in traded_tickers:
            continue
        if ticker not in all_scanned_tickers:
            continue
        sig_entries = [s for s in signal_log if s["ticker"] == ticker]
        sig = sig_entries[-1] if sig_entries else {}
        pc  = sig.get("primary_count", 0)
        cc  = sig.get("confirmation_count", 0)
        # Assign a reason category for grouping
        if not sig.get("macro_gate_pass", True):
            category = "Macro gate closed"
        elif pc == 0:
            category = "No primary signals at scan time (options neutral + no BB breakout)"
        elif pc == 1:
            if sig.get("options_bias") in ("BULLISH", "BEARISH") and not sig.get("bb_breakout_dir"):
                category = "Options bullish but no BB breakout at scan time"
            elif sig.get("bb_breakout_dir") and sig.get("options_bias") == "NEUTRAL":
                category = "BB breakout but options flow neutral at scan time"
            else:
                category = "One of two primary signals fired at scan time"
        elif pc >= 2 and cc == 0:
            category = "Both primaries fired but no confirmation signals"
        else:
            category = "Signals present but threshold not met at scan time"

        notable_gaps.append((ticker, pct, move_data, category, sig))

    notable_gaps.sort(key=lambda x: abs(x[1]), reverse=True)

    # Group by reason category
    from collections import defaultdict
    grouped = defaultdict(list)
    for ticker, pct, move_data, category, sig in notable_gaps:
        grouped[category].append((ticker, pct, move_data, sig))

    lines.append(f"*Notable Moves Not Traded — {len(notable_gaps)}*")
    if grouped:
        for category, items in grouped.items():
            lines.append("")
            # Instruments first — move size + volume
            for ticker, pct, move_data, sig in items:
                pct_str    = f"+{pct*100:.1f}%" if pct > 0 else f"{pct*100:.1f}%"
                vol_ratio  = move_data.get("volume_ratio")
                vol_signal = move_data.get("volume_signal", "NORMAL")
                if vol_ratio is not None:
                    vol_tag = (
                        f"  vol {vol_ratio:.1f}× avg ⬆" if vol_signal == "HIGH" else
                        f"  vol {vol_ratio:.1f}× avg ⬇" if vol_signal == "LOW" else
                        f"  vol {vol_ratio:.1f}× avg"
                    )
                else:
                    vol_tag = ""
                lines.append(f"  *{ticker}* {pct_str}{vol_tag}")
            # One shared explanation for all instruments in this group
            sample_sig = items[0][3]
            summary = group_summary(category, sample_sig)
            lines.append(f"  _{summary}_")
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
    market_moves    = get_intraday_move_and_volume(all_tickers)
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
