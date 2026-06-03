# =============================================================================
# File:         intraday_signals.py
# Author:       Alex Hind
# Created:      2026-06-01
#
# Description:
# -----------------------------------------------------------------------------
# Intraday technical signal computation for the US Monitor session.
# Evaluates open positions and candidate instruments mid-session using
# short-timeframe technical indicators.
#
# Signals computed:
#   RSI (14)           Overbought >70, oversold <30
#   MACD (12/26/9)     Crossover direction and momentum
#   VWAP               Price position vs intraday VWAP
#   Volume             Current volume vs 20-day average (confirmation)
#   Price momentum     % move from open, distance from intraday high/low
#   BB position        Where price sits within Bollinger Bands
#
# Used by:
#   US Monitor (18:30 BST) — mid-session position review
#   Can also be called at any session open for additional confirmation
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-06-01  Alex Hind   Initial build.
# =============================================================================

import os
from dotenv import load_dotenv; load_dotenv(override=True)
import logging
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

from config import YAHOO_MAP

log = logging.getLogger("intraday_signals")


# =============================================================================
# RSI
# =============================================================================

def compute_rsi(closes: pd.Series, period: int = 14) -> float:
    """Compute RSI. Returns 0-100. >70 overbought, <30 oversold."""
    delta  = closes.diff()
    gain   = delta.clip(lower=0).rolling(period).mean()
    loss   = (-delta.clip(upper=0)).rolling(period).mean()
    rs     = gain / loss.replace(0, np.nan)
    rsi    = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 1)


# =============================================================================
# MACD
# =============================================================================

