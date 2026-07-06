# ======================================================================================================================
# File:         hvf_web/server.py
# Author:       Alex Hind
# Created:      2026-06-27
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Flask server for the HVF website (user 2026-06-27). Serves the single-page UI (index.html), the data snapshot
# (build_snapshot.py output) as JSON, and three PNG visuals per instrument:
#   /api/card/<ticker>            the production X post-card (native funnel window)         -> render_x_post_card
#   /api/pricewin/<ticker>?days=N a DATE-WINDOW-REACTIVE price+funnel chart (filters live)  -> _render_price_window
#   /api/hist3yr/<ticker>         the fixed 3-YEAR price history (always 3y, never filtered) -> render_3yr_history_card
# The pricewin chart is a fresh, website-only renderer so the protected production card is never modified.
# Expose to a colleague with ngrok:  ngrok http 5057
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.7.0   2026-07-06  Alex Hind   (user 2026-07-06) Trade filters are now PER-USER: stored in the user's settings,
#                                 hide excluded markets/dirs/locations from THAT user's Scanner + Pre-orders (client)
#                                 and block their pin/place (_user_trade_allows). Owner mirrors to global app_config so
#                                 the shared engine gate (ig_shim / bridge) stays in sync. Others' views unaffected.
# 1.6.0   2026-07-06  Alex Hind   (user 2026-07-06) /api/config GET/POST for x_hvf_markets (morning HVF tweet markets,
#                                 admin); NEW /api/scheduled-jobs (admin Scheduled Jobs tab — cron defs + Actions run
#                                 stats). The Scanner shows the FULL universe: trade filters gate trading only, never
#                                 what is shown (an earlier same-day /api/records hide was reverted per user).
# 1.5.0   2026-07-03  Alex Hind   (user 2026-07-03) Configuration tab APIs: /api/config GET/POST (per-user filter
#                                 defaults + shared per-source execution switches via config_store, changes logged to
#                                 the user's activity); /api/preorder-delete; /api/refresh gated + logged.
# 1.4.0   2026-06-30  Alex Hind   (user 2026-06-30) Login: /api/login (Alex/Rich shared-secret, sha256 token) and
#                                 /api/records now requires X-Auth — gates the Scanner + Pre-orders tabs.
# 1.3.6   2026-06-29  Alex Hind   (user 2026-06-29 "X post card is empty for ABF.L") /api/thread ALSO renders a card
#                                 PNG via matplotlib (collect=True) and was OUTSIDE _RENDER_LOCK, so it raced /api/card
#                                 and both blanked. Wrapped the _generate_x_drafts render in the lock. Card now stays
#                                 full (168KB) when /api/card + /api/thread + /api/pricewin fire together.
# 1.3.5   2026-06-28  Alex Hind   (user 2026-06-28) /api/fundamentals/<ticker> — company KPIs from yfinance .info
#                                 (P/E, FCF, dividends, margins, growth, leverage, 52w) for the new detail KPI card.
#                                 Dividend yield uses yfinance's own field to dodge the .L pence/pounds unit mismatch.
# 1.3.4   2026-06-28  Alex Hind   (user 2026-06-28 "ABF.L still empty") Root cause: matplotlib pyplot is not
#                                 thread-safe + threaded=True, so the card and price-window renders fired concurrently
#                                 and produced a blank card. All chart renders now serialised through _RENDER_LOCK.
# 1.3.3   2026-06-28  Alex Hind   (user 2026-06-28) /api/pubcounts — count of X posts published per instrument
#                                 (x_publications) for the new main-list 'X posts' column.
# 1.3.2   2026-06-28  Alex Hind   (user 2026-06-28) pricewin chart gets the same disk price cache as the X card
#                                 (data/price_cache/win_<sym>_<days>.pkl) — falls back to last-good on a Yahoo throttle.
# 1.3.1   2026-06-28  Alex Hind   (user 2026-06-28 "x post card still empty") api_card now caches ONLY successful
#                                 renders — a transient yfinance throttle no longer sticks an empty card in the cache;
#                                 failed renders 404 and retry on the next load.
# 1.3.0   2026-06-28  Alex Hind   (user 2026-06-28) /api/refresh + /api/status (manual snapshot rebuild in a guarded
#                                 background thread, shared with the 12h loop); /api/broker (6/12-mo analyst up/downgrade
#                                 change). Per-rule justification de-RW-branded ("minimum 3:1", "Rule 1", "Compresses").
# 1.2.0   2026-06-27  Alex Hind   (user 2026-06-27) /api/thread (ALL publication pages — lead + numbered long report),
#                                 /api/rules (Rolls-Royce-style per-rule justification with the numbers), /api/positions
#                                 (live open IG positions per instrument via epic_lookup). Supports the rebuilt UI.
# 1.1.0   2026-06-27  Alex Hind   (user 2026-06-27) /api/links/<ticker> — OUR latest X publication (x_publications) + every
#                                 tracked account that posted the instrument (notable_investors.post_url), fetched at
#                                 selection time. 12-hour snapshot auto-refresh thread (rebuild + cache clear); threaded serve.
# 1.0.0   2026-06-27  Alex Hind   Initial build.
# ======================================================================================================================

import os
import io
import json
import logging

from flask import Flask, jsonify, send_file, request, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("hvf_web.server")

_HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(_HERE, "snapshot.json")

app = Flask(__name__)
_PNG_CACHE: dict = {}
_X_HANDLE = "SqueezeSignals"   # our X account (config.py / publish_one_to_x X_HANDLE)

# matplotlib pyplot is NOT thread-safe and the server is threaded=True. The detail panel fires the
# card + price-window renders in the same instant, so two concurrent plt.figure/savefig calls stomped
# on pyplot's global state and produced a blank card (user 2026-06-28 "ABF.L still empty"). Serialise
# every chart render through one lock.
import threading as _threading
_RENDER_LOCK = _threading.Lock()

# ── System Logs (user 2026-07-04): keep the last 800 log records in memory for the admin tab. ─────────
import collections as _collections
import time as _time
_SERVER_STARTED = _time.time()
_LOG_RING = _collections.deque(maxlen=800)


class _RingHandler(logging.Handler):
    def emit(self, record):
        try:
            _LOG_RING.append({
                "ts": _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(record.created)),
                "level": record.levelname, "logger": record.name,
                "message": record.getMessage()[:400]})
        except Exception:
            pass


logging.getLogger().addHandler(_RingHandler())

# ── Login (user 2026-06-30): the Scanner + Pre-orders tabs need a login; Intro/Appendix stay open. ──
# Credentials live in the SECURE store (hvf_web/web_users.py): PBKDF2-hashed passwords + Fernet-encrypted
# per-user secrets in gitignored data/web_users.json — nothing in source (user 2026-06-30: settings are
# private, IG credentials coming). Tokens rotate automatically when a password changes.
from hvf_web import web_users as _wu


@app.route("/api/login", methods=["POST"])
def api_login():
    body = request.get_json(silent=True) or {}
    name, pwd = (body.get("name") or "").strip(), body.get("pwd") or ""
    if _wu.verify(name, pwd):
        _wu.log_event(name, f"Logged in (from {request.remote_addr})")
        return jsonify({"ok": True, "token": _wu.token_for(name), "name": name})
    return jsonify({"ok": False}), 401


_DATA_DIR = os.path.join(os.path.dirname(_HERE), "data")
_VERSION_FILE = os.path.join(_DATA_DIR, "version_history.json")
_BATCH_FILE = os.path.join(_DATA_DIR, "batch_activity.json")


