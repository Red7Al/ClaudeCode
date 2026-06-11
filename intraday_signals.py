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
# 1.0.1   2026-06-05  Alex Hind   get_intraday_signals: do not fall back to 5m data
#                                 when 1h data is unavailable. RSI/MACD on 5m bars
#                                 is 10× more reactive than intended (14 bars = 1.2h
#                                 instead of 14h) — silent wrong-timeframe signals.
#                                 Now logs a warning and skips RSI/MACD instead.
#                                 Position size fallback 0.5 → 0.0 on exception;
#                                 same dangerous pattern fixed in ig_shim.py 1.0.3.
# 1.0.2   2026-06-08  Alex Hind   compute_rsi: zero-loss period (a pure up-move)
#                                 returned NaN instead of 100 — so `rsi > 70` was
#                                 silently False exactly when overbought mattered
#                                 most. Now resolves NaN to 100 (pure rally) / 50
#                                 (flat or too few bars). Verified on synthetic
#                                 series: rally→100.0, flat→50.0, normal→58.5.
# 1.1.0   2026-06-10  Alex Hind   HVF setups → IG WORKING ORDERS (user 2026-06-10):
#                                 US monitor routes HVF signals to a pending order at
#                                 the exact H3 entry (re-signal = amend, never a
#                                 duplicate; no market fall-through), reconciles
#                                 fills/cancels each pass, and counts open positions
#                                 + today's PENDING working orders in the US slot
#                                 budget (was trade_log only).
# 1.2.0   2026-06-10  Alex Hind   X (Twitter) draft reports: after each tradeable-HVF
#                                 Slack post, _generate_x_drafts() posts one
#                                 tweet-ready block per instrument (with HVF chart
#                                 attached) to SLACK_TWITTER channel for review before
#                                 manual posting to X.
# 1.4.1   2026-06-11  Alex Hind   Signal summary: Confs:N now lists WHICH confirmations
#                                 fired, via signals.conf_names() (user 2026-06-11 —
#                                 'How is NEUTRAL a confirmation?': the Options/BB/COT
#                                 fields show family STATE, not the counted items).
# 1.4.0   2026-06-11  Alex Hind   (renumbered from duplicate 1.3.1)
#                                 _generate_x_drafts: post card image now uploaded to
#                                 the SLACK_TWITTER channel via the Slack external
#                                 upload flow (files.getUploadURLExternal →
#                                 completeUploadExternal; legacy files.upload retired).
#                                 Needs SLACK_BOT_TOKEN (files:write) +
#                                 SLACK_TWITTER_CHANNEL_ID secrets; until both are set
#                                 the draft text posts with an explicit "chart not
#                                 attached" note (no silent gap).
# 1.2.5   2026-06-11  Alex Hind   _generate_x_drafts: revert 1.2.3 — SLACK_CLAUDE_TWITTER
#                                 removed; SLACK_TWITTER is the only secret and already
#                                 points at #claude-twitter (user correction 2026-06-11).
# 1.2.4   2026-06-11  Alex Hind   _generate_x_drafts: chart upgraded to the agreed X
#                                 post card format (2026-06-10): tweet-text header
#                                 panel (@handle, $TICKER (Name), setup, levels,
#                                 hashtags, "Not financial advice."), red upper jaw /
#                                 green lower jaw funnel, full-width entry/stop/target
#                                 lines with right-edge labels. Replaces plain chart.
# 1.2.3   2026-06-11  Alex Hind   _generate_x_drafts: also post to SLACK_CLAUDE_TWITTER
#                                 (#claude-twitter channel) in addition to SLACK_TWITTER.
# 1.2.2   2026-06-11  Alex Hind   _generate_x_drafts: always append "Not financial
#                                 advice." to every tweet (user directive 2026-06-11).
# 1.2.1   2026-06-11  Alex Hind   _generate_x_drafts: SLACK_X renamed to SLACK_TWITTER
#                                 (user directive — no separate SLACK_X secret exists).
# 1.3.0   2026-06-10  Alex Hind   HVF watch deduplication: _post_hvf_watch now
#                                 fingerprints the tradeable+developing lists and
#                                 compares against the last-posted state in
#                                 hvf_watch_state DB table. Sends "No changes in
#                                 the latest period." when nothing has moved; full
#                                 update only when figures actually change.
#                                 HVF watch removed from run_us_monitor (was Part
#                                 1.5 with a 30-min gate) — now a standalone
#                                 US_HVF_WATCH session run every 2 hours via
#                                 trading-us-hvf-watch.yml workflow.
# 1.3.1   2026-06-10  Alex Hind   Fix X-draft funnel chart: upper jaw now drawn
#                                 through real H1→H2→H3 pivot points, lower jaw
#                                 through L1→L2→L3 — anchored to actual price
#                                 history dates/levels from the signal dict.
#                                 History window now spans from 14 days before
#                                 oldest pivot date (not fixed 180 days). Legend
#                                 and R:R removed from chart — shown in Slack
#                                 context block below the tweet instead.
# =============================================================================

