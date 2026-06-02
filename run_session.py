# =============================================================================
# File:         run_session.py
# Author:       Alex Hind
# Created:      2026-06-01
#
# Description:
# -----------------------------------------------------------------------------
# Entry point for GitHub Actions scheduled workflows.
# Called with a session name argument, runs the appropriate routine.
#
# Usage:
#   python run_session.py AUS_OPEN
#   python run_session.py UK_OPEN
#   python run_session.py US_OPEN
#   python run_session.py AUS_MONITOR
#   python run_session.py UK_MONITOR
#   python run_session.py SESSION_CLOSE
#   python run_session.py WEEKEND_REVIEW
#   python run_session.py PREMARKET_BRIEF
#   python run_session.py POSITION_MONITOR
#
# All credentials loaded from environment variables (GitHub Secrets).
# =============================================================================

import sys
import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("run_session")


def run_session_open(session_name: str):
    """Run a session open scan and execute trades."""
    from signals import run_session_scan
    from notify import (session_summary, alert_macro_gate_failed,
                        alert_calendar_block, trade_opened, alert_circuit_breaker)
    from ig_shim import open_trade, get_account_balance, health_check

    # Health check
    hc = health_check()
    if hc["status"] != "OK":
        alert_circuit_breaker("System", "ALL",
            f"IG unreachable at {session_name}: {hc.get('error')}")
        return

    # Full signal scan
    result = run_session_scan(session_name)

    if result.get("block_trading"):
        alert_calendar_block(result["block_reason"], session_name)
        return

    macro = result["macro"]
    if not macro.get("macro_gate_pass"):
        alert_macro_gate_failed(
            macro["gate_reason"], macro["vix"],
            macro["yield_spread"], session_name)
        return

    session_summary(session_name, macro,
                    result["instruments_scanned"],
                    result["trade_candidates"])

    # Execute trades (max 3 per session)
    trades_placed = 0
    for sig in result["trade_candidates"][:3]:
        if trades_placed >= 3:
            break

        ticker     = sig["ticker"]
        direction  = sig["direction"]
        stop_dist  = sig.get("stop_distance", 0)
        limit_dist = round(stop_dist * 2, 4)

        signal_str = (
            f"Options:{sig.get('options_bias','—')} "
            f"BB:{sig.get('bb_breakout_dir','—')} "
            f"COT:{sig.get('cot_bias','—')} "
            f"PA:{sig.get('pa_verdict','—')} "
            f"Confs:{sig.get('confirmation_count',0)}"
        )

        # Size: 2% risk of available balance
        try:
            bal         = get_account_balance()
            risk_amount = bal["available"] * 0.02
            size        = round(risk_amount / stop_dist, 1) if stop_dist > 0 else 0.5
            size        = max(0.5, min(size, 10.0))
        except Exception:
            size = 0.5

        deal_id = open_trade(
            user_id="00000000-0000-0000-0000-000000000001",
            ticker=ticker, direction=direction, size=size,
            stop_distance=stop_dist, limit_distance=limit_dist,
            session_name=session_name, signal_summary=signal_str
        )

        if deal_id:
            trade_opened(ticker, direction, size, 0,
                         stop_dist, limit_dist, session_name, signal_str)
            trades_placed += 1

    # Scan social feeds for new picks at each session open
    try:
        from social_monitor import scan_social_feeds
        scan_social_feeds(max_age_hours=8)   # picks since last session
    except Exception as e:
        log.warning(f"Social feed scan failed: {e}")

    log.info(f"{session_name} complete. Trades placed: {trades_placed}")