def _read_json_entries(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("entries", [])
    except Exception:
        return []


def _append_batch(source, event, by="system"):
    """Append a batch-execution record to Supabase (user 2026-07-03: data rows -> Supabase)."""
    try:
        import web_store
        web_store.append_batch(source, event, by)
    except Exception as e:
        log.warning(f"batch log append failed: {e}")


_REPO_ROOT = os.path.dirname(_HERE)


def _version_category(summary: str) -> str:
    """Categorise a change from its summary (user 2026-07-03)."""
    s = (summary or "").lower()
    if any(w in s for w in ("secur", "password", "credential", "role", "admin", "login", "auth", "encrypt", "gitleak")):
        return "Security"
    if any(w in s for w in ("fix", "bug", "correct", "revert", "repair", "root cause", "regression")):
        return "Bug fix"
    if any(w in s for w in ("redesign", "styl", "ui", "css", "layout", "chart", "tab", "colour", "color",
                            "card", "visual", "label", "format", "prominence", "doc")):
        return "Presentation"
    if any(w in s for w in ("data", "supabase", "snapshot", "universe", "ledger", "record", "etl",
                            "price", "location", "instrument", "trigger")):
        return "Data"
    return "Feature"


_VERSION_FLOOR = "2026-06-04"   # project started 4 June 2026 — hide anything on/before 3 June (user 2026-07-04)


def _version_entries():
    """Version history built LIVE from git log so it's always current (user 2026-07-03), with a
    file fallback. Categorised. Entries before the project start are hidden."""
    entries = []
    try:
        import subprocess
        out = subprocess.check_output(
            ["git", "-C", _REPO_ROOT, "log", "--date=short", "--pretty=format:%ad|%h|%s"],
            text=True, encoding="utf-8", errors="replace", timeout=20)
        for ln in out.splitlines():
            if not ln.strip():
                continue
            date, ver, summ = ln.split("|", 2)
            if date <= _VERSION_FLOOR:
                continue                     # before project start — hidden
            entries.append({"date": date, "version": ver, "summary": summ.strip(),
                            "category": _version_category(summ)})
    except Exception as e:
        log.warning(f"version history from git failed ({e}); using file")
        entries = [e2 for e2 in _read_json_entries(_VERSION_FILE)
                   if (e2.get("date") or "") > _VERSION_FLOOR]
        for e2 in entries:
            e2.setdefault("category", _version_category(e2.get("summary", "")))
    return entries


@app.route("/api/scheduled-jobs")
def api_scheduled_jobs():
    """Scheduled-job definitions + GitHub Actions run stats (user 2026-07-06, admin Scheduled Jobs tab).
    ?refresh=1 bypasses the 30-min cache."""
    if not _wu.is_admin(_wu.name_for_token(request.headers.get("X-Auth") or "")):
        return jsonify({"error": "admin only"}), 403
    from hvf_web import scheduled_jobs as _sj
    return jsonify(_sj.get_jobs(force=(request.args.get("refresh") == "1")))


@app.route("/api/system-logs")
def api_system_logs():
    """System health + recent server log records (user 2026-07-04, admin System Logs tab)."""
    if not _wu.is_admin(_wu.name_for_token(request.headers.get("X-Auth") or "")):
        return jsonify({"error": "admin only"}), 403
    import sys as _sys
    from datetime import datetime, timezone
    snap = _load_snapshot()
    health = {
        "server_started": _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(_SERVER_STARTED)),
        "uptime_mins": round((_time.time() - _SERVER_STARTED) / 60, 1),
        "python": _sys.version.split()[0],
        "snapshot_generated": snap.get("generated_utc"), "snapshot_count": snap.get("count"),
        "refreshing": _REFRESHING.get("on", False),
    }
    try:
        from db_pool import get_db
        t0 = _time.time(); db = get_db()
        try:
            counts = {}
            for label, q in (("working_orders", "select count(*) from working_orders"),
                             ("x_publications", "select count(*) from x_publications"),
                             ("batch_activity", "select count(*) from web_batch_activity"),
                             ("activity_log", "select count(*) from web_activity_log"),
                             ("hvf_triggers", "select count(*) from hvf_triggers")):
                try:
                    counts[label] = db.run(q)[0][0]
                except Exception:
                    counts[label] = None
        finally:
            db.close()
        health["db_ping_ms"] = round((_time.time() - t0) * 1000)
        health["db_counts"] = counts
    except Exception as e:
        health["db_ping_ms"] = None
        health["db_error"] = str(e)[:120]
    return jsonify({"health": health, "logs": list(_LOG_RING)[::-1]})


@app.route("/api/version-history")
def api_version_history():
    if not _wu.is_admin(_wu.name_for_token(request.headers.get("X-Auth") or "")):
        return jsonify({"error": "admin only"}), 403
    return jsonify({"entries": _version_entries()})


@app.route("/api/batch-activity")
def api_batch_activity():
    if not _wu.is_admin(_wu.name_for_token(request.headers.get("X-Auth") or "")):
        return jsonify({"error": "admin only"}), 403
    try:
        import web_store
        entries = web_store.list_batch()
    except Exception:
        entries = _read_json_entries(_BATCH_FILE)      # fallback to legacy file
    return jsonify({"entries": entries})


@app.route("/api/me")
def api_me():
    """The logged-in user's identity + role, for client-side gating (user 2026-07-03)."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"name": None, "subscription": "guest", "is_admin": False})
    return jsonify({"name": name, "subscription": _wu.get_subscription(name), "is_admin": _wu.is_admin(name)})


@app.route("/api/request-account", methods=["POST"])
def api_request_account():
    """PUBLIC (unauthenticated) account request (user 2026-07-03). Stores a pending request for an
    admin to approve — never creates a login directly."""
    body = request.get_json(silent=True) or {}
    ok = _wu.add_request((body.get("name") or "").strip(), (body.get("email") or "").strip(),
                         (body.get("note") or "").strip())
    return (jsonify({"ok": True}) if ok
            else (jsonify({"ok": False, "error": "invalid, duplicate, or name already taken"}), 400))


@app.route("/api/users", methods=["GET", "POST"])
def api_users():
    """User maintenance (admin only, user 2026-07-03): list logins + pending requests, set role /
    enabled status, approve / reject account requests."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    if not _wu.is_admin(name):
        return jsonify({"error": "admin only"}), 403
    if request.method == "GET":
        return jsonify({"users": _wu.list_users(), "subscriptions": _wu.SUBSCRIPTIONS,
                        "requests": _wu.list_requests()})
    body = request.get_json(silent=True) or {}
    target = (body.get("name") or "").strip()
    if not target:
        return jsonify({"ok": False, "error": "no user"}), 400
    # Approve / reject a pending account request.
    if body.get("action") == "approve":
        ok = _wu.approve_request(target)
        if ok:
            _wu.log_event(name, f"Approved account request: {target}")
        return jsonify({"ok": ok, "users": _wu.list_users(), "requests": _wu.list_requests()})
    if body.get("action") == "reject":
        _wu.reject_request(target)
        _wu.log_event(name, f"Rejected account request: {target}")
        return jsonify({"ok": True, "users": _wu.list_users(), "requests": _wu.list_requests()})
    changed = []
    if "subscription" in body and _wu.set_subscription(target, body["subscription"]):
        changed.append(f"subscription={body['subscription']}")
    if "admin" in body and target != name and _wu.set_admin(target, bool(body["admin"])):
        # guard: an admin can't remove their own admin and lock themselves out of maintenance
        changed.append(f"admin={bool(body['admin'])}")
    if "enabled" in body and target != name and _wu.set_enabled(target, bool(body["enabled"])):
        changed.append(f"enabled={bool(body['enabled'])}")
    if changed:
        _wu.log_event(name, f"User maintenance: {target} → {', '.join(changed)}")
    return jsonify({"ok": True, "changed": changed, "users": _wu.list_users()})


@app.route("/api/request-reset-code", methods=["POST"])
def api_request_reset_code():
    """Step 1 of the secure reset (user 2026-07-03): email a one-time code to the REGISTERED address.
    Always returns ok:true (generic) so the response can't be used to enumerate accounts/emails."""
    body = request.get_json(silent=True) or {}
    _wu.request_reset_code((body.get("name") or "").strip(), body.get("email") or "")
    return jsonify({"ok": True})


