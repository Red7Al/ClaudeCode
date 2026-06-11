# =============================================================================
# File:         commodity_macro.py
# Author:       Alex Hind
# Created:      2026-05-30
#
# Description:
# -----------------------------------------------------------------------------
# Macro driver analysis for commodity instruments.
# Commodities are macro assets — they respond to structural forces that
# operate above the level of individual chart patterns or order flow.
#
# Four macro drivers computed:
#
#   1. USD Strength / Weakness
#      Most commodities are priced in USD. Weak USD = bullish commodities.
#      Uses DXY level vs 20-week moving average and rate of change.
#      Signal: BULLISH (DXY weakening) / BEARISH (DXY strengthening) / NEUTRAL
#
#   2. Real Yields (10Y TIPS)
#      Real yield = nominal 10Y Treasury yield minus inflation breakeven.
#      Falling real yields → bullish Gold and Silver (no-yield assets
#      become relatively more attractive vs bonds).
#      Rising real yields → bearish precious metals.
#      Source: FRED DFII10 (10Y TIPS) and T10YIE (10Y breakeven)
#      Signal: BULLISH (real yield falling) / BEARISH (rising) / NEUTRAL
#
#   3. Inflation Expectations (5Y breakeven)
#      Rising inflation expectations → bullish energy and metals.
#      Source: FRED T5YIE (5-Year Breakeven Inflation Rate)
#      Signal: BULLISH (rising) / BEARISH (falling) / NEUTRAL
#
#   4. Global Growth Cycle Proxy
#      Expansion → industrial metals and energy outperform.
#      Contraction → precious metals and agriculture hold up better.
#      Proxy: US yield curve (10Y-2Y spread, already in macro gate) +
#             ISM Manufacturing PMI direction (FRED MANEMP proxy)
#      Signal: EXPANSION / CONTRACTION / NEUTRAL
#
# Commodity-specific macro scoring:
#      Each commodity responds differently to the four drivers.
#      A composite macro score (-100 to +100) is computed per instrument
#      using instrument-specific weightings.
#
# Data sources (all free):
#      FRED API — real yields, inflation expectations, yield curve
#      Yahoo Finance — DXY price history
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-05-30  Alex Hind   Initial build. Four macro drivers with instrument-specific weighting matrices.
#
# Dependencies:
# -----------------------------------------------------------------------------
#   pip install requests yfinance pandas numpy pg8000
#
# Environment Variables Required:
# -----------------------------------------------------------------------------
#   FRED_API_KEY          Free FRED API key
#   SUPABASE_USER         Supabase user
#   SUPABASE_DB_PASSWORD  Supabase password
# =============================================================================

import os
import logging
import requests
import numpy as np
import yfinance as yf
import pg8000.native
from datetime import datetime, timedelta

log = logging.getLogger("commodity_macro")

FRED_API_KEY  = os.environ["FRED_API_KEY"]
SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"
SUPABASE_USER = os.environ["SUPABASE_USER"]
SUPABASE_PASS = os.environ["SUPABASE_DB_PASSWORD"]

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


# =============================================================================
# Instrument-specific macro driver weightings
# How much each macro driver influences each commodity.
# Weights must sum to 100 per instrument.
#
# Drivers:  usd   real_yield   inflation   growth
# =============================================================================

COMMODITY_WEIGHTS = {
    # Gold: most sensitive to real yields and USD. Less growth-driven.
    "XAUUSD":  {"usd": 30, "real_yield": 40, "inflation": 20, "growth":  10},

    # Silver: hybrid — part precious metal, part industrial
    "XAGUSD":  {"usd": 25, "real_yield": 30, "inflation": 20, "growth":  25},

    # Oil: heavily driven by growth and inflation. USD matters but less.
    "OIL":     {"usd": 20, "real_yield": 10, "inflation": 35, "growth":  35},

    # Copper: pure industrial / growth proxy
    "COPPER":  {"usd": 20, "real_yield":  5, "inflation": 20, "growth":  55},

    # Platinum/Palladium: industrial + precious hybrid
    "PLATINUM":{"usd": 25, "real_yield": 25, "inflation": 20, "growth":  30},
    "PALLADIUM":{"usd": 25, "real_yield": 20, "inflation": 20, "growth": 35},
}

