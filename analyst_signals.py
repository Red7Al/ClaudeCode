# =============================================================================
# File:         analyst_signals.py
# Author:       Alex Hind
# Created:      2026-06-01
#
# Description:
# -----------------------------------------------------------------------------
# Broker analyst price target and recommendation signals.
#
# KEY CAVEAT (as noted by the system owner):
# Broker recommendations have a mixed track record. The signal value varies
# enormously by firm and analyst. This module weights signals by:
#   1. Direction of change (upgrade > reiterate > downgrade)
#   2. Consensus breadth (how many analysts agree)
#   3. Upside to target (% gap between current price and mean target)
#   4. Recency (most recent 30 days weighted most heavily)
#
# Analyst signals are used as CONFIRMATION signals only — never primary.
# A single broker upgrade does not trigger a trade. It adds confidence
# when other signals (COT, price action, macro) are already aligned.
#
# Historical context on broker accuracy:
#   - Buy recommendations outnumber sells ~7:1 (structural bias toward bullish)
#   - Upgrades at 52-week highs are less reliable than upgrades after pullbacks
#   - Price target increases during a trend often lag, not lead
#   - Most reliable: upgrades from neutral/sell TO buy (rare, higher conviction)
#   - Most reliable timing: upgrades when stock is out of favour / below 200 SMA
#
# Data source: Yahoo Finance (free, via yfinance)
#   ticker.analyst_price_targets  — consensus PT, low, high, mean
#   ticker.recommendations        — recent firm-level upgrades/downgrades
#   ticker.upgrades_downgrades    — full history of rating changes
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-06-01  Alex Hind   Initial build. Yahoo Finance analyst data with consensus scoring and recency
#                                 weighting.
#
# Dependencies:
# -----------------------------------------------------------------------------
#   pip install yfinance pandas
# =============================================================================

import logging
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta, timezone

from config import YAHOO_MAP

log = logging.getLogger("analyst_signals")

# How many calendar days back to look for recent rating changes
RECENT_DAYS = 30

# Minimum number of analysts covering a stock for consensus to be meaningful
MIN_ANALYSTS = 3

# Minimum upside to mean price target to be signal-worthy
MIN_UPSIDE_PCT = 10.0

# Rating score mapping — higher = more bullish
RATING_SCORES = {
    "strong buy":   2.0,
    "buy":          1.5,
    "outperform":   1.5,
    "overweight":   1.5,
    "market perform": 0.5,
    "neutral":      0.0,
    "hold":         0.0,
    "equal weight": 0.0,
    "underperform":-1.5,
    "sell":        -2.0,
    "strong sell": -2.0,
}


# =============================================================================
# Fetch analyst consensus
# =============================================================================

def get_analyst_consensus(ticker: str) -> dict:
    """
    Fetch analyst price target consensus from Yahoo Finance.

    Returns:
        mean_target:      consensus mean price target
        current_price:    current stock price
        upside_pct:       % upside to mean target
        analyst_count:    number of analysts covering the stock
        recommendation:   overall consensus (strong buy / buy / hold / sell)
        signal:           BULLISH / BEARISH / NEUTRAL
        signal_strength:  0-100 composite score
    """
    result = {
        "mean_target":     None,
        "current_price":   None,
        "upside_pct":      None,
        "analyst_count":   None,
        "recommendation":  None,
        "signal":          "NEUTRAL",
        "signal_strength": 0,
    }

    yticker = YAHOO_MAP.get(ticker, ticker)

    try:
        t = yf.Ticker(yticker)

        # Current price
        current_price = t.fast_info.get("lastPrice") or t.fast_info.get("regularMarketPrice")
        if not current_price:
            return result
        result["current_price"] = round(float(current_price), 4)

        # Price target consensus
        pts = t.analyst_price_targets
        if pts is not None and not (isinstance(pts, dict) and not pts):
            try:
                mean_pt = float(pts.get("mean", 0) if isinstance(pts, dict) else pts["mean"])
                if mean_pt and mean_pt > 0:
                    result["mean_target"] = round(mean_pt, 2)
                    upside = (mean_pt - current_price) / current_price * 100
                    result["upside_pct"]  = round(upside, 1)
            except Exception:
                pass

        # Analyst count and consensus recommendation
        info = t.info
        result["analyst_count"]  = info.get("numberOfAnalystOpinions")
        result["recommendation"] = info.get("recommendationKey", "").lower()

        # Score the recommendation
        rec_score = RATING_SCORES.get(result["recommendation"], 0)

        # Upside score (0-50 points)
        upside_score = 0
        if result["upside_pct"] is not None:
            if result["upside_pct"] >= MIN_UPSIDE_PCT:
                upside_score = min(50, result["upside_pct"] * 2)
            elif result["upside_pct"] < 0:
                upside_score = max(-50, result["upside_pct"] * 2)

        # Consensus score (0-30 points, scaled by analyst count)
        analyst_count = result["analyst_count"] or 0
        consensus_score = 0
        if analyst_count >= MIN_ANALYSTS:
            consensus_score = rec_score * min(analyst_count / 10, 1.0) * 30

        # Signal strength (0-100)
        raw_score = upside_score + consensus_score
        signal_strength = round(max(0, min(100, (raw_score + 80) / 1.6)), 1)
        result["signal_strength"] = signal_strength

        # Determine signal
        if rec_score >= 1.0 and (result["upside_pct"] or 0) >= MIN_UPSIDE_PCT:
            result["signal"] = "BULLISH"
        elif rec_score <= -1.0 or (result["upside_pct"] or 0) < -10:
            result["signal"] = "BEARISH"
        else:
            result["signal"] = "NEUTRAL"

    except Exception as e:
        log.warning(f"Analyst consensus failed for {ticker}: {e}")

    return result


