# ======================================================================================================================
# File:         technical_summary.py
# Author:       Alex Hind
# Created:      2026-06-15
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# A TradingView-style technical read for the VERBOSE outputs (the instrument dossier and the daily HVF report) — user
# 2026-06-15: "for extra useful information share MA10/MA30/MA50 buy/sell/hold plus same for RSI14, Stoch(9,6), ATR, ADX,
# CCI plus dividend growth". This is supplementary context, NOT a trade signal — it never gates a trade or changes HVF
# detection. Everything is computed from ONE sanitised yfinance daily history (price_action._sanitise_ohlc clips the
# phantom LSE wicks first, consistent with the rest of the system).
#
# Buy/Sell/Hold thresholds follow the common TradingView conventions:
#   MA(n)        price above MA → Buy, below → Sell, within ±0.1% → Hold
#   RSI14        <30 Buy (oversold), >70 Sell (overbought), else Hold
#   Stoch %K(9,6) <20 Buy, >80 Sell, else Hold
#   ADX14        <20 = no trend → Hold; else +DI≥-DI → Buy, +DI<-DI → Sell  (ADX = strength, DI = direction)
#   CCI20        <-100 Buy, >100 Sell, else Hold
#   ATR14        volatility, not directional → shown as a value (rating "—")
# Dividend growth = trailing-12-month dividends vs the prior 12 months (YoY %).
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-15  Alex Hind   Initial build (user 2026-06-15): MA/RSI/Stoch/ATR/ADX/CCI Buy/Sell/Hold + dividend growth.
# ======================================================================================================================

import logging

import numpy as np
import pandas as pd

log = logging.getLogger("technical_summary")


# ----------------------------------------------------------------------------------------------------------------------
# Buy / Sell / Hold ratings
# ----------------------------------------------------------------------------------------------------------------------

def _rate_ma(close: float, ma: float) -> str:
    if ma is None or pd.isna(ma):
        return "—"
    if close > ma * 1.001:
        return "Buy"
    if close < ma * 0.999:
        return "Sell"
    return "Hold"


def _rate_band(value, low, high) -> str:
    """Oscillator rating: below `low` = Buy (oversold), above `high` = Sell (overbought)."""
    if value is None or pd.isna(value):
        return "—"
    if value < low:
        return "Buy"
    if value > high:
        return "Sell"
    return "Hold"


def _rate_adx(adx, plus_di, minus_di) -> str:
    if adx is None or pd.isna(adx):
        return "—"
    if adx < 20:
        return "Hold"                       # ADX < 20 → no clear trend
    return "Buy" if plus_di >= minus_di else "Sell"


# ----------------------------------------------------------------------------------------------------------------------
# Indicator maths (all from one OHLC frame)
# ----------------------------------------------------------------------------------------------------------------------

def _rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def _stoch_k(high, low, close, k_period=9, d_smooth=6) -> float:
    ll = low.rolling(k_period).min()
    hh = high.rolling(k_period).max()
    k = 100 * (close - ll) / (hh - ll).replace(0, np.nan)
    return float(k.rolling(d_smooth).mean().iloc[-1])   # %D = %K smoothed (the 9,6 read)


def _atr(high, low, close, period=14) -> float:
    prev = close.shift(1)
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def _cci(high, low, close, period=20) -> float:
    tp = (high + low + close) / 3.0
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    cci = (tp - sma) / (0.015 * mad.replace(0, np.nan))
    return float(cci.iloc[-1])


def _adx(high, low, close, period=14):
    """Wilder ADX with +DI / -DI. Returns (adx, plus_di, minus_di)."""
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    prev = close.shift(1)
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=high.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    return float(adx.iloc[-1]), float(plus_di.iloc[-1]), float(minus_di.iloc[-1])


