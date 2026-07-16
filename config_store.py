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
# 1.1.0   2026-07-06  Alex Hind   (user 2026-07-06) get_x_hvf_markets (morning HVF tweet markets); .DE + ^GDAXI
#                                 Location -> Germany. (Scanner-hide filter reverted same day: filters gate trading only.)
# 1.0.0   2026-07-03  Alex Hind   Initial build — app_config table, get/set, monitor_enabled gate.
# ======================================================================================================================

import logging

log = logging.getLogger("config_store")

_DDL = ("create table if not exists app_config ("
        "key text primary key, value text not null, "
        "updated_by text, updated_at timestamptz not null default now())")
_schema_ready = False

# The momentum trade-execution sources the Config tab offers (the *_MONITOR sessions). The squeeze
# bridge (WEB_BRIDGE) is gated separately in Trading (Squeeze) — user 2026-07-03: not in Momentum.
EXEC_SOURCES = ["AUS_MONITOR", "UK_MONITOR", "US_MONITOR"]
EXEC_DESCRIPTIONS = {
    "AUS_MONITOR": "Automatic trading in the Australian session (AUS equity market + overnight FX/commodities)",
    "UK_MONITOR":  "Automatic trading in the UK session (FTSE 100/250 equities)",
    "US_MONITOR":  "Automatic trading in the US session (NASDAQ / S&P 500 equities, every 5 minutes)",
}


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


def get_num(key: str, fallback):
    """Read a numeric app-config value (Supabase), falling back to the given default. So the engine —
    local AND GitHub Actions — reads the same value (user 2026-07-03 config-to-DB migration)."""
    v = get_value(key, "")
    if v == "":
        return fallback
    try:
        f = float(v)
        return int(f) if float(f).is_integer() else f
    except Exception:
        return fallback


# App-wide engine settings migrated from config.py (user 2026-07-03). key -> config.py fallback name.
# Read at the use sites via cfg_num(); editable in Configuration → Engine (admin).
APP_ENGINE_KEYS = {
    "wo_lifespan_days": 28,          # IG working-order lifespan (was good_till_days=4)
    "x_max_per_day": 2,              # live X publications per UTC day (user 2026-07-10: 5 -> 2)
    "superinvestor_lookback_days": 90,
    "min_senator_trades": 1,
    "spread_retry_attempts": 3,
    "spread_retry_wait_secs": 2,
    "bridge_min_quality": 50,        # min pattern Quality for the bridge to auto-load a setup
}


def cfg_num(key: str, fallback=None):
    """App-wide engine setting with a code fallback (default from APP_ENGINE_KEYS if not given)."""
    return get_num(key, fallback if fallback is not None else APP_ENGINE_KEYS.get(key))


def get_engine_settings() -> dict:
    return {k: get_num(k, d) for k, d in APP_ENGINE_KEYS.items()}


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


def get_disabled_markets() -> list:
    """Admin deny-list from the Markets (Admin) switch (user 2026-07-11): markets switched OFF for
    everyone. Unlike trade_markets (an owner allow-list), this is enforced in the order path so a
    disabled market is kept out of PROCESSING — not just hidden in the UI (user 2026-07-13)."""
    v = get_value("markets_disabled", "")
    return [x.strip() for x in v.split(",") if x.strip()]


def trade_allowed(direction: str = None, market: str = None, location: str = None) -> tuple:
    """Trade-filter gate (user 2026-07-03, Config tab): direction is BULL/BEAR; market/location as in
    the scanner. A missing/empty stored filter = allow all; unknown attribute values pass (fail OPEN).
    A market in the admin deny-list (markets_disabled) is always blocked (user 2026-07-13).
    Returns (allowed, reason)."""
    try:
        # Admin market kill-switch first: a disabled market never reaches IG, regardless of the
        # owner allow-list below (user 2026-07-13 — keep filtered-out markets out of the order path).
        if market and market in get_disabled_markets():
            return False, f"market {market} is disabled (Markets Admin)"
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
    "UK100": "Europe (West)", "^FTMC": "Europe (West)", "^GDAXI": "Germany", "^FCHI": "Europe (West)",
    "^STOXX50E": "Europe (West)", "^AEX": "Europe (West)", "^IBEX": "Europe (West)", "^SSMI": "Europe (West)",
    "JPN225": "Asia", "HK50": "Asia", "^AXJO": "Oceania", "^BSESN": "Asia", "^NSEI": "Asia", "^KS11": "Asia",
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
    if t.endswith(".DE"):
        return "Germany"          # German equities (user 2026-07-06: Location = Germany, Market = DAX)
    if t.endswith(".SS") or t.endswith(".HK") or t.endswith(".T") or t.endswith(".NS"):
        return "Asia"
    if t.endswith(".AX"):
        return "Oceania"
    # Euronext venues — Paris/Amsterdam/Milan/Brussels/Oslo/Lisbon/Dublin. MUST mirror
    # hvf_web/build_snapshot._location_of, which gained this branch with the Euronext universe
    # (user 2026-07-14). Without it every Euronext ticker fell through to "US" here while the web app
    # called it "Europe" — the order layer and the scanner disagreeing about the same instrument
    # (user 2026-07-17, P-27: UMG.AS).
    if t.endswith((".PA", ".AS", ".MI", ".BR", ".OL", ".LS", ".IR")):
        return "Europe"
    return "US"


def monitor_enabled(session_name: str) -> bool:
    """The trade-execution gate for a source (session). Unknown/unset sources are ENABLED, and any
    DB failure fails OPEN — a config read error must never silently stop trading."""
    if not session_name:
        return True
    return get_value(f"exec_{session_name}", "true").lower() != "false"


# Markets whose setups may be tweeted in the MORNING HVF batch (user 2026-07-06). Config -> X Posts.
X_HVF_MARKETS_DEFAULT = ["FTSE 100", "FTSE 250", "NASDAQ 100", "S&P 500"]


def get_x_hvf_markets() -> list:
    """Allowed markets for the morning HVF tweets. Unset (or a save that clears every box) falls back
    to the four defaults so the batch can never accidentally go silent (user 2026-07-06)."""
    v = get_value("x_hvf_markets", "")
    if v == "":
        return list(X_HVF_MARKETS_DEFAULT)
    picked = [x.strip() for x in v.split(",") if x.strip()]
    return picked or list(X_HVF_MARKETS_DEFAULT)
