# ======================================================================================================================
# File:         instrument_name.py
# Author:       Alex Hind
# Created:      2026-06-24
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# SINGLE SOURCE OF TRUTH for an instrument's full company name (user 2026-06-24: "the correct name should only be in one
# place"). Before this, the name was resolved in 3 disagreeing places — intraday_signals._resolve_name (yfinance-first),
# social_monitor.get_company_name (epic_lookup-first) and notify.fmt/_load_names (epic_lookup) — which is how a tracked-X
# dossier showed "MSTR (Morningstar International Shares Active ETF)" while the trade used Strategy Inc: the epic_lookup
# CACHE still held the stale wrong MSTR row, and the epic_lookup-first resolvers trusted it.
#
# Rule: yfinance longName/shortName FIRST (the authoritative company name, immune to a wrong IG epic mapping); the IG
# epic_lookup description is a FALLBACK only, for instruments yfinance cannot name (FX / indices / commodities, e.g.
# XAUUSD -> "Spot Gold"). Cached per process. All three former resolvers now delegate here.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.1.0   2026-06-30  Alex Hind   (user 2026-06-30) Curated index names ("DAX (Deutscher Aktienindex, Germany 40)" not
#                                 "DAX P") + FX-pair rule: both-legs-ISO-currency tickers render ALL-CAPS "GBP/CAD"
#                                 (the old title-caser produced "Gbp/cad"). XAUUSD/BTCUSD excluded via _FX_CCY.
# 1.0.0   2026-06-24  Alex Hind   (user 2026-06-24) Initial — consolidates _resolve_name / get_company_name / notify name
#                                 lookup into one company_name(). Fixes MSTR showing the Morningstar AU ETF in the dossier.
# ======================================================================================================================

import re

_NAME_CACHE: dict = {}   # ticker -> resolved name, per process
_INFO_CACHE: dict = {}   # ticker -> yfinance .info, per process


def _yf_info(ticker: str) -> dict:
    """yfinance .info for a ticker, fetched once per process. FX/indices need the Yahoo symbol via
    config.YAHOO_MAP (e.g. USDJPY -> USDJPY=X). Returns {} on any failure."""
    if ticker in _INFO_CACHE:
        return _INFO_CACHE[ticker]
    info = {}
    try:
        import yfinance as yf
        try:
            from config import YAHOO_MAP
        except Exception:
            YAHOO_MAP = {}
        yt = YAHOO_MAP.get(ticker, ticker)
        info = yf.Ticker(yt).info or {}
    except Exception:
        info = {}
    _INFO_CACHE[ticker] = info
    return info


def _clean_yf(name: str) -> str:
    """Clean a yfinance company name: drop share-class noise, normalise the legal suffix to PLC, and
    title-case an ALL-CAPS shortName while keeping acronyms. (Merged from _resolve_name.)"""
    if not name:
        return ""
    name = name.split(" ORD")[0].split(" REIT")[0].strip()
    name = re.sub(r"\s+Or(?:d(?:inary)?)?\s*$", "", name, flags=re.I).strip()
    name = re.sub(r"\s+Public\s+Limited\s+Company\s*$", " PLC", name, flags=re.I).strip()
    name = re.sub(r"[\s.]*[Pp]\.?[Ll]\.?[Cc]\.?\s*$", " PLC", name).strip()
    if name.isupper():
        name = " ".join(w if len(w) <= 4 else w.capitalize() for w in name.split())
    return name


def _clean_ig(desc: str) -> str:
    """Reduce an IG market description to the bare name. (Merged from notify._clean_name.)
        'CleanSpark Inc (24 Hours)' -> 'CleanSpark Inc';  'GBP/USD' -> 'GBP/USD'."""
    if not desc:
        return ""
    name = desc.split(" - ")[0]                       # drop editorial " - ..." notes
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name)      # drop trailing "(24 Hours)" etc.
    return name.strip()