def _dividend_growth(t) -> float:
    """
    Per-share dividend growth, latest payment vs the payment ~1 year earlier (a
    like-for-like YoY %). This avoids the boundary/payment-count distortion of a
    trailing-12mo-sum comparison (which can read +75% when a window catches an extra
    payment). None if not a regular payer.
    """
    try:
        divs = t.dividends
        if divs is None or len(divs) < 2:
            return None
        last_date = divs.index.max()
        last_amt  = float(divs.iloc[-1])
        target    = last_date - pd.Timedelta(days=365)
        # Candidate prior payments: at least ~4 months before the latest, so we never
        # compare a payment against itself / its own cycle.
        prior = divs[divs.index <= last_date - pd.Timedelta(days=120)]
        if len(prior) == 0:
            return None
        nearest   = int(np.argmin(np.abs((prior.index - target).days)))
        prior_amt = float(prior.iloc[nearest])
        if prior_amt <= 0:
            return None
        return round((last_amt - prior_amt) / prior_amt * 100, 1)
    except Exception:
        return None


# ----------------------------------------------------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------------------------------------------------

def get_technical_summary(ticker: str) -> dict:
    """
    Supplementary technical read for the dossier / HVF report. Returns:
        {ticker, indicators:[(name, value, rating), ...], dividend_growth_pct,
         buy, sell, hold, error}
    Never raises — on any failure returns error set and empty indicators.
    """
    out = {"ticker": ticker, "indicators": [], "dividend_growth_pct": None,
           "buy": 0, "sell": 0, "hold": 0, "error": None}
    try:
        import yfinance as yf
        from config import YAHOO_MAP
        from price_action import _sanitise_ohlc

        yt = YAHOO_MAP.get(ticker, ticker)
        t = yf.Ticker(yt)
        hist = _sanitise_ohlc(t.history(period="1y").dropna(), ticker)
        if len(hist) < 55:
            out["error"] = "insufficient history (<55 bars)"
            return out

        close, high, low = hist["Close"], hist["High"], hist["Low"]
        c = float(close.iloc[-1])

        ind = out["indicators"]
        for n in (10, 30, 50):
            ma = float(close.rolling(n).mean().iloc[-1])
            ind.append((f"MA{n}", round(ma, 2), _rate_ma(c, ma)))

        rsi = _rsi(close, 14)
        ind.append(("RSI14", round(rsi, 1), _rate_band(rsi, 30, 70)))

        k = _stoch_k(high, low, close, 9, 6)
        ind.append(("Stoch %K(9,6)", round(k, 1), _rate_band(k, 20, 80)))

        atr = _atr(high, low, close, 14)
        ind.append(("ATR14", f"{atr:.2f} ({atr / c * 100:.1f}%)", "—"))   # volatility, not directional

        adx, pdi, mdi = _adx(high, low, close, 14)
        ind.append(("ADX14", round(adx, 1), _rate_adx(adx, pdi, mdi)))

        cci = _cci(high, low, close, 20)
        ind.append(("CCI20", round(cci, 1), _rate_band(cci, -100, 100)))

        out["dividend_growth_pct"] = _dividend_growth(t)

        ratings = [r for _, _, r in ind if r in ("Buy", "Sell", "Hold")]
        out["buy"], out["sell"], out["hold"] = ratings.count("Buy"), ratings.count("Sell"), ratings.count("Hold")
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        log.warning(f"technical_summary failed for {ticker}: {e}")
    return out


def summary_line(ts: dict) -> str:
    """One-line compact summary for the HVF report: 'TA: 5 Buy / 2 Sell / 2 Hold · Div growth +8.0%'."""
    if ts.get("error") or not ts.get("indicators"):
        return ""
    dg = ts.get("dividend_growth_pct")
    dg_str = f" · Div growth {dg:+.1f}%" if dg is not None else ""
    return f"TA: {ts['buy']} Buy / {ts['sell']} Sell / {ts['hold']} Hold{dg_str}"


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    for tk in sys.argv[1:] or ["AAPL"]:
        s = get_technical_summary(tk)
        print(f"\n{tk}: {summary_line(s) or s.get('error')}")
        for name, val, rating in s["indicators"]:
            print(f"  {name:14} {str(val):20} {rating}")