@app.route("/api/reset-password", methods=["POST"])
def api_reset_password():
    """Step 2 of the secure reset (user 2026-07-03): verify the emailed CODE + set the new password.
    Checks the code hash, 10-minute expiry and a 5-attempt limit; single-use. On success the user is
    emailed a change notification and the event is logged."""
    body = request.get_json(silent=True) or {}
    ok, err = _wu.reset_password_with_code((body.get("name") or "").strip(), body.get("code") or "",
                                           body.get("new_pwd") or "", ip=request.remote_addr or "")
    return jsonify({"ok": True}) if ok else (jsonify({"ok": False, "error": err}), 400)


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    """Per-user application configuration (user 2026-07-03, Config tab): the user's own filter
    defaults (stored in their web_users record) + the SHARED trade-execution toggles per source
    (Supabase app_config — one trading account, so the switches are global; every change records
    who flipped it and lands in their activity log)."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    import config_store as _cs
    if request.method == "GET":
        s = _wu.get_settings(name)
        # Per-user broker leverage by instrument type (user 2026-07-03; defaults FX 100, else 10).
        lev = {"fx": 30, "equities": 10, "commodities": 10, "indices": 10}   # FX 100 -> 30 (user 2026-07-03)
        lev.update({k: v for k, v in (s.get("leverage") or {}).items() if k in lev})
        return jsonify({"name": name, "filters": s.get("filters", {}),
                        "exec": _cs.get_exec_flags(), "exec_sources": _cs.EXEC_SOURCES,
                        "exec_descriptions": _cs.EXEC_DESCRIPTIONS,
                        "bridge": _cs.get_value("exec_WEB_BRIDGE", "false") == "true",
                        "trade": (s.get("trade_filters") if s.get("trade_filters") is not None
                                  else _cs.get_trade_filters()),
                        "hidden_tabs": s.get("hidden_tabs", []), "leverage": lev,
                        "pinned_preorders": s.get("pinned_preorders", []),
                        "pinned_overrides": s.get("pinned_overrides", {}),
                        "engine": _cs.get_engine_settings(), "is_admin": _wu.is_admin(name),
                        "x_hvf_markets": _cs.get_x_hvf_markets(),
                        "features": {"xposts": _cs.get_value("feature_xposts", "false") == "true"}})
    body = request.get_json(silent=True) or {}
    if "trade" in body:
        # Trade filters are PER-USER (user 2026-07-06): they hide the excluded markets/directions/
        # locations from THIS user's Scanner + Pre-orders and block their pin/place — without affecting
        # what any other user sees. The OWNER's selection additionally mirrors to the global app_config
        # so the shared engine gate (ig_shim.trade_allowed / the squeeze bridge) stays in sync.
        t = body["trade"] or {}
        tf = {fname: [str(x) for x in (t.get(fname) or []) if isinstance(x, str)]
              for fname in _cs.TRADE_FILTER_KEYS}
        s = _wu.get_settings(name)
        s["trade_filters"] = tf
        _wu.set_settings(name, s)
        if name == _OWNER:
            for fname, key in _cs.TRADE_FILTER_KEYS.items():
                _cs.set_value(key, ",".join(tf[fname]) if tf[fname] else "ALL", updated_by=name)
        _wu.log_event(name, "Saved trade filters (Config): " +
                      "; ".join(f"{k}={','.join(v) if v else 'ALL'}" for k, v in tf.items()))
    if "filters" in body:
        s = _wu.get_settings(name)
        s["filters"] = {k: v for k, v in (body["filters"] or {}).items() if isinstance(k, str)}
        _wu.set_settings(name, s)
        _wu.log_event(name, "Saved filter defaults (Config)")
    if "hidden_tabs" in body:
        s = _wu.get_settings(name)
        # Configuration can never be hidden (user 2026-07-03: must stay reachable to change this).
        s["hidden_tabs"] = [t for t in (body["hidden_tabs"] or []) if isinstance(t, str) and t != "config"]
        _wu.set_settings(name, s)
        _wu.log_event(name, "Saved tab visibility (Config)")
    if "x_hvf_markets" in body:
        if not _wu.is_admin(name):
            return jsonify({"ok": False, "error": "admin only"}), 403
        vals = [str(x) for x in (body["x_hvf_markets"] or []) if isinstance(x, str)]
        _cs.set_value("x_hvf_markets", ",".join(vals), updated_by=name)
        _wu.log_event(name, "Saved X HVF tweet markets: " + (", ".join(vals) or "(default four)"))
    if "features" in body:
        if not _wu.is_admin(name):
            return jsonify({"ok": False, "error": "admin only"}), 403
        for k in ("xposts",):
            if k in (body["features"] or {}):
                _cs.set_value(f"feature_{k}", "true" if body["features"][k] else "false", updated_by=name)
        _wu.log_event(name, "Saved feature toggles (Config)")
    if "engine" in body:
        if not _wu.is_admin(name):
            return jsonify({"ok": False, "error": "admin only"}), 403
        for k in _cs.APP_ENGINE_KEYS:
            v = (body["engine"] or {}).get(k)
            if isinstance(v, (int, float)) and v >= 0:
                _cs.set_value(k, str(v), updated_by=name)
        _wu.log_event(name, "Saved engine settings (Config)")
    if "leverage" in body:
        s = _wu.get_settings(name)
        cur = s.get("leverage") or {}
        for k in ("fx", "equities", "commodities", "indices"):
            v = (body["leverage"] or {}).get(k)
            if isinstance(v, (int, float)) and v > 0:
                cur[k] = float(v)
        s["leverage"] = cur
        _wu.set_settings(name, s)
        _wu.log_event(name, "Saved broker leverage (Config)")
    if "exec" in body:
        for src, on in (body["exec"] or {}).items():
            if src in _cs.EXEC_SOURCES:
                _cs.set_value(f"exec_{src}", "true" if on else "false", updated_by=name)
                _wu.log_event(name, f"Trade execution for {src} switched {'ON' if on else 'OFF'}")
    if "bridge" in body:
        # Squeeze bridge (WEB_BRIDGE) execution — gated from Trading (Squeeze), user 2026-07-03.
        _cs.set_value("exec_WEB_BRIDGE", "true" if body["bridge"] else "false", updated_by=name)
        _wu.log_event(name, f"Squeeze bridge execution switched {'ON' if body['bridge'] else 'OFF'}")
    return jsonify({"ok": True})


def _user_trade_allows(name: str, rec: dict) -> bool:
    """PER-USER trade-filter gate (user 2026-07-06): does this user's own market/direction/location
    selection allow the given snapshot record? An empty list for a field = no restriction; a missing
    field value never blocks. Used to keep filtered-out markets out of the user's pin/place path."""
    if not rec:
        return True
    import config_store as _cs
    s = _wu.get_settings(name)
    tf = s.get("trade_filters")
    if tf is None:
        tf = _cs.get_trade_filters()                     # seed from legacy global for pre-migration users
    for key, field in (("directions", "direction"), ("markets", "market"), ("locations", "location")):
        allowed = tf.get(key) or []
        v = rec.get(field)
        if allowed and v is not None and v not in allowed:
            return False
    return True


_OWNER = "Alex"   # the account owner / administrator (also used by /api/order-ops)

