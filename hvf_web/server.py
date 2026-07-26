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
import re as _re
import datetime as _dt
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


def _limit_defaults() -> dict:
    """Code defaults for a user's PERSONAL trading limits (user 2026-07-10), sourced from config.py so
    they track the shared engine's baseline. Per-user overrides layer on top."""
    import config as _cfg
    base = {"min_risk_reward": float(getattr(_cfg, "MIN_RISK_REWARD", 3.0)),
            "min_quality": int(getattr(_cfg, "MIN_PUBLISH_QUALITY", 25)),
            "max_trades_per_instrument_per_day": int(getattr(_cfg, "MAX_TRADES_PER_INSTRUMENT_PER_DAY", 5)),
            "bounce_alert_pct": float(getattr(_cfg, "BOUNCE_ALERT_PCT", 0.02)),
            "bounce_lookback_hours": int(getattr(_cfg, "BOUNCE_LOOKBACK_HOURS", 48)),
            "email_recipients": list(getattr(_cfg, "EMAIL_RECIPIENTS", []))}
    # Fall back to the OWNER's (Alex's) saved limits where set (user 2026-07-11): a user who hasn't
    # picked their own inherits Alex's, not just the code baseline.
    try:
        owner = (_wu.get_settings(_OWNER) or {}).get("limits") or {}
        base.update({k: v for k, v in owner.items() if k in base})
    except Exception:
        pass
    return base


def _user_limits(s: dict) -> dict:
    d = _limit_defaults()
    d.update({k: v for k, v in (s.get("limits") or {}).items() if k in d})
    return d


def _limit_block(name: str, tk: str, on: bool = True) -> str:
    """If the acting user's personal R:R / Quality floor excludes this setup, return a reason; else ''.
    Gates the user's OWN manual actions only (user 2026-07-10) — never the shared automated engine."""
    if not on:
        return ""
    rec = _record(tk) or {}
    lim = _user_limits(_wu.get_settings(name))
    rr, q = rec.get("rr"), rec.get("quality")
    if isinstance(rr, (int, float)) and rr < lim["min_risk_reward"]:
        return f"R:R {rr} is below your personal floor of {lim['min_risk_reward']:g} (Configuration → My trading limits)"
    if isinstance(q, (int, float)) and q < lim["min_quality"]:
        return f"Quality {q} is below your personal floor of {lim['min_quality']} (Configuration → My trading limits)"
    return ""


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
        # Per-user broker leverage by instrument type. Defaults match IG UK RETAIL margin, i.e. the
        # FCA/ESMA leverage caps (user 2026-07-24, P-02): major FX 3.33%->30, major indices 5%->20,
        # commodities (non-gold) 10%->10, individual shares 20%->5. Per-user overrides still win.
        lev = {"fx": 30, "equities": 5, "commodities": 10, "indices": 20}
        lev.update({k: v for k, v in (s.get("leverage") or {}).items() if k in lev})
        return jsonify({"name": name, "filters": s.get("filters", {}),
                        "exec": _cs.get_exec_flags(), "exec_sources": _cs.EXEC_SOURCES,
                        "exec_descriptions": _cs.EXEC_DESCRIPTIONS,
                        "bridge": _cs.get_value("exec_WEB_BRIDGE", "false") == "true",
                        "trade": (s.get("trade_filters") if s.get("trade_filters") is not None
                                  else _cs.get_trade_filters()),
                        "hidden_tabs": s.get("hidden_tabs", []), "shown_tabs": s.get("shown_tabs", []), "leverage": lev,
                        "limits": _user_limits(s),
                        "markets_disabled": [m for m in _cs.get_value("markets_disabled", "").split(",") if m],
                        "markets_off": s.get("markets_off", []),
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
    if "shown_tabs" in body:
        # Opt-in list for tabs that are hidden by DEFAULT (user 2026-07-10): a default-hidden tab is
        # only visible if the user has explicitly enabled it here.
        s = _wu.get_settings(name)
        s["shown_tabs"] = [t for t in (body["shown_tabs"] or []) if isinstance(t, str)]
        _wu.set_settings(name, s)
    if "limits" in body:
        # PER-USER trading limits (user 2026-07-10): stored on the user's record. They gate THIS user's
        # own web actions (manual place-on-IG / pin-to-pre-orders) and their view — the shared automated
        # engine + detection keep using the config.py baseline (one scan, one IG account).
        s = _wu.get_settings(name)
        cur = s.get("limits") or {}
        b = body["limits"] or {}
        for k in ("min_risk_reward", "bounce_alert_pct"):
            v = b.get(k)
            if isinstance(v, (int, float)) and v >= 0:
                cur[k] = float(v)
        for k in ("min_quality", "max_trades_per_instrument_per_day", "bounce_lookback_hours"):
            v = b.get(k)
            if isinstance(v, (int, float)) and v >= 0:
                cur[k] = int(v)
        if isinstance(b.get("email_recipients"), list):
            cur["email_recipients"] = [str(x).strip() for x in b["email_recipients"] if str(x).strip()]
        s["limits"] = cur
        _wu.set_settings(name, s)
        _wu.log_event(name, "Saved personal trading limits (Config)")
    if "markets_disabled" in body:
        # APP-LEVEL market on/off (user 2026-07-11, Markets Admin switch): a disabled market is hidden
        # from everyone's Scanner/Pre-orders and forced OFF for each user. Admin only.
        if not _wu.is_admin(name):
            return jsonify({"ok": False, "error": "admin only"}), 403
        vals = sorted({str(m).strip() for m in (body["markets_disabled"] or []) if str(m).strip()})
        _cs.set_value("markets_disabled", ",".join(vals), updated_by=name)
        _wu.log_event(name, "Saved disabled markets (Markets Admin): " + (", ".join(vals) or "(none)"))
    if "markets_off" in body:
        # PER-USER market on/off (user 2026-07-11, Markets User switch) — hides those markets from THIS
        # user's Scanner/Pre-orders only.
        s = _wu.get_settings(name)
        s["markets_off"] = sorted({str(m).strip() for m in (body["markets_off"] or []) if str(m).strip()})
        _wu.set_settings(name, s)
        _wu.log_event(name, "Saved my hidden markets (Markets)")
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
        # GOLD-tier + a money path: these switches decide which monitor sources may place live IG orders
        # on the shared trading account. The Trading (Momentum) panel that owns them is hidden below Gold,
        # but hiding a panel is not authorisation — /api/config only requires a login, so before this
        # check ANY logged-in guest/silver could POST {"exec":...} and flip live trading (user 2026-07-17).
        # Admin is an access axis, not a subscription, so it does not substitute for Gold.
        if _wu.get_subscription(name) != "gold":
            return jsonify({"ok": False, "error": "Trading (Momentum) is a Gold-tier feature"}), 403
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

# Scanner RVOL (user 2026-07-17, P-30). Cached against the snapshot's generated_utc: a trigger and the
# volume on its bar are historical facts, so they only change when the snapshot is rebuilt.
_RVOL_CACHE = {"gen": None, "data": {}}


def _snapshot_rvol(snap: dict) -> dict:
    """{ticker: rvol} on each TRIGGERED setup's REAL break bar.

    Deliberately does NOT use the Scanner's own "Triggered" column: that is still a proxy (the last pivot
    date, computed client-side in augment() — see the open "HVF site: exact triggered date" backlog item).
    Averaging volume around the pivot instead of the break would quietly measure the wrong day, so this
    replays _perf_trigger_date over price_history exactly as the Performance report does."""
    gen = snap.get("generated_utc")
    if _RVOL_CACHE["gen"] == gen and _RVOL_CACHE["data"]:
        return _RVOL_CACHE["data"]
    out = {}
    try:
        want = []
        for r in snap.get("records", []):
            tk, e = r.get("ticker"), r.get("entry")
            ready = max([d for d in (r.get("h3_date"), r.get("l3_date")) if d], default=None)
            if r.get("status") == "TRIGGERED" and tk and e and ready:
                want.append((tk, _dt.date.fromisoformat(str(ready)[:10]), float(e), r.get("direction") == "BULL"))
        if want:
            from db_pool import get_db
            db = get_db()
            try:
                bars_by_tk = _perf_bars(db, {tk: rd for tk, rd, _e, _b in want},
                                        lookback_days=_RVOL_LOOKBACK_DAYS)
            finally:
                db.close()
            for tk, rd, e, bull in want:
                bars = bars_by_tk.get(tk, [])
                td = _perf_trigger_date(bull, e, rd, bars, None)
                if td:
                    v = _rvol_at(bars, td)
                    if v is not None:
                        out[tk] = v
    except Exception as ex:
        log.warning(f"scanner RVOL failed (column blank): {ex}")
    _RVOL_CACHE.update(gen=gen, data=out)
    return out


# VolumeScore (user 2026-07-24, ToDo P-02 L49): a 0–12 breakout-confirmation score computed on each
# TRIGGERED setup's real break bar, using the SAME bars and trigger-date replay as RVOL above. Cached
# against the snapshot's generated_utc for the same reason (the break and its volume are historical).
_VOLSCORE_CACHE = {"gen": None, "data": {}}
_VOLSCORE_LOOKBACK_DAYS = 160   # ~112 trading bars before the trigger — covers the 60-bar LVN profile + ATR window


def _snapshot_volscore(snap: dict) -> dict:
    """{ticker: volume_score_result_dict} on each TRIGGERED setup's real break bar. The full dict
    (score + per-component breakdown) is cached so /api/records reads the score and /api/volscore
    reads the breakdown without re-fetching bars. "Strong squeeze" is proxied from the funnel's
    Quality (>=60 = tight/fresh), the only squeeze-strength signal available server-side."""
    gen = snap.get("generated_utc")
    if _VOLSCORE_CACHE["gen"] == gen and _VOLSCORE_CACHE["data"]:
        return _VOLSCORE_CACHE["data"]
    out = {}
    try:
        import volume_score as _vscore
        want = []
        for r in snap.get("records", []):
            tk, e = r.get("ticker"), r.get("entry")
            ready = max([d for d in (r.get("h3_date"), r.get("l3_date")) if d], default=None)
            if r.get("status") == "TRIGGERED" and tk and e and ready:
                want.append((tk, _dt.date.fromisoformat(str(ready)[:10]), float(e),
                             r.get("direction") == "BULL", r.get("quality")))
        if want:
            from db_pool import get_db
            db = get_db()
            try:
                bars_by_tk = _perf_bars(db, {tk: rd for tk, rd, _e, _b, _q in want},
                                        lookback_days=_VOLSCORE_LOOKBACK_DAYS)
            finally:
                db.close()
            for tk, rd, e, bull, q in want:
                bars = bars_by_tk.get(tk, [])
                td = _perf_trigger_date(bull, e, rd, bars, None)
                if td:
                    strong = (q is not None and q >= 60)
                    out[tk] = _vscore.volume_score(bars, td, bull, squeeze_strong=strong)
    except Exception as ex:
        log.warning(f"scanner VolumeScore failed (column blank): {ex}")
    _VOLSCORE_CACHE.update(gen=gen, data=out)
    return out


@app.route("/api/volscore/<ticker>")
def api_volscore(ticker):
    """VolumeScore breakdown for one triggered setup (user 2026-07-24, P-02). Logged-in only —
    it is a trading signal, not public teaser data."""
    if not _wu.name_for_token(request.headers.get("X-Auth") or ""):
        return jsonify({"error": "login required"}), 401
    res = _snapshot_volscore(_load_snapshot()).get(ticker)
    return jsonify({"ticker": ticker, "volscore": res})


@app.route("/api/records")
def api_records():
    snap = _load_snapshot()
    authed = request.headers.get("X-Auth") in _wu.valid_tokens()
    # The Scanner shows the FULL universe to every user — the Config trade filters gate only what the
    # operator TRADES (enforced in ig_shim at order time), never what is shown (user 2026-07-06).
    if authed:
        rvol = _snapshot_rvol(snap)                       # RVOL at the real break bar (P-30)
        vscore = _snapshot_volscore(snap)                 # VolumeScore 0–12 at the break bar (P-02 L49)
        recs = [dict({k: v for k, v in r.items() if k != "_card"}, rvol=rvol.get(r.get("ticker")),
                     volume_score=(vscore.get(r.get("ticker")) or {}).get("score"))
                for r in snap.get("records", [])]
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
    # Admin-only (user 2026-07-24, P-02): the per-rule PASS/FAIL justification is an internal scanner
    # diagnostic, not shared with non-admin subscribers. Frontend omits the card too.
    if not _wu.is_admin(_wu.name_for_token(request.headers.get("X-Auth") or "")):
        return jsonify({"error": "admin only"}), 403
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


_FUND_CACHE = {}   # ticker -> last SUCCESSFUL {currency, kpis}; survives Yahoo's transient quoteSummary 404s


@app.route("/api/fundamentals/<ticker>")
def api_fundamentals(ticker):
    """Company KPIs straight from yfinance .info (user 2026-06-28): P/E, FCF, dividends, margins, growth,
    leverage, etc. Live per-ticker; graceful (empty kpis) if Yahoo is unreachable. A good fetch is cached
    so a later transient 404 (e.g. GLEN.L — a FTSE 100 name whose data DOES exist; user 2026-07-24, P-03
    L138) serves the last-good KPIs marked stale rather than blanking the panel."""
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
    # Cache a good fetch; serve last-good (stale) when this one came back empty (P-03 L138).
    if any(v is not None for v in out.values()):
        _FUND_CACHE[ticker] = {"currency": cur, "kpis": out}
        return jsonify({"ticker": ticker, "currency": cur, "kpis": out, "stale": False})
    cached = _FUND_CACHE.get(ticker)
    if cached:
        return jsonify({"ticker": ticker, "currency": cached["currency"], "kpis": cached["kpis"], "stale": True})
    return jsonify({"ticker": ticker, "currency": cur, "kpis": out, "stale": False})


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
    # Also UNPIN any deleted ticker (user 2026-07-10 bug): a pinned pre-order stayed pinned, so
    # isPreorder() kept it in the list — the row never disappeared and returned on reload. Removing it
    # from the user's pins (and any level overrides) makes Delete actually delete it.
    try:
        s = _wu.get_settings(name)
        pins = [t for t in (s.get("pinned_preorders") or []) if t not in tickers]
        ov = {k: v for k, v in (s.get("pinned_overrides") or {}).items() if k not in tickers}
        if pins != (s.get("pinned_preorders") or []) or ov != (s.get("pinned_overrides") or {}):
            s["pinned_preorders"] = pins
            s["pinned_overrides"] = ov
            _wu.set_settings(name, s)
    except Exception as e:
        log.warning(f"preorder-delete unpin failed: {e}")
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
    _lb = _limit_block(name, tk, on=bool(body.get("on", True)))
    if _lb:
        return jsonify({"ok": False, "error": _lb}), 403
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
    _lb = _limit_block(name, tk)
    if _lb:
        return jsonify({"ok": False, "error": _lb, "placed": False}), 200
    sig = _sig_from_snapshot(tk, user=name)
    if not sig:
        return jsonify({"ok": False, "error": "no signal for that instrument"}), 400
    # Capture the log lines emitted DURING placement so a "not placed" (place_hvf_order_from_sig returns
    # None for many reasons — quality below floor, tight stop, direction conflict, trade filters, no epic
    # — each only log.info'd) reports the actual reason instead of a useless "unknown" (user 2026-07-10).
    import logging as _logging
    _cap = []
    _sigtk = str(sig.get("ticker") or tk)

    class _CapH(_logging.Handler):
        def emit(self, rec):
            try:
                m = rec.getMessage()
                if _sigtk in m or tk in m:
                    _cap.append(m)
            except Exception:
                pass

    _h = _CapH(); _h.setLevel(_logging.INFO)
    _root = _logging.getLogger(); _root.addHandler(_h)
    try:
        from run_session import get_user_profile
        import ig_shim
        # Place using the ACTING user's OWN IG session (user 2026-07-03): owner -> env creds; a
        # non-owner -> their own credentials (or blocked above). The swap is serialised under _IG_LOCK.
        with ig_shim.acting_session(name):
            wo = ig_shim.place_hvf_order_from_sig(sig, get_user_profile(), "WEB_MANUAL", 1.0)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or f"{tk}: IG rejected the order"}), 500
    finally:
        _root.removeHandler(_h)
    _wu.log_event(name, f"Manually placed order: {tk}" + (f" ({wo.get('status')})" if wo else " (not placed)"))
    _append_batch("Manual (web)", f"Placed {tk} order", by=name)
    if not wo:
        reason = (_cap[-1].split(": ", 1)[-1] if _cap else
                  "IG did not accept the order — likely the pattern Quality is below the floor, the stop "
                  "is too tight, the direction conflicts, or a circuit breaker (daily loss / max positions "
                  "/ spread) blocked it. See System Logs for detail.")
        return jsonify({"ok": False, "error": reason, "placed": False}), 200
    return jsonify({"ok": True, "status": wo.get("status"), "placed": True})


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


