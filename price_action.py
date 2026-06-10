# =============================================================================
# File:         price_action.py
# Author:       Alex Hind
# Created:      2026-05-30
#
# Description:
# -----------------------------------------------------------------------------
# Price action confirmation signals for the EndToEndTrading system.
#
# PURPOSE — Avoiding the falling knife
# ─────────────────────────────────────────────────────────────────────────────
# COT data and macro fundamentals tell you WHAT to trade and in WHICH
# DIRECTION. Price action tells you WHEN to enter.
#
# Even if COT is bullish and fundamentals are aligned, you do not enter
# until price confirms. This is the difference between analysis and execution.
#
# Without price confirmation you risk:
#   - Buying into a structural downtrend that hasn't reversed yet
#   - Catching a falling knife — fundamentals turn bullish but price keeps falling
#   - Poor timing that turns a correct thesis into a losing trade
#
# Signals implemented:
# ─────────────────────────────────────────────────────────────────────────────
#
#   1. Range Breakout
#      Price breaks above a multi-month consolidation high (bullish)
#      or below a consolidation low (bearish).
#      Uses the highest high and lowest low of the past 3 months (60 trading days).
#      Most reliable when accompanied by expanding volume.
#
#   2. Trend Structure — Higher Highs / Higher Lows
#      A bullish trend is confirmed by a series of higher swing highs and
#      higher swing lows. A bearish trend by lower highs and lower lows.
#      Computed over the last 10 weekly candles.
#      The cleanest entry is on a pullback to a higher low, not at the high.
#
#   3. Volatility Compression → Expansion (Enhanced BB Squeeze)
#      Already computed in signals.py (BB squeeze). Here we also check
#      ATR contraction as a second compression measure.
#      ATR dropping to a 3-month low then expanding = high-probability breakout.
#
#   4. Moving Average Crossovers (20 / 50 / 200 SMA)
#      20 SMA > 50 SMA > 200 SMA = full bullish alignment ("Golden alignment")
#      Price above 200 SMA = long-term uptrend
#      Price crossing 50 SMA from below = medium-term momentum turning bullish
#      Used as a filter — not a signal on its own.
#
#   5. Failed Breakdown (Bullish)
#      Price breaks below a key support level (multi-month low) but then
#      closes back above it. The breakdown failed — sellers tried and failed.
#      One of the most reliable bullish signals in commodities.
#
#   6. Failed Breakout (Bearish)
#      Price breaks above a key resistance level but then closes back below.
#      Buyers tried and failed — bearish signal.
#
# Composite Price Action Score:
# ─────────────────────────────────────────────────────────────────────────────
#   Each signal contributes to a score from -100 to +100.
#   CONFIRM_LONG  = score >= +40  (price structure supports a long entry)
#   CONFIRM_SHORT = score <= -40  (price structure supports a short entry)
#   WAIT          = between -40 and +40 (no clear confirmation — do not enter)
#
# Data source: Yahoo Finance OHLCV (daily and weekly candles)
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-05-30  Alex Hind   Initial build. Six price action signals
#                                 with composite confirmation score.
# 1.1.0   2026-06-05  Alex Hind   Raised HVF_MIN_RR from 2.0 to 2.5 in both
#                                 single-timeframe and multi-timeframe scanners
#                                 to match MIN_RISK_REWARD in config.py.
# 1.3.0   2026-06-06  Alex Hind   Fix 1: remove `if thresh != default` guard so
#                                 config threshold is always authoritative —
#                                 previously changing PA_CONFIRM_THRESHOLD_DEFAULT
#                                 had zero effect on standard equities/indices.
#                                 Fix 2 (HVF bypass): extend to cover direction
#                                 conflicts: previously only fired when verdict==WAIT,
#                                 so a low-threshold crypto with pa_score=+22 would
#                                 get CONFIRM_LONG even while HVF was BEARISH+TRIGGERED.
#                                 Now: always apply HVF direction, force WAIT when HVF
#                                 direction conflicts with PA score at bypass threshold.
# 1.4.0   2026-06-06  Alex Hind   _run_hvf_on_hist: add allow_bearish_override guard
#                                 matching get_hvf_signal. Without the guard,
#                                 STRONG_UPTREND weekly charts had their trend
#                                 overridden to DOWNTREND whenever strict_dec or
#                                 peak_dec fired — generating spurious short signals
#                                 even on a confirmed strong uptrend.
# 1.2.0   2026-06-05  Alex Hind   Per-instrument PA threshold (Fix 1): crypto
#                                 now uses threshold=25, FX/commodities 30-35,
#                                 equities/indices keep default 40.
#                                 HVF TRIGGERED bypass (Fix 2): threshold halved
#                                 when HVF has confirmed entry — price has voted.
#
# Dependencies:
# -----------------------------------------------------------------------------
#   pip install yfinance pandas numpy
# =============================================================================

import logging
import numpy as np
import pandas as pd
import yfinance as yf

from config import YAHOO_MAP, HVF_MIN_RR, PA_CONFIRM_THRESHOLDS, PA_CONFIRM_THRESHOLD_DEFAULT

log = logging.getLogger("price_action")

# Score threshold to confirm entry
CONFIRM_LONG_THRESHOLD  =  40
CONFIRM_SHORT_THRESHOLD = -40

# Lookback periods
RANGE_BREAKOUT_DAYS  = 60    # 3 months for range definition
TREND_STRUCTURE_WKLY = 10    # 10 weekly candles for HH/HL analysis
MA_CROSSOVER_DAYS    = 210   # enough for 200 SMA
FAILED_BREAK_DAYS    = 5     # candles to look back for failed break


# =============================================================================
# Data fetching helper
# =============================================================================

def _get_daily(ticker: str, days: int = 220) -> pd.DataFrame:
    """Fetch daily OHLCV data for a ticker. Returns empty DataFrame on failure."""
    yticker = YAHOO_MAP.get(ticker, ticker)
    try:
        t    = yf.Ticker(yticker)
        hist = t.history(period=f"{days + 30}d", interval="1d")
        return hist.tail(days) if len(hist) >= days else hist
    except Exception as e:
        log.warning(f"Daily data fetch failed for {ticker}: {e}")
        return pd.DataFrame()


def _get_weekly(ticker: str, weeks: int = 30) -> pd.DataFrame:
    """Fetch weekly OHLCV data for a ticker. Returns empty DataFrame on failure."""
    yticker = YAHOO_MAP.get(ticker, ticker)
    try:
        t    = yf.Ticker(yticker)
        hist = t.history(period=f"{weeks * 7 + 30}d", interval="1wk")
        return hist.tail(weeks) if len(hist) >= weeks else hist
    except Exception as e:
        log.warning(f"Weekly data fetch failed for {ticker}: {e}")
        return pd.DataFrame()


# =============================================================================
# Signal 1 — Range Breakout
# =============================================================================

def get_range_breakout(ticker: str) -> dict:
    """
    Detect a breakout from a multi-month consolidation range.

    Bullish: price closes above the 60-day highest high
    Bearish: price closes below the 60-day lowest low

    Returns:
        signal:      BULLISH_BREAKOUT / BEARISH_BREAKOUT / NONE
        range_high:  60-day high (resistance)
        range_low:   60-day low  (support)
        last_close:  current closing price
        pct_vs_high: how close to the high (%)
        pct_vs_low:  how close to the low (%)
    """
    result = {
        "signal":      "NONE",
        "range_high":  None,
        "range_low":   None,
        "last_close":  None,
        "pct_vs_high": None,
        "pct_vs_low":  None,
    }
    hist = _get_daily(ticker, days=RANGE_BREAKOUT_DAYS + 5)
    if hist.empty or len(hist) < 10:
        return result

    # Use all but the last candle to define the range
    range_data  = hist.iloc[:-1]
    current     = hist.iloc[-1]

    range_high  = float(range_data["High"].max())
    range_low   = float(range_data["Low"].min())
    last_close  = float(current["Close"])

    result["range_high"] = round(range_high, 4)
    result["range_low"]  = round(range_low,  4)
    result["last_close"] = round(last_close, 4)
    result["pct_vs_high"] = round((last_close - range_high) / range_high * 100, 2)
    result["pct_vs_low"]  = round((last_close - range_low)  / range_low  * 100, 2)

    if last_close > range_high:
        result["signal"] = "BULLISH_BREAKOUT"
    elif last_close < range_low:
        result["signal"] = "BEARISH_BREAKOUT"

    return result