# Credential sections (user 2026-07-03). Stored ENCRYPTED in the app and fully EDITABLE here; GitHub
# Secrets are only the one-off SEED (import_credentials_from_env below). IG is per-user (each login
# edits their own account); Supabase/X/Slack/Other are shared (owner edits, others masked read-only).
# Each field: (store_key, label, ENV_VAR_NAME) — the env name is used only for the one-off seed.
# admin_only sections are hidden from non-admins entirely (user 2026-07-03: only admins see X /
# Supabase / Slack / Server config). IG + Email(Yahoo) are visible to all (IG editable own; shared
# sections admin-editable, non-admins masked read-only).
CRED_SECTIONS = [
    {"id": "IG", "scope": "ig", "admin_only": False, "note": "Your own IG account — each user trades their own account.",
     "fields": [("ig_api_key", "IG API key", "IG_API_KEY"), ("ig_username", "IG username", "IG_USERNAME"),
                ("ig_password", "IG password", "IG_PASSWORD"), ("ig_account_id", "IG account ID", "IG_ACCOUNT_ID")]},
    {"id": "Supabase", "scope": "app", "admin_only": True, "note": "Shared database credentials.",
     "fields": [("supabase_user", "Supabase user", "SUPABASE_USER"),
                ("supabase_db_password", "Supabase DB password", "SUPABASE_DB_PASSWORD")]},
    {"id": "X Credentials", "scope": "app", "admin_only": True, "note": "Shared X (Twitter) API credentials.",
     "fields": [("x_api_key", "API key", "X_API_KEY"), ("x_api_secret", "API secret", "X_API_SECRET"),
                ("x_access_token", "Access token", "X_ACCESS_TOKEN"), ("x_access_secret", "Access secret", "X_ACCESS_SECRET")]},
    {"id": "Slack", "scope": "app", "admin_only": True, "note": "Shared Slack incoming-webhook URLs.",
     "fields": [("slack_alerts", "#alerts webhook", "SLACK_ALERTS"), ("slack_daily", "#daily webhook", "SLACK_DAILY"),
                ("slack_signals", "#signals webhook", "SLACK_SIGNALS"), ("slack_trades", "#trades webhook", "SLACK_TRADES"),
                ("slack_weekly", "#weekly webhook", "SLACK_WEEKLY")]},
    {"id": "Server", "scope": "app", "admin_only": True, "note": "Server-side data API keys.",
     "fields": [("fred_api_key", "FRED API key", "FRED_API_KEY"), ("eia_api_key", "EIA API key", "EIA_API_KEY"),
                ("quiver_quant_api_key", "Quiver Quant API key", "QUIVER_QUANT_API_KEY"),
                ("cronjob_api_key", "cron-job.org API key", "CRONJOB_API_KEY")]},
    {"id": "Email (Yahoo)", "scope": "app", "admin_only": False, "note": "Yahoo SMTP account for outbound email.",
     "fields": [("yahoo_user", "Yahoo user", "YAHOO_USER"), ("yahoo_app_password", "Yahoo app password", "YAHOO_APP_PASSWORD")]},
]


def import_credentials_from_env(owner: str = None):
    """One-off SEED (user 2026-07-03): populate the encrypted store from GitHub Secrets / env, once.
    IG -> the owner's per-user store; shared sections -> the app store. Only fills fields that are not
    already set (never clobbers an in-app edit). Safe to call on startup."""
    import os as _os
    owner = owner or _OWNER
    seeded = 0
    for sec in CRED_SECTIONS:
        for key, _label, env in sec["fields"]:
            val = (_os.environ.get(env) or "").strip()
            if not val:
                continue
            if sec["scope"] == "ig":
                if not _wu.get_secret(owner, key):
                    _wu.set_secret(owner, key, val); seeded += 1
            else:
                if not _wu.get_app_secret(key):
                    _wu.set_app_secret(key, val); seeded += 1
    if seeded:
        log.info(f"credential store seeded from env/GitHub Secrets ({seeded} field(s))")
    return seeded


def _mask(v: str) -> str:
    if not v:
        return ""
    return ("••••" + v[-4:]) if len(v) > 4 else "••••"


@app.route("/api/credentials", methods=["GET", "POST"])
def api_credentials():
    """Credentials — stored ENCRYPTED in the app (seeded once from GitHub Secrets, then fully editable
    here; user 2026-07-03). Full values are NEVER sent to the browser (masked last-4 only). IG is
    per-user (each login edits their own account). Supabase/X/Slack/Other are shared: the OWNER edits
    them, other users see them masked read-only."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    is_admin = _wu.is_admin(name)

    if request.method == "GET":
        sections = []
        for sec in CRED_SECTIONS:
            if sec.get("admin_only") and not is_admin:
                continue                                # hidden entirely from non-admins
            editable = True if sec["scope"] == "ig" else is_admin   # IG: own; shared: admin only
            fields = []
            for key, label, env in sec["fields"]:
                val = _wu.get_secret(name, key) if sec["scope"] == "ig" else _wu.get_app_secret(key)
                fields.append({"key": key, "label": label, "set": bool(val), "masked": _mask(val)})
            sections.append({"id": sec["id"], "scope": sec["scope"], "note": sec["note"],
                             "admin_only": bool(sec.get("admin_only")), "editable": editable, "fields": fields})
        return jsonify({"name": name, "is_admin": is_admin, "sections": sections})

    body = request.get_json(silent=True) or {}
    sec_id = body.get("section")
    values = body.get("values") or {}
    sec = next((s for s in CRED_SECTIONS if s["id"] == sec_id), None)
    if not sec:
        return jsonify({"ok": False, "error": "unknown section"}), 400
    if sec["scope"] == "app" and not is_admin:
        return jsonify({"ok": False, "error": "only an administrator can edit shared credentials"}), 403
    valid_keys = {k for k, _, _ in sec["fields"]}
    saved = []
    for key, val in values.items():
        if key not in valid_keys or not isinstance(val, str) or val == "":
            continue    # empty = leave unchanged
        if sec["scope"] == "ig":
            _wu.set_secret(name, key, val)
        else:
            _wu.set_app_secret(key, val)
        saved.append(key)
    if saved:
        _wu.log_event(name, f"Updated {sec_id} credentials ({len(saved)} field(s))")
    return jsonify({"ok": True, "saved": saved})


@app.route("/api/userlog")
def api_userlog():
    """The logged-in user's OWN operational log (user 2026-06-30) — token identifies the user, so
    each user only ever sees their own entries."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    return jsonify({"name": name, "log": _wu.get_log(name)})


def _load_snapshot() -> dict:
    try:
        with open(SNAPSHOT, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"generated_utc": None, "count": 0, "records": []}


def _record(ticker: str) -> dict:
    for r in _load_snapshot().get("records", []):
        if r.get("ticker") == ticker:
            return r
    return {}