import os
from db_pool import get_db as _pool_get_db   # resilient session-pooler connection (timeout+retry)
from dotenv import load_dotenv; load_dotenv(override=True)
import logging
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

from config import YAHOO_MAP, DEFAULT_TARGET_RR

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
    val    = rsi.iloc[-1]
    if pd.isna(val):
        # loss == 0 over the period: a pure up-move is max-overbought (RSI 100);
        # a completely flat series (gain also 0, or too few bars) is neutral (50).
        last_gain = gain.iloc[-1]
        val = 100.0 if (pd.notna(last_gain) and last_gain > 0) else 50.0
    return round(float(val), 1)


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
        if hist_h.empty:
            # 1h data unavailable — do not fall back to 5m data. RSI/MACD
            # computed on 5m bars gives 10× more reactive signals than intended
            # (14-period = 1.2h instead of 14h). Return without RSI/MACD rather
            # than produce signals from the wrong timeframe silently.
            log.warning(f"Intraday 1h data unavailable for {ticker} — RSI/MACD skipped")
            closes_1h = None
        else:
            closes_1h = hist_h["Close"]

        # RSI on 1h for smoother signal
        if closes_1h is not None and len(closes_1h) >= 14:
            rsi = compute_rsi(closes_1h)
            result["rsi"] = rsi
            if rsi >= 75:
                result["rsi_signal"] = "OVERBOUGHT"
            elif rsi <= 25:
                result["rsi_signal"] = "OVERSOLD"
            else:
                result["rsi_signal"] = "NEUTRAL"

        # MACD on 1h
        if closes_1h is not None and len(closes_1h) >= 35:
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

# Non-equity members of SESSION_INSTRUMENTS["US_OPEN"] — excluded from the HVF
# equity watch (HVF is a stock pattern; these are index / commodity / crypto / FX).
US_NON_EQUITY = {"SPX500", "XAUUSD", "OIL", "BTCUSD", "ETHUSD", "XRPUSD", "SOLUSD", "BNBUSD"}


def hvf_watch_us_equities(open_tickers: set, notify_slack: bool = True) -> list:
    """
    Run the multi-timeframe HVF scan over the US EQUITIES already in our list
    (SESSION_INSTRUMENTS["US_OPEN"] minus index/commodity/crypto) and surface
    tradeable + developing funnels to #signals.

    This is the always-on HVF VISIBILITY layer for the US Monitor so a funnel on
    one of our equities is never silently missed — including when the daily trade
    cap is hit or the macro gate is closed (when Part 2 does not scan/trade).
    Trading still happens in run_us_monitor Part 2 (HVF is a primary in
    scan_instrument). Uses the same rigorous get_hvf_signal_mtf as the daily HVF
    report. Caller gates cadence (every ~30 min) to bound Yahoo load.
    """
    from price_action import get_hvf_signal_mtf, get_trend_structure
    from config import SESSION_INSTRUMENTS, HVF_MIN_RR

    equities = [t for t in SESSION_INSTRUMENTS.get("US_OPEN", [])
                if t not in US_NON_EQUITY and t not in (open_tickers or set())]

    tradeable, developing = [], []
    for ticker in equities:
        try:
            trend = get_trend_structure(ticker)
            hvf   = get_hvf_signal_mtf(ticker, trend_hint=trend)
            if not hvf.get("hvf_type"):
                continue
            hvf["ticker"] = ticker
            sig = hvf.get("hvf_signal", "")
            rr  = hvf.get("risk_reward") or 0
            if sig in ("READY", "TRIGGERED") and rr >= HVF_MIN_RR:
                tradeable.append(hvf)
            elif sig == "DEVELOPING":
                developing.append(hvf)
            time.sleep(0.3)   # polite to Yahoo Finance
        except Exception as e:
            log.warning(f"HVF watch failed for {ticker}: {e}")

    rank = {"TRIGGERED": 3, "READY": 2, "DEVELOPING": 1}
    tradeable.sort(key=lambda r: (rank.get(r.get("hvf_signal", ""), 0),
                                  r.get("pattern_quality", 0)), reverse=True)
    developing.sort(key=lambda r: r.get("risk_reward") or 0, reverse=True)
    log.info(f"HVF watch (US equities): {len(equities)} scanned, "
             f"{len(tradeable)} tradeable, {len(developing)} developing")

    if notify_slack and (tradeable or developing):
        _post_hvf_watch(tradeable, developing, HVF_MIN_RR)
        if tradeable:
            _generate_x_drafts(tradeable)
    return tradeable


