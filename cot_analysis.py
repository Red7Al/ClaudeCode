# =============================================================================
# File:         cot_analysis.py
# Author:       Alex Hind
# Created:      2026-05-30
#
# Description:
# -----------------------------------------------------------------------------
# Enhanced Commitment of Traders (COT) analysis module.
# Fetches weekly CFTC data and computes professional-grade COT signals
# used by institutional traders to identify turning points and trend strength.
#
# Signal layers computed:
#
#   1. Commercial hedger positioning
#      Commercials (producers, consumers) are "smart money" — they hedge
#      real exposure and are often right at extremes. Extreme net-short
#      commercials in Gold = major turning point signal.
#
#   2. Managed Money (large speculators / hedge funds)
#      Managed Money trends with the market. When they reach extremes
#      (95th percentile net-long) they are crowded — reversals follow.
#      Divergence from commercial positioning is a key signal.
#
#   3. Price vs positioning divergence
#      If price is rising but commercials are increasing shorts →
#      bearish divergence (smart money distributing into strength).
#      If price is falling but commercials are covering shorts →
#      bullish divergence (smart money accumulating into weakness).
#
#   4. Open Interest changes
#      Rising OI + rising price = real money buying (bullish confirmation)
#      Falling OI + rising price = short covering (weaker, less conviction)
#      Rising OI + falling price = real money selling (bearish confirmation)
#      Falling OI + falling price = long liquidation (weaker, may exhaust)
#
#   5. COT Score (composite -100 to +100)
#      Combines all signals into a single directional score per instrument.
#
# Data sources:
#   CFTC Legacy COT:        publicreporting.cftc.gov (6dca-aqww) — weekly
#   CFTC Disaggregated COT: publicreporting.cftc.gov (72hh-3qpy) — weekly
#   Yahoo Finance:          Price data for divergence calculation
#
# Version History:
# -----------------------------------------------------------------------------
# 1.0.0   2026-05-30  Alex Hind   Initial build. Full 4-signal COT stack:
#                                 extremes, managed money, price divergence,
#                                 OI signal. Composite score added.
#
# Dependencies:
# -----------------------------------------------------------------------------
#   pip install requests yfinance pandas numpy pg8000
# =============================================================================

import os
import logging
import requests
import numpy as np
import yfinance as yf
import pg8000.native
from datetime import datetime, timedelta

from config import CFTC_CODES, YAHOO_MAP

log = logging.getLogger("cot_analysis")

SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"
SUPABASE_USER = os.environ["SUPABASE_USER"]
SUPABASE_PASS = os.environ["SUPABASE_DB_PASSWORD"]

# Percentile thresholds for "extreme" positioning
EXTREME_HIGH_PCT = 90    # above 90th percentile = extreme long
EXTREME_LOW_PCT  = 10    # below 10th percentile = extreme short

# Number of weeks of history to use for percentile ranking
HISTORY_WEEKS = 156    # 3 years — better percentile accuracy for multi-year extreme identification


# =============================================================================
# Database helper
# =============================================================================

def get_db():
    return pg8000.native.Connection(
        host=SUPABASE_HOST, port=6543, database="postgres",
        user=SUPABASE_USER, password=SUPABASE_PASS, ssl_context=True
    )


# =============================================================================
# Fetch COT history from CFTC
# Returns last N weeks of data for percentile calculations.
# =============================================================================

def fetch_cot_history(cftc_code: str, weeks: int = HISTORY_WEEKS) -> list:
    """
    Fetch N weeks of COT history from CFTC for a given contract code.
    Returns list of dicts ordered oldest → newest.
    """
    try:
        resp = requests.get(
            "https://publicreporting.cftc.gov/resource/6dca-aqww.json",
            params={
                "$where": f"cftc_contract_market_code='{cftc_code}'",
                "$order": "report_date_as_yyyy_mm_dd DESC",
                "$limit": weeks + 1    # +1 to compute week-on-week change
            },
            timeout=20
        )
        resp.raise_for_status()
        data = resp.json()
        return list(reversed(data))    # oldest first
    except Exception as e:
        log.warning(f"CFTC history fetch failed for {cftc_code}: {e}")
        return []