# =============================================================================
# Signal 2 — Trend Structure (Higher Highs / Higher Lows)
# =============================================================================

def get_trend_structure(ticker: str) -> dict:
    """
    Assess trend structure using weekly swing highs and lows.

    Bullish trend: series of higher swing highs and higher swing lows
    Bearish trend: series of lower swing highs and lower swing lows

    Uses last 10 weekly candles. Counts how many consecutive higher/lower
    pivots appear to classify trend strength.

    Returns:
        signal:         STRONG_UPTREND / UPTREND / DOWNTREND / STRONG_DOWNTREND / SIDEWAYS
        hh_hl_count:    number of higher high / higher low pairs (positive = bullish)
        lh_ll_count:    number of lower high / lower low pairs (positive = bearish)
        last_weekly_close: most recent weekly close
    """
    result = {
        "signal":             "SIDEWAYS",
        "hh_hl_count":        0,
        "lh_ll_count":        0,
        "last_weekly_close":  None,
    }
    hist = _get_weekly(ticker, weeks=TREND_STRUCTURE_WKLY + 4)
    if hist.empty or len(hist) < 4:
        return result

    closes = hist["Close"].values
    highs  = hist["High"].values
    lows   = hist["Low"].values

    result["last_weekly_close"] = round(float(closes[-1]), 4)

    # Count higher highs + higher lows vs lower highs + lower lows
    hh_hl = 0
    lh_ll = 0

    for i in range(2, len(hist)):
        hh = highs[i] > highs[i-1]
        hl = lows[i]  > lows[i-1]
        lh = highs[i] < highs[i-1]
        ll = lows[i]  < lows[i-1]

        if hh and hl:
            hh_hl += 1
        if lh and ll:
            lh_ll += 1

    result["hh_hl_count"] = hh_hl
    result["lh_ll_count"] = lh_ll

    # Classify
    if hh_hl >= 5:
        result["signal"] = "STRONG_UPTREND"
    elif hh_hl >= 3:
        result["signal"] = "UPTREND"
    elif lh_ll >= 5:
        result["signal"] = "STRONG_DOWNTREND"
    elif lh_ll >= 3:
        result["signal"] = "DOWNTREND"
    else:
        result["signal"] = "SIDEWAYS"

    return result


# =============================================================================
# Signal 3 — ATR Compression / Expansion
# =============================================================================

def get_atr_compression(ticker: str, period: int = 14) -> dict:
    """
    Detect ATR compression followed by expansion.

    ATR at a 3-month low = volatility compressed = coiling for a breakout.
    ATR expanding after a period of compression = breakout confirmed.

    This is the ATR equivalent of the Bollinger Band squeeze in signals.py.
    Together they provide two independent compression measures.

    Returns:
        compressed:       True if ATR is at or near its 3-month low
        expanding:        True if ATR increased this week vs last week
        atr_current:      current 14-period ATR
        atr_pct_rank:     percentile rank of current ATR vs 60 days
    """
    result = {
        "compressed":    False,
        "expanding":     False,
        "atr_current":   None,
        "atr_pct_rank":  50.0,
    }
    hist = _get_daily(ticker, days=80)
    if hist.empty or len(hist) < period + 5:
        return result

    high  = hist["High"]
    low   = hist["Low"]
    close = hist["Close"]

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(period).mean().dropna()

    if len(atr) < 5:
        return result

    current_atr = float(atr.iloc[-1])
    prev_atr    = float(atr.iloc[-2])
    atr_series  = atr.values

    # Percentile rank — low rank = compressed
    pct_rank    = float(np.sum(atr_series <= current_atr) / len(atr_series) * 100)

    result["atr_current"]  = round(current_atr, 4)
    result["atr_pct_rank"] = round(pct_rank, 1)
    result["compressed"]   = pct_rank <= 20     # ATR in bottom 20th percentile
    result["expanding"]    = current_atr > prev_atr and pct_rank <= 30

    return result


# =============================================================================
# Signal 4 — Moving Average Alignment
# =============================================================================

def get_ma_alignment(ticker: str) -> dict:
    """
    Assess moving average structure using 20, 50, and 200 SMA.

    Full bullish alignment: 20 SMA > 50 SMA > 200 SMA AND price above all three
    Full bearish alignment: 20 SMA < 50 SMA < 200 SMA AND price below all three

    Returns:
        signal:      FULL_BULL / PARTIAL_BULL / NEUTRAL / PARTIAL_BEAR / FULL_BEAR
        price:       current price
        sma20:       20-day SMA
        sma50:       50-day SMA
        sma200:      200-day SMA
        price_vs_200: % above/below 200 SMA (long-term context)
        golden_cross: True if 50 SMA recently crossed above 200 SMA (within 20 days)
        death_cross:  True if 50 SMA recently crossed below 200 SMA
    """
    result = {
        "signal":       "NEUTRAL",
        "price":        None,
        "sma20":        None,
        "sma50":        None,
        "sma200":       None,
        "price_vs_200": None,
        "golden_cross": False,
        "death_cross":  False,
    }
    hist = _get_daily(ticker, days=MA_CROSSOVER_DAYS)
    if hist.empty or len(hist) < 200:
        return result

    close  = hist["Close"]
    sma20  = float(close.rolling(20).mean().iloc[-1])
    sma50  = float(close.rolling(50).mean().iloc[-1])
    sma200 = float(close.rolling(200).mean().iloc[-1])
    price  = float(close.iloc[-1])

    result["price"]        = round(price,  4)
    result["sma20"]        = round(sma20,  4)
    result["sma50"]        = round(sma50,  4)
    result["sma200"]       = round(sma200, 4)
    result["price_vs_200"] = round((price - sma200) / sma200 * 100, 2)

    # Check for recent golden/death cross (50 SMA vs 200 SMA in last 20 days)
    sma50_series  = close.rolling(50).mean().dropna()
    sma200_series = close.rolling(200).mean().dropna()
    if len(sma50_series) >= 20 and len(sma200_series) >= 20:
        recent_50  = sma50_series.iloc[-20:]
        recent_200 = sma200_series.iloc[-20:]
        diff       = recent_50.values - recent_200.values
        crosses    = np.diff(np.sign(diff))
        if any(crosses > 0):
            result["golden_cross"] = True
        if any(crosses < 0):
            result["death_cross"]  = True

    # Determine signal
    bulls = sum([price > sma20, price > sma50, price > sma200, sma20 > sma50, sma50 > sma200])
    bears = sum([price < sma20, price < sma50, price < sma200, sma20 < sma50, sma50 < sma200])

    if bulls == 5:
        result["signal"] = "FULL_BULL"
    elif bulls >= 3:
        result["signal"] = "PARTIAL_BULL"
    elif bears == 5:
        result["signal"] = "FULL_BEAR"
    elif bears >= 3:
        result["signal"] = "PARTIAL_BEAR"
    else:
        result["signal"] = "NEUTRAL"

    return result


# =============================================================================
# Signal 5 — Candlestick Patterns (daily bars)
# =============================================================================