def _hvf_fingerprint(tradeable: list, developing: list) -> str:
    """
    Stable fingerprint of the current HVF watch state.
    Changes when any instrument is added/removed or its signal, R:R (1dp),
    entry level (2dp), stop or target changes. Developing list is included
    so additions/removals there also trigger a post.
    """
    import hashlib, json

    def _item(r):
        return (
            r.get("ticker", ""),
            r.get("hvf_type", ""),
            r.get("hvf_signal", ""),
            round(r.get("risk_reward") or 0, 1),
            round(r.get("h3_level") or 0, 2),
            round(r.get("stop_level") or 0, 2),
            round(r.get("target") or 0, 2),
        )

    payload = {
        "t": sorted([_item(r) for r in tradeable]),
        "d": sorted([_item(r) for r in developing]),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _hvf_last_fingerprint() -> str:
    """Read the last-posted HVF watch fingerprint from DB. Returns '' on any error."""
    try:
        conn = _pool_get_db()
        rows = conn.run(
            "SELECT fingerprint FROM hvf_watch_state WHERE key = 'us_equities' LIMIT 1"
        )
        conn.close()
        return rows[0][0] if rows else ""
    except Exception as e:
        log.debug(f"hvf_last_fingerprint read failed: {e}")
        return ""


def _hvf_save_fingerprint(fp: str):
    """Upsert the current HVF watch fingerprint into DB."""
    try:
        conn = _pool_get_db()
        conn.run(
            """INSERT INTO hvf_watch_state (key, fingerprint, posted_at)
               VALUES ('us_equities', :fp, now())
               ON CONFLICT (key) DO UPDATE
               SET fingerprint = EXCLUDED.fingerprint,
                   posted_at   = EXCLUDED.posted_at""",
            fp=fp,
        )
        conn.close()
    except Exception as e:
        log.warning(f"hvf_save_fingerprint failed (non-critical): {e}")


def _post_hvf_watch(tradeable: list, developing: list, min_rr: float):
    """
    Post the HVF equity-watch to #claude-trading-signals.

    Deduplication: compute a fingerprint of the current tradeable+developing
    lists. If it matches the last-posted fingerprint stored in hvf_watch_state,
    send a short "No changes" notice instead of repeating the full list.
    The fingerprint changes when any instrument is added/removed or its signal,
    R:R, entry, stop or target changes.
    """
    import requests
    from notify import fmt
    slack_url = os.environ.get("SLACK_SIGNALS", "")
    if not slack_url:
        return

    now_str = datetime.now(timezone.utc).strftime("%d %b %H:%M UTC")

    # ── Deduplication check ───────────────────────────────────────────────────
    current_fp  = _hvf_fingerprint(tradeable, developing)
    last_fp     = _hvf_last_fingerprint()

    if current_fp == last_fp:
        # Nothing changed — send a lightweight "no change" notice and return
        blocks = [
            {"type": "header",
             "text": {"type": "plain_text", "text": "🌀 HVF Watch — US Equities (US Monitor)"}},
            {"type": "section",
             "text": {"type": "mrkdwn",
                      "text": (f"*No changes in the latest period.*\n"
                               f"_{len(tradeable)} tradeable, {len(developing)} developing — "
                               f"unchanged since last post._")}},
            {"type": "context",
             "elements": [{"type": "mrkdwn",
                            "text": f"Checked {now_str} | no figure changes detected"}]},
        ]
        try:
            requests.post(slack_url, json={"blocks": blocks}, timeout=10)
        except Exception as e:
            log.error(f"HVF watch (no-change) post failed: {e}")
        return

    # ── Full update — something changed ──────────────────────────────────────
    def _line(r):
        d  = "🟢" if r.get("hvf_type") == "BULLISH" else "🔴"
        s  = {"TRIGGERED": "⚡", "READY": "✅", "DEVELOPING": "👀"}.get(r.get("hvf_signal", ""), "")
        rr = r.get("risk_reward")
        tf = (r.get("hvf_timeframe", "") or "").replace("daily-", "d")
        return (f"{d}{s} *{fmt(r['ticker'])}*  R:R {f'{rr:.1f}:1' if rr else '—'}  "
                f"entry {r.get('h3_level')}  stop {r.get('stop_level')}  "
                f"target {r.get('target')}  [{tf}]")

    text = ""
    if tradeable:
        text += f"*⚡ Tradeable HVF on our US equities — {len(tradeable)} (R:R ≥ {min_rr:.0f}:1)*\n"
        text += "\n".join(_line(r) for r in tradeable[:15]) + "\n\n"
    if developing:
        text += f"*👀 Developing HVF — {len(developing)} (watch, R:R < {min_rr:.0f}:1)*\n"
        text += "\n".join(_line(r) for r in developing[:15])

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": "🌀 HVF Watch — US Equities (US Monitor)"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": text.strip()[:2900]}},
        {"type": "context",
         "elements": [{"type": "mrkdwn",
                        "text": "_Trading runs via the monitor signal stack; this is the HVF visibility layer._ | "
                                + now_str}]},
    ]
    try:
        requests.post(slack_url, json={"blocks": blocks}, timeout=10)
        _hvf_save_fingerprint(current_fp)
    except Exception as e:
        log.error(f"HVF watch post failed: {e}")


