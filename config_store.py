# ======================================================================================================================
# File:         config_store.py
# Author:       Alex Hind
# Created:      2026-07-03
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Runtime application configuration in Supabase (user 2026-07-03, Application Focus - Configuration). Lives in the DB
# (not a file) so BOTH the local web server and the GitHub-Actions sessions read the same switches.
#
# Primary use: per-source trade-execution toggles ("exec_AUS_MONITOR" = 'false' stops that monitor placing trades —
# scanning/reporting continue). Enforced at the two order entry points in ig_shim (open_trade and
# place_hvf_order_from_sig), so every source — session monitors, the web bridge — passes through the same gate.
# Missing key = enabled (default true); the gate FAILS OPEN on a DB error so a Supabase blip cannot silently stop
# trading, and the web Config tab records who flipped what.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-07-03  Alex Hind   Initial build — app_config table, get/set, monitor_enabled gate.
# ======================================================================================================================

import logging

log = logging.getLogger("config_store")

_DDL = ("create table if not exists app_config ("
        "key text primary key, value text not null, "
        "updated_by text, updated_at timestamptz not null default now())")
_schema_ready = False

# The trade-execution sources the Config tab offers (any *_MONITOR + the web bridge).
EXEC_SOURCES = ["AUS_MONITOR", "UK_MONITOR", "US_MONITOR", "WEB_BRIDGE"]


def _ensure(db):
    global _schema_ready
    if not _schema_ready:
        db.run(_DDL)
        _schema_ready = True


def get_value(key: str, default: str = "") -> str:
    try:
        from db_pool import get_db
        db = get_db()
        try:
            _ensure(db)
            rows = db.run("select value from app_config where key = :k", k=key)
            return rows[0][0] if rows else default
        finally:
            db.close()
    except Exception as e:
        log.warning(f"config read failed for {key} (default '{default}'): {e}")
        return default


def set_value(key: str, value: str, updated_by: str = "") -> bool:
    try:
        from db_pool import get_db
        db = get_db()
        try:
            _ensure(db)
            db.run("insert into app_config (key, value, updated_by) values (:k,:v,:u) "
                   "on conflict (key) do update set value = excluded.value, "
                   "updated_by = excluded.updated_by, updated_at = now()",
                   k=key, v=value, u=updated_by)
            return True
        finally:
            db.close()
    except Exception as e:
        log.warning(f"config write failed for {key}: {e}")
        return False


def get_exec_flags() -> dict:
    """{source: bool} for every EXEC_SOURCES entry (missing key = enabled)."""
    flags = {s: True for s in EXEC_SOURCES}
    try:
        from db_pool import get_db
        db = get_db()
        try:
            _ensure(db)
            for k, v in (db.run("select key, value from app_config where key like 'exec_%'") or []):
                flags[k[5:]] = (str(v).lower() != "false")
        finally:
            db.close()
    except Exception as e:
        log.warning(f"exec flags read failed (all enabled): {e}")
    return flags


TRADE_FILTER_KEYS = {"directions": "trade_directions", "markets": "trade_markets", "locations": "trade_locations"}


def get_trade_filters() -> dict:
    """{directions:[...], markets:[...], locations:[...]} — empty list = no restriction (all allowed)."""
    out = {}
    for name, key in TRADE_FILTER_KEYS.items():
        v = get_value(key, "")
        out[name] = [x.strip() for x in v.split(",") if x.strip()] if (v and v != "ALL") else []
    return out


def trade_allowed(direction: str = None, market: str = None, location: str = None) -> tuple:
    """Trade-filter gate (user 2026-07-03, Config tab): direction is BULL/BEAR; market/location as in
    the scanner. A missing/empty stored filter = allow all; unknown attribute values pass (fail OPEN).
    Returns (allowed, reason)."""
    try:
        f = get_trade_filters()
        if direction and f["directions"] and direction not in f["directions"]:
            return False, f"direction {direction} not in allowed {f['directions']}"
        if market and f["markets"] and market not in f["markets"]:
            return False, f"market {market} not in allowed {f['markets']}"
        if location and f["locations"] and location not in f["locations"]:
            return False, f"location {location} not in allowed {f['locations']}"
    except Exception as e:
        log.warning(f"trade filters read failed (allowing): {e}")
    return True, ""


# Regional locations for index tickers (mirror hvf_web/build_snapshot._INDEX_REGION), user 2026-07-03.
_INDEX_REGION = {
    "SPX500": "US", "NASDAQ": "US", "^DJI": "US", "^RUT": "US", "^GSPTSE": "US", "^BVSP": "US", "^MXX": "US",
    "UK100": "Western Europe", "^FTMC": "Western Europe", "^GDAXI": "Western Europe", "^FCHI": "Western Europe",
    "^STOXX50E": "Western Europe", "^AEX": "Western Europe", "^IBEX": "Western Europe", "^SSMI": "Western Europe",
    "JPN225": "Asia", "HK50": "Asia", "^AXJO": "Asia", "^BSESN": "Asia", "^NSEI": "Asia", "^KS11": "Asia",
    "^TWII": "Asia", "^STI": "Asia", "000001.SS": "Asia",
}


def location_of_ticker(ticker: str) -> str:
    """Location bucket matching the web app (UK / US / FX / regional-index) for tickers at the order layer."""
    t = ticker or ""
    if ticker in _INDEX_REGION:
        return _INDEX_REGION[ticker]
    if "=X" in t or t in ("USDJPY", "GBPUSD", "EURUSD", "AUDUSD"):
        return "FX"
    if t.endswith(".L"):
        return "UK"
    return "US"


def monitor_enabled(session_name: str) -> bool:
    """The trade-execution gate for a source (session). Unknown/unset sources are ENABLED, and any
    DB failure fails OPEN — a config read error must never silently stop trading."""
    if not session_name:
        return True
    return get_value(f"exec_{session_name}", "true").lower() != "false"
