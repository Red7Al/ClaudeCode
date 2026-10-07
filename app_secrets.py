# ======================================================================================================================
# File:         app_secrets.py
# Author:       Alex Hind (via Claude)
# Created:      2026-08-01
#
# Supabase-backed ENCRYPTED secret store (task #53, design in docs/SUPABASE_SECRET_STORE_DESIGN.md).
#
# One Fernet-encrypted `app_secrets` table is the single source of truth for API keys / webhooks / IG creds.
# At process start, load_secrets_into_env() decrypts every row into os.environ, so the many modules that read
# os.environ directly (db_pool, ig_shim, notify, ...) keep working unchanged.
#
# DUAL-READ (this phase): load_secrets_into_env() only fills keys NOT already set, so .env still wins and
# behaviour is unchanged until .env is pruned. Fail-open everywhere — a store/decrypt error never silences or
# breaks the caller; it just leaves os.environ as-is.
#
# The master key comes from APP_SECRET_KEY (comma-separated for MultiFernet rotation). If unset, it falls back
# to the existing local key (data/.web_users.key via web_users._fernet -- repo root, NOT hvf_web/data), so this works locally today
# with no new key to manage; set APP_SECRET_KEY as a GitHub Secret for the Actions runtime.
# ======================================================================================================================

import base64
import logging
import os
import time

log = logging.getLogger("app_secrets")

_CACHE = {"ts": 0.0, "map": None}   # decrypted {key: value}
_TTL = 300                          # seconds


class NoDecryptionKey(RuntimeError):
    """No usable master key is configured, so nothing in app_secrets can be read."""


def _fernet():
    """MultiFernet from APP_SECRET_KEY (comma-separated, newest first for rotation); else the existing local
    web_users Fernet key so Phase 1-2 works with no new key to provision.

    RAISES NoDecryptionKey RATHER THAN MINTING ONE (2026-10-07). web_users._fernet() GENERATES a fresh key
    when its file is absent (web_users.py, "key created -- back it up"), which is right for a first local
    install and wrong everywhere else: a brand-new random key decrypts nothing, so every existing row fails
    with InvalidToken. On a GitHub Actions runner the key file never exists -- it is a secret, so it is not
    in the repo -- and APP_SECRET_KEY was never added as a GitHub Secret (seed-secrets.yml names adding it
    as the one manual prerequisite). MEASURED on run 37618531630, 2026-10-07: all 12 rows logged
    "decrypt failed", none succeeded, each with an empty exception message -- the InvalidToken signature.
    Every scheduled job had been doing that on every run.

    Asking for the key file's existence BEFORE importing web_users is the whole point: importing and
    calling it is what creates the key, so the check has to come first or it cannot fail."""
    from cryptography.fernet import Fernet, MultiFernet
    keys = os.environ.get("APP_SECRET_KEY", "").strip()
    if keys:
        return MultiFernet([Fernet(k.strip().encode()) for k in keys.split(",") if k.strip()])
    # WEB_USERS_FERNET_KEY counts as a real key: web_users._fernet() reads it BEFORE it looks for the file,
    # and IONOS is given exactly that and no data/ directory (build_ionos_package.py excludes "data";
    # migrate_runtime_state_to_supabase.py: "IONOS must receive WEB_USERS_FERNET_KEY as an environment
    # secret"). Testing only for the file would refuse on the live web tier, where decryption works today.
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", ".web_users.key")
    have_file = os.path.exists(key_file) and os.path.getsize(key_file) > 0
    if not os.environ.get("WEB_USERS_FERNET_KEY", "").strip() and not have_file:
        raise NoDecryptionKey(
            "no APP_SECRET_KEY, no WEB_USERS_FERNET_KEY and no local key file; set APP_SECRET_KEY to the "
            "contents of data/.web_users.key (see .github/workflows/seed-secrets.yml) -- os.environ is "
            "left untouched")
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "hvf_web"))
    from web_users import _fernet as _wu_fernet
    return _wu_fernet()


def _db():
    from db_pool import get_db
    return get_db()


def ensure_schema():
    db = _db()
    try:
        db.run("""create table if not exists app_secrets (
                    key         text primary key,
                    ciphertext  text not null,
                    updated_by  text,
                    updated_at  timestamptz default now())""")
    finally:
        db.close()


def _load_all() -> dict:
    """Decrypt every row -> {key: value}. Cached _TTL secs. Fail-open (returns {} on any error)."""
    now = time.time()
    if _CACHE["map"] is not None and now - _CACHE["ts"] < _TTL:
        return _CACHE["map"]
    out = {}
    try:
        f = _fernet()
        db = _db()
        try:
            rows = db.run("select key, ciphertext from app_secrets") or []
        finally:
            db.close()
        for k, ct in rows:
            try:
                out[k] = f.decrypt(base64.b64decode(ct)).decode()
            except Exception as e:
                log.warning(f"app_secrets: decrypt failed for {k}: {e}")
    except Exception as e:
        log.warning(f"app_secrets: load failed ({e}) — leaving os.environ untouched (dual-read)")
    _CACHE.update(ts=now, map=out)
    return out


def get_secret(name: str, default: str = "") -> str:
    """Single value: os.environ first (bootstrap / back-compat), else the decrypted store."""
    v = os.environ.get(name)
    if v not in (None, ""):
        return v
    return _load_all().get(name, default)


def set_secret(name: str, value: str, updated_by: str = "") -> bool:
    """Fernet-encrypt + upsert into app_secrets. Invalidates the cache. Never raises."""
    try:
        f = _fernet()
        ct = base64.b64encode(f.encrypt(value.encode())).decode()
        db = _db()
        try:
            db.run("""insert into app_secrets (key, ciphertext, updated_by, updated_at)
                      values (:k, :c, :b, now())
                      on conflict (key) do update set ciphertext = :c, updated_by = :b, updated_at = now()""",
                   k=name, c=ct, b=updated_by)
        finally:
            db.close()
        _CACHE["map"] = None
        return True
    except Exception as e:
        log.error(f"app_secrets: set failed for {name}: {e}")
        return False


def load_secrets_into_env(override: bool = False) -> int:
    """Decrypt the store into os.environ. DUAL-READ: only fills keys NOT already set unless override=True.
    Call ONCE at startup AFTER the bootstrap creds are present and BEFORE db_pool/ig_shim/notify read their
    values. Returns the count set. Never raises."""
    n = 0
    try:
        for k, v in _load_all().items():
            if override or os.environ.get(k) in (None, ""):
                os.environ[k] = v
                n += 1
    except Exception as e:
        log.warning(f"app_secrets: load_secrets_into_env failed: {e}")
    if n:
        log.info(f"app_secrets: loaded {n} secret(s) from Supabase into env (override={override})")
    return n