# Default weights for any commodity not listed above
DEFAULT_WEIGHTS = {"usd": 25, "real_yield": 25, "inflation": 25, "growth": 25}


# =============================================================================
# FRED API helper
# =============================================================================

def fetch_fred(series_id: str, periods: int = 10) -> list:
    """
    Fetch recent observations from FRED for a given series.
    Returns list of float values, oldest first.
    """
    try:
        resp = requests.get(FRED_BASE, params={
            "series_id":   series_id,
            "api_key":     FRED_API_KEY,
            "file_type":   "json",
            "sort_order":  "desc",
            "limit":       periods,
            "observation_start": (datetime.now() - timedelta(days=periods * 40)).strftime("%Y-%m-%d")   # wide window for monthly series
        }, timeout=10)
        resp.raise_for_status()
        obs = [o for o in resp.json().get("observations", []) if o["value"] != "."]
        values = [float(o["value"]) for o in reversed(obs)]
        return values
    except Exception as e:
        log.warning(f"FRED fetch failed ({series_id}): {e}")
        return []


# =============================================================================
# Driver 1 — USD Strength / Weakness
# =============================================================================

def get_usd_signal() -> dict:
    """
    Assess USD trend using DXY vs its 20-week moving average.

    BULLISH for commodities = DXY weakening (below 20wk MA and trending down)
    BEARISH for commodities = DXY strengthening (above 20wk MA and trending up)
    NEUTRAL = DXY mixed or flat

    Returns:
        signal:     BULLISH / BEARISH / NEUTRAL (from commodity perspective)
        dxy:        current DXY level
        dxy_ma20:   20-week moving average
        dxy_change: 4-week rate of change %
    """
    result = {"signal": "NEUTRAL", "dxy": None, "dxy_ma20": None, "dxy_change": None}
    try:
        t    = yf.Ticker("DX-Y.NYB")
        hist = t.history(period="1y", interval="1wk")
        if len(hist) < 20:
            return result

        closes      = hist["Close"].values
        current     = float(closes[-1])
        ma20        = float(np.mean(closes[-20:]))
        change_4wk  = float((closes[-1] - closes[-5]) / closes[-5] * 100)

        result["dxy"]        = round(current, 3)
        result["dxy_ma20"]   = round(ma20, 3)
        result["dxy_change"] = round(change_4wk, 2)

        # DXY below MA and falling = USD weakening = BULLISH for commodities
        if current < ma20 and change_4wk < -0.5:
            result["signal"] = "BULLISH"
        # DXY above MA and rising = USD strengthening = BEARISH for commodities
        elif current > ma20 and change_4wk > 0.5:
            result["signal"] = "BEARISH"
        else:
            result["signal"] = "NEUTRAL"

    except Exception as e:
        log.warning(f"USD signal failed: {e}")
    return result


# =============================================================================
# Driver 2 — Real Yields
# =============================================================================

def get_real_yield_signal() -> dict:
    """
    Assess real yield trend using FRED 10Y TIPS yield (DFII10).

    Falling real yields = BULLISH for precious metals (Gold, Silver)
    Rising real yields  = BEARISH for precious metals

    Also computes the inflation breakeven (T10YIE) as context.

    Returns:
        signal:           BULLISH / BEARISH / NEUTRAL
        real_yield:       current 10Y TIPS yield
        real_yield_change: 4-week change in real yield
        breakeven_10y:    current 10Y inflation breakeven
        nominal_10y:      current 10Y nominal yield
    """
    result = {
        "signal":             "NEUTRAL",
        "real_yield":         None,
        "real_yield_change":  None,
        "breakeven_10y":      None,
        "nominal_10y":        None,
    }
    try:
        # 10Y TIPS yield (real yield)
        tips = fetch_fred("DFII10", periods=8)
        if len(tips) >= 2:
            current_real   = tips[-1]
            prev_real      = tips[-5] if len(tips) >= 5 else tips[0]
            real_change    = current_real - prev_real

            result["real_yield"]        = round(current_real, 3)
            result["real_yield_change"] = round(real_change, 3)

            # Falling real yield = BULLISH precious metals
            if real_change < -0.1:
                result["signal"] = "BULLISH"
            elif real_change > 0.1:
                result["signal"] = "BEARISH"

        # 10Y inflation breakeven
        breakeven = fetch_fred("T10YIE", periods=5)
        if breakeven:
            result["breakeven_10y"] = round(breakeven[-1], 3)

        # 10Y nominal
        nominal = fetch_fred("DGS10", periods=5)
        if nominal:
            result["nominal_10y"] = round(nominal[-1], 3)

    except Exception as e:
        log.warning(f"Real yield signal failed: {e}")
    return result


