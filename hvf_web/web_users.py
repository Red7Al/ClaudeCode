# ======================================================================================================================
# File:         hvf_web/web_users.py
# Author:       Alex Hind
# Created:      2026-06-30
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Secure user store for the Squeeze web app (user 2026-06-30: "user specific settings are private as we will add IG
# credentials - they must be securely stored").
#
#   - Users live in data/web_users.json — OUTSIDE git (data/ is gitignored). No credentials in source.
#   - Passwords are NEVER stored: PBKDF2-HMAC-SHA256 (200k iterations, per-user random salt) hashes only.
#   - Future per-user secrets (IG API key/username/password) go through set_secret/get_secret, encrypted with a
#     Fernet key at data/.web_users.key (auto-created 0-byte-safe, gitignored). Lose the key = secrets unrecoverable.
#   - Password reset requires the email REGISTERED to the account (reset_password checks it, case-insensitive).
#   - Login tokens = sha256(name : pwd_hash : salt), so a password change invalidates old tokens automatically.
#
# A fresh store seeds the two named accounts LOCKED (no password anywhere in git); each owner sets
# their password through the email-gated reset. To add a user later (run locally, never commit creds):
#   python -c "from hvf_web.web_users import add_user; add_user('Name','pwd','email@x.com')"
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.3.0   2026-07-03  Alex Hind   (user 2026-07-03) get_settings/set_settings — per-user plain preferences (filter
#                                 defaults for the Config tab); secrets remain in the encrypted store.
# 1.2.0   2026-06-30  Alex Hind   (user 2026-06-30) Per-user operational log (log_event/get_log, capped 100, shown in
#                                 the web app's Activity tab); password-change email to the registered address with a
#                                 not-you warning (trade_email.send_simple_email); name_for_token for per-user APIs.
# 1.1.0   2026-06-30  Alex Hind   (user 2026-06-30 "Passwords must NOT be in GIT") Seed carries names+emails only;
#                                 fresh accounts start LOCKED and are activated via the email-gated reset flow.
# 1.0.0   2026-06-30  Alex Hind   Initial build — PBKDF2 password store, Fernet secret store, email-gated reset.
# ======================================================================================================================

import base64
import hashlib
import json
import logging
import os
import secrets as _secrets
import threading

log = logging.getLogger("web_users")

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_USERS_FILE = os.path.join(_DATA_DIR, "web_users.json")
_FERNET_KEY_FILE = os.path.join(_DATA_DIR, ".web_users.key")
_PBKDF2_ITERS = 200_000
_LOCK = threading.Lock()

# First-run seed — NAMES + EMAILS ONLY. Passwords are NEVER in source/git (user 2026-06-30): a fresh
# store seeds each account LOCKED (random unusable password); the owner sets a real password via the
# email-gated "Forgot password?" flow. An existing data/web_users.json is left untouched.
_SEED = [
    ("Alex", "eahind@yahoo.co.uk"),
    ("Rich", "richard.williams@aztecsolarenergy.co.uk"),
]

# Access model (user 2026-07-03): two independent axes.
#   admin (bool)  — full access: user maintenance, admin tabs, shared config, data refresh.
#   subscription  — gold: read/write incl. pre-orders + monitor exec · silver: read/write incl.
#                   pre-orders · guest: read-only (incl. configuration), no pre-orders.
SUBSCRIPTIONS = ["gold", "silver", "guest"]
_SEED_ADMINS = {"Alex", "Rich"}


def _default_admin(name: str) -> bool:
    return name in _SEED_ADMINS


def _default_subscription(name: str) -> str:
    return "gold" if name in _SEED_ADMINS else "guest"   # new accounts default to guest, no admin


def _hash_pwd(pwd: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pwd.encode(), bytes.fromhex(salt), _PBKDF2_ITERS).hex()


def _load() -> dict:
    try:
        with open(_USERS_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(users: dict):
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(users, fh, indent=2)
    os.replace(tmp, _USERS_FILE)


def _ensure_seeded() -> dict:
    with _LOCK:
        users = _load()
        changed = False
        for name, email in _SEED:
            if name not in users:
                salt = _secrets.token_hex(16)
                users[name] = {"salt": salt, "pwd_hash": _hash_pwd(_secrets.token_hex(24), salt),
                               "email": email, "secrets": {}, "admin": _default_admin(name),
                               "subscription": _default_subscription(name), "enabled": True}
                changed = True
                log.info(f"web_users: '{name}' seeded LOCKED - set a password via the reset (email) flow")
        # Backfill admin/subscription/enabled; migrate the old single 'role' field if present.
        for n, u in users.items():
            if isinstance(u, dict) and "pwd_hash" in u:
                old = u.pop("role", None)     # legacy: role held admin OR a subscription level
                if "admin" not in u:
                    u["admin"] = (old == "admin") or _default_admin(n); changed = True
                if "subscription" not in u:
                    u["subscription"] = old if old in SUBSCRIPTIONS else _default_subscription(n); changed = True
                if "enabled" not in u:
                    u["enabled"] = True; changed = True
                if old is not None:
                    changed = True
        if changed:
            _save(users)
            log.info(f"web_users seeded ({_USERS_FILE})")
        return users


def verify(name: str, pwd: str) -> bool:
    u = _ensure_seeded().get(name)
    if not u or not u.get("enabled", True):     # disabled accounts cannot log in (user 2026-07-03)
        return False
    return _secrets.compare_digest(u["pwd_hash"], _hash_pwd(pwd or "", u["salt"]))


# ── Admin flag, subscription & account status (user 2026-07-03) ───────────────────────────────────────
def is_admin(name: str) -> bool:
    u = _ensure_seeded().get(name)
    return bool((u or {}).get("admin", _default_admin(name)))


def get_subscription(name: str) -> str:
    u = _ensure_seeded().get(name)
    return (u or {}).get("subscription", _default_subscription(name))


def is_enabled(name: str) -> bool:
    return bool((_ensure_seeded().get(name) or {}).get("enabled", True))


def list_users() -> list:
    """[{name, email, admin, subscription, enabled}] for the user-maintenance area (admin only)."""
    return [{"name": n, "email": u.get("email", ""), "admin": bool(u.get("admin", _default_admin(n))),
             "subscription": u.get("subscription", _default_subscription(n)),
             "enabled": bool(u.get("enabled", True))}
            for n, u in _ensure_seeded().items() if isinstance(u, dict) and "pwd_hash" in u]


def _set_field(name: str, field: str, value) -> bool:
    with _LOCK:
        users = _load()
        u = users.get(name)
        if not u or "pwd_hash" not in u:
            return False
        u[field] = value
        _save(users)
    return True


def set_admin(name: str, admin: bool) -> bool:
    return _set_field(name, "admin", bool(admin))


def set_subscription(name: str, sub: str) -> bool:
    return sub in SUBSCRIPTIONS and _set_field(name, "subscription", sub)


def set_enabled(name: str, enabled: bool) -> bool:
    return _set_field(name, "enabled", bool(enabled))


def token_for(name: str) -> str:
    """Session token — derived from the pwd hash, so changing the password rotates it."""
    u = _ensure_seeded().get(name)
    if not u or "pwd_hash" not in u:          # e.g. the "__app__" secrets record — not a login
        return ""
    return hashlib.sha256(f"{name}:{u['pwd_hash']}:{u['salt']}".encode()).hexdigest()


def _login_names(users: dict = None) -> list:
    """Real login accounts (excludes the "__app__" shared-secrets record)."""
    return [n for n, u in (users or _ensure_seeded()).items()
            if isinstance(u, dict) and "pwd_hash" in u]


def valid_tokens() -> set:
    return {token_for(n) for n in _login_names()}


def log_event(name: str, event: str):
    """Append to the user's operational log (user 2026-06-30) — shown to THAT user only in the
    Activity tab. Writes to Supabase (user 2026-07-03: data rows -> DB), falling back to the local
    JSON store if the DB is unavailable. Never raises."""
    try:
        import web_store
        if web_store.append_activity(name, event):
            return
    except Exception as e:
        log.warning(f"activity DB write failed for {name} ({e}); using local store")
    from datetime import datetime, timezone
    try:
        with _LOCK:
            users = _load()
            u = users.get(name)
            if not u:
                return
            u.setdefault("log", []).append(
                {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), "event": event})
            u["log"] = u["log"][-100:]
            _save(users)
    except Exception as e:
        log.warning(f"log_event failed for {name}: {e}")


def get_log(name: str) -> list:
    try:
        import web_store
        rows = web_store.list_activity(name)
        if rows:
            return rows                                  # already newest-first
    except Exception as e:
        log.warning(f"activity DB read failed for {name} ({e}); using local store")
    u = _ensure_seeded().get(name)
    return list(reversed((u or {}).get("log") or []))   # newest first (local fallback)


def name_for_token(token: str) -> str:
    for n in _login_names():
        if token and token == token_for(n):
            return n
    return ""


def _send_pwd_change_email(name: str, email: str):
    """Notify the registered address that the password changed, with a NOT-YOU warning
    (user 2026-06-30). Best-effort — an email failure never blocks the reset."""
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subject = "Squeeze Scanner - your password was changed"
    text = (f"Hello {name},\n\n"
            f"The password on your Squeeze Scanner account was changed at {ts}.\n\n"
            f"If YOU made this change, no action is needed.\n\n"
            f"IF YOU DID NOT CHANGE IT, your account may be compromised - act now:\n"
            f"  1. Open the Squeeze Scanner login page and click 'Forgot password?'.\n"
            f"  2. Enter your account name and THIS registered email address, and set a new password\n"
            f"     immediately (this locks out whoever changed it).\n"
            f"  3. Tell the operator (Alex) so the activity log can be reviewed.\n\n"
            f"This notification is sent to the registered address on every password change.\n")
    try:
        from trade_email import send_simple_email
        if send_simple_email(subject, text, recipients=[email]):
            log.info(f"password-change email sent to {email}")
            return
        log.info("Resend/Yahoo not configured - trying the local Gmail fallback")
    except Exception as e:
        log.warning(f"send_simple_email failed for {name}: {e}")
    # Local fallback: the _TVW_EMAIL Gmail creds present on this machine (Actions uses Resend/Yahoo).
    try:
        import smtplib
        from email.message import EmailMessage
        user = os.environ.get("_TVW_EMAIL_USERNAME", "").strip()
        pw = os.environ.get("_TVW_EMAIL_PASSWORD", "").replace(" ", "").strip()
        if not (user and pw):
            log.warning(f"password-change email NOT sent to {email} (no provider configured)")
            return
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, user, email
        msg.set_content(text)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(user, pw)
            s.send_message(msg)
        log.info(f"password-change email sent to {email} (Gmail fallback)")
    except Exception as e:
        log.warning(f"password-change email failed for {name}: {e}")


def _send_reset_code_email(name: str, email: str, code: str) -> bool:
    """Email a one-time reset code to the registered address (user 2026-07-03). Best-effort."""
    subject = "Squeeze Scanner - your password reset code"
    text = (f"Hello {name},\n\n"
            f"Your password reset code is:  {code}\n\n"
            f"Enter it in the app within 10 minutes to set a new password. It can be used once.\n\n"
            f"If you did NOT request this, ignore this email — your password is unchanged.\n")
    try:
        from trade_email import send_simple_email
        if send_simple_email(subject, text, recipients=[email]):
            return True
    except Exception as e:
        log.warning(f"reset-code email via provider failed: {e}")
    try:    # local Gmail fallback (same as the change-notification email)
        import smtplib
        from email.message import EmailMessage
        user = os.environ.get("_TVW_EMAIL_USERNAME", "").strip()
        pw = os.environ.get("_TVW_EMAIL_PASSWORD", "").replace(" ", "").strip()
        if not (user and pw):
            return False
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, user, email
        msg.set_content(text)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(user, pw)
            s.send_message(msg)
        return True
    except Exception as e:
        log.warning(f"reset-code email failed for {name}: {e}")
        return False


def request_reset_code(name: str, email: str) -> bool:
    """Generate a 6-digit reset code, store its hash with a 10-minute expiry, and email it to the
    REGISTERED address (user 2026-07-03). Returns True only when the email was actually sent (name +
    registered email matched and delivery succeeded). Callers should show a generic message either way
    to avoid account enumeration."""
    import time as _t
    with _LOCK:
        users = _load()
        u = users.get((name or "").strip())
        if not u or (email or "").strip().lower() != (u.get("email") or "").lower():
            return False
        code = f"{_secrets.randbelow(1000000):06d}"
        u["reset"] = {"code_hash": _hash_pwd(code, u["salt"]), "expires": _t.time() + 600, "attempts": 0}
        _save(users)
    ok = _send_reset_code_email(name, u.get("email") or email, code)
    if ok:
        log.info(f"reset code emailed to {name}")
        log_event(name, "Password reset code requested (emailed)")
    return ok


def reset_password(name: str, email: str, new_pwd: str, ip: str = "") -> bool:
    """Reset gated on the REGISTERED email (user 2026-06-30). False when the account is unknown,
    the email doesn't match, or the new password is too short. On success: logs the event to the
    user's operational log and emails the registered address with a not-you warning."""
    if not new_pwd or len(new_pwd) < 4:
        return False
    name = (name or "").strip()
    with _LOCK:
        users = _load()
        u = users.get(name)
        if not u or (email or "").strip().lower() != (u.get("email") or "").lower():
            return False
        u["salt"] = _secrets.token_hex(16)
        u["pwd_hash"] = _hash_pwd(new_pwd, u["salt"])
        _save(users)
    log.info(f"password reset for {name}")
    log_event(name, f"Password changed (email-verified reset{', from ' + ip if ip else ''})")
    _send_pwd_change_email(name, u.get("email") or email)
    return True


def reset_password_with_code(name: str, code: str, new_pwd: str, ip: str = "") -> tuple:
    """Reset the password using the one-time CODE emailed by request_reset_code (user 2026-07-03).
    Checks the code hash, 10-minute expiry and a 5-attempt limit. Single-use. Returns (ok, error)."""
    import time as _t
    if not new_pwd or len(new_pwd) < 4:
        return False, "new password too short (min 4 characters)"
    name = (name or "").strip()
    with _LOCK:
        users = _load()
        u = users.get(name)
        if not u:
            return False, "invalid name or code"
        rst = u.get("reset") or {}
        if not rst or _t.time() > rst.get("expires", 0):
            u.pop("reset", None); _save(users)
            return False, "no code requested, or it has expired — request a new one"
        if rst.get("attempts", 0) >= 5:
            u.pop("reset", None); _save(users)
            return False, "too many attempts — request a new code"
        if not _secrets.compare_digest(rst.get("code_hash", ""), _hash_pwd((code or "").strip(), u["salt"])):
            rst["attempts"] = rst.get("attempts", 0) + 1
            u["reset"] = rst; _save(users)
            return False, "incorrect code"
        # Valid — set the new password (new salt), invalidate the code and all existing sessions.
        u["salt"] = _secrets.token_hex(16)
        u["pwd_hash"] = _hash_pwd(new_pwd, u["salt"])
        u.pop("reset", None)
        _save(users)
    log.info(f"password reset (code) for {name}")
    log_event(name, f"Password changed (email-code reset{', from ' + ip if ip else ''})")
    _send_pwd_change_email(name, u.get("email") or "")
    return True, ""


# ── Account requests (user 2026-07-03): the public can REQUEST an account; an admin approves. ─────────
_REQ_KEY = "__requests__"


def add_request(name: str, email: str, note: str = "") -> bool:
    """Store a public account request (unauthenticated). Deduped by name; capped. Never creates a login."""
    from datetime import datetime, timezone
    name = (name or "").strip()
    email = (email or "").strip()
    if not name or "@" not in email:
        return False
    with _LOCK:
        users = _load()
        if name in users:                       # a real login already has this name
            return False
        reqs = users.get(_REQ_KEY)
        if not isinstance(reqs, list):
            reqs = []
        if any(r.get("name") == name for r in reqs) or len(reqs) >= 100:
            return False
        reqs.append({"name": name, "email": email, "note": (note or "")[:200],
                     "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")})
        users[_REQ_KEY] = reqs
        _save(users)
    log.info(f"account request stored: {name}")
    return True


def list_requests() -> list:
    r = _load().get(_REQ_KEY)
    return list(r) if isinstance(r, list) else []


def _remove_request(name: str, users: dict):
    reqs = users.get(_REQ_KEY)
    if isinstance(reqs, list):
        users[_REQ_KEY] = [r for r in reqs if r.get("name") != name]


def approve_request(name: str) -> bool:
    """Approve a request → create a LOCKED (no usable password), enabled, guest, non-admin account.
    The new user sets their password via the email-gated 'Forgot password?' flow."""
    with _LOCK:
        users = _load()
        reqs = users.get(_REQ_KEY) or []
        req = next((r for r in reqs if r.get("name") == name), None)
        if not req or name in users:
            return False
        salt = _secrets.token_hex(16)
        users[name] = {"salt": salt, "pwd_hash": _hash_pwd(_secrets.token_hex(24), salt),
                       "email": req.get("email", ""), "secrets": {}, "admin": False,
                       "subscription": "guest", "enabled": True}
        _remove_request(name, users)
        _save(users)
    log.info(f"account request approved: {name}")
    return True


def reject_request(name: str) -> bool:
    with _LOCK:
        users = _load()
        _remove_request(name, users)
        _save(users)
    return True


def add_user(name: str, pwd: str, email: str, admin: bool = False, subscription: str = "guest"):
    """Create a login. New accounts default to NO admin, guest subscription (user 2026-07-03)."""
    with _LOCK:
        users = _load()
        salt = _secrets.token_hex(16)
        users[name] = {"salt": salt, "pwd_hash": _hash_pwd(pwd, salt), "email": email, "secrets": {},
                       "admin": bool(admin),
                       "subscription": subscription if subscription in SUBSCRIPTIONS else "guest",
                       "enabled": True}
        _save(users)


# ── Per-user application settings (Config tab, user 2026-07-03) — plain (non-secret) preferences ──────
def get_settings(name: str) -> dict:
    u = _ensure_seeded().get(name)
    return dict((u or {}).get("settings") or {})


def set_settings(name: str, settings: dict) -> bool:
    with _LOCK:
        users = _load()
        u = users.get(name)
        if not u:
            return False
        u["settings"] = settings or {}
        _save(users)
    return True


# ── Per-user encrypted secrets (for the coming IG credentials) ────────────────────────────────────────
def _fernet():
    from cryptography.fernet import Fernet
    if not os.path.exists(_FERNET_KEY_FILE) or os.path.getsize(_FERNET_KEY_FILE) == 0:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_FERNET_KEY_FILE, "wb") as fh:
            fh.write(Fernet.generate_key())
        log.info(f"web_users Fernet key created ({_FERNET_KEY_FILE}) — back it up; without it secrets are lost")
    with open(_FERNET_KEY_FILE, "rb") as fh:
        return Fernet(fh.read())