def run_monitor(session_name: str = "AUS_MONITOR"):
    """
    Check open positions and update trailing stops.
    Also scans session instruments for new trade entries — signals can fire
    at any point in the session, not just at the open.
    """
    from ig_shim import (get_open_positions, get_snapshot, update_stop,
                         get_close_reason, _log_trade_close_to_db, health_check,
                         open_trade, get_account_balance)
    from notify import trade_closed, alert_circuit_breaker
    from signals import scan_instrument, get_macro_gate
    from config import ATR_MULTIPLIERS, ATR_MULTIPLIER_DEFAULT, SESSION_INSTRUMENTS, MAX_TRADES_PER_SESSION
    import pg8000.native
    from datetime import datetime, timezone

    # Map monitor session to the open session's instrument list
    SESSION_MAP = {
        "AUS_MONITOR":      "AUS_OPEN",
        "UK_MONITOR":       "UK_OPEN",
        "POSITION_MONITOR": "US_OPEN",
    }

    hc = health_check()
    if hc["status"] != "OK":
        alert_circuit_breaker("System", "ALL",
            f"IG unreachable: {hc.get('error')}")
        return

    ig_positions = get_open_positions()
    open_tickers = set()

    if ig_positions:
        log.info(f"Monitoring {len(ig_positions)} position(s)")
    else:
        log.info(f"{session_name}: no open positions — skipping stop review")

    # ── Part 1: trailing stops ────────────────────────────────────────────────
    for pos in ig_positions:
        open_tickers.add(pos["market"].get("instrumentName", ""))
        epic      = pos["market"]["epic"]
        deal_id   = pos["position"]["dealId"]
        direction = pos["position"]["direction"]
        stop      = pos["position"].get("stopLevel", 0)

        snap  = get_snapshot(epic)
        price = snap.get("bid", 0) if direction == "BUY" else snap.get("offer", 0)
        if not price:
            continue

        ticker = epic.split(".")[-3] if "." in epic else epic
        mult   = ATR_MULTIPLIERS.get(ticker, ATR_MULTIPLIER_DEFAULT)
        stop_dist = price * 0.015 * mult

        if direction == "BUY":
            new_stop = round(price - stop_dist, 4)
            if stop and new_stop > float(stop) * 1.005:
                update_stop(deal_id, new_stop)
                log.info(f"Stop raised: {epic} {stop} -> {new_stop}")
        else:
            new_stop = round(price + stop_dist, 4)
            if stop and new_stop < float(stop) * 0.995:
                update_stop(deal_id, new_stop)
                log.info(f"Stop lowered: {epic} {stop} -> {new_stop}")

    # Detect positions closed by IG
    conn = pg8000.native.Connection(
        host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
        database="postgres", user=os.environ["SUPABASE_USER"],
        password=os.environ["SUPABASE_DB_PASSWORD"], ssl_context=True
    )
    our_deals  = {r[0]: r for r in conn.run(
        "select deal_id, ticker, direction, open_price, size from positions"
    )}
    ig_deal_ids = {pos["position"]["dealId"] for pos in ig_positions}
    conn.close()

    for deal_id, row in our_deals.items():
        if deal_id not in ig_deal_ids and not deal_id.startswith("PAPER-"):
            close_reason = get_close_reason(deal_id)
            ticker, direction, open_price, size = row[1], row[2], float(row[3]), float(row[4])
            open_tickers.add(ticker)
            _log_trade_close_to_db(deal_id, open_price, close_reason)
            trade_closed(ticker, direction, open_price, open_price, 0.0, close_reason)
            log.info(f"Detected closure: {ticker} {deal_id} — {close_reason}")

    # ── Part 2: scan for new entries ─────────────────────────────────────────
    try:
        conn2 = pg8000.native.Connection(
            host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
            database="postgres", user=os.environ["SUPABASE_USER"],
            password=os.environ["SUPABASE_DB_PASSWORD"], ssl_context=True
        )
        today_count = conn2.run(
            "select count(*) from trade_log where date(opened_at) = current_date"
        )
        db_tickers = {r[0] for r in conn2.run("select ticker from positions")}
        conn2.close()
        open_tickers |= db_tickers
        trades_today    = int(today_count[0][0]) if today_count else 0
        slots_remaining = max(0, MAX_TRADES_PER_SESSION - trades_today)
    except Exception as e:
        log.error(f"Could not check trade count: {e}")
        slots_remaining = 0

    if slots_remaining > 0:
        open_session = SESSION_MAP.get(session_name, "US_OPEN")
        candidates   = [t for t in SESSION_INSTRUMENTS.get(open_session, [])
                        if t not in open_tickers]
        log.info(f"{session_name}: scanning {len(candidates)} instruments for new entries "
                 f"({slots_remaining} slot(s) remaining)")
        try:
            macro = get_macro_gate(session_name)
            if macro.get("macro_gate_pass"):
                new_trades = 0
                for ticker in candidates:
                    if new_trades >= slots_remaining:
                        break
                    try:
                        sig = scan_instrument(ticker, session_name, macro)
                        if sig.get("trade_signal"):
                            stop_dist  = sig.get("stop_distance", 0)
                            limit_dist = round(stop_dist * 2, 4)
                            try:
                                bal         = get_account_balance()
                                risk_amount = bal["available"] * 0.02
                                size        = round(risk_amount / stop_dist, 1) if stop_dist > 0 else 0.5
                                size        = max(0.5, min(size, 10.0))
                            except Exception:
                                size = 0.5
                            signal_str = (
                                f"Options:{sig.get('options_bias','—')} "
                                f"BB:{sig.get('bb_breakout_dir','—')} "
                                f"Vol:{sig.get('volume_signal','—')} "
                                f"COT:{sig.get('cot_bias','—')} "
                                f"Confs:{sig.get('confirmation_count',0)} "
                                f"[{session_name} rescan]"
                            )
                            deal_id = open_trade(
                                user_id="00000000-0000-0000-0000-000000000001",
                                ticker=ticker,
                                direction=sig["direction"],
                                size=size,
                                stop_distance=stop_dist,
                                limit_distance=limit_dist,
                                session_name=session_name,
                                signal_summary=signal_str
                            )
                            if deal_id:
                                log.info(f"{session_name} NEW TRADE: {ticker} {sig['direction']}")
                                new_trades += 1
                    except Exception as e:
                        log.warning(f"Monitor scan failed for {ticker}: {e}")
            else:
                log.info(f"{session_name}: macro gate closed — {macro.get('gate_reason')}")
        except Exception as e:
            log.error(f"{session_name} new-entry scan failed: {e}")
    else:
        log.info(f"{session_name}: daily trade limit reached — no new entries scanned")