# =============================================================================
# Driver 3 — Inflation Expectations
# =============================================================================

def get_inflation_signal() -> dict:
    """
    Assess inflation expectations using the 5-Year Breakeven Inflation Rate (T5YIE).

    Rising inflation expectations = BULLISH for energy and metals
    Falling inflation expectations = BEARISH

    Returns:
        signal:              BULLISH / BEARISH / NEUTRAL
        breakeven_5y:        current 5Y inflation breakeven
        breakeven_5y_change: 4-week change
    """
    result = {
        "signal":              "NEUTRAL",
        "breakeven_5y":        None,
        "breakeven_5y_change": None,
    }
    try:
        data = fetch_fred("T5YIE", periods=8)
        if len(data) >= 2:
            current = data[-1]
            prev    = data[-5] if len(data) >= 5 else data[0]
            change  = current - prev

            result["breakeven_5y"]        = round(current, 3)
            result["breakeven_5y_change"] = round(change,  3)

            if change > 0.05:
                result["signal"] = "BULLISH"
            elif change < -0.05:
                result["signal"] = "BEARISH"

    except Exception as e:
        log.warning(f"Inflation signal failed: {e}")
    return result


# =============================================================================
# Driver 3b — 5Y5Y Forward Inflation Breakeven
# More sophisticated than 5Y spot breakeven — used by central banks and
# institutional traders to measure long-run inflation expectations.
# 5Y5Y = what the market expects inflation to average in years 5-10 from now.
# =============================================================================

def get_5y5y_signal() -> dict:
    """
    Fetch the 5-Year, 5-Year Forward Inflation Expectation Rate (FRED T5YIFR).

    This is the market's view of where inflation will be in 5-10 years —
    less affected by near-term oil/food shocks than the 5Y spot breakeven.
    Preferred by central banks and institutional desks for structural inflation view.

    Rising 5Y5Y above 2.5% → persistently elevated inflation expected → bullish metals/energy
    Falling 5Y5Y below 2.0% → deflation concerns → bearish commodities broadly

    Returns:
        signal:       BULLISH / BEARISH / NEUTRAL
        value:        current 5Y5Y rate
        change:       4-week change
    """
    result = {"signal": "NEUTRAL", "value": None, "change": None}
    try:
        data = fetch_fred("T5YIFR", periods=8)
        if len(data) >= 2:
            current = data[-1]
            prev    = data[-5] if len(data) >= 5 else data[0]
            change  = current - prev

            result["value"]  = round(current, 3)
            result["change"] = round(change,  3)

            if current > 2.5 and change > 0.02:
                result["signal"] = "BULLISH"
            elif current < 2.0 or change < -0.05:
                result["signal"] = "BEARISH"
    except Exception as e:
        log.warning(f"5Y5Y signal failed: {e}")
    return result


# =============================================================================
# Driver 5 — ISM Manufacturing PMI
# Actual PMI data: above 50 = expansion, below 50 = contraction.
# More direct than employment proxy used previously.
# =============================================================================

def get_pmi_signal() -> dict:
    """
    Fetch ISM Manufacturing PMI from FRED (MANEMP is an employment proxy;
    the actual PMI composite index is NAPM on FRED).

    Above 50 and rising → expansion → bullish industrial metals, energy
    Below 50 and falling → contraction → defensive / precious metals

    Returns:
        signal:    EXPANSION / CONTRACTION / NEUTRAL
        pmi:       latest PMI reading
        change:    month-on-month change
    """
    result = {"signal": "NEUTRAL", "pmi": None, "change": None}
    try:
        # ISM Manufacturing PMI direction via manufacturing employment trend (FRED MANEMP)
        # NAPM/ISMMAN series not available on free FRED tier.
        # MANEMP (total manufacturing employment) provides a reliable directional proxy:
        # rising manufacturing employment → expanding → above 50 equivalent
        data = fetch_fred("MANEMP", periods=12)   # monthly series needs longer window
        if len(data) >= 3:
            # MANEMP: use 3-month trend as PMI direction proxy
            current  = data[-1]
            prev_3m  = data[-3]
            change   = current - prev_3m

            result["pmi"]    = round(current, 1)     # employment level (000s)
            result["change"] = round(change,  1)

            # Rising employment = EXPANSION proxy; falling = CONTRACTION
            if change > 20:
                result["signal"] = "EXPANSION"
            elif change < -20:
                result["signal"] = "CONTRACTION"
    except Exception as e:
        log.warning(f"PMI signal failed: {e}")
    return result