# ── Accurate performance report (user 2026-07-13) ─────────────────────────────────────────────────────
# Built from the RECORDED trigger events (hvf_triggers: entry/stop/target/dates captured the moment each
# setup fired — point-in-time, immune to the mutable snapshot) and classified against price_history from
# the trigger date forward: STOPPED (price hit the stop first), TARGET (hit the target first), or OPEN
# (neither yet — marked to the freshest price). That trigger date is DERIVED from price
# (_perf_trigger_date), not taken from recorded_at — recorded_at is only when a scan first noticed the
# break, which for a late-added market is weeks late (user 2026-07-17, P-01). Entry/stop/target AND
# price_history are both in the instrument's native (Yahoo) scale, so no unit mismatch. One row per
# recorded funnel instance, so the
# same ticker can appear several times (successive triggers). Cached briefly — it walks 100k+ price bars.
_PERF_CACHE = {"ts": 0.0, "data": None}
_PERF_TTL = 300   # seconds


# Intraday high/low carry bad ticks (Yahoo): e.g. LSEG.L 2026-06-18 printed a 10130 high on an 8338
# close — a 21% wick that would trip a stop price never really reached. When a bar's high (or low)
# deviates from its OWN close by more than this fraction, treat it as a wick and fall back to the close
# for level-touch tests. A close that is itself beyond the level still counts — that is a real breach,
# not a wick (user 2026-07-17, P-01 LSEG bug B).
_BAD_TICK_CAP = 0.15