def fetch_disaggregated_history(cftc_code: str, weeks: int = HISTORY_WEEKS) -> list:
    """
    Fetch Disaggregated COT report — provides explicit Managed Money category.
    Falls back gracefully if not available for this contract.
    """
    try:
        resp = requests.get(
            "https://publicreporting.cftc.gov/resource/72hh-3qpy.json",
            params={
                "$where": f"cftc_contract_market_code='{cftc_code}'",
                "$order": "report_date_as_yyyy_mm_dd DESC",
                "$limit": weeks + 1
            },
            timeout=20
        )
        resp.raise_for_status()
        data = resp.json()
        return list(reversed(data))
    except Exception as e:
        log.debug(f"Disaggregated COT not available for {cftc_code}: {e}")
        return []


# =============================================================================
# Percentile ranking
# =============================================================================

def percentile_rank(series: list, current_value: float) -> float:
    """
    Return the percentile rank of current_value within series (0–100).
    Used to identify extreme positioning.
    """
    if not series or len(series) < 5:
        return 50.0
    arr = np.array([float(x) for x in series if x is not None])
    if len(arr) == 0:
        return 50.0
    rank = float(np.sum(arr <= current_value) / len(arr) * 100)
    return round(rank, 1)


# =============================================================================
# Price direction from Yahoo Finance
# Used for divergence calculation — is price trending up or down?
# =============================================================================

def get_price_direction(ticker: str, weeks: int = 4) -> str:
    """
    Return RISING, FALLING, or FLAT based on price trend over last N weeks.
    """
    yticker = YAHOO_MAP.get(ticker, ticker)
    try:
        t    = yf.Ticker(yticker)
        hist = t.history(period=f"{weeks * 7 + 5}d", interval="1wk")
        if len(hist) < 2:
            return "FLAT"
        first = float(hist["Close"].iloc[0])
        last  = float(hist["Close"].iloc[-1])
        change_pct = (last - first) / first * 100
        if change_pct > 1.0:
            return "RISING"
        if change_pct < -1.0:
            return "FALLING"
        return "FLAT"
    except Exception as e:
        log.warning(f"Price direction failed for {ticker}: {e}")
        return "FLAT"


# =============================================================================
# OI Signal
# Determines whether a price move is driven by real money or covering/liquidation
# =============================================================================

def compute_oi_signal(oi_change: float, price_direction: str) -> str:
    """
    Classify the Open Interest signal.

    REAL_MONEY_BUY   — OI rising + price rising  = new longs entering
    SHORT_COVERING   — OI falling + price rising = shorts exiting (less conviction)
    REAL_MONEY_SELL  — OI rising + price falling = new shorts entering
    LONG_LIQUIDATION — OI falling + price falling = longs exiting (may exhaust)
    NEUTRAL          — OI flat or ambiguous
    """
    if oi_change is None:
        return "NEUTRAL"

    if oi_change > 0 and price_direction == "RISING":
        return "REAL_MONEY_BUY"
    if oi_change < 0 and price_direction == "RISING":
        return "SHORT_COVERING"
    if oi_change > 0 and price_direction == "FALLING":
        return "REAL_MONEY_SELL"
    if oi_change < 0 and price_direction == "FALLING":
        return "LONG_LIQUIDATION"
    return "NEUTRAL"


# =============================================================================
# Price vs Positioning Divergence
# Smart money (commercials) diverging from price = major signal
# =============================================================================

def compute_divergence(
    price_direction: str,
    comm_net_change: float
) -> str:
    """
    Detect divergence between price trend and commercial positioning.

    BULLISH  — price falling but commercials reducing shorts (accumulating)
               → smart money thinks the low is near
    BEARISH  — price rising but commercials increasing shorts (distributing)
               → smart money thinks the top is near
    NONE     — price and positioning aligned, or ambiguous
    """
    if comm_net_change is None:
        return "NONE"

    if price_direction == "FALLING" and comm_net_change > 0:
        return "BULLISH"    # Price down, commercials covering — bullish divergence
    if price_direction == "RISING" and comm_net_change < 0:
        return "BEARISH"    # Price up, commercials adding shorts — bearish divergence
    return "NONE"


# =============================================================================
# Composite COT Score (-100 to +100)
# Combines all signals into a single directional reading.
# =============================================================================