# =============================================================================
# Recent upgrades/downgrades (last 30 days)
# =============================================================================

def get_recent_rating_changes(ticker: str) -> dict:
    """
    Fetch recent broker rating changes from Yahoo Finance.
    Focus on UPGRADES — from neutral/sell to buy is the highest conviction signal.

    Returns:
        upgrades:         list of recent upgrades (firm, from_grade, to_grade, date)
        downgrades:       list of recent downgrades
        net_change:       upgrades - downgrades count
        signal:           BULLISH (net upgrades) / BEARISH (net downgrades) / NEUTRAL
        most_significant: description of most significant recent change
    """
    result = {
        "upgrades":         [],
        "downgrades":       [],
        "net_change":       0,
        "signal":           "NEUTRAL",
        "most_significant": "",
    }

    yticker = YAHOO_MAP.get(ticker, ticker)
    cutoff  = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)

    try:
        t  = yf.Ticker(yticker)
        ud = t.upgrades_downgrades

        if ud is None or ud.empty:
            return result

        # Filter to recent
        ud.index = pd.to_datetime(ud.index, utc=True)
        recent = ud[ud.index >= cutoff].copy()

        if recent.empty:
            return result

        # Classify each change
        upgrades   = []
        downgrades = []

        for date, row in recent.iterrows():
            firm      = row.get("Firm", "Unknown")
            action    = str(row.get("Action", "")).lower()
            to_grade  = str(row.get("ToGrade", "")).lower()
            from_grade = str(row.get("FromGrade", "")).lower()

            to_score   = RATING_SCORES.get(to_grade,   0)
            from_score = RATING_SCORES.get(from_grade, 0)

            change = {
                "firm":       firm,
                "action":     action,
                "from_grade": from_grade or "—",
                "to_grade":   to_grade,
                "date":       date.strftime("%Y-%m-%d"),
                "conviction": to_score - from_score,   # positive = more bullish
            }

            if to_score > from_score or action == "upgrade":
                upgrades.append(change)
            elif to_score < from_score or action == "downgrade":
                downgrades.append(change)

        result["upgrades"]   = upgrades
        result["downgrades"] = downgrades
        result["net_change"] = len(upgrades) - len(downgrades)

        # Signal based on net change
        if result["net_change"] >= 2:
            result["signal"] = "BULLISH"
        elif result["net_change"] == 1:
            result["signal"] = "MILD_BULLISH"
        elif result["net_change"] <= -2:
            result["signal"] = "BEARISH"
        elif result["net_change"] == -1:
            result["signal"] = "MILD_BEARISH"

        # Most significant change = highest conviction upgrade
        all_changes = sorted(upgrades + downgrades, key=lambda x: abs(x["conviction"]), reverse=True)
        if all_changes:
            top = all_changes[0]
            direction = "upgraded" if top["conviction"] > 0 else "downgraded"
            result["most_significant"] = (
                f"{top['firm']} {direction} {top['from_grade']} → {top['to_grade']} "
                f"on {top['date']}"
            )

    except Exception as e:
        log.warning(f"Rating changes failed for {ticker}: {e}")

    return result