def _perf_exit(bull: bool, entry: float, stop: float, target: float, bars: list):
    """bars: chronological (high, low, close) STRICTLY AFTER the trigger bar. Entry is the trigger bar's
    CLOSE, so the trade can only be stopped/targeted from the NEXT bar on — the trigger bar's own intraday
    range must never stop it before entry (user 2026-07-17, P-01 LSEG bug A). Returns (state, return_pct)
    where state is STOPPED / TARGET / None (None = no exit hit yet -> caller marks OPEN to market)."""
    for hi, lo, cl in bars:
        if hi is None or lo is None:
            continue
        # Reject implausible intraday wicks: fall back to the close when high/low is too far from it.
        if cl is not None:
            hi_eff = hi if hi <= cl * (1 + _BAD_TICK_CAP) else cl
            lo_eff = lo if lo >= cl * (1 - _BAD_TICK_CAP) else cl
        else:
            hi_eff, lo_eff = hi, lo
        hit_stop = (lo_eff <= stop) if bull else (hi_eff >= stop)
        hit_tgt = (hi_eff >= target) if bull else (lo_eff <= target)
        if hit_stop:   # same-bar tie resolves to the stop (worst case), standard backtest convention
            return "STOPPED", ((stop - entry) / entry * 100 if bull else (entry - stop) / entry * 100)
        if hit_tgt:
            return "TARGET", ((target - entry) / entry * 100 if bull else (entry - target) / entry * 100)
    return None, None


def _perf_trigger_date(bull: bool, entry: float, ready, bars: list, recorded):
    """The date the setup actually FIRED, derived from price rather than from when a scan noticed it.

    hvf_triggers.recorded_at is only the moment a scan first SAW the break, which is not the break: an
    instrument added to the universe late (the Euronext top-100 landed 2026-07-14) records weeks after
    it fired — RAND.AS broke its 26.28 entry on 07-01 but recorded_at says 07-14 (user 2026-07-17 P-01).

    So replay hvf_clean's own trigger rule (a CLOSE beyond the entry pivot, hvf_clean.py rule 4) over the
    bars from `ready` — the funnel's last pivot, before which the pattern did not yet exist, so an earlier
    break is not this setup's. No lookahead: every level is fixed by pivots dated on or before `ready`.
    Falls back to `recorded` when price_history cannot show the break (e.g. bars older than the store)."""
    for bd, _hi, _lo, cl, *_ in bars:
        if cl is None or (ready is not None and bd < ready):
            continue
        if (cl > entry) if bull else (cl < entry):
            return min(bd, recorded) if recorded else bd
    return recorded


# Relative volume at the trigger (user 2026-07-17, P-30): RVOL = the trigger bar's volume / the mean
# volume of the RVOL_BARS bars BEFORE it. The request said "last 20-50 bars"; 20 is the desk standard and
# the shortest in that range, so it reacts to the burst that accompanies a break rather than smoothing it
# away. The average EXCLUDES the trigger bar — including it would dilute the very spike being measured.
RVOL_BARS = 20
# Fetch this much history before each funnel's first pivot so the 20-bar average exists at the trigger.
# ~40 calendar days ≈ 28 trading days, comfortably more than RVOL_BARS.
_RVOL_LOOKBACK_DAYS = 40


def _rvol_at(bars: list, td) -> float:
    """RVOL on bar `td` from `bars` [(bar_date, high, low, close, volume), ...] ascending, or None.
    None (never 0) when there is no volume to speak of — FX and indices carry no real volume, and a
    fabricated 1.0 there would read as 'average participation' rather than 'not applicable'."""
    i = next((k for k, b in enumerate(bars) if b[0] == td), None)
    if i is None:
        return None
    vol = bars[i][4]
    prior = [b[4] for b in bars[max(0, i - RVOL_BARS):i] if b[4]]
    if not vol or len(prior) < 5:        # too few real bars to average against
        return None
    avg = sum(prior) / len(prior)
    return round(vol / avg, 2) if avg > 0 else None


def _perf_bars(db, cutoff: dict, lookback_days: int = 0) -> dict:
    """{ticker: [(bar_date, high, low, close, volume), ...]} for every ticker in `cutoff`
    ({ticker: from_date}), in ONE round trip. `lookback_days` widens each cutoff backwards (P-30 needs
    bars BEFORE the trigger to average against); the extra bars are harmless to the outcome walk, which
    filters to bar_date >= the trigger date anyway.

    This used to be a query PER TICKER inside the report loop. Supabase is remote, so 268 tickers meant
    268 sequential round-trips at ~66ms — ~18s of pure latency before the tab could paint, which is why
    a 348-row report felt slow (user 2026-07-17, P-17a): it was never the rendering. Joining against a
    VALUES list keeps each ticker's own cutoff (so we fetch exactly the same bars, not more) and costs
    one round-trip: measured 14,768 bars in 0.85s, ~21x faster."""
    items = [(tk, d0) for tk, d0 in cutoff.items() if d0]
    if not items:
        return {}
    back = _dt.timedelta(days=lookback_days) if lookback_days else _dt.timedelta(0)
    vals = ",".join(f"(:t{i}, :d{i}::date)" for i in range(len(items)))
    params = {}
    for i, (tk, d0) in enumerate(items):
        params[f"t{i}"] = tk
        params[f"d{i}"] = str(d0 - back)
    rows = db.run(
        f"select p.ticker, p.bar_date, p.high, p.low, p.close, p.volume from price_history p "
        f"join (values {vals}) as f(ticker, d0) on p.ticker = f.ticker and p.bar_date >= f.d0 "
        f"order by p.ticker, p.bar_date", **params) or []
    out = {}
    for tk, bd, hi, lo, cl, vol in rows:
        out.setdefault(tk, []).append((bd, hi, lo, cl, vol))
    return out


@app.route("/api/performance")
def api_performance():
    """Every tradeable trigger over the LAST 12 MONTHS with its levels and realised/open outcome. This is
    the SAME dataset as the "What separates the winners" tab (user 2026-07-18: the two must never diverge)
    — the 12-month squeeze_history replay via _sqa_all_rows (R:R>=3, FX/Crypto excluded, direction-aware
    marked-to-market return%), NOT the recent hvf_triggers. Public (client-side blur); cached."""
    now = _time.time()
    if _PERF_CACHE["data"] is not None and now - _PERF_CACHE["ts"] < _PERF_TTL:
        return jsonify(_PERF_CACHE["data"])
    out = []
    try:
        import datetime as _dt
        cut12 = (_dt.date.today() - _dt.timedelta(days=365)).isoformat()
        def _days_open(r):   # days the trade was/has been active (user 2026-07-25, P-05 L99)
            td = r.get("trig_date")
            if not td:
                return None
            try:
                d0 = _dt.date.fromisoformat(str(td)[:10])
                d1 = _dt.date.fromisoformat(str(r.get("exit_date"))[:10]) if r.get("exit_date") else _dt.date.today()
                return max(0, (d1 - d0).days)
            except Exception:
                return None
        for r in _sqa_all_rows():
            if (r.get("trig_date") or "") < cut12:
                continue
            out.append({
                "ticker": r["ticker"], "name": r["name"],
                "market": r["market"], "sector": r["sector"], "location": r["location"],
                "direction": ("BULL" if r["direction"] == "BULLISH" else "BEAR"), "timeframe": r["timeframe"],
                "quality": r["quality"], "rr": r["rr"],
                "entry": r["entry"], "stop": r["stop"], "target": r["target"],
                "current_price": r["current_price"],
                "trig_date": r["trig_date"], "state": r["outcome"],
                "days_open": _days_open(r),
                "perf": (round(r["return_pct"], 2) if r["return_pct"] is not None else None),
                "rvol": r["rvol"]})
        out.sort(key=lambda r: (r.get("perf") is None, -(r.get("perf") or 0)))
    except Exception as ex:
        log.warning(f"performance report failed: {ex}")
    payload = {"rows": out, "generated": _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime())}
    _PERF_CACHE.update(ts=now, data=payload)
    return jsonify(payload)


# ── Squeeze analysis (user 2026-07-17, P-21b) ────────────────────────────────────────────────────────
# "Do some analysis of all 15 month price squeezes and give advice on how to filter out the best options
# e.g. location, sector, R:R." Reads squeeze_history — the 15-month engine replay (squeeze_history.py) —
# because hvf_triggers only starts 2026-06-30 and is far too small a sample to draw a conclusion from.
#
# The population here is EVERY funnel the engine would have seen, including ones the live filters reject
# (quality floor, R:R floor, the 1.5% bridge band). That is the point: you cannot learn which filter
# helps by looking only at setups that already passed the filters.
#
# Win rate counts RESOLVED funnels only (TARGET vs STOPPED). OPEN ones have not finished, and counting
# them would flatter whichever bucket happens to hold the most unfinished trades. NEVER_TRIGGERED are
# excluded from the win rate but reported, because "never fired" is a cost of a filter, not a loss.
_SQA_CACHE = {"ts": 0.0, "data": None}
_SQA_TTL = 600
_SQA_MIN_N = 10          # below this a bucket is reported but never called good or bad


def _sqa_sector(ticker: str):
    """Cached sector for any ticker (user 2026-07-17) — so the squeeze analysis has a sector for funnels
    whose ticker has no current signal, not just the ~250 in today's snapshot with one."""
    try:
        import sector_cache
        return sector_cache.get_sector(ticker)
    except Exception:
        return None


