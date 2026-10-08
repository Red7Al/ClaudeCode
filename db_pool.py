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
# 1.1.0   2026-10-08  Claude      GitHub Actions jobs wait up to 120s for a free slot on EMAXCONNSESSION instead of
#                                 failing after ~9s (8 emailed failures 2026-10-07/08). Web tier unchanged.
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


# ── The web tier gets a BOUNDED, SHARED pool (2026-10-08) ─────────────────────────────────────────
#
# The thread-local pool below gives every thread its own connection and keeps it open while idle for up
# to _POOL_MAX_AGE. On the web tier every concurrent request is a thread, so one page view opened one
# connection per API call and held them for minutes. MEASURED 2026-10-08: ten simultaneous logged-out
# requests to the live site opened 9 connections from the IONOS host (Supavisor log, 13:46, from a
# baseline of 0); bursts of 15/min at 06:29 and 12/min at 07:28 from that host preceded the scheduled
# jobs failing with EMAXCONNSESSION -- the session pooler admits 15 clients in all.
#
# So outside GitHub Actions a process holds at most _WEB_MAX_CONN connections, shared across threads.
# A lease is still exclusive -- one borrower at a time per connection, which is the property the
# thread-local design existed to guarantee. A borrower waits up to _WEB_SLOT_WAIT for a free slot and then
# gets DatabaseUnavailable, which every caller already handles by falling back. Idle connections are
# closed after _WEB_IDLE_MAX so a quiet worker does not sit on slots. Batch jobs keep the thread-local
# pool unchanged.
_WEB_MAX_CONN = int(os.environ.get("DB_WEB_MAX_CONN", "4"))
_WEB_SLOT_WAIT = float(os.environ.get("DB_WEB_SLOT_WAIT_SECS", "10"))
_WEB_IDLE_MAX = float(os.environ.get("DB_WEB_IDLE_SECS", "60"))
_web_slots = threading.BoundedSemaphore(_WEB_MAX_CONN)   # one permit per OPEN web connection
_web_idle = []                                            # open connections nobody is borrowing
_web_lock = threading.Lock()


def _web_close(conn):
    """Close a web-tier connection and give its slot back."""
    try:
        conn.close()
    except Exception:
        pass
    _web_slots.release()


def _web_reap():
    """Close idle web connections that are too old or idle too long. Returns the ones still usable."""
    now = time.time()
    with _web_lock:
        keep, drop = [], []
        for c in _web_idle:
            idle = now - getattr(c, "_pool_idle_since", now)
            age = now - getattr(c, "_pool_born", now)
            (keep if idle < _WEB_IDLE_MAX and age < _POOL_MAX_AGE else drop).append(c)
        _web_idle[:] = keep
    for c in drop:
        _POOL_STATS["discarded"] += 1
        _web_close(c)


def _web_take_idle():
    """An idle web connection that still answers, or None."""
    while True:
        with _web_lock:
            conn = _web_idle.pop() if _web_idle else None
        if conn is None:
            return None
        if _still_good(conn):
            return conn
        _POOL_STATS["discarded"] += 1
        _web_close(conn)


def _web_get(timeout: int, attempts: int):
    _web_reap()
    deadline = time.monotonic() + _WEB_SLOT_WAIT
    # A returned connection keeps its slot, so a borrower blocked on the semaphore alone would never see
    # it come free. Wait in short steps and look in the idle list between them.
    while True:
        conn = _web_take_idle()
        if conn is not None:
            _POOL_STATS["reused"] += 1
            return _Leased(conn, pooled=True, web=True)
        if _web_slots.acquire(timeout=0.05):
            break
        if time.monotonic() >= deadline:
            raise DatabaseUnavailable(f"all {_WEB_MAX_CONN} web database connections busy for "
                                      f"{_WEB_SLOT_WAIT:.0f}s")
    try:
        fresh = _connect(timeout, attempts)
    except Exception:
        _web_slots.release()
        raise
    fresh._pool_born = time.time()
    _POOL_STATS["opened"] += 1
    return _Leased(fresh, pooled=True, web=True)


class _Leased:
    """A borrowed connection. close() returns it to the thread's slot instead of dropping it."""

    __slots__ = ("_conn", "_pooled", "_closed", "_web")

    def __init__(self, conn, pooled: bool, web: bool = False):
        self._conn, self._pooled, self._closed, self._web = conn, pooled, False, web

    def __getattr__(self, name):
        # Everything except close() goes straight through, so a lease behaves exactly like the real
        # pg8000 connection -- including run(), prepare() and the attributes callers read off it.
        return getattr(object.__getattribute__(self, "_conn"), name)

    def close(self):
        if self._closed:
            return
        self._closed = True
        conn = self._conn
        if self._web:
            if _still_good(conn) and (time.time() - getattr(conn, "_pool_born", 0)) < _POOL_MAX_AGE:
                conn._pool_idle_since = time.time()
                with _web_lock:
                    _web_idle.append(conn)
            else:
                _POOL_STATS["discarded"] += 1
                _web_close(conn)
            return
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
    if _pool_enabled() and not _is_batch_job():
        return _web_get(timeout, attempts)
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