# =============================================================================
# Driver 4 — Global Growth Cycle Proxy
# =============================================================================

def get_growth_signal(yield_spread: float = None) -> dict:
    """
    Assess the global growth cycle using two proxies:

    1. US yield curve (10Y-2Y spread) — already in macro gate.
       Steepening = expansion. Inverting / flat = contraction.

    2. ISM Manufacturing PMI direction (proxied via FRED MANEMP —
       manufacturing employment, available free). Trend up = expansion.

    For commodities:
        EXPANSION  → industrial metals (copper), energy outperform
        CONTRACTION → precious metals hold / outperform
        NEUTRAL    → mixed

    Returns:
        signal:        EXPANSION / CONTRACTION / NEUTRAL
        yield_spread:  10Y-2Y spread (from macro gate if provided)
        pmi_direction: RISING / FALLING / FLAT
    """
    result = {
        "signal":        "NEUTRAL",
        "yield_spread":  yield_spread,
        "pmi_direction": "FLAT",
    }
    try:
        # Use yield spread from macro gate if available, else fetch
        if yield_spread is None:
            us2y = fetch_fred("DGS2",  periods=3)
            us10y = fetch_fred("DGS10", periods=3)
            if us2y and us10y:
                yield_spread = us10y[-1] - us2y[-1]
                result["yield_spread"] = round(yield_spread, 3)

        # Actual ISM Manufacturing PMI (FRED NAPM) — above/below 50
        pmi_data = get_pmi_signal()
        result["pmi"]       = pmi_data.get("pmi")
        result["pmi_change"] = pmi_data.get("change")
        pmi_signal = pmi_data.get("signal", "NEUTRAL")

        # Legacy direction label for compatibility
        if pmi_signal == "EXPANSION":
            result["pmi_direction"] = "RISING"
        elif pmi_signal == "CONTRACTION":
            result["pmi_direction"] = "FALLING"
        else:
            result["pmi_direction"] = "FLAT"

        # Determine growth signal — PMI + yield curve together
        spread_expansionary   = yield_spread is not None and yield_spread > 0.3
        spread_contractionary = yield_spread is not None and yield_spread < 0.0
        pmi_expansion   = pmi_signal == "EXPANSION"
        pmi_contraction = pmi_signal == "CONTRACTION"

        if pmi_expansion and (spread_expansionary or yield_spread is None):
            result["signal"] = "EXPANSION"
        elif pmi_contraction or spread_contractionary:
            result["signal"] = "CONTRACTION"
        else:
            result["signal"] = "NEUTRAL"

    except Exception as e:
        log.warning(f"Growth signal failed: {e}")
    return result


# =============================================================================
# Instrument-specific macro score
# Different commodities respond differently to each macro driver.
# =============================================================================

def score_for_instrument(
    instrument:       str,
    usd_signal:       str,
    real_yield_signal: str,
    inflation_signal: str,
    growth_signal:    str
) -> float:
    """
    Compute a weighted macro score (-100 to +100) for a specific commodity
    based on how that commodity responds to each macro driver.

    For each driver, the signal is converted to a score:
        BULLISH / EXPANSION = +100
        BEARISH / CONTRACTION = -100
        NEUTRAL = 0

    The weighted average of all four drivers gives the final score.
    """
    weights = COMMODITY_WEIGHTS.get(instrument, DEFAULT_WEIGHTS)

    signal_score = {
        "BULLISH":     100,
        "EXPANSION":   100,
        "NEUTRAL":       0,
        "FLAT":          0,
        "BEARISH":    -100,
        "CONTRACTION":-100,
    }

    usd_s       = signal_score.get(usd_signal, 0)
    real_s      = signal_score.get(real_yield_signal, 0)
    inflation_s = signal_score.get(inflation_signal, 0)
    growth_s    = signal_score.get(growth_signal, 0)

    score = (
        usd_s       * weights["usd"]        +
        real_s      * weights["real_yield"] +
        inflation_s * weights["inflation"]  +
        growth_s    * weights["growth"]
    ) / 100

    return round(score, 1)