# Methodology RECONCILED to the code that already defines these (user 2026-07-18 — a QA correction).
#   Win / loss / avg — the Performance report's _pfSeg (hvf_web/index.html): a GAIN is a marked-to-market
#     return above +PF_BE, a LOSS below -PF_BE, and BOTH counts include OPEN trades. Win% = gains / every
#     trade that has a return (not target-hits / closed-only, which is a different, contradictory number).
#   £ impact & compounding — calculate_position_size (ig_shim.py): each trade risks a FIXED fraction of
#     the wallet (risk_per_trade, 2%), size = risk_amount / stop_distance. So a stop loses exactly that
#     fraction and NOTHING blows up. A trade's wallet impact = risk% x R-multiple, where the R-multiple is
#     return% / stop-distance% (STOPPED = -1R, TARGET = +R:R, open = partial).
_SQA_BE = 0.5             # |return| <= this = break-even (matches PF_BE in _pfSeg)
_SQA_RISK = 0.02          # risk per trade (calculate_position_size default risk_per_trade)


def _sqa_bridge_min_quality() -> float:
    try:
        import config_store as _cs
        return float(_cs.get_value("bridge_min_quality", 50))
    except Exception:
        return 50.0


def _sqa_seg(rows):
    """Segment stats for a set of funnels, using the SAME definitions as the Performance report's _pfSeg,
    plus the 2%-risk £ impact. `rows` may include OPEN (marked-to-market) funnels — they count, exactly as
    on the Performance report."""
    ps = [r for r in rows if r.get("return_pct") is not None]      # 'Returns Available' (incl. OPEN)
    if not ps:
        return {"funnels": len(rows), "avail": 0, "gains": 0, "losses": 0, "be": 0, "wins": 0,
                "losses_n": 0, "win_pct": None, "loss_pct": None, "avg_return": None,
                "max": None, "min": None, "pnl_per_10k": None, "enough": False}
    rets = [r["return_pct"] for r in ps]
    gains = sum(1 for p in rets if p > _SQA_BE)
    losses = sum(1 for p in rets if p < -_SQA_BE)
    be = len(ps) - gains - losses
    rms = [r["r_mult"] for r in ps if r.get("r_mult") is not None]
    # £ P&L on a £10,000 wallet for one trade: risk 2% (£200) x the trade's R-multiple, averaged.
    pnl10k = round(10000 * _SQA_RISK * (sum(rms) / len(rms))) if rms else None
    return {
        "funnels": len(rows), "avail": len(ps),
        "gains": gains, "losses": losses, "be": be,
        "wins": gains, "losses_n": losses,     # aliases the UI already reads
        "win_pct": round(gains / len(ps) * 100, 1),
        "loss_pct": round(losses / len(ps) * 100, 1),
        "avg_return": round(sum(rets) / len(rets), 2),
        "max": round(max(rets), 1), "min": round(min(rets), 1),
        "pnl_per_10k": pnl10k,
        "enough": len(ps) >= _SQA_MIN_N,
    }


# Max concurrent open positions in the compound simulation. RISK-BASED, not the bridge's per-pass cap
# (user 2026-07-18): each trade risks 2%, so 50 open at once = 100% of equity at risk — the absolute
# ceiling before you are over-committed. (A prudent book runs lower "heat", ~10-15 positions / 20-30%,
# so a cluster of correlated stops can't wipe it — worth revisiting, but 50 is the defensible maximum.)
_SQA_MAX_CONCURRENT = 50


def _sqa_compound(rows, start=10000.0, max_concurrent=_SQA_MAX_CONCURRENT):
    """Compounded wallet as a PORTFOLIO SIMULATION, not a sequential product (user 2026-07-18). Sequential
    compounding of thousands of overlapping trades is degenerate — with a positive per-trade edge it
    explodes regardless of risk size, because the count, not the stake, drives it. Real trades run in
    parallel and are capped, so this simulates that:

      * population = BRIDGE-TRADEABLE resolved funnels only (quality >= the bridge floor, R:R >= 3) — the
        setups the 2-hourly bridge would actually have placed;
      * at most `max_concurrent` positions open at once (the bridge's own per-pass cap); a setup that
        arrives with every slot full is SKIPPED, exactly as the live cap would skip it;
      * each position risks 2% of the wallet AT THE MOMENT IT OPENS (calculate_position_size) and realises
        risked£ x R-multiple when it closes. A stop loses its 2%, nothing more — it cannot blow up.

    Returns {final wallet, trades taken, skipped, max_concurrent, start, ledger}. `ledger` is every taken
    trade in the order it CLOSED (the moment the wallet changes), each carrying the running wallet after it —
    so the headline £ can be audited line by line and reconciles exactly to `final` (user 2026-07-18)."""
    import heapq, itertools
    minq = _sqa_bridge_min_quality()
    seq = sorted((r for r in rows
                  if r["outcome"] in ("TARGET", "STOPPED")
                  and r.get("r_mult") is not None and r.get("trig_date") and r.get("exit_date")
                  and (r.get("quality") or 0) >= minq and (r.get("rr") or 0) >= 3),
                 key=lambda r: r["trig_date"])
    if not seq:
        return None
    wallet = float(start)
    taken = skipped = 0
    ledger = []
    _seq = itertools.count()           # tie-breaker so heap never compares dict payloads
    open_pos = []                      # heap of (exit_date, seq, risked_gbp, r_mult, trade)
    def _close(item):
        nonlocal wallet
        _ed, _s, risked, rm, tr = item
        before = wallet
        wallet = max(0.0, wallet + risked * rm)
        ledger.append({"ticker": tr["ticker"], "market": tr["market"], "sector": tr["sector"],
                       "direction": tr["direction"], "quality": tr["quality"], "rr": tr["rr"],
                       "return_pct": tr["return_pct"], "outcome": tr["outcome"],
                       "r_mult": round(rm, 2), "trig_date": tr["trig_date"], "exit_date": _ed,
                       "wallet_before": round(before), "risked": round(risked, 2),
                       "pnl": round(risked * rm, 2), "wallet_after": round(wallet)})
    for t in seq:
        td = t["trig_date"]
        while open_pos and open_pos[0][0] <= td:        # close anything that has matured
            _close(heapq.heappop(open_pos))
        if len(open_pos) < max_concurrent:
            heapq.heappush(open_pos, (t["exit_date"], next(_seq), wallet * _SQA_RISK, t["r_mult"], t))
            taken += 1
        else:
            skipped += 1
    while open_pos:                                     # close the book at the end
        _close(heapq.heappop(open_pos))
    return {"final": round(wallet), "start": round(start), "gain": round(wallet - start),
            "trades": taken, "skipped": skipped, "max_concurrent": max_concurrent, "ledger": ledger}


def _sqa_buckets(rows, keyfn, label):
    out = []
    seen = {}
    for r in rows:
        k = keyfn(r)
        if k is None:
            continue
        seen.setdefault(str(k), []).append(r)
    for k, rs in seen.items():
        seg = _sqa_seg(rs)
        never = sum(1 for r in rs if r["outcome"] == "NEVER_TRIGGERED")
        out.append({"dimension": label, "bucket": k, "never_triggered": never,
                    "resolved": seg["avail"], **seg})   # 'resolved' kept as the sample-size the UI shows
    return sorted(out, key=lambda b: (b["win_pct"] is None, -(b["win_pct"] or 0)))


def _sqa_band(v, edges, fmt="{}–{}"):
    if v is None:
        return None
    for lo, hi in zip(edges, edges[1:]):
        if lo <= v < hi:
            return fmt.format(lo, hi)
    return f"{edges[-1]}+" if v >= edges[-1] else None


_SQA_ROWS = {"ts": 0.0, "rows": None}