def run_session_close():
    """End of day — hold vs close decisions."""
    from ig_shim import get_open_positions, close_trade, get_snapshot
    from notify import trade_closed, session_summary

    positions  = get_open_positions()
    closed = held = 0

    for pos in positions:
        epic      = pos["market"]["epic"]
        deal_id   = pos["position"]["dealId"]
        direction = pos["position"]["direction"]
        level     = float(pos["position"]["level"])
        size      = float(pos["position"]["size"])
        stop      = pos["position"].get("stopLevel", 0)

        snap  = get_snapshot(epic)
        price = snap.get("bid", 0) if direction == "BUY" else snap.get("offer", 0)
        if not price:
            held += 1
            continue

        pnl       = (price - level) * size if direction == "BUY" else (level - price) * size
        stop_dist = abs(level - float(stop)) if stop else 0
        risk_amt  = stop_dist * size

        should_close = False
        reason       = "SESSION_CLOSE"

        if "CL." in epic or "CL=" in epic:
            should_close = True
            reason       = "SESSION_CLOSE_OIL"
        elif risk_amt > 0 and pnl >= risk_amt * 1.5:
            should_close = True
            reason       = "SESSION_CLOSE_PROFIT_LOCK"

        if should_close:
            if close_trade(deal_id, reason=reason):
                trade_closed(epic, direction, level, price, round(pnl, 2), reason)
                closed += 1
        else:
            held += 1

    log.info(f"Session close: closed={closed} held={held}")


def run_weekend_review():
    """COT refresh, senator scoring, weekly digest."""
    import requests as req
    import pg8000.native
    from notify import weekly_digest
    from config import CFTC_CODES
    from cot_analysis import refresh_all_cot
    from datetime import datetime, timedelta

    # Refresh COT
    log.info("Refreshing COT data...")
    refresh_all_cot()

    # Weekly P&L
    conn = pg8000.native.Connection(
        host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
        database="postgres", user=os.environ["SUPABASE_USER"],
        password=os.environ["SUPABASE_DB_PASSWORD"], ssl_context=True
    )
    since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    rows  = conn.run(
        f"select sum(pnl), count(*), sum(case when pnl>0 then 1 else 0 end) "
        f"from trade_log where closed_at >= '{since}'"
    )
    total_pnl   = float(rows[0][0] or 0)
    trade_count = int(rows[0][1]   or 0)
    win_count   = int(rows[0][2]   or 0)
    win_rate    = (win_count / trade_count * 100) if trade_count > 0 else 0

    best_rows = conn.run(
        f"select ticker, direction, pnl from trade_log "
        f"where closed_at >= '{since}' order by pnl desc limit 1"
    )
    best_trade = (f"{best_rows[0][0]} {best_rows[0][1]} +£{best_rows[0][2]:.2f}"
                  if best_rows else "None")

    senator_rows = conn.run(
        "select senator_name, score, win_rate, trade_count "
        "from senator_scores where qualified=true order by score desc limit 5"
    )
    top_senators = [
        {"name": r[0], "score": float(r[1] or 0),
         "win_rate": float(r[2] or 0), "trade_count": int(r[3] or 0)}
        for r in senator_rows
    ]

    inv_rows = conn.run(
        f"select investor_name, action, ticker from notable_investors "
        f"where recorded_at >= '{since}' order by recorded_at desc limit 5"
    )
    superinvestor_changes = [
        {"investor": r[0], "action": r[1], "ticker": r[2]}
        for r in inv_rows
    ]
    conn.close()

    weekly_digest(
        stats={"total_pnl": total_pnl, "trade_count": trade_count,
               "win_rate": win_rate, "best_trade": best_trade},
        top_senators=top_senators,
        superinvestor_changes=superinvestor_changes
    )
    log.info("Weekend review complete")


def run_premarket_brief():
    """Sunday pre-market brief to Slack."""
    import importlib.util, subprocess
    # Re-use the Sunday brief logic inline
    exec(open("run_session.py").read())   # noqa — entry point handles this via main


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_session.py <SESSION_NAME>")
        print("Sessions: AUS_OPEN AUS_MONITOR UK_OPEN UK_MONITOR US_OPEN")
        print("          SESSION_CLOSE WEEKEND_REVIEW PREMARKET_BRIEF POSITION_MONITOR")
        sys.exit(1)

    session = sys.argv[1].upper()
    log.info(f"Starting session: {session}")

    if session in ("AUS_OPEN", "UK_OPEN", "US_OPEN"):
        run_session_open(session)
    elif session in ("AUS_MONITOR", "UK_MONITOR", "POSITION_MONITOR"):
        run_monitor(session)
    elif session == "US_MONITOR":
        from intraday_signals import run_us_monitor
        run_us_monitor(notify_slack=True)
    elif session == "SESSION_CLOSE":
        run_session_close()
    elif session == "WEEKEND_REVIEW":
        run_weekend_review()
    else:
        log.error(f"Unknown session: {session}")
        sys.exit(1)
