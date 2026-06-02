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
#
# Dependencies:
# -----------------------------------------------------------------------------
#   pip install yfinance pandas numpy
# =============================================================================

import logging
import numpy as np
import pandas as pd
import yfinance as yf

from config import YAHOO_MAP

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

    range_bo   = get_range_breakout(ticker)
    trend      = get_trend_structure(ticker)
    atr_comp   = get_atr_compression(ticker)
    ma_align   = get_ma_alignment(ticker)
    failed     = get_failed_break(ticker)
    candlestick = get_candlestick_pattern(ticker)

    score, verdict = compute_price_action_score(range_bo, trend, atr_comp, ma_align, failed, candlestick)

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
    }

    log.info(
        f"Price action {ticker}: verdict={verdict} score={score:+.1f} | "
        f"breakout={range_bo.get('signal')} trend={trend.get('signal')} "
        f"MA={ma_align.get('signal')} failed={failed.get('signal')} "
        f"candle={candlestick.get('pattern')}"
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
