# ======================================================================================================================
# File:         commodity_supply_demand.py
# Author:       Alex Hind
# Created:      2026-05-30
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Supply and demand fundamentals for commodity instruments.
# This is where commodities differ from equities — physical inventory levels
# and production/consumption balances drive prices over weeks and months.
#
# Key principle:
#   Falling inventories + rising demand → strong bullish signal
#   Rising inventories + weak demand   → bearish signal
#   Fundamentals move slowly — they confirm trend continuation.
#
# Data sources:
#
#   Oil (EIA Weekly Petroleum Status Report)
#   ─────────────────────────────────────────
#   Published every Wednesday by the US Energy Information Administration.
#   Covers: crude oil inventories, gasoline, distillates, refinery utilisation.
#   This is the most important weekly data release for crude oil traders.
#   API: https://api.eia.gov/v2/ — FREE (API key required)
#   Key series (verified vs live EIA API 2026-06-07):
#     PET.WCRSTUS1.W  — US crude stocks INCL SPR (thousand barrels)
#     PET.WCESTUS1.W  — US crude stocks EXCL SPR (commercial — the supply/demand
#                       signal; recommended over WCRSTUS1, see version 1.1.0 note)
#     PET.WGTSTUS1.W  — US gasoline stocks
#     PET.WDISTUS1.W  — US distillate stocks
#     PET.WPULEUS3.W  — US refinery % utilisation of operable capacity (units=%)
#     PET.WCRRIUS2.W  — US refiner net input of crude (kbbl/d) — NOT utilisation
#     PET.WCRFPUS2.W  — US field production of crude (kbbl/d)
#
#   Precious Metals (COMEX / LME)
#   ─────────────────────────────────────────
#   COMEX registered Gold and Silver stocks — physical tightness indicator.
#   LME warehouse stocks for industrial metals (Copper, Aluminium, Zinc).
#   Source: Nasdaq Data Link (partial free tier).
#
#   Geopolitical Risk
#   ─────────────────────────────────────────
#   Manual risk registry stored in Supabase.
#   Scored as a binary amplifier — active disruption widens stops,
#   reduces position size, but does not block trade direction.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.1.0   2026-06-07  Alex Hind   Fix refinery-utilisation EIA series ID. The code tried PET.WCRRIUS2.W / WCRRIUS2 /
#                                 PET.WPULEUS2.W — the first is refiner crude INPUT (kbbl/d, rejected by the 50-100
#                                 sanity check) and the other two 404 — so refinery_util never populated and every
#                                 commodity scan logged two EIA 404 warnings. Now uses the verified PET.WPULEUS3.W (%
#                                 utilisation). Series IDs confirmed against the live EIA API 2026-06-07. FLAGGED (not
#                                 changed): crude-stocks signal uses PET.WCRSTUS1.W (total incl SPR, 790M bbl); the
#                                 market supply/demand number is PET.WCESTUS1.W (commercial excl SPR, 434M bbl).
#                                 Switching changes a live oil signal — left for user confirmation.
# 1.0.0   2026-05-30  Alex Hind   Initial build. EIA oil inventory signal fully implemented. COMEX/LME stubs with
#                                 graceful fallbacks. Geopolitical risk registry from Supabase.
#
# Dependencies:
# ----------------------------------------------------------------------------------------------------------------------
#   pip install requests pg8000
#
# Environment Variables Required:
# ----------------------------------------------------------------------------------------------------------------------
#   EIA_API_KEY           Free EIA API key (register at eia.gov/opendata)
#   SUPABASE_USER         Supabase user
#   SUPABASE_DB_PASSWORD  Supabase password
#
# Note on EIA_API_KEY:
# ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
#   Register free at: https://www.eia.gov/opendata/register.php
#   Add to environment variables once obtained.
# ======================================================================================================================

import os
from db_pool import get_db as _pool_get_db   # resilient session-pooler connection (timeout+retry)
import logging
import requests
import pg8000.native
from datetime import datetime, timedelta

log = logging.getLogger("commodity_supply_demand")

EIA_API_KEY   = os.environ.get("EIA_API_KEY", "")       # Optional — fallback to graceful degradation
SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"
SUPABASE_USER = os.environ["SUPABASE_USER"]
SUPABASE_PASS = os.environ["SUPABASE_DB_PASSWORD"]