# =============================================================================
# Full commodity macro analysis
# Runs all four drivers and scores all commodity instruments.
# =============================================================================

def analyse_commodity_macro(yield_spread: float = None) -> dict:
    """
    Run all four macro drivers and compute instrument-specific scores.
    Called at each session open for commodity instruments.

    Args:
        yield_spread: 10Y-2Y spread from macro gate (avoids duplicate FRED call)

    Returns dict with:
        drivers:     raw signal for each macro driver
        scores:      macro score per commodity instrument
        summary:     human-readable narrative
    """
    log.info("Running commodity macro analysis...")

    # Fetch all four drivers
    usd      = get_usd_signal()
    real_yld = get_real_yield_signal()
    inflation = get_inflation_signal()
    growth   = get_growth_signal(yield_spread)

    drivers = {
        "usd":       usd,
        "real_yield": real_yld,
        "inflation": inflation,
        "growth":    growth,
    }

    # Score each commodity
    scores = {}
    commodities = list(COMMODITY_WEIGHTS.keys()) + ["OIL"]
    for instrument in set(commodities):
        scores[instrument] = score_for_instrument(
            instrument,
            usd["signal"],
            real_yld["signal"],
            inflation["signal"],
            growth["signal"]
        )

    # Build narrative summary
    summary_parts = []

    if usd["signal"] != "NEUTRAL":
        direction = "weakening" if usd["signal"] == "BULLISH" else "strengthening"
        summary_parts.append(f"USD {direction} (DXY {usd.get('dxy','?')})")

    if real_yld["signal"] != "NEUTRAL":
        direction = "falling" if real_yld["signal"] == "BULLISH" else "rising"
        summary_parts.append(f"Real yields {direction} ({real_yld.get('real_yield','?')}%)")

    if inflation["signal"] != "NEUTRAL":
        direction = "rising" if inflation["signal"] == "BULLISH" else "falling"
        summary_parts.append(f"Inflation expectations {direction} (5Y BE: {inflation.get('breakeven_5y','?')}%)")

    if growth["signal"] != "NEUTRAL":
        summary_parts.append(f"Growth cycle: {growth['signal']}")

    summary = " | ".join(summary_parts) if summary_parts else "Macro drivers neutral"

    log.info(f"Commodity macro: USD={usd['signal']} | RealYield={real_yld['signal']} | "
             f"Inflation={inflation['signal']} | Growth={growth['signal']}")

    return {
        "drivers": drivers,
        "scores":  scores,
        "summary": summary,
    }


# =============================================================================
# Entry point — run analysis and print table
# Usage: python commodity_macro.py
# =============================================================================

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("Running commodity macro analysis...\n")
    result = analyse_commodity_macro()

    d = result["drivers"]
    print(f"USD Signal:          {d['usd']['signal']:<10} DXY={d['usd'].get('dxy','?')}  MA20={d['usd'].get('dxy_ma20','?')}  4wk chg={d['usd'].get('dxy_change','?')}%")
    print(f"Real Yield Signal:   {d['real_yield']['signal']:<10} 10Y TIPS={d['real_yield'].get('real_yield','?')}%  4wk chg={d['real_yield'].get('real_yield_change','?')}%")
    print(f"Inflation Signal:    {d['inflation']['signal']:<10} 5Y BE={d['inflation'].get('breakeven_5y','?')}%  4wk chg={d['inflation'].get('breakeven_5y_change','?')}%")
    print(f"Growth Signal:       {d['growth']['signal']:<10} Yield spread={d['growth'].get('yield_spread','?')}%  PMI direction={d['growth'].get('pmi_direction','?')}")
    print(f"\nSummary: {result['summary']}\n")

    print(f"{'Instrument':<12} {'Macro Score':>12}")
    print("-" * 26)
    for inst, score in sorted(result["scores"].items(), key=lambda x: -x[1]):
        bar = "▲" * int(abs(score) / 10) if score > 0 else "▼" * int(abs(score) / 10)
        print(f"{inst:<12} {score:>+8.1f}  {bar}")