# ── Waiting out a full pool (2026-10-08) ──────────────────────────────────────────────────────────────
#
# The session pooler admits 15 clients. When it is full it refuses with EMAXCONNSESSION. Between
# 2026-10-07 23:01 and 2026-10-08 07:30, eight scheduled jobs failed on exactly that error, each having
# given up after the ~9 s the three attempts below allow -- and each failure emailed the owner. A full pool
# is transient by nature: the slot frees when another client finishes.
#
# So a BATCH job (GitHub Actions, where GITHUB_ACTIONS=true) keeps waiting on that one error, up to
# _POOL_FULL_PATIENCE seconds. 120 s fits inside every scheduled job's timeout-minutes (smallest is 5).
#
# The WEB tier deliberately does NOT wait: it runs on IONOS, where GITHUB_ACTIONS is unset, and a page
# request that sat for two minutes would hit the gateway timeout instead of falling back to the IONOS copy.
# Any other error -- bad credentials, DNS -- is not transient and keeps the original three attempts.
_POOL_FULL_MARK = "EMAXCONNSESSION"


def _is_batch_job() -> bool:
    return os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true"


def _pool_full_patience() -> float:
    if not _is_batch_job():
        return 0.0
    return float(os.environ.get("DB_POOL_FULL_PATIENCE_SECS", "120"))


# ── Failing fast on the web tier during an outage (2026-10-08) ─────────────────────────────────────
#
# Owner: "if supabase is not allowing data writes or reads - IONOS must be available and we should not
# experience an error". Measured that day with the real app and unreachable credentials: every route that
# touches the database spent ~10 s on doomed connect attempts before its fallback ran, and /api/freshness
# (four surfaces) took 40 s. In a real outage that TIMES OUT rather than refusing, each attempt costs up to
# `timeout` seconds, which walks a page into the gateway's ~120 s limit -- itself an error to the visitor.
#
# So once a web process has seen a connect fail, it stops trying for _WEB_DOWN_SECS and raises at once;
# every caller already handles a DB error by falling back. Batch jobs never trip this: they would rather
# wait (see _pool_full_patience) than skip work.
_WEB_DOWN_SECS = float(os.environ.get("DB_WEB_DOWN_SECS", "30"))
_down_until = 0.0


class DatabaseUnavailable(ConnectionError):
    """Raised immediately while the web tier is backing off after a failed connect."""


def _connect(timeout: int = 15, attempts: int = 3):
    """One real connection, with the retry/backoff this module has always applied."""
    global _down_until
    web_tier = not _is_batch_job()
    if web_tier and time.monotonic() < _down_until:
        raise DatabaseUnavailable(f"Supabase unreachable; not retrying for "
                                  f"{_down_until - time.monotonic():.0f}s more")
    try:
        conn = _connect_with_retry(timeout, attempts)
    except Exception:
        if web_tier:
            _down_until = time.monotonic() + _WEB_DOWN_SECS
        raise
    _down_until = 0.0
    return conn


def _connect_with_retry(timeout: int, attempts: int):
    last = None
    deadline = time.monotonic() + _pool_full_patience()
    i = 0
    while True:
        try:
            return pg8000.native.Connection(
                host=SUPABASE_HOST, port=SESSION_POOLER_PORT, database="postgres",
                user=os.environ["SUPABASE_USER"],
                password=os.environ["SUPABASE_DB_PASSWORD"],
                ssl_context=True, timeout=timeout,
            )
        except Exception as e:
            last = e
            i += 1
            pool_full = _POOL_FULL_MARK in str(e)
            if i < attempts:
                wait = 3 * i                           # 3s, 6s — let a session-pool slot free up
            elif pool_full and time.monotonic() < deadline:
                wait = min(15.0, max(0.0, deadline - time.monotonic()))
            else:
                log.warning(f"DB connect attempt {i} failed, giving up: {e}")
                raise last
            log.warning(f"DB connect attempt {i} failed ({'pool full, waiting' if pool_full else 'retrying'}"
                        f" {wait:.0f}s): {e}")
            time.sleep(wait)


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
