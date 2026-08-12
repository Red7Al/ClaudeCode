# ======================================================================================================================
# File:         web_store.py
# Author:       Alex Hind
# Created:      2026-07-04
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Supabase-backed store for the web app's row data (user 2026-07-03: "move all JSON files with data rows to supabase").
# Replaces the local JSON files for:
#   - batch_activity  (cron-job.org / Refresh-button execution log)   -> table web_batch_activity
#   - activity_log    (per-user operational log: logins, config, etc.) -> table web_activity_log
#   - small durable JSON stores (users/reference caches/overrides/version fallback) -> table web_json_store
# Version History remains derived live from git where available, and snapshot.json stays a local render cache.
#
# Fails soft: if Supabase is unavailable, reads/writes degrade gracefully (empty list / no-op) and log a warning —
# the web app keeps working. One-off migration helpers import the legacy JSON rows.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-07-04  Alex Hind   Initial build — web_batch_activity + web_activity_log tables, append/list, migration.
# ======================================================================================================================

import hashlib
import json
import logging
import threading

log = logging.getLogger("web_store")

_DDL = [
    """create table if not exists web_batch_activity (
        id bigserial primary key, ts timestamptz not null default now(),
        source text, event text, by_user text)""",
    "create index if not exists idx_web_batch_ts on web_batch_activity (ts desc)",
    """create table if not exists web_activity_log (
        id bigserial primary key, ts timestamptz not null default now(),
        user_id text not null, event text)""",
    "create index if not exists idx_web_activity_user on web_activity_log (user_id, ts desc)",
    """create table if not exists web_best_settings_history (
        id bigserial primary key, ts timestamptz not null default now(),
        user_id text not null, snapshot_day date not null default current_date,
        dataset_generated text, data_through text,
        model_json jsonb not null, options_json jsonb not null, fingerprint text not null,
        unique (user_id, snapshot_day))""",
    "create index if not exists idx_web_best_settings_user on web_best_settings_history (user_id, snapshot_day desc)",
    # IG account audit trail (user 2026-08-03, P-25): append-only ENCRYPTED history of each user's IG
    # account identity. This store holds only CIPHERTEXT (the *_enc columns) — the Fernet encrypt/decrypt
    # lives in web_users, which owns the key. account_number_last3 is cleartext for masked display.
    """create table if not exists web_ig_account_audit (
        id bigserial primary key, ts timestamptz not null default now(),
        user_id text not null, account_name_enc text, account_number_enc text,
        account_number_last3 text, source text, by_user text)""",
    "create index if not exists idx_web_ig_audit_user on web_ig_account_audit (user_id, ts desc)",
    # Small, low-write durable stores formerly held only in local JSON files. One JSONB document per logical
    # store keeps migration non-destructive and preserves the existing file shape for compatibility fallbacks.
    """create table if not exists web_json_store (
        store_key text primary key, payload jsonb not null,
        revision bigint not null default 1,
        updated_at timestamptz not null default now())""",
    "alter table web_json_store add column if not exists revision bigint not null default 1",
]
_ready = False
_best_history_lock = threading.Lock()
_json_store_lock = threading.Lock()


def _db():
    from db_pool import get_db
    db = get_db()
    global _ready
    if not _ready:
        for stmt in _DDL:
            db.run(stmt)
        _ready = True
    return db


def _fmt(ts) -> str:
    """timestamptz -> 'YYYY-MM-DD HH:MM:SS UTC' to match the old file format."""
    try:
        return ts.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts or "")


def _decode_json(value):
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value) if isinstance(value, str) else None
        return parsed if isinstance(parsed, (dict, list)) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


# ── Small durable JSON stores ─────────────────────────────────────────────────────────────────────────
def read_json_store_versioned(store_key: str) -> tuple:
    """Return (database_available, payload-or-None, revision), distinguishing absence from an outage."""
    try:
        db = _db()
        try:
            rows = db.run("select payload, revision from web_json_store where store_key=:k", k=store_key) or []
            return (True, _decode_json(rows[0][0]), int(rows[0][1])) if rows else (True, None, 0)
        finally:
            db.close()
    except Exception as e:
        log.warning(f"read_json_store failed for {store_key}: {e}")
        return False, None, None


def read_json_store(store_key: str) -> tuple:
    """Return (database_available, payload-or-None), distinguishing absence from an outage."""
    available, payload, _revision = read_json_store_versioned(store_key)
    return available, payload


def load_json_store(store_key: str):
    """Compatibility helper: return a dict/list from Supabase, else None."""
    return read_json_store(store_key)[1]