def compute_cot_score(
    comm_net_pct_rank:  float,
    mm_net_pct_rank:    float,
    comm_extreme:       str,
    mm_extreme:         str,
    price_divergence:   str,
    oi_signal:          str,
    bias:               str
) -> float:
    """
    Composite COT score from -100 (strongly bearish) to +100 (strongly bullish).

    Weighting:
        Commercial positioning bias    ±30
        Managed money extremes         ±20 (contrarian — extreme MM long = bearish)
        Price divergence               ±25
        OI signal                      ±15
        Base bias direction            ±10
    """
    score = 0.0

    # Commercial positioning (smart money direction)
    # High percentile rank = commercials net long = bullish
    score += (comm_net_pct_rank - 50) * 0.6    # maps 0-100 → -30 to +30

    # Managed money extremes (contrarian)
    # MM at extreme long → crowded → fade = bearish signal
    # MM at extreme short → crowded → fade = bullish signal
    if mm_extreme == "EXTREME_SHORT":
        score += 20     # contrarian bullish
    elif mm_extreme == "EXTREME_LONG":
        score -= 20     # contrarian bearish
    else:
        score += (50 - mm_net_pct_rank) * 0.4  # mild contrarian lean

    # Price divergence
    if price_divergence == "BULLISH":
        score += 25
    elif price_divergence == "BEARISH":
        score -= 25

    # OI signal
    oi_map = {
        "REAL_MONEY_BUY":   15,
        "SHORT_COVERING":    5,
        "REAL_MONEY_SELL": -15,
        "LONG_LIQUIDATION": -5,
        "NEUTRAL":           0,
    }
    score += oi_map.get(oi_signal, 0)

    # Base bias
    if bias == "BULLISH":
        score += 10
    elif bias == "BEARISH":
        score -= 10

    return round(max(-100, min(100, score)), 1)


# =============================================================================
# Master function — full enhanced COT analysis for one instrument
# =============================================================================

