# ======================================================================================================================
# File:         db_pool.py
# Author:       Alex Hind
# Created:      2026-06-10
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Single source of truth for Supabase connections. Every script connects through
# get_db() here — never inline pg8000 Connection() calls.
#
# Why the SESSION pooler (port 5432), never 6543:
#   The 6543 TRANSACTION pooler is incompatible with pg8000's extended protocol —
#   unnamed prepared statements collide across queries on a shared backend, raising
#   26000 / 22007 / 08P01 (crashed social_monitor, watchdog and run_daily_report on
#   2026-06-09). The 5432 SESSION pooler gives a dedicated backend per connection,
#   so statements never collide.
#
# Why retry:
#   The session pooler has a LOWER connection limit than 6543 and can transiently
#   time out under the aggressive */5 monitor schedule (watchdog crashed 2026-06-10
#   07:50 with "TimeoutError [Errno 110] Connection timed out … port 5432"). So set
#   an explicit connect timeout and retry with backoff — a brief pool-exhaustion
#   spike self-recovers instead of failing the job.
#
# Environment Variables Required:
#   SUPABASE_USER, SUPABASE_DB_PASSWORD
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-10  Alex Hind   Initial build — resilient session-pooler connect (timeout + retry/backoff), extracted
#                                 from the watchdog fix and shared across the whole codebase.
# ======================================================================================================================

import os
import threading
from dotenv import load_dotenv; load_dotenv(override=True)
import time
import logging

import pg8000.native

log = logging.getLogger("db_pool")

SUPABASE_HOST       = "aws-0-eu-west-1.pooler.supabase.com"
SESSION_POOLER_PORT = 5432   # session pooler — NEVER 6543 (pg8000 statement collisions)


# ── The actual pool (2026-09-06) ──────────────────────────────────────────────────────────────────────
#
# This module has been called db_pool since 2026-06-10 and did no pooling: get_db() opened a fresh TCP +
# TLS session on every call and every caller closed it. Measured on 2026-09-06 against the Supabase
# session pooler: 229 ms median to connect, 22 ms to run a query on a connection already open. A
# single-query call therefore spent 91% of its time connecting.
#
# WHY THREAD-LOCAL rather than a shared pool. This process serves concurrent web requests. A shared pool
# needs locking, and the failure mode of getting that wrong is not a slow page -- it is two requests
# interleaving statements on one backend, which on the session pooler means one user's transaction state
# reaching another's query. A thread-local connection cannot be shared by construction, which removes the
# entire class of hazard and still collapses repeated connects within a request or a script.
#
# WHY A LEASE WRAPPER rather than changing callers. Several hundred call sites follow
# `db = get_db(); try: ... finally: db.close()`. Rewriting them all would be a large, risky diff for no
# behavioural gain. get_db() returns a lease that proxies the real connection and, on close(), RETURNS it
# instead of dropping it. Callers are unchanged and cannot tell the difference.
#
# THE FOUR THINGS THAT MAKE REUSE SAFE, each of which is a way this could silently corrupt reads:
#
#   1. Re-entrancy. If a caller obtains a second connection before closing the first, they must not be
#      the same session -- interleaved statements on one backend is exactly the 6543 bug this module was
#      created to avoid. While a lease is outstanding the next get_db() opens a REAL new connection and
#      does not pool it.
#   2. Transaction state. A connection returned mid-transaction would hand the next borrower an open
#      transaction and its locks. pg8000 reports status on _transaction_status ('I' idle, 'T' in a
#      transaction, 'E' failed); anything other than idle is rolled back before reuse, and discarded if
#      the rollback itself fails.
#   3. Liveness. The pooler closes idle sessions. A pooled connection is validated before reuse and
#      replaced if it does not answer.
#   4. Age. Long-lived sessions accumulate server-side state and hold a pooler slot. One is retired after
#      _POOL_MAX_AGE regardless of health.
#
# Set DB_POOL_DISABLED=1 to fall back to connect-per-call. That is the escape hatch if pooling is ever
# suspected in an incident: it restores the previous behaviour exactly, without a deploy.

_POOL_MAX_AGE = float(os.environ.get("DB_POOL_MAX_AGE_SECS", "270"))   # under the pooler's idle timeout
_POOL_PING_AFTER = float(os.environ.get("DB_POOL_PING_AFTER_SECS", "20"))  # only ping a connection idle this long
_pool_local = threading.local()
_POOL_STATS = {"reused": 0, "opened": 0, "discarded": 0, "unpooled": 0}


def _pool_enabled() -> bool:
    return os.environ.get("DB_POOL_DISABLED", "").strip() not in ("1", "true", "True")