def save_json_store_versioned(store_key: str, payload, expected_revision=None):
    """Write one document and return its revision; expected_revision rejects a stale concurrent writer."""
    if not isinstance(payload, (dict, list)):
        raise TypeError("JSON store payload must be a dict or list")
    try:
        with _json_store_lock:
            db = _db()
            try:
                encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                if expected_revision is None:
                    rows = db.run("""insert into web_json_store (store_key, payload, revision, updated_at)
                                     values (:k, cast(:p as jsonb), 1, now())
                                     on conflict (store_key) do update set
                                       payload=excluded.payload, revision=web_json_store.revision+1,
                                       updated_at=excluded.updated_at returning revision""",
                                  k=store_key, p=encoded) or []
                elif int(expected_revision) == 0:
                    rows = db.run("""insert into web_json_store (store_key, payload, revision, updated_at)
                                     values (:k, cast(:p as jsonb), 1, now())
                                     on conflict (store_key) do nothing returning revision""",
                                  k=store_key, p=encoded) or []
                else:
                    rows = db.run("""update web_json_store set payload=cast(:p as jsonb),
                                       revision=revision+1, updated_at=now()
                                      where store_key=:k and revision=:r returning revision""",
                                  k=store_key, p=encoded, r=int(expected_revision)) or []
                return int(rows[0][0]) if rows else None
            finally:
                db.close()
    except Exception as e:
        log.warning(f"save_json_store failed for {store_key}: {e}")
        return None


def save_json_store(store_key: str, payload) -> bool:
    """Unconditional compatibility upsert used for reference caches and explicit migrations."""
    return save_json_store_versioned(store_key, payload) is not None


# ── Batch activity ───────────────────────────────────────────────────────────────────────────────────
def append_batch(source: str, event: str, by: str = "system") -> bool:
    try:
        db = _db()
        try:
            db.run("insert into web_batch_activity (source, event, by_user) values (:s,:e,:b)",
                   s=source, e=event, b=by)
            return True
        finally:
            db.close()
    except Exception as e:
        log.warning(f"append_batch failed: {e}")
        return False


def list_batch(limit: int = 500) -> list:
    try:
        db = _db()
        try:
            rows = db.run("select ts, source, event, by_user from web_batch_activity "
                          "order by ts desc limit :n", n=limit) or []
            return [{"ts": _fmt(r[0]), "source": r[1], "event": r[2], "by": r[3]} for r in rows]
        finally:
            db.close()
    except Exception as e:
        log.warning(f"list_batch failed: {e}")
        return []


# ── Per-user activity log ──────────────────────────────────────────────────────────────────────────────
def append_activity(user_id: str, event: str) -> bool:
    try:
        db = _db()
        try:
            db.run("insert into web_activity_log (user_id, event) values (:u,:e)", u=user_id, e=event)
            return True
        finally:
            db.close()
    except Exception as e:
        log.warning(f"append_activity failed: {e}")
        return False


def list_activity(user_id: str, limit: int = 200) -> list:
    try:
        db = _db()
        try:
            rows = db.run("select ts, event from web_activity_log where user_id = :u "
                          "order by ts desc limit :n", u=user_id, n=limit) or []
            return [{"ts": _fmt(r[0]), "event": r[1]} for r in rows]
        finally:
            db.close()
    except Exception as e:
        log.warning(f"list_activity failed: {e}")
        return []


# ── Per-user Best Settings history ─────────────────────────────────────────────────────────────────────
def record_best_settings_history(user_id: str, snapshot: dict) -> str:
    """Save at most one snapshot per user/day; repeated identical calculations are no-ops."""
    model = snapshot.get("model") or {}
    options = snapshot.get("options") or []
    canonical = json.dumps({"model": model, "options": options}, sort_keys=True,
                           separators=(",", ":"), ensure_ascii=True)
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    try:
        with _best_history_lock:
            db = _db()
            try:
                prior = db.run("select fingerprint from web_best_settings_history "
                               "where user_id=:u and snapshot_day=current_date", u=user_id) or []
                if prior and prior[0][0] == fingerprint:
                    return "unchanged"
                db.run("""insert into web_best_settings_history
                              (user_id, snapshot_day, dataset_generated, data_through,
                               model_json, options_json, fingerprint)
                           values (:u, current_date, :g, :d, cast(:m as jsonb), cast(:o as jsonb), :f)
                           on conflict (user_id, snapshot_day) do update set
                              ts=now(), dataset_generated=excluded.dataset_generated,
                              data_through=excluded.data_through, model_json=excluded.model_json,
                              options_json=excluded.options_json, fingerprint=excluded.fingerprint""",
                       u=user_id, g=snapshot.get("dataset_generated", ""),
                       d=snapshot.get("data_through", ""),
                       m=json.dumps(model, separators=(",", ":")),
                       o=json.dumps(options, separators=(",", ":")), f=fingerprint)
                return "updated" if prior else "inserted"
            finally:
                db.close()
    except Exception as e:
        log.warning(f"record_best_settings_history failed: {e}")
        return "error"