def get_candlestick_pattern(ticker: str) -> dict:
    """
    Detect high-conviction single and two-bar candlestick patterns on daily bars.

    Patterns detected:
      BULLISH_ENGULFING  Today's bull candle fully engulfs yesterday's bear candle.
                         Strong reversal — institutional buying absorbed all sellers.
      BEARISH_ENGULFING  Today's bear candle fully engulfs yesterday's bull candle.
      HAMMER             Long lower wick (≥2× body), close near high.
                         Rejection of lower prices — buyers stepped in.
      SHOOTING_STAR      Long upper wick (≥2× body), close near low.
                         Rejection of higher prices — sellers stepped in.
      MARUBOZU_BULL      Near-bodyless candle, close at/near high, high volume.
                         Pure conviction — no wick = no hesitation.
      MARUBOZU_BEAR      Close at/near low, high volume.
      NONE               No significant pattern.
    """
    result = {"pattern": "NONE", "pattern_direction": None, "description": ""}
    hist = _get_daily(ticker, days=5)
    if len(hist) < 2:
        return result

    today = hist.iloc[-1]
    prev  = hist.iloc[-2]

    t_open  = float(today["Open"])
    t_close = float(today["Close"])
    t_high  = float(today["High"])
    t_low   = float(today["Low"])
    p_open  = float(prev["Open"])
    p_close = float(prev["Close"])

    t_body  = abs(t_close - t_open)
    t_range = t_high - t_low
    if t_range == 0:
        return result

    t_bull = t_close > t_open
    t_bear = t_close < t_open
    p_bull = p_close > p_open
    p_bear = p_close < p_open

    lower_wick = (min(t_open, t_close) - t_low)
    upper_wick = (t_high - max(t_open, t_close))

    # ── Bullish engulfing ─────────────────────────────────────────────────
    if t_bull and p_bear and t_open < p_close and t_close > p_open:
        result["pattern"]           = "BULLISH_ENGULFING"
        result["pattern_direction"] = "BULLISH"
        result["description"]       = (
            "Bullish engulfing — today's buying completely absorbed yesterday's selling. "
            "High-conviction reversal signal."
        )
        return result

    # ── Bearish engulfing ─────────────────────────────────────────────────
    if t_bear and p_bull and t_open > p_close and t_close < p_open:
        result["pattern"]           = "BEARISH_ENGULFING"
        result["pattern_direction"] = "BEARISH"
        result["description"]       = (
            "Bearish engulfing — today's selling completely absorbed yesterday's buying. "
            "High-conviction reversal signal."
        )
        return result

    # ── Hammer (bullish) ──────────────────────────────────────────────────
    if (lower_wick >= 2 * t_body and
            upper_wick <= 0.3 * t_body and
            t_body > 0):
        result["pattern"]           = "HAMMER"
        result["pattern_direction"] = "BULLISH"
        result["description"]       = (
            f"Hammer — lower wick {lower_wick/t_body:.1f}× body. "
            "Buyers rejected lower prices; sellers exhausted."
        )
        return result

    # ── Shooting star (bearish) ────────────────────────────────────────────
    if (upper_wick >= 2 * t_body and
            lower_wick <= 0.3 * t_body and
            t_body > 0):
        result["pattern"]           = "SHOOTING_STAR"
        result["pattern_direction"] = "BEARISH"
        result["description"]       = (
            f"Shooting star — upper wick {upper_wick/t_body:.1f}× body. "
            "Sellers rejected higher prices; buyers exhausted."
        )
        return result

    # ── Marubozu bull (full conviction) ───────────────────────────────────
    if (t_bull and
            lower_wick <= 0.05 * t_range and
            upper_wick <= 0.05 * t_range):
        result["pattern"]           = "MARUBOZU_BULL"
        result["pattern_direction"] = "BULLISH"
        result["description"]       = "Bullish Marubozu — no wicks, pure buying conviction."
        return result

    # ── Marubozu bear ─────────────────────────────────────────────────────
    if (t_bear and
            lower_wick <= 0.05 * t_range and
            upper_wick <= 0.05 * t_range):
        result["pattern"]           = "MARUBOZU_BEAR"
        result["pattern_direction"] = "BEARISH"
        result["description"]       = "Bearish Marubozu — no wicks, pure selling conviction."
        return result

    return result


# =============================================================================
# Signal 6 & 7 — Failed Breakdown (Bullish) / Failed Breakout (Bearish)
# =============================================================================

def get_failed_break(ticker: str) -> dict:
    """
    Detect failed breakdowns (bullish) and failed breakouts (bearish).

    Failed Breakdown (BULLISH):
        Price briefly closes below the 60-day range low (triggers stops)
        but within 1-3 candles recovers back above it.
        This traps short sellers and often leads to sharp moves higher.
        One of the highest-conviction signals in commodity markets.

    Failed Breakout (BEARISH):
        Price briefly closes above the 60-day range high
        but within 1-3 candles falls back below it.
        Traps late buyers — bearish signal.

    Returns:
        signal:         FAILED_BREAKDOWN / FAILED_BREAKOUT / NONE
        level:          the support/resistance level that was tested
        candles_since:  how many candles since the failed break
        description:    plain English narrative
    """
    result = {
        "signal":       "NONE",
        "level":        None,
        "candles_since": None,
        "description":  "",
    }
    hist = _get_daily(ticker, days=RANGE_BREAKOUT_DAYS + FAILED_BREAK_DAYS + 5)
    if hist.empty or len(hist) < RANGE_BREAKOUT_DAYS + 3:
        return result

    # Range defined on the 60-day window before the last 5 candles
    range_window = hist.iloc[-(RANGE_BREAKOUT_DAYS + FAILED_BREAK_DAYS):-(FAILED_BREAK_DAYS)]
    recent       = hist.iloc[-FAILED_BREAK_DAYS:]

    range_high = float(range_window["High"].max())
    range_low  = float(range_window["Low"].min())

    # Check each recent candle for a failed break
    for i, (idx, row) in enumerate(recent.iterrows()):
        # Failed breakdown: close went below range_low but current price is back above
        if float(row["Close"]) < range_low:
            # Check if subsequent candles recovered above range_low
            subsequent = recent.iloc[i+1:]
            if not subsequent.empty and float(subsequent["Close"].iloc[-1]) > range_low:
                result["signal"]       = "FAILED_BREAKDOWN"
                result["level"]        = round(range_low, 4)
                result["candles_since"] = len(subsequent)
                result["description"]  = (
                    f"Failed breakdown below {range_low:.4f} — "
                    f"sellers rejected, price recovered in {len(subsequent)} candle(s)"
                )
                return result

        # Failed breakout: close went above range_high but current price is back below
        if float(row["Close"]) > range_high:
            subsequent = recent.iloc[i+1:]
            if not subsequent.empty and float(subsequent["Close"].iloc[-1]) < range_high:
                result["signal"]       = "FAILED_BREAKOUT"
                result["level"]        = round(range_high, 4)
                result["candles_since"] = len(subsequent)
                result["description"]  = (
                    f"Failed breakout above {range_high:.4f} — "
                    f"buyers rejected, price fell back in {len(subsequent)} candle(s)"
                )
                return result

    return result


# =============================================================================
# Composite Price Action Score and Confirmation
# =============================================================================

def compute_price_action_score(
    range_breakout:  dict,
    trend_structure: dict,
    atr_compression: dict,
    ma_alignment:    dict,
    failed_break:    dict,
    candlestick:     dict = None,
) -> tuple[float, str]:
    """
    Combine all price action signals into a composite score (-100 to +100)
    and a confirmation verdict.

    Weighting:
        Range breakout:       ±30  (high conviction directional signal)
        Trend structure:      ±25  (structural context)
        MA alignment:         ±20  (trend filter)
        Failed break:         ±15  (high conviction reversal)
        ATR compression:      ±10  (timing confirmation)

    Returns:
        score:   float from -100 to +100
        verdict: CONFIRM_LONG / CONFIRM_SHORT / WAIT
    """
    score = 0.0

    # ── Range breakout ────────────────────────────────────────────────────
    breakout_scores = {
        "BULLISH_BREAKOUT": +30,
        "BEARISH_BREAKOUT": -30,
        "NONE":               0,
    }
    score += breakout_scores.get(range_breakout.get("signal", "NONE"), 0)

    # ── Trend structure ───────────────────────────────────────────────────
    trend_scores = {
        "STRONG_UPTREND":    +25,
        "UPTREND":           +15,
        "SIDEWAYS":            0,
        "DOWNTREND":         -15,
        "STRONG_DOWNTREND":  -25,
    }
    score += trend_scores.get(trend_structure.get("signal", "SIDEWAYS"), 0)

    # ── MA alignment ──────────────────────────────────────────────────────
    ma_scores = {
        "FULL_BULL":    +20,
        "PARTIAL_BULL": +10,
        "NEUTRAL":        0,
        "PARTIAL_BEAR": -10,
        "FULL_BEAR":    -20,
    }
    score += ma_scores.get(ma_alignment.get("signal", "NEUTRAL"), 0)

    # Golden/death cross bonus
    if ma_alignment.get("golden_cross"):
        score += 5
    if ma_alignment.get("death_cross"):
        score -= 5

    # ── Failed break (high conviction reversal) ───────────────────────────
    failed_scores = {
        "FAILED_BREAKDOWN": +15,
        "FAILED_BREAKOUT":  -15,
        "NONE":               0,
    }
    score += failed_scores.get(failed_break.get("signal", "NONE"), 0)

    # ── ATR compression + expansion (timing) ─────────────────────────────
    if atr_compression.get("expanding") and atr_compression.get("compressed"):
        score += 10     # Compressed AND expanding = breakout confirmed
    elif atr_compression.get("compressed"):
        score += 5      # Still coiling — slight timing bonus

    # ── Candlestick pattern ───────────────────────────────────────────────
    if candlestick:
        candle_scores = {
            "BULLISH_ENGULFING": +15,
            "BEARISH_ENGULFING": -15,
            "HAMMER":            +10,
            "SHOOTING_STAR":     -10,
            "MARUBOZU_BULL":     +12,
            "MARUBOZU_BEAR":     -12,
            "NONE":                0,
        }
        score += candle_scores.get(candlestick.get("pattern", "NONE"), 0)

    score = round(max(-100, min(100, score)), 1)

    if score >= CONFIRM_LONG_THRESHOLD:
        verdict = "CONFIRM_LONG"
    elif score <= CONFIRM_SHORT_THRESHOLD:
        verdict = "CONFIRM_SHORT"
    else:
        verdict = "WAIT"

    return score, verdict


