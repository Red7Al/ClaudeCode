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
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-06-01  Alex Hind   Initial build. Full end-of-day report covering
#                                 macro, trades opened/closed, overnight positions,
#                                 notable missed moves, daily P&L, and tomorrow's
#                                 economic calendar.
# 1.0.2   2026-06-06  Alex Hind   Fix 3 bugs: (a) market_moves.get(ticker) returns
#                                 a dict, not a float — was multiplied by 100,
#                                 crashing with TypeError on any day with overnight
#                                 positions; (b) fetch_company_names: sanitise ticker
#                                 symbols before SQL f-string interpolation;
#                                 (c) group_summary vol_signal: was comparing "HIGH"/"LOW"
#                                 but signal_log stores "HIGH_VOLUME"/"LOW_VOLUME" — fixed
#                                 to show correct narrative.
# 1.0.1   2026-06-05  Alex Hind   SLACK_URL confirmed as SLACK_DAILY — posts to
#                                 #claude-trading-daily, the correct channel for
#                                 end-of-day executive reports.
# =============================================================================

import os
from dotenv import load_dotenv; load_dotenv(override=True)
import logging
import requests
import yfinance as yf
from datetime import datetime, timedelta, timezone

import pg8000.native

from config import SESSION_INSTRUMENTS, YAHOO_MAP

log = logging.getLogger("daily_report")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"
SLACK_URL     = os.environ.get("SLACK_DAILY", "")    # end-of-day executive report → #claude-trading-daily

NOTABLE_MOVE_THRESHOLD = 0.03   # 3% — flag instruments that moved this much intraday


