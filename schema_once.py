"""Schema statements applied ONCE across every job, not once per process.

Every GitHub job is a new process, so a module's "once per process" guard still ran its CREATE/ALTER ... IF NOT EXISTS
statements on every run -- and each one, even a no-op, fires Supabase's pgrst_ddl_watch event trigger, which makes the
REST layer reload its schema cache (`SELECT name FROM pg_timezone_names` + catalogue queries). MEASURED 2026-10-23: 0
reloads in 20 s idle, 1 in the 20 s after one no-op ALTER. 2026-10-23 -> 10-26: web_store's 10 statements ran 196
times, app_config's 174, x_draft_state's 69; 266 reloads, 0.72 GB/30d of egress.

A statement list is recorded in schema_migrations under a name with the hash of its SQL, and skipped while the record
matches; a changed list runs again. run_schema.py keeps its migrations in the same table.
"""
import hashlib

RECORD_TABLE = "schema_migrations"
_APPLIED = None        # {name: sql hash}, read once per process


def sql_hash(statements) -> str:
    if isinstance(statements, str):
        statements = [statements]
    return hashlib.sha256("\n".join(statements).encode("utf-8")).hexdigest()[:16]


def applied(db, fresh=False) -> dict:
    """{name: sql hash} already applied; creates the record table the first time (one DDL, once)."""
    global _APPLIED
    if _APPLIED is None or fresh:
        try:
            _APPLIED = {n: h for n, h in (db.run(f"select name, sql_hash from {RECORD_TABLE}") or [])}
        except Exception as e:
            if "42P01" not in str(e):
                raise
            db.run(f"create table if not exists {RECORD_TABLE} (name text primary key, sql_hash text not null, "
                   f"applied_at timestamptz not null default now())")
            _APPLIED = {}
    return _APPLIED


def record(db, name, h) -> None:
    db.run(f"insert into {RECORD_TABLE} (name, sql_hash) values (:n, :h) "
           f"on conflict (name) do update set sql_hash = excluded.sql_hash, applied_at = now()", n=name, h=h)
    if _APPLIED is not None:
        _APPLIED[name] = h


def ensure(db, name, statements) -> bool:
    """Run `statements` (one SQL string or a list) unless this exact list is already recorded under `name`. True when
    it ran. A failure records nothing, so the next job tries again."""
    if isinstance(statements, str):
        statements = [statements]
    h = sql_hash(statements)
    if applied(db).get(name) == h:
        return False
    for stmt in statements:
        db.run(stmt)
    record(db, name, h)
    return True
