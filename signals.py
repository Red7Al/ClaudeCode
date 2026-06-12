# ======================================================================================================================
# File:         signals.py
# Author:       Alex Hind
# Created:      2026-05-30
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Signal computation layer for the EndToEndTrading system.
# Queries all external data sources and returns structured signal data
# for each instrument. Contains NO trading decision logic — all decisions
# are made downstream by the Claude Cloud Routines.
#
# Signal layers computed:
#   Macro Gate      VIX, DXY, yield curve (FRED), COT (CFTC)
#   Primary         Options flow call/put imbalance + IV rank (Yahoo Finance)
#                   Bollinger Band squeeze / breakout (HVF equivalent, Yahoo Finance)
#   Confirmation    GEX by strike (computed from Yahoo Finance chains)
#                   VWAP position (computed from intraday price data)
#                   Director cluster buys (SEC EDGAR Form 4)
#                   Activist accumulation (SEC EDGAR Schedule 13D)
#                   Senate equity buys from scored senators (Capitol Trades)
#                   Superinvestor positions (Dataroma / notable_investors table)
#                   Social mentions — Trump / Musk (Supabase social_mentions table)
#   Risk Filter     Economic calendar (ForexFactory)
#   Discovery       Dynamic instrument screener (Yahoo Finance)
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-05-30  Alex Hind   Initial build. Full signal stack across all layers. AUS200 removed from AUS/Asia
#                                 session instrument list (not traded).
# 1.1.0   2026-06-05  Alex Hind   Added director_cluster_strong to signal_log INSERT — was computed but never persisted.
# 1.9.0   2026-06-11  Alex Hind   Confirmations must AGREE WITH THE TRADE DIRECTION (user: "Bearish is not confirmation
#                                 for a buy" — a BUY was showing "COT positioning BEARISH" as a counted confirmation).
#                                 Director/activist/senate/superinvestor/social buying evidence now counts on BUY only;
#                                 COT bias must match the side; ADX STRONG_TREND counts only when the dominant DI
#                                 (+DI vs -DI) matches the side. confirmations_fired list mirrors the same rules, so
#                                 emails/tweets/Slack never name a misaligned confirmation. Tightens trade entry:
#                                 conf_count can only drop, never rise.
# 1.8.0   2026-06-11  Alex Hind   New conf_names(sig) helper — short names of the confirmations that actually fired,
#                                 shown next to Confs:N in every signal summary (user 2026-06-11: count beside
#                                 Options/BB/COT status fields wrongly implied NEUTRAL items were being counted).
# 1.4.0   2026-06-07  Alex Hind   Fix get_potus_elite_senate_primary POTUS query: it selected account/context from
#                                 social_mentions filtering on ticker/mention_date — none of which exist (real cols:
#                                 author, post_text, tickers_found [array], post_time). Every scan logged "column
#                                 'account' does not exist" and the POTUS primary could never fire. Verified corrected
#                                 query vs live schema. (Surfaced by run_diagnostics 2026-06-07.)
# 1.3.0   2026-06-06  Alex Hind   Fix 3 bugs: (a) get_yield_curve: falsy-zero check on us2y/us10y silenced spread at
#                                 rate=0.0 — use `is not None` guard; (b) get_macro_gate: same falsy-zero on VIX — `if
#                                 vix and vix > threshold` allowed VIX=0.0 to bypass the gate; (c) stale `s` variable in
#                                 data_failures critical-count list comprehension — `s` was last value from outer scan
#                                 loop, not per-failure ticker; now builds a set of critical tickers then filters
#                                 against it.
# 1.2.0   2026-06-06  Alex Hind   get_candidate_instruments: skip US equity tickers from the dynamic screener in non-US
#                                 sessions (AUS_OPEN, UK_OPEN and their monitors). These tickers are blocked by the NYSE
#                                 hours guard anyway so scanning them wastes API calls, inflates signal_log, and
#                                 clutters Slack #signals. This week: AMD, RIVN, GOOGL appeared in AUS_OPEN and UK_OPEN
#                                 signal_log via screener even though they can never trade at 04:40 or 12:25 UTC.
# 1.1.1   2026-06-05  Alex Hind   get_gex_bias: detect and surface Yahoo Finance gamma unavailability. Previously
#                                 returned gex=0.0 (false NEUTRAL) when gamma column was absent or all-zero — 544 rows
#                                 in signal_log all NEUTRAL, signal never fired PINNED/TRENDING in production. Now
#                                 returns gex=None with a warning when no real gamma data found. signal_log INSERT:
#                                 retry up to 2× on pg8000 08P01 bind error (OIL 04-Jun-2026).
# 1.5.0   2026-06-10  Alex Hind   Trade-email detail (user 2026-06-10): enrich the fired-signal strings — Options flow
#                                 now carries call/put ratio + IV rank + GEX bias; Director buys carries insider count +
#                                 30d window + names; COT positioning carries composite score, commercial /
#                                 managed-money extremes, OI signal, price divergence and net + WoW change. Carry HVF
#                                 pivot dates, director_count and cot_comm_net in the signal dict.
# 1.6.0   2026-07-09  Alex Hind   Director buys: parse Form 4 XML from EDGAR to get actual transaction dates, share
#                                 counts, prices and total amounts (not just filing metadata). New helper
#                                 _fetch_form4_transactions(). director_transactions field added to signal dict.
#
# Dependencies:
# ----------------------------------------------------------------------------------------------------------------------
#   pip install yfinance pandas numpy requests pg8000
#
# Environment Variables Required:
# ----------------------------------------------------------------------------------------------------------------------
#   FRED_API_KEY          FRED API key for yield curve data
#   SUPABASE_USER         Supabase PostgreSQL user (postgres.{project_id})
#   SUPABASE_DB_PASSWORD  Supabase database password
# ======================================================================================================================

import os
from dotenv import load_dotenv; load_dotenv(override=True)
import time
import logging
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta, timezone
from typing import Optional
import pg8000.native

from config import (
    YAHOO_MAP,
    OPTIONS_PROXY_MAP,
    CFTC_CODES        as CFTC_MAP,
    ATR_MULTIPLIERS,
    ATR_MULTIPLIER_DEFAULT,
    SESSION_INSTRUMENTS,
    SECTOR_ETF_MAP,
    MIN_PRIMARY_SIGNALS,
    MIN_CONFIRMATION_SIGNALS,
    MIN_CALL_PUT_RATIO_BULL,
    MAX_CALL_PUT_RATIO_BEAR,
    VIX_GATE_THRESHOLD,
    YIELD_SPREAD_GATE_THRESHOLD,
    SPX_HIGH_STRESS_PCT,
    SPX_STRESS_PCT,
    INTRADAY_GUARD_ATR_MULTIPLIER,
    CALENDAR_BLOCK_MINUTES,
    MIN_DIRECTOR_CLUSTER,
    SUPERINVESTOR_LOOKBACK_DAYS,
    SOCIAL_MENTION_LOOKBACK_HOURS,
)

log = logging.getLogger("signals")

FRED_API_KEY  = os.environ.get("FRED_API_KEY", "")
SUPABASE_HOST = "aws-0-eu-west-1.pooler.supabase.com"
SUPABASE_USER = os.environ["SUPABASE_USER"]
SUPABASE_PASS = os.environ["SUPABASE_DB_PASSWORD"]

# ----------------------------------------------------------------------------------------------------------------------
# DB helper
# ----------------------------------------------------------------------------------------------------------------------
def get_db():
    """Supabase connection via the shared resilient session-pooler helper (timeout + retry)."""
    from db_pool import get_db as _pool_get_db
    return _pool_get_db()


def conf_names(sig: dict) -> str:
    """
    Short names of the confirmations that ACTUALLY fired — e.g.
    'Director buys, ADX strong trend, Sector ETF XLK aligned'.

    Use this next to confirmation_count in any signal summary: the count alone is
    misleading beside the Options/BB/COT status fields (user 2026-06-11 — 'How is
    NEUTRAL a confirmation?': those fields show family STATE, not what was counted).
    """
    names = [c.split(" — ")[0] for c in (sig.get("confirmations_fired") or [])]
    return ", ".join(names)

# ----------------------------------------------------------------------------------------------------------------------
# 1. MACRO GATE
# ----------------------------------------------------------------------------------------------------------------------
def get_vix() -> Optional[float]:
    try:
        t = yf.Ticker("^VIX")
        return round(t.fast_info["lastPrice"], 2)
    except Exception as e:
        log.warning(f"VIX fetch failed: {e}")
        return None

def get_dxy() -> Optional[float]:
    try:
        t = yf.Ticker("DX-Y.NYB")
        return round(t.fast_info["lastPrice"], 4)
    except Exception as e:
        log.warning(f"DXY fetch failed: {e}")
        return None

def get_yield_curve() -> dict:
    """Fetch US 2Y and 10Y yields from FRED. Returns spread (10Y - 2Y)."""
    base = "https://api.stlouisfed.org/fred/series/observations"
    result = {"us2y": None, "us10y": None, "yield_spread": None}
    try:
        for series, key in [("DGS2", "us2y"), ("DGS10", "us10y")]:
            resp = requests.get(base, params={
                "series_id":       series,
                "api_key":         FRED_API_KEY,
                "file_type":       "json",
                "sort_order":      "desc",
                "limit":           5,
                "observation_start": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            }, timeout=10)
            resp.raise_for_status()
            obs = [o for o in resp.json()["observations"] if o["value"] != "."]
            if obs:
                result[key] = float(obs[0]["value"])

        if result["us2y"] is not None and result["us10y"] is not None:
            result["yield_spread"] = round(result["us10y"] - result["us2y"], 4)
    except Exception as e:
        log.warning(f"FRED yield curve fetch failed: {e}")
    return result

def get_cot_bias(instrument: str) -> dict:
    """
    Fetch enhanced COT analysis from Supabase cot_snapshot table.
    Returns full signal set including composite score, extremes, divergence and OI signal.
    Table is refreshed weekly by cot_analysis.py via the weekend review routine.
    """
    result = {
        "comm_net":          None,
        "comm_net_change":   None,
        "bias":              "NEUTRAL",
        "cot_score":         0.0,
        "comm_extreme":      "NORMAL",
        "mm_extreme":        "NORMAL",
        "price_divergence":  "NONE",
        "oi_signal":         "NEUTRAL",
    }
    try:
        db = get_db()
        try:
            rows = db.run(
                """select comm_net, comm_net_change, bias, cot_score,
                          comm_extreme, mm_extreme, price_divergence, oi_signal
                   from   cot_snapshot
                   where  instrument = :i
                   order  by report_date desc limit 1""",
                i=instrument
            )
            if rows:
                result["comm_net"]         = rows[0][0]
                result["comm_net_change"]  = rows[0][1]
                result["bias"]             = rows[0][2] or "NEUTRAL"
                result["cot_score"]        = float(rows[0][3] or 0)
                result["comm_extreme"]     = rows[0][4] or "NORMAL"
                result["mm_extreme"]       = rows[0][5] or "NORMAL"
                result["price_divergence"] = rows[0][6] or "NONE"
                result["oi_signal"]        = rows[0][7] or "NEUTRAL"
        finally:
            db.close()
    except Exception as e:
        log.warning(f"COT fetch failed for {instrument}: {e}")
    return result