def _from_epic_lookup(ticker: str) -> str:
    """FALLBACK: the IG description from epic_lookup (instruments yfinance cannot name). Tries the
    ticker and, for UK names, the .L-stripped key. Best-effort — '' on any DB error."""
    try:
        from db_pool import get_db
        conn = get_db()
        try:
            keys = [ticker, ticker[:-2]] if ticker.endswith(".L") and len(ticker) > 2 else [ticker]
            for k in keys:
                rows = conn.run("select description from epic_lookup where ticker = :t limit 1", t=k)
                if rows and rows[0][0]:
                    nm = _clean_ig(rows[0][0])
                    if nm and nm != ticker:
                        return nm
        finally:
            conn.close()
    except Exception:
        pass
    return ""


# Curated display names (user 2026-06-30): indices need a PRODUCT explanation, not yfinance's raw
# shortName ("DAX P"); checked before any lookup so they always win. FX pairs are handled by rule below.
_CURATED_NAMES = {
    "^DJI":      "Dow Jones Industrial Average (US 30 blue-chips)",
    "^RUT":      "Russell 2000 (US small-cap index)",
    "^GDAXI":    "DAX (Deutscher Aktienindex, Germany 40)",
    "^FCHI":     "CAC 40 (France blue-chip index)",
    "^STOXX50E": "Euro Stoxx 50 (Eurozone blue-chips)",
    "^FTMC":     "FTSE 250 (UK mid-cap index)",
    "^AEX":      "AEX (Amsterdam Exchange Index, Netherlands)",
    "^IBEX":     "IBEX 35 (Spain blue-chip index)",
    "^SSMI":     "SMI (Swiss Market Index)",
    "^AXJO":     "S&P/ASX 200 (Australia)",
    "^GSPTSE":   "S&P/TSX Composite (Canada)",
    "^BSESN":    "S&P BSE Sensex (Bombay Stock Exchange, India)",
    "^NSEI":     "Nifty 50 (National Stock Exchange, India)",
    "^KS11":     "KOSPI (Korea Composite Index)",
    "^TWII":     "TAIEX (Taiwan Weighted Index)",
    "^STI":      "Straits Times Index (Singapore)",
    "^BVSP":     "Bovespa (Brazil benchmark index)",
    "^MXX":      "IPC (Mexico benchmark index)",
    "000001.SS": "Shanghai Composite (China)",
    "SPX500":    "S&P 500 (US large-cap index)",
    "NASDAQ":    "Nasdaq Composite (US tech-heavy index)",
    "UK100":     "FTSE 100 (UK blue-chip index)",
    "JPN225":    "Nikkei 225 (Japan)",
    "HK50":      "Hang Seng (Hong Kong)",
}
_FX_RE = re.compile(r"^([A-Z]{3})([A-Z]{3})(=X)?$")
# Both legs must be REAL currency codes so XAUUSD (gold), BTCUSD (crypto) etc. fall through to the
# normal resolvers instead of reading "XAU/USD".
_FX_CCY = {"USD", "GBP", "EUR", "AUD", "NZD", "CAD", "CHF", "JPY", "CNY", "INR",
           "HKD", "NOK", "SEK", "MXN", "ZAR", "TRY", "PLN", "SGD"}


def company_name(ticker: str) -> str:
    """The full company/instrument name for a ticker — THE single source of truth. Curated names
    (indices) and the FX-pair rule first, then yfinance (authoritative), then the IG epic_lookup
    description (commodities), else ''. Cached."""
    if not ticker:
        return ""
    if ticker in _CURATED_NAMES:
        return _CURATED_NAMES[ticker]
    _fx = _FX_RE.match(ticker)
    if _fx and _fx.group(1) in _FX_CCY and _fx.group(2) in _FX_CCY:
        return f"{_fx.group(1)}/{_fx.group(2)}"   # ALL-CAPS pairs: GBPCAD=X -> "GBP/CAD" (user 2026-06-30)
    if ticker in _NAME_CACHE:
        return _NAME_CACHE[ticker]
    name = ""
    try:
        info = _yf_info(ticker)
        name = _clean_yf(info.get("longName") or info.get("shortName") or "")
    except Exception:
        name = ""
    if not name:
        name = _from_epic_lookup(ticker)
    _NAME_CACHE[ticker] = name
    return name