def _sqa_all_rows():
    """The full bridge-eligible population (ready + R:R>=3), built once and cached — the query + sector
    lookups are the slow part. Cherry-pick presets filter this in Python, so they cost nothing."""
    now = _time.time()
    if _SQA_ROWS["rows"] is not None and now - _SQA_ROWS["ts"] < _SQA_TTL:
        return _SQA_ROWS["rows"]
    snap = {r["ticker"]: r for r in _load_snapshot().get("records", []) if r.get("ticker")}
    # Only markets the account ACTUALLY TRADES (user 2026-07-18: "FX is showing very profitable and we
    # are not trading FX"). Use the trade allow-list — the same gate ig_shim.trade_allowed enforces — so
    # the analysis population matches what would really have been placed. Empty list = allow all.
    try:
        import config_store as _cs
        allowed = set(_cs.get_trade_filters().get("markets") or [])
    except Exception:
        allowed = set()
    from db_pool import get_db
    db = get_db()
    try:
        raw = db.run(
            "select ticker, market, timeframe, hvf_type, quality, risk_reward, rvol, "
            "outcome, return_pct, triggered_date, outcome_date, entry_level, stop_level, target_level "
            "from squeeze_history where ready_date is not null and risk_reward >= 3") or []
    finally:
        db.close()
    rows = []
    for tk, mk, tf, ht, q, rr, rv, oc, ret, td, od, e, s_, t_ in raw:
        s = snap.get(tk, {})
        market = mk or s.get("market")
        if allowed and market not in allowed:      # not a traded market — exclude (FX, Crypto, ...)
            continue
        ret = float(ret) if ret is not None else None
        # R-multiple = the trade's return in units of what it RISKED (return% / stop-distance%): a stop is
        # -1R, a target +R:R (user 2026-07-18, matching calculate_position_size).
        r_mult = None
        if ret is not None and e and s_:
            sd = abs(float(e) - float(s_)) / float(e) * 100.0
            if sd > 0:
                r_mult = ret / sd
        rows.append({"ticker": tk, "name": s.get("name") or tk, "market": market,
                     "sector": _sqa_sector(tk) or s.get("sector"),
                     "location": s.get("location"), "timeframe": tf, "direction": ht,
                     "quality": (float(q) if q is not None else None),
                     "rr": (float(rr) if rr is not None else None),
                     "rvol": (float(rv) if rv is not None else None),
                     "entry": (float(e) if e is not None else None),
                     "stop": (float(s_) if s_ is not None else None),
                     "target": (float(t_) if t_ is not None else None),
                     "current_price": s.get("current_price"),
                     "trig_date": str(td) if td else None, "exit_date": str(od) if od else None,
                     "r_mult": r_mult, "outcome": oc, "return_pct": ret})
    # NOW-price fallback (user 2026-07-24, P-03 BUG "multiple rows with blank NOW"): the snapshot only
    # covers the CURRENT universe, so a squeeze_history ticker no longer scanned had current_price=None
    # and showed a blank NOW. Fill those from the latest stored close in price_history — one query for
    # all the missing tickers.
    missing = sorted({r["ticker"] for r in rows if r.get("current_price") is None})
    if missing:
        try:
            from db_pool import get_db
            db = get_db()
            try:
                ph = ",".join(f":m{i}" for i in range(len(missing)))
                params = {f"m{i}": tk for i, tk in enumerate(missing)}
                last = {tk: (float(cl) if cl is not None else None)
                        for tk, cl in (db.run(
                            f"select distinct on (ticker) ticker, close from price_history "
                            f"where ticker in ({ph}) order by ticker, bar_date desc", **params) or [])}
            finally:
                db.close()
            for r in rows:
                if r.get("current_price") is None and last.get(r["ticker"]) is not None:
                    r["current_price"] = last[r["ticker"]]
        except Exception as ex:
            log.warning(f"NOW-price fallback failed: {ex}")
    _SQA_ROWS.update(ts=now, rows=rows)
    return rows


def _sqa_num(name):
    try:
        v = request.args.get(name)
        return float(v) if v not in (None, "") else None
    except Exception:
        return None


@app.route("/api/squeeze-analysis")
def api_squeeze_analysis():
    """Which attributes actually separate the winners, over the 15-month replayed population. Optional
    cherry-pick filters (query params rvmin, qmin, rrmin) narrow the population, so a preset shows its own
    win rate / expectancy / compounded £ (user 2026-07-18)."""
    now = _time.time()
    rvmin, qmin, rrmin = _sqa_num("rvmin"), _sqa_num("qmin"), _sqa_num("rrmin")
    conc = _sqa_num("conc")                     # concurrency dial (user 2026-07-18)
    conc = int(conc) if conc and conc >= 1 else _SQA_MAX_CONCURRENT
    ckey = (rvmin, qmin, rrmin, conc)
    if _SQA_CACHE.get("key") == ckey and _SQA_CACHE["data"] is not None and now - _SQA_CACHE["ts"] < _SQA_TTL:
        return jsonify(_SQA_CACHE["data"])
    payload = {"rows": 0, "baseline": None, "dimensions": [], "advice": [],
               "filter": {"rvmin": rvmin, "qmin": qmin, "rrmin": rrmin},
               "generated": _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime())}
    try:
        rows = _sqa_all_rows()
        # Cherry-pick: null attribute is EXCLUDED when a min is set (you can't select on what you can't
        # measure — e.g. RVOL>1.8 must drop no-RVOL FX/index funnels).
        if rvmin is not None:
            rows = [r for r in rows if r["rvol"] is not None and r["rvol"] > rvmin]
        if qmin is not None:
            rows = [r for r in rows if r["quality"] is not None and r["quality"] >= qmin]
        if rrmin is not None:
            rows = [r for r in rows if r["rr"] is not None and r["rr"] >= rrmin]
        # Summary figures use the LAST 12 MONTHS (user 2026-07-18: "for summary it should be 12 months");
        # the per-trade detail table below keeps the full 15-month replay for reference.
        import datetime as _dt
        cut12 = (_dt.date.today() - _dt.timedelta(days=365)).isoformat()
        rows12 = [r for r in rows if (r.get("trig_date") or "") >= cut12]
        base = _sqa_seg(rows12)
        payload["rows"] = len(rows12)
        payload["summary_months"] = 12
        payload["detail_months"] = 15
        payload["detail_funnels"] = len(rows)          # full 15-month population (for the reference note)
        payload["baseline"] = {**base,
                               "resolved": base["avail"],
                               "trades_per_year": base["avail"],   # 12-month window => resolved count IS the annual rate
                               "compound_10k": _sqa_compound(rows12, max_concurrent=conc),   # dial (P-18)
                               "never_triggered": sum(1 for r in rows12 if r["outcome"] == "NEVER_TRIGGERED"),
                               "open": sum(1 for r in rows12 if r["outcome"] == "OPEN")}
        # Every trade behind the compound number is carried in baseline.compound_10k.ledger (built by
        # _sqa_compound, in close order with the running wallet) so the headline £ can be audited line by
        # line and reconciles exactly to `final` (user 2026-07-18: "show the wallet grow after each trade").
        dims = [
            ("Location", lambda r: r["location"]),
            ("Sector", lambda r: r["sector"]),
            ("Market", lambda r: r["market"]),
            ("Direction", lambda r: r["direction"]),
            ("Timeframe", lambda r: r["timeframe"]),
            ("R:R", lambda r: _sqa_band(r["rr"], [0, 3, 5, 8, 12, 20])),
            ("Quality", lambda r: _sqa_band(r["quality"], [0, 30, 40, 50, 60, 70])),
            ("RVOL at trigger", lambda r: _sqa_band(r["rvol"], [0, 0.8, 1.0, 1.4, 1.8, 2.5])),
        ]
        for label, fn in dims:
            payload["dimensions"].append({"name": label, "buckets": _sqa_buckets(rows12, fn, label)})
        # Advice = buckets with a real sample that beat the baseline win rate by >= 10 points.
        base_win = base.get("win_pct")
        if base_win is not None:
            for d in payload["dimensions"]:
                for b in d["buckets"]:
                    if b["enough"] and b["win_pct"] is not None and b["win_pct"] - base_win >= 10:
                        payload["advice"].append({**b, "lift": round(b["win_pct"] - base_win, 1)})
            payload["advice"].sort(key=lambda a: -a["lift"])
    except Exception as ex:
        log.warning(f"squeeze analysis failed: {ex}")
    _SQA_CACHE.update(ts=now, data=payload, key=ckey)
    return jsonify(payload)


def _sl_path(direction, entry, stop, target, bars, thr):
    """Re-walk one trade's daily OHLC bars applying the trailing stop each bar (user 2026-07-18,
    illustration-only). Same hit convention as squeeze_history._exit_outcome — stop wins a same-bar tie;
    the stop trails on the bar CLOSE (avoids intra-bar whipsaw). Returns (outcome, exit_price)."""
    import ig_shim
    buy = direction == "BULLISH"
    cur = stop
    last = None
    for _bd, hi, lo, cl in bars:
        last = cl
        if buy:
            if lo <= cur:
                return "STOPPED", cur
            if target and hi >= target:
                return "TARGET", target
        else:
            if hi >= cur:
                return "STOPPED", cur
            if target and lo <= target:
                return "TARGET", target
        ns = ig_shim.compute_trailing_stop(direction, entry, cur, cl, thr)   # thr is a fraction
        if ns is not None:
            cur = ns
    if last is None:
        return "OPEN", None
    return "OPEN", last


# VolumeScore impact report (user 2026-07-24, ToDo P-02 L55). Uses the SAME 12-month replay population as
# the Performance Results / "What separates the winners" tabs (_sqa_all_rows) — never the small hvf_triggers
# set — so the numbers reconcile with those tabs. Scores every trade's break bar, then shows how filtering
# on VolumeScore changes win rate, average return and the compounded £, and where the profit concentrates.
_VSR_CACHE = {"ts": 0.0, "data": None}


