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

# Roles (user 2026-07-03). admin = full access incl. user maintenance, admin tabs, shared config,
# data refresh. gold = read/write incl. pre-orders + monitor exec. silver = read/write incl.
# pre-orders. guest = read-only (incl. configuration), no pre-orders.
ROLES = ["admin", "gold", "silver", "guest"]
_SEED_ADMINS = {"Alex", "Rich"}
_DEFAULT_ROLE = "guest"


def _default_role(name: str) -> str:
    return "admin" if name in _SEED_ADMINS else _DEFAULT_ROLE


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
                               "email": email, "secrets": {},
                               "role": _default_role(name), "enabled": True}   # LOCKED until reset
                changed = True
                log.info(f"web_users: '{name}' seeded LOCKED - set a password via the reset (email) flow")
        # Backfill role/enabled on any legacy login record (not the "__app__" secrets record).
        for n, u in users.items():
            if isinstance(u, dict) and "pwd_hash" in u:
                if "role" not in u:
                    u["role"] = _default_role(n); changed = True
                if "enabled" not in u:
                    u["enabled"] = True; changed = True
        if changed:
            _save(users)
            log.info(f"web_users seeded ({_USERS_FILE})")
        return users


def verify(name: str, pwd: str) -> bool:
    u = _ensure_seeded().get(name)
    if not u or not u.get("enabled", True):     # disabled accounts cannot log in (user 2026-07-03)
        return False
    return _secrets.compare_digest(u["pwd_hash"], _hash_pwd(pwd or "", u["salt"]))


# ── Roles & account status (user 2026-07-03) ─────────────────────────────────────────────────────────
def get_role(name: str) -> str:
    u = _ensure_seeded().get(name)
    return (u or {}).get("role", _default_role(name))


def is_admin(name: str) -> bool:
    return get_role(name) == "admin"


def is_enabled(name: str) -> bool:
    return bool((_ensure_seeded().get(name) or {}).get("enabled", True))


def list_users() -> list:
    """[{name, email, role, enabled}] for the user-maintenance area (admin only)."""
    return [{"name": n, "email": u.get("email", ""), "role": u.get("role", _default_role(n)),
             "enabled": bool(u.get("enabled", True))}
            for n, u in _ensure_seeded().items() if isinstance(u, dict) and "pwd_hash" in u]


def set_role(name: str, role: str) -> bool:
    if role not in ROLES:
        return False
    with _LOCK:
        users = _load()
        u = users.get(name)
        if not u or "pwd_hash" not in u:
            return False
        u["role"] = role
        _save(users)
    return True


def set_enabled(name: str, enabled: bool) -> bool:
    with _LOCK:
        users = _load()
        u = users.get(name)
        if not u or "pwd_hash" not in u:
            return False
        u["enabled"] = bool(enabled)
        _save(users)
    return True


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
    Activity tab. Capped at the most recent 100 entries. Never raises."""
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
    u = _ensure_seeded().get(name)
    return list(reversed((u or {}).get("log") or []))   # newest first


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


def add_user(name: str, pwd: str, email: str):
    with _LOCK:
        users = _load()
        salt = _secrets.token_hex(16)
        users[name] = {"salt": salt, "pwd_hash": _hash_pwd(pwd, salt), "email": email, "secrets": {}}
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