def analyse_cot(instrument: str) -> dict:
    """
    Run the full enhanced COT analysis for one instrument.
    Fetches 52 weeks of CFTC history and computes all signal layers.

    Returns a dict with all COT signals ready to store in Supabase
    and use in the trading signal stack.
    """
    result = {
        "instrument":         instrument,
        "report_date":        None,
        "comm_net":           None,
        "comm_net_change":    None,
        "noncomm_net":        None,
        "noncomm_net_change": None,
        "managed_money_long":  None,
        "managed_money_short": None,
        "managed_money_net":   None,
        "managed_money_change": None,
        "open_interest":      None,
        "oi_change":          None,
        "pct_comm_long":      None,
        "pct_comm_short":     None,
        "comm_net_pct_rank":  50.0,
        "mm_net_pct_rank":    50.0,
        "comm_extreme":       "NORMAL",
        "mm_extreme":         "NORMAL",
        "price_divergence":   "NONE",
        "oi_signal":          "NEUTRAL",
        "bias":               "NEUTRAL",
        "cot_score":          0.0,
    }

    cftc_code = CFTC_CODES.get(instrument)
    if not cftc_code:
        log.warning(f"No CFTC code for {instrument}")
        return result

    # ── 1. Fetch Legacy COT history (52 weeks) ─────────────────────────────
    legacy = fetch_cot_history(cftc_code, weeks=HISTORY_WEEKS)
    if len(legacy) < 2:
        log.warning(f"Insufficient COT history for {instrument}")
        return result

    # Latest and previous week
    latest = legacy[-1]
    prev   = legacy[-2]

    # ── 2. Commercial positioning ──────────────────────────────────────────
    comm_long   = float(latest.get("comm_positions_long_all",  0))
    comm_short  = float(latest.get("comm_positions_short_all", 0))
    comm_net    = comm_long - comm_short

    prev_comm_long  = float(prev.get("comm_positions_long_all",  0))
    prev_comm_short = float(prev.get("comm_positions_short_all", 0))
    prev_comm_net   = prev_comm_long - prev_comm_short
    comm_net_change = comm_net - prev_comm_net

    result["comm_net"]        = round(comm_net, 0)
    result["comm_net_change"] = round(comm_net_change, 0)
    result["report_date"]     = latest.get("report_date_as_yyyy_mm_dd", "")[:10]
    result["pct_comm_long"]   = float(latest.get("pct_of_oi_comm_long_all",  0) or 0)
    result["pct_comm_short"]  = float(latest.get("pct_of_oi_comm_short_all", 0) or 0)

    # ── 3. Non-commercial (large speculators) ─────────────────────────────
    nc_long  = float(latest.get("noncomm_positions_long_all",  0))
    nc_short = float(latest.get("noncomm_positions_short_all", 0))
    nc_net   = nc_long - nc_short
    prev_nc_net = (float(prev.get("noncomm_positions_long_all", 0)) -
                   float(prev.get("noncomm_positions_short_all", 0)))

    result["noncomm_net"]        = round(nc_net, 0)
    result["noncomm_net_change"] = round(nc_net - prev_nc_net, 0)

    # ── 4. Open interest ──────────────────────────────────────────────────
    oi      = float(latest.get("open_interest_all", 0))
    prev_oi = float(prev.get("open_interest_all",   0))
    oi_change = oi - prev_oi

    result["open_interest"] = round(oi, 0)
    result["oi_change"]     = round(oi_change, 0)

    # ── 5. Managed Money (from Disaggregated report) ───────────────────────
    disagg = fetch_disaggregated_history(cftc_code, weeks=HISTORY_WEEKS)
    mm_net_series = []

    if len(disagg) >= 2:
        mm_latest = disagg[-1]
        mm_prev   = disagg[-2]
        mm_long   = float(mm_latest.get("m_money_positions_long_all",  0))
        mm_short  = float(mm_latest.get("m_money_positions_short_all", 0))
        mm_net    = mm_long - mm_short
        prev_mm_net = (float(mm_prev.get("m_money_positions_long_all",  0)) -
                       float(mm_prev.get("m_money_positions_short_all", 0)))

        result["managed_money_long"]   = round(mm_long,  0)
        result["managed_money_short"]  = round(mm_short, 0)
        result["managed_money_net"]    = round(mm_net,   0)
        result["managed_money_change"] = round(mm_net - prev_mm_net, 0)

        mm_net_series = [
            float(w.get("m_money_positions_long_all",  0)) -
            float(w.get("m_money_positions_short_all", 0))
            for w in disagg
        ]
    else:
        # Fallback: use non-commercial as managed money proxy
        result["managed_money_net"]    = result["noncomm_net"]
        result["managed_money_change"] = result["noncomm_net_change"]
        mm_net = nc_net
        mm_net_series = [
            float(w.get("noncomm_positions_long_all",  0)) -
            float(w.get("noncomm_positions_short_all", 0))
            for w in legacy
        ]

    # ── 6. Percentile rankings (52-week history) ───────────────────────────
    comm_net_series = [
        float(w.get("comm_positions_long_all",  0)) -
        float(w.get("comm_positions_short_all", 0))
        for w in legacy
    ]

    comm_pct_rank = percentile_rank(comm_net_series, comm_net)
    mm_pct_rank   = percentile_rank(mm_net_series,   mm_net if mm_net_series else nc_net)

    result["comm_net_pct_rank"] = comm_pct_rank
    result["mm_net_pct_rank"]   = mm_pct_rank

    # ── 7. Extreme positioning flags ──────────────────────────────────────
    if comm_pct_rank >= EXTREME_HIGH_PCT:
        comm_extreme = "EXTREME_LONG"
    elif comm_pct_rank <= EXTREME_LOW_PCT:
        comm_extreme = "EXTREME_SHORT"
    else:
        comm_extreme = "NORMAL"

    if mm_pct_rank >= EXTREME_HIGH_PCT:
        mm_extreme = "EXTREME_LONG"    # Crowd is very long — reversal risk
    elif mm_pct_rank <= EXTREME_LOW_PCT:
        mm_extreme = "EXTREME_SHORT"   # Crowd is very short — reversal potential
    else:
        mm_extreme = "NORMAL"

    result["comm_extreme"] = comm_extreme
    result["mm_extreme"]   = mm_extreme

    # ── 8. Basic bias (commercial direction + change) ─────────────────────
    if comm_net > 0 and comm_net_change > 0:
        bias = "BULLISH"
    elif comm_net < 0 and comm_net_change < 0:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"
    result["bias"] = bias

    # ── 9. Price vs positioning divergence ────────────────────────────────
    price_direction = get_price_direction(instrument, weeks=4)
    divergence      = compute_divergence(price_direction, comm_net_change)
    result["price_divergence"] = divergence

    # ── 10. OI signal ─────────────────────────────────────────────────────
    oi_signal = compute_oi_signal(oi_change, price_direction)
    result["oi_signal"] = oi_signal

    # ── 11. Composite COT score ────────────────────────────────────────────
    cot_score = compute_cot_score(
        comm_pct_rank, mm_pct_rank,
        comm_extreme,  mm_extreme,
        divergence,    oi_signal,  bias
    )
    result["cot_score"] = cot_score

    log.info(
        f"COT {instrument}: bias={bias} | score={cot_score:+.1f} | "
        f"comm_extreme={comm_extreme} | mm_extreme={mm_extreme} | "
        f"divergence={divergence} | OI={oi_signal}"
    )

    return result