@app.route("/")
def index():
    with open(os.path.join(_HERE, "index.html"), "r", encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html")


# Fields a LOGGED-OUT visitor may see (user 2026-07-03: first 5 Scanner columns; the rest obfuscated).
_PUBLIC_FIELDS = ("ticker", "name", "direction", "h3_date", "l3_date", "sector", "has_signal", "status")


@app.route("/api/records")
def api_records():
    snap = _load_snapshot()
    authed = request.headers.get("X-Auth") in _wu.valid_tokens()
    # The Scanner shows the FULL universe to every user — the Config trade filters gate only what the
    # operator TRADES (enforced in ig_shim at order time), never what is shown (user 2026-07-06).
    if authed:
        recs = [{k: v for k, v in r.items() if k != "_card"} for r in snap.get("records", [])]
    else:
        # Teaser mode (user 2026-07-03): only the first-5-column fields leave the server — the rest
        # are stripped HERE, not hidden client-side, so logged-out users cannot fetch them at all.
        recs = [{k: r.get(k) for k in _PUBLIC_FIELDS} for r in snap.get("records", [])]
    return jsonify({"generated_utc": snap.get("generated_utc"), "count": len(recs),
                    "records": recs, "limited": not authed})


def _png_response(png: bytes):
    # no-store so the browser never serves a stale image — UK cards rendered broken (16KB) while the
    # host disk was full and browsers cached that; without this they keep showing the empty one
    # (user 2026-06-27 "X post card still empty" even after the disk was freed).
    resp = send_file(io.BytesIO(png), mimetype="image/png")
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@app.route("/api/card/<ticker>")
def api_card(ticker):
    key = f"card:{ticker}"
    png = _PNG_CACHE.get(key)
    if not png:                        # cache only SUCCESSFUL renders — a transient yfinance throttle
        from intraday_signals import render_x_post_card   # must not stick an empty card in the cache
        rec = _record(ticker)          # (user 2026-06-28: "x post card still empty"); failures retry next load
        card = dict(rec.get("_card") or {})
        card["name"] = rec.get("name")
        with _RENDER_LOCK:
            png = render_x_post_card(card) or b""
        if png:
            _PNG_CACHE[key] = png
    return _png_response(png) if png else ("no card", 404)


@app.route("/api/hist3yr/<ticker>")
def api_hist3yr(ticker):
    key = f"hist3yr:{ticker}"
    if key not in _PNG_CACHE:
        from intraday_signals import render_3yr_history_card
        rec = _record(ticker)
        card = dict(rec.get("_card") or {})
        card["name"] = rec.get("name")
        with _RENDER_LOCK:
            _PNG_CACHE[key] = render_3yr_history_card(card) or b""
    png = _PNG_CACHE[key]
    return _png_response(png) if png else ("no 3yr chart", 404)


@app.route("/api/links/<ticker>")
def api_links(ticker):
    """On-demand X links for an instrument (user 2026-06-27): OUR latest publication (x_publications)
    and every tracked account we follow that posted about it (notable_investors.post_url). Queried
    live at selection time so the links are always current; never raises."""
    ours, mentions = None, []
    try:
        from db_pool import get_db
        db = get_db()
        try:
            rows = db.run("select tweet_id from x_publications where ticker = :t and tweet_id is not null "
                          "order by published_at desc limit 1", t=ticker)
            if rows:
                tid = rows[0][0]
                ours = {"tweet_id": str(tid), "url": f"https://x.com/{_X_HANDLE}/status/{tid}"}
            mrows = db.run("select investor_name, post_url, disclosed_at from notable_investors "
                           "where ticker = :t and post_url is not null order by disclosed_at desc limit 15", t=ticker)
            seen = set()
            for inv, url, dt in (mrows or []):
                if not url or url in seen:
                    continue
                seen.add(url)
                mentions.append({"account": inv, "url": url, "date": str(dt) if dt else None})
        finally:
            db.close()
    except Exception as e:
        log.warning(f"links lookup failed for {ticker}: {e}")
    return jsonify({"ticker": ticker, "ours": ours, "mentions": mentions})


@app.route("/api/tweet/<ticker>")
def api_tweet(ticker):
    """Build the exact X tweet text for ONE instrument on demand (one render = low memory; the build
    deliberately doesn't render all ~150)."""
    key = f"tweet:{ticker}"
    if key not in _PNG_CACHE:
        from intraday_signals import _generate_x_drafts
        rec = _record(ticker)
        card = dict(rec.get("_card") or {})
        card["name"] = rec.get("name")
        card["index"] = rec.get("market")
        txt = ""
        try:
            drafts = _generate_x_drafts([card], post=False, collect=True)
            if drafts:
                txt = drafts[0].get("tweet") or ""
        except Exception as e:
            log.warning(f"tweet render failed for {ticker}: {e}")
        _PNG_CACHE[key] = txt
    return jsonify({"ticker": ticker, "tweet": _PNG_CACHE[key]})


@app.route("/api/thread/<ticker>")
def api_thread(ticker):
    """ALL pages of the X publication for one instrument (user 2026-06-27 'not showing all pages
    6/7'): the lead short tweet + every numbered long-report part (1/n..n/n). One render = low mem."""
    key = f"thread:{ticker}"
    if key not in _PNG_CACHE:
        rec = _record(ticker)
        card = dict(rec.get("_card") or {})
        card["name"] = rec.get("name"); card["index"] = rec.get("market")
        parts = []
        try:
            from intraday_signals import _generate_x_drafts
            # collect=True renders the card PNG via matplotlib (pyplot) — must share the render lock or it
            # races /api/card and both come out blank (user 2026-06-29 "X post card is empty for ABF.L").
            with _RENDER_LOCK:
                drafts = _generate_x_drafts([card], post=False, collect=True)
            if drafts and drafts[0].get("tweet"):
                parts.append(drafts[0]["tweet"])
        except Exception as e:
            log.warning(f"thread lead failed for {ticker}: {e}")
        try:
            from quality_report import publish_long_report_for
            parts += [p for p in (publish_long_report_for(card, post=False) or []) if p]
        except Exception as e:
            log.warning(f"thread report failed for {ticker}: {e}")
        _PNG_CACHE[key] = parts
    return jsonify({"ticker": ticker, "parts": _PNG_CACHE[key]})


def _rule_detail(rec: dict) -> list:
    """Rolls-Royce-style per-rule justification (user 2026-06-27) with the actual numbers, so each
    of the 5 RW rules is explained, not just PASS/FAIL."""
    c = rec.get("_card") or {}
    bull = rec.get("direction") == "BULL"
    g = lambda k: c.get(k)
    h1, h2, h3 = g("h1_level"), g("h2_level"), g("h3_level")
    l1, l2, l3 = g("l1_level"), g("l2_level"), g("l3_level")
    entry, stop, target, rr = rec.get("entry"), rec.get("stop"), rec.get("target"), rec.get("rr")
    out, base = [], {u["n"]: u for u in (rec.get("rules") or [])}

    def add(n, name, verdict, detail):
        out.append({"n": n, "name": name, "verdict": (base.get(n) or {}).get("verdict", verdict), "detail": detail})

    add(1, "Prior trend", "PASS",
        f"The funnel trades in the direction of the prior trend ({'BULLISH long' if bull else 'BEARISH short'}). "
        f"A clear, recent move of the same direction is needed before the coil forms.")
    if None not in (h1, h2, h3, l1, l2, l3):
        add(2, "Three swings", "PASS",
            f"Lower highs  H1 {h1:g} ({g('h1_date')}) > H2 {h2:g} > H3 {h3:g} ({g('h3_date')}); "
            f"higher lows  L1 {l1:g} ({g('l1_date')}) < L2 {l2:g} < L3 {l3:g} ({g('l3_date')}). "
            f"Three real, alternating candle swings converging — the HVF pattern.")
    else:
        add(2, "Three swings", "DEVELOPING", "Not all three alternating swings are confirmed yet.")
    if None not in (h1, h3, l1, l3) and (h1 - l1):
        amp1 = h1 - l1; tight = (h3 - l3) / amp1 * 100
        add(3, "Tightness ≤35%", "PASS" if tight <= 35 else "FAIL",
            f"Current funnel range (H3−L3 = {h3 - l3:g}) vs the funnel mouth (FNM1 = H1−L1 = {amp1:g}) "
            f"= {tight:.0f}%. Compresses to ≤35% — tighter coil, tighter stop.")
        mid = (h3 + l3) / 2
        add(4, "Levels & target", "PASS" if (target and target > 0) else "FAIL",
            f"FNM1 = {amp1:g}; midpoint (H3+L3)/2 = {mid:g}. Entry = {entry:g} (break of the 3rd "
            f"{'high' if bull else 'low'}); stop beyond the opposite pivot = {stop:g}; "
            f"target = mid {'+' if bull else '−'} FNM1 = {target:g}.")
    if isinstance(rr, (int, float)):
        risk = abs((entry or 0) - (stop or 0)); reward = abs((target or 0) - (entry or 0))
        add(5, "R:R ≥ 3:1", "PASS" if rr >= 3 else "DEVELOPING",
            f"Reward {reward:g} ÷ risk {risk:g} = {rr:.2f}:1 (minimum 3:1).")
    return out


@app.route("/api/rules/<ticker>")
def api_rules(ticker):
    return jsonify({"ticker": ticker, "rules": _rule_detail(_record(ticker))})


@app.route("/api/positions")
def api_positions():
    """Live count of OPEN IG positions per instrument (user 2026-06-27). Best-effort: needs IG env +
    epic_lookup; returns {} on any failure so the page still loads."""
    counts = {}
    try:
        import ig_shim
        epic2tk = {}
        try:
            from db_pool import get_db
            db = get_db()
            try:
                for row in (db.run("select ticker, epic from epic_lookup") or []):
                    if row[1]:
                        epic2tk[str(row[1])] = row[0]
            finally:
                db.close()
        except Exception:
            pass
        with ig_shim._IG_LOCK:      # serialise vs a per-user place-order session swap (user 2026-07-03)
            _positions = ig_shim.get_open_positions() or []
        for pos in _positions:
            mk = pos.get("market", {}) or {}
            tk = epic2tk.get(str(mk.get("epic"))) or mk.get("instrumentName") or mk.get("epic")
            if tk:
                counts[tk] = counts.get(tk, 0) + 1
    except Exception as e:
        log.warning(f"positions lookup failed: {e}")
    return jsonify({"positions": counts})


@app.route("/api/pubcounts")
def api_pubcounts():
    """Number of X posts we've published per instrument (user 2026-06-28) — one row per publication in
    x_publications. Fetched once for the whole list; never raises."""
    counts = {}
    try:
        from db_pool import get_db
        db = get_db()
        try:
            for row in (db.run("select ticker, count(*) from x_publications "
                               "where tweet_id is not null group by ticker") or []):
                if row[0]:
                    counts[row[0]] = row[1]
        finally:
            db.close()
    except Exception as e:
        log.warning(f"pubcounts lookup failed: {e}")
    return jsonify({"pubcounts": counts})


@app.route("/api/working-orders")
def api_working_orders():
    """Tickers that already have a LIVE IG working order (working_orders.status='PENDING'), so the web
    Pre-orders tab can drop them once they've moved to IG (user 2026-06-29: "remove from the database once
    in IG"). Best-effort; returns [] on any DB/table error so the page still loads."""
    tickers = []
    try:
        from db_pool import get_db
        db = get_db()
        try:
            # PENDING = live on IG (hidden for everyone — one trading account); DELETED (30 days) is
            # PER-USER (user 2026-07-03: each login has their own data set) — a delete by Rich only
            # hides the setup for Rich.
            _name = _wu.name_for_token(request.headers.get("X-Auth") or "")
            rows = db.run("select distinct ticker from working_orders where status = 'PENDING' "
                          "or (status = 'DELETED' and user_id = :u and updated_at > now() - interval '30 days')",
                          u=_name or "-")
            tickers = [r[0] for r in (rows or []) if r[0]]
        finally:
            db.close()
    except Exception as e:
        log.warning(f"working-orders lookup failed: {e}")
    return jsonify({"tickers": tickers})


_REFRESHING = {"on": False}


def _do_rebuild() -> bool:
    """Rebuild the snapshot (shared by the 12h loop + the manual refresh button). Guards against a
    concurrent rebuild and clears the PNG/tweet/links caches afterwards."""
    if _REFRESHING["on"]:
        return False
    _REFRESHING["on"] = True
    try:
        from hvf_web.build_snapshot import build
        build()
        _PNG_CACHE.clear()
        log.info("snapshot rebuilt; caches cleared")
        return True
    except Exception as e:
        log.error(f"snapshot rebuild failed: {e}")
        return False
    finally:
        _REFRESHING["on"] = False


@app.route("/api/refresh", methods=["POST", "GET"])
def api_refresh():
    """Trigger an on-demand snapshot rebuild in a background thread (user 2026-06-28). Login-gated,
    and the request is recorded in the acting user's activity log (user 2026-07-03)."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    if not _wu.is_admin(name):                     # admin-only (user 2026-07-03)
        return jsonify({"error": "admin only"}), 403
    if _REFRESHING["on"]:
        _wu.log_event(name, "Requested data refresh (one already running)")
        return jsonify({"started": False, "busy": True})
    _wu.log_event(name, "Requested data refresh (full universe rebuild)")
    _append_batch("Refresh button", "Full universe snapshot rebuild started", by=name)
    import threading
    threading.Thread(target=_do_rebuild, daemon=True).start()
    return jsonify({"started": True})


@app.route("/api/status")
def api_status():
    snap = _load_snapshot()
    resp = {"refreshing": _REFRESHING["on"], "generated_utc": snap.get("generated_utc"),
            "count": snap.get("count")}
    if _REFRESHING["on"]:                     # live "34/424" progress while a build runs (user 2026-06-29)
        try:
            from hvf_web.build_snapshot import PROGRESS
            resp["progress"] = {"done": PROGRESS.get("done", 0), "total": PROGRESS.get("total", 0)}
        except Exception:
            pass
    return jsonify(resp)


@app.route("/api/fundamentals/<ticker>")
def api_fundamentals(ticker):
    """Company KPIs straight from yfinance .info (user 2026-06-28): P/E, FCF, dividends, margins, growth,
    leverage, etc. Live per-ticker; graceful (empty kpis) if Yahoo is unreachable."""
    out = {}
    cur = None
    try:
        import yfinance as yf
        try:
            from config import YAHOO_MAP
        except Exception:
            YAHOO_MAP = {}
        info = yf.Ticker(YAHOO_MAP.get(ticker, ticker)).info or {}
        cur = info.get("currency") or ("GBp" if ticker.endswith(".L") else "USD")

        def n(k):
            v = info.get(k)
            return v if isinstance(v, (int, float)) else None
        price = n("currentPrice") or n("regularMarketPrice")
        drate = n("dividendRate")
        # Use yfinance's own yield (it handles the .L pence/pounds units); only fall back to rate/price
        # when it's missing. Normalise the percent-vs-fraction quirk (some versions give 2.9, some 0.029).
        dyield = n("dividendYield")
        if dyield is None and drate and price:
            dyield = drate / price
        if isinstance(dyield, (int, float)) and dyield > 1.5:
            dyield = dyield / 100.0
        out = {
            "marketCap": n("marketCap"), "totalRevenue": n("totalRevenue"), "ebitda": n("ebitda"),
            "trailingPE": n("trailingPE"), "forwardPE": n("forwardPE"),
            "pegRatio": n("trailingPegRatio") or n("pegRatio"), "priceToBook": n("priceToBook"),
            "evToEbitda": n("enterpriseToEbitda"), "priceToSales": n("priceToSalesTrailing12Months"),
            "trailingEps": n("trailingEps"), "forwardEps": n("forwardEps"),
            "dividendRate": drate, "dividendYield": dyield, "payoutRatio": n("payoutRatio"),
            "freeCashflow": n("freeCashflow"), "operatingCashflow": n("operatingCashflow"),
            "profitMargin": n("profitMargins"), "operatingMargin": n("operatingMargins"),
            "grossMargin": n("grossMargins"), "roe": n("returnOnEquity"), "roa": n("returnOnAssets"),
            "revenueGrowth": n("revenueGrowth"), "earningsGrowth": n("earningsGrowth"),
            "debtToEquity": n("debtToEquity"), "currentRatio": n("currentRatio"),
            "quickRatio": n("quickRatio"), "beta": n("beta"),
            "fiftyTwoWeekHigh": n("fiftyTwoWeekHigh"), "fiftyTwoWeekLow": n("fiftyTwoWeekLow"),
        }
    except Exception as e:
        log.warning(f"fundamentals lookup failed for {ticker}: {e}")
    return jsonify({"ticker": ticker, "currency": cur, "kpis": out})


@app.route("/api/broker/<ticker>")
def api_broker(ticker):
    """Change in broker coverage over the last 6 and 12 months (user 2026-06-27): net analyst
    upgrades vs downgrades from yfinance upgrades_downgrades. Live per-ticker; graceful if Yahoo is
    unreachable (available=False)."""
    res = {"up6": 0, "down6": 0, "up12": 0, "down12": 0, "available": False}
    try:
        import yfinance as yf, pandas as pd
        try:
            from config import YAHOO_MAP
        except Exception:
            YAHOO_MAP = {}
        ud = yf.Ticker(YAHOO_MAP.get(ticker, ticker)).upgrades_downgrades
        if ud is not None and not ud.empty:
            res["available"] = True
            now = pd.Timestamp.now(tz="UTC")
            for dt, row in ud.iterrows():
                try:
                    d = pd.Timestamp(dt)
                    d = d.tz_localize("UTC") if d.tzinfo is None else d.tz_convert("UTC")
                except Exception:
                    continue
                months = (now - d).days / 30.44
                act = str(row.get("Action", "")).lower()
                if 0 <= months <= 12:
                    if act == "up":
                        res["up12"] += 1; res["up6"] += (months <= 6)
                    elif act == "down":
                        res["down12"] += 1; res["down6"] += (months <= 6)
    except Exception as e:
        log.warning(f"broker history failed for {ticker}: {e}")
    return jsonify({"ticker": ticker, **{k: int(v) if isinstance(v, bool) else v for k, v in res.items()}})


def _render_price_window(rec: dict, days: int, theme: str) -> bytes:
    """Website-only price+funnel chart for the last `days` sessions — re-rendered as the date-range
    filter changes (does NOT touch the protected production card). Funnel pivots that fall inside the
    window are overlaid; entry/stop/target drawn as horizontal lines."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd, yfinance as yf
    from datetime import datetime, timezone, timedelta
    try:
        from config import YAHOO_MAP
    except Exception:
        YAHOO_MAP = {}
    dark = theme != "light"
    bg, fg, grid = ("#0d1117", "#c9d1d9", "#30363d") if dark else ("#ffffff", "#24292f", "#d0d7de")
    tk = rec.get("ticker", "")
    card = rec.get("_card") or {}
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(20, days))
    _yt = YAHOO_MAP.get(tk, tk)
    # Supabase price_history is the golden source (user 2026-06-29); get_bars_or_fetch reads it first and
    # only hits yfinance on a miss/stale bar (writing the result back). The pkl below stays as a last-ditch
    # fallback for when both Supabase and yfinance are unreachable.
    try:
        import price_store
        hist = price_store.get_bars_or_fetch(tk, _yt, start, end)
    except Exception:
        hist = None
    import pandas as _pd
    _pc_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "price_cache")
    _pc_file = os.path.join(_pc_dir, f"win_{_yt}_{int(days)}".replace("/", "_").replace("^", "_").replace("=", "_") + ".pkl")
    if hist is not None and not hist.empty:
        try:
            os.makedirs(_pc_dir, exist_ok=True); hist.to_pickle(_pc_file)
        except Exception:
            pass
    elif os.path.exists(_pc_file):
        try:
            hist = _pd.read_pickle(_pc_file)
        except Exception:
            hist = None
    fig = plt.figure(figsize=(9, 4.2)); fig.patch.set_facecolor(bg)
    ax = fig.add_axes([0.08, 0.12, 0.88, 0.80]); ax.set_facecolor(bg)
    if hist is None or hist.empty:
        ax.text(0.5, 0.5, "no price data", color=fg, ha="center");
    else:
        close = hist["Close"].squeeze().dropna()
        ax.plot(close.index, close.values, color="#58a6ff", lw=1.5)
        col = "#3fb950" if card.get("hvf_type") == "BULLISH" else "#f85149"
        for lvl, lab, c in ((card.get("h3_level"), "Entry", "#e3b341"),
                            (card.get("stop_level"), "Stop", "#f85149"),
                            (card.get("target"), "Target", "#3fb950")):
            if isinstance(lvl, (int, float)):
                ax.axhline(lvl, color=c, lw=1.0, ls="--", alpha=0.8)
                ax.text(close.index[-1], lvl, f" {lab}", color=c, fontsize=8, va="center")
        # funnel pivots inside the window
        for dk, lk, c in (("h1_date", "h1_level", col), ("h2_date", "h2_level", col), ("h3_date", "h3_level", col),
                          ("l1_date", "l1_level", "#3fb950"), ("l2_date", "l2_level", "#3fb950"), ("l3_date", "l3_level", "#3fb950")):
            d, l = card.get(dk), card.get(lk)
            if d and isinstance(l, (int, float)):
                try:
                    dt = pd.Timestamp(d)
                    if close.index[0] <= dt.tz_localize(close.index.tz) <= close.index[-1]:
                        ax.scatter([dt], [l], color=c, s=22, zorder=5)
                except Exception:
                    pass
    ax.tick_params(colors=fg, labelsize=8)
    for s in ax.spines.values():
        s.set_color(grid)
    ax.grid(True, color=grid, alpha=0.4, lw=0.5)
    ax.set_title(f"{tk} — last {days}d", color=fg, fontsize=10)
    buf = io.BytesIO(); fig.savefig(buf, format="png", facecolor=bg, dpi=110); plt.close(fig)
    return buf.getvalue()


