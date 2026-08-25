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
# Live site: https://www.squeezescanner.cloud/ (IONOS — see IONOS_DEPLOYMENT.md). Running this module
# directly starts a LOCAL DEVELOPMENT instance only; the laptop + ngrok public share was retired 2026-08-15.
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
import math
import numbers

from flask import Flask, jsonify, send_file, request, Response
from flask.json.provider import DefaultJSONProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("hvf_web.server")

_HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(_HERE, "snapshot.json")

app = Flask(__name__)
_PNG_CACHE: dict = {}
_X_HANDLE = "SqueezeSignals"   # our X account (config.py / publish_one_to_x X_HANDLE)


def _json_safe(value):
    """Convert non-finite numeric values to JSON null before browser responses are emitted."""
    # NumPy scalar values (used by the replay calculations) are not always instances of built-in
    # float, so normalise scalar wrappers before checking finiteness.
    item = getattr(value, "item", None)
    if callable(item) and not isinstance(value, (dict, list, tuple)):
        try:
            value = item()
        except Exception:
            pass
    if isinstance(value, numbers.Real):
        try:
            if not math.isfinite(float(value)):
                return None
        except (TypeError, ValueError, OverflowError):
            return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    return value


class _StrictJSONProvider(DefaultJSONProvider):
    """Apply the non-finite-number contract to every Flask JSON response."""

    def dumps(self, obj, **kwargs):
        kwargs["allow_nan"] = False
        return super().dumps(_json_safe(obj), **kwargs)


app.json = _StrictJSONProvider(app)

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
    # Brute-force protection (2026-08-18, SECURITY_RECOMMENDATIONS HIGH #1). This endpoint previously
    # accepted unlimited guesses. The check runs BEFORE verify() on purpose: a locked-out attacker must
    # not reach PBKDF2, both to deny them the answer and because verification is deliberately expensive
    # and would otherwise make this endpoint a CPU amplifier.
    import login_throttle
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    allowed, retry_after = login_throttle.check(ip, name)
    if not allowed:
        _wu.log_event(name, f"Login BLOCKED — too many attempts (from {ip})")
        return jsonify({"ok": False, "error": "Too many attempts. Try again shortly.",
                        "retry_after": retry_after}), 429
    if _wu.verify(name, pwd):
        login_throttle.record_success(ip, name)
        _wu.log_event(name, f"Logged in (from {ip})")
        return jsonify({"ok": True, "token": _wu.token_for(name), "name": name})
    login_throttle.record_failure(ip, name)
    return jsonify({"ok": False}), 401


_DATA_DIR = os.path.join(os.path.dirname(_HERE), "data")
_VERSION_FILE = os.path.join(_DATA_DIR, "version_history.json")
_BATCH_FILE = os.path.join(_DATA_DIR, "batch_activity.json")
_IG_CLOSE_AUDIT_FILE = os.path.join(_DATA_DIR, "ig_close_audit.jsonl")