# =============================================================================
# Signal 8 — Hunt Volatility Funnel (HVF)
#
# The HVF is a continuation breakout pattern developed by Francis Hunt
# (TheMarketSniper). It is significantly more rigorous than a standard
# BB squeeze because it requires:
#
#   1. A clear prior trend (continuation, not reversal)
#   2. Exactly 3 alternating inflection points:
#        Lower Highs: H1 > H2 > H3
#        Higher Lows: L1 < L2 < L3
#      Volatility contracts into a funnel shape.
#   3. All levels are horizontal (pivot highs/lows), not trendline-based.
#   4. Entry: pending buy-stop at H3 (bullish) or sell-stop at L3 (bearish)
#   5. Stop: horizontal level just below L3 (bullish) or above H3 (bearish)
#   6. Target: H1-L1 distance measured from the midpoint of H3/L3
#      (often gives 3-5× R:R on quality setups — much better than 2× ATR)
#
# This replaces / upgrades our existing BB squeeze primary signal.
# =============================================================================

def _find_swing_highs_lows(hist: pd.DataFrame, n: int = 5) -> tuple:
    """
    Find significant swing highs and lows using a lookback window of N bars.

    A swing high: the High at bar i is the maximum in the window [i-n, i+n]
                  AND it is higher than the immediate neighbours.
    A swing low:  the Low  at bar i is the minimum in the same window
                  AND it is lower than the immediate neighbours.

    N=5 on daily data gives 11-bar pivots — filters out minor noise while
    capturing the significant turns that form HVF inflection points.

    Spike wick filter (swing lows):
        If a bar's close is more than 40% of its range above its low, that low
        is a wick/spike rather than a structural support level — it represents
        a brief intraday rejection, not where price actually settled.
        Such bars are excluded from swing low candidates.
        Example: BP.L 26-May-2026 — Low 500p, Close 521p, range 51p.
        Wick = 21p = 41% of range → excluded. Structural lows (512→525→535)
        correctly identified as rising, revealing the bearish HVF funnel.

    Returns:
        swing_highs: list of (bar_index, price) tuples, oldest first
        swing_lows:  list of (bar_index, price) tuples, oldest first
    """
    swing_highs = []
    swing_lows  = []

    highs  = hist["High"].values
    lows   = hist["Low"].values
    closes = hist["Close"].values
    n_bars = len(hist)

    for i in range(n, n_bars - n):
        window_hi = highs[i - n : i + n + 1]
        window_lo = lows[i  - n : i + n + 1]

        if (highs[i] == window_hi.max()
                and highs[i] > highs[i - 1]
                and highs[i] > highs[i + 1]):
            swing_highs.append((i, float(highs[i])))

        if (lows[i] == window_lo.min()
                and lows[i] < lows[i - 1]
                and lows[i] < lows[i + 1]):
            # Spike-wick filter (see docstring): if the close sits more than 40%
            # of the bar's range above the low, this low is a wick/rejection, not
            # a structural support level — exclude it from swing-low candidates so
            # the HVF funnel is built from where price actually settled.
            bar_range = highs[i] - lows[i]
            if bar_range > 0 and (closes[i] - lows[i]) / bar_range > 0.40:
                continue
            swing_lows.append((i, float(lows[i])))

    return swing_highs, swing_lows


