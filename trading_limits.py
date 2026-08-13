# ======================================================================================================================
# File:         trading_limits.py
# Author:       Claude (for Alex Hind / Eddie)
# Created:      2026-08-11
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Single source of truth for a login's PERSONAL trading-limit floors (Configuration -> My trading limits):
# min R:R, min Quality, min VolumeScore, min RVOL, require-above-VWAP, require-ATR-expanding, and an
# instrument-value (mcap) band.
#
# WHY this file exists (user 2026-08-11): these floors previously lived only inside hvf_web/server.py as
# _limit_defaults()/_user_limits()/_limit_block(), which gated exactly two web routes — /api/preorder-pin
# and /api/place-order — i.e. a user's OWN manual actions. The automated engine (hvf_web/order_bridge.py's
# 2h sweep, and the session-based cron jobs in run_session.py/intraday_signals.py, all of which call
# ig_shim.place_hvf_order_from_sig) never imported hvf_web/server.py and so never saw these floors at all —
# confirmed by reading every caller, not assumed. The user asked "make sure my trading filter settings are
# being used to choose instruments to order" and, on finding they weren't, asked for the automated engine to
# honour the account owner's own floors too. Extracting the floor logic here lets BOTH hvf_web/server.py
# (manual actions) and ig_shim.py (the automated engine, which must NOT import the Flask app) call the exact
# same check — one rulebook, no drift between "what you can do yourself" and "what the robot does for you".
#
# UPDATE (2026-08-11, same-day data-completeness audit — "check all instruments have current rvol,
# volumescore, above VWAP and above ATR metrics"): above_vwap/atr_expanding were wired into both paths the
# same day this file was created, but order_bridge.py's _candidates() and hvf_web/server.py's
# _sig_from_snapshot() were both reading the snapshot record's OWN above_vwap/atr_expanding fields — which
# are ALWAYS None (price_action.get_hvf_signal_mtf(), the function that actually produces snapshot signal
# rows, never computes VWAP position and never merges in atr_expanding). So "Require above VWAP"/"Require
# ATR expanding" silently never blocked anything on the automated 2h-bridge or manual place-order paths,
# even though this file's check_limits() itself was correct. Fixed by adding hvf_web/server.py's
# _live_vwap_atr() — the same volume_score.py-derived source api_records() already uses for the Scanner's
# VWAP/ATR ticks — and pointing both callers at it instead of the dead static field. R:R, Quality,
# above-VWAP and ATR-expanding are now genuinely enforced on both paths (not just nominally wired).
#
# KNOWN GAP (documented, not silently glossed over): RVOL and VolumeScore are computed at request time in
# hvf_web/server.py (_snapshot_rvol/_snapshot_volscore — a bulk price-history fetch across the whole
# universe, cached per snapshot generation) and instrument value (mcap) is joined from the separate
# instrument_mcap Supabase table at the API layer. Neither is available to the automated engine's sig dicts
# today (order_bridge.py reads snapshot.json directly; the session scanners build sig dicts with no
# rvol/volume_score/mcap fields at all). check_limits() below happily accepts those as optional and simply
# skips the check when the value is None — so RVOL/VolumeScore/instrument-value floors are enforced for
# manual actions (which DO have this data, via hvf_web/server.py's _record()) but NOT yet for the automated
# engine.
# ======================================================================================================================

import logging

log = logging.getLogger("trading_limits")


def limit_defaults() -> dict:
    """Code defaults for a user's personal trading limits, sourced from config.py so they track the shared
    engine's baseline. Mirrors hvf_web/server.py's _limit_defaults() — kept minimal here (just the fields
    check_limits() below actually gates) rather than the full settings-page default set."""
    import config as _cfg
    return {
        "min_risk_reward":       float(getattr(_cfg, "MIN_RISK_REWARD", 3.0)),
        "min_quality":           int(getattr(_cfg, "MIN_PUBLISH_QUALITY", 25)),
        "min_volume_score":      int(getattr(_cfg, "MIN_VOLUME_SCORE", 1)),
        "min_rvol":              float(getattr(_cfg, "MIN_RVOL", 0)),
        "require_above_vwap":    int(getattr(_cfg, "REQUIRE_ABOVE_VWAP", 0)),
        "require_atr_expanding": int(getattr(_cfg, "REQUIRE_ATR_EXPANDING", 0)),
        "min_instrument_value":  float(getattr(_cfg, "MIN_INSTRUMENT_VALUE", 0)),
        "max_instrument_value":  float(getattr(_cfg, "MAX_INSTRUMENT_VALUE", 0)),
    }