def _generate_x_drafts(tradeable: list):
    """
    Post one tweet-ready draft per tradeable instrument to #claude-x-drafts
    (SLACK_TWITTER env var).

    Tweet format (≤280 chars — no pattern name, describe the setup naturally):
        📈 $TICKER (Company) — Volatility squeeze breaking {direction}, {tf} setup
        Entry: {h3}  Stop: {stop}  Target: {target}  R:R {rr}:1
        #StockAlert #TechnicalAnalysis #{TICKER} #Trading

    Chart: 90-day price history with the converging funnel drawn explicitly
    (upper + lower boundary lines narrowing to the breakout point, then
    entry/stop/target projected to the right).
    """
    import requests
    import io
    import base64
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import yfinance as _yf
    from datetime import datetime, timezone, timedelta
    from notify import fmt

    slack_url = os.environ.get("SLACK_TWITTER", "")
    if not slack_url:
        log.warning("SLACK_TWITTER not set — X draft reports skipped")
        return

    # ── Batch fetch latest signal context per ticker from signal_log ──────────
    # Enriches tweet with options flow / director buy confirmation when available.
    # Fails silently — absence of this data never blocks the draft post.
    _sig_ctx: dict = {}   # ticker → {options_bias, call_put_ratio, director_signal}
    try:
        tickers_in = tradeable[:10]
        ticker_list = [r.get("ticker", "") for r in tickers_in if r.get("ticker")]
        if ticker_list:
            placeholders = ", ".join(f"'{t}'" for t in ticker_list)
            conn = _pool_get_db()
            rows = conn.run(f"""
                SELECT DISTINCT ON (ticker)
                       ticker, options_bias, call_put_ratio, iv_rank, director_signal
                FROM signal_log
                WHERE ticker IN ({placeholders})
                ORDER BY ticker, session_time DESC
            """)
            conn.close()
            for row in rows:
                _sig_ctx[row[0]] = {
                    "options_bias":   row[1],
                    "call_put_ratio": row[2],
                    "iv_rank":        row[3],
                    "director_signal": row[4],
                }
    except Exception as e:
        log.debug(f"X drafts: signal_log lookup failed (non-critical): {e}")

    # Human-readable signal state labels (no pattern name)
    _SIG_LABEL = {
        "TRIGGERED": "breaking out",
        "READY":     "coiled, ready",
        "DEVELOPING": "compressing",
    }
    # Timeframe descriptions
    def _tf_desc(tf_raw: str) -> str:
        mapping = {"30d": "30-day", "60d": "60-day", "90d": "90-day",
                   "220d": "long-term", "weekly": "weekly"}
        return mapping.get(tf_raw, tf_raw or "multi-month")

    for r in tradeable[:10]:
        ticker    = r.get("ticker", "")
        direction = r.get("hvf_type", "BULLISH")
        signal    = r.get("hvf_signal", "")
        h3        = r.get("h3_level")
        stop      = r.get("stop_level")
        target    = r.get("target")
        rr        = r.get("risk_reward")
        quality   = r.get("hvf_quality") or r.get("pattern_quality") or ""
        tf_raw    = (r.get("hvf_timeframe", "") or "").replace("daily-", "d")
        name      = r.get("name") or ticker

        dir_emoji  = "📈" if direction == "BULLISH" else "📉"
        dir_word   = "higher" if direction == "BULLISH" else "lower"
        rr_str     = f"{rr:.1f}:1" if rr else "—"
        h3_str     = f"{h3:g}" if h3 else "—"
        stop_str   = f"{stop:g}" if stop else "—"
        tgt_str    = f"{target:g}" if target else "—"
        sig_desc   = _SIG_LABEL.get(signal, signal.lower())
        tf_desc    = _tf_desc(tf_raw)

        # ── Justification line from signal_log (options flow / director buys) ──
        ctx   = _sig_ctx.get(ticker, {})
        obs_b = ctx.get("options_bias") or ""
        cpr   = ctx.get("call_put_ratio")
        ivr   = ctx.get("iv_rank")
        dir_s = ctx.get("director_signal")

        justifications = []
        # Options flow — only include if aligned with trade direction
        aligned_options = (
            (direction == "BULLISH" and obs_b == "BULLISH") or
            (direction == "BEARISH" and obs_b == "BEARISH")
        )
        if obs_b and obs_b not in ("NEUTRAL", "") and aligned_options:
            # Build two versions: full (with IV rank) and short (without)
            bits_full  = []
            bits_short = []
            if cpr is not None:
                bits_full.append(f"call/put {float(cpr):.2f}")
                bits_short.append(f"call/put {float(cpr):.2f}")
            if ivr is not None:
                bits_full.append(f"implied volatility rank {float(ivr):.0f}%")
            detail_full  = f" ({', '.join(bits_full)})"  if bits_full  else ""
            detail_short = f" ({', '.join(bits_short)})" if bits_short else ""
            justifications.append((
                f"Options flow {obs_b.lower()}{detail_full}",   # full version
                f"Options flow {obs_b.lower()}{detail_short}",  # short fallback
            ))
        # Director buys — stored as plain string (same in both versions)
        if dir_s:
            justifications.append(("Insider buying on record", "Insider buying on record"))

        def _just_line(use_full: bool) -> str:
            idx = 0 if use_full else 1
            return "  ·  ".join(j[idx] for j in justifications)

        # ── Tweet text — try progressively shorter versions to fit 280 chars ──
        base_with_name = (
            f"{dir_emoji} ${ticker} ({name}) — Volatility squeeze {sig_desc} {dir_word}, {tf_desc} setup\n"
            f"Entry: {h3_str}  Stop: {stop_str}  Target: {tgt_str}  R:R {rr_str}\n"
        )
        base_no_name = (
            f"{dir_emoji} ${ticker} — Volatility squeeze {sig_desc} {dir_word}, {tf_desc}\n"
            f"Entry: {h3_str}  Stop: {stop_str}  Target: {tgt_str}  R:R {rr_str}\n"
        )
        # "Not financial advice." is always appended — user directive 2026-06-11.
        disclaimer = "\nNot financial advice."
        tags_long  = f"#StockAlert #TechnicalAnalysis #{ticker} #Trading"
        tags_short = f"#StockAlert #TechnicalAnalysis #{ticker}"

        def _build(base, just, tags):
            return base + (f"{just}\n" if just else "") + tags + disclaimer

        tweet = None
        for base in (base_with_name, base_no_name):
            for use_full in (True, False):
                just = _just_line(use_full) if justifications else ""
                for tags in (tags_long, tags_short):
                    candidate = _build(base, just, tags)
                    if len(candidate) <= 280:
                        tweet = candidate
                        break
                if tweet:
                    break
            if tweet:
                break
        if not tweet:
            # Absolute fallback — no justification, short tags
            tweet = base_no_name + tags_short + disclaimer

        # ── Chart: price history + funnel through real pivot points ─────────────
        chart_b64 = None
        try:
            end_dt   = datetime.now(timezone.utc)
            # Fetch from 14 days before the oldest pivot so H1/L1 are visible.
            # Fall back to 90 days when no pivot dates are present.
            _oldest_pivot_date = min(
                (pd.Timestamp(r[k]) for k in
                 ("h1_date", "h2_date", "h3_date", "l1_date", "l2_date", "l3_date")
                 if r.get(k)),
                default=None
            )
            if _oldest_pivot_date is not None:
                start_dt = _oldest_pivot_date - timedelta(days=14)
                # Cap at 365 days to avoid huge downloads; minimum 30 days
                start_dt = max(start_dt, end_dt - timedelta(days=365))
                start_dt = min(start_dt, end_dt - timedelta(days=30))
            else:
                start_dt = end_dt - timedelta(days=90)
            hist = _yf.download(ticker, start=start_dt.strftime("%Y-%m-%d"),
                                end=end_dt.strftime("%Y-%m-%d"),
                                progress=False, auto_adjust=True)
            if hist is not None and not hist.empty:
                # ig_scale normalisation
                ig_scale = 1.0
                if h3:
                    yf_med = float(hist["Close"].median())
                    if yf_med > 0 and h3 / yf_med > 5:
                        ig_scale = h3 / yf_med

                def _s(v):
                    return v / ig_scale if v else None

                h3_p   = _s(h3)
                stop_p = _s(stop)
                targ_p = _s(target)
                h1_p   = _s(r.get("h1_level"))
                h2_p   = _s(r.get("h2_level"))
                l1_p   = _s(r.get("l1_level"))
                l2_p   = _s(r.get("l2_level"))
                l3_p   = _s(r.get("l3_level") or stop)   # l3 = stop base

                # Convert pivot date strings to datetime for plotting
                def _pd(key):
                    ds = r.get(key)
                    if not ds:
                        return None
                    try:
                        return pd.Timestamp(ds)
                    except Exception:
                        return None

                h1_dt = _pd("h1_date"); h2_dt = _pd("h2_date"); h3_dt = _pd("h3_date")
                l1_dt = _pd("l1_date"); l2_dt = _pd("l2_date"); l3_dt = _pd("l3_date")

                close = hist["Close"].squeeze()
                dates = hist.index
                n     = len(dates)

                # ── Agreed X post card format (user-approved 2026-06-10) ──────
                # Combined card: tweet-text header panel above the chart.
                # Header: @handle / $TICKER (Name) / setup line / levels line /
                # hashtags / "Not financial advice." — chart fills the rest.
                fig = plt.figure(figsize=(12, 8.5))
                fig.patch.set_facecolor("#0d1117")
                ax = fig.add_axes([0.05, 0.06, 0.83, 0.62])
                ax.set_facecolor("#0d1117")

                dir_arrow = "▲" if direction == "BULLISH" else "▼"
                hdr_lines = [
                    (0.965, "@EndToEndTrading", "#1d9bf0", 13, "bold",   "normal"),
                    (0.925, f"{dir_arrow} ${ticker} ({name})",
                                                 "#ffffff", 16, "bold",   "normal"),
                    (0.885, f"Volatility squeeze {sig_desc} {dir_word} — "
                            f"{tf_raw or 'multi-month'} setup",
                                                 "#c9d1d9", 13, "normal", "normal"),
                    (0.845, f"Entry: {h3_str}   Stop: {stop_str}   "
                            f"Target: {tgt_str}   R:R {rr_str}",
                                                 "#c9d1d9", 12, "normal", "normal"),
                    (0.805, f"#StockAlert #TechnicalAnalysis #{ticker} #Trading",
                                                 "#8b949e", 11, "normal", "normal"),
                    (0.770, "Not financial advice.",
                                                 "#8b949e", 10, "normal", "italic"),
                ]
                for hy, htxt, hcol, hsize, hweight, hstyle in hdr_lines:
                    fig.text(0.05, hy, htxt, color=hcol, fontsize=hsize,
                             fontweight=hweight, style=hstyle,
                             ha="left", va="top")

                # Price line + fill
                ax.plot(dates, close, color="#58a6ff", linewidth=1.6, zorder=3)
                ax.fill_between(dates, close, float(close.min()), alpha=0.07,
                                color="#58a6ff", zorder=2)

                # ── Funnel: upper jaw H1→H2→H3 (red), lower jaw L1→L2→L3
                # (green) — through the actual pivot points so the lines sit
                # on the real swing highs/lows in the price history.
                upper_pts = [(dt, lv) for dt, lv in
                             [(h1_dt, h1_p), (h2_dt, h2_p), (h3_dt, h3_p)]
                             if dt is not None and lv is not None]
                lower_pts = [(dt, lv) for dt, lv in
                             [(l1_dt, l1_p), (l2_dt, l2_p), (l3_dt, l3_p)]
                             if dt is not None and lv is not None]

                if len(upper_pts) >= 2:
                    ux, uy = zip(*upper_pts)
                    ax.plot(ux, uy, color="#f85149", linewidth=1.4,
                            linestyle="--", alpha=0.9, zorder=4)
                    ax.scatter(ux, uy, color="#f85149", s=26, zorder=5, alpha=0.95)

                if len(lower_pts) >= 2:
                    lx, ly = zip(*lower_pts)
                    ax.plot(lx, ly, color="#3fb950", linewidth=1.4,
                            linestyle="--", alpha=0.9, zorder=4)
                    ax.scatter(lx, ly, color="#3fb950", s=26, zorder=5, alpha=0.95)

                # ── Entry / stop / target: full-width lines + right-edge
                # labels in matching colours (agreed card format).
                trans = ax.get_yaxis_transform()
                if h3_p:
                    ax.axhline(h3_p, color="#e3b341", linewidth=1.2,
                               linestyle="--", alpha=0.9, zorder=4)
                    ax.text(1.01, h3_p, f"Entry {h3_str}", transform=trans,
                            color="#e3b341", fontsize=9, va="center")
                if stop_p:
                    ax.axhline(stop_p, color="#f85149", linewidth=1.0,
                               linestyle=":", alpha=0.9, zorder=4)
                    ax.text(1.01, stop_p, f"Stop {stop_str}", transform=trans,
                            color="#f85149", fontsize=9, va="center")
                if targ_p:
                    ax.axhline(targ_p, color="#3fb950", linewidth=1.0,
                               linestyle=":", alpha=0.9, zorder=4)
                    ax.text(1.01, targ_p, f"Target {tgt_str}", transform=trans,
                            color="#3fb950", fontsize=9, va="center")

                ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
                ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
                plt.setp(ax.get_xticklabels(), rotation=0,
                         color="#8b949e", fontsize=9)
                plt.setp(ax.get_yticklabels(), color="#8b949e", fontsize=9)
                for spine in ax.spines.values():
                    spine.set_edgecolor("#30363d")
                ax.tick_params(colors="#8b949e")

                buf = io.BytesIO()
                plt.savefig(buf, format="png", dpi=140,
                            facecolor="#0d1117")
                plt.close(fig)
                buf.seek(0)
                chart_b64 = base64.b64encode(buf.read()).decode()
        except Exception as e:
            log.warning(f"X draft chart failed for {ticker}: {e}")

        # ── Post to SLACK_TWITTER channel ───────────────────────────────────────────
        dir_label = "Bullish" if direction == "BULLISH" else "Bearish"
        blocks = [
            {"type": "header",
             "text": {"type": "plain_text",
                      "text": f"X Draft — {fmt(ticker)} {dir_label} · {sig_desc.title()}"}},
            {"type": "section",
             "text": {"type": "mrkdwn",
                      "text": f"*Tweet ({len(tweet)} chars):*\n```{tweet}```"}},
            {"type": "context",
             "elements": [{"type": "mrkdwn",
                            "text": (f"R:R {rr_str}  |  Quality: {quality or '—'}  |  "
                                     f"{tf_raw or '—'}  |  "
                                     + datetime.now(timezone.utc).strftime("%d %b %H:%M UTC"))}]},
        ]
        bot_token  = os.environ.get("SLACK_BOT_TOKEN", "")
        channel_id = os.environ.get("SLACK_TWITTER_CHANNEL_ID", "")
        if chart_b64 and not (bot_token and channel_id):
            blocks.insert(2, {
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": "_Chart generated but not attached — "
                                 "SLACK_BOT_TOKEN / SLACK_TWITTER_CHANNEL_ID not set_"}
            })

        try:
            requests.post(slack_url, json={"blocks": blocks}, timeout=10)
            log.info(f"X draft posted to SLACK_TWITTER for {ticker} ({len(tweet)} chars)")
        except Exception as e:
            log.error(f"X draft Slack post failed (SLACK_TWITTER) for {ticker}: {e}")

        # ── Attach the post card image via Slack external upload flow ────────
        # Legacy files.upload is retired (2025) — the current flow is:
        # 1. files.getUploadURLExternal  → one-time upload URL + file id
        # 2. POST raw bytes to that URL
        # 3. files.completeUploadExternal → finalise + share into the channel
        if chart_b64 and bot_token and channel_id:
            try:
                png_bytes = base64.b64decode(chart_b64)
                fname     = f"x_post_{ticker.replace('.', '_')}.png"
                hdrs      = {"Authorization": f"Bearer {bot_token}"}

                r1 = requests.post(
                    "https://slack.com/api/files.getUploadURLExternal",
                    headers=hdrs,
                    data={"filename": fname, "length": len(png_bytes)},
                    timeout=10,
                ).json()
                if not r1.get("ok"):
                    raise RuntimeError(f"getUploadURLExternal: {r1.get('error')}")

                r2 = requests.post(r1["upload_url"], data=png_bytes, timeout=30)
                r2.raise_for_status()

                r3 = requests.post(
                    "https://slack.com/api/files.completeUploadExternal",
                    headers={**hdrs, "Content-Type": "application/json"},
                    json={"files": [{"id": r1["file_id"],
                                     "title": f"X post card — {ticker} ({name})"}],
                          "channel_id": channel_id},
                    timeout=10,
                ).json()
                if not r3.get("ok"):
                    raise RuntimeError(f"completeUploadExternal: {r3.get('error')}")
                log.info(f"X draft chart attached for {ticker} "
                         f"({len(png_bytes)} bytes → {channel_id})")
            except Exception as e:
                log.error(f"X draft chart upload failed for {ticker}: {e}")


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
    from ig_shim import (open_trade, get_account_balance,
                         place_hvf_order_from_sig, reconcile_working_orders)
    from config import SESSION_INSTRUMENTS, MAX_TRADES_PER_SESSION, SESSION_TRADE_CAPS
    from notify import fmt, should_post_summary   # name fmt + 2h summary gate

    results = []

    # ── Reconcile pending HVF working orders first ────────────────────────────
    # A fill inserts the position row (so the DB fetch below sees it and Part 1
    # monitors it); cancels/expiries are surfaced to Slack — nothing ends silently.
    try:
        wo_sum = reconcile_working_orders()
        if wo_sum["filled"] or wo_sum["cancelled"] or wo_sum["expired"]:
            log.info(f"US Monitor: working orders — filled {wo_sum['filled']}, "
                     f"cancelled {wo_sum['cancelled']}, expired {wo_sum['expired']}")
    except Exception as e:
        log.warning(f"US Monitor: working-order reconcile failed: {e}")

    # ── DB connection ─────────────────────────────────────────────────────────
    try:
        conn = _pool_get_db()
        pos_rows = conn.run(
            "select ticker, direction, open_price, stop_loss, deal_id from positions"
        )
        # Trades already placed today: closed (trade_log) + still open (positions,
        # previously missing from the count) + pending working orders placed today.
        today_count = conn.run(
            """select
                 (select count(*) from trade_log
                    where session like 'US%' and date(opened_at) = current_date)
               + (select count(*) from positions
                    where session like 'US%' and date(opened_at) = current_date)
               + (select count(*) from working_orders
                    where session like 'US%' and status = 'PENDING'
                      and date(placed_at) = current_date)"""
        )
        conn.close()
    except Exception as e:
        log.error(f"Could not fetch positions: {e}")
        return results

    open_tickers    = {r[0] for r in pos_rows}
    trades_today    = int(today_count[0][0]) if today_count else 0   # US-session trades today
    slots_remaining = max(0, SESSION_TRADE_CAPS.get("US", MAX_TRADES_PER_SESSION) - trades_today)

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
    # NOTE: HVF watch (visibility layer) is now a separate workflow
    # (trading-us-hvf-watch.yml, session US_HVF_WATCH) running every 2 hours.
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
                            from run_session import get_user_profile
                            profile = get_user_profile()

                            # HVF setups → pending working order at the pattern's
                            # exact entry/stop/target (re-signal = amend, never a
                            # duplicate). No fall-through to a market order.
                            _hvf_dir_ok = ((sig.get("hvf_type") == "BULLISH" and sig["direction"] == "BUY") or
                                           (sig.get("hvf_type") == "BEARISH" and sig["direction"] == "SELL"))
                            if _hvf_dir_ok and sig.get("hvf_signal") in ("READY", "TRIGGERED") and \
                                    sig.get("hvf_h3_level") and sig.get("hvf_stop_level") and sig.get("hvf_target"):
                                wo = place_hvf_order_from_sig(
                                    sig, profile, "US_MONITOR",
                                    macro.get("stress_size_multiplier", 1.0))
                                if wo and not wo.get("updated"):
                                    new_trades_placed += 1
                                continue

                            stop_dist  = sig.get("stop_distance", 0)
                            limit_dist = round(stop_dist * DEFAULT_TARGET_RR, 4)
                            try:
                                bal         = get_account_balance()
                                risk_amount = bal["available"] * 0.02
                                size        = round(risk_amount / stop_dist, 1) if stop_dist > 0 else 0.0
                                size        = max(0.5, min(size, 10.0)) if size > 0 else 0.0
                            except Exception:
                                size = 0.0  # skip trade on error — 0.5 fallback caused INSUFFICIENT_FUNDS
                            from signals import conf_names
                            _confs = conf_names(sig)
                            signal_str = (
                                f"Options:{sig.get('options_bias','—')} "
                                f"BB:{sig.get('bb_breakout_dir','—')} "
                                f"COT:{sig.get('cot_bias','—')} "
                                f"PA:{sig.get('pa_verdict','—')} "
                                f"Confs:{sig.get('confirmation_count',0)}"
                                + (f" ({_confs})" if _confs else "") +
                                f" [intraday rescan]"
                            )
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
                                try:
                                    from trade_email import send_trade_email
                                    send_trade_email(ticker, sig["direction"], sig, result,
                                                     size=size, session_name="US_MONITOR")
                                except Exception as e:
                                    log.warning(f"Trade email failed for {ticker}: {e}")
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
                    f"• *{fmt(s['ticker'])}* {s['direction']} — ⚠️ {s['alert']}\n"
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

    # Periodic session summary to #signals — gated to <= every 2h (monitoring runs
    # every 5 min, but the full review must not spam the channel). The position
    # alerts above are immediate and NOT gated. See user directive 2026-06-09.
    if notify_slack and should_post_summary():
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
                lines += f"{flag} *{fmt(s['ticker'])}* | RSI:{rsi} | MACD:{macd} | VWAP:{vwap} | Trend:{trend} | Vol:{vol}\n"

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