def get_hvf_signal(ticker: str, lookback_days: int = 220,
                   trend_hint: dict = None) -> dict:
    """
    Detect a Hunt Volatility Funnel (HVF) continuation pattern.

    The algorithm:
      1. Fetch daily candles (lookback_days).
      2. Confirm a prior trend (uptrend → look for bullish HVF; downtrend → bearish).
      3. Find swing highs/lows (N=5 lookback).
      4. Search for the best valid triplet:
           H1 > H2 > H3   (lower highs — volatility contracting from above)
           L1 < L2 < L3   (higher lows — volatility contracting from below)
         with the pairs interleaving correctly in time and the funnel converging
         by at least 30% from H1-L1 to H3-L3.
      5. Calculate:
           Entry  = H3 (bullish pending buy-stop) or L3 (bearish sell-stop)
           Stop   = L3 − 0.2% (bullish) or H3 + 0.2% (bearish)
           Target = midpoint(H3, L3) ± (H1 − L1)

    Args:
        ticker:       instrument ticker
        lookback_days: bars of history to scan
        trend_hint:   pre-computed trend dict (from get_trend_structure) to
                      avoid a duplicate Yahoo Finance call when called from
                      analyse_price_action()

    Returns dict with keys:
        hvf_type:        "BULLISH" | "BEARISH" | None
        hvf_signal:      "TRIGGERED" (price past H3/L3) | "READY" | None
        h3_level:        pending-order entry price
        l3_level:        the third low (used for stop calculation)
        stop_level:      exact stop price
        target:          calculated price target
        risk_reward:     R:R at current price
        h1_level:        first high (for context)
        l1_level:        first low (for context)
        pattern_range:   H1-L1 distance
        bars_since_h3:   how recently H3 formed (freshness)
        pattern_quality: 0-100 composite quality score
        convergence:     current funnel width / initial funnel width (lower = tighter)
    """
    result = {
        "hvf_type": None, "hvf_signal": None,
        "h3_level": None, "l3_level": None, "stop_level": None,
        "target": None,   "risk_reward": None,
        "h1_level": None, "l1_level": None, "pattern_range": None,
        "bars_since_h3": None, "pattern_quality": 0, "convergence": None,
        "volume_confirmed": False,
    }

    try:
        hist = _get_daily(ticker, days=lookback_days)
        if len(hist) < 60:
            return result

        current_price = float(hist["Close"].iloc[-1])

        # Prior trend — use hint if supplied (avoids double API call)
        if trend_hint:
            trend_signal = trend_hint.get("signal", "SIDEWAYS")
        else:
            trend = get_trend_structure(ticker)
            trend_signal = trend.get("signal", "SIDEWAYS")

        # Swing detection — must happen before trend override (which uses swing data)
        swing_highs_5, swing_lows_5 = _find_swing_highs_lows(hist, n=5)
        swing_highs_3, swing_lows_3 = _find_swing_highs_lows(hist, n=3)

        if len(swing_highs_5) < 3 and len(swing_highs_3) < 3:
            return result
        if len(swing_lows_5) < 3 and len(swing_lows_3) < 3:
            return result

        # ── Recent-trend override ─────────────────────────────────────────────
        # The long-term 220-day trend can mask a post-peak reversal.
        # Example: BP rallied 355→609p (220-day = UPTREND) but then peaked at 609p
        # in March 2026 and has been printing lower highs ever since.  A bearish HVF
        # is forming but the dominant lookback never switches direction.
        #
        # Two detection methods — either triggers the override:
        #
        # A) Strict declining: last 3 swing highs are strictly h[-3]>h[-2]>h[-1]
        #    AND the H1→H3 decline is ≥5% (ignores minor oscillations in an uptrend)
        #
        # B) Peak-and-decline: there is a dominant peak in the last 90 bars AND
        #    current price is ≥7% below that peak AND every subsequent swing high
        #    after the peak is below the peak.  Catches BP-style tops where minor
        #    bounces prevent strict monotonic decline but the overall direction is clear.

        recent_swings_h = swing_highs_3[-5:] if len(swing_highs_3) >= 5 else swing_highs_3

        # Method A — strict monotonic decline with magnitude check
        strict_declining = (
            len(recent_swings_h) >= 3 and
            recent_swings_h[-1][1] < recent_swings_h[-2][1] < recent_swings_h[-3][1] and
            (recent_swings_h[-3][1] - recent_swings_h[-1][1]) / recent_swings_h[-3][1] >= 0.05
        )

        # Method B — peak-and-decline (handles bounces like BP 553→575 within downtrend)
        # Only fires when the peak IS the dominant high of the full lookback — i.e. a
        # genuine major top (BP 609p = 16-yr high), not routine consolidation in an
        # uptrend (Lloyds pulling back 8% after a local high that isn't the overall max).
        peak_and_decline = False
        if swing_highs_3:
            full_lookback_high = float(hist["High"].max())
            # Find the highest swing high in the last 90 bars
            post_bars = [(i, p) for i, p in swing_highs_3 if i >= len(hist) - 90]
            if post_bars:
                peak_bar, peak_price = max(post_bars, key=lambda x: x[1])
                pct_below_peak       = (peak_price - current_price) / peak_price
                at_all_time_high     = peak_price >= full_lookback_high * 0.98  # within 2% of max
                post_peak_highs      = [(i, p) for i, p in swing_highs_3 if i > peak_bar]
                all_below_peak       = all(p < peak_price for _, p in post_peak_highs)
                peak_and_decline = (
                    at_all_time_high and                 # peak must be the dominant high
                    pct_below_peak >= 0.07 and           # ≥7% off that peak
                    len(post_peak_highs) >= 2 and        # at least 2 lower highs formed
                    all_below_peak
                )

        # Rising highs (for bullish override)
        recent_highs_rising = (
            len(recent_swings_h) >= 3 and
            recent_swings_h[-1][1] > recent_swings_h[-2][1] > recent_swings_h[-3][1]
        )

        # Peak-and-decline only overrides when the long-term trend is NOT
        # STRONG_UPTREND — a 7-10% pullback in a confirmed strong uptrend is
        # normal consolidation, not a reversal. Reserve the override for stocks
        # where the long-term trend was moderate (UPTREND) or mixed (SIDEWAYS).
        allow_bearish_override = trend_signal not in ("STRONG_UPTREND",)

        if (strict_declining or peak_and_decline) and allow_bearish_override:
            effective_trend = "DOWNTREND"
        elif recent_highs_rising:
            effective_trend = "UPTREND"
        else:
            effective_trend = trend_signal

        # HVF only valid when there is a clear directional trend to continue
        if effective_trend not in ("STRONG_UPTREND", "UPTREND",
                                   "STRONG_DOWNTREND", "DOWNTREND"):
            return result

        bullish = effective_trend in ("STRONG_UPTREND", "UPTREND")

        # Search both swing sets; take the highest-quality pattern found
        best_pattern = None
        best_quality  = 0

        for swing_highs, swing_lows in [(swing_highs_5, swing_lows_5),
                                        (swing_highs_3, swing_lows_3)]:
            if len(swing_highs) < 3 or len(swing_lows) < 3:
                continue

            # Use the most recent 10 swings of each type for the search
            recent_highs = swing_highs[-10:]
            recent_lows  = swing_lows[-10:]

            # ── Search for valid H1>H2>H3 with interleaved L1<L2<L3 ──────────
            for hi in range(len(recent_highs)):
                for hj in range(hi + 1, len(recent_highs)):
                    for hk in range(hj + 1, len(recent_highs)):
                        h1, h2, h3 = recent_highs[hi], recent_highs[hj], recent_highs[hk]

                        # Condition 1: lower highs
                        if not (h1[1] > h2[1] > h3[1]):
                            continue

                        # Condition 2: H3 must be recent (within 60 daily bars)
                        bars_since_h3 = len(hist) - 1 - h3[0]
                        if bars_since_h3 > 60:
                            continue

                        # Condition 3: Pattern must span at least 10 bars (H1→H3)
                        if h3[0] - h1[0] < 10:
                            continue

                        # Find the most significant low between each pair of highs
                        lows_h1_h2 = [l for l in recent_lows if h1[0] < l[0] < h2[0]]
                        lows_h2_h3 = [l for l in recent_lows if h2[0] < l[0] < h3[0]]

                        if not lows_h1_h2 or not lows_h2_h3:
                            continue

                        # L1 = lowest low between H1 and H2
                        # L2 = lowest low between H2 and H3
                        l1 = min(lows_h1_h2, key=lambda x: x[1])
                        l2 = min(lows_h2_h3, key=lambda x: x[1])

                        # Condition 4: higher lows — L2 must be at or above L1.
                        # Allow 0.5% tolerance: a flat base (L2 ≈ L1) with declining
                        # highs still produces converging pressure and is a valid setup.
                        # This handles data-source precision differences (e.g. Yahoo
                        # Finance showing BP April lows as 531.3 vs 531.6 — 0.3p apart).
                        if l2[1] < l1[1] * 0.995:
                            continue

                        # L3: third higher low — after L2, at or above L2 (0.5% tol), below H3
                        l3_candidates = [
                            l for l in recent_lows
                            if l[0] > l2[0]
                            and l[1] >= l2[1] * 0.995
                            and l[1] < h3[1]
                        ]

                        if l3_candidates:
                            l3 = max(l3_candidates, key=lambda x: x[0])
                        elif current_price > h3[1]:
                            all_lows_after_l2 = [
                                l for l in recent_lows
                                if l[0] > l2[0] and l[1] > l2[1] and l[1] < h3[1]
                            ]
                            all_lows_wider = [
                                l for l in swing_lows
                                if l[0] > l2[0] and l[1] > l2[1] and l[1] < h3[1]
                            ]
                            combined = all_lows_after_l2 + all_lows_wider
                            if combined:
                                l3 = max(combined, key=lambda x: x[1])
                            else:
                                l3 = (h3[0] - 1, round((l2[1] + h3[1]) / 2, 6))
                        else:
                            if l2[1] < current_price < h3[1]:
                                l3 = (len(hist) - 1, current_price)
                            else:
                                continue

                        # Condition 5: funnel must be converging (≥30% contraction)
                        # Also enforce a minimum funnel width: H3-L3 must be ≥1% of
                        # price — prevents degenerate patterns where flat lows + 0.5%
                        # tolerance produce a near-zero H3-L3 range and infinite R:R.
                        initial_range = h1[1] - l1[1]
                        current_range = h3[1] - l3[1]
                        min_width     = current_price * 0.01
                        if initial_range <= 0 or current_range < min_width:
                            continue
                        convergence = current_range / initial_range
                        if convergence >= 0.70:
                            continue

                        # ── Quality scoring ──────────────────────────────────
                        quality = 0
                        quality += int((1.0 - convergence) * 50)
                        quality += max(0, 30 - bars_since_h3)
                        quality += 20 if effective_trend.startswith("STRONG") else 10
                        span_h1_h2 = h2[0] - h1[0]
                        span_h2_h3 = h3[0] - h2[0]
                        if span_h1_h2 > 0:
                            symmetry = min(span_h1_h2, span_h2_h3) / max(span_h1_h2, span_h2_h3)
                            quality += int(symmetry * 10)
                        quality = min(100, quality)

                        if quality > best_quality:
                            best_quality = quality
                            best_pattern = {
                                "h1": h1, "h2": h2, "h3": h3,
                                "l1": l1, "l2": l2, "l3": l3,
                                "convergence": convergence,
                                "bars_since_h3": bars_since_h3,
                                "initial_range": initial_range,
                            }

        if best_pattern is None:
            return result

        # ── Unpack best pattern ───────────────────────────────────────────────
        h1 = best_pattern["h1"]
        h2 = best_pattern["h2"]
        h3 = best_pattern["h3"]
        l1 = best_pattern["l1"]
        l3 = best_pattern["l3"]
        initial_range = best_pattern["initial_range"]

        # ── Volume profile check (Pattern Checker criterion #4) ───────────────
        # Volume should DECLINE as price compresses into the funnel (the coil),
        # then EXPAND on the breakout above H3 (bullish) or below L3 (bearish).
        # We measure:
        #   funnel_avg_vol  = average volume from H1 to H3 (the compression window)
        #   pre_h1_avg_vol  = average volume in the equivalent window before H1
        #   breakout_vol    = volume on the most recent bar (or bar after H3 for TRIGGERED)
        volume_confirmed = False
        try:
            vol_series = hist["Volume"].values
            h1_bar = h1[0]; h3_bar = h3[0]
            funnel_len = h3_bar - h1_bar

            if funnel_len > 2:
                funnel_vols  = vol_series[h1_bar:h3_bar + 1]
                pre_h1_vols  = vol_series[max(0, h1_bar - funnel_len):h1_bar]
                funnel_avg   = float(funnel_vols.mean())  if len(funnel_vols)  > 0 else None
                pre_h1_avg   = float(pre_h1_vols.mean()) if len(pre_h1_vols)  > 0 else None
                recent_vol   = float(vol_series[-1])
                overall_avg  = float(vol_series.mean())

                vol_declining = (funnel_avg is not None and pre_h1_avg is not None
                                 and funnel_avg < pre_h1_avg * 0.85)   # 15% drier in funnel
                vol_expanding = recent_vol > overall_avg * 1.2          # 20% above average on break

                volume_confirmed = vol_declining or vol_expanding
        except Exception:
            volume_confirmed = False  # don't fail HVF just because volume data is missing

        # ── Entry, stop, and target ───────────────────────────────────────────
        # Target = midpoint(H3, L3) ± (H1 - L1)  [Hunt's formula]
        midpoint_h3_l3 = (h3[1] + l3[1]) / 2.0

        if bullish:
            hvf_type    = "BULLISH"
            entry_level = round(h3[1], 6)                        # buy-stop pending at H3
            stop_price  = round(l3[1] * 0.998, 6)               # 0.2% below L3
            target      = round(midpoint_h3_l3 + initial_range, 6)
        else:
            hvf_type    = "BEARISH"
            entry_level = round(l3[1], 6)                        # sell-stop pending at L3
            stop_price  = round(h3[1] * 1.002, 6)               # 0.2% above H3
            target      = round(midpoint_h3_l3 - initial_range, 6)

        # Has price already triggered? (broken above H3 for bullish)
        if bullish:
            triggered = current_price > h3[1]
        else:
            triggered = current_price < l3[1]
        hvf_signal_str = "TRIGGERED" if triggered else "READY"

        # Risk/reward calculated from the ENTRY LEVEL (H3 for bullish, L3 for bearish).
        # Using entry — not current price — gives a stable R:R that is a property of
        # the setup itself, not a function of where price happens to be at scan time.
        # A READY trade with current price near the stop would give misleadingly huge
        # R:R from current price (Lloyds case: current ~100p, stop 100.5p → 443:1).
        risk   = abs(entry_level - stop_price)
        reward = abs(target - entry_level)
        rr     = round(reward / risk, 2) if risk > 0 else 0.0

        # ── R:R gate (Pattern Checker criterion #5) ───────────────────────────
        # Threshold imported from config.HVF_MIN_RR (= MIN_RISK_REWARD = 2.5).
        # Single source of truth — do not hardcode here.
        if rr < HVF_MIN_RR:
            log.info(f"HVF {ticker}: pattern found but R:R {rr} < {HVF_MIN_RR} — DEVELOPING (watch, not trade)")
            result.update({
                "hvf_type":         hvf_type,
                "hvf_signal":       "DEVELOPING",       # new state: real pattern, R:R not there yet
                "h3_level":         entry_level,
                "l3_level":         round(l3[1], 6),
                "stop_level":       stop_price,
                "target":           target,
                "risk_reward":      rr,
                "h1_level":         round(h1[1], 6),
                "l1_level":         round(l1[1], 6),
                "pattern_range":    round(initial_range, 6),
                "bars_since_h3":    best_pattern["bars_since_h3"],
                "pattern_quality":  best_quality,
                "convergence":      round(best_pattern["convergence"], 3),
                "volume_confirmed": volume_confirmed,
            })
            return result

        result.update({
            "hvf_type":          hvf_type,
            "hvf_signal":        hvf_signal_str,
            "h3_level":          entry_level,
            "l3_level":          round(l3[1], 6),
            "stop_level":        stop_price,
            "target":            target,
            "risk_reward":       rr,
            "h1_level":          round(h1[1], 6),
            "l1_level":          round(l1[1], 6),
            "pattern_range":     round(initial_range, 6),
            "bars_since_h3":     best_pattern["bars_since_h3"],
            "pattern_quality":   best_quality,
            "convergence":       round(best_pattern["convergence"], 3),
            "volume_confirmed":  volume_confirmed,
        })

        log.info(
            f"HVF {ticker}: {hvf_type} {hvf_signal_str} | "
            f"quality={best_quality} convergence={best_pattern['convergence']:.2f} | "
            f"H3={entry_level} stop={stop_price} target={target} R:R={rr} "
            f"vol_confirmed={volume_confirmed}"
        )

    except Exception as e:
        log.warning(f"HVF detection failed for {ticker}: {e}")

    return result