def user_limits(name: str) -> dict:
    """This login's personal limits: code defaults, overridden by whatever they've saved in
    Configuration -> My trading limits. No setting is inherited between users (user 2026-08-01) — a login
    with nothing saved gets the code baseline, never another user's values."""
    d = limit_defaults()
    if not name:
        return d
    try:
        from hvf_web import web_users as _wu
        saved = (_wu.get_settings(name) or {}).get("limits") or {}
        d.update({k: v for k, v in saved.items() if k in d})
    except Exception as e:
        log.warning(f"user_limits({name}): could not read saved settings, using code defaults: {e}")
    return d


def check_limits(name: str, ticker: str, *, quality=None, rr=None, volume_score=None,
                  rvol=None, above_vwap=None, atr_expanding=None, mcap=None,
                  require_data=False) -> str:
    """Return a reason string if this login's personal floors exclude the setup; '' if it passes (or a
    given criterion has no data to check — fail-open per field, matching hvf_web/server.py's _limit_block).
    `name` may be None/empty (e.g. no owner resolved) -> code defaults apply, same as an unconfigured user."""
    lim = user_limits(name)
    if isinstance(rr, (int, float)) and rr < lim["min_risk_reward"]:
        return f"{ticker}: R:R {rr} is below the personal floor of {lim['min_risk_reward']:g}"
    if isinstance(quality, (int, float)) and quality < lim["min_quality"]:
        return f"{ticker}: Quality {quality} is below the personal floor of {lim['min_quality']}"
    if require_data and lim.get("min_volume_score", 0) > 0 and not isinstance(volume_score, (int, float)):
        return f"{ticker}: VolumeScore is unavailable, so the personal floor cannot be verified"
    if isinstance(volume_score, (int, float)) and volume_score < lim.get("min_volume_score", 1):
        return f"{ticker}: VolumeScore {volume_score} is below the personal floor of {lim['min_volume_score']}"
    if require_data and lim.get("min_rvol", 0) > 0 and not isinstance(rvol, (int, float)):
        return f"{ticker}: RVOL is unavailable, so the personal floor cannot be verified"
    if isinstance(rvol, (int, float)) and lim.get("min_rvol", 0) > 0 and rvol < lim["min_rvol"]:
        return f"{ticker}: RVOL {rvol:g} is below the personal floor of {lim['min_rvol']:g}"
    if lim.get("require_above_vwap") and (above_vwap is False or (require_data and above_vwap is None)):
        return (f"{ticker}: VWAP position is unavailable, so the personal filter cannot be verified"
                if above_vwap is None else f"{ticker}: price is below VWAP (personal filter requires above)")
    if lim.get("require_atr_expanding") and (atr_expanding is False or (require_data and atr_expanding is None)):
        return (f"{ticker}: ATR state is unavailable, so the personal filter cannot be verified"
                if atr_expanding is None else f"{ticker}: ATR is not expanding (personal filter requires it)")
    if isinstance(mcap, (int, float)):
        vmin, vmax = lim.get("min_instrument_value", 0), lim.get("max_instrument_value", 0)
        if vmin and mcap < vmin:
            return f"{ticker}: instrument value {mcap:,.0f} is below the personal minimum of {vmin:,.0f}"
        if vmax and mcap > vmax:
            return f"{ticker}: instrument value {mcap:,.0f} is above the personal maximum of {vmax:,.0f}"
    elif require_data and (lim.get("min_instrument_value", 0) or lim.get("max_instrument_value", 0)):
        return f"{ticker}: instrument value is unavailable, so the personal range cannot be verified"
    return ""