def _volscore_report():
    import datetime as _dt
    import volume_score as _vscore
    now = _time.time()
    if _VSR_CACHE["data"] is not None and now - _VSR_CACHE["ts"] < _SQA_TTL:
        return _VSR_CACHE["data"]
    cut12 = (_dt.date.today() - _dt.timedelta(days=365)).isoformat()
    rows = [r for r in _sqa_all_rows()
            if (r.get("trig_date") or "") >= cut12 and r.get("trig_date") and r.get("entry")]
    # One bar cutoff per ticker = its EARLIEST trigger minus the lookback, so a ticker with several trades
    # is fetched once and each trade finds its own break bar in the shared list.
    cut = {}
    for r in rows:
        td = _dt.date.fromisoformat(str(r["trig_date"])[:10])
        cut[r["ticker"]] = min(cut.get(r["ticker"], td), td)
    bars_by = {}
    if cut:
        from db_pool import get_db
        db = get_db()
        try:
            bars_by = _perf_bars(db, cut, lookback_days=_VOLSCORE_LOOKBACK_DAYS)
        finally:
            db.close()
    scored = []
    for r in rows:
        bars = bars_by.get(r["ticker"], [])
        td = _dt.date.fromisoformat(str(r["trig_date"])[:10])
        # Snap to a real bar date if the recorded trigger fell on a non-trading day.
        bdates = [b[0] for b in bars]
        if td not in bdates:
            later = [d for d in bdates if d >= td]
            td = later[0] if later else (bdates[-1] if bdates else td)
        bull = r["direction"] == "BULLISH"
        strong = (r.get("quality") is not None and r["quality"] >= 60)
        res = _vscore.volume_score(bars, td, bull, squeeze_strong=strong)
        rr = dict(r)
        rr["volume_score"] = res["score"]
        scored.append(rr)

    def _band(v):
        if v is None:
            return None
        return "0–4" if v < 5 else "5–7" if v < 8 else "8–9" if v < 10 else "10–12"

    buckets = _sqa_buckets(scored, lambda r: _band(r.get("volume_score")), "VolumeScore")
    order = {"0–4": 0, "5–7": 1, "8–9": 2, "10–12": 3}
    buckets.sort(key=lambda b: order.get(b["bucket"], 9))

    passing = [r for r in scored if (r.get("volume_score") or 0) >= _vscore.PASS_THRESHOLD]
    seg_all, seg_pass = _sqa_seg(scored), _sqa_seg(passing)
    comp_all, comp_pass = _sqa_compound(scored), _sqa_compound(passing)

    # Plain-English takeaways, derived (not hand-written) so they track the data.
    advice = []
    if seg_all.get("avail") and seg_pass.get("avail"):
        d_win = (seg_pass["win_pct"] or 0) - (seg_all["win_pct"] or 0)
        d_ret = (seg_pass["avg_return"] or 0) - (seg_all["avg_return"] or 0)
        advice.append(
            f"Filtering to VolumeScore ≥ {_vscore.PASS_THRESHOLD} keeps {seg_pass['avail']} of "
            f"{seg_all['avail']} resolved trades ({round(seg_pass['avail'] / seg_all['avail'] * 100)}%), "
            f"moving win rate {('+' if d_win >= 0 else '')}{round(d_win, 1)} pts "
            f"({seg_all['win_pct']}% → {seg_pass['win_pct']}%) and average return "
            f"{('+' if d_ret >= 0 else '')}{round(d_ret, 1)} pts "
            f"({seg_all['avg_return']}% → {seg_pass['avg_return']}%).")
    best = max((b for b in buckets if b.get("enough")), key=lambda b: (b["avg_return"] or -999), default=None)
    if best:
        advice.append(f"The most profitable band is VolumeScore {best['bucket']}: "
                      f"{best['win_pct']}% win, avg return {best['avg_return']}% over {best['resolved']} trades.")
    if comp_all and comp_pass:
        advice.append(f"On the £{comp_all['start']:,} concurrency-capped simulation, the unfiltered book ends "
                      f"£{comp_all['final']:,} ({comp_all['trades']} trades); the ≥{_vscore.PASS_THRESHOLD} book ends "
                      f"£{comp_pass['final']:,} ({comp_pass['trades']} trades).")

    data = {
        "generated": _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime()),
        "threshold": _vscore.PASS_THRESHOLD, "max": _vscore.MAX_SCORE,
        "n": len(scored),
        "all": seg_all, "passing": seg_pass,
        "compound_all": comp_all, "compound_passing": comp_pass,
        "buckets": buckets, "advice": advice,
    }
    _VSR_CACHE.update(ts=now, data=data)
    return data


@app.route("/api/volscore-report")
def api_volscore_report():
    """VolumeScore impact over the 12-month replay (user 2026-07-24, P-02). Admin only — sits on the
    admin 'What separates the winners' analysis tab alongside the other replayed-population reports."""
    if not _wu.is_admin(_wu.name_for_token(request.headers.get("X-Auth") or "")):
        return jsonify({"error": "admin only"}), 403
    try:
        return jsonify(_volscore_report())
    except Exception as ex:
        log.warning(f"volscore report failed: {ex}")
        return jsonify({"error": "report unavailable"}), 500


# Best settings by quarter (user 2026-07-24 P-04 L59; 2026-07-25 do 3yr not 5yr). Over the AVAILABLE
# replayed population (_sqa_all_rows — up to price retention, 3yr; currently ~15mo), for each calendar
# quarter find the best Market / Quality band / R:R band by average return. Auto-extends as data grows.
_BEST_CACHE = {"ts": 0.0, "data": None}


def _best_bucket(rows, keyfn, min_n=3):
    """The bucket (by keyfn) with the highest average return, among buckets with >= min_n resolved trades."""
    from collections import defaultdict
    g = defaultdict(list)
    for r in rows:
        k = keyfn(r)
        if k is not None and r.get("return_pct") is not None:
            g[k].append(r["return_pct"])
    best = None
    for k, rets in g.items():
        if len(rets) < min_n:
            continue
        avg = sum(rets) / len(rets)
        if best is None or avg > best["avg"]:
            best = {"value": k, "n": len(rets), "avg": round(avg, 1)}
    return best


def _best_settings():
    now = _time.time()
    if _BEST_CACHE["data"] is not None and now - _BEST_CACHE["ts"] < _SQA_TTL:
        return _BEST_CACHE["data"]
    from collections import defaultdict
    rows = [r for r in _sqa_all_rows() if r.get("trig_date") and r.get("return_pct") is not None]
    q = defaultdict(list)
    for r in rows:
        d = str(r["trig_date"])
        try:
            yr, mo = int(d[:4]), int(d[5:7])
            q[f"{yr} Q{(mo - 1) // 3 + 1}"].append(r)
        except Exception:
            pass
    quarters = []
    for qk in sorted(q, reverse=True):                 # newest quarter first
        rs = q[qk]
        seg = _sqa_seg(rs)
        quarters.append({
            "quarter": qk, "trades": seg["avail"], "win_pct": seg["win_pct"], "avg_return": seg["avg_return"],
            "best_market": _best_bucket(rs, lambda r: r.get("market")),
            "best_quality": _best_bucket(rs, lambda r: _sqa_band(r.get("quality"), [0, 30, 40, 50, 60, 70])),
            "best_rr": _best_bucket(rs, lambda r: _sqa_band(r.get("rr"), [0, 3, 5, 8, 12, 20])),
        })
    span = f"{min(q) if q else '—'} → {max(q) if q else '—'}"
    data = {"generated": _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime()),
            "quarters": quarters, "span": span, "n": len(rows),
            "note": "Over the available replayed population (up to 3-year price retention; extends as data grows)."}
    _BEST_CACHE.update(ts=now, data=data)
    return data


@app.route("/api/best-settings")
def api_best_settings():
    """Best Market / Quality / R:R per quarter over the available data (user 2026-07-25, P-04 L59).
    Admin only — sits on the analysis tab with the other replayed-population reports."""
    if not _wu.is_admin(_wu.name_for_token(request.headers.get("X-Auth") or "")):
        return jsonify({"error": "admin only"}), 403
    try:
        return jsonify(_best_settings())
    except Exception as ex:
        log.warning(f"best-settings report failed: {ex}")
        return jsonify({"error": "report unavailable"}), 500


_SLBARS = {"ts": 0.0, "by_tk": None}


def _winners_sl_rows(threshold_pct):
    """Every 12-month tradeable trade with BOTH its plain return% and the return%/outcome it WOULD have had
    with the trailing stop applied (re-backtest). threshold_pct=0 => the two are identical (reconciliation)."""
    import datetime as _dt
    thr = (float(threshold_pct or 0) or 0) / 100.0
    cut12 = (_dt.date.today() - _dt.timedelta(days=365)).isoformat()
    rows = sorted((r for r in _sqa_all_rows() if (r.get("trig_date") or "") >= cut12),
                  key=lambda r: (r.get("trig_date") or ""))
    by_tk = None
    if thr > 0:
        # Load the bars we need once, keyed by ticker (cached 10 min). Only when the feature is on.
        now = _time.time()
        if _SLBARS["by_tk"] is not None and now - _SLBARS["ts"] < _SQA_TTL:
            by_tk = _SLBARS["by_tk"]
        else:
            # One bulk query for every bar in the window (all trades trigger within the last 12 months, so
            # their forward paths live in [cut12, now]) — 1000+ per-ticker round-trips were ~80s.
            by_tk = {}
            from db_pool import get_db
            db = get_db()
            try:
                raw = db.run("select ticker, bar_date, high, low, close from price_history "
                             "where bar_date >= :d order by ticker, bar_date", d=cut12) or []
            finally:
                db.close()
            for tk, bd, hi, lo, cl in raw:
                if hi is None or lo is None or cl is None:
                    continue
                by_tk.setdefault(tk, []).append((str(bd), float(hi), float(lo), float(cl)))
            _SLBARS.update(ts=now, by_tk=by_tk)
    out = []
    for r in rows:
        plain = r["return_pct"]
        sl_perf, sl_out = plain, r["outcome"]
        if thr > 0 and r.get("entry") and r.get("stop") and r.get("trig_date"):
            td = r["trig_date"]
            bars = [b for b in by_tk.get(r["ticker"], []) if b[0] >= td]
            if bars:
                sl_out, ex = _sl_path(r["direction"], r["entry"], r["stop"], r.get("target"), bars, thr)
                if ex is not None:
                    buy = r["direction"] == "BULLISH"
                    sl_perf = round(((ex - r["entry"]) / r["entry"] * 100.0) if buy
                                    else ((r["entry"] - ex) / r["entry"] * 100.0), 2)
        out.append({"ticker": r["ticker"], "name": r["name"], "market": r["market"], "sector": r["sector"],
                    "location": r["location"], "direction": ("BULL" if r["direction"] == "BULLISH" else "BEAR"),
                    "trig_date": r["trig_date"], "entry": r["entry"], "stop": r["stop"],
                    "outcome": r["outcome"], "perf": plain,
                    "sl_outcome": sl_out, "sl_perf": sl_perf,
                    "quality": r["quality"], "rr": r["rr"], "rvol": r["rvol"]})
    return out