def compute_macd(closes: pd.Series,
                 fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """
    Compute MACD line, signal line, and histogram.

    Returns:
        macd_line:    MACD line value
        signal_line:  Signal line value
        histogram:    MACD - Signal (positive = bullish momentum)
        crossover:    BULLISH (MACD crossed above signal),
                      BEARISH (crossed below), or NONE
    """
    ema_fast   = closes.ewm(span=fast,   adjust=False).mean()
    ema_slow   = closes.ewm(span=slow,   adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - signal_line

    # Detect crossover in last 3 bars
    crossover = "NONE"
    if len(histogram) >= 3:
        prev_hist = float(histogram.iloc[-2])
        curr_hist = float(histogram.iloc[-1])
        if prev_hist < 0 and curr_hist > 0:
            crossover = "BULLISH"
        elif prev_hist > 0 and curr_hist < 0:
            crossover = "BEARISH"

    return {
        "macd_line":   round(float(macd_line.iloc[-1]),   4),
        "signal_line": round(float(signal_line.iloc[-1]), 4),
        "histogram":   round(float(histogram.iloc[-1]),   4),
        "crossover":   crossover,
        "momentum":    "BULLISH" if float(histogram.iloc[-1]) > 0 else "BEARISH",
    }


# =============================================================================
# VWAP (intraday)
# =============================================================================

def compute_vwap(hist: pd.DataFrame) -> dict:
    """
    Compute intraday VWAP from 5-minute candles.
    Returns VWAP level and whether price is above or below.
    """
    result = {"vwap": None, "position": None, "pct_from_vwap": None}
    try:
        typical = (hist["High"] + hist["Low"] + hist["Close"]) / 3
        vwap    = (typical * hist["Volume"]).cumsum() / hist["Volume"].cumsum()
        last_vwap  = float(vwap.iloc[-1])
        last_close = float(hist["Close"].iloc[-1])
        pct_from   = (last_close - last_vwap) / last_vwap * 100

        result["vwap"]         = round(last_vwap, 4)
        result["position"]     = "ABOVE" if last_close > last_vwap else "BELOW"
        result["pct_from_vwap"] = round(pct_from, 2)
    except Exception as e:
        log.warning(f"VWAP failed: {e}")
    return result


# =============================================================================
# Volume analysis
# =============================================================================

def compute_volume_signal(ticker: str, hist_5m: pd.DataFrame) -> dict:
    """
    Compare current session volume to 20-day average daily volume.

    HIGH_VOLUME  — today's volume > 1.5× 20-day average (strong conviction)
    NORMAL       — within ±50% of average
    LOW_VOLUME   — below 50% of average (weak conviction)
    """
    result = {"volume_signal": "NORMAL", "volume_ratio": None}
    try:
        yticker = YAHOO_MAP.get(ticker, ticker)
        t       = yf.Ticker(yticker)

        # 20-day average daily volume
        hist_daily = t.history(period="25d", interval="1d")
        if hist_daily.empty:
            return result

        avg_vol     = float(hist_daily["Volume"].mean())
        today_vol   = float(hist_5m["Volume"].sum()) if not hist_5m.empty else 0

        if avg_vol > 0:
            ratio = today_vol / avg_vol
            result["volume_ratio"] = round(ratio, 2)
            if ratio >= 1.5:
                result["volume_signal"] = "HIGH_VOLUME"
            elif ratio < 0.5:
                result["volume_signal"] = "LOW_VOLUME"

    except Exception as e:
        log.warning(f"Volume signal failed for {ticker}: {e}")
    return result


# =============================================================================
# Price momentum
# =============================================================================

def compute_price_momentum(hist_5m: pd.DataFrame) -> dict:
    """
    Intraday price momentum — % from open, distance from high/low.
    """
    result = {
        "pct_from_open":   None,
        "pct_from_high":   None,
        "pct_from_low":    None,
        "intraday_trend":  None,   # UP, DOWN, FLAT
    }
    try:
        if hist_5m.empty or len(hist_5m) < 2:
            return result

        open_price  = float(hist_5m["Open"].iloc[0])
        close_price = float(hist_5m["Close"].iloc[-1])
        high_price  = float(hist_5m["High"].max())
        low_price   = float(hist_5m["Low"].min())

        result["pct_from_open"] = round((close_price - open_price) / open_price * 100, 2)
        result["pct_from_high"] = round((close_price - high_price) / high_price * 100, 2)
        result["pct_from_low"]  = round((close_price - low_price)  / low_price  * 100, 2)

        # Intraday trend from first half vs second half of session
        mid  = len(hist_5m) // 2
        if mid > 0:
            first_half  = float(hist_5m["Close"].iloc[:mid].mean())
            second_half = float(hist_5m["Close"].iloc[mid:].mean())
            if second_half > first_half * 1.002:
                result["intraday_trend"] = "UP"
            elif second_half < first_half * 0.998:
                result["intraday_trend"] = "DOWN"
            else:
                result["intraday_trend"] = "FLAT"

    except Exception as e:
        log.warning(f"Price momentum failed: {e}")
    return result


# =============================================================================
# BB position (intraday)
# =============================================================================

def compute_bb_position(closes: pd.Series, period: int = 20) -> dict:
    """
    Where is price within the Bollinger Bands?
    %B = (price - lower) / (upper - lower)
    %B > 1 = above upper band (overbought intraday)
    %B < 0 = below lower band (oversold intraday)
    """
    result = {"bb_pct_b": None, "bb_position": None}
    try:
        if len(closes) < period:
            return result

        sma   = closes.rolling(period).mean()
        std   = closes.rolling(period).std()
        upper = sma + 2 * std
        lower = sma - 2 * std

        price = float(closes.iloc[-1])
        u     = float(upper.iloc[-1])
        l     = float(lower.iloc[-1])

        if u != l:
            pct_b = (price - l) / (u - l)
            result["bb_pct_b"]   = round(pct_b, 3)
            if pct_b > 1.0:
                result["bb_position"] = "ABOVE_UPPER"
            elif pct_b > 0.8:
                result["bb_position"] = "NEAR_UPPER"
            elif pct_b < 0.0:
                result["bb_position"] = "BELOW_LOWER"
            elif pct_b < 0.2:
                result["bb_position"] = "NEAR_LOWER"
            else:
                result["bb_position"] = "MIDDLE"

    except Exception as e:
        log.warning(f"BB position failed: {e}")
    return result


# =============================================================================
# Master intraday scan — one instrument
# =============================================================================

def scan_intraday(ticker: str) -> dict:
    """
    Run full intraday technical analysis for one instrument.
    Uses 5-minute candles for the current session.

    Returns a comprehensive technical picture used by the US Monitor
    to decide whether to hold, tighten stops, or flag for early exit.
    """
    result = {
        "ticker":          ticker,
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "rsi":             None,
        "rsi_signal":      None,    # OVERBOUGHT / OVERSOLD / NEUTRAL
        "macd":            {},
        "vwap":            {},
        "volume":          {},
        "momentum":        {},
        "bb":              {},
        "overall_signal":  "NEUTRAL",
        "hold_flag":       True,    # False = consider early exit
        "alert":           "",
    }

    yticker = YAHOO_MAP.get(ticker, ticker)

    try:
        t      = yf.Ticker(yticker)
        hist   = t.history(period="1d",  interval="5m")
        hist_h = t.history(period="5d",  interval="1h")

        if hist.empty:
            log.warning(f"No intraday data for {ticker}")
            return result

        closes_5m = hist["Close"]
        closes_1h = hist_h["Close"] if not hist_h.empty else closes_5m

        # RSI on 1h for smoother signal
        if len(closes_1h) >= 14:
            rsi = compute_rsi(closes_1h)
            result["rsi"] = rsi
            if rsi >= 75:
                result["rsi_signal"] = "OVERBOUGHT"
            elif rsi <= 25:
                result["rsi_signal"] = "OVERSOLD"
            else:
                result["rsi_signal"] = "NEUTRAL"

        # MACD on 1h
        if len(closes_1h) >= 35:
            result["macd"] = compute_macd(closes_1h)

        # VWAP on 5m (intraday)
        result["vwap"]     = compute_vwap(hist)

        # Volume
        result["volume"]   = compute_volume_signal(ticker, hist)

        # Price momentum
        result["momentum"] = compute_price_momentum(hist)

        # BB position on 5m
        if len(closes_5m) >= 20:
            result["bb"] = compute_bb_position(closes_5m)

        # Overall signal and hold/exit logic
        bull_signals = 0
        bear_signals = 0

        if result["rsi_signal"] == "OVERSOLD":        bull_signals += 1
        if result["rsi_signal"] == "OVERBOUGHT":      bear_signals += 1
        if result["macd"].get("momentum") == "BULLISH": bull_signals += 1
        if result["macd"].get("momentum") == "BEARISH": bear_signals += 1
        if result["vwap"].get("position") == "ABOVE":  bull_signals += 1
        if result["vwap"].get("position") == "BELOW":  bear_signals += 1
        if result["momentum"].get("intraday_trend") == "UP":   bull_signals += 1
        if result["momentum"].get("intraday_trend") == "DOWN": bear_signals += 1

        if bull_signals >= 3:
            result["overall_signal"] = "BULLISH"
        elif bear_signals >= 3:
            result["overall_signal"] = "BEARISH"

        # Hold flag — consider early exit if:
        alerts = []
        if result["rsi"] and result["rsi"] > 78:
            alerts.append(f"RSI extremely overbought ({result['rsi']})")
            result["hold_flag"] = False
        if result["macd"].get("crossover") == "BEARISH":
            alerts.append("MACD bearish crossover")
            result["hold_flag"] = False
        if result["vwap"].get("position") == "BELOW" and result["momentum"].get("intraday_trend") == "DOWN":
            alerts.append("Below VWAP and trending down")
            result["hold_flag"] = False
        if result["bb"].get("bb_position") == "ABOVE_UPPER" and bear_signals >= 2:
            alerts.append("Above upper BB with bearish signals")
            result["hold_flag"] = False
        if result["volume"].get("volume_signal") == "LOW_VOLUME":
            alerts.append("Low volume — weak conviction")

        result["alert"] = " | ".join(alerts) if alerts else ""

    except Exception as e:
        log.error(f"Intraday scan failed for {ticker}: {e}")

    return result


# =============================================================================
# US Monitor — scan all open positions + watch list
# =============================================================================

def run_us_monitor(notify_slack: bool = True) -> list:
    """
    Mid-session US Monitor — runs every 15 minutes during the US session.

    Two jobs:
    1. Watch all OPEN POSITIONS — flag deterioration (RSI, MACD, VWAP, volume).
    2. Re-scan ALL SESSION INSTRUMENTS for NEW entries — signals can fire at
       any point in the session, not just at the open. If IBM's BB breakout
       happens at 14:45, this catches it and places a trade.
    """
    import os
    import requests
    import pg8000.native
    from signals import scan_instrument, get_macro_gate
    from ig_shim import open_trade, get_account_balance
    from config import SESSION_INSTRUMENTS, MAX_TRADES_PER_SESSION

    results = []

    # ── DB connection ─────────────────────────────────────────────────────────
    try:
        conn = pg8000.native.Connection(
            host="aws-0-eu-west-1.pooler.supabase.com", port=6543,
            database="postgres", user=os.environ["SUPABASE_USER"],
            password=os.environ["SUPABASE_DB_PASSWORD"], ssl_context=True
        )
        pos_rows = conn.run(
            "select ticker, direction, open_price, stop_loss, deal_id from positions"
        )
        # How many trades already placed today
        today_count = conn.run(
            "select count(*) from trade_log where date(opened_at) = current_date"
        )
        conn.close()
    except Exception as e:
        log.error(f"Could not fetch positions: {e}")
        return results

    open_tickers    = {r[0] for r in pos_rows}
    trades_today    = int(today_count[0][0]) if today_count else 0
    slots_remaining = max(0, MAX_TRADES_PER_SESSION - trades_today)

    # ── Part 1: Monitor existing positions ────────────────────────────────────
    if not pos_rows:
        log.info("US Monitor: no open positions — skipping position review")

    position_alerts = []
    for row in pos_rows:
        ticker, direction, open_price, stop_loss, deal_id = row
        log.info(f"Scanning intraday: {ticker}")

        scan = scan_intraday(ticker)
        scan["direction"]   = direction
        scan["open_price"]  = float(open_price or 0)
        scan["stop_loss"]   = float(stop_loss  or 0)
        scan["deal_id"]     = deal_id
        results.append(scan)

        # Flag positions that may need attention
        if not scan["hold_flag"] and scan["alert"]:
            position_alerts.append(scan)

    # ── Part 2: Re-scan session instruments for NEW entries ───────────────────
    new_trades_placed = 0
    if slots_remaining > 0:
        log.info(f"US Monitor: scanning for new entries ({slots_remaining} slot(s) remaining today)")
        try:
            macro = get_macro_gate("US_MONITOR")
            if macro.get("macro_gate_pass"):
                candidates = [t for t in SESSION_INSTRUMENTS.get("US_OPEN", [])
                              if t not in open_tickers]
                for ticker in candidates:
                    if new_trades_placed >= slots_remaining:
                        break
                    try:
                        sig = scan_instrument(ticker, "US_MONITOR", macro)
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
                                f"COT:{sig.get('cot_bias','—')} "
                                f"PA:{sig.get('pa_verdict','—')} "
                                f"Confs:{sig.get('confirmation_count',0)} "
                                f"[intraday rescan]"
                            )
                            from run_session import get_user_profile
                            profile = get_user_profile()
                            result = open_trade(
                                user_id=profile["user_id"],
                                ticker=ticker,
                                direction=sig["direction"],
                                size=size,
                                stop_distance=stop_dist,
                                limit_distance=limit_dist,
                                session_name="US_MONITOR",
                                signal_summary=signal_str,
                                paper_trade=profile["paper_trade"]
                            )
                            if result:
                                log.info(f"US Monitor NEW TRADE: {ticker} {sig['direction']}")
                                new_trades_placed += 1
                    except Exception as e:
                        log.warning(f"Monitor scan failed for {ticker}: {e}")
            else:
                log.info(f"US Monitor: macro gate closed — {macro.get('gate_reason')} — no new entries")
        except Exception as e:
            log.error(f"US Monitor new-entry scan failed: {e}")
    else:
        log.info("US Monitor: daily trade limit reached — no new entries scanned")

    # Send Slack alert for flagged positions
    if position_alerts and notify_slack:
        slack_url = os.environ.get("SLACK_ALERTS", "")
        if slack_url:
            lines = ""
            for s in position_alerts:
                rsi_str  = f"RSI:{s['rsi']}" if s.get("rsi") else ""
                macd_str = f"MACD:{s['macd'].get('momentum','')}" if s.get("macd") else ""
                vwap_str = f"VWAP:{s['vwap'].get('position','')}" if s.get("vwap") else ""
                lines += (
                    f"• *{s['ticker']}* {s['direction']} — ⚠️ {s['alert']}\n"
                    f"  {rsi_str}  {macd_str}  {vwap_str}\n"
                )

            blocks = [
                {"type": "header",
                 "text": {"type": "plain_text", "text": "⚠️ US Monitor — Position Alert"}},
                {"type": "section",
                 "text": {"type": "mrkdwn",
                          "text": f"*{len(position_alerts)} position(s) flagged for review:*\n{lines}"}},
                {"type": "context",
                 "elements": [{"type": "mrkdwn",
                                "text": datetime.now(timezone.utc).strftime("%d %b %H:%M UTC")}]}
            ]
            requests.post(slack_url, json={"blocks": blocks}, timeout=10)
            log.info(f"US Monitor alert sent for {len(position_alerts)} positions")

    # Always send session summary to signals channel
    if notify_slack:
        slack_url = os.environ.get("SLACK_SIGNALS", "")
        if slack_url:
            lines = ""
            for s in results:
                rsi   = s.get("rsi", "—")
                macd  = s.get("macd", {}).get("momentum", "—")
                vwap  = s.get("vwap", {}).get("position", "—")
                trend = s.get("momentum", {}).get("intraday_trend", "—")
                vol   = s.get("volume", {}).get("volume_signal", "—")
                flag  = "⚠️" if not s["hold_flag"] else "✅"
                lines += f"{flag} *{s['ticker']}* | RSI:{rsi} | MACD:{macd} | VWAP:{vwap} | Trend:{trend} | Vol:{vol}\n"

            blocks = [
                {"type": "header",
                 "text": {"type": "plain_text", "text": "📊 US Monitor — Mid-Session Review"}},
                {"type": "section",
                 "text": {"type": "mrkdwn", "text": lines or "_No open positions_"}},
                {"type": "context",
                 "elements": [{"type": "mrkdwn",
                                "text": datetime.now(timezone.utc).strftime("%d %b %H:%M UTC")}]}
            ]
            requests.post(slack_url, json={"blocks": blocks}, timeout=10)

    return results


# =============================================================================
# Entry point
# Usage: python intraday_signals.py
# =============================================================================

if __name__ == "__main__":
    import logging, os
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    os.environ.setdefault("SUPABASE_USER", os.environ.get("SUPABASE_USER", ""))
    os.environ.setdefault("SUPABASE_DB_PASSWORD", os.environ.get("SUPABASE_DB_PASSWORD", ""))

    results = run_us_monitor(notify_slack=False)
    for r in results:
        print(f"\n{r['ticker']} {r['direction']}:")
        print(f"  RSI:    {r.get('rsi')} ({r.get('rsi_signal')})")
        print(f"  MACD:   {r['macd'].get('momentum')} | crossover={r['macd'].get('crossover')}")
        print(f"  VWAP:   {r['vwap'].get('position')} ({r['vwap'].get('pct_from_vwap')}%)")
        print(f"  Volume: {r['volume'].get('volume_signal')} ({r['volume'].get('volume_ratio')}x avg)")
        print(f"  Trend:  {r['momentum'].get('intraday_trend')}")
        print(f"  Hold:   {r['hold_flag']}")
        if r["alert"]:
            print(f"  ALERT:  {r['alert']}")