def get_db():
    # Port 5432 = Supabase SESSION pooler (dedicated backend per connection). The
    # daily report runs MANY parameterised queries on one connection (macro, closes,
    # signal_log[45 params], positions, daily_pnl). On the 6543 transaction pooler
    # pg8000's unnamed prepared statements collide across them — crashed 2026-06-09
    # with 08P01 "bind message supplies 1 parameters, but prepared statement requires
    # 45" (the signal_log plan colliding with a 1-param query). Session pooler isolates.
    return pg8000.native.Connection(
        host=SUPABASE_HOST, port=5432, database="postgres",
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
    """Most recent macro snapshot for today, plus yesterday's for direction."""
    rows = db.run(
        """select vix, dxy, yield_spread, macro_gate_pass, gate_reason, session
           from macro_snapshot
           where date(snapshot_time) = :d
           order by snapshot_time desc limit 1""",
        d=today_str
    )
    if not rows:
        return {}
    result = {
        "vix": float(rows[0][0] or 0), "dxy": float(rows[0][1] or 0),
        "yield_spread": float(rows[0][2] or 0), "macro_gate_pass": rows[0][3],
        "gate_reason": rows[0][4], "session": rows[0][5],
        "prev_vix": None, "prev_dxy": None, "prev_spread": None,
    }
    # Yesterday's snapshot for direction arrows
    from datetime import datetime as _dt, timedelta as _td
    yesterday = (_dt.strptime(today_str, "%Y-%m-%d") - _td(days=1)).strftime("%Y-%m-%d")
    prev = db.run(
        """select vix, dxy, yield_spread from macro_snapshot
           where date(snapshot_time) = :d
           order by snapshot_time desc limit 1""",
        d=yesterday
    )
    if prev:
        result["prev_vix"]    = float(prev[0][0] or 0)
        result["prev_dxy"]    = float(prev[0][1] or 0)
        result["prev_spread"] = float(prev[0][2] or 0)
    return result


def fetch_signal_log(db, today_str: str) -> list[dict]:
    """All instruments scanned today — including those that did NOT trigger a trade."""
    rows = db.run(
        """select ticker, session, options_bias, bb_breakout_dir, bb_squeeze,
                  cot_bias, confirmation_count,
                  director_signal, senate_signal, notable_investor, social_mention,
                  trade_triggered, adx_signal, obv_signal, volume_signal, volume_ratio,
                  primary_count, direction, macro_gate_pass
           from signal_log
           where date(session_time) = :d
           order by session, ticker""",
        d=today_str
    )
    result = []
    for r in (rows or []):
        result.append({
            "ticker":             r[0],
            "session":            r[1],
            "options_bias":       r[2],
            "bb_breakout_dir":    r[3],
            "bb_squeeze":         r[4],
            "cot_bias":           r[5],
            "confirmation_count": int(r[6] or 0),
            "director_signal":    r[7],
            "senate_signal":      r[8],
            "notable_investor":   r[9],
            "social_mention":     r[10],
            "trade_triggered":    r[11],
            "adx_signal":         r[12],
            "obv_signal":         r[13],
            "volume_signal":      r[14],
            "volume_ratio":       float(r[15]) if r[15] else None,
            "primary_count":      int(r[16] or 0),   # stored value, not recalculated
            "direction":          r[17],
            "macro_gate_pass":    r[18],
        })
    return result


# ---------------------------------------------------------------------------
# Market move detection
# ---------------------------------------------------------------------------

def fetch_company_names(db, tickers: list[str]) -> dict[str, str]:
    """
    Batch-fetch company names from epic_lookup for a list of tickers.
    Returns {ticker: company_name}. Strips IG suffix e.g. '(24 Hours)'.
    """
    if not tickers:
        return {}
    import re
    # Reject any ticker with chars outside the safe set before SQL interpolation.
    # Tickers in this system are alphanumeric plus dots, hyphens, equals and ^.
    safe_tickers = [t for t in set(tickers) if re.match(r'^[\w.\-=^]+$', t)]
    if not safe_tickers:
        return {}
    placeholders = ", ".join(f"'{t}'" for t in safe_tickers)
    try:
        rows = db.run(
            f"select ticker, description from epic_lookup where ticker in ({placeholders})"
        )
        result = {}
        for ticker, desc in (rows or []):
            if desc:
                name = re.sub(r'\s*\(.*?(Hours|Daily|DFB|Spot).*?\)\s*$', '', desc,
                              flags=re.IGNORECASE).strip()
                if name and name != ticker:
                    result[ticker] = name
        return result
    except Exception:
        return {}


def get_intraday_move_and_volume(tickers: list[str]) -> dict[str, dict]:
    """
    Fetch today's day return (vs previous close) and volume vs 20-day average.
    Uses previous close as base — correctly handles gap-up/gap-down opens.
    e.g. IBM opens +8% on news, drifts to +7.5% — correctly shows +7.5% not -0.5%.
    Returns {ticker: {pct_move, volume_ratio, volume_signal}}.
    """
    data = {}
    for ticker in tickers:
        yticker = YAHOO_MAP.get(ticker, ticker)
        try:
            t    = yf.Ticker(yticker)
            # Fetch 2 days so we have yesterday's close as the base
            hist = t.history(period="5d", interval="1d")
            if len(hist) < 2:
                continue

            prev_close  = float(hist["Close"].iloc[-2])
            today_close = float(hist["Close"].iloc[-1])
            today_vol   = float(hist["Volume"].iloc[-1])

            if not prev_close:
                continue

            pct = (today_close - prev_close) / prev_close

            # Volume vs 20-day average (excluding today)
            avg_vol    = float(hist["Volume"].iloc[:-1].mean())
            vol_ratio  = None
            vol_signal = "NORMAL"
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
    """Thin wrapper — returns just pct_move from get_intraday_move_and_volume."""
    return {k: v["pct_move"] for k, v in get_intraday_move_and_volume(tickers).items()}


# ---------------------------------------------------------------------------
# Signal gap explanation
# ---------------------------------------------------------------------------

def group_summary(category: str, sample_sig: dict) -> str:
    """
    Produce ONE shared plain-English explanation for a group of instruments.
    Includes ADX, OBV, and volume context where available.
    """
    session = sample_sig.get("session", "US_OPEN scan")
    pc      = sample_sig.get("primary_count", 0)
    cc      = sample_sig.get("confirmation_count", 0)
    options = sample_sig.get("options_bias", "NEUTRAL")
    bb      = sample_sig.get("bb_breakout_dir")
    squeeze = sample_sig.get("bb_squeeze", False)
    adx     = sample_sig.get("adx_signal")
    obv     = sample_sig.get("obv_signal")
    vol     = sample_sig.get("volume_signal")

    options_str = {
        "BULLISH": "options flow was bullish — institutional call buying above the 1.2× threshold",
        "BEARISH": "options flow was bearish — put buying dominant",
        "NEUTRAL": "options flow was neutral — no clear institutional directional bias",
    }.get(options, "options flow unavailable")

    if bb:
        bb_str = f"price broke out of its volatility squeeze ({bb.lower()} direction)"
    elif squeeze:
        bb_str = (
            "price was in a Bollinger Band squeeze (compressed volatility) "
            "but the breakout had not yet fired — the setup was there, the signal was not"
        )
    else:
        bb_str = "no Bollinger Band squeeze or breakout — no imminent directional move detected"

    vol_str = ""
    # signal_log.volume_signal stores "HIGH_VOLUME" / "LOW_VOLUME" (from get_volume_signal)
    if vol in ("HIGH_VOLUME", "HIGH"):
        vol_str = " Volume was above average (institutional conviction present)."
    elif vol in ("LOW_VOLUME", "LOW"):
        vol_str = " Volume was below average — weak conviction behind the move."

    adx_str = ""
    if adx == "STRONG_TREND":
        adx_str = " ADX confirmed a strong trend was in place."
    elif adx == "WEAK_TREND":
        adx_str = " ADX indicated a weak/ranging market — lower confidence in breakout signals."

    obv_str = ""
    if obv == "BULLISH_DIVERGENCE":
        obv_str = " OBV was diverging bullishly (volume accumulation ahead of price) — a leading signal."
    elif obv == "CONFIRMING_BULLISH":
        obv_str = " OBV confirmed the upward move with rising volume participation."
    elif obv == "BEARISH_DIVERGENCE":
        obv_str = " OBV was diverging bearishly (distribution ahead of price decline)."

    if not sample_sig.get("macro_gate_pass", True):
        return f"Not evaluated — macro gate was closed at {session} (VIX or yield curve threshold breached), blocking all instruments."

    if pc == 0:
        return (
            f"At {session}: {options_str}, and {bb_str}. "
            f"Neither of the two required primary signals had fired.{vol_str}{adx_str}{obv_str} "
            f"The system requires both to align before placing any trade."
        )

    if pc == 1:
        if options != "NEUTRAL" and not bb:
            fired, missing = options_str, bb_str
        else:
            fired, missing = bb_str, options_str
        proximity = (
            " The volatility squeeze was in place — the breakout was the only missing piece."
            if squeeze and not bb else ""
        )
        return (
            f"At {session}: {fired}. However, {missing}.{proximity}{vol_str}{adx_str}{obv_str} "
            f"One signal short of the required two — both must agree before the system acts."
        )

    if pc >= 2 and cc < 1:
        return (
            f"At {session}: both primary signals were present.{vol_str}{adx_str}{obv_str} "
            f"However, no confirmation signals fired (no director buys, senate trades, "
            f"superinvestor activity, COT bias, strong ADX, or OBV confirmation). "
            f"At least one confirmation is required. These were the closest to triggering a trade today."
        )

    return (
        f"At {session}: {pc} primary and {cc} confirmation signal(s) present.{vol_str}{adx_str}{obv_str} "
        f"The combined threshold was not met at the scan window. "
        f"The move may have developed after all 15-minute rescan windows closed."
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
    company_names: dict = None,
) -> str:
    company_names = company_names or {}

    def _display(ticker: str) -> str:
        """Return 'MRVL' — company name goes at end of line separately via _name()."""
        return ticker

    def _name(ticker: str) -> str:
        """Return '  _(Marvell Technology)_' or '' if name unknown."""
        name = company_names.get(ticker, "")
        return f"  _({name})_" if name else ""

    lines = []

    # ── Header ───────────────────────────────────────────────────────────────
    lines.append(f"*EndToEndTrading — Daily Report  {today_str}*")
    lines.append("─" * 48)

    # ── Macro ─────────────────────────────────────────────────────────────────
    def _arrow(current, prev):
        if prev is None or current is None: return ""
        diff = current - prev
        if abs(diff) < 0.01: return " →"
        return f" ▲{abs(diff):.2f}" if diff > 0 else f" ▼{abs(diff):.2f}"

    vix    = macro.get("vix", 0)
    dxy    = macro.get("dxy", 0)
    spread = macro.get("yield_spread", 0)

    # VIX label
    if vix < 15:
        vix_label = "very calm"
    elif vix < 20:
        vix_label = "calm"
    elif vix < 25:
        vix_label = "slightly elevated"
    elif vix < 35:
        vix_label = "elevated — caution"
    else:
        vix_label = "CRISIS level — gate closed"

    # DXY label
    dxy_label = (
        "strong dollar — headwind for gold and commodities" if dxy > 103 else
        "weak dollar — tailwind for gold and commodities"   if dxy < 100 else
        "neutral dollar"
    )

    # Yield spread label
    if spread >= 0.5:
        spread_label = "upward sloping — normal, growth expected"
    elif spread >= 0:
        spread_label = "flat — slowing growth signal"
    elif spread >= -0.5:
        spread_label = "mildly inverted — mild recession risk"
    else:
        spread_label = "deeply inverted — recession warning"

    # Risk regime summary
    if vix < 20 and spread >= 0 and dxy < 103:
        regime = "RISK-ON — low volatility, normal curve, neutral/weak dollar. Favours equities, commodities, growth."
    elif vix > 25 or spread < -0.5:
        regime = "RISK-OFF — elevated volatility or inverted curve. Favours gold, defensives. Reduce size."
    else:
        regime = "MIXED — some risk-on, some caution signals. Normal position sizing."

    gate_word = "✅ OPEN" if macro.get("macro_gate_pass") else "🚫 CLOSED"

    lines.append("*Macro Environment*")
    lines.append(
        f"  VIX {vix}{_arrow(vix, macro.get('prev_vix'))}  —  {vix_label}  _(gate closes above 35)_"
    )
    lines.append(
        f"  DXY {dxy}{_arrow(dxy, macro.get('prev_dxy'))}  —  {dxy_label}"
    )
    lines.append(
        f"  Yield spread {spread:+.2f}%{_arrow(spread, macro.get('prev_spread'))} (10Y–2Y)  —  {spread_label}  _(gate closes below -1.0%)_"
    )
    lines.append(f"  Gate: {gate_word}")
    lines.append(f"  _Regime: {regime}_")
    if not macro.get("macro_gate_pass"):
        lines.append(f"  ⚠ *Reason: {macro.get('gate_reason','')}*")
    lines.append("")

    # ── Trades Opened ─────────────────────────────────────────────────────────
    lines.append(f"*Trades Opened Today — {len(trades_opened)}*")
    if trades_opened:
        for t in trades_opened:
            lines.append(
                f"  {_direction_arrow(t['direction'])}  *{t['ticker']}*  "
                f"Entry {t['open_price']}  Stop {t['stop_loss']}  "
                f"Size {t['size']}  [{t['session']}]{_name(t['ticker'])}"
            )
            if t.get("signal_summary"):
                lines.append(f"  _Trigger: {t['signal_summary']}_")
    else:
        # Explain WHY no trades opened (user directive — "if none, why").
        lines.append("  No trades opened today.")
        if not macro.get("macro_gate_pass", True):
            lines.append(f"  ↳ Why: macro gate was CLOSED ({macro.get('gate_reason','—')}) "
                         f"— no new positions permitted all day.")
        elif signal_log:
            triggered = [s for s in signal_log if s.get("trade_triggered")]
            near = sorted(signal_log,
                          key=lambda s: (s.get("primary_count", 0), s.get("confirmation_count", 0)),
                          reverse=True)
            top = near[0] if near else None
            if triggered:
                lines.append(f"  ↳ Why: {len(triggered)} signal(s) triggered but the trade was "
                             f"blocked downstream (circuit breaker / spread / market hours / size) "
                             f"— see #alerts.")
            else:
                lines.append(f"  ↳ Why: {len(signal_log)} instrument(s) scanned, none met the entry "
                             f"bar (macro gate + ≥1 primary + ≥1 confirmation).")
            if top:
                lines.append(f"  ↳ Closest: *{top['ticker']}*{_name(top['ticker'])} — "
                             f"{top.get('primary_count', 0)} primary, "
                             f"{top.get('confirmation_count', 0)} confirmation(s).")
        else:
            lines.append("  ↳ Why: no instruments were scanned today — the session may not have "
                         "run (check #alerts / Session Watchdog).")
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
                f"{t['open_price']} → {t['close_price']}  *{pnl_str}*  — {reason_str}{_name(t['ticker'])}"
            )
    else:
        lines.append("  No trades closed today.")
    lines.append("")

    # ── Open Positions (Overnight) ────────────────────────────────────────────
    lines.append(f"*Positions Held Overnight — {len(open_positions)}*")
    if open_positions:
        for p in open_positions:
            # market_moves values are dicts {pct_move, volume_ratio, volume_signal}
            current = (market_moves.get(p["ticker"]) or {}).get("pct_move")
            unreal  = ""
            if current is not None:
                move_str = f"+{current*100:.1f}%" if current > 0 else f"{current*100:.1f}%"
                unreal   = f"  (today's move: {move_str})"
            lines.append(
                f"  {_direction_arrow(p['direction'])}  *{p['ticker']}*  "
                f"Entry {p['open_price']}  Stop {p['stop_loss']}"
                f"  Held {_hold_duration(p['opened_at'])}{unreal}{_name(p['ticker'])}"
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
                lines.append(f"  *{ticker}* {pct_str}{vol_tag}{_name(ticker)}")
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
    } | {
        t["ticker"] for t in trades_opened
    } | {
        t["ticker"] for t in trades_closed
    })
    market_moves    = get_intraday_move_and_volume(all_tickers)
    tomorrow_events = get_tomorrows_events()

    # Batch-fetch company names for all tickers appearing in the report
    db2 = get_db()
    company_names = fetch_company_names(db2, all_tickers)
    db2.close()

    report = build_report(
        today_str, macro, trades_opened, trades_closed,
        open_positions, daily_pnl, signal_log, market_moves,
        tomorrow_events, company_names
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
