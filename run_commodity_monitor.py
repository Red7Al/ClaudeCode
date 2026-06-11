# ======================================================================================================================
# File:         run_commodity_monitor.py
# Author:       Alex Hind
# Created:      2026-06-01
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Dedicated commodity monitor for Gold (XAUUSD), Silver (XAGUSD) and
# Oil (OIL/CL). Runs every 30 minutes during gap hours when no session
# monitor is active — fills 24/5 coverage for commodity positions.
#
# Gap hours covered:
#   04:00-08:00 UTC — between AUS close and UK open
#   21:00-00:00 UTC — after US close, before next AUS open
#
# Lightweight — exits in < 1 minute when no commodity positions are open.
# Only processes XAUUSD, XAGUSD and OIL positions, ignores equities.
#
# Actions:
#   - Update trailing stops (ATR-based)
#   - Detect IG-triggered closures (stop hit, limit hit)
#   - Alert via Slack if position closed or stop moved significantly
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-01  Alex Hind   Initial build.
# 1.0.1   2026-06-06  Alex Hind   Detected-closure notify: replace hardcoded 0.0 P&L with the realised P&L computed from
#                                 open/close price, direction and size (mirrors the run_session.py fix). Slack
#                                 previously reported every commodity closure as £0.00 regardless of actual gain/loss.
# ======================================================================================================================

import os
from db_pool import get_db as _pool_get_db   # resilient session-pooler connection (timeout+retry)
from dotenv import load_dotenv; load_dotenv(override=True)
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("commodity_monitor")

# Commodity instruments to monitor
COMMODITY_TICKERS  = {"XAUUSD", "XAGUSD", "OIL", "USOIL", "GOLD", "SILVER"}
COMMODITY_EPICS    = {
    "CS.D.USCGC.TODAY.IP",   # Gold
    "CS.D.USCSI.TODAY.IP",   # Silver
    "CC.D.CL.USS.IP",        # Oil
}


def run():
    from ig_shim import (get_open_positions, get_snapshot, update_stop,
                         get_close_reason, _log_trade_close_to_db, health_check)
    from notify import trade_closed, alert_circuit_breaker
    from config import ATR_MULTIPLIERS, ATR_MULTIPLIER_DEFAULT
    import pg8000.native
    from datetime import datetime, timezone

    # Quick health check
    hc = health_check()
    if hc["status"] != "OK":
        alert_circuit_breaker("System", "COMMODITIES",
            f"IG unreachable: {hc.get('error')}")
        sys.exit(1)

    # Get all open positions
    all_positions = get_open_positions()

    # Filter to commodity positions only
    commodity_positions = [
        pos for pos in all_positions
        if pos["market"]["epic"] in COMMODITY_EPICS
        or any(t in pos["market"].get("instrumentName", "").upper()
               for t in ["GOLD", "SILVER", "OIL", "CRUDE"])
    ]

    if not commodity_positions:
        log.info("No open commodity positions — exiting")
        sys.exit(0)

    log.info(f"Monitoring {len(commodity_positions)} commodity position(s)")

    # Connect to Supabase
    conn = _pool_get_db()
    our_deals = {
        r[0]: r for r in conn.run(
            "select deal_id, ticker, direction, open_price, stop_loss, size, take_profit from positions"
        )
    }
    ig_deal_ids = {pos["position"]["dealId"] for pos in all_positions}
    conn.close()

    # Process each commodity position
    for pos in commodity_positions:
        epic      = pos["market"]["epic"]
        deal_id   = pos["position"]["dealId"]
        direction = pos["position"]["direction"]
        level     = float(pos["position"]["level"])
        stop      = pos["position"].get("stopLevel", 0)
        size      = float(pos["position"]["size"])

        # Get current price
        snap  = get_snapshot(epic)
        bid   = snap.get("bid", 0)
        offer = snap.get("offer", 0)
        price = bid if direction == "BUY" else offer
        if not price:
            continue

        pnl = (price - level) * size if direction == "BUY" else (level - price) * size

        # Determine ticker from epic
        if "USCGC" in epic:    ticker = "XAUUSD"
        elif "USCSI" in epic:  ticker = "XAGUSD"
        elif "CL" in epic:     ticker = "OIL"
        else:                  ticker = epic.split(".")[-3] if "." in epic else epic

        # ATR-based trailing stop
        mult      = ATR_MULTIPLIERS.get(ticker, ATR_MULTIPLIER_DEFAULT)
        stop_dist = price * 0.012 * mult   # ~1.2% of price × multiplier

        if direction == "BUY":
            new_stop = round(price - stop_dist, 4)
            if stop and new_stop > float(stop) * 1.003:   # >0.3% improvement
                result = update_stop(deal_id, new_stop)
                if result:
                    log.info(f"Stop raised: {ticker} {stop:.4f} → {new_stop:.4f} | P&L: £{pnl:.2f}")
        else:
            new_stop = round(price + stop_dist, 4)
            if stop and new_stop < float(stop) * 0.997:
                result = update_stop(deal_id, new_stop)
                if result:
                    log.info(f"Stop lowered: {ticker} {stop:.4f} → {new_stop:.4f} | P&L: £{pnl:.2f}")

    # Detect commodity positions closed by IG since last check
    for deal_id, row in our_deals.items():
        ticker    = row[1]
        if ticker not in COMMODITY_TICKERS and not any(
            t in str(ticker).upper() for t in ["XAU", "XAG", "OIL", "GOLD", "SILVER"]
        ):
            continue   # skip non-commodity positions

        if deal_id not in ig_deal_ids and not deal_id.startswith("PAPER-"):
            direction   = row[2]
            open_price  = float(row[3])
            stop_loss   = float(row[4]) if row[4] else None
            size        = float(row[5])
            take_profit = float(row[6]) if len(row) > 6 and row[6] else None
            rr = None
            if stop_loss and take_profit and open_price:
                stop_dist   = abs(open_price - stop_loss)
                target_dist = abs(take_profit - open_price)
                if stop_dist > 0:
                    rr = round(target_dist / stop_dist, 2)
            close_reason, close_price = get_close_reason(deal_id)
            actual_close = close_price or open_price
            _log_trade_close_to_db(deal_id, actual_close, close_reason)
            pnl = round(
                (actual_close - open_price) * size if direction == "BUY"
                else (open_price - actual_close) * size,
                2
            )
            trade_closed(ticker, direction, open_price, actual_close, pnl, close_reason, rr=rr)
            log.info(f"Commodity position closed: {ticker} {deal_id} — {close_reason} "
                     f"@ {close_price} | P&L £{pnl:.2f}")

    log.info("Commodity monitor complete")


if __name__ == "__main__":
    run()