def get_market_stress() -> dict:
    """
    Detect intraday market stress — the gap the VIX threshold misses.

    VIX > 35 catches systemic crises (COVID, 2008). It does NOT catch:
    - A single large-cap dropping 10% at open (falling knife risk)
    - SPX down 1.5-2.5% from yesterday (elevated stress, not crisis)
    - A "normal" day where VIX is 18 but everything is selling off

    This check adds an intraday SPX drawdown gate:
        NORMAL      SPX down <1.0% from yesterday close  — no impact
        STRESS      SPX down 1.0-2.5%                    — halve position sizes
        HIGH_STRESS SPX down >2.5%                       — gate FAILS, no new entries

    Returns:
        stress_level:   NORMAL / STRESS / HIGH_STRESS
        spx_change_pct: % change from yesterday's close (negative = down)
        gate_pass:      False if HIGH_STRESS
    """
    result = {
        "stress_level":   "NORMAL",
        "spx_change_pct": None,
        "gate_pass":      True,
        "stress_reason":  "",
    }
    try:
        t    = yf.Ticker("^GSPC")
        hist = t.history(period="3d", interval="1d")
        if len(hist) < 2:
            return result

        prev_close  = float(hist["Close"].iloc[-2])
        today_close = float(hist["Close"].iloc[-1])   # last available bar
        pct         = round((today_close - prev_close) / prev_close * 100, 2)
        result["spx_change_pct"] = pct

        if pct < SPX_HIGH_STRESS_PCT:
            result["stress_level"] = "HIGH_STRESS"
            result["gate_pass"]    = False
            result["stress_reason"] = (
                f"SPX down {abs(pct):.1f}% from yesterday — HIGH_STRESS: no new entries. "
                f"Wait for intraday stabilisation before re-entering."
            )
        elif pct < SPX_STRESS_PCT:
            result["stress_level"] = "STRESS"
            # Gate still passes but run_session_open / run_monitor will see this
            # and halve position sizes (handled in caller)
            result["stress_reason"] = (
                f"SPX down {abs(pct):.1f}% from yesterday — STRESS mode: "
                f"position sizes halved, confirmation threshold raised."
            )
        else:
            result["stress_reason"] = f"SPX {pct:+.2f}% from yesterday — market normal"

    except Exception as e:
        log.warning(f"Market stress check failed: {e}")

    return result


def get_macro_gate(session_name: str) -> dict:
    """
    Evaluate macro gate. Returns gate_pass=True if conditions are safe to trade.

    Gate fails if ANY of:
      - VIX > 35                      (systemic crisis — 2008/COVID territory)
      - yield curve < -1.0%           (deeply inverted — severe recession risk)
      - SPX down > 2.5% intraday      (intraday stress — falling knife risk)

    STRESS mode (gate passes but position sizes halved):
      - SPX down 1.0-2.5% intraday

    Note: VIX 16 on a day where one stock crashes $200B is expected — VIX
    measures S&P 500 implied vol. A concentrated single-stock event barely
    moves VIX. The SPX drawdown check catches broad market sell-offs.
    """
    vix    = get_vix()
    dxy    = get_dxy()
    yields = get_yield_curve()
    stress = get_market_stress()

    gate_pass = True
    reasons = []

    if vix is not None and vix > VIX_GATE_THRESHOLD:
        gate_pass = False
        reasons.append(f"VIX too high: {vix}")

    spread = yields.get("yield_spread")
    if spread is not None and spread < YIELD_SPREAD_GATE_THRESHOLD:
        gate_pass = False
        reasons.append(f"Yield curve deeply inverted: {spread}")

    # Intraday stress check — catches what VIX threshold misses
    if not stress.get("gate_pass"):
        gate_pass = False
        reasons.append(stress["stress_reason"])

    gate_reason = " | ".join(reasons) if reasons else "All macro conditions normal"

    # Append stress annotation even when gate passes — visible in Slack summary
    if stress["stress_level"] == "STRESS" and gate_pass:
        gate_reason += f" ⚠ {stress['stress_reason']}"

    result = {
        "vix":             vix,
        "dxy":             dxy,
        "us2y":            yields.get("us2y"),
        "us10y":           yields.get("us10y"),
        "yield_spread":    spread,
        "macro_gate_pass": gate_pass,
        "gate_reason":     gate_reason,
        "session":         session_name,
        # Stress fields — used by run_session to adjust position sizing
        "market_stress":   stress["stress_level"],          # NORMAL / STRESS / HIGH_STRESS
        "spx_change_pct":  stress.get("spx_change_pct"),
        "stress_size_multiplier": 0.5 if stress["stress_level"] == "STRESS" else 1.0,
    }

    # Persist to Supabase
    try:
        db = get_db()
        db.run(
            """insert into macro_snapshot
               (session, vix, dxy, us2y, us10y, yield_spread, macro_gate_pass, gate_reason)
               values (:v_session, :v_vix, :v_dxy, :v_us2y, :v_us10y,
                       :v_spread, :v_gate_pass, :v_gate_reason)""",
            v_session=session_name, v_vix=vix, v_dxy=dxy,
            v_us2y=yields.get("us2y"), v_us10y=yields.get("us10y"),
            v_spread=spread, v_gate_pass=gate_pass, v_gate_reason=gate_reason
        )
        db.close()
    except Exception as e:
        log.warning(f"Failed to save macro snapshot: {e}")

    return result

# ----------------------------------------------------------------------------------------------------------------------
# 2. OPTIONS FLOW — call/put imbalance + IV rank
# ----------------------------------------------------------------------------------------------------------------------
def get_options_signal(ticker: str) -> dict:
    """
    Compute options bias from Yahoo Finance chain snapshot.
    Returns call/put volume ratio, IV rank (0-100), and bias label.

    Uses OPTIONS_PROXY_MAP for instruments that don't have options chains
    directly (indices like ^GSPC, commodities like GC=F).
    e.g. SPX500 → SPY options, XAUUSD → GLD options, UK100 → EWU options.
    """
    result = {"call_put_ratio": None, "iv_rank": None, "options_bias": "NEUTRAL"}
    # Use ETF proxy if available, otherwise fall back to YAHOO_MAP / direct ticker
    yticker = OPTIONS_PROXY_MAP.get(ticker) or YAHOO_MAP.get(ticker, ticker)
    try:
        t = yf.Ticker(yticker)
        expirations = t.options
        if not expirations:
            return result

        # Use nearest expiry for flow signal
        exp = expirations[0]
        chain = t.option_chain(exp)
        calls = chain.calls
        puts  = chain.puts

        call_vol = calls["volume"].fillna(0).sum()
        put_vol  = puts["volume"].fillna(0).sum()

        if put_vol > 0:
            ratio = round(call_vol / put_vol, 3)
            result["call_put_ratio"] = ratio
            if ratio > MIN_CALL_PUT_RATIO_BULL:
                result["options_bias"] = "BULLISH"
            elif ratio < MAX_CALL_PUT_RATIO_BEAR:
                result["options_bias"] = "BEARISH"

        # IV rank: compare current avg IV to 52-week range
        current_iv = calls["impliedVolatility"].mean()
        hist = t.history(period="1y", interval="1wk")
        if not hist.empty and current_iv:
            # Approximate IV range from historical volatility
            returns = hist["Close"].pct_change().dropna()
            hist_vol_ann = returns.std() * np.sqrt(52)
            iv_rank = min(100, max(0, int((current_iv / max(hist_vol_ann, 0.01)) * 50)))
            result["iv_rank"] = iv_rank

    except Exception as e:
        log.warning(f"Options signal failed for {ticker}: {e}")
    return result

# ----------------------------------------------------------------------------------------------------------------------
# 3. BOLLINGER BAND SQUEEZE (HVF equivalent)
# ----------------------------------------------------------------------------------------------------------------------
def get_bb_squeeze(ticker: str, period: int = 20) -> dict:
    """
    Detect volatility compression (Bollinger Band squeeze) and breakout direction.
    Squeeze = BB width at lowest in {period} bars.
    Breakout = close above upper BB (BULLISH) or below lower BB (BEARISH).
    Uses daily candles for bias, 1H for entry confirmation.
    """
    result = {"bb_squeeze": False, "bb_breakout_dir": None, "bb_width": None}
    yticker = YAHOO_MAP.get(ticker, ticker)
    try:
        t    = yf.Ticker(yticker)
        hist = t.history(period="60d", interval="1d")
        if len(hist) < period + 5:
            return result

        close  = hist["Close"]
        sma    = close.rolling(period).mean()
        std    = close.rolling(period).std()
        upper  = sma + 2 * std
        lower  = sma - 2 * std
        width  = (upper - lower) / sma

        current_width = width.iloc[-1]
        min_width     = width.iloc[-period:].min()

        result["bb_width"] = round(float(current_width), 6)

        # Squeeze: current width is at or near its 20-period minimum
        squeeze = bool(current_width <= min_width * 1.05)
        result["bb_squeeze"] = squeeze

        # Breakout: previous bar was in squeeze, current close breaks band
        prev_squeeze = bool(width.iloc[-2] <= width.iloc[-period:-1].min() * 1.05)
        if prev_squeeze:
            last_close = float(close.iloc[-1])
            last_upper = float(upper.iloc[-1])
            last_lower = float(lower.iloc[-1])
            if last_close > last_upper:
                result["bb_breakout_dir"] = "BULLISH"
            elif last_close < last_lower:
                result["bb_breakout_dir"] = "BEARISH"

    except Exception as e:
        log.warning(f"BB squeeze failed for {ticker}: {e}")
    return result

# ----------------------------------------------------------------------------------------------------------------------
# 4. GEX (Gamma Exposure) — computed from options chain
# ----------------------------------------------------------------------------------------------------------------------
def get_gex_bias(ticker: str) -> dict:
    """
    Estimate net gamma exposure from options chain.
    Positive GEX = dealers long gamma = price pinned (mean reversion).
    Negative GEX = dealers short gamma = price trending (momentum).

    Uses OPTIONS_PROXY_MAP for instruments without direct options chains.
    Gamma column may be absent or differently-cased depending on Yahoo Finance
    version — check case-insensitively and skip gracefully if unavailable.
    """
    result = {"gex": None, "gex_bias": "NEUTRAL"}
    yticker = OPTIONS_PROXY_MAP.get(ticker) or YAHOO_MAP.get(ticker, ticker)
    try:
        t = yf.Ticker(yticker)
        expirations = t.options
        if not expirations:
            return result

        spot = t.fast_info["lastPrice"]
        total_gex   = 0.0
        gamma_found = False   # track whether Yahoo returned any gamma data at all

        # Use nearest 2 expiries
        for exp in expirations[:2]:
            chain = t.option_chain(exp)
            for df, sign in [(chain.calls, 1), (chain.puts, -1)]:
                df = df.copy()
                # Yahoo Finance options chains may not include greeks, or may
                # return the column with different capitalisation ("Gamma" vs "gamma").
                # Find the gamma column case-insensitively; skip if absent.
                gamma_col = next(
                    (c for c in df.columns if c.lower() == "gamma"), None
                )
                if gamma_col is None:
                    continue   # silent — tracked via gamma_found below
                # Treat all-zero/NaN gamma as absent (Yahoo sometimes returns a
                # gamma column filled entirely with 0 or NaN when greeks are not
                # available, which would silently produce gex=0.0 = NEUTRAL).
                gamma_vals = df[gamma_col].fillna(0)
                if gamma_vals.abs().sum() == 0:
                    continue
                gamma_found = True
                df["oi"]    = df["openInterest"].fillna(0)
                df["gamma"] = gamma_vals
                # GEX = gamma × OI × spot² × 0.01 (per 1% move)
                df["gex"]   = df["gamma"] * df["oi"] * spot * spot * 0.01 * sign
                total_gex  += df["gex"].sum()

        if not gamma_found:
            # Yahoo Finance no longer supplies greeks in free options chains.
            # Return gex=None so the signal is treated as unavailable (not NEUTRAL).
            log.warning(f"GEX {ticker}: no gamma data from Yahoo Finance — signal unavailable")
            return result   # gex=None, gex_bias=NEUTRAL

        result["gex"] = round(total_gex, 2)
        if total_gex > 0:
            result["gex_bias"] = "PINNED"    # dealers long gamma → price mean-reverts
        elif total_gex < 0:
            result["gex_bias"] = "TRENDING"  # dealers short gamma → price trends

    except Exception as e:
        log.warning(f"GEX computation failed for {ticker}: {e}")
    return result