# =============================================================================
# Master analyst signal — combines consensus + recent changes
# =============================================================================

def get_analyst_signal(ticker: str) -> dict:
    """
    Combine consensus price target and recent rating changes into one signal.

    Signal hierarchy:
        STRONG_BULLISH  — buy consensus + meaningful upside + net upgrades
        BULLISH         — buy consensus + upside OR net upgrades
        NEUTRAL         — hold consensus, minimal upside, no clear direction
        BEARISH         — sell consensus OR net downgrades
        STRONG_BEARISH  — sell consensus + net downgrades

    IMPORTANT: This is a confirmation signal only. The structural broker bias
    toward buy ratings means a single 'buy' is weak evidence. Look for:
        - Upgrades FROM neutral/hold (analysts changing their mind)
        - High upside to target (>20%)
        - Multiple analysts upgrading in the same period
        - Upgrades happening when the stock is OUT of favour (below 200 SMA)
    """
    consensus = get_analyst_consensus(ticker)
    changes   = get_recent_rating_changes(ticker)

    # Combine signals
    signals = [consensus["signal"], changes["signal"]]
    bull = sum(1 for s in signals if "BULL" in s)
    bear = sum(1 for s in signals if "BEAR" in s)

    if bull >= 2:
        combined = "STRONG_BULLISH"
    elif bull == 1 and bear == 0:
        combined = "BULLISH"
    elif bear >= 2:
        combined = "STRONG_BEARISH"
    elif bear == 1 and bull == 0:
        combined = "BEARISH"
    else:
        combined = "NEUTRAL"

    # Build narrative
    parts = []
    if consensus["upside_pct"] is not None:
        parts.append(f"Analyst PT: {consensus.get('mean_target','?')} ({consensus['upside_pct']:+.1f}% upside)")
    if consensus["recommendation"]:
        n = consensus.get("analyst_count") or "?"
        parts.append(f"Consensus: {consensus['recommendation']} ({n} analysts)")
    if changes["most_significant"]:
        parts.append(changes["most_significant"])
    if changes["net_change"] != 0:
        direction = "upgrades" if changes["net_change"] > 0 else "downgrades"
        parts.append(f"Last 30 days: {abs(changes['net_change'])} net {direction}")

    return {
        "ticker":             ticker,
        "signal":             combined,
        "consensus_signal":   consensus["signal"],
        "changes_signal":     changes["signal"],
        "mean_target":        consensus.get("mean_target"),
        "current_price":      consensus.get("current_price"),
        "upside_pct":         consensus.get("upside_pct"),
        "analyst_count":      consensus.get("analyst_count"),
        "recommendation":     consensus.get("recommendation"),
        "recent_upgrades":    len(changes["upgrades"]),
        "recent_downgrades":  len(changes["downgrades"]),
        "most_significant":   changes.get("most_significant"),
        "narrative":          " | ".join(parts) if parts else "No analyst data",
    }


# =============================================================================
# Entry point — scan Aschenbrenner and key investor picks
# Usage: python analyst_signals.py
# =============================================================================

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

    # Focus on high-interest picks from our tracked investors
    tickers = [
        "NBIS",   # Aschenbrenner — Nebius (highlighted as very interesting)
        "CRWV",   # Aschenbrenner — CoreWeave
        "BE",     # Aschenbrenner — Bloom Energy
        "NOW",    # Trump + Jensen Huang
        "PLTR",   # Jensen Huang
        "CRWD",   # Jensen Huang
        "NVDA",   # Core holding
        "IBM",    # Trump
        "DELL",   # Trump
    ]

    print(f"\n{'Ticker':<8} {'Signal':<16} {'Upside':>8} {'Consensus':<14} {'Analysts':>9}  Recent Changes")
    print("-" * 90)

    for ticker in tickers:
        r = get_analyst_signal(ticker)
        upside = f"{r['upside_pct']:+.1f}%" if r['upside_pct'] is not None else "  n/a"
        rec    = r.get("recommendation") or "—"
        count  = r.get("analyst_count") or "—"
        chg    = f"+{r['recent_upgrades']}/-{r['recent_downgrades']}" if r['recent_upgrades'] or r['recent_downgrades'] else "—"
        print(f"{ticker:<8} {r['signal']:<16} {upside:>8} {rec:<14} {str(count):>9}  {chg}")