@app.route("/api/pricewin/<ticker>")
def api_pricewin(ticker):
    days = int(request.args.get("days", "180") or 180)
    theme = request.args.get("theme", "dark")
    rec = _record(ticker)
    if not rec:
        return ("unknown ticker", 404)
    with _RENDER_LOCK:
        png = _render_price_window(rec, days, theme)
    return _png_response(png)


def _refresh_loop():
    """Rebuild the snapshot every 12h (user 2026-06-27) — and once on startup if it's missing or
    already older than 12h. The build is light (no PNG rendering — those are lazy), so running it
    in-process is fine; the PNG/tweet/links caches are cleared after each rebuild. Checks every 6h."""
    import time as _t
    from datetime import datetime, timezone
    while True:
        try:
            need = True
            snap = _load_snapshot()
            gen = snap.get("generated_utc")
            if gen:
                try:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(gen)).total_seconds()
                    need = age >= 12 * 3600
                except Exception:
                    need = True
            if need:
                log.info("snapshot refresh (12h): building ...")
                _do_rebuild()
        except Exception as e:
            log.warning(f"snapshot refresh failed (will retry): {e}")
        _t.sleep(6 * 3600)


def _bridge_loop():
    """Database -> IG order bridge every BRIDGE_INTERVAL_H hours (user 2026-06-30, Orders A/B):
    READY snapshot setups with quality > 50 within 1.5% of entry go to the guarded IG order path
    (hvf_web/order_bridge.py). First pass ~2 min after startup so a restart re-checks promptly."""
    import time as _t
    _t.sleep(120)
    interval_h = 2
    while True:
        try:
            from hvf_web.order_bridge import run_bridge, BRIDGE_INTERVAL_H
            interval_h = BRIDGE_INTERVAL_H
            run_bridge()
        except Exception as e:
            log.warning(f"order bridge pass failed (will retry): {e}")
        _t.sleep(interval_h * 3600)