def _still_good(conn) -> bool:
    """Is this connection safe to hand to the next borrower?

    The transaction check is a LOCAL attribute read and costs nothing. The liveness ping is a full round
    trip, so it runs only after the connection has been sitting idle long enough to plausibly have been
    dropped. Pinging on every borrow measured 63 ms per get/query/close cycle against a 22 ms query --
    the validation cost as much as the work, which defeats most of the point of pooling.

    A connection handed back moments ago is not going to have died in the interim, and if it somehow has,
    the caller's own query raises and the next borrow discards it. Trading a vanishingly rare extra
    failure for a round trip on every single query is not a good exchange.
    """
    if conn is None:
        return False
    try:
        status = getattr(conn, "_transaction_status", None)
        if status not in (b"I", None):
            # Mid-transaction, or failed. Roll back rather than pass on someone else's locks.
            conn.run("rollback")
            if getattr(conn, "_transaction_status", None) not in (b"I", None):
                return False
        if time.time() - getattr(conn, "_pool_idle_since", 0) >= _POOL_PING_AFTER:
            conn.run("select 1")        # the pooler drops idle sessions; prove it still answers
        return True
    except Exception:
        return False


class _Leased:
    """A borrowed connection. close() returns it to the thread's slot instead of dropping it."""

    __slots__ = ("_conn", "_pooled", "_closed")

    def __init__(self, conn, pooled: bool):
        self._conn, self._pooled, self._closed = conn, pooled, False

    def __getattr__(self, name):
        # Everything except close() goes straight through, so a lease behaves exactly like the real
        # pg8000 connection -- including run(), prepare() and the attributes callers read off it.
        return getattr(object.__getattribute__(self, "_conn"), name)

    def close(self):
        if self._closed:
            return
        self._closed = True
        conn = self._conn
        if not self._pooled:
            try:
                conn.close()
            except Exception:
                pass
            return
        _pool_local.leased = False
        if _still_good(conn) and (time.time() - getattr(conn, "_pool_born", 0)) < _POOL_MAX_AGE:
            conn._pool_idle_since = time.time()
            _pool_local.conn = conn
        else:
            _POOL_STATS["discarded"] += 1
            _pool_local.conn = None
            try:
                conn.close()
            except Exception:
                pass

    # Used as a context manager in a few places, and harmless everywhere else.
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def pool_stats() -> dict:
    """Reuse counters, for the diagnostics page and for asserting the pool is actually pooling."""
    return dict(_POOL_STATS)


def get_db(timeout: int = 15, attempts: int = 3):
    """
    A pooled connection to Supabase via the SESSION pooler (5432), with an explicit timeout and
    retry/backoff on connect. Raises the last exception only after all attempts are exhausted.

    Returns a LEASE: it behaves exactly like the pg8000 connection, and close() returns it to this
    thread's slot rather than dropping it. Callers are unchanged -- keep using
    `db = get_db(); try: ... finally: db.close()`.
    """
    if _pool_enabled():
        # An outstanding lease means this thread is already using its connection. Give the caller a
        # separate, UNPOOLED session: sharing one backend between two open borrowers is the statement
        # collision this module exists to prevent.
        if getattr(_pool_local, "leased", False):
            _POOL_STATS["unpooled"] += 1
            return _Leased(_connect(timeout, attempts), pooled=False)
        conn = getattr(_pool_local, "conn", None)
        if conn is not None:
            if _still_good(conn) and (time.time() - getattr(conn, "_pool_born", 0)) < _POOL_MAX_AGE:
                _pool_local.conn = None
                _pool_local.leased = True
                _POOL_STATS["reused"] += 1
                return _Leased(conn, pooled=True)
            _POOL_STATS["discarded"] += 1
            _pool_local.conn = None
            try:
                conn.close()
            except Exception:
                pass
        fresh = _connect(timeout, attempts)
        fresh._pool_born = time.time()
        _pool_local.leased = True
        _POOL_STATS["opened"] += 1
        return _Leased(fresh, pooled=True)
    return _connect(timeout, attempts)


def _connect(timeout: int = 15, attempts: int = 3):
    """One real connection, with the retry/backoff this module has always applied."""
    last = None
    for i in range(attempts):
        try:
            return pg8000.native.Connection(
                host=SUPABASE_HOST, port=SESSION_POOLER_PORT, database="postgres",
                user=os.environ["SUPABASE_USER"],
                password=os.environ["SUPABASE_DB_PASSWORD"],
                ssl_context=True, timeout=timeout,
            )
        except Exception as e:
            last = e
            log.warning(f"DB connect attempt {i + 1}/{attempts} failed: {e}")
            if i < attempts - 1:
                time.sleep(3 * (i + 1))   # 3s, 6s — let a session-pool slot free up
    raise last


# ── Encrypted secret-store bootstrap (task #53) ───────────────────────────────────────────────────────
# db_pool is imported early by EVERY DB-using entrypoint (web server + all GitHub-Actions scripts), so this
# is the single place to decrypt the Supabase app_secrets store into os.environ. DUAL-READ (override=False)
# — only fills env vars not already set from .env, so behaviour is unchanged until .env is pruned. FULLY
# fail-open: this must NEVER raise, since a failure here would break the import everything depends on. Runs
# once per process, AFTER get_db is defined (app_secrets reads back through get_db).
_SECRETS_BOOTSTRAPPED = False


def _bootstrap_secrets():
    global _SECRETS_BOOTSTRAPPED
    if _SECRETS_BOOTSTRAPPED:
        return
    _SECRETS_BOOTSTRAPPED = True
    try:
        import app_secrets
        app_secrets.load_secrets_into_env()
    except Exception as e:
        log.warning(f"app_secrets bootstrap skipped: {e}")


_bootstrap_secrets()
