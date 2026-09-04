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
        # Currency comes back with the value and the result is converted to GBP (user 2026-09-04: "MCAP is
        # expected to be in GBP in our system"). This one matters more than the reporting paths: the mcap
        # returned here goes straight into trading_limits.check_limits against min/max_instrument_value in
        # ig_shim, the last gate before an order is sent to IG. Reading it raw meant a JPY or INR
        # instrument was compared against a GBP floor on a number roughly 150x and 128x too large, so it
        # cleared a mega-cap floor on denomination alone -- and the IG Account audit, which now converts,
        # would then disagree with the gate that placed the order.
        mcap_rows = db.run("select mcap, currency from instrument_mcap where ticker=:ticker",
                           ticker=ticker) or []
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
    # An unconvertible currency yields None, exactly as an absent row does. trading_limits is called with
    # require_data=True, so None BLOCKS the order rather than letting it through unchecked -- which is the
    # right way round: we would rather not place an order than place one on a number we cannot read.
    mcap = None
    if mcap_rows and mcap_rows[0][0] is not None:
        import fx_rates
        mcap = fx_rates.to_gbp(mcap_rows[0][0], mcap_rows[0][1] if len(mcap_rows[0]) > 1 else None)
    result = {"rvol": rvol, "volume_score": score, "above_vwap": above_vwap,
              "atr_expanding": atr_expanding, "mcap": mcap,
              "sector": sector, "metric_date": (bars[-1][0] if bars else None)}
    _CACHE[key] = (now, dict(result))
    return result