# =============================================================================
# Persist to Supabase
# =============================================================================

def save_cot_analysis(result: dict):
    """Write or update the COT analysis result in the cot_snapshot table."""
    db = get_db()
    try:
        db.run(
            """insert into cot_snapshot
               (report_date, instrument, cftc_code, comm_net, comm_net_change,
                noncomm_net, open_interest, oi_change, pct_comm_long, pct_comm_short,
                managed_money_long, managed_money_short, managed_money_net, managed_money_change,
                comm_net_pct_rank, mm_net_pct_rank, comm_extreme, mm_extreme,
                price_divergence, oi_signal, bias, cot_score)
               values
               (:rd, :i, :c, :cn, :cnc, :nn, :oi, :oic, :pcl, :pcs,
                :mml, :mms, :mmn, :mmch,
                :cpr, :mpr, :ce, :me, :pd, :ois, :b, :cs)
               on conflict (report_date, instrument) do update
               set comm_net=excluded.comm_net,
                   comm_net_change=excluded.comm_net_change,
                   noncomm_net=excluded.noncomm_net,
                   open_interest=excluded.open_interest,
                   oi_change=excluded.oi_change,
                   managed_money_long=excluded.managed_money_long,
                   managed_money_short=excluded.managed_money_short,
                   managed_money_net=excluded.managed_money_net,
                   managed_money_change=excluded.managed_money_change,
                   comm_net_pct_rank=excluded.comm_net_pct_rank,
                   mm_net_pct_rank=excluded.mm_net_pct_rank,
                   comm_extreme=excluded.comm_extreme,
                   mm_extreme=excluded.mm_extreme,
                   price_divergence=excluded.price_divergence,
                   oi_signal=excluded.oi_signal,
                   bias=excluded.bias,
                   cot_score=excluded.cot_score,
                   updated_at=now()""",
            rd=result["report_date"], i=result["instrument"],
            c=CFTC_CODES.get(result["instrument"], ""),
            cn=result["comm_net"],        cnc=result["comm_net_change"],
            nn=result["noncomm_net"],     oi=result["open_interest"],
            oic=result["oi_change"],      pcl=result["pct_comm_long"],
            pcs=result["pct_comm_short"],
            mml=result["managed_money_long"],  mms=result["managed_money_short"],
            mmn=result["managed_money_net"],   mmch=result["managed_money_change"],
            cpr=result["comm_net_pct_rank"],   mpr=result["mm_net_pct_rank"],
            ce=result["comm_extreme"],         me=result["mm_extreme"],
            pd=result["price_divergence"],     ois=result["oi_signal"],
            b=result["bias"],                  cs=result["cot_score"]
        )
        log.info(f"COT saved: {result['instrument']}")
    except Exception as ex:
        log.error(f"Failed to save COT for {result['instrument']}: {ex}")
    finally:
        db.close()


# =============================================================================
# Refresh all instruments — called by weekend review routine
# =============================================================================

def refresh_all_cot():
    """
    Run the full enhanced COT analysis for all configured instruments
    and persist results to Supabase.
    Called by the weekend review scheduled task.
    """
    results = {}
    for instrument in CFTC_CODES:
        log.info(f"Analysing COT: {instrument}")
        r = analyse_cot(instrument)
        save_cot_analysis(r)
        results[instrument] = r
    return results


# =============================================================================
# Entry point — run full refresh and print summary
# Usage: python cot_analysis.py
# =============================================================================

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("Running enhanced COT analysis for all instruments...\n")
    results = refresh_all_cot()

    print(f"\n{'Instrument':<10} {'Score':>7} {'Bias':<10} {'Comm Extreme':<14} {'MM Extreme':<14} {'Divergence':<10} {'OI Signal'}")
    print("-" * 90)
    for inst, r in results.items():
        print(
            f"{inst:<10} {r['cot_score']:>+7.1f} {r['bias']:<10} "
            f"{r['comm_extreme']:<14} {r['mm_extreme']:<14} "
            f"{r['price_divergence']:<10} {r['oi_signal']}"
        )