EIA_BASE = "https://api.eia.gov/v2/seriesid"

# Consecutive draw/build weeks to qualify as a trend
TREND_WEEKS = 4

# Significant weekly inventory change thresholds (millions of barrels for oil)
# EIA reports inventory in thousands of barrels
OIL_SIGNIFICANT_DRAW  = -2_000   # -2M barrels draw  (= -2,000 thousand barrels)
OIL_SIGNIFICANT_BUILD =  2_000   # +2M barrels build (= +2,000 thousand barrels)


# ======================================================================================================================
# Database helper
# ======================================================================================================================

def get_db():
    return _pool_get_db()


# ======================================================================================================================
# EIA API helper
# ======================================================================================================================

def fetch_eia_series(series_id: str, periods: int = 8) -> list:
    """
    Fetch recent data points from the EIA API for a given series.
    Returns list of (date_str, float_value) tuples, oldest first.
    Returns empty list if EIA_API_KEY not set or request fails.
    """
    if not EIA_API_KEY:
        log.debug("EIA_API_KEY not set — skipping EIA data")
        return []
    try:
        resp = requests.get(
            f"{EIA_BASE}/{series_id}",
            params={
                "api_key": EIA_API_KEY,
                "num":     periods,
                "out":     "json"
            },
            timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        series_data = data.get("response", {}).get("data", [])

        # EIA returns newest first — reverse to oldest first
        points = []
        for item in reversed(series_data):
            try:
                val = float(item.get("value", 0))
                dt  = item.get("period", "")
                points.append((dt, val))
            except (ValueError, TypeError):
                continue
        return points
    except Exception as e:
        log.warning(f"EIA fetch failed ({series_id}): {e}")
        return []


# ======================================================================================================================
# Oil Supply & Demand Signal (EIA)
# ======================================================================================================================

def get_oil_inventory_signal() -> dict:
    """
    Analyse EIA weekly crude oil inventory data.

    Signals:
        STRONG_BULLISH  — 4+ consecutive draws, or single draw > 4M barrels
        BULLISH         — draw > 2M barrels, or 2-3 consecutive draws
        NEUTRAL         — change within ±2M barrels, no clear trend
        BEARISH         — build > 2M barrels, or 2-3 consecutive builds
        STRONG_BEARISH  — 4+ consecutive builds, or single build > 4M barrels

    Returns:
        signal:              STRONG_BULLISH / BULLISH / NEUTRAL / BEARISH / STRONG_BEARISH
        latest_change:       week-on-week change in barrels
        latest_stocks:       current total stock level
        trend_weeks:         consecutive draw (+) or build (-) weeks
        refinery_util:       current refinery utilisation %
        narrative:           plain English explanation
    """
    result = {
        "signal":         "NEUTRAL",
        "latest_change":  None,
        "latest_stocks":  None,
        "trend_weeks":    0,
        "refinery_util":  None,
        "narrative":      "EIA data unavailable",
        "data_available": False,
    }

    # Crude oil stocks — US total (PET.WCRSTUS1.W)
    stocks = fetch_eia_series("PET.WCRSTUS1.W", periods=TREND_WEEKS + 2)
    if len(stocks) < 2:
        return result

    result["data_available"] = True

    # EIA data is in thousands of barrels
    # Convert to millions for readability in narrative
    latest_stocks = stocks[-1][1]
    prev_stocks   = stocks[-2][1]
    latest_change = latest_stocks - prev_stocks   # thousands of barrels

    result["latest_stocks"] = round(latest_stocks, 0)   # thousands of barrels
    result["latest_change"] = round(latest_change, 0)   # thousands of barrels

    # Consecutive draw / build streak
    trend = 0
    for i in range(len(stocks) - 1, 0, -1):
        change = stocks[i][1] - stocks[i-1][1]
        if latest_change < 0 and change < 0:
            trend -= 1      # consecutive draw
        elif latest_change > 0 and change > 0:
            trend += 1      # consecutive build
        else:
            break
    result["trend_weeks"] = trend

    # Refinery utilisation % of capacity — series verified vs the live EIA API
    # 2026-06-07: PET.WPULEUS3.W = "U.S. Percent Utilization of Refinery Operable
    # Capacity" (units=%). The previous IDs were wrong: WPULEUS2/WCRRIUS2 (bare)
    # return 404, and PET.WCRRIUS2.W is refiner crude INPUT (kbbl/d, ~16,881),
    # not a percentage — so refinery_util never populated and every scan logged
    # two EIA 404 warnings.
    refinery = fetch_eia_series("PET.WPULEUS3.W", periods=3)
    if refinery:
        util = refinery[-1][1]
        if 50 <= util <= 100:   # sanity: utilisation is a percentage
            result["refinery_util"] = round(util, 1)

    # Determine signal
    abs_change = abs(latest_change)

    # Convert thousands of barrels to millions for narrative
    chg_mb = latest_change / 1000   # thousands → millions of barrels

    if latest_change < 0:
        # Draw
        if trend <= -(TREND_WEEKS) or abs_change > 4_000:
            signal    = "STRONG_BULLISH"
            narrative = f"Strong draw: {chg_mb:.1f}M barrels, {abs(trend)}-week draw streak"
        elif abs_change > abs(OIL_SIGNIFICANT_DRAW) or trend <= -2:
            signal    = "BULLISH"
            narrative = f"Inventory draw: {chg_mb:.1f}M barrels"
        else:
            signal    = "NEUTRAL"
            narrative = f"Small draw: {chg_mb:.1f}M barrels — within normal range"
    else:
        # Build
        if trend >= TREND_WEEKS or abs_change > 4_000:
            signal    = "STRONG_BEARISH"
            narrative = f"Strong build: +{chg_mb:.1f}M barrels, {trend}-week build streak"
        elif abs_change > OIL_SIGNIFICANT_BUILD or trend >= 2:
            signal    = "BEARISH"
            narrative = f"Inventory build: +{chg_mb:.1f}M barrels"
        else:
            signal    = "NEUTRAL"
            narrative = f"Small build: +{chg_mb:.1f}M barrels — within normal range"

    if result["refinery_util"]:
        narrative += f" | Refinery util: {result['refinery_util']}%"

    result["signal"]    = signal
    result["narrative"] = narrative

    log.info(f"Oil inventory: {signal} — {narrative}")
    return result


# ======================================================================================================================
# Precious Metals Inventory Signal (COMEX registered stocks)
# ======================================================================================================================

def get_metals_inventory_signal(instrument: str) -> dict:
    """
    Assess physical tightness for Gold and Silver from COMEX registered stocks.

    Falling registered stocks = physical tightness = bullish
    Rising registered stocks  = physical surplus = bearish

    Note: COMEX registered stock data is available via Nasdaq Data Link
    (some free access). Returns NEUTRAL with note if data unavailable.
    """
    result = {
        "signal":         "NEUTRAL",
        "registered_oz":  None,
        "change_oz":      None,
        "narrative":      "COMEX inventory data requires Nasdaq Data Link subscription",
        "data_available": False,
    }

    # COMEX data requires Nasdaq Data Link (partial free)
    # Placeholder for when subscription is available
    # Series: CHRIS/CME_GC1 (Gold), CHRIS/CME_SI1 (Silver)
    # TODO: Add Nasdaq Data Link integration when subscription obtained

    log.debug(f"COMEX inventory data for {instrument} not yet integrated — returning NEUTRAL")
    return result


# ======================================================================================================================
# Oil Futures Curve — Contango vs Backwardation
# The shape of the oil futures curve is one of the most reliable indicators
# of physical supply/demand tightness.
#
# Backwardation (spot > futures) = tight supply, strong demand → BULLISH
# Contango (spot < futures) = oversupply, weak demand → BEARISH
#
# Uses front-month vs 6-month forward price from Yahoo Finance.
# ======================================================================================================================

def get_oil_curve_signal() -> dict:
    """
    Assess the oil futures curve shape (contango vs backwardation).

    Backwardation: prompt month more expensive than deferred months
    → Physical market is tight, buyers paying premium for immediate delivery
    → Bullish signal

    Contango: deferred months more expensive than prompt
    → Oversupply — holders storing oil and selling forward
    → Bearish signal

    Returns:
        signal:       BACKWARDATION / CONTANGO / FLAT
        front_price:  front-month futures price (CL=F)
        back_price:   6-month forward futures price (CLM=F approximate)
        spread:       front minus back (positive = backwardation)
        spread_pct:   spread as % of front price
    """
    result = {
        "signal":      "FLAT",
        "front_price": None,
        "back_price":  None,
        "spread":      None,
        "spread_pct":  None,
    }
    try:
        import yfinance as yf

        # WTI front-month (CL=F) vs 12-month deferred (CLZ=F approx)
        # True contango/backwardation = front vs deferred same contract
        # Yahoo Finance: CL=F = front month, CLJ26.NYM = deferred (year ahead)
        # Use USO (ETF) 1-month roll yield as proxy when deferred unavailable
        front = yf.Ticker("CL=F")
        front_price = None
        try:
            front_price = float(front.fast_info.get("lastPrice") or front.fast_info.get("regularMarketPrice") or 0)
        except Exception:
            pass

        # Try deferred WTI contracts — update these tickers when front-month rolls.
        # Format: CL{Month}{Year}.NYM  e.g. CLQ26 = Aug 2026, CLZ26 = Dec 2026
        # Using 6-month and 12-month deferred as proxies for curve shape.
        back_price = None
        for deferred_ticker in ["CLQ26.NYM", "CLZ26.NYM", "CLH27.NYM"]:
            try:
                back = yf.Ticker(deferred_ticker)
                bp   = back.fast_info.get("lastPrice") or back.fast_info.get("regularMarketPrice")
                if bp:
                    back_price = float(bp)
                    break
            except Exception:
                continue

        if not front_price:
            return result

        if not back_price:
            # Deferred contract data unavailable — return FLAT with note
            result["front_price"] = round(front_price, 3)
            result["signal"]      = "FLAT"
            log.debug("Oil deferred contract unavailable — curve shape FLAT (unknown)")
            return result

        spread     = front_price - back_price
        spread_pct = round(spread / front_price * 100, 3)

        result["front_price"] = round(front_price, 3)
        result["back_price"]  = round(back_price,  3)
        result["spread"]      = round(spread,       3)
        result["spread_pct"]  = spread_pct

        # Positive spread (front > back) = backwardation = tight market = BULLISH
        # Negative spread (front < back) = contango = oversupply = BEARISH
        if spread > 1.0:
            result["signal"] = "BACKWARDATION"
        elif spread < -1.0:
            result["signal"] = "CONTANGO"
        else:
            result["signal"] = "FLAT"

    except Exception as e:
        log.warning(f"Oil curve signal failed: {e}")
    return result


# ======================================================================================================================
# Demand Signals — Baltic Dry Index + China Proxy
# BDI measures shipping rates for dry bulk commodities (coal, grain, iron ore).
# Rising BDI = strong demand for physical commodities = bullish industrial metals.
# ======================================================================================================================

def get_demand_signals() -> dict:
    """
    Assess broad commodity demand using:

    1. Baltic Dry Index (BDI)
       Measures cost of shipping dry bulk commodities globally.
       Rising BDI = strong demand, tight shipping capacity → bullish commodities
       Falling BDI = weak demand → bearish industrial metals, energy

    2. Copper price trend (China demand proxy)
       Copper is the most China-sensitive major commodity.
       Rising copper = Chinese industrial demand healthy → bullish broad commodities
       Falling copper = Chinese demand weakening → bearish industrial metals

    Returns:
        bdi_signal:     BULLISH / BEARISH / NEUTRAL
        bdi_value:      current BDI level
        bdi_change_pct: 4-week % change
        copper_signal:  BULLISH / BEARISH / NEUTRAL
        copper_trend:   4-week % change in copper price
    """
    result = {
        "bdi_signal":     "NEUTRAL",
        "bdi_value":      None,
        "bdi_change_pct": None,
        "copper_signal":  "NEUTRAL",
        "copper_trend":   None,
    }

    try:
        import yfinance as yf
        import numpy as np

        # Baltic Dry Index — try multiple sources, fail gracefully
        # Yahoo Finance discontinued BDI tickers; use FRED or Quandl when available
        # Primary fallback: use shipping ETFs as proxy (BDRY - Breakwave Dry Bulk)
        bdi_found = False
        for bdi_ticker in ["BDRY", "SHIPS", "SEA"]:
            try:
                bdi  = yf.Ticker(bdi_ticker)
                hist = bdi.history(period="40d", interval="1d")
                if not hist.empty and len(hist) >= 5:
                    current    = float(hist["Close"].iloc[-1])
                    four_wk    = float(hist["Close"].iloc[-21]) if len(hist) >= 21 else float(hist["Close"].iloc[0])
                    change_pct = (current - four_wk) / four_wk * 100

                    result["bdi_value"]      = round(current, 2)
                    result["bdi_change_pct"] = round(change_pct, 1)

                    if change_pct > 10:
                        result["bdi_signal"] = "BULLISH"
                    elif change_pct < -10:
                        result["bdi_signal"] = "BEARISH"
                    bdi_found = True
                    break
            except Exception:
                continue

        if not bdi_found:
            log.debug("BDI proxy unavailable — returning NEUTRAL. Add EIA_API_KEY for shipping data.")

    except Exception as e:
        log.warning(f"BDI signal failed: {e}")

    try:
        import yfinance as yf

        # Copper as China demand proxy (HG=F on Yahoo Finance)
        copper = yf.Ticker("HG=F")
        hist   = copper.history(period="40d", interval="1d")
        if not hist.empty and len(hist) >= 5:
            current   = float(hist["Close"].iloc[-1])
            four_wk   = float(hist["Close"].iloc[-21]) if len(hist) >= 21 else float(hist["Close"].iloc[0])
            chg_pct   = (current - four_wk) / four_wk * 100

            result["copper_trend"] = round(chg_pct, 2)

            if chg_pct > 3:
                result["copper_signal"] = "BULLISH"
            elif chg_pct < -3:
                result["copper_signal"] = "BEARISH"

    except Exception as e:
        log.warning(f"Copper demand signal failed: {e}")

    return result


# ======================================================================================================================
# Geopolitical Risk Registry
# ======================================================================================================================

def get_geopolitical_risk(instrument: str) -> dict:
    """
    Check the Supabase geopolitical_risk table for active disruptions
    affecting the given instrument.

    Risk levels:
        HIGH    — active major disruption (Middle East escalation, major strike)
                  Effect: widen stop by 50%, reduce position size by 50%
        MEDIUM  — elevated risk (sanctions, minor disruption)
                  Effect: widen stop by 25%, reduce position size by 25%
        LOW     — background risk (monitoring only)
                  Effect: no size adjustment, log as context
        NONE    — no active risk events

    The geopolitical_risk table is maintained manually via weekend review.
    """
    result = {
        "risk_level":     "NONE",
        "description":    "",
        "stop_multiplier": 1.0,    # multiplier applied to normal stop distance
        "size_multiplier": 1.0,    # multiplier applied to normal position size
    }

    try:
        db = get_db()
        try:
            rows = db.run(
                """select risk_level, description
                   from   geopolitical_risk
                   where  instrument = :i
                   and    active = true
                   order  by risk_level desc
                   limit  1""",
                i=instrument
            )
            if rows:
                risk_level  = rows[0][0]
                description = rows[0][1]
                result["risk_level"]  = risk_level
                result["description"] = description

                if risk_level == "HIGH":
                    result["stop_multiplier"] = 1.5
                    result["size_multiplier"] = 0.5
                elif risk_level == "MEDIUM":
                    result["stop_multiplier"] = 1.25
                    result["size_multiplier"] = 0.75
        finally:
            db.close()
    except Exception as e:
        # Table may not exist yet — graceful fallback
        log.debug(f"Geopolitical risk table not available: {e}")

    return result


# ======================================================================================================================
# Master supply & demand analysis per instrument
# ======================================================================================================================

def analyse_supply_demand(instrument: str) -> dict:
    """
    Run the full supply & demand analysis for one commodity instrument.

    Returns a dict with:
        signal:           overall supply/demand signal
        inventory:        instrument-specific inventory signal
        geopolitical:     geopolitical risk dict
        stop_multiplier:  adjusted stop distance multiplier
        size_multiplier:  adjusted position size multiplier
        narrative:        plain English explanation for trade log
    """
    result = {
        "instrument":      instrument,
        "signal":          "NEUTRAL",
        "inventory":       {},
        "geopolitical":    {},
        "stop_multiplier": 1.0,
        "size_multiplier": 1.0,
        "narrative":       "",
        "data_available":  False,
    }

    # ── Oil ───────────────────────────────────────────────────────────────────────────────────────────────────────────
    if instrument in ("OIL", "USOIL", "CL"):
        inv     = get_oil_inventory_signal()
        curve   = get_oil_curve_signal()
        demand  = get_demand_signals()
        geo     = get_geopolitical_risk(instrument)

        result["inventory"]      = inv
        result["oil_curve"]      = curve
        result["demand"]         = demand
        result["geopolitical"]   = geo
        result["data_available"] = inv["data_available"]

        # Combined signal: inventory + curve shape
        signals = [inv["signal"], curve["signal"]]
        bull = sum(1 for s in signals if "BULL" in s or s == "BACKWARDATION")
        bear = sum(1 for s in signals if "BEAR" in s or s == "CONTANGO")
        result["signal"] = "BULLISH" if bull > bear else ("BEARISH" if bear > bull else "NEUTRAL")

        # Build narrative
        narrative = inv["narrative"]
        if curve["signal"] != "FLAT":
            narrative += f" | Curve: {curve['signal']} (WTI-Brent spread {curve.get('spread',0):+.2f})"
        if demand["bdi_signal"] != "NEUTRAL":
            narrative += f" | BDI: {demand['bdi_signal']} ({demand.get('bdi_change_pct',0):+.1f}% 4wk)"
        result["narrative"] = narrative

        if geo["risk_level"] in ("HIGH", "MEDIUM"):
            result["narrative"] += f" | Geopolitical: {geo['risk_level']} — {geo['description']}"
            result["stop_multiplier"] = geo["stop_multiplier"]
            result["size_multiplier"] = geo["size_multiplier"]

    # ── Gold / Silver ─────────────────────────────────────────────────────────────────────────────────────────────────
    elif instrument in ("XAUUSD", "GOLD", "XAGUSD", "SILVER"):
        inv  = get_metals_inventory_signal(instrument)
        geo  = get_geopolitical_risk(instrument)
        result["inventory"]    = inv
        result["geopolitical"] = geo
        result["signal"]       = inv["signal"]
        result["narrative"]    = inv["narrative"]

        if geo["risk_level"] in ("HIGH", "MEDIUM"):
            # Geopolitical uncertainty is bullish for Gold (safe haven)
            result["narrative"] += f" | Geopolitical: {geo['risk_level']} — {geo['description']} (safe haven demand)"
            result["stop_multiplier"] = geo["stop_multiplier"]

    # ── Industrial metals ─────────────────────────────────────────────────────────────────────────────────────────────
    elif instrument in ("COPPER", "PLATINUM", "PALLADIUM", "ALUMINIUM", "ZINC", "NICKEL"):
        inv  = get_metals_inventory_signal(instrument)
        geo  = get_geopolitical_risk(instrument)
        result["inventory"]    = inv
        result["geopolitical"] = geo
        result["signal"]       = inv["signal"]
        result["narrative"]    = inv["narrative"]

        if geo["risk_level"] in ("HIGH", "MEDIUM"):
            result["narrative"]      += f" | Supply disruption: {geo['description']}"
            result["stop_multiplier"] = geo["stop_multiplier"]
            result["size_multiplier"] = geo["size_multiplier"]

    # ── Non-commodity ─────────────────────────────────────────────────────────────────────────────────────────────────
    else:
        result["narrative"] = "Not a commodity instrument — supply/demand not applicable"

    return result


# ======================================================================================================================
# Entry point — run analysis for all commodity instruments
# Usage: python commodity_supply_demand.py
# ======================================================================================================================

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    instruments = ["OIL", "XAUUSD", "XAGUSD", "COPPER"]

    print("Commodity Supply & Demand Analysis\n")
    print(f"{'Instrument':<12} {'Signal':<16} {'Data':>5}  Narrative")
    print("-" * 80)

    for inst in instruments:
        r = analyse_supply_demand(inst)
        avail = "YES" if r["data_available"] else "NO"
        print(f"{inst:<12} {r['signal']:<16} {avail:>5}  {r['narrative'][:50]}")

    print("\nNote: EIA data requires EIA_API_KEY environment variable.")
    print("      COMEX data requires Nasdaq Data Link subscription.")
    print("      Register free EIA key at: https://www.eia.gov/opendata/register.php")