# Identity of the build this PROCESS loaded, captured at import time (see /api/build). Read once on
# purpose: re-reading on request would report the file on disk, and the disk is not what is stale.
def _read_build_id() -> dict:
    try:
        with open(os.path.join(_DATA_DIR, "build_id.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return {"fingerprint": str(data.get("fingerprint") or ""), "built_at": str(data.get("built_at") or "")}
    except (OSError, json.JSONDecodeError, AttributeError):
        return {"fingerprint": "", "built_at": ""}


_BUILD_ID = _read_build_id()
_MODULE_LOADED_AT = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
_IG_CLOSE_AUDIT_LOCK = _threading.Lock()


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


def _append_ig_close_audit(user: str, deal_id: str, phase: str, detail: str = "") -> None:
    """Durable host-side evidence for a live close attempt, independent of Supabase availability."""
    from datetime import datetime, timezone
    entry = {"at": datetime.now(timezone.utc).isoformat(), "user": str(user),
             "deal_id": str(deal_id), "phase": str(phase), "detail": str(detail or "")[:500]}
    try:
        with _IG_CLOSE_AUDIT_LOCK:
            os.makedirs(_DATA_DIR, exist_ok=True)
            with open(_IG_CLOSE_AUDIT_FILE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
    except OSError as exc:
        log.error("Could not persist IG close audit for %s: %s", deal_id, exc)


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
    Supabase then file fallback for IONOS deployments without .git. Categorised. Entries before the
    project start are hidden."""
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
        log.warning(f"version history from git failed ({e}); using Supabase/file fallback")
        remote = None
        try:
            import web_store
            remote = web_store.load_json_store("version_history")
        except Exception as store_ex:
            log.warning(f"version history Supabase fallback failed: {store_ex}")
        stored = (remote or {}).get("entries", []) if isinstance(remote, dict) else []
        packaged = _read_json_entries(_VERSION_FILE)
        # Take whichever fallback is FRESHER, not simply the Supabase one (user 2026-08-23: "version
        # history - latest entry 18/8"). The Supabase copy is a one-off seed: migrate_runtime_state_to_
        # supabase.py wrote it once and nothing has updated it since, so it sat at 958 entries ending
        # 2026-08-18. It was consulted first and the file was only read "if not source", so once that seed
        # existed the file could never be reached — and the file is the one generated from git at package
        # time, i.e. the actual commits in the build being deployed. Comparing newest entry keeps this
        # correct whichever way round they happen to be.
        newest = lambda rows: max((r.get("date") or "" for r in rows), default="")
        source = packaged if newest(packaged) > newest(stored) else stored
        if not source:
            source = packaged or stored
        entries = [e2 for e2 in source if (e2.get("date") or "") > _VERSION_FLOOR]
        for e2 in entries:
            e2.setdefault("category", _version_category(e2.get("summary", "")))
    return entries


@app.route("/api/scheduled-jobs")
def api_scheduled_jobs():
    """Scheduled-job definitions + GitHub Actions run stats (user 2026-07-06, admin Scheduled Jobs tab).
    ?refresh=1 bypasses the 30-min cache."""
    _n = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not (_wu.is_admin(_n) or _wu.is_support(_n)):
        return jsonify({"error": "admin only"}), 403
    from hvf_web import scheduled_jobs as _sj
    return jsonify(_sj.get_jobs(force=(request.args.get("refresh") == "1")))


@app.route("/api/system-logs")
def api_system_logs():
    """System health + recent server log records (user 2026-07-04, admin System Logs tab)."""
    _n = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not (_wu.is_admin(_n) or _wu.is_support(_n)):
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
    _n = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not (_wu.is_admin(_n) or _wu.is_support(_n)):
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
        return jsonify({"name": None, "subscription": "guest", "is_admin": False, "is_support": False})
    return jsonify({"name": name, "subscription": _wu.get_subscription(name), "is_admin": _wu.is_admin(name),
                    "is_support": _wu.is_support(name)})


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
        users = _wu.list_users()
        # has_ig drives the "Set temp password" affordance (user 2026-08-08): it is offered only for
        # accounts with NO IG credentials, since an IG-linked account can place real trades.
        for u in users:
            u["has_ig"] = _user_has_ig_creds(u["name"])
        return jsonify({"users": users, "subscriptions": _wu.SUBSCRIPTIONS,
                        "requests": _wu.list_requests()})
    body = request.get_json(silent=True) or {}
    target = (body.get("name") or "").strip()
    if not target:
        return jsonify({"ok": False, "error": "no user"}), 400
    # Admin-initiated direct account creation (user 2026-08-07, User Management "Add user") — no
    # self-service request needed. New account is LOCKED; the user sets their password via the
    # email-gated reset flow, so we fire that email immediately (best-effort, never blocks creation).
    if body.get("action") == "create":
        email = (body.get("email") or "").strip()
        ok = _wu.admin_create_user(target, email, body.get("subscription") or "guest",
                                   bool(body.get("admin")), bool(body.get("support")))
        if ok:
            _wu.log_event(name, f"Created account: {target} ({body.get('subscription') or 'guest'}"
                                 f"{', admin' if body.get('admin') else ''}"
                                 f"{', support' if body.get('support') else ''})")
            try:
                _wu.request_reset_code(target, email)
            except Exception:
                pass
        return jsonify({"ok": ok, "error": None if ok else "name already taken or invalid email",
                        "users": _wu.list_users(), "requests": _wu.list_requests()})
    # Approve / reject a pending account request.
    if body.get("action") == "approve":
        ok = _wu.approve_request(target)
        if ok:
            _wu.log_event(name, f"Approved account request: {target}")
            # Notify the new user the same way "+ Add user" does (user 2026-08-08): fire a password-setup
            # email to their registered address so they know the account exists and can set a password.
            # Best-effort — an email failure never undoes the approval.
            try:
                _wu.request_reset_code(target, _wu.email_for(target))
            except Exception:
                pass
        return jsonify({"ok": ok, "users": _wu.list_users(), "requests": _wu.list_requests()})
    if body.get("action") == "reject":
        _wu.reject_request(target)
        _wu.log_event(name, f"Rejected account request: {target}")
        return jsonify({"ok": True, "users": _wu.list_users(), "requests": _wu.list_requests()})
    # Admin sets a temporary password directly (user 2026-08-08) — for use when outbound email is not
    # configured, so a locked account can be activated out-of-band. Refused for any account that holds
    # IG credentials (an IG-linked login can place real trades; it must use the email-based reset). The
    # route is already admin-only, so only admins reach this branch.
    if body.get("action") == "set_temp_password":
        if _user_has_ig_creds(target):
            return jsonify({"ok": False, "error": "This account has IG credentials — a temporary "
                            "password cannot be set for it. It must use the email-based reset."}), 400
        new_pwd = (body.get("new_pwd") or "").strip()
        ok = _wu.reset_password(target, _wu.email_for(target), new_pwd, ip=request.remote_addr or "")
        if ok:
            _wu.log_event(name, f"Set a temporary password for {target}")
        return jsonify({"ok": ok, "error": None if ok else "Could not set the password. It must be at "
                        "least 4 characters and the account must exist.",
                        "users": _wu.list_users()})
    changed = []
    if "subscription" in body and _wu.set_subscription(target, body["subscription"]):
        changed.append(f"subscription={body['subscription']}")
    if "admin" in body and target != name and _wu.set_admin(target, bool(body["admin"])):
        # guard: an admin can't remove their own admin and lock themselves out of maintenance
        changed.append(f"admin={bool(body['admin'])}")
    if "support" in body and _wu.set_support(target, bool(body["support"])):
        changed.append(f"support={bool(body['support'])}")
    if "enabled" in body and target != name and _wu.set_enabled(target, bool(body["enabled"])):
        changed.append(f"enabled={bool(body['enabled'])}")
    # Per-user fee discount (user 2026-08-02, P-20/P-40) — mgmt/perf % + optional start/end dates.
    if "fee_discount" in body and isinstance(body["fee_discount"], dict):
        fd = body["fee_discount"]
        if _wu.set_fee_discount(target, fd.get("mgmt_pct"), fd.get("perf_pct"),
                                (str(fd.get("start") or "").strip() or None),
                                (str(fd.get("end") or "").strip() or None), by=name):
            changed.append(f"fee discount mgmt={fd.get('mgmt_pct') or 0}% perf={fd.get('perf_pct') or 0}%")
    if changed:
        _wu.log_event(name, f"User maintenance: {target} → {', '.join(changed)}")
    return jsonify({"ok": True, "changed": changed, "users": _wu.list_users()})


@app.route("/api/ig-account-audit")
def api_ig_account_audit():
    """Admin-only (user 2026-08-03, P-25): the encrypted IG-account-identity audit trail for one user —
    decrypted account NAME + MASKED number (last-3) + source/by/timestamp, newest first. The full account
    number is never returned to the browser; it stays encrypted in Supabase."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    if not _wu.is_admin(name):
        return jsonify({"error": "admin only"}), 403
    target = (request.args.get("user") or "").strip()
    if not target:
        return jsonify({"error": "no user"}), 400
    return jsonify({"user": target, "audit": _wu.get_ig_account_audit(target)})


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


def _wo_lifespan_baseline() -> int:
    """Baseline IG working-order lifespan (days) for a user who hasn't set their own (user 2026-08-01):
    the shared app_config value if present, else the code default 28."""
    try:
        import config_store as _cs
        v = _cs.get_value("wo_lifespan_days", "")
        return int(float(v)) if v else 28
    except Exception:
        return 28


def _preorder_threshold_baseline() -> float:
    """Baseline Pre-order-to-IG proximity band (%) for a user who hasn't set their own (user 2026-08-03,
    P-75): the shared app_config value if present, else the engine default (ig_shim.WO_PROXIMITY_PCT, 1.5)."""
    try:
        import config_store as _cs
        v = _cs.get_value("wo_proximity_pct", "")
        if v:
            return float(v)
    except Exception:
        pass
    try:
        import ig_shim
        return float(getattr(ig_shim, "WO_PROXIMITY_PCT", 1.5))
    except Exception:
        return 1.5


def _limit_defaults() -> dict:
    """Code defaults for a user's PERSONAL trading limits (user 2026-07-10), sourced from config.py so
    they track the shared engine's baseline. Per-user overrides layer on top."""
    import config as _cfg
    base = {"min_risk_reward": float(getattr(_cfg, "MIN_RISK_REWARD", 3.0)),
            "min_quality": int(getattr(_cfg, "MIN_PUBLISH_QUALITY", 25)),
            "min_trade": float(getattr(_cfg, "MIN_TRADE", 25)),
            "min_volume_score": int(getattr(_cfg, "MIN_VOLUME_SCORE", 1)),   # personal VolumeScore floor (user 2026-07-27, P-03) — default 1
            "min_rvol": float(getattr(_cfg, "MIN_RVOL", 0)),
            "require_above_vwap": int(getattr(_cfg, "REQUIRE_ABOVE_VWAP", 0)),
            "require_atr_expanding": int(getattr(_cfg, "REQUIRE_ATR_EXPANDING", 0)),
            # Min/Max tradeable instrument VALUE (user 2026-07-27, P-07) — for equities the value is market
            # cap; 0 = off. Enforced (like the Quality floor) once records carry an `mcap` field; until that
            # data pipeline lands the gate is a graceful no-op, so the variable exists and is honoured now.
            "min_instrument_value": float(getattr(_cfg, "MIN_INSTRUMENT_VALUE", 0)),
            "max_instrument_value": float(getattr(_cfg, "MAX_INSTRUMENT_VALUE", 0)),
            # Adaptive filters (user 2026-07-27, P-07): when on, the user's Market/Quality/R:R filters are
            # re-tuned from the recent best-settings analysis every `rebalance_weeks`. The walk-forward
            # engine that applies it is the separate (deferred) L58 task; this is the configuration knob.
            "adaptive_filters": 0,  # disabled pending a completed walk-forward implementation
            "rebalance_weeks": int(getattr(_cfg, "REBALANCE_WEEKS", 4)),
            # "What separates the winners" model variables saved as the user's defaults (user 2026-07-28,
            # P-10 L158) — Wallet £, Max position size %, Max open positions — so the winners tab remembers them.
            "wallet": float(getattr(_cfg, "MODEL_WALLET", 10000)),   # default £10,000 (user 2026-08-01)
            "max_position_pct": float(getattr(_cfg, "MODEL_STAKE_PCT", 2)),
            "max_open": int(getattr(_cfg, "MODEL_MAX_OPEN", 0)),
            "max_trades_per_instrument_per_day": int(getattr(_cfg, "MAX_TRADES_PER_INSTRUMENT_PER_DAY", 5)),
            # IG working-order lifespan is now PER-USER (user 2026-08-01) — default from the shared app_config
            # (or the code baseline 28). The engine's own default still applies to the automated bridge.
            "wo_lifespan_days": _wo_lifespan_baseline(),
            # Pre-order-to-IG proximity band is now PER-USER (user 2026-08-03, P-75) — how close price must
            # be to the entry (%) before the bridge places a live IG order (further away = queued WATCHING).
            # Default from the shared app_config (or the engine baseline 1.5). The automated bridge keeps the
            # shared default; a WATCHING row records the band it was queued under so it promotes consistently.
            "preorder_threshold_pct": _preorder_threshold_baseline(),
            # "Let winners run" (user 2026-08-02): per-user opt-in for the winners-run illustration. OFF by
            # default (0) — the report only renders when the user turns it on; the trail % is their chosen
            # ratchet above target. Report-only (never touches live orders).
            "let_winners_run": int(getattr(_cfg, "LET_WINNERS_RUN", 0)),
            "let_winners_run_trail": int(getattr(_cfg, "LET_WINNERS_RUN_TRAIL", 25)),
            "let_winners_run_stop": int(getattr(_cfg, "LET_WINNERS_RUN_STOP", 0)),   # pre-target stop-loss trail % (0=hard stop)
            "bounce_alert_pct": float(getattr(_cfg, "BOUNCE_ALERT_PCT", 0.02)),
            "bounce_lookback_hours": int(getattr(_cfg, "BOUNCE_LOOKBACK_HOURS", 48)),
            "email_recipients": list(getattr(_cfg, "EMAIL_RECIPIENTS", []))}
    # Defaults come from the code baseline ONLY (user 2026-08-01: "all user settings must be unique to
    # the user - none must be shared"). A user who hasn't picked their own limits falls back to config.py,
    # NOT to another user's (previously Alex's) saved limits — no setting is inherited between users.
    return base


def _user_limits(s: dict) -> dict:
    d = _limit_defaults()
    d.update({k: v for k, v in (s.get("limits") or {}).items() if k in d})
    return d


def _limit_block(name: str, tk: str, on: bool = True) -> str:
    """If the acting user's personal R:R / Quality floor excludes this setup, return a reason; else ''.
    Gates the user's OWN manual actions (/api/preorder-pin, /api/place-order).

    2026-08-11 (user, P-verify): the automated engine (order_bridge.py + run_session.py/intraday_signals.py,
    via ig_shim.place_hvf_order_from_sig) now ALSO enforces these same personal floors for the account
    owner — see trading_limits.py, the shared module this function delegates to so both paths use one
    rulebook. This function keeps its own web-facing wording ("your personal floor ... Configuration →
    My trading limits") since it's shown directly to the user in the UI; trading_limits.check_limits()
    returns a plainer engine-facing reason used only in logs."""
    if not on:
        return ""
    rec = _record(tk) or {}
    import trading_limits
    lim = trading_limits.user_limits(name)
    rr, q = rec.get("rr"), rec.get("quality")
    if isinstance(rr, (int, float)) and rr < lim["min_risk_reward"]:
        return f"R:R {rr} is below your personal floor of {lim['min_risk_reward']:g} (Configuration → My trading limits)"
    if isinstance(q, (int, float)) and q < lim["min_quality"]:
        return f"Quality {q} is below your personal floor of {lim['min_quality']} (Configuration → My trading limits)"
    vs = rec.get("volume_score")
    if isinstance(vs, (int, float)) and vs < lim.get("min_volume_score", 1):
        return f"VolumeScore {vs} is below your personal floor of {lim['min_volume_score']} (Configuration → My trading limits)"
    rvol = rec.get("rvol")
    if isinstance(rvol, (int, float)) and lim.get("min_rvol", 0) > 0 and rvol < lim["min_rvol"]:
        return f"RVOL {rvol:g} is below your personal floor of {lim['min_rvol']:g} (Configuration → My trading limits)"
    if lim.get("require_above_vwap") and rec.get("above_vwap") is False:
        return "Price is below VWAP (Configuration → My trading limits)"
    if lim.get("require_atr_expanding") and rec.get("atr_expanding") is False:
        return "ATR is not expanding (Configuration → My trading limits)"
    # Instrument-value band (user 2026-07-27, P-07) — MCAP for equities; only gates when the record carries
    # a value AND the user set a bound (0 = off). No-op until the `mcap` data lands, so it's safe now.
    val, vmin, vmax = rec.get("mcap"), lim.get("min_instrument_value", 0), lim.get("max_instrument_value", 0)
    if isinstance(val, (int, float)):
        if vmin and val < vmin:
            return f"Instrument value {val:,.0f} is below your minimum of {vmin:,.0f} (Configuration → My trading limits)"
        if vmax and val > vmax:
            return f"Instrument value {val:,.0f} is above your maximum of {vmax:,.0f} (Configuration → My trading limits)"
    return ""


def _user_has_ig_creds(name: str) -> bool:
    """Whether this login resolves a complete IG credential set, without opening an IG session."""
    try:
        import ig_shim
        creds = ig_shim._resolve_ig_creds(name)
        return bool(creds and creds.get("api_key") and creds.get("username") and creds.get("password"))
    except Exception as exc:
        log.warning(f"IG credential check failed for {name}: {exc}")
        return False


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
        has_ig_creds = _user_has_ig_creds(name)
        return jsonify({"name": name, "filters": s.get("filters", {}),
                        "exec": _cs.get_exec_flags(), "exec_sources": _cs.EXEC_SOURCES,
                        "exec_descriptions": _cs.EXEC_DESCRIPTIONS,
                        # A login without its own complete IG credentials must always see Bridge OFF,
                        # even if the shared engine switch is enabled for a configured account.
                        "bridge": has_ig_creds and _cs.get_value("exec_WEB_BRIDGE", "false") == "true",
                        "has_ig_creds": has_ig_creds,
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
                        "slack_channels": {ch: str(_cs.get_value(f"slack_ch_{ch}", "1")).strip().lower() not in ("0", "false", "off", "no")
                                           for ch in ("alerts", "daily", "signals", "trades", "weekly")},
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
        for k in ("min_risk_reward", "min_trade", "bounce_alert_pct", "min_instrument_value", "max_instrument_value", "min_rvol",
                  "wallet", "max_position_pct", "preorder_threshold_pct"):
            v = b.get(k)
            if isinstance(v, (int, float)) and v >= 0:
                cur[k] = float(v)
        for k in ("min_quality", "min_volume_score", "require_above_vwap", "require_atr_expanding", "max_trades_per_instrument_per_day", "bounce_lookback_hours",
                  "adaptive_filters", "rebalance_weeks", "max_open", "wo_lifespan_days",
                  "let_winners_run", "let_winners_run_trail", "let_winners_run_stop"):
            v = b.get(k)
            if isinstance(v, (int, float)) and v >= 0:
                cur[k] = int(v)
        cur["adaptive_filters"] = 0  # disabled pending a completed walk-forward implementation
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
    if "slack_channel" in body:   # PER-CHANNEL Slack on/off (user 2026-08-01) — admin; a toggle per webhook
        if not _wu.is_admin(name):
            return jsonify({"ok": False, "error": "admin only"}), 403
        sc = body["slack_channel"] or {}
        ch = str(sc.get("name", "")).strip()
        if ch in ("alerts", "daily", "signals", "trades", "weekly", "orders", "twitter"):
            on = bool(sc.get("on"))
            _cs.set_value(f"slack_ch_{ch}", "1" if on else "0", updated_by=name)
            _wu.log_event(name, f"Slack #{ch} {'ENABLED' if on else 'DISABLED'} (Config)")
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
        # This is a GLOBAL live-order switch for the shared bridge, not a personal preference.
        # UI visibility is not authorisation: only an administrator may change it.
        if not _wu.is_admin(name):
            return jsonify({"ok": False, "error": "admin only"}), 403
        if body["bridge"] and not _user_has_ig_creds(name):
            return jsonify({"ok": False, "error": "IG credentials required"}), 409
        _cs.set_value("exec_WEB_BRIDGE", "true" if body["bridge"] else "false", updated_by=name)
        _wu.log_event(name, f"Squeeze bridge execution switched {'ON' if body['bridge'] else 'OFF'}")
    return jsonify({"ok": True})


def _user_trade_allows(name: str, rec: dict) -> bool:
    """PER-USER trade gate: does this user's own selection allow the given snapshot record? An empty
    list for a field = no restriction; a missing field value never blocks. Used to keep filtered-out
    setups out of the user's pin/place path.

    Direction/location come from the user's Trading (Squeeze) trade filters. MARKET is governed by the
    per-user Markets (User) on/off switch (`markets_off`) plus the admin deny-list (`markets_disabled`),
    which now gates trading as well as visibility (user 2026-08-01: "Markets (User) drives both what is
    visible and what is traded"). Market was removed from the Trading (Squeeze) allow-list."""
    if not rec:
        return True
    import config_store as _cs
    s = _wu.get_settings(name)
    tf = s.get("trade_filters")
    if tf is None:
        tf = _cs.get_trade_filters()                     # seed from legacy global for pre-migration users
    for key, field in (("directions", "direction"), ("locations", "location")):
        allowed = tf.get(key) or []
        v = rec.get(field)
        if allowed and v is not None and v not in allowed:
            return False
    # Market gate: this user's Markets (User) switch + the admin app-wide deny-list.
    market = rec.get("market")
    if market:
        if market in (s.get("markets_off") or []):
            return False
        if market in _cs.get_disabled_markets():
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
                ("slack_weekly", "#weekly webhook", "SLACK_WEEKLY"),
                # P-11 (2026-08-08): these existed only as GitHub Secrets — no local .env / Credentials-UI
                # path had ever seeded them, so import_credentials_from_env couldn't reach them either.
                ("slack_orders", "#orders webhook", "SLACK_ORDERS"), ("slack_rw_hvf", "RW HVF webhook", "SLACK_RW_HVF"),
                ("slack_twitter", "#twitter webhook", "SLACK_TWITTER"),
                ("slack_bot_token", "Bot token (file uploads)", "SLACK_BOT_TOKEN"),
                ("slack_signals_channel_id", "#signals channel ID", "SLACK_SIGNALS_CHANNEL_ID"),
                ("slack_twitter_channel_id", "#twitter channel ID", "SLACK_TWITTER_CHANNEL_ID")]},
    {"id": "Server", "scope": "app", "admin_only": True, "note": "Server-side data API keys and narrowly scoped automation credentials.",
     "fields": [("fred_api_key", "FRED API key", "FRED_API_KEY"), ("eia_api_key", "EIA API key", "EIA_API_KEY"),
                ("quiver_quant_api_key", "Quiver Quant API key", "QUIVER_QUANT_API_KEY"),
                ("cronjob_api_key", "cron-job.org API key", "CRONJOB_API_KEY"),
                ("gh_pat", "GitHub workflow dispatch token (Actions: write; this repository only)", "GH_PAT")]},
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
        # Audit trail (user 2026-08-03, P-25): when IG credentials change, capture the account identity
        # (name + number) under the acting user's fresh session and record it encrypted in Supabase.
        # Best-effort — it also validates the new credentials, but never blocks or fails the save.
        if sec["scope"] == "ig":
            try:
                import ig_shim
                with ig_shim._IG_LOCK, ig_shim.acting_session(name):
                    ai = ig_shim.get_account_info() or {}
                if ai.get("account_id") or ai.get("account_name"):
                    _wu.record_ig_account_audit(name, ai.get("account_name", ""),
                                                ai.get("account_id", ""), source="cred_update", by=name)
            except Exception as e:
                log.warning(f"ig audit on cred update failed for {name}: {e}")
    return jsonify({"ok": True, "saved": saved})


@app.route("/api/userlog")
def api_userlog():
    """The logged-in user's OWN operational log (user 2026-06-30) — token identifies the user, so
    each user only ever sees their own entries."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    return jsonify({"name": name, "log": _wu.get_log(name)})


# ── Documentation guides (user 2026-08-08, P-13) ──────────────────────────────────────────────────────
# Serves the guide .docx built into docs/guides/ (with _manifest.json). Access: "login" guides (the User
# Guides) to any logged-in user; "staff" guides (Support/Operations) to admin or support only. The role
# filter is enforced here, not just in the UI. docs/guides/ is gitignored and built by docs/_build_guides.js,
# so the files must be present on the server machine.
_GUIDES_DIR = os.path.join(os.path.dirname(_HERE), "docs", "guides")


def _load_guides_manifest() -> list:
    try:
        with open(os.path.join(_GUIDES_DIR, "_manifest.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _guide_allowed(entry: dict, name: str, admin: bool, support: bool) -> bool:
    access = entry.get("access", "staff")
    if access == "login":
        return bool(name)              # any authenticated user
    if access == "staff":
        return bool(admin or support)  # Support or Admin only
    return False                       # unknown access → deny


@app.route("/api/guides")
def api_guides():
    """The guide list the caller's role may see (user 2026-08-08, P-13)."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    admin, support = _wu.is_admin(name), _wu.is_support(name)
    guides = [{"slug": g.get("slug"), "title": g.get("title", ""),
               "category": g.get("category", ""), "subtitle": g.get("subtitle", ""),
               "access": g.get("access", "staff")}
              for g in _load_guides_manifest()
              if g.get("slug") and _guide_allowed(g, name, admin, support)]
    return jsonify({"guides": guides})


@app.route("/api/guides/<slug>")
def api_guide_file(slug):
    """Serve one guide as an HTML fragment (rendered in-app), gated by the same role rule as the list."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    admin, support = _wu.is_admin(name), _wu.is_support(name)
    entry = next((g for g in _load_guides_manifest() if g.get("slug") == slug), None)
    if not entry or not _guide_allowed(entry, name, admin, support):
        return jsonify({"error": "not found"}), 404
    fname = os.path.basename(entry.get("file") or (slug + ".html"))   # no path traversal
    path = os.path.join(_GUIDES_DIR, fname)
    if not os.path.isfile(path):
        return jsonify({"error": "document not built on this server"}), 404
    return send_file(path, mimetype="text/html")


def _load_snapshot() -> dict:
    # Supabase is the durable published source when its Storage key is configured. The helper verifies
    # SHA-256 before atomically advancing this local last-known-good cache and fails back to the file.
    try:
        import scanner_snapshot_store
        return scanner_snapshot_store.load_snapshot(SNAPSHOT)
    except Exception as exc:
        log.warning(f"Scanner snapshot store unavailable; using local file: {exc}")
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


@app.route("/app.js")
def app_js():
    """The page's JavaScript, extracted from index.html on 2026-08-23.

    On IONOS, Apache serves this straight from the web root and never reaches Flask — only /api/* is
    rewritten to the CGI adapter. This route is what local runs and any non-Apache host use, and it
    mirrors index() deliberately: same directory, same read-and-return, no static folder involved
    (Flask's default static path is /static, which this file is not under).

    no-store because a browser holding yesterday's app.js against today's index.html is a silent,
    confusing failure: the markup and the code would disagree with nothing to indicate why.
    """
    with open(os.path.join(_HERE, "app.js"), "r", encoding="utf-8") as f:
        return Response(f.read(), mimetype="application/javascript",
                        headers={"Cache-Control": "no-store"})


# Fields a LOGGED-OUT visitor may see (user 2026-07-03: first 5 Scanner columns; the rest obfuscated).
_PUBLIC_FIELDS = ("ticker", "name", "direction", "h3_date", "l3_date", "sector", "market", "location",
                  "has_signal", "status")

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


_LIVE_VWAP_ATR_CACHE = {"gen": None, "data": {}}
# ATR_PERIOD(14)*2 + VWAP_BARS(20) trading bars needed (volume_score.py) => >=48 bars; 90 calendar days
# gives comfortable headroom (~62 trading bars) without the size of the RVOL/VolumeScore lookback.
_LIVE_VWAP_ATR_LOOKBACK_DAYS = 90

_LIVE_INSTRUMENT_METRICS_CACHE = {"gen": None, "data": {}}
# ANSS ceased trading after Synopsys completed its acquisition in July 2025.
# Preserve historic evidence, but never report a misleading current-data repair
# requirement for an instrument that no longer has a tradable listing.
_DELISTED_INSTRUMENTS = {
    "ANSS": "Delisted after acquisition by Synopsys (July 2025)",
    # Qube was suspended after its scheme became effective on 8 July 2026 and
    # removed from the ASX on 17 August. Yahoo's later flat, zero-volume bars
    # are the last scheme price, not live market observations.
    "QUB.AX": "Delisted after the Rubik Australia scheme (August 2026)",
}


def _live_instrument_metrics(snap: dict) -> dict:
    """Current RVOL/VWAP/ATR for every instrument, including rows with no squeeze setup."""
    gen = snap.get("generated_utc")
    if (_LIVE_INSTRUMENT_METRICS_CACHE["gen"] == gen
            and _LIVE_INSTRUMENT_METRICS_CACHE["data"]):
        return _LIVE_INSTRUMENT_METRICS_CACHE["data"]
    out = {}
    try:
        import volume_score as _vscore
        want = [r.get("ticker") for r in snap.get("records", []) if r.get("ticker")]
        if want:
            from db_pool import get_db
            today = _dt.date.today()
            db = get_db()
            try:
                bars_by_tk = _perf_bars(
                    db, {ticker: today for ticker in want},
                    lookback_days=_LIVE_VWAP_ATR_LOOKBACK_DAYS)
                try:
                    source_by_tk = {ticker: source for ticker, source in (db.run(
                        "select distinct on (ticker) ticker, source from price_history "
                        "where ticker = any(:tickers) order by ticker, bar_date desc",
                        tickers=want) or [])}
                except Exception:
                    # Source labelling adds provenance only; it must not turn a
                    # usable OHLCV calculation into a blank metric if metadata
                    # is temporarily unavailable.
                    source_by_tk = {}
            finally:
                db.close()
            for ticker in want:
                if ticker in _DELISTED_INSTRUMENTS:
                    out[ticker] = {"status": "delisted", "reason": _DELISTED_INSTRUMENTS[ticker]}
                    continue
                bars = bars_by_tk.get(ticker, [])
                if not bars:
                    out[ticker] = {"status": "no_price_history"}
                    continue
                index = len(bars) - 1
                # A number of otherwise complete Yahoo daily bars carry a close but not a volume value
                # for the most recent session.  RVOL is a volume measure, so use the newest bar that
                # actually reports volume rather than showing a false blank for the whole instrument.
                # Keep its date separately: a trader can see that price metrics are current to a later
                # close while RVOL is current to the latest usable volume bar.
                rvol_index = next((i for i in range(index, -1, -1)
                                   if bars[i][4] and _vscore._rvol_at(bars, i) is not None), None)
                rvol = _vscore._rvol_at(bars, rvol_index) if rvol_index is not None else None
                has_volume = any(bar[4] for bar in bars)
                source = source_by_tk.get(ticker)
                status = ("complete" if rvol is not None and rvol_index == index else
                          ("complete_latest_volume_bar" if rvol is not None else
                           ("no_reported_volume" if not has_volume else "insufficient_volume_history")))
                if source and source.startswith("YF_NSE_FALLBACK") and status.startswith("complete"):
                    status = "complete_nse_fallback"
                elif source and source.startswith("YF_TICKER_SUCCESSOR") and status.startswith("complete"):
                    status = "complete_ticker_successor"
                out[ticker] = {
                    "rvol": rvol,
                    "rvol_date": str(bars[rvol_index][0])[:10] if rvol_index is not None else None,
                    # This is the literal current-price-above-VWAP instrument metric. Unlike the
                    # direction-aware setup confirmation metric, a BEAR row must not invert it.
                    "above_vwap": _vscore._above_vwap(bars, index, True),
                    "atr_expanding": _vscore._atr_expanding(bars, index),
                    "date": str(bars[index][0])[:10],
                    "source": source,
                    # A missing RVOL is a data-quality state, never an unlabelled value. FX, indices
                    # and some vendor instruments do not publish meaningful daily volume; equities with
                    # a real-volume history but fewer than five usable prior bars need backfill/review.
                    "status": status,
                }
    except Exception as exc:
        log.warning("live all-instrument metrics failed (columns blank): %s", exc)
    _LIVE_INSTRUMENT_METRICS_CACHE.update(gen=gen, data=out)
    return out


def _live_vwap_atr(snap: dict) -> dict:
    """{ticker: (above_vwap, atr_expanding)} for EVERY has_signal record — TRIGGERED, READY and
    DEVELOPING alike — using the most recent available bar (today) as the reference point.

    Found via the data-completeness audit (user 2026-08-11, "check all instruments have current rvol,
    volumescore, above VWAP and above ATR metrics"): build_snapshot.py's own above_vwap/atr_expanding
    fields are ALWAYS None — price_action.get_hvf_signal_mtf() (the function that actually produces the
    snapshot's signal rows) never computes VWAP position and never merges in atr_expanding from the
    separate analyse_price_action() helper.

    First fix (same day) routed this through _snapshot_volscore() instead — correct for TRIGGERED rows,
    but volume_score.py only scores TRIGGERED setups on their break bar (RVOL/VolumeScore are inherently
    about the break itself), so READY/DEVELOPING setups — ~45% of a typical day's has_signal rows — got
    above_vwap=atr_expanding=None regardless of their true state, which silently let them through both
    the Scanner Report's hard filter (fails open on unknown) and the personal-limit order-placement gate.
    User pushback (2026-08-11) was the right call: "for any one day you should be able to calculate these
    values, shouldn't you?" — yes. volume_score._above_vwap(bars, i, bull) and _atr_expanding(bars, i)
    take ANY bar index, not specifically a trigger bar; they only need enough daily history before it.
    "Is this currently above VWAP / is ATR currently expanding" is a plain today's-state read, and is
    arguably the MORE correct thing to gate a live decision on anyway (a setup that triggered days ago and
    has since gone flat should not still read as "above VWAP" from its trigger day). So this now fetches
    fresh bars for every has_signal ticker and reads the LATEST bar directly, instead of going via
    volume_score()'s trigger-bar-scored output. Cached per snapshot generation like every sibling here."""
    gen = snap.get("generated_utc")
    if _LIVE_VWAP_ATR_CACHE["gen"] == gen and _LIVE_VWAP_ATR_CACHE["data"]:
        return _LIVE_VWAP_ATR_CACHE["data"]
    out = {}
    try:
        import volume_score as _vscore
        want = [(r.get("ticker"), r.get("direction") == "BULL")
                for r in snap.get("records", []) if r.get("has_signal") and r.get("ticker")]
        if want:
            from db_pool import get_db
            today = _dt.date.today()
            db = get_db()
            try:
                bars_by_tk = _perf_bars(db, {tk: today for tk, _bull in want},
                                        lookback_days=_LIVE_VWAP_ATR_LOOKBACK_DAYS)
            finally:
                db.close()
            for tk, bull in want:
                bars = bars_by_tk.get(tk, [])
                if not bars:
                    continue
                i = len(bars) - 1   # most recent bar = "today" (or the last trading day on record)
                out[tk] = (_vscore._above_vwap(bars, i, bull), _vscore._atr_expanding(bars, i))
    except Exception as ex:
        log.warning(f"live VWAP/ATR failed (columns blank): {ex}")
    _LIVE_VWAP_ATR_CACHE.update(gen=gen, data=out)
    return out


# 52-week Low/High (user 2026-08-07, ChangeRequest P-08 — Instruments tab): the trailing-year price range
# for EVERY instrument in the snapshot (not just triggered setups), unlike RVOL/VolumeScore above which are
# scoped to TRIGGERED rows only. Cached against the snapshot's generated_utc, same reasoning as the caches
# above — the trailing range only changes when the snapshot (and its underlying price_history) is rebuilt.
_WK52_CACHE = {"gen": None, "data": {}}
_WK52_LOOKBACK_DAYS = 365


def _snapshot_52wk(snap: dict) -> dict:
    """{ticker: (low, high)} over the trailing 52 weeks of price_history. Public data (unlike RVOL/Quality/
    R:R/VolumeScore) — the Instruments tab shows it to logged-out visitors too."""
    gen = snap.get("generated_utc")
    if _WK52_CACHE["gen"] == gen and _WK52_CACHE["data"]:
        return _WK52_CACHE["data"]
    out = {}
    try:
        tickers = sorted({r.get("ticker") for r in snap.get("records", []) if r.get("ticker")})
        if tickers:
            from db_pool import get_db
            today = _dt.date.today()
            db = get_db()
            try:
                bars_by_tk = _perf_bars(db, {tk: today for tk in tickers}, lookback_days=_WK52_LOOKBACK_DAYS)
            finally:
                db.close()
            for tk, bars in bars_by_tk.items():
                highs = [b[1] for b in bars if b[1] is not None]
                lows = [b[2] for b in bars if b[2] is not None]
                if highs and lows:
                    out[tk] = (min(lows), max(highs))
    except Exception as ex:
        log.warning(f"52wk high/low failed (columns blank): {ex}")
    _WK52_CACHE.update(gen=gen, data=out)
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
    # 52-week Low/High (user 2026-08-07, ChangeRequest P-08 — Instruments tab): PUBLIC, unlike RVOL/
    # VolumeScore/Quality/R:R below, so it is computed for both branches, not just the authed one.
    wk52 = _snapshot_52wk(snap)
    # The Scanner shows the FULL universe to every user — the Config trade filters gate only what the
    # operator TRADES (enforced in ig_shim at order time), never what is shown (user 2026-07-06).
    markets = None
    if authed:
        rvol = _snapshot_rvol(snap)                       # RVOL at the real break bar (P-30) — TRIGGERED only
        vscore = _snapshot_volscore(snap)                 # VolumeScore 0–12 at the break bar (P-02 L49) — TRIGGERED only
        # above_vwap/atr_expanding (user 2026-08-11): current-state reads, computed for EVERY has_signal
        # row (TRIGGERED/READY/DEVELOPING) from today's bar — NOT scoped to TRIGGERED like RVOL/VolumeScore
        # above, which are inherently about the break bar itself. See _live_vwap_atr's docstring: previously
        # this came from vscore's per-ticker "components" (TRIGGERED-only), which silently left READY/
        # DEVELOPING rows' VWAP/ATR ticks blank/unknown even though they ARE computable.
        vwap_atr = _live_vwap_atr(snap)
        live_metrics = _live_instrument_metrics(snap)
        recs = []
        for r in snap.get("records", []):
            result = vscore.get(r.get("ticker")) or {}
            w = wk52.get(r.get("ticker")) or (None, None)
            av, ae = vwap_atr.get(r.get("ticker"), (None, None))
            current = live_metrics.get(r.get("ticker"), {})
            recs.append(dict({k: v for k, v in r.items() if k != "_card"},
                             rvol=rvol.get(r.get("ticker")),
                             volume_score=result.get("score"),
                             above_vwap=av,
                             atr_expanding=ae,
                             current_rvol=current.get("rvol"),
                             current_rvol_date=current.get("rvol_date"),
                             current_above_vwap=current.get("above_vwap"),
                             current_atr_expanding=current.get("atr_expanding"),
                             current_metric_date=current.get("date"),
                             current_metric_status=current.get("status", "not_calculated"),
                             current_metric_reason=current.get("reason"),
                             wk52_low=w[0], wk52_high=w[1]))
        # Canonical market list (user 2026-07-31, P-15) — drives the Scanner "Refresh a choice of markets"
        # picker independent of which fields the client keeps on DATA.
        markets = sorted({r.get("market") for r in snap.get("records", []) if r.get("market")})
    else:
        # Teaser mode (user 2026-07-03): only the first-5-column fields leave the server — the rest
        # are stripped HERE, not hidden client-side, so logged-out users cannot fetch them at all.
        recs = []
        for r in snap.get("records", []):
            row = {k: r.get(k) for k in _PUBLIC_FIELDS}
            w = wk52.get(r.get("ticker")) or (None, None)
            row["wk52_low"], row["wk52_high"] = w
            recs.append(row)
    return jsonify({"generated_utc": snap.get("generated_utc"), "count": len(recs),
                    "records": recs, "limited": not authed, "markets": markets})


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
    visual_sources = {
        "ratedmarkets": {"key": "ratedmarkets", "account": "@ratedmarkets", "url": None, "date": None},
        "investingvisual": {"key": "investingvisual", "account": "@InvestingVisual", "url": None, "date": None},
    }

    def visual_key(account):
        key = "".join(ch for ch in str(account or "").lower() if ch.isalnum())
        if key in ("ratedmarket", "ratedmarkets"):
            return "ratedmarkets"
        if key in ("investingvisual", "investingvisuals"):
            return "investingvisual"
        return None

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
                           "where ticker = :t and post_url is not null "
                           "order by disclosed_at desc, recorded_at desc limit 100", t=ticker)
            seen = set()
            for inv, url, dt in (mrows or []):
                if not url or url in seen:
                    continue
                seen.add(url)
                mentions.append({"account": inv, "url": url, "date": str(dt) if dt else None})
                source_key = visual_key(inv)
                if source_key and visual_sources[source_key]["url"] is None:
                    visual_sources[source_key].update({"url": url, "date": str(dt) if dt else None})
        finally:
            db.close()
    except Exception as e:
        log.warning(f"links lookup failed for {ticker}: {e}")
    return jsonify({"ticker": ticker, "ours": ours, "mentions": mentions,
                    "visuals": list(visual_sources.values())})


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
    epic_lookup; returns {} on any failure so the page still loads.

    LOGIN REQUIRED from 2026-08-25. This endpoint had no auth check at all, so an unauthenticated
    request returned the account's REAL open book -- 17 instruments including 2202.HK, 4503.T, ASL.L,
    BP and BRWM.L -- to anyone who asked. It carries no sizes or P&L, but it discloses which instruments
    the account is in, live, which for a trading account is material. Found by sweeping every /api route
    for a missing auth check after the requester asked how a logged-out Performance tab could hold 4,145
    rows (register item 72).

    An unauthenticated caller gets an EMPTY positions map rather than an error shape, so the Scanner's
    Promise.all still resolves and the page renders exactly as before -- it simply shows no position
    indicators, which a logged-out visitor should never have had.
    """
    if not _wu.name_for_token(request.headers.get("X-Auth") or ""):
        return jsonify({"positions": {}}), 401
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


_REFRESHING = {"on": False, "mode": None, "requested_at": 0.0, "base_generated": None,
               "refresh_id": None}
_SCANNER_CRON_TITLE = "Scanner Snapshot Refresh"
_SCANNER_ON_DEMAND_TITLE = "Scanner Snapshot Refresh On Demand"


def _do_rebuild(markets=None) -> bool:
    """Rebuild the snapshot (shared by the 12h loop + the manual refresh button). Guards against a
    concurrent rebuild and clears the PNG/tweet/links caches afterwards. markets (user 2026-07-31, P-15) —
    an optional list of market names to refresh a CHOICE of markets (merged into the snapshot); None =
    full universe."""
    if _REFRESHING["on"]:
        return False
    _REFRESHING["on"] = True
    try:
        from hvf_web.build_snapshot import build
        build(markets=markets or None)
        _PNG_CACHE.clear()
        log.info("snapshot rebuilt; caches cleared" + (f" (markets: {markets})" if markets else ""))
        return True
    except Exception as e:
        log.error(f"snapshot rebuild failed: {e}")
        return False
    finally:
        _REFRESHING["on"] = False


def _dispatch_via_cron_broker(api_key: str, markets=None, refresh_id: str = "") -> str:
    """Schedule one expiring cron-job.org dispatch, reusing its existing GitHub credential.

    cron-job.org has no run-now REST method. A fixed on-demand job is therefore scheduled two minutes ahead
    and expires shortly afterwards. Later clicks reschedule the same job, so requests do not accumulate jobs
    or expose a GitHub token on the web host.
    """
    import datetime as _dt
    import json as _json
    import requests

    endpoint = "https://api.cron-job.org"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    jobs_response = requests.get(f"{endpoint}/jobs", headers=headers, timeout=20)
    jobs_response.raise_for_status()
    jobs = jobs_response.json().get("jobs", [])
    by_title = {job.get("title"): job.get("jobId") for job in jobs}
    target_id = by_title.get(_SCANNER_ON_DEMAND_TITLE)
    source_id = target_id or by_title.get(_SCANNER_CRON_TITLE)
    if not source_id:
        raise RuntimeError("cron-job.org Scanner dispatcher was not found")

    detail_response = requests.get(f"{endpoint}/jobs/{int(source_id)}", headers=headers, timeout=20)
    detail_response.raise_for_status()
    source = detail_response.json().get("jobDetails") or {}
    expected_tail = "/actions/workflows/trading-scanner-snapshot.yml/dispatches"
    if not str(source.get("url") or "").endswith(expected_tail):
        raise RuntimeError("cron-job.org Scanner dispatcher URL did not match the expected workflow")
    extended = dict(source.get("extendedData") or {})
    auth_headers = dict(extended.get("headers") or {})
    if not str(auth_headers.get("Authorization") or "").startswith("Bearer "):
        raise RuntimeError("cron-job.org Scanner dispatcher has no reusable GitHub authorization header")
    extended["headers"] = auth_headers
    extended["body"] = _json.dumps({"ref": os.environ.get("GITHUB_REF_NAME", "main"),
                                     "inputs": {"markets": ",".join(markets or []),
                                                "refresh_id": refresh_id}}, separators=(",", ":"))

    run_at = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=2)).replace(second=0, microsecond=0)
    expires = run_at + _dt.timedelta(minutes=3)
    schedule = {"timezone": "UTC", "expiresAt": int(expires.strftime("%Y%m%d%H%M%S")),
                "minutes": [run_at.minute], "hours": [run_at.hour], "mdays": [run_at.day],
                "months": [run_at.month], "wdays": [-1]}
    delta = {"enabled": True, "schedule": schedule, "extendedData": extended}
    if target_id:
        response = requests.patch(f"{endpoint}/jobs/{int(target_id)}", headers=headers,
                                  json={"job": delta}, timeout=20)
    else:
        create = {key: source[key] for key in ("url", "saveResponses", "requestTimeout", "redirectSuccess",
                                                "folderId", "requestMethod", "auth", "notification") if key in source}
        create.update(delta)
        create["title"] = _SCANNER_ON_DEMAND_TITLE
        response = requests.put(f"{endpoint}/jobs", headers=headers, json={"job": create}, timeout=20)
    response.raise_for_status()
    return run_at.isoformat()


def _dispatch_snapshot_rebuild(markets=None) -> bool:
    """Dispatch the external publisher so the web host never performs the heavy scan."""
    import app_secrets
    import requests
    import uuid

    errors = []
    refresh_id = uuid.uuid4().hex
    token = app_secrets.get_secret("GH_PAT") or app_secrets.get_secret("GITHUB_TOKEN")
    if token:
        try:
            import scanner_snapshot_store as _snapshot_store
            _snapshot_store.queue_refresh_progress(refresh_id, markets, "GitHub Actions", None)
            repo = os.environ.get("GITHUB_REPO", "Red7Al/ClaudeCode")
            response = requests.post(
                f"https://api.github.com/repos/{repo}/actions/workflows/trading-scanner-snapshot.yml/dispatches",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"},
                json={"ref": os.environ.get("GITHUB_REF_NAME", "main"),
                      "inputs": {"markets": ",".join(markets or []), "refresh_id": refresh_id}}, timeout=20,
            )
            response.raise_for_status()
            worker, queued_for = "GitHub Actions", None
        except Exception as exc:
            errors.append(f"direct GitHub dispatch: {exc}")
            token = None
    if not token:
        try:
            cron_key = app_secrets.get_secret("CRONJOB_API_KEY")
            if not cron_key:
                raise RuntimeError("no cron-job.org API key configured")
            queued_for = _dispatch_via_cron_broker(cron_key, markets, refresh_id)
            worker = "cron-job.org → GitHub Actions"
        except Exception as exc:
            errors.append(f"cron-job.org dispatch: {exc}")
            log.error("external Scanner refresh dispatch failed: " + "; ".join(errors))
            try:
                progress = _snapshot_store.RefreshProgressReporter(refresh_id)
                progress.fail("; ".join(errors))
                progress.close()
            except Exception:
                pass
            return False
    snap = _load_snapshot()
    _REFRESHING.update(on=True, mode="external", worker=worker, queued_for=queued_for,
                       requested_at=_time.time(), base_generated=snap.get("generated_utc"),
                       refresh_id=refresh_id)
    try:
        import scanner_snapshot_store as _snapshot_store
        _snapshot_store.queue_refresh_progress(refresh_id, markets, worker, queued_for)
    except Exception as exc:
        log.warning("could not persist queued Scanner progress: %s", exc)
    return True


@app.route("/api/refresh", methods=["POST", "GET"])
def api_refresh():
    """Queue an external snapshot rebuild. The web host never performs the heavy scan."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    if not _wu.is_admin(name):                     # admin-only (user 2026-07-03)
        return jsonify({"error": "admin only"}), 403
    remote_active = None
    try:
        import scanner_snapshot_store as _snapshot_store
        remote_active = _snapshot_store.get_refresh_progress()
    except Exception:
        pass
    if _REFRESHING["on"] or remote_active:
        _wu.log_event(name, "Requested data refresh (one already running)")
        return jsonify({"started": False, "busy": True})
    # Optional choice of markets to refresh (user 2026-07-31, P-15); empty/absent = full universe.
    markets = None
    try:
        body = request.get_json(silent=True) or {}
        raw = body.get("markets")
        if isinstance(raw, list):
            markets = [str(m).strip() for m in raw if str(m).strip()] or None
    except Exception:
        markets = None
    label = ("markets: " + ", ".join(markets)) if markets else "full universe rebuild"
    _wu.log_event(name, f"Requested data refresh ({label})")
    _append_batch("Refresh button",
                  ("Snapshot rebuild started — " + ", ".join(markets)) if markets
                  else "Full universe snapshot rebuild started", by=name)
    if not _dispatch_snapshot_rebuild(markets):
        _append_batch("Refresh button", "External Scanner rebuild dispatch failed", by=name)
        return jsonify({"started": False, "error": "external refresh could not be queued"}), 503
    return jsonify({"started": True, "queued": True, "worker": _REFRESHING.get("worker"),
                    "queued_for": _REFRESHING.get("queued_for"), "markets": markets,
                    "base_generated": _REFRESHING.get("base_generated"),
                    "refresh_id": _REFRESHING.get("refresh_id")})


@app.route("/api/build")
def api_build():
    """Which build THIS worker process is running — the deploy's only way to prove the API took effect.

    IONOS shared hosting keeps the imported Flask module resident behind the CGI wrapper and gives no way
    to restart it: no control panel button, and the SSH session is a sandbox that cannot see the web
    worker. A deploy therefore updates every file while /api/* keeps answering from a previously-loaded
    module. That went unnoticed twice on 2026-08-22/23 and is exactly what blocked the IG close audit.

    Read ONCE at import and cached in the module, deliberately: re-reading the file would report what is
    on disk, which is the very thing that lies. This reports what the RUNNING process loaded, so a
    mismatch against the packaged id is positive proof the worker is stale.

    Unauthenticated, because the deploy must check it before anyone logs in, and it carries no secret:
    a short fingerprint of the commit rather than the commit itself.
    """
    return jsonify(dict(_BUILD_ID, module_loaded_at=_MODULE_LOADED_AT))


@app.route("/api/status")
def api_status():
    snap = _load_snapshot()
    remote_progress = None
    try:
        import scanner_snapshot_store as _snapshot_store
        remote_progress = _snapshot_store.get_refresh_progress(request.args.get("refresh_id") or None)
    except Exception:
        pass
    if _REFRESHING["on"] and _REFRESHING.get("mode") == "external":
        advanced = snap.get("generated_utc") and snap.get("generated_utc") != _REFRESHING.get("base_generated")
        expired = _time.time() - float(_REFRESHING.get("requested_at") or 0) > 2 * 3600
        if advanced or expired:
            _REFRESHING.update(on=False, mode=None, worker=None, queued_for=None,
                               requested_at=0.0, base_generated=None, refresh_id=None)
            if advanced:
                _PNG_CACHE.clear()
    resp = {"refreshing": _REFRESHING["on"], "generated_utc": snap.get("generated_utc"),
            "count": snap.get("count")}
    if remote_progress:
        # A completed external worker has published a new immutable snapshot, but _load_snapshot() may
        # legitimately have returned the local copy from its 60-second metadata-check window.  The browser
        # reloads as soon as this endpoint says the rebuild is complete; without this forced one-time pull,
        # that reload simply paints the old records and, most visibly, the old header timestamp.  Restrict
        # the forced synchronisation to a terminal refresh whose reported generation differs, so ordinary
        # status polling keeps the cache protection.
        if (remote_progress.get("status") == "completed"
                and remote_progress.get("generated_utc")
                and remote_progress.get("generated_utc") != snap.get("generated_utc")):
            try:
                snap, _meta, _changed = _snapshot_store.pull_current(SNAPSHOT, force=True)
                resp.update({"generated_utc": snap.get("generated_utc"), "count": snap.get("count")})
            except Exception as exc:
                # Keep the verified local snapshot available.  A later page/API request retries the normal
                # synchronisation path; do not turn a successfully completed external refresh into a UI error.
                log.warning("could not synchronise completed Scanner snapshot: %s", exc)
        active = remote_progress.get("status") in {"queued", "running", "publishing", "history"}
        resp.update({"refreshing": active, "refresh_id": remote_progress.get("refresh_id"),
                     "worker": remote_progress.get("worker") or "GitHub Actions",
                     "refresh_stage": remote_progress.get("stage"),
                     "progress": {"done": remote_progress.get("done", 0),
                                  "total": remote_progress.get("total", 0)}})
        if remote_progress.get("status") == "failed":
            resp["refresh_error"] = remote_progress.get("error") or "External Scanner refresh failed"
        return jsonify(resp)
    if _REFRESHING["on"] and _REFRESHING.get("mode") == "external":
        resp["worker"] = _REFRESHING.get("worker") or "GitHub Actions"
        resp["queued"] = True
    elif _REFRESHING["on"]:                   # local development build progress
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
    # Non-equity instruments (FX pairs "=X", futures "=F", indices "^", crypto "-USD"/"-USDT") have no
    # company fundamentals — skip the Yahoo call entirely so we don't spam its transient quoteSummary 404s
    # (e.g. GBPCAD=X, which we neither download nor trade; user 2026-07-27, P-10 L140). Return empty KPIs.
    _t = (ticker or "").upper()
    if _t.endswith("=X") or _t.endswith("=F") or _t.startswith("^") or _t.endswith("-USD") or _t.endswith("-USDT"):
        return jsonify({"ticker": ticker, "currency": None, "kpis": {}, "stale": False,
                        "note": "No company fundamentals for non-equity instruments (FX / index / future / crypto)."})
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
                ok = _do_rebuild()
                if ok:
                    try:   # record this run in the web app's Batch Activity (user 2026-08-11, P-12: this
                        # automatic path previously ran silently — only the manual "Refresh data" button
                        # logged. "Did this morning's batch run?" surfaced the gap: any batch activity with
                        # frequency under 6x/day should appear in Batch Activity.)
                        from web_store import append_batch
                        n = _load_snapshot().get("count") or 0
                        append_batch("Auto refresh (12h)", f"Full universe snapshot rebuild ({n} instruments)", by="system")
                    except Exception:
                        pass
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
    # above_vwap/atr_expanding: the snapshot record's OWN fields are always None (data-completeness audit,
    # user 2026-08-11 — see _live_vwap_atr's docstring); use the same live computation api_records() shows.
    _av, _ae = _live_vwap_atr(_load_snapshot()).get(tk, (None, None))
    return {"ticker": tk, "direction": "BUY" if r.get("direction") == "BULL" else "SELL",
            "hvf_type": card.get("hvf_type") or ("BULLISH" if r.get("direction") == "BULL" else "BEARISH"),
            "hvf_signal": r.get("status"), "hvf_h3_level": ov.get("entry", r.get("entry")),
            "hvf_stop_level": ov.get("stop", r.get("stop")), "hvf_target": ov.get("target", r.get("target")),
            "hvf_quality": r.get("quality"), "hvf_risk_reward": r.get("rr"),
            "hvf_timeframe": r.get("timeframe"), "index": r.get("market"), "location": r.get("location"),
            "above_vwap": _av, "atr_expanding": _ae}


@app.route("/api/place-order", methods=["POST"])
def api_place_order():
    """Manually place a pre-order as a live IG working order NOW (user 2026-07-03), instead of waiting
    for the 2-hour bridge. MONEY PATH: subscription must allow pre-orders; the order uses the user's
    OWN IG account (owner = env creds; a non-owner must have supplied their own IG credentials — else
    blocked so no one trades on another account). Goes through the same guarded place_hvf_order_from_sig."""
    import config_store as _cs
    if not _cs.PREORDERS_TO_IG_ENABLED:
        return jsonify({"ok": False, "error": "Pre-orders to IG are disabled for all users."}), 403
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
        # Route the trade-open email to the ACCOUNT HOLDER (user 2026-08-01, P-12): carry the acting
        # user's registered email + name on the profile so place_hvf_order_from_sig emails them (with the
        # instrument report — rules/broker/ownership), not just the global recipients list.
        _profile = dict(get_user_profile() or {})
        _acct_email = _wu.email_for(name)
        if _acct_email:
            _profile["email"] = _acct_email
        _profile["name"] = name
        # Per-user IG working-order lifespan (user 2026-08-01) — apply the acting user's own value at
        # placement; falls back to the shared default when unset.
        _ulim = _user_limits(_wu.get_settings(name))
        _wol = _ulim.get("wo_lifespan_days")
        if isinstance(_wol, (int, float)) and _wol >= 1:
            _profile["wo_lifespan_days"] = int(_wol)
        # Per-user Pre-order proximity band (user 2026-08-03, P-75) — apply the acting user's own % at
        # placement; falls back to the shared default when unset.
        _prox = _ulim.get("preorder_threshold_pct")
        if isinstance(_prox, (int, float)) and _prox > 0:
            _profile["preorder_threshold_pct"] = float(_prox)
        with ig_shim.acting_session(name):
            wo = ig_shim.place_hvf_order_from_sig(sig, _profile, "WEB_MANUAL", 1.0)
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


def _attach_setup_metrics(rows: list) -> None:
    """Attach RVOL / VolumeScore / Quality / R:R / VWAP / ATR to working-order rows, in place.

    working_orders stores none of them -- the table has no such columns -- so the Pre-orders to my IG
    grid rendered "—" in all six for every row ever (user 2026-08-16: "there are many rows without RVOl,
    VOLUMESCORE and QUALITY - these should be available for any instrument for any day").

    Resolved from the SETUP THAT CAUSED THE ORDER rather than from today's snapshot: the most recent
    squeeze_history trigger for that ticker at or before the order was placed. That is point-in-time
    correct, and it backfills every historical row at read time instead of needing a migration that would
    still leave the past blank. VolumeScore and the VWAP/ATR flags come from the same per-trigger maps
    /api/winners and the Back Test use, so all three surfaces report identical figures for a given setup.
    """
    tickers = sorted({r.get("ticker") for r in rows if r.get("ticker")})
    if not tickers:
        return
    setups = {}
    try:
        from db_pool import get_db
        db = get_db()
        try:
            for tk, td, q, rr, rv in (db.run(
                    "select ticker, triggered_date, quality, risk_reward, rvol from squeeze_history "
                    "where ticker = any(:tks) and triggered_date is not null "
                    "order by ticker, triggered_date", tks=list(tickers)) or []):
                setups.setdefault(tk, []).append((str(td)[:10], q, rr, rv))
        finally:
            db.close()
    except Exception as exc:
        log.warning(f"order-ops setup metrics unavailable: {exc}")
        return
    try:
        vsmap, vfmap = _volscore_trigger_map(), _volscore_trigger_feature_map()
    except Exception:
        vsmap, vfmap = {}, {}
    matched = []
    for row in rows:
        hist = setups.get(row.get("ticker")) or []
        if not hist:
            continue
        placed = str(row.get("placed_at") or "")[:10]
        # Latest trigger at or before placement; fall back to the earliest if the order predates them all.
        match = None
        for entry in hist:
            if not placed or entry[0] <= placed:
                match = entry
            else:
                break
        match = match or hist[0]
        td, q, rr, rv = match
        key = (row.get("ticker"), td)
        feat = vfmap.get(key, {})
        row.update({"setup_date": td, "quality": q, "rr": rr, "rvol": rv,
                    "volume_score": vsmap.get(key),
                    "above_vwap": feat.get("above_vwap"),
                    "atr_expanding": feat.get("atr_expanding")})
        matched.append((row, td))
    _fill_missing_rvol(matched)


def _fill_missing_rvol(matched: list) -> None:
    """Second pass: compute RVOL from price_history where squeeze_history stored none.

    815 of 30,408 triggered squeeze_history rows carry rvol NULL (2026-08-17 measurement). Most are FX
    and indices, which have no real volume -- _rvol_at returns None there deliberately, and a fabricated
    1.0 would read as "average participation" rather than "not applicable", so those stay blank and
    should. The rest are equities whose row was written before the volume bar landed: IWG.L's 2026-08-04
    trigger is stored NULL but computes to 1.10 from bars we already hold, and that is the row the user
    reported (2026-08-17, "these should be available for any instrument for any day").

    Uses hvf_web.server._rvol_at -- the same function the Scanner column and volume_score._rvol_at
    mirror -- rather than a second formula, so all three surfaces cannot drift apart. One _perf_bars
    round trip for every ticker needing a fill, not one per row.
    """
    need = {}
    for row, td in matched:
        if row.get("rvol") is not None:
            continue
        try:
            d = _dt.date.fromisoformat(td)
        except Exception:
            continue
        need.setdefault(row.get("ticker"), d)
        need[row["ticker"]] = min(need[row["ticker"]], d)
    if not need:
        return
    try:
        from db_pool import get_db
        db = get_db()
        try:
            # RVOL_BARS is a count of TRADING bars; widen generously in calendar days to cover them.
            bars = _perf_bars(db, need, lookback_days=RVOL_BARS * 3)
        finally:
            db.close()
    except Exception as exc:
        log.warning(f"order-ops RVOL backfill unavailable: {exc}")
        return
    for row, td in matched:
        if row.get("rvol") is not None:
            continue
        b = bars.get(row.get("ticker"))
        if not b:
            continue
        try:
            trigger_date = _dt.date.fromisoformat(td)
            trigger_bar = next((bar for bar in b if bar[0] == trigger_date), None)
            # Keep the source observation alongside the derived RVOL.  A zero
            # is materially different from missing history: downstream audits
            # can label volume-derived features N/A for that bar rather than
            # manufacture an RVOL or silently call the dataset complete.
            if trigger_bar is not None:
                row["trigger_volume"] = trigger_bar[4]
            row["rvol"] = _rvol_at(b, trigger_date)
        except Exception:
            pass


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
        _attach_setup_metrics(rows)
    except Exception as e:
        log.warning(f"order-ops lookup failed: {e}")
    return jsonify(_json_safe({"rows": rows}))


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
_PERF_CACHE = {"ts": 0.0, "data": None, "gzip": None}
_PERF_TTL = 900   # seconds (15 min) — raised with the background warmer so the payload never expires under a user
# Background pre-warm interval (user 2026-08-03): keep the Performance caches warm OFF the request path so a
# tab click never triggers the ~35s cold build (the "processing the 12-month replay…" hang). Kept < every
# cache TTL (_PERF_TTL / _SQA_TTL) so each cache is refreshed before it can expire under a user request.
_PERF_WARM_INTERVAL = 600   # seconds (10 min)


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


_PERF_WARMING = {"on": False}
_PERF_WARM_LOCK = _threading.Lock()


def _claim_perf_warm() -> bool:
    """Atomically reserve the one allowed Performance build."""
    with _PERF_WARM_LOCK:
        if _PERF_WARMING["on"]:
            return False
        _PERF_WARMING["on"] = True
        return True


def _finish_perf_warm():
    with _PERF_WARM_LOCK:
        _PERF_WARMING["on"] = False


def _build_perf_payload():
    """Build the /api/performance payload — the ~40s cold work (12-month replay + per-trigger VolumeScore)
    — and store it in _PERF_CACHE. Safe to call from a background thread."""
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
        vsmap = _volscore_trigger_map()   # per-trigger VolumeScore for the Results Vol column (P-03, 2026-07-27)
        vfmap = _volscore_trigger_feature_map()
        for r in _sqa_all_rows():
            if (r.get("trig_date") or "") < cut12:
                continue
            vf = vfmap.get((r["ticker"], str(r.get("trig_date") or "")[:10]), {})
            out.append({
                "ticker": r["ticker"], "name": r["name"], "mcap": r.get("mcap"),
                "market": r["market"], "sector": r["sector"], "location": r["location"],
                "direction": ("BULL" if r["direction"] == "BULLISH" else "BEAR"), "timeframe": r["timeframe"],
                "quality": r["quality"], "rr": r["rr"],
                "entry": r["entry"], "stop": r["stop"], "target": r["target"],
                "current_price": r["current_price"],
                "trig_date": r["trig_date"], "state": r["outcome"],
                "days_open": _days_open(r),
                "exit_date": r.get("exit_date"),   # close/outcome date (blank while OPEN) — Back Test "Closed" column (user 2026-08-01)
                "perf": (round(r["return_pct"], 2) if r["return_pct"] is not None else None),
                "rvol": r["rvol"],
                "volume_score": vsmap.get((r["ticker"], str(r.get("trig_date") or "")[:10])),
                "above_vwap": vf.get("above_vwap"), "atr_expanding": vf.get("atr_expanding")})
        out.sort(key=lambda r: (r.get("perf") is None, -(r.get("perf") or 0)))
    except Exception as ex:
        log.warning(f"performance report failed: {ex}")
    payload = {"rows": out, "generated": _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime())}
    _PERF_CACHE.update(ts=_time.time(), data=payload, gzip=None)
    return payload


def _gzip_json(payload, cache=None):
    """Return `payload` as JSON, gzipped when the client advertises support for it.

    Large payloads cost far more in transfer than in anything else, and JSON with one repeated set of
    keys per row compresses heavily. /api/performance is ~2 MB uncompressed; /api/squeeze-history was
    measured at 14,927,347 bytes on 2026-08-25, of which roughly 21.7 s was pure download time on a
    warm server -- the browser cannot paint a single row until the last byte lands.

    When `cache` is the dict that produced `payload` (identity, not equality), the compressed bytes are
    stored beside it so neither the transfer nor the compression work repeats on the next request.
    """
    import math
    def _safe(value):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, list):
            return [_safe(v) for v in value]
        if isinstance(value, dict):
            return {k: _safe(v) for k, v in value.items()}
        return value
    safe_payload = _safe(payload)        # JSON forbids NaN/Infinity; browsers reject either token
    if "gzip" not in (request.headers.get("Accept-Encoding") or "").lower():
        return jsonify(safe_payload)
    import gzip
    cacheable = cache is not None and payload is cache.get("data")
    if cacheable and cache.get("gzip") is not None:
        body = cache["gzip"]
    else:
        body = gzip.compress(json.dumps(safe_payload, separators=(",", ":"), default=str,
                                        allow_nan=False).encode("utf-8"), compresslevel=5)
        if cacheable:
            cache["gzip"] = body
    return Response(body, content_type="application/json", headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})


def _perf_response(payload):
    """Return the large Performance payload compressed when the client supports gzip."""
    return _gzip_json(payload, _PERF_CACHE)


def _kick_perf_warm():
    """Start a one-off background build if one isn't already running (user 2026-08-03, P-01: "performance
    still slow") — so a cold /api/performance NEVER blocks the request thread for ~40s."""
    if not _claim_perf_warm():
        return
    def _run():
        try:
            _build_perf_payload()
        finally:
            _finish_perf_warm()
    _threading.Thread(target=_run, daemon=True).start()


@app.route("/api/performance")
def api_performance():
    """Every tradeable trigger over the LAST 12 MONTHS with its levels and realised/open outcome. This is
    the SAME dataset as the "What separates the winners" tab (user 2026-07-18: the two must never diverge)
    — the 12-month squeeze_history replay via _sqa_all_rows (R:R>=3, FX/Crypto excluded, direction-aware
    marked-to-market return%), NOT the recent hvf_triggers. Public; cached + background-warmed.

    NON-BLOCKING (user 2026-08-03, P-01): a cold cache NEVER builds on the request thread (that ~40s build
    is what made the tab feel hung). We kick a background build and answer immediately — the current cache
    if we have one (stale-while-revalidate), else a {warming:true} marker the frontend polls on."""
    now = _time.time()
    if _PERF_CACHE["data"] is not None and now - _PERF_CACHE["ts"] < _PERF_TTL:
        return _perf_response(_PERF_CACHE["data"])
    _kick_perf_warm()
    if _PERF_CACHE["data"] is not None:
        return _perf_response(_PERF_CACHE["data"])   # stale — refreshed in the background
    return jsonify({"rows": [], "warming": True, "generated": ""})


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
_SQA_TTL = 900   # 15 min — matches _PERF_TTL so the background warmer keeps every replay cache fresh (user 2026-08-03)
_SQA_MIN_N = 10          # below this a bucket is reported but never called good or bad
# Matches ig_shim's live tight-stop guard so the analysis population and the order path agree on what
# is tradeable (user 2026-08-16).
_MIN_STOP_DISTANCE = 0.005


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


def _sqa_compound(rows, start=10000.0, max_concurrent=_SQA_MAX_CONCURRENT,
                  position_pct=2.0, leverage=None, min_trade=25.0):
    """Replay a funded portfolio using the same contract as the browser wallet.

    Position size is ``position_pct`` of equity at entry and P&L is position size × actual return%.
    The effective max-open cap can never exceed ``floor(100 / position_pct)``: a 4% model cannot hold
    more than 25 positions. Margin (position size ÷ instrument leverage) remains reserved until exit.
    A trigger is skipped if the position is below the broker minimum, the cap is reached, or available
    cash cannot fund its margin.
    """
    import heapq, itertools
    leverage = {**{"fx": 30.0, "equities": 5.0, "commodities": 10.0, "indices": 20.0}, **(leverage or {})}
    position_fraction = max(0.0, float(position_pct)) / 100.0
    # floor(1 / fraction), computed so floating point cannot shave a position off it (2026-08-23).
    # `1.0 // 0.05` is 19.0, not 20, because 1/0.05 floats to 19.999999999999996 -- so this capped a 5%
    # model at 19 concurrent positions while the browser's Math.floor(1/0.05) allowed 20. Off by one for
    # 2%, 4%, 5%, 10% and 20%, including the shipped 5% default, and contradicting this function's own
    # docstring ("a 4% model cannot hold more than 25 positions" -- it computed 24). Found by
    # test_replay_equivalence.py, which compares this replay against the browser's on the same input.
    funded_cap = max(1, int(1.0 / position_fraction + 1e-9)) if position_fraction > 0 else 1
    requested_cap = int(max_concurrent or 0)
    max_concurrent = min(max(1, requested_cap), funded_cap) if requested_cap > 0 else funded_cap

    def _lev(trade):
        market = trade.get("market") or ""
        kind = "fx" if market == "FX" else "indices" if market == "Indices" else \
               "commodities" if market == "Commodities" else "equities"
        return max(1.0, float(leverage[kind]))

    minq = _sqa_bridge_min_quality()
    seq = sorted((r for r in rows
                  if r["outcome"] in ("TARGET", "STOPPED")
                  and r.get("return_pct") is not None and r.get("trig_date") and r.get("exit_date")
                  and (r.get("quality") or 0) >= minq and (r.get("rr") or 0) >= 3),
                 key=lambda r: r["trig_date"])
    if not seq:
        return None
    wallet, reserved_margin = float(start), 0.0
    taken = skipped = 0
    ledger = []
    _seq = itertools.count()           # tie-breaker so heap never compares dict payloads
    open_pos = []                      # heap of (exit_date, seq, position, margin, return_pct, trade)
    def _close(item):
        nonlocal wallet, reserved_margin
        _ed, _s, position, margin, ret, tr = item
        before = wallet
        pnl = position * ret / 100.0
        wallet = max(0.0, wallet + pnl)
        reserved_margin = max(0.0, reserved_margin - margin)
        ledger.append({"ticker": tr["ticker"], "market": tr["market"], "sector": tr["sector"],
                       "direction": tr["direction"], "quality": tr["quality"], "rr": tr["rr"],
                       "return_pct": tr["return_pct"], "outcome": tr["outcome"],
                       "r_mult": round(tr.get("r_mult") or 0, 2), "trig_date": tr["trig_date"], "exit_date": _ed,
                       "wallet_before": round(before), "stake": round(position, 2),
                       "margin": round(margin, 2), "risked": round(position, 2),
                       "pnl": round(pnl, 2), "wallet_after": round(wallet)})
    for t in seq:
        td = t["trig_date"]
        while open_pos and open_pos[0][0] <= td:        # close anything that has matured
            _close(heapq.heappop(open_pos))
        position = wallet * position_fraction
        margin = position / _lev(t)
        if position >= float(min_trade or 0) and len(open_pos) < max_concurrent and margin <= wallet - reserved_margin:
            reserved_margin += margin
            heapq.heappush(open_pos, (t["exit_date"], next(_seq), position, margin, t["return_pct"], t))
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
    # are not trading FX"). Market is now gated by the app-wide Markets (Admin) deny-list (user 2026-08-01,
    # replacing the retired owner market allow-list); each user's Markets (User) switch refines this further
    # client-side. To keep a market (e.g. FX) out of this shared analysis, disable it in Markets (Admin).
    try:
        import config_store as _cs
        denied = set(_cs.get_disabled_markets())
    except Exception:
        denied = set()
    from db_pool import get_db
    db = get_db()
    # config.MAX_RISK_REWARD has existed since 2026-06-09 documenting exactly this -- "ratios above this
    # are treated as bad level geometry rather than an advantage. A very distant target combined with a
    # tight stop can produce a mathematically valid but non-actionable setup" -- and was enforced NOWHERE.
    # 1,183 of 4,385 deduped 12-month trades sat above it, and Best Settings SELECTED for them because its
    # R:R filters (3/5/8) reward a high ratio. They are not better trades: measured over the live
    # population they carry a HIGHER mean return (5.36% vs 3.22%) on a LOWER win rate (34.5% vs 38.3%),
    # which is the signature of a near-zero stop making the return look large against a trivial risk. That
    # is where "9844% growth" came from (user 2026-08-16: "the status of 9844% growth is nonsense").
    try:
        from config import MAX_RISK_REWARD as _MAX_RR
    except Exception:
        _MAX_RR = 10.0
    try:
        raw = db.run(
            "select ticker, market, timeframe, hvf_type, quality, risk_reward, rvol, "
            "outcome, return_pct, triggered_date, outcome_date, entry_level, stop_level, target_level "
            "from squeeze_history where ready_date is not null "
            "and risk_reward >= 3 and risk_reward <= :maxrr", maxrr=_MAX_RR) or []
    finally:
        db.close()
    # Market-cap map (user 2026-08-01, P-07/P-08) — one query; attached to every row so the Back Test /
    # winners MCap column + the Min/Max instrument value (MCAP) filters have data. Empty until the
    # mcap_backfill has populated instrument_mcap (graceful: missing tickers -> mcap None -> "—").
    mcap_map = {}
    try:
        dbm = get_db()
        try:
            for _t, _m in (dbm.run("select ticker, mcap from instrument_mcap") or []):
                mcap_map[_t] = _m
        finally:
            dbm.close()
    except Exception as _e:
        log.warning(f"mcap map load failed: {_e}")
    rows = []
    for tk, mk, tf, ht, q, rr, rv, oc, ret, td, od, e, s_, t_ in raw:
        s = snap.get(tk, {})
        market = mk or s.get("market")
        if market and market in denied:             # market disabled app-wide (Markets Admin) — exclude
            continue
        # Mirror the engine's own tight-stop guard (ig_shim: "skip trade when stop_distance < 0.5% of
        # price"). Without it the analysis recommends configurations built on setups the order path would
        # refuse to place -- a stop 0.2% from entry is inside spread and normal noise, and 209 of the
        # 12-month rows were tighter than half a percent.
        if e and s_:
            try:
                if abs(float(e) - float(s_)) / float(e) < _MIN_STOP_DISTANCE:
                    continue
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        ret = float(ret) if ret is not None else None
        # R-multiple = the trade's return in units of what it RISKED (return% / stop-distance%): a stop is
        # -1R, a target +R:R (user 2026-07-18, matching calculate_position_size).
        r_mult = None
        if ret is not None and e and s_:
            sd = abs(float(e) - float(s_)) / float(e) * 100.0
            if sd > 0:
                r_mult = ret / sd
        rows.append({"ticker": tk, "name": s.get("name") or tk, "market": market,
                     "mcap": mcap_map.get(tk),   # market cap (normalised) for the MCap column + value filters
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
    # Historical rows written before the trigger-bar volume arrived retain rvol=NULL even where the
    # durable OHLCV store can now prove it.  Backfill the in-memory canonical analysis population with
    # the same point-in-time calculation used by operational evidence, rather than letting Best Settings
    # silently exclude otherwise valid equity triggers.  Markets with no meaningful volume remain None.
    _fill_missing_rvol([(row, str(row.get("trig_date") or "")[:10]) for row in rows if row.get("trig_date")])
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
                # LATERAL per-ticker index probe (user 2026-08-03 P1 FIX): a `distinct on (ticker) ...
                # where ticker in (<1388 params>)` degenerated into a full sort of the 1.74M-row
                # price_history — ~50s — which hung /api/performance ("processing the 12-month replay…"
                # forever) after the 5-year backfill. unnest+LATERAL(LIMIT 1) does one index scan per
                # ticker on idx_price_history_ticker_date instead: ~2.4s for the same 1388 tickers.
                last = {tk: (float(cl) if cl is not None else None)
                        for tk, cl in (db.run(
                            "select t.tk, ph.close from unnest(:tks::text[]) as t(tk) "
                            "cross join lateral (select close from price_history p "
                            "where p.ticker = t.tk order by bar_date desc limit 1) ph",
                            tks=missing) or [])}
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
    return jsonify(_json_safe(payload))


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


def _run_path(direction, entry, stop, target, bars, thr, stop_thr=0, return_date=False,
              target_already_hit=False):
    """Let a winner RUN but ratchet the stop UP TO THE TARGET the moment target is reached, so the trade can
    never give back below the target gain (user 2026-08-01: "put a stop near target so 17 doesn't go to 7").
    Two configurable trailing knobs (user 2026-08-02):
      * `stop_thr` — the STOP-LOSS trailing % applied BEFORE target: if >0 the stop follows price up on the
        way to target (so a trade that runs then reverses before target keeps some gain); 0 = the original
        hard stop stays put until target (same as the real trade).
      * `thr` — the trailing % applied ABOVE target once the stop has floored at the target.
    At target the stop moves to the target and is never let below it, so a target-hitter's return is ALWAYS
    >= its target return, plus any further run. Returns (outcome, exit_price)."""
    import ig_shim
    buy = direction == "BULLISH"
    # For a historical TARGET row, the baseline replay is authoritative evidence that the position reached
    # its target on outcome_date. Start the counterfactual immediately after that event with the target lock
    # already in place. Rewalking revised/vendor-adjusted pre-target bars could otherwise manufacture an
    # impossible early stop and compare a different history with the recorded baseline.
    floored = bool(target_already_hit and target is not None)
    cur = target if floored else stop
    last = last_date = None
    for _bd, hi, lo, cl in bars:
        last_date = str(_bd)
        last = cl
        if buy:
            if lo <= cur:
                result = (("RAN" if floored else "STOPPED"), cur, last_date)
                return result if return_date else result[:2]
            if not floored and target and hi >= target:
                floored = True
                cur = max(cur, target)
        else:
            if hi >= cur:
                result = (("RAN" if floored else "STOPPED"), cur, last_date)
                return result if return_date else result[:2]
            if not floored and target and lo <= target:
                floored = True
                cur = min(cur, target)
        # AFTER target: `thr` is a DISTANCE below the running price (0.04 = 4%), floored at the target.
        # BEFORE target: the hard stop stands unless `stop_thr` is set, in which case it means "keep this
        # share of the run" via compute_trailing_stop.
        #
        # The units genuinely differ, and that is a deliberate choice made with the trade-off measured
        # (user 2026-08-16: "stops are only relevant once target is met ... set the trailing stop loss to
        # 5% from this price", then "drop to 4%"). A share-of-the-run trail is TIGHTER -- at a +79% run it
        # hands back 4.0 points against 8.9 for a 5% distance -- but it cannot be expressed as an IG order.
        # A distance can: IG's native trailing stop then moves the level tick by tick, continuously,
        # including overnight and at weekends. A slightly looser stop the broker always enforces beats a
        # tighter one that only updates when our job happens to run, which is the window a gap falls into.
        # This models what the live order will do, so the report and the account agree.
        if floored:
            if thr and thr > 0:
                ns = cl * (1 - thr) if buy else cl * (1 + thr)
                cur = max(cur, ns) if buy else min(cur, ns)
        elif stop_thr and stop_thr > 0:
            ns = ig_shim.compute_trailing_stop(direction, entry, cur, cl, stop_thr)
            if ns is not None:
                cur = max(cur, ns) if buy else min(cur, ns)
    result = ("OPEN", (last if last is not None else None), last_date)
    return result if return_date else result[:2]


# VolumeScore impact report (user 2026-07-24, ToDo P-02 L55). Uses the SAME 12-month replay population as
# the Performance Results / "What separates the winners" tabs (_sqa_all_rows) — never the small hvf_triggers
# set — so the numbers reconcile with those tabs. Scores every trade's break bar, then shows how filtering
# on VolumeScore changes win rate, average return and the compounded £, and where the profit concentrates.
_VSR_CACHE = {"ts": 0.0, "data": None}


# Keyed BY window, one slot per `years`, not one slot total (user 2026-08-22).
#
# These were single-slot caches carrying a "years" tag: a hit required the tag to match, so a call for a
# DIFFERENT window evicted the previous one instead of sitting beside it. Best Settings requests the
# annual window and then, deliberately deferred, the three-year window -- so the two calls the page always
# makes together evicted each other every time, and neither was ever served warm. Measured on the live
# site 2026-08-22: /api/winners 34.9 s, /api/winners?years=3 16.9 s, the longer window FASTER only because
# the shared _sqa_all_rows population happened to be warm from the call before it.
#
# `years` is clamped to 1..4 by both callers, so this is bounded at four entries per cache.
_VSCORED_CACHE = {}      # years -> {"ts": float, "data": list}
_VSMAP_CACHE = {}        # years -> {"ts": float, "data": dict}   (same per-window keying as above)
_VSFEAT_CACHE = {}       # years -> {"ts": float, "data": dict}


def _volscore_scored(years=1):
    """Windowed _sqa rows annotated with trigger-time VolumeScore confirmations.

    The cache is deliberately keyed by ``years``.  A three-year Winners/Best Settings review must not
    borrow the one-year feature map: that was the source of otherwise unexplained blank VWAP, ATR and
    VolumeScore cells on older rows.  Missing values still remain missing when retained bars cannot prove
    a feature; callers can then expose that as a data-quality exception rather than treating it as a pass.
    """
    import datetime as _dt
    import volume_score as _vscore
    now = _time.time()
    try:
        years = max(1, min(4, int(years)))
    except (TypeError, ValueError):
        years = 1
    _hit = _VSCORED_CACHE.get(years)
    if _hit is not None and now - _hit["ts"] < _SQA_TTL:
        return _hit["data"]
    cutoff = (_dt.date.today() - _dt.timedelta(days=365 * years)).isoformat()
    rows = [r for r in _sqa_all_rows()
            if (r.get("trig_date") or "") >= cutoff and r.get("trig_date") and r.get("entry")]
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
        components = {c["key"]: c.get("got") for c in res.get("components", [])}
        rr["above_vwap"] = components.get("above_vwap")
        rr["atr_expanding"] = components.get("atr_expanding")
        scored.append(rr)
    _VSCORED_CACHE[years] = {"ts": now, "data": scored}
    return scored


def _volscore_trigger_map(years=1):
    """Windowed {(ticker, trigger date): VolumeScore} map for the evidence ledger."""
    now = _time.time()
    hit = _VSMAP_CACHE.get(years)
    if hit is not None and now - hit["ts"] < _SQA_TTL:
        return hit["data"]
    m = {(r["ticker"], str(r["trig_date"])[:10]): r.get("volume_score")
         for r in _volscore_scored(years)}
    _VSMAP_CACHE[years] = {"ts": now, "data": m}
    return m


def _volscore_trigger_feature_map(years=1):
    """Windowed per-trigger confirmations used by Best Settings and its evidence table.

    Cached like its sibling above: /api/winners calls this AND _volscore_trigger_map on every request, so
    without a cache of its own this dict was rebuilt over the whole window every time even when the
    underlying scored rows were served warm.
    """
    now = _time.time()
    hit = _VSFEAT_CACHE.get(years)
    if hit is not None and now - hit["ts"] < _SQA_TTL:
        return hit["data"]
    m = {(r["ticker"], str(r["trig_date"])[:10]): {
             "volume_score": r.get("volume_score"),
             "above_vwap": r.get("above_vwap"),
             "atr_expanding": r.get("atr_expanding")}
         for r in _volscore_scored(years)}
    _VSFEAT_CACHE[years] = {"ts": now, "data": m}
    return m


def _volscore_report(model=None):
    import volume_score as _vscore
    now = _time.time()
    model = model or {}
    model_key = (float(model.get("wallet", 10000)), float(model.get("position_pct", 2)),
                 int(model.get("max_open", _SQA_MAX_CONCURRENT)),
                 tuple(sorted((model.get("leverage") or {}).items())))
    if (_VSR_CACHE["data"] is not None and _VSR_CACHE.get("key") == model_key
            and now - _VSR_CACHE["ts"] < _SQA_TTL):
        return _VSR_CACHE["data"]
    scored = _volscore_scored()

    def _band(v):
        if v is None:
            return None
        return "0–4" if v < 5 else "5–7" if v < 8 else "8–9" if v < 10 else "10–12"

    buckets = _sqa_buckets(scored, lambda r: _band(r.get("volume_score")), "VolumeScore")
    order = {"0–4": 0, "5–7": 1, "8–9": 2, "10–12": 3}
    buckets.sort(key=lambda b: order.get(b["bucket"], 9))

    passing = [r for r in scored if (r.get("volume_score") or 0) >= _vscore.PASS_THRESHOLD]
    seg_all, seg_pass = _sqa_seg(scored), _sqa_seg(passing)
    replay_args = {
        "start": model_key[0], "position_pct": model_key[1], "max_concurrent": max(1, model_key[2]),
        "leverage": model.get("leverage") or None,
    }
    comp_all, comp_pass = _sqa_compound(scored, **replay_args), _sqa_compound(passing, **replay_args)

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
    _VSR_CACHE.update(ts=now, data=data, key=model_key)
    return data


@app.route("/api/volscore-report")
def api_volscore_report():
    """VolumeScore impact over the 12-month replay (user 2026-07-24, P-02). Admin only — sits on the
    admin 'What separates the winners' analysis tab alongside the other replayed-population reports."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not _wu.is_admin(name):
        return jsonify({"error": "admin only"}), 403
    try:
        settings = _wu.get_settings(name)
        limits = _user_limits(settings)
        leverage = {"fx": 30, "equities": 5, "commodities": 10, "indices": 20}
        leverage.update(settings.get("leverage") or {})
        max_open = int(limits.get("max_open") or _SQA_MAX_CONCURRENT)
        return jsonify(_volscore_report({
            "wallet": float(limits.get("wallet") or 10000),
            "position_pct": float(limits.get("max_position_pct") or 2),
            "max_open": max_open,
            "leverage": leverage,
        }))
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
        return jsonify(_json_safe(_best_settings()))
    except Exception as ex:
        log.warning(f"best-settings report failed: {ex}")
        return jsonify({"error": "report unavailable"}), 500


_BEST_HISTORY_LABELS = ("Balanced", "Growth", "Defensive", "Broad evidence")
_BEST_HISTORY_SETTING_KEYS = (
    "scope", "min_rr", "min_quality", "min_volume_score", "min_rvol",
    "require_above_vwap", "require_atr_expanding", "max_position_pct", "max_open",
)
_BEST_HISTORY_RESULT_KEYS = (
    "annual_return", "max_drawdown", "funded_trades", "eligible_trades",
    "positive_quarters", "quarters",
)


def _normalise_best_history_snapshot(body):
    """Allow only the calculated settings/results fields used by the history UI."""
    if not isinstance(body, dict):
        raise ValueError("snapshot required")

    def finite(value, *, integer=False, minimum=None):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError("invalid numeric value")
        value = int(value) if integer else float(value)
        if minimum is not None and value < minimum:
            raise ValueError("numeric value below minimum")
        return value

    raw_model = body.get("model") or {}
    model = {
        "wallet": finite(raw_model.get("wallet"), minimum=1),
        "minimum_trade": finite(raw_model.get("minimum_trade"), minimum=0),
        "position_pct": finite(raw_model.get("position_pct"), minimum=0.1),
        "max_open": finite(raw_model.get("max_open"), integer=True, minimum=1),
    }
    options = []
    seen = set()
    for raw in body.get("options") or []:
        label = str((raw or {}).get("label") or "")
        if label not in _BEST_HISTORY_LABELS or label in seen:
            raise ValueError("invalid recommendation label")
        seen.add(label)
        raw_settings, raw_results = raw.get("settings") or {}, raw.get("results") or {}
        settings = {
            "scope": str(raw_settings.get("scope") or "All markets")[:120],
            "min_rr": finite(raw_settings.get("min_rr"), minimum=0),
            "min_quality": finite(raw_settings.get("min_quality"), minimum=0),
            "min_volume_score": finite(raw_settings.get("min_volume_score"), minimum=0),
            "min_rvol": finite(raw_settings.get("min_rvol"), minimum=0),
            "require_above_vwap": bool(raw_settings.get("require_above_vwap")),
            "require_atr_expanding": bool(raw_settings.get("require_atr_expanding")),
            "max_position_pct": finite(raw_settings.get("max_position_pct"), minimum=0.1),
            "max_open": finite(raw_settings.get("max_open"), integer=True, minimum=1),
        }
        results = {}
        for key in _BEST_HISTORY_RESULT_KEYS:
            results[key] = finite(raw_results.get(key),
                                  integer=key in ("funded_trades", "eligible_trades", "positive_quarters", "quarters"),
                                  minimum=0 if key != "annual_return" else None)
        options.append({"label": label, "settings": settings, "results": results})
    if not options or options[0]["label"] != "Balanced":
        raise ValueError("Balanced recommendation required")
    generated = str(body.get("dataset_generated") or "")[:40]
    data_through = str(body.get("data_through") or "")[:10]
    if data_through and not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", data_through):
        raise ValueError("invalid data-through date")
    return {"dataset_generated": generated, "data_through": data_through,
            "model": model, "options": options}


@app.route("/api/best-settings-history", methods=["GET", "POST"])
def api_best_settings_history():
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    import web_store
    if request.method == "POST":
        try:
            snapshot = _normalise_best_history_snapshot(request.get_json(silent=True))
        except ValueError as ex:
            return jsonify({"error": str(ex)}), 400
        result = web_store.record_best_settings_history(name, snapshot)
        if result == "error":
            return jsonify({"error": "history could not be saved"}), 503
        if result != "unchanged":
            _wu.log_event(name, f"Best Settings daily snapshot {result}")
    else:
        result = "loaded"
    return jsonify({"ok": True, "result": result,
                    "history": web_store.list_best_settings_history(name, 90)})


# Bar cache PER WINDOW LENGTH (2026-08-18). Was a single slot shared by the stop-loss report and the
# winners replay, which was fine while both were fixed at twelve months. Once a 3-year window exists,
# one slot would serve a 3-year replay from 1-year bars and manufacture stop-outs that never happened.
_SLBARS = {}


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
        # Own slot in the per-window cache. This report is always twelve months, so it takes "y1" -- the
        # same slot the default winners replay uses, which is correct because the window is identical.
        _slot = _SLBARS.setdefault("y1", {"ts": 0.0, "by_tk": None})
        if _slot["by_tk"] is not None and now - _slot["ts"] < _SQA_TTL:
            by_tk = _slot["by_tk"]
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
            _slot.update(ts=now, by_tk=by_tk)
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


def _winner_run_target(row):
    """Return the target used by the runner replay, including preserved historical rows.

    Older ``squeeze_history`` rows can have a recorded TARGET outcome/return but no target_level.  Using a
    null target silently turns the runner into an ordinary stop replay and makes a known target winner look
    like a loser.  Prefer the stored level, reconstruct a recorded target outcome from its authoritative
    return, then fall back to the stored risk/reward geometry for unresolved/stopped rows.
    """
    target = row.get("target")
    if target is not None:
        return float(target)
    entry = row.get("entry")
    if entry is None:
        return None
    entry = float(entry)
    buy = row.get("direction") == "BULLISH"
    perf = row.get("return_pct")
    if row.get("outcome") == "TARGET" and perf is not None:
        move = entry * float(perf) / 100.0
        return entry + move if buy else entry - move
    stop, rr = row.get("stop"), row.get("rr")
    if stop is None or rr is None:
        return None
    move = abs(entry - float(stop)) * float(rr)
    return entry + move if buy else entry - move


def _winner_run_bars(by_ticker, ticker, triggered_date):
    """Bars strictly after entry; the trigger bar happened before its closing-price entry."""
    td = str(triggered_date or "")[:10]
    return [b for b in by_ticker.get(ticker, []) if str(b[0])[:10] > td]


def _winners_run_rows(threshold_pct, stop_pct=0, years=1):
    """Every 12-month tradeable trade with its plain return% AND the return%/outcome it WOULD have had if we
    did NOT sell at target but let it RUN — re-walk with the target exit DISABLED, so the position keeps
    trailing past target and exits only on the trailing stop, the hard stop, or the window end.
    threshold_pct = the trailing % ABOVE target; stop_pct = the STOP-LOSS trailing % applied BEFORE target
    (0 => the original hard stop holds until target). The delta (run - plain) is the per-trade impact of
    letting winners run. Illustration only — never touches live orders. (ToDo P-10 'let them run'.)"""
    import datetime as _dt
    thr = (float(threshold_pct or 0) or 0) / 100.0
    sthr = (float(stop_pct or 0) or 0) / 100.0
    # Window length in YEARS (user 2026-08-18: "add a card on best settings for best over three years").
    # Default 1 so every existing caller keeps the twelve-month population it has always had; only the
    # three-year card asks for more. Measured 2026-08-18: 1y = 3,866 rows / 3,104 resolved, 3y = 11,731 /
    # 10,831. price_history retains 4.5 years, so a 3y window is covered -- had yesterday's prune gone to
    # 2 years instead of 4.5 this card could not exist.
    years = max(1.0, min(4.0, float(years or 1)))
    cutoff = (_dt.date.today() - _dt.timedelta(days=int(365 * years))).isoformat()
    rows = sorted((r for r in _sqa_all_rows() if (r.get("trig_date") or "") >= cutoff),
                  key=lambda r: (r.get("trig_date") or ""))
    # Bar cache is keyed by WINDOW. It used to be shared outright with the stop-loss report, which was
    # safe only while both were fixed at twelve months; serving a 3-year request from a 1-year cache would
    # silently truncate every older trade's forward path and report stop-outs that never happened.
    now = _time.time()
    slot = _SLBARS.setdefault(f"y{years:g}", {"ts": 0.0, "by_tk": None})
    if slot["by_tk"] is not None and now - slot["ts"] < _SQA_TTL:
        by_tk = slot["by_tk"]
    else:
        by_tk = {}
        from db_pool import get_db
        db = get_db()
        try:
            raw = db.run("select ticker, bar_date, high, low, close from price_history "
                         "where bar_date >= :d order by ticker, bar_date", d=cutoff) or []
        finally:
            db.close()
        for tk, bd, hi, lo, cl in raw:
            if hi is None or lo is None or cl is None:
                continue
            by_tk.setdefault(tk, []).append((str(bd), float(hi), float(lo), float(cl)))
        slot.update(ts=now, by_tk=by_tk)
    vsmap = _volscore_trigger_map()
    vfmap = _volscore_trigger_feature_map()
    out = []
    for r in rows:
        plain = r["return_pct"]
        run_perf, run_out, run_exit_date = plain, r["outcome"], r.get("exit_date")
        target = _winner_run_target(r)
        if r.get("entry") and r.get("stop") and r.get("trig_date"):
            # Match squeeze_history's execution convention: entry is the trigger bar CLOSE, therefore the
            # trigger bar's high/low cannot stop or target a position that did not exist intraday.
            target_hit = r.get("outcome") == "TARGET" and target is not None and r.get("exit_date")
            replay_start = r["exit_date"] if target_hit else r["trig_date"]
            bars = _winner_run_bars(by_tk, r["ticker"], replay_start)
            if bars:
                # Stop ratchets to the target once reached, then trails above it (user 2026-08-01) — so a
                # target-hitter never falls back below its target gain; only genuine runners beat it.
                run_out, ex, run_exit_date = _run_path(
                    r["direction"], r["entry"], r["stop"], target, bars, thr, sthr, True,
                    target_already_hit=bool(target_hit))
                if ex is not None:
                    buy = r["direction"] == "BULLISH"
                    run_perf = round(((ex - r["entry"]) / r["entry"] * 100.0) if buy
                                     else ((r["entry"] - ex) / r["entry"] * 100.0), 2)
        key = (r["ticker"], str(r.get("trig_date") or "")[:10])
        vf = vfmap.get(key, {})
        out.append({"ticker": r["ticker"], "name": r["name"], "market": r["market"], "sector": r["sector"],
                    "location": r["location"], "direction": ("BULL" if r["direction"] == "BULLISH" else "BEAR"),
                    "trig_date": r["trig_date"], "exit_date": r.get("exit_date"), "run_exit_date": run_exit_date,
                    "entry": r["entry"], "stop": r["stop"],
                    "outcome": r["outcome"], "perf": plain,
                    "run_outcome": run_out, "run_perf": run_perf,
                    "quality": r["quality"], "rr": r["rr"], "rvol": r.get("rvol"),
                    "volume_score": vsmap.get(key),
                    "above_vwap": vf.get("above_vwap"), "atr_expanding": vf.get("atr_expanding")})
    return out


def _winner_run_dedupe(rows):
    """Match the shared Performance population: one best recorded return per ticker/trigger day."""
    selected = {}
    for row in rows or []:
        key = (row.get("ticker") or "", str(row.get("trig_date") or "")[:10])
        current = selected.get(key)
        value = row.get("perf")
        old_value = current.get("perf") if current else None
        if current is None or (value is not None and (old_value is None or float(value) > float(old_value))):
            selected[key] = row
    return sorted(selected.values(), key=lambda row: (str(row.get("trig_date") or ""), row.get("ticker") or ""))


def _winner_run_replay(rows, wallet=10_000, position_pct=5, max_open=20, min_trade=25,
                       perf_key="perf"):
    """Fixed-stake, exit-settled wallet replay matching the browser's ``_combReplay(..., false)``."""
    wallet = max(1.0, float(wallet or 0))
    stake = wallet * max(0.0, float(position_pct or 0)) / 100.0
    limit = max(1, int(max_open or 1))
    if stake > 0:
        limit = min(limit, max(1, int(wallet // stake)))
    equity = peak = wallet
    max_drawdown = peak_open = 0.0
    open_positions, proof = [], []

    def settle(until):
        nonlocal equity, peak, max_drawdown
        open_positions.sort(key=lambda item: item["exit"])
        while open_positions and open_positions[0]["exit"] <= until:
            item = open_positions.pop(0)
            equity += item["net"]
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, ((peak - equity) / peak) if peak > 0 else 0)

    for row in rows:
        triggered = str(row.get("trig_date") or "")
        settle(triggered)
        market = row.get("market") or ""
        leverage = 30 if market == "FX" else 20 if market == "Indices" else 10 if market == "Commodities" else 5
        margin = stake / leverage
        used = sum(item["margin"] for item in open_positions)
        reason = None
        if stake + 1e-9 < float(min_trade or 0):
            reason = "Below minimum trade"
        elif len(open_positions) >= limit:
            reason = "Max open cap"
        elif used + margin > equity + 1e-9:
            reason = "Wallet / margin full"
        key = (row.get("ticker") or "", str(row.get("trig_date") or "")[:10])
        if reason:
            proof.append({"key": key, "row": row, "placed": False, "reason": reason, "stake": stake})
            continue
        # Mirrors the browser's _pfExitDate (user 2026-08-14, P-03/P-05): an unresolved position never
        # frees its slot inside the window. _run_path reports a run_exit_date on the last bar it walked
        # even when the runner is STILL OPEN, so without this guard the runner released its capital at the
        # window edge while the baseline arm of the same trade held it to "9999-99-99" - the two arms of a
        # "like-for-like" comparison were then funded from different books.
        if perf_key == "run_perf":
            exit_date = ("9999-99-99" if row.get("run_outcome") == "OPEN"
                         else (row.get("run_exit_date") or row.get("exit_date") or "9999-99-99"))
        else:
            exit_date = row.get("exit_date") or "9999-99-99"
        result = float(row.get(perf_key) or 0)
        open_positions.append({"exit": str(exit_date), "margin": margin, "net": stake * result / 100.0})
        peak_open = max(peak_open, len(open_positions))
        proof.append({"key": key, "row": row, "placed": True, "reason": "Placed", "stake": stake})
    settle("9999-99-99")
    return {"end_wallet": equity, "return": (equity / wallet) - 1, "max_drawdown": max_drawdown,
            "funded": sum(1 for item in proof if item["placed"]), "peak_open": int(peak_open),
            "proof": proof}


def _winner_run_portfolio_evidence(rows, wallet=10_000, position_pct=5, max_open=20, min_trade=25):
    rows = _winner_run_dedupe([row for row in (rows or []) if row.get("perf") is not None
                               and row.get("run_perf") is not None])
    plain = _winner_run_replay(rows, wallet, position_pct, max_open, min_trade, "perf")
    run = _winner_run_replay(rows, wallet, position_pct, max_open, min_trade, "run_perf")
    p = {item["key"]: item for item in plain["proof"] if item["placed"]}
    q = {item["key"]: item for item in run["proof"] if item["placed"]}
    common = set(p) & set(q)
    plain_only, run_only = set(p) - set(q), set(q) - set(p)
    exit_impact = sum(q[key]["stake"] * (float(q[key]["row"].get("run_perf") or 0)
                                         - float(p[key]["row"].get("perf") or 0)) / 100.0
                      for key in common)
    capacity_impact = (-sum(p[key]["stake"] * float(p[key]["row"].get("perf") or 0) / 100.0
                            for key in plain_only)
                       + sum(q[key]["stake"] * float(q[key]["row"].get("run_perf") or 0) / 100.0
                             for key in run_only))
    total = run["end_wallet"] - plain["end_wallet"]
    unexplained = total - exit_impact - capacity_impact
    target_rows = [row for row in rows if row.get("outcome") == "TARGET"]
    target_breaches = sum(1 for row in target_rows
                          if float(row.get("run_perf") or 0) < float(row.get("perf") or 0) - .01)
    tolerance = .005
    verdict = "improved" if total > tolerance else "worse" if total < -tolerance else "equal"
    return {"eligible": len(rows), "first_trigger": (rows[0].get("trig_date") if rows else None),
            "last_trigger": (rows[-1].get("trig_date") if rows else None),
            "model": {"wallet": wallet, "position_pct": position_pct, "max_open": max_open,
                      "minimum_trade": min_trade},
            "baseline": {key: value for key, value in plain.items() if key != "proof"},
            "runner": {key: value for key, value in run.items() if key != "proof"},
            "attribution": {"common_funded": len(common), "baseline_only": len(plain_only),
                            "runner_only": len(run_only), "exit_impact": exit_impact,
                            "capacity_impact": capacity_impact, "total_difference": total,
                            "unexplained": unexplained, "reconciled": abs(unexplained) < tolerance},
            "target_lock": {"target_hits": len(target_rows), "breaches": target_breaches},
            "trade_comparison": {"better": sum(1 for row in rows if row["run_perf"] > row["perf"] + .01),
                                 "equal": sum(1 for row in rows if abs(row["run_perf"] - row["perf"]) <= .01),
                                 "worse": sum(1 for row in rows if row["run_perf"] < row["perf"] - .01)},
            "verdict": verdict,
            "valid": target_breaches == 0 and abs(unexplained) < tolerance}


@app.route("/api/winners-run")
def api_winners_run():
    """Winners rows re-backtested LETTING WINNERS RUN — no sell at target, trail past it. Query params:
    `thr` % = trail above target (default 25); `stop` % = stop-loss trail before target (default 0 = hard stop
    holds until target). Illustration-only (user 2026-08-01/02, ToDo P-08 'let them run')."""
    try:
        thr = request.args.get("thr")
        if thr in (None, ""):
            thr = "4"     # distance below price once target is reached (user 2026-08-16)
        stop = request.args.get("stop") or "0"
        # `years` = replay window (user 2026-08-18, three-year card). Default 1 keeps every existing
        # caller on the twelve-month population.
        try:
            years = max(1.0, min(4.0, float(request.args.get("years", 1))))
        except (TypeError, ValueError):
            years = 1.0
        rows = _winners_run_rows(thr, stop, years)
        def _number(name, default, low, high):
            try:
                return min(high, max(low, float(request.args.get(name, default))))
            except (TypeError, ValueError):
                return default
        wallet = _number("wallet", 10_000, 1, 100_000_000)
        position_pct = _number("position_pct", 5, .1, 100)
        max_open = int(_number("max_open", 20, 1, 10_000))
        min_trade = _number("min_trade", 25, 0, 1_000_000)
        evidence = _winner_run_portfolio_evidence(rows, wallet, position_pct, max_open, min_trade)
        return jsonify({"rows": rows, "threshold_pct": float(thr or 0), "stop_pct": float(stop or 0),
                        "months": 12, "generated": _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime()),
                        "evidence": evidence})
    except Exception as ex:
        log.warning(f"winners-run failed: {ex}")
        return jsonify({"rows": [], "threshold_pct": 0, "stop_pct": 0, "months": 12})


# Squeeze History payload cache (user 2026-08-25: "squeeze history is still very slow to load").
# The response is identical for every logged-in caller -- the endpoint uses the token only to authorise,
# never to filter -- so one shared copy is correct. Measured cold on 2026-08-25: 31,558 ms to first byte,
# because building it warms _volscore_scored, queries the whole squeeze_history table and then constructs
# a dict per row. Warm it was 4,203 ms. The underlying table is rewritten once a day by refresh_daily,
# so a 15-minute TTL (matching _SQA_TTL and _PERF_TTL) cannot serve meaningfully stale history.
_SQH_TTL = 900
_SQH_CACHE = {"ts": 0.0, "data": None, "gzip": None, "key": None}
_SQH_WARMING = {"on": False}
_SQH_WARM_LOCK = _threading.Lock()


def _claim_sqh_warm() -> bool:
    """Atomically reserve the one allowed Squeeze History build."""
    with _SQH_WARM_LOCK:
        if _SQH_WARMING["on"]:
            return False
        _SQH_WARMING["on"] = True
        return True


def _finish_sqh_warm():
    with _SQH_WARM_LOCK:
        _SQH_WARMING["on"] = False


def _kick_sqh_warm():
    """Start a one-off background build if one isn't already running, so a cold /api/squeeze-history
    never blocks the request thread. Mirrors _kick_perf_warm, for the same reason and after the same
    symptom: on 2026-08-25 a cold request was left outstanding for over ten minutes without returning a
    single byte, while a scan competed for the same database. On IONOS's single resident worker one such
    request can hold up others, so this build must never happen on the request thread."""
    if not _claim_sqh_warm():
        return
    def _run():
        try:
            _build_sqh_payload()
        finally:
            _finish_sqh_warm()
    _threading.Thread(target=_run, daemon=True).start()


def _build_sqh_payload():
    """Build the Squeeze History payload and store it in _SQH_CACHE. Safe to call from a background thread.

    Keyed on the data's own freshness, not merely on elapsed time. squeeze_history is rewritten ONCE A DAY
    by refresh_daily, so a bare 15-minute TTL would trigger roughly 96 full rebuilds a day to produce a
    byte-identical answer -- wasted work that competes with the scanner for the same database. The cheap
    max(refreshed_at) probe runs first; when it matches what the cache was built from, the existing
    payload is kept and only its timestamp is bumped."""
    payload = {"rows": [], "generated": _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime()),
               "refreshed_at": None, "data_through": None}
    key = None
    try:
        from db_pool import get_db
        db = get_db()
        try:
            freshness = db.run(
                "select max(refreshed_at), greatest(max(last_seen),max(triggered_date),max(outcome_date)) "
                "from squeeze_history") or []
            if freshness:
                payload["refreshed_at"] = str(freshness[0][0]) if freshness[0][0] else None
                payload["data_through"] = str(freshness[0][1]) if freshness[0][1] else None
            key = (payload["refreshed_at"], payload["data_through"])
            if _SQH_CACHE.get("data") is not None and _SQH_CACHE.get("key") == key:
                # Same data as the cached copy — keep the payload AND its compressed bytes, and just mark
                # it fresh. This is the whole point of keying on the data rather than on the clock.
                _SQH_CACHE["ts"] = _time.time()
                return _SQH_CACHE["data"]
            raw = db.run(
                "select ticker, market, timeframe, hvf_type, first_seen, first_signal, ready_date, "
                "triggered_date, outcome, outcome_date, return_pct, quality, risk_reward, rvol "
                "from squeeze_history "
                "order by coalesce(triggered_date, ready_date, first_seen) desc nulls last") or []   # no row cap (user 2026-07-18, P-01)
        finally:
            db.close()
        snap = {r["ticker"]: r for r in _load_snapshot().get("records", []) if r.get("ticker")}
        vsmap = _volscore_trigger_map()
        vfmap = _volscore_trigger_feature_map()
        for (tk, mk, tf, ht, fseen, fsig, rd, td, oc, od, ret, q, rr, rv) in raw:
            s = snap.get(tk, {})
            feature_key = (tk, str(td or "")[:10])
            features = vfmap.get(feature_key, {})
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
                "rr": (round(rr, 1) if rr is not None else None),
                "rvol": (round(rv, 2) if rv is not None else None),
                "volume_score": vsmap.get(feature_key),
                "above_vwap": features.get("above_vwap"),
                "atr_expanding": features.get("atr_expanding")})
    except Exception as ex:
        log.warning(f"squeeze history failed: {ex}")
        # Deliberately NOT cached: a failure here yields an empty table, and pinning that would turn one
        # bad query into a prolonged "no history" for every admin. Returning None leaves any previously
        # good payload in place rather than replacing it with an empty one.
        return None
    _SQH_CACHE.update(ts=_time.time(), data=payload, gzip=None, key=key)
    return payload


@app.route("/api/squeeze-history")
def api_squeeze_history():
    """Lifecycle history of squeeze funnels (user 2026-07-18, Squeeze History (Admin) tab): each funnel's
    developing → ready → triggered → outcome journey, replayed over price history. Newest first.

    Served gzipped, cached, and background-warmed (user 2026-08-25: "squeeze history is still very slow
    to load"). NON-BLOCKING for the same reason as /api/performance: the cold build NEVER runs on the
    request thread. Measured before this change -- 14,927,347 bytes of uncompressed JSON, 33.0 s and
    25.9 s for two loads, and one cold request that never returned at all while a scan held the database.
    A cold caller now gets {warming:true} immediately and polls, exactly as the Performance tab does.
    """
    if not _wu.name_for_token(request.headers.get("X-Auth") or ""):
        return jsonify({"error": "login required"}), 401
    now = _time.time()
    if _SQH_CACHE["data"] is not None and now - _SQH_CACHE["ts"] < _SQH_TTL:
        return _gzip_json(_SQH_CACHE["data"], _SQH_CACHE)
    _kick_sqh_warm()
    if _SQH_CACHE["data"] is not None:
        return _gzip_json(_SQH_CACHE["data"], _SQH_CACHE)   # stale — refreshed in the background
    return jsonify({"rows": [], "warming": True, "generated": ""})


@app.route("/api/fees")
def api_fees():
    """Fees (Admin) tab (user 2026-07-18): management fee (1%/mo of AUM) + performance fee (10%/mo of
    profits). Returns THREE periods (user 2026-07-31, P-05; third period added 2026-08-07, ChangeRequest
    P-09 — "add another previous month tab so we can see two previous months plus current month"):
    "prev_month" (two months ago), "last_month" (the billed month) and "this_month" (month-to-date, "so
    far"). Each carries the daily_pnl aggregate PLUS the underlying per-trade transactions (trade_log) that
    EXPLAIN the realised profit — the transaction pnl sums back to the period profit (reconciled per
    row-count/total)."""
    import datetime as _dt

    def _ig_day(value):
        """Normalise IG ISO or locale date strings to YYYY-MM-DD for month filtering."""
        s = str(value or "")
        m = _re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", s)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        m = _re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
        if m:
            year = int(m.group(3)); year += 2000 if year < 100 else 0
            return f"{year:04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
        return s[:10]

    def _period_ig(start, end, label, ig_txns):
        """Build a period from the viewer's REAL IG transaction history (user 2026-08-02, P-06) — the source
        of truth, so ALL closed trades show (not just the sparse trade_log) and IG's own charges (overnight
        funding/interest) are surfaced. `pnl` is the NET realised profit (trade P&L + charges)."""
        a, b = start.isoformat(), end.isoformat()
        inwin = [t for t in ig_txns if a <= _ig_day(t.get("date")) <= b]
        trades = [t for t in inwin if t.get("kind") == "TRADE"]
        charges = [t for t in inwin if t.get("kind") == "CHARGE"]
        txns = []
        for t in sorted(trades, key=lambda x: str(x.get("date") or x.get("open_date") or ""), reverse=True):
            sz = t.get("size")
            op, cp = t.get("open_level"), t.get("close_level")
            direction = "BUY" if (sz is not None and sz > 0) else "SELL"
            pct = None
            if op and cp and op != 0:
                mv = (cp - op) / op * 100.0
                pct = round(mv if direction == "BUY" else -mv, 2)
            txns.append({
                "ticker": t.get("instrument"), "direction": direction,
                "size": (abs(sz) if sz is not None else None),
                "open": op, "close": cp,
                "pnl": round(float(t.get("pnl") or 0), 2), "pnl_pct": pct,
                "opened_at": t.get("open_date"), "closed_at": t.get("date"),
                "reason": t.get("type")})
        chg_rows = [{"date": t.get("date"), "instrument": t.get("instrument"),
                     "type": t.get("type"), "pnl": round(float(t.get("pnl") or 0), 2)} for t in
                    sorted(charges, key=lambda x: str(x.get("date") or ""), reverse=True)]
        trade_pnl = round(sum(t["pnl"] for t in txns), 2)
        charges_total = round(sum(c["pnl"] for c in chg_rows), 2)
        net = round(trade_pnl + charges_total, 2)
        wins = sum(1 for t in txns if t["pnl"] > 0)
        losses = sum(1 for t in txns if t["pnl"] < 0)
        return {"label": label, "start": a, "end": b, "source": "ig",
                "pnl": net, "trade_pnl": trade_pnl, "charges_total": charges_total,
                "trades": len(txns), "wins": wins, "losses": losses,
                "txns": txns, "txn_pnl": trade_pnl, "charges": chg_rows, "reconciled": True}

    def _period(db, start, end, label):
        # Fallback (no IG session): the app's own record — daily_pnl aggregate + trade_log transactions.
        row = db.run("select coalesce(sum(total_pnl),0), coalesce(sum(trade_count),0), "
                     "coalesce(sum(win_count),0), coalesce(sum(loss_count),0) from daily_pnl "
                     "where trade_date >= :a and trade_date <= :b",
                     a=start.isoformat(), b=end.isoformat()) or [(0, 0, 0, 0)]
        pnl, tc, wc, lc = row[0]
        # The transactions behind it — closed trades whose close date falls in the window.
        txns = []
        try:
            for (tk, dr, sz, op, cp, pl, plp, oa, ca, cr) in (db.run(
                    "select ticker, direction, size, open_price, close_price, pnl, pnl_pct, "
                    "opened_at, closed_at, close_reason from trade_log "
                    "where closed_at::date >= :a and closed_at::date <= :b order by closed_at",
                    a=start.isoformat(), b=end.isoformat()) or []):
                txns.append({
                    "ticker": tk, "direction": dr,
                    "size": (float(sz) if sz is not None else None),
                    "open": (float(op) if op is not None else None),
                    "close": (float(cp) if cp is not None else None),
                    "pnl": (float(pl) if pl is not None else 0.0),
                    "pnl_pct": (float(plp) if plp is not None else None),
                    "opened_at": (oa.isoformat() if oa else None),
                    "closed_at": (ca.isoformat() if ca else None),
                    "reason": cr})
        except Exception as tex:
            log.warning(f"fees txns failed for {label}: {tex}")
        txn_sum = round(sum(t["pnl"] for t in txns), 2)
        return {"label": label, "start": start.isoformat(), "end": end.isoformat(), "source": "app",
                "pnl": float(pnl or 0), "trades": int(tc or 0),
                "wins": int(wc or 0), "losses": int(lc or 0),
                "txns": txns, "txn_pnl": txn_sum, "charges": [], "charges_total": 0.0,
                # True when the per-trade transactions reconcile with the billed daily_pnl total.
                "reconciled": (abs(txn_sum - float(pnl or 0)) < 0.01)}

    # The viewing user's active fee discount (user 2026-08-02, P-20) — applied to the worked example so
    # the numbers reflect what THAT account is actually charged. Zero for users without a discount.
    _viewer = _wu.name_for_token(request.headers.get("X-Auth") or "")
    try:
        _disc = _wu.active_fee_discount(_viewer) if _viewer else {"mgmt_pct": 0, "perf_pct": 0, "active": False}
    except Exception:
        _disc = {"mgmt_pct": 0, "perf_pct": 0, "active": False}
    # The REAL account value for the AUM basis (user 2026-08-02, P-12) — so the management-fee example uses
    # the actual equity (cash balance + open P&L), not a round typed figure. Best-effort; None when the
    # viewer has no IG session/creds (the UI then keeps the manual default).
    today = _dt.date.today()
    first_this = today.replace(day=1)
    last_month_end = first_this - _dt.timedelta(days=1)
    first_last = last_month_end.replace(day=1)
    prev_month_end = first_last - _dt.timedelta(days=1)   # two months ago (2026-08-07, ChangeRequest P-09)
    first_prev = prev_month_end.replace(day=1)

    # One IG session block (user 2026-08-02): fetch the real account equity (AUM basis) AND the full
    # transaction history spanning both periods, so the fees reflect the ACTUAL closed trades + IG charges.
    _real_aum = None
    _aum_ccy = None
    _ig_txns = None
    _fees_warning = None
    try:
        import ig_shim
        if _viewer and ig_shim.session_for(_viewer) is not None:
            with ig_shim._IG_LOCK, ig_shim.acting_session(_viewer):
                _bal = ig_shim.get_account_balance() or {}
                # Load a broad history first. The period renderer then filters by close/charge date;
                # this preserves trades opened before the billed month but closed inside it.
                history_from = first_last - _dt.timedelta(days=365)
                _ig_txns = ig_shim.get_transactions(history_from.strftime("%Y-%m-%dT00:00:00"),
                                                     today.strftime("%Y-%m-%dT23:59:59"), strict=True)
            # IG can return an empty history during a transient/rate-limited read even though the
            # application's trade log contains the closed transactions. Do not present that as a
            # confirmed empty month; use the local ledger fallback and let the payload identify it.
            if not any(t.get("kind") == "TRADE" for t in (_ig_txns or [])):
                log.warning("fees IG history returned no closed trades for %s; using app ledger fallback", _viewer)
                _fees_warning = ("IG returned no closed trades in the requested history window. "
                                 "The figures below use the application ledger and may be incomplete.")
                _ig_txns = None
            _b = float(_bal.get("balance") or 0)
            _pl = float(_bal.get("profit_loss") or 0)
            _real_aum = round(_b + _pl, 2)      # account equity = ledger balance + open P&L
            _aum_ccy = _bal.get("currency")
    except Exception as _ex:
        log.warning(f"fees IG data unavailable: {_ex}")
        _fees_warning = ("IG transaction history could not be read. "
                         "The figures below use the application ledger and may be incomplete. "
                         f"Broker error: {_ex}")

    payload = {"mgmt_pct": 1.0, "perf_pct": 10.0, "prev_month": None, "last_month": None, "this_month": None,
               "discount": _disc, "user": _viewer or None,
               "real_aum": _real_aum, "aum_currency": _aum_ccy,
               "source": ("ig" if _ig_txns is not None else "app_fallback"),
               "data_warning": _fees_warning,
               "generated": _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime())}
    try:
        if _ig_txns is not None:
            # Truth: the account's real transaction history (all closed trades + IG charges).
            payload["prev_month"] = _period_ig(first_prev, prev_month_end, first_prev.strftime("%B %Y"), _ig_txns)
            payload["last_month"] = _period_ig(first_last, last_month_end, first_last.strftime("%B %Y"), _ig_txns)
            payload["this_month"] = _period_ig(first_this, today, today.strftime("%B %Y") + " (so far)", _ig_txns)
            # IG can return a valid-looking history response while omitting a month (or returning only
            # charges). Compare each period with the application's closed-trade ledger so an empty IG
            # slice is never presented as proof that no trades occurred.
            from db_pool import get_db
            _db = get_db()
            try:
                _app_prev = _period(_db, first_prev, prev_month_end, first_prev.strftime("%B %Y"))
                _app_last = _period(_db, first_last, last_month_end, first_last.strftime("%B %Y"))
                _app_this = _period(_db, first_this, today, today.strftime("%B %Y") + " (so far)")
            finally:
                _db.close()
            for _key, _app_seg in (("prev_month", _app_prev), ("last_month", _app_last), ("this_month", _app_this)):
                _ig_seg = payload[_key]
                if len(_app_seg.get("txns") or []) > len(_ig_seg.get("txns") or []):
                    payload[_key] = _app_seg
                    payload[_key]["source"] = "app_fallback"
                    _fees_warning = (_fees_warning or "") + (
                        f" IG history was incomplete for {_app_seg['label']}; "
                        "the fuller application ledger is shown for that period."
                    )
        else:
            # Fallback: the app's own record (daily_pnl + trade_log).
            from db_pool import get_db
            db = get_db()
            try:
                payload["prev_month"] = _period(db, first_prev, prev_month_end, first_prev.strftime("%B %Y"))
                payload["last_month"] = _period(db, first_last, last_month_end, first_last.strftime("%B %Y"))
                payload["this_month"] = _period(db, first_this, today, today.strftime("%B %Y") + " (so far)")
            finally:
                db.close()
    except Exception as ex:
        log.warning(f"fees failed: {ex}")
    return jsonify(payload)


# Precomputed winners payloads (user 2026-08-23). Building one costs about 33 seconds -- the DB replay in
# _sqa_all_rows plus the per-trigger feature pass -- and Best Settings asks for two windows on every visit.
# Caching fixed the REPEAT cost; only precomputing fixes the first one. run_winners_precompute.py builds
# these after the daily data refresh and stores them here, exactly as the Best Settings full-grid audit
# already does with best_settings_full_grid_audit.
#
# Stored copies are used ONLY when they were built from the dataset now in play: the snapshot's
# generated_utc is stored alongside and must match, and the copy must be under a day old. Anything else
# falls through to the live build, so a missed or failed precompute is slow, never wrong.
_WINNERS_STORE_MAX_AGE = 26 * 3600


def _winners_store_key(years: int) -> str:
    return f"winners_rows_{int(years)}y"


def _winners_payload(years: int) -> dict:
    """Build the winners payload for one window.

    THE SINGLE DEFINITION, shared by /api/winners and the scheduled precompute, so a stored copy cannot
    drift from what the endpoint would have produced. See memory results-winners-same-dataset: the
    Performance tabs and this surface must be the same population, not two lookalike builds.
    """
    import datetime as _dt
    payload = {"rows": [], "months": years * 12, "generated": _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime())}
    cutoff = (_dt.date.today() - _dt.timedelta(days=365 * years)).isoformat()
    rows = [r for r in _sqa_all_rows() if (r.get("trig_date") or "") >= cutoff]
    rows.sort(key=lambda r: (r.get("trig_date") or ""))
    # Use the same requested review window for the population and its trigger-time features.  Do
    # not silently borrow the annual cache for the three-year card.
    # Keep the established no-argument annual call shape for existing integrations; the longer
    # window is the exceptional path that must request its own feature population explicitly.
    vsmap = _volscore_trigger_map() if years == 1 else _volscore_trigger_map(years)
    vfmap = _volscore_trigger_feature_map() if years == 1 else _volscore_trigger_feature_map(years)
    payload["rows"] = [
        {"ticker": r["ticker"], "name": r["name"], "market": r["market"], "mcap": _json_safe(r.get("mcap")), "sector": r["sector"],
         "location": r["location"], "direction": ("BULL" if r["direction"] == "BULLISH" else "BEAR"),
         "trig_date": r["trig_date"], "exit_date": r.get("exit_date"), "entry": _json_safe(r["entry"]), "stop": _json_safe(r["stop"]),
         "outcome": r["outcome"], "perf": _json_safe(r["return_pct"]),
         "quality": _json_safe(r["quality"]), "rr": _json_safe(r["rr"]), "rvol": _json_safe(r["rvol"]),
         "volume_score": _json_safe(vsmap.get((r["ticker"], str(r.get("trig_date") or "")[:10]))),
         "above_vwap": vfmap.get((r["ticker"], str(r.get("trig_date") or "")[:10]), {}).get("above_vwap"),
         "atr_expanding": vfmap.get((r["ticker"], str(r.get("trig_date") or "")[:10]), {}).get("atr_expanding")}
        for r in rows]
    return payload


@app.route("/api/winners")
def api_winners():
    """Raw per-trade rows for the "What separates the winners" tab (user 2026-07-18): the FULL last-12-months
    tradeable population (squeeze_history replay, R:R>=3, FX/Crypto excluded), each with its direction-aware
    return% — the SAME definition the Performance report uses. The frontend applies a 2%-of-the-running-wallet
    stake to compound the £. Chronological so the wallet can be built oldest-first."""
    try:
        years = max(1, min(4, int(request.args.get("years", "1"))))
    except (TypeError, ValueError):
        years = 1
    stored = _winners_stored(years)
    if stored is not None:
        return jsonify(stored)
    try:
        payload = _winners_payload(years)
    except Exception as ex:
        log.warning(f"winners rows failed: {ex}")
        payload = {"rows": [], "months": years * 12,
                   "generated": _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime()),
                   "error": "The annual trade dataset could not be built."}
    return jsonify(_json_safe(payload))


def _winners_stored(years: int):
    """A precomputed payload, but only if it was built from the dataset now in play. Else None."""
    try:
        import web_store
        doc = web_store.load_json_store(_winners_store_key(years))
    except Exception as ex:
        log.warning(f"winners precompute unavailable ({ex}); building live")
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("payload"), dict):
        return None
    try:
        if _time.time() - float(doc.get("built_at") or 0) > _WINNERS_STORE_MAX_AGE:
            return None
        if (doc.get("dataset") or "") != (_load_snapshot().get("generated_utc") or ""):
            return None       # built from a different scan; the live build is the correct answer
    except Exception:
        return None
    return doc["payload"]


_CR_DIR = os.path.join(_REPO_ROOT, "ChangeRequests")
# Status a requirement line can carry (user 2026-07-10, Change Requests tab). A line is Completed/In
# Progress/Cancelled/Requested when it ends with a bracketed marker (e.g. "[Completed]") or carries a
# short leading tag ([x] done, [~] wip, [-] cancelled, [?] requested); otherwise it is Not Started.
# The marker may include a short inline note (e.g. "[Deferred - user 2026-08-05]") or be followed by a
# parenthetical note (user 2026-07-17). Without those allowances, noted rows silently read as Not Started.
_CR_TAIL = _re.compile(
    r"\[(completed|in[\s-]?progress|not[\s-]?started|cancelled|canceled|requested|deferred)(?:\s+-[^\]]*)?\]\s*(?:\([^)]*\)\s*)?$",
    _re.I)
_CR_LEAD = {"[x]": "Completed", "[X]": "Completed", "[~]": "In Progress",
            "[-]": "Cancelled", "[?]": "Requested"}

# Second register format (used since 2026-08-13 by the *-COMPLETE.txt archives and, from 2026-08-20, by
# the ACTIVE 20260820/20260821 registers): a numbered block instead of a "*" line.
#
#     ## Testing and quality
#     3. Time: 10:15:00
#        Request: <what was asked>
#        Result:  <what was delivered>
#        Status:  in progress
#
# Before 2026-08-22 the loop below only ever produced a requirement from a line starting with "*", so
# every one of these blocks was silently absent from the tab -- 33 of the 34 items across the four files
# using it, including the in-progress and deferred ones the user tracks. Parsed here rather than by
# rewriting the registers: the files are the user's record and AGENTS.md forbids reformatting them.
_CR_BLOCK = _re.compile(r"^\s*\d+\.\s+Time:\s*(.*)$")
_CR_FIELD = _re.compile(r"^\s*(Request|Result|Completion|Evidence|Status|Original request date)\s*:\s*(.*)$", _re.I)
_CR_MD_HEAD = _re.compile(r"^##\s+(\S.*?)\s*$")

# Free-text status words these blocks use -> the six statuses the tab counts. Longest key first, so
# "deferred external dependency" is not shadowed by "deferred". "out of scope" is deliberate,
# recorded non-delivery, so it lands on Cancelled rather than defaulting to Not Started.
_CR_WORD_STATUS = [
    ("deferred external dependency", "Deferred"),
    ("not started",   "Not Started"),
    ("in progress",   "In Progress"),
    ("out of scope",  "Cancelled"),
    ("withdrawn",     "Cancelled"),
    ("cancelled",     "Cancelled"),
    ("canceled",      "Cancelled"),
    ("completed",     "Completed"),
    ("complete",      "Completed"),
    ("deferred",      "Deferred"),
    ("requested",     "Requested"),
]


def _cr_word_status(value: str) -> str:
    """Map a bare 'Status: complete' value to the tab's vocabulary. Unknown -> Not Started."""
    v = " ".join((value or "").strip().lower().replace("-", " ").split())
    for word, status in _CR_WORD_STATUS:
        if v.startswith(word):
            return status
    return "Not Started"


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


def _cr_flush_block(block, reqs, area, scope):
    """Append an accumulated numbered block as a requirement. Returns None (the new 'no open block')."""
    if not block:
        return None
    req = (block.get("request") or "").strip()
    if req:
        note = " ".join(n for n in block.get("note", []) if n).strip()
        st = _cr_word_status(block.get("status", ""))
        deferred = (st == "Deferred")
        reqs.append({"row": len(reqs) + 1,
                     "text": req, "delivery_notes": note,
                     "working_area": area, "scope": _cr_scope(req, scope), "status": st,
                     "prange": (None if deferred else _cr_prange(req)),
                     "prioritised": (False if deferred else _cr_prioritised(req, area))})
    return None


def _cr_parse(path: str) -> dict:
    """Parse one ChangeRequests/*.txt into a summary + requirement list.

    Two register formats are supported (see _CR_BLOCK above):
      - a line whose trimmed form starts with '*', its status carried by a trailing '[Status]' marker;
      - a numbered 'N. Time: / Request: / Result: / Status:' block, its Working Area taken from the
        nearest preceding '## Heading'.
    For '*' lines the nearest preceding 'Application Focus - X' header is the Working Area and a
    '- NEW DELIVERY' suffix on it is the Scope. '## Heading' deliberately scopes ONLY numbered blocks,
    so adding block support left every previously-parsed row byte-identical."""
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
    block_area, block = "", None      # '## Heading' area + the numbered block being accumulated
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except Exception:
        lines = []
    for raw in lines:
        s = raw.strip()
        _h = _CR_MD_HEAD.match(raw)
        if _h:
            block = _cr_flush_block(block, reqs, block_area, scope)
            block_area = _h.group(1)
            continue
        _b = _CR_BLOCK.match(raw)
        if _b:
            block = _cr_flush_block(block, reqs, block_area, scope)
            block = {"request": "", "note": [], "status": "", "last": None}
            continue
        if block is not None:
            _f = _CR_FIELD.match(raw)
            if _f:
                key, val = _f.group(1).lower(), _f.group(2).strip()
                if key == "request":
                    block["request"], block["last"] = val, "request"
                elif key == "status":
                    block["status"], block["last"] = val, None
                elif key == "original request date":
                    block["last"] = None
                else:                                   # Result / Completion / Evidence
                    block["note"].append(val)
                    block["last"] = "note"
                continue
            if s and block["last"]:                     # wrapped continuation of the field above
                if block["last"] == "request":
                    block["request"] = (block["request"] + " " + s).strip()
                else:
                    block["note"][-1] = (block["note"][-1] + " " + s).strip()
                continue
        if s.startswith("Application Focus"):
            block = _cr_flush_block(block, reqs, block_area, scope)
            a = s.split("-", 1)[1].strip() if "-" in s else s
            if a.upper().endswith("NEW DELIVERY"):
                scope = "NEW DELIVERY"
                a = a[:-len("NEW DELIVERY")].rstrip(" -").strip()
            else:
                scope = ""
            area = a
            continue
        if s.startswith("*"):
            block = _cr_flush_block(block, reqs, block_area, scope)
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
            _st = _cr_status(raw)
            # A DEFERRED item is parked, so it carries NO priority — blank its band + prioritised flag
            # (user 2026-07-27, P-06). This also drops it from the priority-range counts/filter below.
            _deferred = (_st == "Deferred")
            reqs.append({"row": len(reqs) + 1,   # stable 1-based number so "#26" maps to a line (user 2026-07-18)
                         "text": req.strip(), "delivery_notes": note.strip(),
                         "working_area": area, "scope": _cr_scope(req, scope), "status": _st,
                         "prange": (None if _deferred else _cr_prange(req)),
                         "prioritised": (False if _deferred else _cr_prioritised(req, area))})
    _cr_flush_block(block, reqs, block_area, scope)   # the last block in the file has no successor to flush it
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
    resp = jsonify({"files": out})
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@app.route("/api/ig-status")
def api_ig_status():
    """Cheap 'does this user have IG credentials' check (user 2026-07-27, P-10 L218/L225 + P-25/30
    L219/L226). Drives the no-credentials warning + 'Open IG settings' button on My Pre-orders and
    Pre-orders-to-my-IG, WITHOUT building a live IG session (no network login) — reads the stored/env
    creds via ig_shim._resolve_ig_creds, owner-aware (owner falls back to env, non-owner needs own)."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    no_creds = True
    try:
        import ig_shim
        c = ig_shim._resolve_ig_creds(name)
        no_creds = not (c and c.get("api_key") and c.get("username") and c.get("password"))
    except Exception as e:
        log.warning(f"ig-status check failed for {name}: {e}")
    return jsonify({"no_creds": bool(no_creds)})


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
            out["no_creds"] = True   # drives the warning + "Open IG settings" button on the page (P-07 #91/#92)
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
    _snap_recs = _load_snapshot().get("records", [])
    tk2name = {r["ticker"]: (r.get("name") or "") for r in _snap_recs if r.get("ticker")}
    # Ticker -> Market map (user 2026-08-03, P-10): show Market next to Name on both IG Account tables.
    tk2market = {r["ticker"]: (r.get("market") or "") for r in _snap_recs if r.get("ticker")}

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
    # Audit trail (user 2026-08-03, P-25): record this user's IG account identity (name + number),
    # encrypted in Supabase. Deduped — only writes when the identity changes. Best-effort, never blocks.
    if acct_info.get("account_id") or acct_info.get("account_name"):
        _wu.record_ig_account_audit(name, acct_info.get("account_name", ""),
                                    acct_info.get("account_id", ""), source="ig_account_view", by=name)
    if not pos_ok and not ord_ok:
        out["note"] = "Could not read your IG account right now — try Refresh."
        return jsonify(out)
    if not pos_ok:
        out["note"] = "Open positions are unavailable right now — try Refresh."
    elif not ord_ok:
        out["note"] = "Working orders are unavailable right now — try Refresh."

    def _tk(epic, mk):   # short ticker for an epic (epic_lookup, else IG's instrument name, else epic)
        return epic2tk.get(str(epic)) or (mk.get("instrumentName") if mk else None) or epic or ""

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    for p in positions:
        pos, mk = (p.get("position") or {}), (p.get("market") or {})
        epic = mk.get("epic")
        tk = _tk(epic, mk)
        # Live P&L (user 2026-08-01): a BUY closes at the BID, a SELL at the OFFER. Profit per point =
        # size x contractSize; profit% is direction-aware against the open level. Best-effort — any
        # missing quote leaves profit None ("—" on the page).
        direction = pos.get("direction")
        open_lvl = _num(pos.get("level") or pos.get("openLevel"))
        size = _num(pos.get("size"))
        csize = _num(pos.get("contractSize")) or 1.0
        close = _num(mk.get("bid")) if direction == "BUY" else _num(mk.get("offer"))
        profit = profit_pct = None
        if open_lvl and close is not None and size is not None:
            pts = (close - open_lvl) if direction == "BUY" else (open_lvl - close)
            profit = round(pts * size * csize, 2)
            if open_lvl:
                profit_pct = round(pts / open_lvl * 100.0, 2)
        out["positions"].append({
            "ticker": tk, "name": tk2name.get(tk) or mk.get("instrumentName") or tk,
            "market": tk2market.get(tk) or "",   # Market column (user 2026-08-03, P-10)
            "epic": epic, "deal_id": pos.get("dealId"), "direction": direction,
            "size": pos.get("size"), "level": pos.get("level") or pos.get("openLevel"),
            "current": close, "profit": profit, "profit_pct": profit_pct,
            "currency": pos.get("currency"), "stop": pos.get("stopLevel"), "limit": pos.get("limitLevel"),
            "opened": str(pos.get("createdDateUTC") or pos.get("createdDate") or "")[:19]})
    for w in orders:
        od, mk = (w.get("workingOrderData") or {}), (w.get("marketData") or {})
        epic = od.get("epic") or mk.get("epic")
        tk = _tk(epic, mk)
        out["orders"].append({
            "ticker": tk, "name": tk2name.get(tk) or mk.get("instrumentName") or tk,
            "market": tk2market.get(tk) or "",   # Market column (user 2026-08-03, P-10)
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


@app.route("/api/ig-close-positions", methods=["POST"])
def api_ig_close_positions():
    """Close only explicitly confirmed, currently-open positions belonging to the acting web user."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    body = request.get_json(silent=True) or {}
    deal_ids = list(dict.fromkeys(str(v).strip() for v in (body.get("deal_ids") or []) if str(v).strip()))
    if not body.get("confirmed"):
        return jsonify({"error": "explicit confirmation is required; no position was closed"}), 400
    if not deal_ids:
        return jsonify({"error": "select at least one open position"}), 400
    if len(deal_ids) > 50:
        return jsonify({"error": "too many positions requested"}), 400
    try:
        import ig_shim
        if ig_shim.session_for(name) is None:
            return jsonify({"error": "no IG credentials for this user"}), 403
        results = []
        # Re-read the account under this user's own session: never trust a browser row or another user's ID.
        with ig_shim._IG_LOCK, ig_shim.acting_session(name):
            current = {str((p.get("position") or {}).get("dealId") or ""): p
                       for p in (ig_shim.get_open_positions() or [])}
            for deal_id in deal_ids:
                position = current.get(deal_id)
                if not position:
                    _append_ig_close_audit(name, deal_id, "rejected_preflight", "not currently open")
                    results.append({"deal_id": deal_id, "closed": False, "error": "not currently open"})
                    continue
                _append_ig_close_audit(name, deal_id, "submitted", "live position re-read matched")
                ok = bool(ig_shim.close_trade(deal_id, reason="WEB_USER_CONFIRMED"))
                outcome = ig_shim.last_close_outcome()
                broker_error = str(outcome.get("reason") or "")
                if not ok and not broker_error:
                    broker_error = "Internal close path returned no broker outcome; no IG confirmation was accepted."
                _append_ig_close_audit(name, deal_id, "confirmed" if ok else "not_closed",
                                       broker_error or "IG confirmation ACCEPTED")
                results.append({"deal_id": deal_id, "closed": ok,
                                "error": broker_error or ("IG did not confirm the close" if not ok else "")})
        closed = [r["deal_id"] for r in results if r["closed"]]
        for result in results:
            outcome = "IG confirmed closed" if result["closed"] else f"still open / failed: {result['error']}"
            _wu.log_event(name, f"User-confirmed IG close outcome for {result['deal_id']}: {outcome}")
        _append_batch("IG Account", f"User-confirmed close outcome: {len(closed)}/{len(results)} position(s) closed", by=name)
        return jsonify({"ok": bool(closed), "results": results,
                        "close_handler_version": "2026-08-21-audit-v2"})
    except Exception as exc:
        log.warning("ig user-confirmed close failed for %s: %s", name, exc)
        return jsonify({"error": "IG close request failed; refresh the account before retrying."}), 502


@app.route("/api/ig-close-audit")
def api_ig_close_audit():
    """Every close attempt this user has made, with the broker outcome that was recorded at the time.

    _append_ig_close_audit has written this durably since 2026-08-21, but nothing ever read it back, so
    the evidence existed only on disk: once the result dialog was dismissed or the page reloaded, a user
    had no way to see whether a position they asked to close actually closed (user 2026-08-22, deferred
    to 2026-08-23). Host-side and append-only, so it survives a Supabase outage — which is the point of
    an audit trail for a live broker action.

    A user sees their OWN attempts; an admin sees every user's, because that is what makes it an audit.
    """
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    everyone = bool(_wu.is_admin(name))
    try:
        limit = max(1, min(500, int(request.args.get("limit", "200"))))
    except (TypeError, ValueError):
        limit = 200
    entries = []
    try:
        with _IG_CLOSE_AUDIT_LOCK:
            try:
                with open(_IG_CLOSE_AUDIT_FILE, "r", encoding="utf-8") as fh:
                    lines = fh.readlines()
            except FileNotFoundError:
                lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue          # a torn final write must not hide the rest of the trail
            if not isinstance(entry, dict):
                continue
            if everyone or str(entry.get("user") or "") == name:
                entries.append(entry)
    except OSError as exc:
        log.warning("could not read the IG close audit for %s: %s", name, exc)
        return jsonify({"error": "The close history could not be read."}), 502
    entries.reverse()             # newest first
    return jsonify({"entries": entries[:limit], "total": len(entries), "scope": "all" if everyone else "mine"})


@app.route("/api/ig-closed")
def api_ig_closed():
    """Recently CLOSED trades for the acting user's IG account, each with the REASON it closed (user
    2026-08-03, P-10/P-12: "see closed trades e.g. China Mengniu and understand why"). Lazy-loaded by the
    IG Account tab's "Closed trades" reveal. Best-effort — returns a note (not an error) with no session."""
    name = _wu.name_for_token(request.headers.get("X-Auth") or "")
    if not name:
        return jsonify({"error": "login required"}), 401
    try:
        import ig_shim, datetime as _dt
        if ig_shim.session_for(name) is None:
            return jsonify({"trades": [], "note": "No IG credentials of your own — set them in Configuration → IG."})
        try:
            days = max(1, min(365, int(request.args.get("days", 365))))
        except Exception:
            days = 120
        frm = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        with ig_shim._IG_LOCK, ig_shim.acting_session(name):
            trades = ig_shim.get_closed_trades(frm) or []
        if trades:
            return jsonify({"trades": trades, "days": days})
        # IG can return an empty history during a transient/rate-limited read. Keep the account view
        # useful and explicit by showing the application's closed-trade ledger rather than claiming
        # that no closed trades exist.
        try:
            from db_pool import get_db
            db = get_db()
            try:
                rows = db.run(
                    "select ticker, direction, size, open_price, close_price, pnl, pnl_pct, "
                    "opened_at, closed_at, close_reason from trade_log "
                    "where closed_at >= :frm order by closed_at desc",
                    frm=frm) or []
            finally:
                db.close()
            fallback = []
            for tk, dr, sz, op, cp, pl, plp, oa, ca, reason in rows:
                fallback.append({
                    "date": ca.isoformat() if ca else "", "instrument": tk, "direction": dr,
                    "size": float(sz) if sz is not None else None,
                    "open_level": float(op) if op is not None else None,
                    "close_level": float(cp) if cp is not None else None,
                    "pnl": float(pl) if pl is not None else 0.0,
                    "pnl_pct": float(plp) if plp is not None else None,
                    "currency": "GBP", "reason": reason or "UNKNOWN"})
            if fallback:
                return jsonify({"trades": fallback, "days": days,
                                "note": "IG returned no closed trades; showing the application ledger fallback."})
        except Exception as fallback_ex:
            log.warning(f"ig-closed ledger fallback failed for {name}: {fallback_ex}")
        return jsonify({"trades": [], "days": days,
                        "note": "IG returned no closed trades for this period. The result may be incomplete; use Refresh to retry."})
    except Exception as e:
        log.warning(f"ig-closed failed for {name}: {e}")
        return jsonify({"trades": [], "note": "Could not read your closed trades right now — try again."})


def _perf_warm_loop():
    """Pre-compute the Performance caches OFF the request path (user 2026-08-03). The 12-month replay +
    per-trigger VolumeScore take ~35s cold — after the 5-year backfill that used to hang /api/performance
    on the first click ("processing the 12-month replay…" forever). This warms them at startup and every
    _PERF_WARM_INTERVAL (< the payload TTL), so a click always hits a warm cache (~0.3s) and the heavy build
    never runs synchronously under a user. The user asked for exactly this: process the data ahead of time
    (it's ready after the morning refresh), don't wait for the tab to be clicked."""
    import time as _t
    while True:
        if _claim_perf_warm():
            try:
                t0 = _t.time()
                _build_perf_payload()      # builds _sqa_all_rows + VolumeScore + the payload, into _PERF_CACHE
                log.info(f"performance caches warmed in {_t.time() - t0:.1f}s")
            except Exception as e:
                log.warning(f"performance warm failed: {e}")
            finally:
                _finish_perf_warm()
        _t.sleep(_PERF_WARM_INTERVAL)


if __name__ == "__main__":
    import threading
    try:
        # Supabase-backed encrypted secret store (task #53). DUAL-READ: fills only env vars not already
        # set from .env, so behaviour is unchanged now but pruning .env later keeps the app working.
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        import app_secrets
        app_secrets.load_secrets_into_env()
    except Exception as _e:
        log.warning(f"app_secrets load skipped: {_e}")
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
    # Production Scanner builds run on the external worker and publish to Supabase. Keep the legacy local
    # loop opt-in for isolated development only; CGI/WSGI hosts must never spend web CPU rebuilding data.
    if os.environ.get("HVF_ENABLE_LOCAL_SNAPSHOT_REBUILD", "").strip().lower() in ("1", "true", "yes"):
        threading.Thread(target=_refresh_loop, daemon=True).start()
    threading.Thread(target=_bridge_loop, daemon=True).start()
    threading.Thread(target=_perf_warm_loop, daemon=True).start()   # keep Performance caches warm (user 2026-08-03)
    web_port = int(os.environ.get("HVF_WEB_PORT", "5057"))
    log.info(f"HVF site on http://127.0.0.1:{web_port}  (local development instance)")
    app.run(host="0.0.0.0", port=web_port, debug=False, threaded=True)