# ----------------------------------------------------------------------------------------------------------------------
# 5. VWAP POSITION
# ----------------------------------------------------------------------------------------------------------------------
def get_vwap_position(ticker: str) -> dict:
    """Return whether price is above or below intraday VWAP."""
    result = {"vwap": None, "vwap_position": None}
    yticker = YAHOO_MAP.get(ticker, ticker)
    try:
        t    = yf.Ticker(yticker)
        hist = t.history(period="1d", interval="5m")
        if hist.empty:
            return result

        typical = (hist["High"] + hist["Low"] + hist["Close"]) / 3
        vwap    = (typical * hist["Volume"]).cumsum() / hist["Volume"].cumsum()
        last_vwap  = float(vwap.iloc[-1])
        last_close = float(hist["Close"].iloc[-1])

        result["vwap"]          = round(last_vwap, 4)
        result["vwap_position"] = "ABOVE" if last_close > last_vwap else "BELOW"
    except Exception as e:
        log.warning(f"VWAP failed for {ticker}: {e}")
    return result

# ----------------------------------------------------------------------------------------------------------------------
# 6. DIRECTOR BUYS — SEC Form 4
# ----------------------------------------------------------------------------------------------------------------------
def _fetch_form4_transactions(accession_no: str) -> list:
    """
    Fetch a Form 4 XML from SEC EDGAR and return all open-market purchase
    transactions (code=P, acquired=A) as a list of dicts:
      {name, date, shares, price_per_share, amount}

    The filer CIK is extracted from the accession number (first 10 digits).
    Best-effort — returns [] on any network or parse error.
    """
    import xml.etree.ElementTree as ET
    try:
        # Accession format: XXXXXXXXXX-YY-ZZZZZZ (filer CIK = first segment, stripped)
        cik_str = accession_no.split("-")[0].lstrip("0") or "0"
        acc_clean = accession_no.replace("-", "")
        hdrs = {"User-Agent": "EndToEndTrading research@trading.com"}

        # Filing index — lists all documents so we can find the primary XML.
        idx_r = requests.get(
            f"https://www.sec.gov/Archives/edgar/data/{cik_str}/{acc_clean}/index.json",
            headers=hdrs, timeout=10)
        if idx_r.status_code != 200:
            return []

        items = idx_r.json().get("directory", {}).get("item", [])
        xml_name = None
        for item in items:
            name = item.get("name", "")
            # Primary Form 4 XML: ends in .xml, not an XSL stylesheet
            if name.lower().endswith(".xml") and "xsl" not in name.lower():
                xml_name = name
                break
        if not xml_name:
            return []

        xml_r = requests.get(
            f"https://www.sec.gov/Archives/edgar/data/{cik_str}/{acc_clean}/{xml_name}",
            headers=hdrs, timeout=10)
        if xml_r.status_code != 200:
            return []

        root = ET.fromstring(xml_r.text)

        # Reporter name sits at the top of the ownershipDocument
        name_el = root.find(".//reportingOwnerName/value")
        reporter = (name_el.text or "").strip() if name_el is not None else ""

        txs = []
        for tx in root.findall(".//nonDerivativeTransaction"):
            code = (tx.findtext(".//transactionCoding/transactionCode") or "").strip().upper()
            acq  = (tx.findtext(".//transactionAmounts/transactionAcquiredDisposedCode/value") or "").strip().upper()
            # Only open-market purchases
            if code != "P" or acq != "A":
                continue
            date       = (tx.findtext(".//transactionDate/value") or "").strip()
            shares_str = (tx.findtext(".//transactionAmounts/transactionShares/value") or "").strip()
            price_str  = (tx.findtext(".//transactionAmounts/transactionPricePerShare/value") or "").strip()
            try:
                shares = float(shares_str)
                price  = float(price_str)
            except (ValueError, TypeError):
                continue
            txs.append({
                "name":            reporter,
                "date":            date,
                "shares":          int(shares),
                "price_per_share": round(price, 2),
                "amount":          round(shares * price, 2),
            })
        return txs
    except Exception as e:
        log.debug(f"Form 4 XML fetch failed ({accession_no}): {e}")
        return []


def get_director_buys(ticker: str, days: int = 30) -> dict:
    """
    Query SEC EDGAR for Form 4 open-market purchases by directors/officers.
    Fetches each Form 4 XML to get actual transaction dates, share counts,
    prices and amounts (not just filing metadata).
    Returns cluster_buy=True if 2+ purchases found within {days} days.
    """
    result = {
        "director_signal":        False,
        "director_count":         0,
        "director_detail":        "",
        "director_transactions":  [],   # [{name, date, shares, price_per_share, amount}]
    }
    try:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        resp = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={
                "q":         f'"{ticker}"',
                "forms":     "4",
                "dateRange": "custom",
                "startdt":   since,
                "enddt":     datetime.now().strftime("%Y-%m-%d"),
            },
            headers={"User-Agent": "EndToEndTrading research@trading.com"},
            timeout=15,
        )
        if resp.status_code != 200:
            return result

        hits = resp.json().get("hits", {}).get("hits", [])
        all_txs = []
        seen = set()
        for hit in hits[:12]:   # cap requests: 12 filings max
            src = hit.get("_source", {})
            if src.get("form_type") != "4":
                continue
            acc = src.get("accession_no", "")
            if not acc or acc in seen:
                continue
            seen.add(acc)
            txs = _fetch_form4_transactions(acc)
            all_txs.extend(txs)
            time.sleep(0.15)    # polite to SEC servers (max ~7 req/s)

        result["director_count"]          = len(all_txs)
        result["director_signal"]         = len(all_txs) >= 2
        result["director_cluster_strong"] = len(all_txs) >= 3
        result["director_transactions"]   = sorted(all_txs, key=lambda x: x["date"], reverse=True)

        if all_txs:
            suffix = " [STRONG CLUSTER]" if len(all_txs) >= 3 else ""
            lines = []
            for t in result["director_transactions"][:5]:
                lines.append(
                    f"{t['name']} — {t['shares']:,} shares @ ${t['price_per_share']:,.2f}"
                    f" (${t['amount']:,.0f}) on {t['date']}"
                )
            result["director_detail"] = (
                f"{len(all_txs)} insider purchase{'s' if len(all_txs) != 1 else ''} "
                f"in last {days}d (Form 4){suffix}:\n" + "\n".join(lines)
            )

    except Exception as e:
        log.warning(f"Director buy check failed for {ticker}: {e}")
    return result

# ----------------------------------------------------------------------------------------------------------------------
# 7. ACTIVIST SIGNAL — SEC Schedule 13D
# ----------------------------------------------------------------------------------------------------------------------
def get_activist_signal(ticker: str, days: int = 60) -> dict:
    """Check for recent SC 13D (activist) filings for the ticker."""
    result = {"activist_signal": False, "activist_detail": ""}
    try:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        resp = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={
                "q":         f'"{ticker}"',
                "forms":     "SC 13D",
                "dateRange": "custom",
                "startdt":   since,
                "enddt":     datetime.now().strftime("%Y-%m-%d"),
            },
            headers={"User-Agent": "EndToEndTrading research@trading.com"},
            timeout=15
        )
        if resp.status_code != 200:
            return result

        hits = resp.json().get("hits", {}).get("hits", [])
        if hits:
            result["activist_signal"] = True
            filer = hits[0].get("_source", {}).get("entity_name", "unknown")
            result["activist_detail"] = f"13D filed by {filer}"
    except Exception as e:
        log.warning(f"Activist signal check failed for {ticker}: {e}")
    return result

# ----------------------------------------------------------------------------------------------------------------------
# 8. SENATE SIGNAL — Quiver Quant + qualified senator list
# ----------------------------------------------------------------------------------------------------------------------
def get_senate_signal(ticker: str, days: int = 30) -> dict:
    """
    Check Capitol Trades for recent Senate equity purchases in this ticker
    by senators in our qualified list (senator_scores where qualified=true).
    """
    result = {"senate_signal": False, "senate_senator": "", "senate_detail": ""}
    try:
        # Get qualified senators from Supabase
        db = get_db()
        try:
            rows = db.run(
                "select senator_name from senator_scores where qualified = true"
            )
            qualified = [r[0].lower() for r in rows] if rows else []
        finally:
            db.close()

        if not qualified:
            result["senate_detail"] = "No qualified senators scored yet"
            return result

        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        resp = requests.get(
            "https://www.capitoltrades.com/trades",
            params={
                "chamber":   "senate",
                "assetType": "stock",
                "txType":    "purchase",
                "issuer":    ticker,
                "pageSize":  20,
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15
        )

        # Capitol Trades is JS-rendered; attempt to parse any visible data
        # If empty, signal stays false — weekend scoring routine handles full pull
        text = resp.text.lower()
        matched = [s for s in qualified if s in text]
        if matched:
            result["senate_signal"]  = True
            result["senate_senator"] = matched[0]
            result["senate_detail"]  = f"Senate buy: {matched[0].title()} in {ticker}"

    except Exception as e:
        log.warning(f"Senate signal check failed for {ticker}: {e}")
    return result

# ----------------------------------------------------------------------------------------------------------------------
# 8b. ELITE SENATOR / POTUS PRIMARY SIGNAL
# ----------------------------------------------------------------------------------------------------------------------
def get_potus_elite_senate_primary(ticker: str, days_senate: int = 60,
                                   days_potus: int = 90) -> dict:
    """
    Primary signal — fires when a PROVEN top-performer or the President acts
    on this stock.  Two independent triggers:

    A) Elite senator buy
       Senators with win_rate >= 0.70 AND qualified=true in senator_scores.
       These are not merely "disclosed traders" — they have a documented track
       record of outperforming the market.  When they buy, it matters.
       Lookback: 60 days (covers the 45-day congressional disclosure window).

    B) Presidential mention / recommendation
       Check social_mentions table for any Trump entry on this ticker within
       the last 90 days.  Presidential stock picks move markets regardless of
       the fundamental thesis — that directional pressure is real.

    Returns:
        elite_senate_primary  bool   — elite senator bought this stock
        elite_senator_name    str    — name of the senator (if A fired)
        potus_primary         bool   — presidential mention/recommendation found
        potus_detail          str    — context of the mention (if B fired)
        primary_fired         bool   — True if either A or B fired
        primary_source        str    — "ELITE_SENATE" | "POTUS" | "BOTH" | ""
    """
    result = {
        "elite_senate_primary": False,
        "elite_senator_name":   "",
        "potus_primary":        False,
        "potus_detail":         "",
        "primary_fired":        False,
        "primary_source":       "",
    }

    # ── A) Elite senator buy ──────────────────────────────────────────────────────────────────────────────────────────
    try:
        db = get_db()
        try:
            rows = db.run(
                """select senator_name from senator_scores
                   where qualified = true
                     and win_rate  >= 0.70
                   order by score desc"""
            )
            elite = [r[0].lower() for r in rows] if rows else []
        finally:
            db.close()

        if elite:
            resp = requests.get(
                "https://www.capitoltrades.com/trades",
                params={
                    "chamber":   "senate",
                    "assetType": "stock",
                    "txType":    "purchase",
                    "issuer":    ticker,
                    "pageSize":  20,
                },
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15
            )
            text = resp.text.lower()
            matched = [s for s in elite if s in text]
            if matched:
                result["elite_senate_primary"] = True
                result["elite_senator_name"]   = matched[0]
                log.info(f"{ticker}: ELITE SENATOR BUY — {matched[0].title()} "
                         f"(win_rate>=70%)")

    except Exception as e:
        log.warning(f"Elite senate primary check failed for {ticker}: {e}")

    # ── B) Presidential mention ───────────────────────────────────────────────────────────────────────────────────────
    try:
        db = get_db()
        try:
            since = (datetime.now() - timedelta(days=days_potus)).strftime("%Y-%m-%d")
            rows = db.run(
                """select author, post_text from social_mentions
                   where exists (select 1 from unnest(tickers_found) t
                                 where lower(t) = lower(:tk))
                     and lower(author) like '%trump%'
                     and post_time >= :since
                   order by post_time desc
                   limit 1""",
                tk=ticker, since=since
            )
        finally:
            db.close()

        if rows:
            author, post_text = rows[0][0], rows[0][1]
            result["potus_primary"] = True
            result["potus_detail"]  = f"POTUS mention ({author}): {str(post_text)[:120]}"
            log.info(f"{ticker}: POTUS PRIMARY — presidential mention in last {days_potus}d")

    except Exception as e:
        log.warning(f"POTUS primary check failed for {ticker}: {e}")

    # ── Combine ───────────────────────────────────────────────────────────────────────────────────────────────────────
    if result["elite_senate_primary"] and result["potus_primary"]:
        result["primary_fired"]  = True
        result["primary_source"] = "BOTH"
    elif result["elite_senate_primary"]:
        result["primary_fired"]  = True
        result["primary_source"] = "ELITE_SENATE"
    elif result["potus_primary"]:
        result["primary_fired"]  = True
        result["primary_source"] = "POTUS"

    return result


# ----------------------------------------------------------------------------------------------------------------------
# 9. SUPERINVESTOR SIGNAL — Dataroma
# ----------------------------------------------------------------------------------------------------------------------
SUPERINVESTORS = [
    ("Berkshire Hathaway", "BRK"),
    ("Pershing Square",    "PS"),
    ("Scion",              "MSB"),
    ("Appaloosa",          "DAV"),
    ("Icahn Capital",      "ICA"),
    ("Baupost",            "BAU"),
    ("Tiger Global",       "CHA"),
    ("Fundsmith",          "TER"),
    ("Trian",              "NEL"),
]

def get_superinvestor_signal(ticker: str) -> dict:
    """
    Check Supabase notable_investors table for recent buys of this ticker.
    Table is refreshed weekly by the weekend routine.
    """
    result = {"notable_investor": "", "superinvestor_signal": False}
    try:
        db = get_db()
        try:
            rows = db.run(
                """select investor_name, action, disclosed_at
                   from notable_investors
                   where ticker = :t
                     and action in ('BUY','NEW','ADD')
                     and disclosed_at >= current_date - interval '90 days'
                   order by disclosed_at desc
                   limit 3""",
                t=ticker
            )
            if rows:
                result["superinvestor_signal"] = True
                names = ", ".join(r[0] for r in rows)
                result["notable_investor"] = names
        finally:
            db.close()
    except Exception as e:
        log.warning(f"Superinvestor signal failed for {ticker}: {e}")
    return result

# ----------------------------------------------------------------------------------------------------------------------
# 10. SOCIAL MENTIONS — Trump (Truth Social) + Musk (X), last 24h
# ----------------------------------------------------------------------------------------------------------------------
def get_social_mentions(ticker: str) -> dict:
    """
    Check Supabase social_mentions table for ticker mentions in last 24 hours.
    Table populated by pre-session scan routine.
    """
    result = {"social_mention": "", "social_signal": False}
    try:
        db = get_db()
        try:
            rows = db.run(
                """select author, platform, sentiment, post_time
                   from social_mentions
                   where :t = any(tickers_found)
                     and post_time >= now() - interval '24 hours'
                   order by post_time desc
                   limit 3""",
                t=ticker
            )
            if rows:
                result["social_signal"] = True
                detail = ", ".join(f"{r[0]} ({r[2]})" for r in rows)
                result["social_mention"] = detail
        finally:
            db.close()
    except Exception as e:
        log.warning(f"Social mention check failed for {ticker}: {e}")
    return result

# ----------------------------------------------------------------------------------------------------------------------
# 11. ECONOMIC CALENDAR — risk filter
# ----------------------------------------------------------------------------------------------------------------------
def get_upcoming_events(minutes_ahead: int = 30) -> dict:
    """
    Check for major economic events in the next {minutes_ahead} minutes.
    Uses ForexFactory calendar RSS feed.
    Returns block_trading=True if high-impact event imminent.
    """
    result = {"block_trading": False, "event_detail": ""}
    try:
        resp = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=10
        )
        if resp.status_code != 200:
            return result

        now     = datetime.now(timezone.utc)
        cutoff  = now + timedelta(minutes=minutes_ahead)
        events  = resp.json()

        for ev in events:
            impact = ev.get("impact", "").upper()
            if impact != "HIGH":
                continue
            try:
                ev_time_str = ev.get("date", "") + " " + ev.get("time", "")
                ev_time = datetime.strptime(ev_time_str, "%Y-%m-%d %I:%M%p")
                ev_time = ev_time.replace(tzinfo=timezone.utc)
                if now <= ev_time <= cutoff:
                    result["block_trading"] = True
                    result["event_detail"]  = f"{ev.get('title')} at {ev.get('time')} UTC"
                    break
            except Exception:
                continue
    except Exception as e:
        log.warning(f"Economic calendar check failed: {e}")
    return result