def _json_value(value, fallback):
    if isinstance(value, type(fallback)):
        return value
    try:
        parsed = _decode_json(value)
        return parsed if isinstance(parsed, type(fallback)) else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def list_best_settings_history(user_id: str, limit: int = 90) -> list:
    try:
        db = _db()
        try:
            rows = db.run("""select snapshot_day, ts, dataset_generated, data_through,
                                    model_json, options_json
                               from web_best_settings_history where user_id=:u
                               order by snapshot_day desc limit :n""",
                          u=user_id, n=max(1, min(int(limit), 365))) or []
            return [{"snapshot_day": str(r[0]), "recorded_at": _fmt(r[1]),
                     "dataset_generated": r[2] or "", "data_through": r[3] or "",
                     "model": _json_value(r[4], {}), "options": _json_value(r[5], [])}
                    for r in rows]
        finally:
            db.close()
    except Exception as e:
        log.warning(f"list_best_settings_history failed: {e}")
        return []


# ── IG account audit trail (user 2026-08-03, P-25) ─────────────────────────────────────────────────────
# Ciphertext-only row store for the encrypted IG-account-identity history; encrypt/decrypt is in web_users.
def append_ig_audit(user_id: str, name_enc: str, number_enc: str, number_last3: str,
                    source: str = "", by: str = "") -> bool:
    try:
        db = _db()
        try:
            db.run("""insert into web_ig_account_audit
                        (user_id, account_name_enc, account_number_enc, account_number_last3, source, by_user)
                      values (:u,:ne,:nu,:l3,:s,:b)""",
                   u=user_id, ne=name_enc, nu=number_enc, l3=number_last3, s=source, b=by)
            return True
        finally:
            db.close()
    except Exception as e:
        log.warning(f"append_ig_audit failed: {e}")
        return False


def list_ig_audit(user_id: str, limit: int = 100) -> list:
    """Raw rows (ciphertext included) newest-first — web_users decrypts the *_enc fields for display."""
    try:
        db = _db()
        try:
            rows = db.run("""select ts, account_name_enc, account_number_enc, account_number_last3,
                                    source, by_user
                               from web_ig_account_audit where user_id = :u
                               order by ts desc limit :n""", u=user_id, n=limit) or []
            return [{"ts": _fmt(r[0]), "name_enc": r[1], "number_enc": r[2],
                     "last3": r[3], "source": r[4], "by": r[5]} for r in rows]
        finally:
            db.close()
    except Exception as e:
        log.warning(f"list_ig_audit failed: {e}")
        return []


# ── One-off migration of the legacy JSON files/records ────────────────────────────────────────────────
def migrate_from_files(batch_file: str = None, users_file: str = None) -> dict:
    """Import legacy rows once. Idempotent-ish: only imports when the DB tables are empty."""
    import json
    summary = {"batch": 0, "activity": 0, "web_users": 0}
    try:
        db = _db()
        try:
            empty_batch = (db.run("select count(*) from web_batch_activity") or [[0]])[0][0] == 0
            empty_act = (db.run("select count(*) from web_activity_log") or [[0]])[0][0] == 0
        finally:
            db.close()
    except Exception as e:
        log.warning(f"migration precheck failed: {e}")
        return summary
    if empty_batch and batch_file:
        try:
            for e in reversed((json.load(open(batch_file, encoding="utf-8")) or {}).get("entries", [])):
                if append_batch(e.get("source", ""), e.get("event", ""), e.get("by", "system")):
                    summary["batch"] += 1
        except Exception as e:
            log.warning(f"batch migration failed: {e}")
    if empty_act and users_file:
        try:
            users = json.load(open(users_file, encoding="utf-8"))
            for name, u in users.items():
                if not (isinstance(u, dict) and "pwd_hash" in u):
                    continue
                for e in reversed(u.get("log") or []):
                    if append_activity(name, e.get("event", "")):
                        summary["activity"] += 1
        except Exception as e:
            log.warning(f"activity migration failed: {e}")
    # Preserve the complete user/settings/request document in Supabase as well as the local compatibility
    # copy. Never overwrite an existing remote document during automatic startup migration.
    if users_file and load_json_store("web_users") is None:
        try:
            with open(users_file, encoding="utf-8") as fh:
                users = json.load(fh)
            if isinstance(users, dict) and save_json_store("web_users", users):
                summary["web_users"] = len(users)
        except Exception as e:
            log.warning(f"web-users state migration failed: {e}")
    if summary["batch"] or summary["activity"]:
        log.info(f"web_store migration imported {summary}")
    return summary
