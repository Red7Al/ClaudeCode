"""Current, Supabase-backed metrics used at the automated order-placement gate.

The Scanner computes these fields for display, but session signals and the two-hour bridge historically
reached ``ig_shim.place_hvf_order_from_sig`` without RVOL, VolumeScore or market cap.  This small shared
reader makes the final order chokepoint self-sufficient and caches each ticker briefly so repeated session
checks do not create avoidable database load.
"""

import logging
import time

log = logging.getLogger("order_metrics")

_TTL_SECONDS = 10 * 60
_CACHE = {}


def _components(result):
    return {item.get("key"): item.get("got") for item in (result or {}).get("components", [])}


def live_order_metrics(ticker: str, *, bull: bool, quality=None) -> dict:
    """Return current RVOL/VolumeScore/VWAP/ATR, mcap and cached sector for one candidate."""
    key = (ticker, bool(bull), bool(quality is not None and quality >= 60))
    cached = _CACHE.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _TTL_SECONDS:
        return dict(cached[1])

    from db_pool import get_db
    db = get_db()
    try:
        raw = db.run(
            "select bar_date,high,low,close,volume from price_history "
            "where ticker=:ticker order by bar_date desc limit 100", ticker=ticker) or []
        mcap_rows = db.run("select mcap from instrument_mcap where ticker=:ticker", ticker=ticker) or []
    finally:
        db.close()
    bars = [(str(day)[:10], float(high), float(low), float(close), float(volume) if volume else None)
            for day, high, low, close, volume in reversed(raw)
            if None not in (day, high, low, close)]
    score = None
    rvol = above_vwap = atr_expanding = None
    if bars:
        import volume_score
        result = volume_score.volume_score(
            bars, bars[-1][0], bool(bull), squeeze_strong=(quality is not None and quality >= 60))
        score = result.get("score")
        rvol = volume_score._rvol_at(bars, len(bars) - 1)
        parts = _components(result)
        above_vwap = parts.get("above_vwap")
        atr_expanding = parts.get("atr_expanding")
    try:
        import sector_cache
        sector = sector_cache.get_sector(ticker)
    except Exception as exc:
        log.warning("%s: sector cache unavailable: %s", ticker, exc)
        sector = None
    result = {"rvol": rvol, "volume_score": score, "above_vwap": above_vwap,
              "atr_expanding": atr_expanding,
              "mcap": (float(mcap_rows[0][0]) if mcap_rows and mcap_rows[0][0] is not None else None),
              "sector": sector, "metric_date": (bars[-1][0] if bars else None)}
    _CACHE[key] = (now, dict(result))
    return result