# ----------------------------------------------------------------------------------------------------------------------
# 12. ATR COMPUTATION (for position sizing)
# ----------------------------------------------------------------------------------------------------------------------

def get_atr(ticker: str, period: int = 14) -> dict:
    """Compute 14-period ATR and stop distance from current price."""
    result = {"atr": None, "stop_distance": None, "atr_multiplier": ATR_MULTIPLIER_DEFAULT}
    yticker = YAHOO_MAP.get(ticker, ticker)
    mult    = ATR_MULTIPLIERS.get(ticker, ATR_MULTIPLIER_DEFAULT)
    result["atr_multiplier"] = mult
    try:
        t    = yf.Ticker(yticker)
        hist = t.history(period="30d", interval="1d")
        if len(hist) < period + 1:
            return result

        high  = hist["High"]
        low   = hist["Low"]
        close = hist["Close"]
        tr    = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)

        atr  = float(tr.rolling(period).mean().iloc[-1])
        result["atr"]           = round(atr, 4)
        result["stop_distance"] = round(atr * mult, 4)
    except Exception as e:
        log.warning(f"ATR computation failed for {ticker}: {e}")
    return result

# ----------------------------------------------------------------------------------------------------------------------
# 13. DYNAMIC INSTRUMENT DISCOVERY
# ----------------------------------------------------------------------------------------------------------------------
# Session instrument lists imported from config.py

def get_candidate_instruments(session_name: str) -> list:
    """
    Return list of candidate instruments for this session.
    Layer 1: static core instruments for the session.
    Layer 2: dynamic discovery via Yahoo Finance screener (high options volume).
    Filters: market cap > $10B, not already in open positions.

    US equity tickers (NYSE-listed) are excluded from the dynamic screener when
    the session is not US_OPEN or US_MONITOR — they cannot be traded outside
    NYSE hours (14:30–21:00 UTC) and including them wastes scan time, inflates
    signal_log with untradeable signals, and clutters Slack #signals.
    The static instrument lists (SESSION_INSTRUMENTS) already exclude US equities
    from AUS/UK sessions, so this guard applies only to screener additions.
    """
    candidates = list(SESSION_INSTRUMENTS.get(session_name, SESSION_INSTRUMENTS["US_OPEN"]))

    # Dynamic discovery — top movers by volume from Yahoo Finance
    # Only add US equities when the session can actually trade them (NYSE hours).
    US_SESSION = session_name in ("US_OPEN", "US_MONITOR", "POSITION_MONITOR")
    try:
        screener_tickers = ["TSLA","AMZN","GOOGL","AMD","PLTR","SOFI","RIVN",
                            "BAC","JPM","XOM","CVX","BABA","NIO","F","GM"]
        for ticker in screener_tickers:
            if ticker in candidates:
                continue
            # Skip US equities entirely in non-US sessions — they will be blocked
            # by the NYSE hours guard in ig_shim.open_trade() anyway, so scanning
            # them is pure waste.  Crypto and FX tickers (not in this list) are
            # always tradeable and are already in the static AUS/UK instrument lists.
            if not US_SESSION:
                continue
            try:
                t = yf.Ticker(ticker)
                info = t.fast_info
                mkt_cap = getattr(info, "market_cap", 0) or 0
                if mkt_cap >= 10_000_000_000:  # $10B minimum
                    candidates.append(ticker)
                    if len(candidates) >= 15:
                        break
            except Exception:
                continue
    except Exception as e:
        log.warning(f"Dynamic instrument discovery failed: {e}")

    return candidates[:15]

# ----------------------------------------------------------------------------------------------------------------------
# 14. FULL SIGNAL SCAN — one instrument
# ----------------------------------------------------------------------------------------------------------------------
def _get_analyst_signal(ticker: str) -> dict:
    """Wrapper for broker analyst signal. Fails gracefully."""
    try:
        from analyst_signals import get_analyst_signal
        return get_analyst_signal(ticker)
    except Exception as e:
        log.debug(f"Analyst signal unavailable for {ticker}: {e}")
        return {"signal": "NEUTRAL", "narrative": "", "upside_pct": None}


def _get_price_action(ticker: str) -> dict:
    """Wrapper for price action analysis. Fails gracefully."""
    try:
        from price_action import analyse_price_action
        return analyse_price_action(ticker)
    except Exception as e:
        log.debug(f"Price action analysis unavailable for {ticker}: {e}")
        return {"verdict": "WAIT", "pa_score": 0.0}


def _get_supply_demand(ticker: str) -> dict:
    """Wrapper for commodity supply & demand analysis. Fails gracefully."""
    try:
        from commodity_supply_demand import analyse_supply_demand
        return analyse_supply_demand(ticker)
    except Exception as e:
        log.debug(f"Supply/demand analysis unavailable for {ticker}: {e}")
        return {"signal": "NEUTRAL", "narrative": "", "stop_multiplier": 1.0, "size_multiplier": 1.0}


def get_commodity_macro_score(ticker: str, yield_spread: float = None) -> dict:
    """
    Return commodity macro score for instruments that are commodity assets.
    Non-commodity instruments return a neutral score.
    """
    COMMODITY_INSTRUMENTS = {"XAUUSD","XAGUSD","OIL","USOIL","COPPER","PLATINUM","PALLADIUM"}
    result = {"commodity_macro_score": None, "commodity_macro_summary": ""}

    if ticker not in COMMODITY_INSTRUMENTS:
        return result

    try:
        from commodity_macro import analyse_commodity_macro
        analysis = analyse_commodity_macro(yield_spread=yield_spread)
        score    = analysis["scores"].get(ticker)
        result["commodity_macro_score"]   = score
        result["commodity_macro_summary"] = analysis["summary"]
    except Exception as e:
        log.warning(f"Commodity macro score failed for {ticker}: {e}")
    return result


# ----------------------------------------------------------------------------------------------------------------------
# 15. ADX — trend strength filter
# ----------------------------------------------------------------------------------------------------------------------
def get_adx(ticker: str, period: int = 14) -> dict:
    """
    Average Directional Index — measures trend STRENGTH, not direction.
    ADX > 25 = strong trend (breakouts and momentum trades more reliable).
    ADX < 15 = ranging/choppy market (avoid breakout trades).
    Also returns +DI / -DI to indicate direction of dominant trend.
    """
    result = {"adx": None, "adx_signal": "NEUTRAL", "di_plus": None, "di_minus": None}
    yticker = YAHOO_MAP.get(ticker, ticker)
    try:
        t    = yf.Ticker(yticker)
        hist = t.history(period="60d", interval="1d")
        if len(hist) < period * 2:
            return result

        high  = hist["High"]
        low   = hist["Low"]
        close = hist["Close"]

        # True Range
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        # Directional Movement
        up_move   = high.diff()
        down_move = -low.diff()
        dm_plus  = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        dm_minus = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

        # Smoothed (Wilder's EMA = span = 2*period - 1)
        span     = 2 * period - 1
        atr_s    = tr.ewm(span=span,     adjust=False).mean()
        dm_plus_s  = dm_plus.ewm(span=span,  adjust=False).mean()
        dm_minus_s = dm_minus.ewm(span=span, adjust=False).mean()

        di_plus  = 100 * dm_plus_s  / atr_s.replace(0, np.nan)
        di_minus = 100 * dm_minus_s / atr_s.replace(0, np.nan)
        dx       = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
        adx      = dx.ewm(span=span, adjust=False).mean()

        adx_val      = round(float(adx.iloc[-1]),  1)
        di_plus_val  = round(float(di_plus.iloc[-1]),  1)
        di_minus_val = round(float(di_minus.iloc[-1]), 1)

        result["adx"]      = adx_val
        result["di_plus"]  = di_plus_val
        result["di_minus"] = di_minus_val

        if adx_val >= 25:
            result["adx_signal"] = "STRONG_TREND"
        elif adx_val <= 15:
            result["adx_signal"] = "WEAK_TREND"
        else:
            result["adx_signal"] = "NEUTRAL"

    except Exception as e:
        log.warning(f"ADX failed for {ticker}: {e}")
    return result


