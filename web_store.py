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
# Version History is derived live from git (no file), and snapshot.json stays a local render cache.
#
# Fails soft: if Supabase is unavailable, reads/writes degrade gracefully (empty list / no-op) and log a warning —
# the web app keeps working. One-off migration helpers import the legacy JSON rows.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-07-04  Alex Hind   Initial build — web_batch_activity + web_activity_log tables, append/list, migration.
# ======================================================================================================================

import logging

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
    # IG account audit trail (user 2026-08-03, P-25): append-only ENCRYPTED history of each user's IG
    # account identity. This store holds only CIPHERTEXT (the *_enc columns) — the Fernet encrypt/decrypt
    # lives in web_users, which owns the key. account_number_last3 is cleartext for masked display.
    """create table if not exists web_ig_account_audit (
        id bigserial primary key, ts timestamptz not null default now(),
        user_id text not null, account_name_enc text, account_number_enc text,
        account_number_last3 text, source text, by_user text)""",
    "create index if not exists idx_web_ig_audit_user on web_ig_account_audit (user_id, ts desc)",
]
_ready = False


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
    summary = {"batch": 0, "activity": 0}
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
    if summary["batch"] or summary["activity"]:
        log.info(f"web_store migration imported {summary}")
    return summary