@app.route("/api/winners-sl")
def api_winners_sl():
    """Winners rows re-backtested with the trailing stop at the requested threshold % (query `sl`, else the
    configured stop_amend_threshold). Illustration-only — never touches live stops (user 2026-07-18)."""
    try:
        sl = request.args.get("sl")
        if sl in (None, ""):
            import config_store as _cs
            sl = _cs.get_value("stop_amend_threshold", "0")
        rows = _winners_sl_rows(sl)
        return jsonify({"rows": rows, "threshold_pct": float(sl or 0), "months": 12,
                        "generated": _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime())})
    except Exception as ex:
        log.warning(f"winners-sl failed: {ex}")
        return jsonify({"rows": [], "threshold_pct": 0, "months": 12})


@app.route("/api/squeeze-history")
def api_squeeze_history():
    """Lifecycle history of squeeze funnels (user 2026-07-18, Squeeze History (Admin) tab): each funnel's
    developing → ready → triggered → outcome journey, replayed over price history. Newest first."""
    payload = {"rows": [], "generated": _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime())}
    try:
        snap = {r["ticker"]: r for r in _load_snapshot().get("records", []) if r.get("ticker")}
        from db_pool import get_db
        db = get_db()
        try:
            raw = db.run(
                "select ticker, market, timeframe, hvf_type, first_seen, first_signal, ready_date, "
                "triggered_date, outcome, outcome_date, return_pct, quality, risk_reward "
                "from squeeze_history "
                "order by coalesce(triggered_date, ready_date, first_seen) desc nulls last") or []   # no row cap (user 2026-07-18, P-01)
        finally:
            db.close()
        for (tk, mk, tf, ht, fseen, fsig, rd, td, oc, od, ret, q, rr) in raw:
            s = snap.get(tk, {})
            payload["rows"].append({
                "ticker": tk, "name": s.get("name") or tk, "market": mk or s.get("market"),
                "location": s.get("location"),   # for the Location chart (user 2026-07-18, P-01)
                "sector": _sqa_sector(tk) or s.get("sector"),
                "direction": ("BULL" if ht == "BULLISH" else "BEAR"), "timeframe": tf,
                "first_seen": (str(fseen) if fseen else None), "first_signal": fsig,
                "ready_date": (str(rd) if rd else None), "triggered_date": (str(td) if td else None),
                "outcome": oc, "outcome_date": (str(od) if od else None),
                "return_pct": (round(ret, 2) if ret is not None else None),
                "quality": (round(q) if q is not None else None),
                "rr": (round(rr, 1) if rr is not None else None)})
    except Exception as ex:
        log.warning(f"squeeze history failed: {ex}")
    return jsonify(payload)


@app.route("/api/fees")
def api_fees():
    """Fees (Admin) tab (user 2026-07-18): management fee (1%/mo of AUM) + performance fee (10%/mo of
    profits), with a worked example from LAST MONTH's realised P&L (daily_pnl)."""
    import datetime as _dt
    payload = {"mgmt_pct": 1.0, "perf_pct": 10.0, "last_month": None,
               "generated": _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime())}
    try:
        today = _dt.date.today()
        first_this = today.replace(day=1)
        last_month_end = first_this - _dt.timedelta(days=1)
        first_last = last_month_end.replace(day=1)
        from db_pool import get_db
        db = get_db()
        try:
            row = db.run("select coalesce(sum(total_pnl),0), coalesce(sum(trade_count),0), "
                         "coalesce(sum(win_count),0), coalesce(sum(loss_count),0) from daily_pnl "
                         "where trade_date >= :a and trade_date <= :b",
                         a=first_last.isoformat(), b=last_month_end.isoformat()) or [(0, 0, 0, 0)]
            pnl, tc, wc, lc = row[0]
        finally:
            db.close()
        payload["last_month"] = {"label": first_last.strftime("%B %Y"),
                                 "pnl": float(pnl or 0), "trades": int(tc or 0),
                                 "wins": int(wc or 0), "losses": int(lc or 0)}
    except Exception as ex:
        log.warning(f"fees failed: {ex}")
    return jsonify(payload)


@app.route("/api/winners")
def api_winners():
    """Raw per-trade rows for the "What separates the winners" tab (user 2026-07-18): the FULL last-12-months
    tradeable population (squeeze_history replay, R:R>=3, FX/Crypto excluded), each with its direction-aware
    return% — the SAME definition the Performance report uses. The frontend applies a 2%-of-the-running-wallet
    stake to compound the £. Chronological so the wallet can be built oldest-first."""
    import datetime as _dt
    payload = {"rows": [], "months": 12, "generated": _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime())}
    try:
        cut12 = (_dt.date.today() - _dt.timedelta(days=365)).isoformat()
        rows = [r for r in _sqa_all_rows() if (r.get("trig_date") or "") >= cut12]
        rows.sort(key=lambda r: (r.get("trig_date") or ""))
        payload["rows"] = [
            {"ticker": r["ticker"], "name": r["name"], "market": r["market"], "sector": r["sector"],
             "location": r["location"], "direction": ("BULL" if r["direction"] == "BULLISH" else "BEAR"),
             "trig_date": r["trig_date"], "exit_date": r.get("exit_date"), "entry": r["entry"], "stop": r["stop"],
             "outcome": r["outcome"], "perf": r["return_pct"],
             "quality": r["quality"], "rr": r["rr"], "rvol": r["rvol"]}
            for r in rows]
    except Exception as ex:
        log.warning(f"winners rows failed: {ex}")
    return jsonify(payload)


_CR_DIR = os.path.join(_REPO_ROOT, "ChangeRequests")
# Status a requirement line can carry (user 2026-07-10, Change Requests tab). A line is Completed/In
# Progress/Cancelled/Requested when it ends with a bracketed marker (e.g. "[Completed]") or carries a
# short leading tag ([x] done, [~] wip, [-] cancelled, [?] requested); otherwise it is Not Started.
# The marker may be followed by a short parenthetical note (user 2026-07-17) — e.g.
# "[Completed]  (superseded by P-11a)" or "[In Progress]  (finishing last 264 tickers)". Without the
# optional `(...)$` this was end-anchored, so any noted line silently read as Not Started.
_CR_TAIL = _re.compile(
    r"\[(completed|in[\s-]?progress|not[\s-]?started|cancelled|canceled|requested|deferred)\]\s*(?:\([^)]*\)\s*)?$",
    _re.I)
_CR_LEAD = {"[x]": "Completed", "[X]": "Completed", "[~]": "In Progress",
            "[-]": "Cancelled", "[?]": "Requested"}


# A requirement is PRIORITISED when it carries a P-number tag ("P-01 - ...", "P-16a - ...") or sits under
# an "Explicitly prioritised work" heading (user 2026-07-17, P-22) — the two ways priority is marked in
# these files. Derived, not stored: the tag IS the prioritisation, so there is nothing to keep in sync.
_CR_PRIO_TAG = _re.compile(r"^P-\d+[a-z]?\b", _re.I)


def _cr_prioritised(text: str, area: str) -> bool:
    if _CR_PRIO_TAG.match((text or "").strip()):
        return True
    a = (area or "").lower()
    return "prioritis" in a or "prioritiz" in a


# Per-requirement Scope = its category word (user 2026-07-25, P-05 L310) — Data/Format/Content/BUG/etc.,
# so the Scope column is populated like Working Area instead of mostly blank. Falls back to the heading
# scope ("NEW DELIVERY") when the line has no category.
_CR_CATEGORY = _re.compile(r"^P-\d+[a-z]?\s+(BUG|Format|Data|Content|Default|Filter|Query)\b", _re.I)
_CR_PNUM = _re.compile(r"^P-0*(\d+)", _re.I)


def _cr_scope(req: str, heading_scope: str) -> str:
    m = _CR_CATEGORY.match((req or "").strip())
    return (m.group(1).title() if m else "") or heading_scope


def _cr_prange(req: str):
    """Priority range bucket for the file-level summary (user 2026-07-25, P-05 L311)."""
    m = _CR_PNUM.match((req or "").strip())
    if not m:
        return None
    n = int(m.group(1))
    return "P01-05" if n <= 5 else "P06-10" if n <= 10 else "P11-25" if n <= 25 else "P26+"


def _cr_status(line: str) -> str:
    s = line.strip()
    for tag, st in _CR_LEAD.items():
        if s.startswith("* " + tag) or s.startswith(tag):
            return st
    m = _CR_TAIL.search(s)
    if m:
        v = m.group(1).lower().replace("-", " ").replace("inprogress", "in progress")
        return {"completed": "Completed", "in progress": "In Progress", "not started": "Not Started",
                "cancelled": "Cancelled", "canceled": "Cancelled", "requested": "Requested",
                "deferred": "Deferred"}.get(v, "Not Started")
    return "Not Started"