# ----------------------------------------------------------------------------------------------------------------------
# 16. OBV — On-Balance Volume divergence
# ----------------------------------------------------------------------------------------------------------------------
def get_obv_signal(ticker: str, period: int = 20) -> dict:
    """
    On-Balance Volume trend vs price trend over the last {period} days.
    Divergence = smart money moving opposite to price = leading signal.

    BULLISH_DIVERGENCE:  OBV trending up while price is flat/down
                         (institutions accumulating quietly — price will follow)
    BEARISH_DIVERGENCE:  OBV trending down while price is up
                         (institutions distributing — price likely to fall)
    CONFIRMING_BULLISH:  Both OBV and price trending up (strong uptrend)
    CONFIRMING_BEARISH:  Both trending down (strong downtrend)
    NEUTRAL:             No clear divergence or confirmation
    """
    result = {"obv_signal": "NEUTRAL", "obv_trend": None, "price_trend": None}
    yticker = YAHOO_MAP.get(ticker, ticker)
    try:
        t    = yf.Ticker(yticker)
        hist = t.history(period=f"{period + 10}d", interval="1d")
        if len(hist) < period:
            return result

        hist = hist.tail(period)
        close = hist["Close"]
        vol   = hist["Volume"]

        # OBV = cumulative sum of signed volume
        direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        obv = (direction * vol).cumsum()

        # Linear regression slopes to measure trend direction
        x = np.arange(len(obv))
        obv_slope   = float(np.polyfit(x, obv.values,   1)[0])
        price_slope = float(np.polyfit(x, close.values, 1)[0])

        # Normalise by mean to make comparable
        obv_norm   = obv_slope   / (abs(obv.mean())   + 1e-10)
        price_norm = price_slope / (abs(close.mean()) + 1e-10)

        result["obv_trend"]   = "UP" if obv_norm > 0 else "DOWN"
        result["price_trend"] = "UP" if price_norm > 0 else "DOWN"

        THRESHOLD = 0.0005   # meaningful slope vs noise
        obv_up    = obv_norm   >  THRESHOLD
        obv_down  = obv_norm   < -THRESHOLD
        price_up  = price_norm >  THRESHOLD
        price_down = price_norm < -THRESHOLD

        if obv_up and price_down:
            result["obv_signal"] = "BULLISH_DIVERGENCE"
        elif obv_down and price_up:
            result["obv_signal"] = "BEARISH_DIVERGENCE"
        elif obv_up and price_up:
            result["obv_signal"] = "CONFIRMING_BULLISH"
        elif obv_down and price_down:
            result["obv_signal"] = "CONFIRMING_BEARISH"

    except Exception as e:
        log.warning(f"OBV signal failed for {ticker}: {e}")
    return result


# ----------------------------------------------------------------------------------------------------------------------
# 17. VOLUME SIGNAL — today vs 20-day average
# ----------------------------------------------------------------------------------------------------------------------
def get_volume_signal(ticker: str) -> dict:
    """
    Compare today's volume to the 20-day average daily volume.
    HIGH_VOLUME (≥1.5×) alongside a directional options signal substitutes
    for a BB breakout as the second primary signal — institutional conviction
    expressed through both options activity AND unusual volume is actionable
    even without a price breakout from compression.
    """
    result = {"volume_signal": "NORMAL", "volume_ratio": None}
    yticker = YAHOO_MAP.get(ticker, ticker)
    try:
        t = yf.Ticker(yticker)
        hist = t.history(period="25d", interval="1d")
        if len(hist) < 2:
            return result
        avg_vol   = float(hist["Volume"].iloc[:-1].mean())
        today_vol = float(hist["Volume"].iloc[-1])
        if avg_vol > 0:
            ratio = round(today_vol / avg_vol, 2)
            result["volume_ratio"] = ratio
            if ratio >= 1.5:
                result["volume_signal"] = "HIGH_VOLUME"
            elif ratio < 0.5:
                result["volume_signal"] = "LOW_VOLUME"
    except Exception as e:
        log.warning(f"Volume signal failed for {ticker}: {e}")
    return result