# =============================================================================
# Multi-timeframe HVF wrapper
# =============================================================================

def get_hvf_signal_mtf(ticker: str, trend_hint: dict = None) -> dict:
    """
    Run HVF detection across three timeframes and return the best result.

    Timeframes:
      daily-220   Standard full-history scan — catches mature multi-month funnels
      daily-90    Shorter daily scan — catches post-peak reversals forming over
                  the last 3 months (e.g. a stock topping after a big rally)
      weekly      Weekly candles via a separate fetch — catches large-scale funnels
                  spanning many months that daily noise can mask

    Priority: TRIGGERED > READY > DEVELOPING, then highest quality within state.
    The timeframe that produced the result is recorded in hvf_timeframe.
    """
    yticker = YAHOO_MAP.get(ticker, ticker)

    candidates = []

    # ── daily-220, daily-90, daily-60, daily-30 ──────────────────────────────
    for days, label in [(220, "daily-220"), (90, "daily-90"),
                        (60, "daily-60"),   (30, "daily-30")]:
        try:
            r = get_hvf_signal(ticker, lookback_days=days, trend_hint=trend_hint)
            if r.get("hvf_type"):
                r["hvf_timeframe"] = label
                candidates.append(r)
        except Exception as e:
            log.debug(f"HVF {label} failed for {ticker}: {e}")

    # ── weekly ───────────────────────────────────────────────────────────────
    try:
        import yfinance as yf
        t        = yf.Ticker(yticker)
        hist_wk  = t.history(period="3y", interval="1wk").dropna()
        if len(hist_wk) >= 30:
            # Patch _get_daily to return weekly bars for this call
            # by temporarily overriding the history fetch inline
            r_wk = _run_hvf_on_hist(ticker, hist_wk)
            if r_wk.get("hvf_type"):
                r_wk["hvf_timeframe"] = "weekly"
                candidates.append(r_wk)
    except Exception as e:
        log.debug(f"HVF weekly failed for {ticker}: {e}")

    if not candidates:
        empty = {k: None for k in [
            "hvf_type", "hvf_signal", "h3_level", "l3_level", "stop_level",
            "target", "risk_reward", "h1_level", "l1_level", "pattern_range",
            "bars_since_h3", "pattern_quality", "convergence", "volume_confirmed",
        ]}
        empty["hvf_timeframe"] = None
        return empty

    # Pick best: TRIGGERED > READY > DEVELOPING, then quality
    signal_rank = {"TRIGGERED": 3, "READY": 2, "DEVELOPING": 1}
    best = max(candidates,
               key=lambda r: (signal_rank.get(r.get("hvf_signal", ""), 0),
                              r.get("pattern_quality", 0)))
    return best