def _cr_parse(path: str) -> dict:
    """Parse one ChangeRequests/*.txt into a summary + requirement list. A requirement is any line whose
    trimmed form starts with '*'. The nearest preceding 'Application Focus - X' header is its Working
    Area; a '- NEW DELIVERY' suffix on that header is the Scope."""
    fn = os.path.basename(path)
    stem = fn[:-4] if fn.lower().endswith(".txt") else fn      # drop .txt
    name = stem.replace("-Claude", "").replace("-claude", "")  # drop -Claude (user 2026-07-10)
    created = ""
    _m = _re.match(r"(\d{4})(\d{2})(\d{2})", stem)             # YYYYMMDD filename prefix -> Date Created
    if _m:
        created = f"{_m.group(1)}-{_m.group(2)}-{_m.group(3)}"
    try:
        updated = _time.strftime("%Y-%m-%d", _time.localtime(os.path.getmtime(path)))
    except Exception:
        updated = ""
    area, scope, reqs = "", "", []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except Exception:
        lines = []
    for raw in lines:
        s = raw.strip()
        if s.startswith("Application Focus"):
            a = s.split("-", 1)[1].strip() if "-" in s else s
            if a.upper().endswith("NEW DELIVERY"):
                scope = "NEW DELIVERY"
                a = a[:-len("NEW DELIVERY")].rstrip(" -").strip()
            else:
                scope = ""
            area = a
            continue
        if s.startswith("*"):
            text = s.lstrip("* ").strip()
            text = _CR_TAIL.sub("", text).strip()
            for tag in _CR_LEAD:
                if text.startswith(tag):
                    text = text[len(tag):].strip()
            if not text:
                continue
            # Split the requirement from any "-- …" delivery note (user 2026-07-25, P-02 L305) so the tab
            # shows them in separate columns instead of one cluttered Requirement cell. " -- " is the
            # delimiter the notes are written with; the "(Claude …)" older inline form stays with the text.
            req, _sep, note = text.partition(" -- ")
            reqs.append({"row": len(reqs) + 1,   # stable 1-based number so "#26" maps to a line (user 2026-07-18)
                         "text": req.strip(), "delivery_notes": note.strip(),
                         "working_area": area, "scope": _cr_scope(req, scope), "status": _cr_status(raw),
                         "prange": _cr_prange(req), "prioritised": _cr_prioritised(req, area)})
    counts = {"Completed": 0, "In Progress": 0, "Not Started": 0, "Cancelled": 0, "Requested": 0, "Deferred": 0}
    pranges = {"P01-05": 0, "P06-10": 0, "P11-25": 0, "P26+": 0}
    for r in reqs:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if r.get("prange"):
            pranges[r["prange"]] += 1
    return {"name": name, "file": fn, "created": created, "updated": updated,
            "total": len(reqs), "counts": counts, "pranges": pranges, "requirements": reqs,
            "prioritised": sum(1 for r in reqs if r["prioritised"])}


@app.route("/api/change-requests")
def api_change_requests():
    """List (and optionally detail one of) the ChangeRequests/*.txt files (user 2026-07-10). Admin only.
    Without ?file= returns a summary row per file (totals + status counts + created/updated); with
    ?file=<filename> returns that file's parsed requirements and raw text."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    if not _wu.is_admin(name):
        return jsonify({"error": "admin only"}), 403
    try:
        files = sorted(f for f in os.listdir(_CR_DIR) if f.lower().endswith(".txt"))
    except Exception as e:
        log.warning(f"change-requests dir unreadable: {e}")
        return jsonify({"files": []})
    want = request.args.get("file")
    if want:
        want = os.path.basename(want)   # never traverse outside the dir
        if want not in files:
            return jsonify({"error": "not found"}), 404
        path = os.path.join(_CR_DIR, want)
        d = _cr_parse(path)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                d["raw"] = f.read()
        except Exception:
            d["raw"] = ""
        return jsonify(d)
    out = []
    for f in files:
        try:
            out.append({k: v for k, v in _cr_parse(os.path.join(_CR_DIR, f)).items() if k != "requirements"})
        except Exception as e:
            log.warning(f"change-request parse failed for {f}: {e}")
    out.sort(key=lambda r: (r.get("created") or "", r.get("file") or ""), reverse=True)
    return jsonify({"files": out})


@app.route("/api/ig-account")
def api_ig_account():
    """The acting user's OWN IG account (user 2026-07-10, IG Account tab): open positions + working
    orders. Best-effort — returns a note (not an error) when the user has no IG session, so the page
    always renders. The order 'source' (AUS_MONITOR / Squeeze / WEB_MANUAL …) comes from the DB
    working_orders.session matched by epic."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    out = {"positions": [], "orders": [], "note": ""}
    try:
        import ig_shim
        if ig_shim.session_for(name) is None:
            out["note"] = "No IG credentials of your own — set them in Configuration → IG to see your account."
            return jsonify(out)
    except Exception as e:
        out["note"] = f"IG session unavailable: {e}"
        return jsonify(out)
    epic2tk, epic2src = {}, {}
    try:
        from db_pool import get_db
        db = get_db()
        try:
            for row in (db.run("select ticker, epic from epic_lookup") or []):
                if row[1]:
                    epic2tk[str(row[1])] = row[0]
            for row in (db.run("select epic, session from working_orders where status = 'PENDING'") or []):
                if row[0]:
                    epic2src[str(row[0])] = row[1]
        finally:
            db.close()
    except Exception as e:
        log.warning(f"ig-account DB maps failed: {e}")
    # Company name per ticker from the snapshot (user 2026-07-13: show the instrument NAME, not just
    # the ticker/epic, on the IG Account report).
    tk2name = {r["ticker"]: (r.get("name") or "")
               for r in _load_snapshot().get("records", []) if r.get("ticker")}

    # Fetch positions and orders INDEPENDENTLY (user 2026-07-13 bug: a failure in get_working_orders
    # used to abort the whole block, so open positions silently vanished even though that call had
    # already succeeded). Each call now stands on its own; one failing never hides the other.
    positions, orders, acct_info = [], [], {}
    pos_ok = ord_ok = False
    try:
        with ig_shim._IG_LOCK, ig_shim.acting_session(name):
            try:
                positions = ig_shim.get_open_positions() or []
                pos_ok = True
            except Exception as e:
                log.warning(f"ig-account positions read failed for {name}: {e}")
            try:
                orders = ig_shim.get_working_orders() or []
                ord_ok = True
            except Exception as e:
                log.warning(f"ig-account orders read failed for {name}: {e}")
            try:   # account name + id for the header (user 2026-07-20); id is masked before it leaves here
                acct_info = ig_shim.get_account_info() or {}
            except Exception as e:
                log.warning(f"ig-account info read failed for {name}: {e}")
    except Exception as e:
        log.warning(f"ig-account session unavailable for {name}: {e}")
        out["note"] = "Could not read your IG account right now — try Refresh."
        return jsonify(out)
    if not pos_ok and not ord_ok:
        out["note"] = "Could not read your IG account right now — try Refresh."
        return jsonify(out)
    if not pos_ok:
        out["note"] = "Open positions are unavailable right now — try Refresh."
    elif not ord_ok:
        out["note"] = "Working orders are unavailable right now — try Refresh."

    def _tk(epic, mk):   # short ticker for an epic (epic_lookup, else IG's instrument name, else epic)
        return epic2tk.get(str(epic)) or (mk.get("instrumentName") if mk else None) or epic or ""

    for p in positions:
        pos, mk = (p.get("position") or {}), (p.get("market") or {})
        epic = mk.get("epic")
        tk = _tk(epic, mk)
        out["positions"].append({
            "ticker": tk, "name": tk2name.get(tk) or mk.get("instrumentName") or tk,
            "epic": epic, "direction": pos.get("direction"),
            "size": pos.get("size"), "level": pos.get("level") or pos.get("openLevel"),
            "currency": pos.get("currency"), "stop": pos.get("stopLevel"), "limit": pos.get("limitLevel"),
            "opened": str(pos.get("createdDateUTC") or pos.get("createdDate") or "")[:19]})
    for w in orders:
        od, mk = (w.get("workingOrderData") or {}), (w.get("marketData") or {})
        epic = od.get("epic") or mk.get("epic")
        tk = _tk(epic, mk)
        out["orders"].append({
            "ticker": tk, "name": tk2name.get(tk) or mk.get("instrumentName") or tk,
            "epic": epic,
            "direction": od.get("direction"), "size": od.get("orderSize") or od.get("size"),
            "level": od.get("orderLevel") or od.get("level"), "type": od.get("orderType"),
            "good_till": str(od.get("goodTillDate") or "")[:19], "source": epic2src.get(str(epic)) or "—"})
    # Account name + OBFUSCATED number for the header (user 2026-07-20). The raw account id is masked here
    # so the full number never reaches the browser — only the last 3 chars survive.
    aid = acct_info.get("account_id") or ""
    out["account_name"] = acct_info.get("account_name") or ""
    out["account_masked"] = ("••••" + aid[-3:]) if aid else ""
    return jsonify(out)


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