@app.route("/api/preorder-delete", methods=["POST"])
def api_preorder_delete():
    """Delete (dismiss) one or more pre-orders (user 2026-07-03): records a DELETED row in
    working_orders — visible in Order (Operations) — and hides the ticker from Pre-orders for 30
    days. Login-gated; the acting user is recorded on the row and in their activity log."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    body = request.get_json(silent=True) or {}
    tickers = [t for t in (body.get("tickers") or []) if isinstance(t, str) and t.strip()][:50]
    if not tickers:
        return jsonify({"ok": False, "error": "no tickers"}), 400
    snap = {r.get("ticker"): r for r in _load_snapshot().get("records", [])}
    deleted = []
    try:
        from db_pool import get_db
        import time as _t
        db = get_db()
        try:
            for tk in tickers:
                r = snap.get(tk) or {}
                db.run("insert into working_orders (deal_ref, user_id, ticker, epic, direction, size, "
                       "entry_level, stop_level, limit_level, otype, hvf_type, status, session, notes) "
                       "values (:ref,:uid,:tk,:epic,:dir,0,:entry,:stop,:tgt,'STOP',:ht,'DELETED','WEB_USER',:no)",
                       ref=f"WEBDEL-{tk}-{int(_t.time())}", uid=name, tk=tk, epic=tk,
                       dir=("BUY" if r.get("direction") == "BULL" else "SELL"),
                       entry=float(r.get("entry") or 0), stop=r.get("stop"), tgt=r.get("target"),
                       ht=("BULLISH" if r.get("direction") == "BULL" else "BEARISH"),
                       no=f"Deleted from Pre-orders by {name}")
                deleted.append(tk)
        finally:
            db.close()
    except Exception as e:
        log.warning(f"preorder-delete failed: {e}")
        return jsonify({"ok": False, "error": "database error", "deleted": deleted}), 500
    _wu.log_event(name, f"Deleted pre-order(s): {', '.join(deleted)}")
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/api/preorder-pin", methods=["POST"])
def api_preorder_pin():
    """Push a Scanner instrument into (or out of) the user's pinned pre-orders (user 2026-07-03) —
    forces it into My Pre-orders even if it wouldn't naturally qualify. Per-user, stored in settings."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    if _wu.get_subscription(name) == "guest" and not _wu.is_admin(name):
        return jsonify({"ok": False, "error": "your subscription has no pre-orders"}), 403
    body = request.get_json(silent=True) or {}
    tk = (body.get("ticker") or "").strip()
    if not tk:
        return jsonify({"ok": False, "error": "no ticker"}), 400
    if bool(body.get("on", True)) and not _user_trade_allows(name, _record(tk)):
        return jsonify({"ok": False, "error": "that market/direction is excluded in your Trading (Squeeze) filters"}), 403
    s = _wu.get_settings(name)
    pinned = set(s.get("pinned_preorders") or [])
    on = bool(body.get("on", True))
    pinned.add(tk) if on else pinned.discard(tk)
    s["pinned_preorders"] = sorted(pinned)
    # Optional level overrides (user 2026-07-04): entry/stop/target may be edited on the way from the
    # Scanner to My Pre-orders. Any CHANGE vs the setup's own levels is recorded in My Activity.
    ov = s.get("pinned_overrides") or {}
    if on and isinstance(body.get("levels"), dict):
        rec = _record(tk) or {}
        lv, diffs = {}, []
        for k in ("entry", "stop", "target"):
            v = body["levels"].get(k)
            if isinstance(v, (int, float)) and v > 0:
                lv[k] = float(v)
                orig = rec.get(k)
                if orig is not None and abs(float(orig) - float(v)) > 1e-9:
                    diffs.append(f"{k} {orig:g} → {v:g}")
        if lv:
            ov[tk] = lv
        if diffs:
            _wu.log_event(name, f"Pre-order {tk}: levels adjusted ({'; '.join(diffs)})")
    if not on:
        ov.pop(tk, None)
    s["pinned_overrides"] = ov
    _wu.set_settings(name, s)
    _wu.log_event(name, f"{'Pinned' if on else 'Unpinned'} pre-order: {tk}")
    return jsonify({"ok": True, "pinned": s["pinned_preorders"], "overrides": ov})