def set_secret(name: str, key: str, value: str) -> bool:
    """Store an encrypted per-user secret (e.g. 'ig_api_key'). Fernet-encrypted at rest."""
    f = _fernet()
    with _LOCK:
        users = _load()
        u = users.get(name)
        if not u:
            return False
        u.setdefault("secrets", {})[key] = base64.b64encode(f.encrypt(value.encode())).decode()
        _save(users)
    log_event(name, f"Secure setting '{key}' updated")
    return True


def get_secret(name: str, key: str) -> str:
    u = _ensure_seeded().get(name)
    if not u or key not in (u.get("secrets") or {}):
        return ""
    try:
        return _fernet().decrypt(base64.b64decode(u["secrets"][key])).decode()
    except Exception as e:
        log.warning(f"secret decrypt failed for {name}/{key}: {e}")
        return ""


# ── Application-level (shared) secrets — Supabase / X / Slack / Other (user 2026-07-03) ────────────────
# One trading system, so these are shared: stored ONCE (under the "__app__" record), Fernet-encrypted.
# The owner (Alex) may edit them; everyone else sees them masked, read-only.
_APP_KEY = "__app__"


def set_app_secret(key: str, value: str) -> bool:
    f = _fernet()
    with _LOCK:
        users = _load()
        rec = users.setdefault(_APP_KEY, {"secrets": {}})
        rec.setdefault("secrets", {})[key] = base64.b64encode(f.encrypt(value.encode())).decode()
        _save(users)
    return True


def get_app_secret(key: str) -> str:
    rec = _load().get(_APP_KEY) or {}
    if key not in (rec.get("secrets") or {}):
        return ""
    try:
        return _fernet().decrypt(base64.b64decode(rec["secrets"][key])).decode()
    except Exception as e:
        log.warning(f"app secret decrypt failed for {key}: {e}")
        return ""