def get_intraday_guard(ticker: str) -> dict:
    """
    Falling-knife guard — check whether this instrument has already made a
    violent intraday move that makes entry dangerous.

    Logic:
        atr_intraday = today's (high - low) as a % of today's open
        atr_daily    = 14-day ATR / yesterday's close  (% normalised)

        If |today's move| > 1.5 × daily ATR:
            → instrument already in a violent move, skip this bar
            → wait for next session when the candle has settled

    This catches: NVDA -8% at the open, a gap-up on earnings,
    a commodity spike on news — anything where price action has already
    moved far enough that ATR-based stops would be set incorrectly.

    Returns:
        block:       True if the instrument should be skipped this bar
        reason:      human-readable explanation
        move_pct:    today's open-to-current move %
        atr_pct:     14-day ATR as % of price (the benchmark)
    """
    result = {"block": False, "reason": "", "move_pct": None, "atr_pct": None}
    yticker = YAHOO_MAP.get(ticker, ticker)
    try:
        t    = yf.Ticker(yticker)
        hist = t.history(period="20d", interval="1d")
        if len(hist) < 5:
            return result

        # Today's intraday range
        today     = hist.iloc[-1]
        open_p    = float(today["Open"])
        close_p   = float(today["Close"])
        high_p    = float(today["High"])
        low_p     = float(today["Low"])
        if open_p <= 0:
            return result

        move_pct = abs(close_p - open_p) / open_p * 100

        # 14-day ATR as % of price (normalised so we can compare across instruments)
        high  = hist["High"]
        low   = hist["Low"]
        close = hist["Close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr      = float(tr.rolling(14).mean().iloc[-2])  # use yesterday's ATR (today not closed)
        atr_pct  = (atr / float(close.iloc[-2])) * 100

        result["move_pct"] = round(move_pct, 2)
        result["atr_pct"]  = round(atr_pct, 2)

        MULTIPLIER = INTRADAY_GUARD_ATR_MULTIPLIER   # from config.py — 2.0
        if move_pct > MULTIPLIER * atr_pct:
            result["block"]  = True
            direction_word   = "down" if close_p < open_p else "up"
            result["reason"] = (
                f"Intraday move {move_pct:.1f}% ({direction_word}) exceeds "
                f"{MULTIPLIER:.1f}× ATR ({atr_pct:.1f}%) — violent intraday move, "
                f"entry deferred to next session."
            )
            log.info(f"Intraday guard blocked {ticker}: {result['reason']}")

    except Exception as e:
        log.debug(f"Intraday guard failed for {ticker}: {e}")
    return result


# ----------------------------------------------------------------------------------------------------------------------
# 18. OPENING RANGE BREAKOUT (ORB) — first 30-min bar vs current price
# ----------------------------------------------------------------------------------------------------------------------
def get_orb_signal(ticker: str) -> dict:
    """
    Compare current price to the high/low of the first 30-minute bar of today's
    session. A break above the ORB high = bullish primary; below ORB low = bearish.

    Only meaningful once the opening bar has formed (30+ min into session).
    Returns INSIDE if price hasn't broken either level yet.
    """
    result = {"orb_signal": None, "orb_dir": None, "orb_high": None, "orb_low": None}
    yticker = YAHOO_MAP.get(ticker, ticker)
    try:
        t       = yf.Ticker(yticker)
        intraday = t.history(period="1d", interval="30m")
        if len(intraday) < 2:
            return result

        orb_high   = float(intraday["High"].iloc[0])
        orb_low    = float(intraday["Low"].iloc[0])
        last_close = float(intraday["Close"].iloc[-1])

        result["orb_high"] = round(orb_high, 4)
        result["orb_low"]  = round(orb_low, 4)

        if last_close > orb_high:
            result["orb_signal"] = "BREAKOUT"
            result["orb_dir"]    = "BULLISH"
        elif last_close < orb_low:
            result["orb_signal"] = "BREAKDOWN"
            result["orb_dir"]    = "BEARISH"
        else:
            result["orb_signal"] = "INSIDE"

    except Exception as e:
        log.warning(f"ORB signal failed for {ticker}: {e}")
    return result


# ----------------------------------------------------------------------------------------------------------------------
# 19. 52-WEEK HIGH/LOW BREAKOUT — momentum primary
# ----------------------------------------------------------------------------------------------------------------------
def get_52week_signal(ticker: str) -> dict:
    """
    Price at or beyond its 52-week extreme is a momentum primary signal.
    Within 0.5% of the 52-week high = AT_52W_HIGH (bullish).
    Within 0.5% of the 52-week low  = AT_52W_LOW  (bearish).
    """
    result = {
        "week52_signal":   None,
        "week52_dir":      None,
        "week52_high":     None,
        "week52_low":      None,
        "pct_from_high":   None,
        "pct_from_low":    None,
    }
    yticker = YAHOO_MAP.get(ticker, ticker)
    try:
        t    = yf.Ticker(yticker)
        hist = t.history(period="1y", interval="1d")
        if len(hist) < 50:
            return result

        week52_high = float(hist["High"].max())
        week52_low  = float(hist["Low"].min())
        last_close  = float(hist["Close"].iloc[-1])

        pct_from_high = (last_close - week52_high) / week52_high * 100
        pct_from_low  = (last_close - week52_low)  / week52_low  * 100

        result["week52_high"]   = round(week52_high, 4)
        result["week52_low"]    = round(week52_low, 4)
        result["pct_from_high"] = round(pct_from_high, 2)
        result["pct_from_low"]  = round(pct_from_low, 2)

        if pct_from_high >= -0.5:
            result["week52_signal"] = "AT_52W_HIGH"
            result["week52_dir"]    = "BULLISH"
        elif pct_from_low <= 0.5:
            result["week52_signal"] = "AT_52W_LOW"
            result["week52_dir"]    = "BEARISH"

    except Exception as e:
        log.warning(f"52-week signal failed for {ticker}: {e}")
    return result


# ----------------------------------------------------------------------------------------------------------------------
# 20. SECTOR ALIGNMENT — sector ETF direction as primary signal
# ----------------------------------------------------------------------------------------------------------------------
def get_sector_alignment(ticker: str) -> dict:
    """
    Check whether the ticker's sector ETF is directionally aligned.

    Uses VWAP position of the sector ETF (already computed intraday) as the
    directional proxy. If the whole sector is above VWAP the individual stock
    has wind behind it — and vice versa for shorts.

    Only fires for equities mapped in SECTOR_ETF_MAP.  FX, indices, commodities,
    and crypto are intentionally excluded (they have COT / macro signals instead).
    """
    result = {"sector_etf": None, "sector_dir": None, "sector_vwap_pos": None}
    etf = SECTOR_ETF_MAP.get(ticker)
    if not etf:
        return result
    result["sector_etf"] = etf
    try:
        # Reuse get_vwap_position — ETF tickers pass straight through to Yahoo Finance
        vwap_data = get_vwap_position(etf)
        pos = vwap_data.get("vwap_position")
        result["sector_vwap_pos"] = pos
        result["sector_dir"] = "BULLISH" if pos == "ABOVE" else "BEARISH" if pos == "BELOW" else None
    except Exception as e:
        log.warning(f"Sector alignment failed for {ticker} ({etf}): {e}")
    return result


def scan_instrument(ticker: str, session_name: str, macro: dict) -> dict:
    """
    Run the full signal stack for one instrument.
    Returns a complete signal dict ready for Claude to evaluate.
    """
    log.info(f"Scanning {ticker}...")

    # ── Intraday guard ────────────────────────────────────────────────────────────────────────────────────────────────
    # Skip instruments that have already made a violent intraday move.
    # A stock down 8% at the open is a falling knife — our ATR-based stop will
    # be set incorrectly and the PA signals (which use daily closes) won't
    # reflect today's damage until end of day.
    guard = get_intraday_guard(ticker)
    if guard.get("block"):
        # Return a minimal WAIT signal — no point running the full stack
        return {
            "ticker":          ticker,
            "session":         session_name,
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "macro_gate_pass": macro.get("macro_gate_pass"),
            "trade_signal":    False,
            "direction":       None,
            "primary_count":   0,
            "confirmation_count": 0,
            "intraday_blocked": True,
            "intraday_reason": guard["reason"],
            "move_pct":        guard["move_pct"],
            "atr_pct":         guard["atr_pct"],
        }

    options   = get_options_signal(ticker)
    squeeze   = get_bb_squeeze(ticker)
    volume    = get_volume_signal(ticker)
    adx       = get_adx(ticker)
    obv       = get_obv_signal(ticker)
    gex       = get_gex_bias(ticker)
    vwap      = get_vwap_position(ticker)
    orb       = get_orb_signal(ticker)
    week52    = get_52week_signal(ticker)
    sector    = get_sector_alignment(ticker)

    cot       = get_cot_bias(ticker)
    comm_macro   = get_commodity_macro_score(ticker, macro.get("yield_spread"))
    supply_demand = _get_supply_demand(ticker)
    price_act  = _get_price_action(ticker)
    analyst    = _get_analyst_signal(ticker)
    directors  = get_director_buys(ticker)
    activist  = get_activist_signal(ticker)
    senate    = get_senate_signal(ticker)
    potus_sen = get_potus_elite_senate_primary(ticker)
    superinv  = get_superinvestor_signal(ticker)
    social    = get_social_mentions(ticker)
    atr_data  = get_atr(ticker)

    # ── Data reconciliation ───────────────────────────────────────────────────────────────────────────────────────────
    # Check that core price-data sources actually returned values.
    # A ticker with a bad Yahoo Finance mapping silently returns neutral dicts
    # that look identical to a genuine no-signal result — impossible to distinguish
    # without an explicit check. If core data is absent, block the trade decision
    # and surface the failure so it can be investigated.
    #
    # Core sources (all use daily OHLCV — must have data to be meaningful):
    #   volume  → volume_ratio    bb   → bb_squeeze
    #   adx     → adx             pa   → range_high (from price_action.py)
    #   atr     → atr             vwap → vwap (intraday)
    #
    # Non-penalised absences (legitimately None for many instruments):
    #   options (no chain for FX/indices/crypto)
    #   cot     (no CFTC coverage for equities)
    #   gex     (needs options chain)
    # FX/spot pairs (Yahoo "...=X") have no centralised volume — do NOT penalise
    # them for its absence (expected, not a data failure). This both removes the
    # log noise and stops FX from sitting one source closer to the critical
    # threshold. See task C (GBPUSD "missing volume" false alarm).
    _is_fx = YAHOO_MAP.get(ticker, ticker).endswith("=X")
    data_health = {
        "bb":     squeeze.get("bb_squeeze")         is not None,
        "adx":    adx.get("adx")                    is not None,
        "pa":     price_act.get("range_high")       is not None,
        "atr":    atr_data.get("atr")               is not None,
        "vwap":   vwap.get("vwap")                  is not None,
    }
    if not _is_fx:
        data_health["volume"] = volume.get("volume_ratio") is not None
    data_failures   = [src for src, ok in data_health.items() if not ok]
    data_ok         = len(data_failures) == 0
    # Partial failure: ≥3 core sources missing = no reliable decision possible
    data_critical   = len(data_failures) >= 3

    if data_failures:
        log.warning(f"{ticker}: data fetch failures — {', '.join(data_failures)}"
                    + (" [CRITICAL — trade blocked]" if data_critical else ""))

    if data_critical:
        return {
            "ticker":          ticker,
            "session":         session_name,
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "macro_gate_pass": macro.get("macro_gate_pass"),
            "trade_signal":    False,
            "direction":       None,
            "primary_count":   0,
            "confirmation_count": 0,
            "data_ok":         False,
            "data_failures":   data_failures,
            "data_critical":   True,
        }

    # Count primary signals aligned
    # Threshold: primary_count >= 2  OR  HVF fired alone (high conviction bypass)
    # Primary 1: Options flow      — call/put imbalance via Yahoo Finance / ETF proxies
    # Primary 2: BB breakout       — volatility compression breakout (or high-vol substitute)
    # Primary 3: HVF               — Hunt Volatility Funnel — standalone bypass allowed
    # Primary 4: ADX +DI/-DI       — directional index gap ≥5 with ADX ≥20 (trending market)
    # Primary 5: ORB breakout      — price beyond first 30-min session range
    # Primary 6: 52-week extreme   — price within 0.5% of 52-week high/low
    # Primary 7: Elite senator/POTUS — top-performing senator (win_rate≥70%) or
    #            presidential mention buying this equity
    # (Sector alignment demoted to confirmation — fires on too many equity scans as primary)
    primary_count = 0
    primary_dir   = []
    options_dir   = options.get("options_bias")
    bb_dir        = squeeze.get("bb_breakout_dir")
    high_volume   = volume.get("volume_signal") == "HIGH_VOLUME"

    # HVF: extract from price action result (computed once in _get_price_action)
    hvf_type      = price_act.get("hvf_type")        # "BULLISH" | "BEARISH" | None
    hvf_sig       = price_act.get("hvf_signal")       # "READY" | "TRIGGERED" | None
    hvf_fired     = hvf_type is not None and hvf_sig in ("READY", "TRIGGERED")

    if options_dir in ("BULLISH", "BEARISH"):
        primary_count += 1
        primary_dir.append(options_dir)

    if bb_dir in ("BULLISH", "BEARISH"):
        primary_count += 1
        primary_dir.append(bb_dir)
    elif high_volume:
        # High volume substitutes for BB breakout — direction from VWAP position
        # (price above VWAP on high volume = buyers in control; no options needed)
        vwap_pos = vwap.get("vwap_position")
        vol_dir  = "BULLISH" if vwap_pos == "ABOVE" else "BEARISH" if vwap_pos == "BELOW" else None
        if vol_dir:
            primary_count += 1
            primary_dir.append(vol_dir)
            log.info(f"{ticker}: HIGH volume ({volume.get('volume_ratio')}x) + price {vwap_pos} VWAP → {vol_dir}")

    if hvf_fired:
        primary_count += 1
        primary_dir.append(hvf_type)
        log.info(f"{ticker}: HVF {hvf_type} {hvf_sig} "
                 f"(quality={price_act.get('hvf_quality')} "
                 f"H3={price_act.get('hvf_h3_level')} "
                 f"target={price_act.get('hvf_target')} "
                 f"R:R={price_act.get('hvf_risk_reward')})")

    # ADX directional primary — +DI/-DI gap ≥5 in a trending market (ADX ≥20)
    adx_val  = adx.get("adx")
    di_plus  = adx.get("di_plus")
    di_minus = adx.get("di_minus")
    adx_dir  = None
    if adx_val is not None and di_plus is not None and di_minus is not None:
        if adx_val >= 20 and abs(di_plus - di_minus) >= 5:
            adx_dir = "BULLISH" if di_plus > di_minus else "BEARISH"
            primary_count += 1
            primary_dir.append(adx_dir)
            log.info(f"{ticker}: ADX directional +DI={di_plus} -DI={di_minus} ADX={adx_val} → {adx_dir}")

    # ORB primary — price broke beyond the first 30-min session range
    orb_dir = orb.get("orb_dir")
    if orb_dir in ("BULLISH", "BEARISH"):
        primary_count += 1
        primary_dir.append(orb_dir)
        log.info(f"{ticker}: ORB {orb.get('orb_signal')} high={orb.get('orb_high')} low={orb.get('orb_low')} → {orb_dir}")

    # 52-week extreme primary — price at yearly high or low
    week52_dir = week52.get("week52_dir")
    if week52_dir in ("BULLISH", "BEARISH"):
        primary_count += 1
        primary_dir.append(week52_dir)
        log.info(f"{ticker}: {week52.get('week52_signal')} ({week52.get('pct_from_high')}% from 52w high, "
                 f"{week52.get('pct_from_low')}% from 52w low) → {week52_dir}")

    # Elite senator / POTUS primary — direction inferred from the buy itself
    # (a disclosed purchase is inherently bullish; POTUS mentions are context-driven
    # but historically bullish for the named stock)
    if potus_sen.get("primary_fired"):
        primary_count += 1
        primary_dir.append("BULLISH")
        log.info(f"{ticker}: ELITE/POTUS PRIMARY — {potus_sen.get('primary_source')} "
                 f"({potus_sen.get('elite_senator_name') or potus_sen.get('potus_detail','')})")

    sector_dir = sector.get("sector_dir")

    # Direction consensus — must be computed before OBV conf checks below
    direction = None
    if primary_dir:
        bullish = primary_dir.count("BULLISH")
        bearish = primary_dir.count("BEARISH")
        if bullish > bearish:
            direction = "BUY"
        elif bearish > bullish:
            direction = "SELL"

    # Count confirmation signals — every confirmation must AGREE WITH THE TRADE
    # DIRECTION (user 2026-06-11: "Bearish is not confirmation for a buy").
    # Insider/political/social buying evidence is long-side only; COT must match
    # the side; ADX strength counts only when the dominant DI matches the side.
    conf_count = 0
    _adx_trend_up = (di_plus or 0) > (di_minus or 0)
    if direction == "BUY":
        if directors.get("director_signal"):      conf_count += 1
        if activist.get("activist_signal"):       conf_count += 1
        if senate.get("senate_signal"):           conf_count += 1
        if superinv.get("superinvestor_signal"):  conf_count += 1
        if social.get("social_signal"):           conf_count += 1
    if (cot.get("bias") == "BULLISH" and direction == "BUY") or \
       (cot.get("bias") == "BEARISH" and direction == "SELL"):
        conf_count += 1
    if adx.get("adx_signal") == "STRONG_TREND" and \
       ((_adx_trend_up and direction == "BUY") or (not _adx_trend_up and direction == "SELL")):
        conf_count += 1
    if obv.get("obv_signal") in ("BULLISH_DIVERGENCE","CONFIRMING_BULLISH") and direction == "BUY":  conf_count += 1
    if obv.get("obv_signal") in ("BEARISH_DIVERGENCE","CONFIRMING_BEARISH") and direction == "SELL": conf_count += 1
    # Commodity macro score — only fires for commodity instruments (returns None for everything else)
    comm_score = comm_macro.get("commodity_macro_score")
    if comm_score is not None:
        if comm_score > 0 and direction == "BUY":   conf_count += 1
        elif comm_score < 0 and direction == "SELL": conf_count += 1
    # Sector alignment — sector ETF VWAP position aligns with direction (equity-only)
    if sector_dir in ("BULLISH", "BEARISH"):
        if (sector_dir == "BULLISH" and direction == "BUY") or \
           (sector_dir == "BEARISH" and direction == "SELL"):
            conf_count += 1
            log.info(f"{ticker}: sector ETF {sector.get('sector_etf')} {sector.get('sector_vwap_pos')} VWAP → conf +1")

    # Named lists of which signals actually fired — mirrors the counting above so the
    # trade-open email can EXPLAIN the trade (not just show counts). User 2026-06-10.
    primaries_fired = []
    if options_dir in ("BULLISH", "BEARISH"):
        # Show the evidence behind the bias: call/put volume ratio, IV rank, and
        # dealer gamma (GEX) bias when non-neutral. User 2026-06-10.
        _opt_bits = []
        if options.get("call_put_ratio") is not None:
            _opt_bits.append(f"call/put {options['call_put_ratio']:.2f}")
        if options.get("iv_rank") is not None:
            _opt_bits.append(f"IV rank {options['iv_rank']}%")
        if gex.get("gex_bias") and gex.get("gex_bias") != "NEUTRAL":
            _opt_bits.append(f"GEX {gex['gex_bias']}")
        primaries_fired.append(
            f"Options flow {options_dir}" + (f" — {', '.join(_opt_bits)}" if _opt_bits else ""))
    if bb_dir in ("BULLISH", "BEARISH"):
        primaries_fired.append(f"BB breakout {bb_dir}")
    elif high_volume and vwap.get("vwap_position") in ("ABOVE", "BELOW"):
        primaries_fired.append(f"High volume + price {vwap.get('vwap_position')} VWAP")
    if hvf_fired:
        primaries_fired.append(f"HVF {hvf_type} {hvf_sig} (R:R {price_act.get('hvf_risk_reward')}, "
                               f"quality {price_act.get('hvf_quality')})")
    if adx_dir:
        primaries_fired.append(f"ADX directional {adx_dir} (+DI {di_plus} / -DI {di_minus}, ADX {adx_val})")
    if orb_dir in ("BULLISH", "BEARISH"):
        primaries_fired.append(f"ORB breakout {orb_dir}")
    if week52_dir in ("BULLISH", "BEARISH"):
        primaries_fired.append(f"52-week extreme {week52_dir}")
    if potus_sen.get("primary_fired"):
        primaries_fired.append(f"Elite senator / POTUS ({potus_sen.get('primary_source', '')} "
                               f"{potus_sen.get('elite_senator_name') or potus_sen.get('potus_detail', '')})".strip())

    confirmations_fired = []
    # Mirrors the direction-aligned counting above exactly — a confirmation that
    # disagrees with the trade side must neither count NOR appear in the list
    # (user 2026-06-11: "Bearish is not confirmation for a buy").
    if direction == "BUY" and directors.get("director_signal"):
        # director_detail already reads "N insiders bought in last 30d (Form 4): NAMES".
        confirmations_fired.append("Director buys — " +
                                   (directors.get("director_detail") or "insider cluster (Form 4)"))
    if direction == "BUY" and activist.get("activist_signal"):
        confirmations_fired.append(f"Activist 13D — {activist.get('activist_detail', '')}".strip(" —"))
    if direction == "BUY" and senate.get("senate_signal"):
        confirmations_fired.append(f"Senate buy — {senate.get('senate_senator', '')}".strip(" —"))
    if direction == "BUY" and superinv.get("superinvestor_signal"):
        confirmations_fired.append(f"Superinvestor — {superinv.get('notable_investor', '')}".strip(" —"))
    if direction == "BUY" and social.get("social_signal"):
        confirmations_fired.append("Social mention")
    if (cot.get("bias") == "BULLISH" and direction == "BUY") or \
       (cot.get("bias") == "BEARISH" and direction == "SELL"):
        # Show what's driving the COT bias: composite score, positioning extremes,
        # open-interest signal, price divergence and commercial net + WoW change.
        _cot_bits = []
        if cot.get("cot_score"):
            _cot_bits.append(f"score {cot['cot_score']:+.0f}")
        if cot.get("comm_extreme") and cot["comm_extreme"] != "NORMAL":
            _cot_bits.append(f"commercials {cot['comm_extreme']}")
        if cot.get("mm_extreme") and cot["mm_extreme"] != "NORMAL":
            _cot_bits.append(f"managed-money {cot['mm_extreme']}")
        if cot.get("oi_signal") and cot["oi_signal"] != "NEUTRAL":
            _cot_bits.append(f"OI {cot['oi_signal']}")
        if cot.get("price_divergence") and cot["price_divergence"] != "NONE":
            _cot_bits.append(f"price divergence {cot['price_divergence']}")
        if cot.get("comm_net") is not None:
            _net = f"commercials net {int(cot['comm_net']):+,}"
            if cot.get("comm_net_change") is not None:
                _net += f" ({int(cot['comm_net_change']):+,} WoW)"
            _cot_bits.append(_net)
        confirmations_fired.append(
            f"COT positioning {cot.get('bias')}" + (f" — {', '.join(_cot_bits)}" if _cot_bits else ""))
    if adx.get("adx_signal") == "STRONG_TREND" and \
       ((_adx_trend_up and direction == "BUY") or (not _adx_trend_up and direction == "SELL")):
        confirmations_fired.append(
            f"ADX strong trend {'up' if _adx_trend_up else 'down'} (+DI {di_plus} / -DI {di_minus})")
    if (obv.get("obv_signal") in ("BULLISH_DIVERGENCE", "CONFIRMING_BULLISH") and direction == "BUY") or \
       (obv.get("obv_signal") in ("BEARISH_DIVERGENCE", "CONFIRMING_BEARISH") and direction == "SELL"):
        confirmations_fired.append(f"OBV {obv.get('obv_signal')}")
    if comm_score is not None and ((comm_score > 0 and direction == "BUY") or (comm_score < 0 and direction == "SELL")):
        confirmations_fired.append("Commodity macro aligned")
    if sector_dir in ("BULLISH", "BEARISH") and \
       ((sector_dir == "BULLISH" and direction == "BUY") or (sector_dir == "BEARISH" and direction == "SELL")):
        confirmations_fired.append(f"Sector ETF {sector.get('sector_etf', '')} aligned".strip())

    # Trade fires when: macro gate passes + primary threshold + 1 confirmation + PA confirms
    # Primary threshold: primary_count >= 2  OR  HVF fired alone
    # HVF bypass: HVF is high enough conviction to pass the primary stage unassisted.
    # Price action must confirm direction — prevents catching falling knives.
    pa_verdict       = price_act.get("verdict", "WAIT")
    pa_confirms_long  = pa_verdict == "CONFIRM_LONG"  and direction == "BUY"
    pa_confirms_short = pa_verdict == "CONFIRM_SHORT" and direction == "SELL"
    pa_confirmed      = pa_confirms_long or pa_confirms_short

    # Standalone bypasses — signals high-conviction enough to pass primary gate alone:
    #   HVF:              complete setup with entry/stop/target pre-calculated
    #   Elite senate:     senator with ≥70% win-rate disclosed a buy
    #   POTUS:            presidential mention/recommendation
    #   Either or both of the above two count — no second primary needed
    potus_or_senate = potus_sen.get("primary_fired", False)
    primary_gate = (primary_count >= MIN_PRIMARY_SIGNALS) or hvf_fired or potus_or_senate

    trade_signal = (
        macro.get("macro_gate_pass", False) and
        primary_gate and
        conf_count >= MIN_CONFIRMATION_SIGNALS and
        direction is not None and
        pa_confirmed                               # Price action must confirm
    )

    signal = {
        "ticker":            ticker,
        "session":           session_name,
        "timestamp":         datetime.now(timezone.utc).isoformat(),

        # Macro
        "macro_gate_pass":   macro.get("macro_gate_pass"),
        "vix":               macro.get("vix"),
        "yield_spread":      macro.get("yield_spread"),

        # Primary signals
        "options_bias":      options.get("options_bias"),
        "call_put_ratio":    options.get("call_put_ratio"),
        "iv_rank":           options.get("iv_rank"),
        "bb_squeeze":        squeeze.get("bb_squeeze"),
        "bb_breakout_dir":   squeeze.get("bb_breakout_dir"),
        "volume_signal":     volume.get("volume_signal"),
        "volume_ratio":      volume.get("volume_ratio"),
        "adx":               adx.get("adx"),
        "adx_signal":        adx.get("adx_signal"),
        "di_plus":           adx.get("di_plus"),
        "di_minus":          adx.get("di_minus"),
        "obv_signal":        obv.get("obv_signal"),
        "obv_trend":         obv.get("obv_trend"),

        # Confirmation signals
        "gex_bias":          gex.get("gex_bias"),
        "vwap_position":     vwap.get("vwap_position"),
        "cot_bias":              cot.get("bias"),
        "cot_score":             cot.get("cot_score"),
        "cot_comm_extreme":      cot.get("comm_extreme"),
        "cot_mm_extreme":        cot.get("mm_extreme"),
        "cot_price_divergence":  cot.get("price_divergence"),
        "cot_oi_signal":         cot.get("oi_signal"),
        "cot_comm_net":          cot.get("comm_net"),
        "cot_comm_net_change":   cot.get("comm_net_change"),
        "commodity_macro_score":    comm_macro.get("commodity_macro_score"),
        "commodity_macro_summary":  comm_macro.get("commodity_macro_summary"),
        "supply_demand_signal":     supply_demand.get("signal"),
        "supply_demand_narrative":  supply_demand.get("narrative"),
        "geo_stop_multiplier":      supply_demand.get("stop_multiplier", 1.0),
        "geo_size_multiplier":      supply_demand.get("size_multiplier", 1.0),

        # Price action confirmation
        "pa_verdict":               price_act.get("verdict"),         # CONFIRM_LONG / CONFIRM_SHORT / WAIT
        "pa_score":                 price_act.get("pa_score"),
        "pa_range_breakout":        price_act.get("range_breakout"),
        "pa_trend_structure":       price_act.get("trend_structure"),
        "pa_ma_signal":             price_act.get("ma_signal"),
        "pa_failed_break":          price_act.get("failed_break"),
        "pa_atr_compressed":        price_act.get("atr_compressed"),
        "pa_atr_expanding":         price_act.get("atr_expanding"),

        # Hunt Volatility Funnel (primary signal + pending-order entry levels)
        "hvf_type":          price_act.get("hvf_type"),       # BULLISH / BEARISH / None
        "hvf_signal":        price_act.get("hvf_signal"),     # READY / TRIGGERED / None
        "hvf_h3_level":      price_act.get("hvf_h3_level"),   # pending entry price
        "hvf_stop_level":    price_act.get("hvf_stop_level"), # exact stop price
        "hvf_target":        price_act.get("hvf_target"),     # H1-L1 range target
        "hvf_risk_reward":   price_act.get("hvf_risk_reward"),
        "hvf_quality":       price_act.get("hvf_quality"),    # 0-100 pattern quality
        # Funnel shape (for the trade-open email's HVF chart): lower-highs H1→H3,
        # higher-lows L1→L3.
        "hvf_h1_level":      price_act.get("hvf_h1_level"),
        "hvf_h2_level":      price_act.get("hvf_h2_level"),
        "hvf_l1_level":      price_act.get("hvf_l1_level"),
        "hvf_l2_level":      price_act.get("hvf_l2_level"),
        "hvf_l3_level":      price_act.get("hvf_l3_level"),
        # Pivot dates — let the trade-open email draw the funnel on the real price axis.
        "hvf_h1_date":       price_act.get("hvf_h1_date"),
        "hvf_h2_date":       price_act.get("hvf_h2_date"),
        "hvf_h3_date":       price_act.get("hvf_h3_date"),
        "hvf_l1_date":       price_act.get("hvf_l1_date"),
        "hvf_l2_date":       price_act.get("hvf_l2_date"),
        "hvf_l3_date":       price_act.get("hvf_l3_date"),

        # Analyst / broker signals
        "analyst_signal":           analyst.get("signal"),
        "analyst_upside_pct":       analyst.get("upside_pct"),
        "analyst_recommendation":   analyst.get("recommendation"),
        "analyst_count":            analyst.get("analyst_count"),
        "analyst_recent_upgrades":  analyst.get("recent_upgrades"),
        "analyst_narrative":        analyst.get("narrative"),
        "director_signal":         directors.get("director_signal"),
        "director_cluster_strong": directors.get("director_cluster_strong", False),
        "director_count":          directors.get("director_count"),
        "director_detail":         directors.get("director_detail"),
        "activist_signal":   activist.get("activist_signal"),
        "activist_detail":   activist.get("activist_detail"),
        "senate_signal":     senate.get("senate_signal"),
        "senate_senator":    senate.get("senate_senator"),
        "elite_senate_primary": potus_sen.get("elite_senate_primary"),
        "elite_senator_name":   potus_sen.get("elite_senator_name"),
        "potus_primary":        potus_sen.get("potus_primary"),
        "potus_detail":         potus_sen.get("potus_detail"),
        "notable_investor":  superinv.get("notable_investor"),
        "social_mention":    social.get("social_mention"),

        # Sizing
        "atr":               atr_data.get("atr"),
        "stop_distance":     atr_data.get("stop_distance"),
        "atr_multiplier":    atr_data.get("atr_multiplier"),

        # Decision inputs
        "primary_count":     primary_count,
        "confirmation_count": conf_count,
        "primaries_fired":   primaries_fired,      # named list — for the trade-open email
        "confirmations_fired": confirmations_fired,
        "direction":         direction,
        "trade_signal":      trade_signal,

        # Data reconciliation
        "data_ok":       data_ok,
        "data_failures": data_failures,   # [] if all sources returned data
    }

    # Log to Supabase
    # pg8000.native uses :param_name style. $N positional style fails with
    # "list index out of range" because pg8000 accesses args[N] not args[N-1].
    # Using verbose v_ prefixed names ensures the parser finds all placeholders.
    # Retry up to 2 times on pg8000 08P01 bind errors (prepared-statement cache
    # collision seen in production for OIL on 2026-06-04 — transient, self-clears
    # with a fresh connection on retry).
    _INSERT_MAX_RETRIES = 2
    for _insert_attempt in range(1, _INSERT_MAX_RETRIES + 2):
      try:
        db = get_db()
        db.run(
            """insert into signal_log
               (session, ticker, macro_gate_pass, options_bias, call_put_ratio, iv_rank,
                gex_bias, vwap_position, cot_bias, bb_squeeze, bb_breakout_dir,
                director_signal, director_cluster_strong, activist_signal, senate_signal, senate_senator,
                elite_senate_primary, elite_senator_name, potus_primary,
                notable_investor, social_mention, primary_count, confirmation_count,
                direction, pa_verdict, pa_score, trade_triggered,
                adx_signal, adx_dir, obv_signal, volume_signal, volume_ratio,
                hvf_type, hvf_signal, hvf_h3_level, hvf_stop_level,
                hvf_target, hvf_risk_reward, hvf_quality,
                orb_signal, orb_dir, week52_signal, week52_dir,
                sector_etf, sector_dir)
               values (:v_session, :v_ticker, :v_mgp, :v_opts_bias, :v_call_put, :v_ivr,
                       :v_gex_bias, :v_vwap_pos, :v_cot_bias, :v_bb_squeeze, :v_bb_breakout,
                       :v_director, :v_dir_cluster_strong, :v_activist, :v_senate, :v_senator_name,
                       :v_elite_senate, :v_elite_senator, :v_potus,
                       :v_notable, :v_social, :v_primaries, :v_confirms, :v_direction,
                       :v_pa_verdict, :v_pa_score, :v_triggered,
                       :v_adx, :v_adx_dir, :v_obv, :v_vol_sig, :v_vol_ratio,
                       :v_hvf_type, :v_hvf_sig, :v_hvf_h3, :v_hvf_stop,
                       :v_hvf_target, :v_hvf_rr, :v_hvf_quality,
                       :v_orb_sig, :v_orb_dir, :v_week52_sig, :v_week52_dir,
                       :v_sector_etf, :v_sector_dir)""",
            v_session=session_name, v_ticker=ticker,
            v_mgp=macro.get("macro_gate_pass"), v_opts_bias=options.get("options_bias"),
            v_call_put=options.get("call_put_ratio"), v_ivr=options.get("iv_rank"),
            v_gex_bias=gex.get("gex_bias"), v_vwap_pos=vwap.get("vwap_position"),
            v_cot_bias=cot.get("bias"), v_bb_squeeze=squeeze.get("bb_squeeze"),
            v_bb_breakout=squeeze.get("bb_breakout_dir"),
            v_director=directors.get("director_signal"),
            v_dir_cluster_strong=directors.get("director_cluster_strong", False),
            v_activist=activist.get("activist_signal"),
            v_senate=senate.get("senate_signal"), v_senator_name=senate.get("senate_senator"),
            v_elite_senate=potus_sen.get("elite_senate_primary"),
            v_elite_senator=potus_sen.get("elite_senator_name"),
            v_potus=potus_sen.get("potus_primary"),
            v_notable=superinv.get("notable_investor"), v_social=social.get("social_mention"),
            v_primaries=primary_count, v_confirms=conf_count, v_direction=direction,
            v_pa_verdict=price_act.get("verdict"), v_pa_score=price_act.get("pa_score"),
            v_triggered=trade_signal,
            v_adx=adx.get("adx_signal"), v_adx_dir=adx_dir,
            v_obv=obv.get("obv_signal"),
            v_vol_sig=volume.get("volume_signal"), v_vol_ratio=volume.get("volume_ratio"),
            v_hvf_type=price_act.get("hvf_type"),    v_hvf_sig=price_act.get("hvf_signal"),
            v_hvf_h3=price_act.get("hvf_h3_level"),  v_hvf_stop=price_act.get("hvf_stop_level"),
            v_hvf_target=price_act.get("hvf_target"), v_hvf_rr=price_act.get("hvf_risk_reward"),
            v_hvf_quality=price_act.get("hvf_quality"),
            v_orb_sig=orb.get("orb_signal"), v_orb_dir=orb_dir,
            v_week52_sig=week52.get("week52_signal"), v_week52_dir=week52_dir,
            v_sector_etf=sector.get("sector_etf"), v_sector_dir=sector_dir
        )
        db.close()
        break  # success — exit retry loop
      except Exception as e:
        try:
            db.close()
        except Exception:
            pass
        if _insert_attempt <= _INSERT_MAX_RETRIES:
            log.warning(f"signal_log INSERT attempt {_insert_attempt} failed for {ticker}: {e} — retrying")
            time.sleep(1)
        else:
            log.warning(f"Failed to log signal for {ticker} after {_INSERT_MAX_RETRIES + 1} attempts: {e}")
            signal["_log_failed"] = True
            signal["_log_error"]  = str(e)

    return signal

# ----------------------------------------------------------------------------------------------------------------------
# 15. SESSION SCAN — all instruments
# ----------------------------------------------------------------------------------------------------------------------
def run_session_scan(session_name: str) -> dict:
    """
    Run the full signal scan for a session.
    Returns macro conditions + list of instrument signals.
    Intended to be called by the Claude Routine at session open.
    """
    log.info(f"Starting session scan: {session_name}")

    # Check economic calendar first
    calendar = get_upcoming_events(minutes_ahead=30)
    if calendar["block_trading"]:
        log.warning(f"Trading blocked by economic calendar: {calendar['event_detail']}")
        return {
            "session":       session_name,
            "block_trading": True,
            "block_reason":  calendar["event_detail"],
            "instruments":   []
        }

    macro      = get_macro_gate(session_name)
    candidates = get_candidate_instruments(session_name)

    instrument_signals = []
    scan_errors = []
    for ticker in candidates:
        try:
            sig = scan_instrument(ticker, session_name, macro)
            instrument_signals.append(sig)
            time.sleep(0.5)   # be polite to Yahoo Finance
        except Exception as e:
            log.error(f"Scan failed for {ticker}: {e}")
            scan_errors.append(f"{ticker}: {e}")

    # Alert on DB logging failures, scan exceptions, and data reconciliation failures
    try:
        from notify import alert_system_error

        # 1. DB write failures
        log_failures = [
            (s["ticker"], s.get("_log_error", ""))
            for s in instrument_signals if s.get("_log_failed")
        ]
        if log_failures:
            tickers_str = ", ".join(t for t, _ in log_failures[:10])
            first_error = log_failures[0][1]
            alert_system_error(
                session=session_name,
                component="signal_log INSERT",
                summary=f"{len(log_failures)}/{len(instrument_signals)} instruments failed to write to signal_log",
                detail=f"Tickers: {tickers_str}\nError: {first_error}"
            )

        # 2. Scan exceptions (ticker crashed completely)
        if scan_errors and len(scan_errors) >= max(3, len(candidates) // 2):
            alert_system_error(
                session=session_name,
                component="scan_instrument",
                summary=f"{len(scan_errors)}/{len(candidates)} instrument scans threw exceptions",
                detail="\n".join(scan_errors[:10])
            )

        # 3. Data reconciliation failures.
        #    Only a CRITICAL gap (one that actually blocked a trade) is a system
        #    error worth an "Action Required" alert. Non-critical gaps are logged
        #    for the record but never raise a #alerts error — e.g. FX/spot pairs
        #    have no centralised volume, so "GBPUSD: missing volume" is EXPECTED,
        #    not a failure. Previously ANY gap alerted, crying wolf with "0 critical".
        data_failures = [
            (s["ticker"], ", ".join(s.get("data_failures", [])))
            for s in instrument_signals
            if s.get("data_failures")
        ]
        if data_failures:
            ticker_critical = {sig["ticker"] for sig in instrument_signals if sig.get("data_critical")}
            critical = [(t, f) for t, f in data_failures if t in ticker_critical]
            # Always log every gap (verbose record for retrospective analysis).
            for ticker_fail, sources in data_failures:
                log.warning(f"Data gap: {ticker_fail} — missing {sources}")
            # Alert ONLY when a gap was critical enough to block a trade.
            if critical:
                lines = [f"{t}: missing {f}" for t, f in data_failures[:15]]
                alert_system_error(
                    session=session_name,
                    component="data reconciliation",
                    summary=(
                        f"{len(critical)}/{len(instrument_signals)} instruments had "
                        f"CRITICAL missing data (trade blocked)"
                    ),
                    detail="\n".join(lines)
                )

        # 4. Zero results
        if candidates and not instrument_signals:
            alert_system_error(
                session=session_name,
                component="run_session_scan",
                summary=f"0 instruments scanned from {len(candidates)} candidates — session produced no data",
                detail="\n".join(scan_errors[:10])
            )
    except Exception as e:
        log.warning(f"Failed to send system error alert: {e}")

    trade_candidates = [s for s in instrument_signals if s.get("trade_signal")]
    # Order by CONVICTION so the strongest setups are placed first — the trade
    # loop stops at the cap and slow spread-retries on weak names must not starve a
    # high-R:R setup (e.g. IBM HVF R:R 5.75 was 4th in list-order and never tried).
    # Rank: HVF risk:reward, then confirmation count, then primary count.
    trade_candidates.sort(
        key=lambda s: (s.get("hvf_risk_reward") or 0,
                       s.get("confirmation_count") or 0,
                       s.get("primary_count") or 0),
        reverse=True,
    )

    # Director cluster developing alert — 3+ insider Form 4 buys but no trade yet.
    # These are early-stage opportunities: insiders are accumulating but the technical
    # setup hasn't formed. Surface them so they can be watched.
    try:
        from notify import alert_director_cluster_developing
        strong_clusters = [
            {
                "ticker":         s.get("ticker", ""),
                "director_count": s.get("director_count", 0),
                "director_detail": s.get("director_detail", ""),
            }
            for s in instrument_signals
            if s.get("director_cluster_strong") and not s.get("trade_signal")
        ]
        if strong_clusters:
            alert_director_cluster_developing(session_name, strong_clusters)
            log.info(f"Director cluster developing alert: {len(strong_clusters)} ticker(s)")
    except Exception as e:
        log.warning(f"Director cluster alert failed: {e}")

    return {
        "session":           session_name,
        "timestamp":         datetime.now(timezone.utc).isoformat(),
        "block_trading":     False,
        "macro":             macro,
        "instruments_scanned": len(instrument_signals),
        "trade_candidates":  trade_candidates,
        "all_signals":       instrument_signals,
    }


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    result = run_session_scan("US_OPEN")
    # Print summary only — full signal data goes to Supabase
    print(f"\nSession: {result['session']}")
    print(f"Macro gate: {result['macro']['macro_gate_pass']} — {result['macro']['gate_reason']}")
    print(f"VIX: {result['macro']['vix']}  DXY: {result['macro']['dxy']}  Yield spread: {result['macro']['yield_spread']}")
    print(f"Instruments scanned: {result['instruments_scanned']}")
    print(f"Trade candidates: {len(result['trade_candidates'])}")
    for s in result["trade_candidates"]:
        print(f"  → {s['ticker']} {s['direction']} | primaries={s['primary_count']} confs={s['confirmation_count']}")