def _sig_from_snapshot(tk, user=None):
    """Build an engine sig dict from a snapshot record, for manual order placement. If the user has
    edited entry/stop/target when pinning (user 2026-07-04), THEIR levels are used."""
    r = _record(tk)
    if not (r and r.get("has_signal")):
        return None
    card = r.get("_card") or {}
    ov = {}
    if user:
        ov = (_wu.get_settings(user).get("pinned_overrides") or {}).get(tk) or {}
    return {"ticker": tk, "direction": "BUY" if r.get("direction") == "BULL" else "SELL",
            "hvf_type": card.get("hvf_type") or ("BULLISH" if r.get("direction") == "BULL" else "BEARISH"),
            "hvf_signal": r.get("status"), "hvf_h3_level": ov.get("entry", r.get("entry")),
            "hvf_stop_level": ov.get("stop", r.get("stop")), "hvf_target": ov.get("target", r.get("target")),
            "hvf_quality": r.get("quality"), "hvf_risk_reward": r.get("rr"),
            "hvf_timeframe": r.get("timeframe"), "index": r.get("market"), "location": r.get("location")}


@app.route("/api/place-order", methods=["POST"])
def api_place_order():
    """Manually place a pre-order as a live IG working order NOW (user 2026-07-03), instead of waiting
    for the 2-hour bridge. MONEY PATH: subscription must allow pre-orders; the order uses the user's
    OWN IG account (owner = env creds; a non-owner must have supplied their own IG credentials — else
    blocked so no one trades on another account). Goes through the same guarded place_hvf_order_from_sig."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    if _wu.get_subscription(name) == "guest" and not _wu.is_admin(name):
        return jsonify({"ok": False, "error": "your subscription cannot place orders"}), 403
    try:
        import ig_shim
        if ig_shim.session_for(name) is None:    # non-owner without their own IG creds
            return jsonify({"ok": False, "error": "no IG credentials of your own — set them in Configuration → IG"}), 403
    except Exception as e:
        return jsonify({"ok": False, "error": f"IG session error: {e}"}), 500
    body = request.get_json(silent=True) or {}
    tk = (body.get("ticker") or "").strip()
    if not _user_trade_allows(name, _record(tk)):
        return jsonify({"ok": False, "error": "that market/direction is excluded in your Trading (Squeeze) filters"}), 403
    sig = _sig_from_snapshot(tk, user=name)
    if not sig:
        return jsonify({"ok": False, "error": "no signal for that instrument"}), 400
    try:
        from run_session import get_user_profile
        import ig_shim
        # Place using the ACTING user's OWN IG session (user 2026-07-03): owner -> env creds; a
        # non-owner -> their own credentials (or blocked above). The swap is serialised under _IG_LOCK.
        with ig_shim.acting_session(name):
            wo = ig_shim.place_hvf_order_from_sig(sig, get_user_profile(), "WEB_MANUAL", 1.0)
    except Exception as e:
        log.warning(f"manual place-order {tk} failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
    _wu.log_event(name, f"Manually placed order: {tk}" + (f" ({wo.get('status')})" if wo else " (not placed)"))
    _append_batch("Manual (web)", f"Placed {tk} order", by=name)
    return jsonify({"ok": bool(wo), "status": (wo or {}).get("status"), "placed": bool(wo)})


@app.route("/api/x-posts")
def api_x_posts():
    """All tweets we've published (user 2026-07-03, X tab). ADMIN-only. Each row carries the tweet
    URL, thread size, and the instrument name/market from the snapshot for filtering."""
    if not _wu.is_admin(_wu.name_for_token(request.headers.get("X-Auth") or "")):
        return jsonify({"error": "admin only"}), 403
    snap = {r.get("ticker"): r for r in _load_snapshot().get("records", [])}
    rows = []
    try:
        from db_pool import get_db
        db = get_db()
        try:
            for r in (db.run("select ticker, tweet_id, published_at::timestamp(0), thread_ids "
                             "from x_publications order by published_at desc limit 500") or []):
                tk, tid, pub, thread = r[0], r[1], str(r[2] or ""), (r[3] or "")
                srec = snap.get(tk) or {}
                n = len([x for x in thread.split(",") if x.strip()]) if thread else 1
                rows.append({"ticker": tk, "name": srec.get("name") or "",
                             "market": srec.get("market") or "", "sector": srec.get("sector") or "",
                             "published_at": pub, "tweet_id": tid, "thread": n,
                             "url": f"https://x.com/{_X_HANDLE}/status/{tid}"})
        finally:
            db.close()
    except Exception as e:
        log.warning(f"x-posts lookup failed: {e}")
    return jsonify({"rows": rows})


@app.route("/api/order-ops")
def api_order_ops():
    """Operational record of database -> IG order moves (user 2026-06-30): the working_orders rows,
    newest first. Login-gated like /api/records."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    rows = []
    try:
        from db_pool import get_db
        db = get_db()
        try:
            # Per-user visibility (user 2026-07-03): Alex (the account owner) sees everything incl.
            # the system sessions' rows; any other login sees ONLY rows recorded under their name.
            _where = "" if name == "Alex" else "where user_id = :u "
            for r in (db.run(
                    "select placed_at::timestamp(0), updated_at::timestamp(0), ticker, direction, "
                    "entry_level, stop_level, limit_level, size, status, session, notes "
                    f"from working_orders {_where}order by coalesce(updated_at, placed_at) desc limit 200",
                    **({} if name == "Alex" else {"u": name})) or []):
                rows.append({"placed_at": str(r[0] or ""), "updated_at": str(r[1] or ""), "ticker": r[2],
                             "direction": r[3], "entry": r[4], "stop": r[5], "target": r[6],
                             "size": r[7], "status": r[8], "session": r[9], "notes": r[10] or ""})
        finally:
            db.close()
    except Exception as e:
        log.warning(f"order-ops lookup failed: {e}")
    return jsonify({"rows": rows})


if __name__ == "__main__":
    import threading
    try:
        import_credentials_from_env()   # one-off seed of the encrypted store from GitHub Secrets/env
    except Exception as _e:
        log.warning(f"credential seed skipped: {_e}")
    try:
        import web_store                 # one-off migration of legacy JSON rows -> Supabase
        web_store.migrate_from_files(batch_file=_BATCH_FILE,
                                     users_file=os.path.join(_DATA_DIR, "web_users.json"))
    except Exception as _e:
        log.warning(f"web_store migration skipped: {_e}")
    threading.Thread(target=_refresh_loop, daemon=True).start()
    threading.Thread(target=_bridge_loop, daemon=True).start()
    log.info("HVF site on http://127.0.0.1:5057  (ngrok http 5057 to share)")
    app.run(host="0.0.0.0", port=5057, debug=False, threaded=True)