def _run_hvf_on_hist(ticker: str, hist) -> dict:
    """
    Run the HVF pattern search on a pre-fetched history DataFrame.
    Used by get_hvf_signal_mtf for weekly-bar analysis without a second API call.
    Mirrors the core logic of get_hvf_signal but accepts hist directly.
    """
    result = {
        "hvf_type": None, "hvf_signal": None,
        "h3_level": None, "l3_level": None, "stop_level": None,
        "target": None, "risk_reward": None,
        "h1_level": None, "l1_level": None, "pattern_range": None,
        "bars_since_h3": None, "pattern_quality": 0, "convergence": None,
        "volume_confirmed": False,
    }
    try:
        if len(hist) < 20:
            return result

        current_price = float(hist["Close"].iloc[-1])
        trend         = get_trend_structure(ticker)
        trend_signal  = trend.get("signal", "SIDEWAYS")

        sw_h5, sw_l5 = _find_swing_highs_lows(hist, n=5)
        sw_h3, sw_l3 = _find_swing_highs_lows(hist, n=2)   # n=2 for weekly (fewer bars)

        # Recent-trend override — peak-and-decline or strict declining
        rsh = sw_h3[-5:] if len(sw_h3) >= 5 else sw_h3
        strict_dec = (len(rsh) >= 3 and rsh[-1][1] < rsh[-2][1] < rsh[-3][1]
                      and (rsh[-3][1] - rsh[-1][1]) / rsh[-3][1] >= 0.05)
        peak_dec = False
        if sw_h3:
            post_bars = [(i, p) for i, p in sw_h3 if i >= len(hist) - 40]  # 40 weeks ~1yr
            if post_bars:
                pk_bar, pk_price = max(post_bars, key=lambda x: x[1])
                pct_off = (pk_price - current_price) / pk_price
                post_pk = [(i, p) for i, p in sw_h3 if i > pk_bar]
                peak_dec = (pct_off >= 0.07 and len(post_pk) >= 2
                            and all(p < pk_price for _, p in post_pk))
        rising = (len(rsh) >= 3 and rsh[-1][1] > rsh[-2][1] > rsh[-3][1])
        # Mirror the guard in get_hvf_signal: STRONG_UPTREND suppresses
        # bearish override — strict_dec / peak_dec should not flip a confirmed
        # strong uptrend into a DOWNTREND on historical replay.
        allow_bearish_override = trend_signal not in ("STRONG_UPTREND",)
        effective_trend = ("DOWNTREND" if (strict_dec or peak_dec) and allow_bearish_override else
                           "UPTREND"   if rising else trend_signal)

        if effective_trend not in ("STRONG_UPTREND", "UPTREND",
                                   "STRONG_DOWNTREND", "DOWNTREND"):
            return result

        bullish = effective_trend in ("STRONG_UPTREND", "UPTREND")
        best_pattern = None
        best_quality  = 0

        for swing_highs, swing_lows in [(sw_h5, sw_l5), (sw_h3, sw_l3)]:
            if len(swing_highs) < 3 or len(swing_lows) < 3:
                continue
            rh = swing_highs[-10:]
            rl = swing_lows[-10:]

            for hi in range(len(rh)):
                for hj in range(hi + 1, len(rh)):
                    for hk in range(hj + 1, len(rh)):
                        h1, h2, h3 = rh[hi], rh[hj], rh[hk]
                        if not (h1[1] > h2[1] > h3[1]):
                            continue
                        bars_since_h3 = len(hist) - 1 - h3[0]
                        if bars_since_h3 > 40 or h3[0] - h1[0] < 4:
                            continue
                        lows_12 = [l for l in rl if h1[0] < l[0] < h2[0]]
                        lows_23 = [l for l in rl if h2[0] < l[0] < h3[0]]
                        if not lows_12 or not lows_23:
                            continue
                        l1 = min(lows_12, key=lambda x: x[1])
                        l2 = min(lows_23, key=lambda x: x[1])
                        if l2[1] < l1[1] * 0.995:
                            continue
                        l3c = [l for l in rl if l[0] > l2[0] and l[1] >= l2[1] * 0.995 and l[1] < h3[1]]
                        if l3c:
                            l3 = max(l3c, key=lambda x: x[0])
                        elif l2[1] < current_price < h3[1]:
                            l3 = (len(hist) - 1, current_price)
                        else:
                            continue
                        ir = h1[1] - l1[1]; cr = h3[1] - l3[1]
                        if ir <= 0 or cr <= 0: continue
                        conv = cr / ir
                        if conv >= 0.70: continue
                        quality = (int((1.0 - conv) * 50) + max(0, 20 - bars_since_h3) +
                                   (20 if effective_trend.startswith("STRONG") else 10))
                        quality = min(100, quality)
                        if quality > best_quality:
                            best_quality = quality
                            best_pattern = {"h1": h1, "h2": h2, "h3": h3,
                                            "l1": l1, "l2": l2, "l3": l3,
                                            "convergence": conv, "bars_since_h3": bars_since_h3,
                                            "initial_range": ir}

        if not best_pattern:
            return result

        h1 = best_pattern["h1"]; h3 = best_pattern["h3"]
        l1 = best_pattern["l1"]; l3 = best_pattern["l3"]
        ir = best_pattern["initial_range"]
        mid = (h3[1] + l3[1]) / 2.0

        if bullish:
            hvf_type = "BULLISH"; entry = round(h3[1], 4)
            stop = round(l3[1] * 0.998, 4); target = round(mid + ir, 4)
            triggered = current_price > h3[1]
        else:
            hvf_type = "BEARISH"; entry = round(l3[1], 4)
            stop = round(h3[1] * 1.002, 4); target = round(mid - ir, 4)
            triggered = current_price < l3[1]

        risk = abs(entry - stop)          # R:R from entry level, not current price
        rr   = round(abs(target - entry) / risk, 2) if risk > 0 else 0.0
        # R:R gate must apply BEFORE TRIGGERED — a pattern that has broken out
        # but has poor R:R (e.g. entry already far past H3) is DEVELOPING, not
        # TRIGGERED. Without this gate the weekly scan was promoting low-R:R
        # patterns to TRIGGERED, which then won the mtf ranking over high-R:R
        # daily READY patterns (TRIGGERED rank=3 beats READY rank=2 regardless).
        # This matched the production bug: NVDA weekly TRIGGERED R:R=0.09 beat
        # NVDA daily READY R:R=5+ in the multi-timeframe wrapper (2026-06-04).
        if rr < HVF_MIN_RR:          # threshold from config — single source of truth
            hvf_sig = "DEVELOPING"
        elif triggered:
            hvf_sig = "TRIGGERED"
        else:
            hvf_sig = "READY"

        result.update({
            "hvf_type": hvf_type, "hvf_signal": hvf_sig,
            "h3_level": entry, "l3_level": round(l3[1], 4),
            "stop_level": stop, "target": target, "risk_reward": rr,
            "h1_level": round(h1[1], 4), "l1_level": round(l1[1], 4),
            "pattern_range": round(ir, 4),
            "bars_since_h3": best_pattern["bars_since_h3"],
            "pattern_quality": best_quality,
            "convergence": round(best_pattern["convergence"], 3),
            "volume_confirmed": False,
        })
    except Exception as e:
        log.warning(f"HVF weekly scan failed for {ticker}: {e}")
    return result


# =============================================================================
# Master function — full price action analysis for one instrument
# =============================================================================

def analyse_price_action(ticker: str) -> dict:
    """
    Run the full price action analysis for one instrument.

    Returns a dict with all signal components, composite score,
    and a clear verdict: CONFIRM_LONG / CONFIRM_SHORT / WAIT.

    The verdict is the gating condition for trade execution:
    - Even if COT + macro + fundamentals are all bullish,
      if verdict = WAIT, no trade is placed.
    - This prevents catching falling knives and entering too early.
    """
    log.info(f"Running price action analysis: {ticker}")

    range_bo    = get_range_breakout(ticker)
    trend       = get_trend_structure(ticker)
    atr_comp    = get_atr_compression(ticker)
    ma_align    = get_ma_alignment(ticker)
    failed      = get_failed_break(ticker)
    candlestick = get_candlestick_pattern(ticker)
    # HVF: multi-timeframe scan (daily-220, daily-90, weekly) — best result wins
    hvf         = get_hvf_signal_mtf(ticker, trend_hint=trend)

    score, verdict = compute_price_action_score(range_bo, trend, atr_comp, ma_align, failed, candlestick)

    # ── Fix 1: per-instrument PA threshold ────────────────────────────────────
    # compute_price_action_score uses the fixed module-level ±40 thresholds.
    # Override the verdict here using the per-class threshold from config.
    # Crypto uses 25, FX/commodities 30–35, equities/indices keep the default 40.
    thresh = PA_CONFIRM_THRESHOLDS.get(ticker, PA_CONFIRM_THRESHOLD_DEFAULT)
    # Always re-evaluate verdict against the config threshold.
    # Removing the previous `if thresh != PA_CONFIRM_THRESHOLD_DEFAULT` guard
    # which caused two bugs:
    #   (a) compute_price_action_score() uses hardcoded ±40 — changing
    #       PA_CONFIRM_THRESHOLD_DEFAULT had zero effect on standard instruments.
    #   (b) Any instrument added to PA_CONFIRM_THRESHOLDS with value 40 would
    #       still silently use the hardcoded threshold.
    # Now: the config value is always authoritative regardless of what it equals.
    if score >= thresh:
        verdict = "CONFIRM_LONG"
    elif score <= -thresh:
        verdict = "CONFIRM_SHORT"
    else:
        verdict = "WAIT"

    # ── Fix 2: HVF TRIGGERED bypass ────────────────────────────────────────
    # If the HVF pattern has already triggered (price through H3/L3), halve the
    # effective threshold — price has voted via the pattern, so less PA score
    # confirmation is required to proceed.
    # Floor at 15 to prevent near-zero thresholds on very low-threshold instruments.
    #
    # IMPORTANT: also resolves a direction conflict.  If Fix 1 set a CONFIRM in
    # the WRONG direction (e.g. CONFIRM_LONG with pa_score=+22 for a low-thresh
    # crypto) while the HVF has triggered BEARISH, we must override the verdict —
    # entering a BUY against a triggered bearish HVF is exactly the falling-knife
    # scenario this module was designed to prevent.
    # Previous guard `verdict == "WAIT"` was too narrow: it skipped the bypass
    # whenever Fix 1 had already set a (possibly conflicting) verdict.
    hvf_signal = hvf.get("hvf_signal")
    hvf_type   = hvf.get("hvf_type")
    if hvf_signal == "TRIGGERED":
        bypass = max(thresh * 0.5, 15)
        if hvf_type == "BULLISH" and score >= bypass:
            if verdict != "CONFIRM_LONG":
                log.info(
                    f"HVF TRIGGERED bypass: {ticker} → CONFIRM_LONG "
                    f"(pa_score={score:+.1f} bypass={bypass:.0f} was={verdict})"
                )
            verdict = "CONFIRM_LONG"
        elif hvf_type == "BEARISH" and score <= -bypass:
            if verdict != "CONFIRM_SHORT":
                log.info(
                    f"HVF TRIGGERED bypass: {ticker} → CONFIRM_SHORT "
                    f"(pa_score={score:+.1f} bypass={-bypass:.0f} was={verdict})"
                )
            verdict = "CONFIRM_SHORT"
        else:
            # HVF triggered but PA score doesn't support its direction even at
            # the reduced bypass threshold.  Force WAIT to avoid a trade that
            # contradicts both the HVF pattern and the PA score.
            if verdict not in ("WAIT",):
                log.info(
                    f"HVF TRIGGERED conflict: {ticker} hvf={hvf_type} but "
                    f"pa_score={score:+.1f} does not reach bypass={bypass:.0f} — "
                    f"overriding {verdict} → WAIT"
                )
            verdict = "WAIT"

    result = {
        "ticker":           ticker,
        "verdict":          verdict,        # CONFIRM_LONG / CONFIRM_SHORT / WAIT
        "pa_score":         score,

        # Individual signals
        "range_breakout":   range_bo.get("signal"),
        "range_high":       range_bo.get("range_high"),
        "range_low":        range_bo.get("range_low"),

        "trend_structure":  trend.get("signal"),
        "hh_hl_count":      trend.get("hh_hl_count"),
        "lh_ll_count":      trend.get("lh_ll_count"),

        "atr_compressed":   atr_comp.get("compressed"),
        "atr_expanding":    atr_comp.get("expanding"),
        "atr_pct_rank":     atr_comp.get("atr_pct_rank"),

        "ma_signal":        ma_align.get("signal"),
        "sma20":            ma_align.get("sma20"),
        "sma50":            ma_align.get("sma50"),
        "sma200":           ma_align.get("sma200"),
        "price_vs_200":     ma_align.get("price_vs_200"),
        "golden_cross":     ma_align.get("golden_cross"),
        "death_cross":      ma_align.get("death_cross"),

        "failed_break":      failed.get("signal"),
        "failed_break_desc": failed.get("description"),

        "candlestick":       candlestick.get("pattern"),
        "candlestick_dir":   candlestick.get("pattern_direction"),
        "candlestick_desc":  candlestick.get("description"),

        # Hunt Volatility Funnel
        "hvf_type":          hvf.get("hvf_type"),          # BULLISH / BEARISH / None
        "hvf_signal":        hvf.get("hvf_signal"),        # READY / TRIGGERED / None
        "hvf_h3_level":      hvf.get("h3_level"),          # pending entry level
        "hvf_l3_level":      hvf.get("l3_level"),          # third low (stop reference)
        "hvf_h1_level":      hvf.get("h1_level"),          # first lower-high (funnel top) — for the email chart
        "hvf_l1_level":      hvf.get("l1_level"),          # first higher-low (funnel bottom) — for the email chart
        "hvf_stop_level":    hvf.get("stop_level"),        # exact stop price
        "hvf_target":        hvf.get("target"),            # H1-L1 range target
        "hvf_risk_reward":   hvf.get("risk_reward"),       # pre-calculated R:R
        "hvf_quality":       hvf.get("pattern_quality"),   # 0-100
        "hvf_convergence":   hvf.get("convergence"),       # funnel tightness
        "hvf_bars_since_h3": hvf.get("bars_since_h3"),    # freshness
        "hvf_timeframe":     hvf.get("hvf_timeframe"),    # daily-220 / daily-90 / weekly
        "hvf_volume_confirmed": hvf.get("volume_confirmed", False),
    }

    log.info(
        f"Price action {ticker}: verdict={verdict} score={score:+.1f} | "
        f"breakout={range_bo.get('signal')} trend={trend.get('signal')} "
        f"MA={ma_align.get('signal')} failed={failed.get('signal')} "
        f"candle={candlestick.get('pattern')} "
        f"HVF={hvf.get('hvf_type')}({hvf.get('hvf_signal')})"
    )

    return result


# =============================================================================
# Entry point — run analysis for a list of instruments
# Usage: python price_action.py
# =============================================================================

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    instruments = ["XAUUSD", "OIL", "SPX500", "NVDA", "GBPUSD"]

    print("\nPrice Action Confirmation Analysis")
    print(f"{'Ticker':<10} {'Verdict':<15} {'Score':>7}  {'Breakout':<20} {'Trend':<18} {'MA':<14} {'Failed Break'}")
    print("-" * 100)

    for ticker in instruments:
        r = analyse_price_action(ticker)
        print(
            f"{r['ticker']:<10} {r['verdict']:<15} {r['pa_score']:>+7.1f}  "
            f"{r['range_breakout'] or 'NONE':<20} "
            f"{r['trend_structure'] or 'UNKNOWN':<18} "
            f"{r['ma_signal'] or 'UNKNOWN':<14} "
            f"{r['failed_break'] or 'NONE'}"
        )
