# ======================================================================================================
# db_pool actually pools (2026-09-06).
#
# The module has been called db_pool since 2026-06-10 and did no pooling: get_db() opened a fresh TCP+TLS
# session every call. Measured that day against the Supabase session pooler: 229 ms to connect, 22 ms to
# run a query on an open connection -- a single-query call spent 91% of its time connecting.
#
# Reusing connections in a system that places real trades is only safe if four things hold, and each of
# these tests is one way it could otherwise corrupt a read rather than merely be slow. They use fakes
# throughout: the point is the LEASE LOGIC, and a test that needed Supabase could not run in CI.
# ======================================================================================================

import threading
import time

import pytest

import db_pool


class FakeConn:
    """Enough of pg8000.native.Connection for the lease logic."""
    def __init__(self, alive=True, status=b"I"):
        self.alive, self._transaction_status = alive, status
        self.ran, self.closed = [], False

    def run(self, sql, **kw):
        if not self.alive:
            raise OSError("connection is dead")
        self.ran.append(sql)
        if sql == "rollback":
            self._transaction_status = b"I"
        return [[1]]

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """Every test starts with an empty thread slot and its own connection factory."""
    monkeypatch.setattr(db_pool, "_pool_local", threading.local())
    monkeypatch.delenv("DB_POOL_DISABLED", raising=False)
    yield


def _factory(monkeypatch, conns):
    made = []
    def fake(timeout=15, attempts=3):
        c = conns.pop(0) if conns else FakeConn()
        made.append(c)
        return c
    monkeypatch.setattr(db_pool, "_connect", fake)
    return made


def test_a_connection_is_reused_rather_than_reopened(monkeypatch):
    made = _factory(monkeypatch, [])
    for _ in range(5):
        db = db_pool.get_db()
        db.run("select 1")
        db.close()
    assert len(made) == 1, f"opened {len(made)} connections for 5 sequential calls; this is the whole point"


def test_closing_a_lease_does_not_close_the_real_connection(monkeypatch):
    made = _factory(monkeypatch, [])
    db = db_pool.get_db(); db.close()
    assert made[0].closed is False, "close() must RETURN the connection, not drop it"


def test_a_second_borrower_never_shares_the_first_ones_session(monkeypatch):
    """RE-ENTRANCY. Two open borrowers on one backend is the statement collision this module was created
    to avoid -- it is why the 6543 transaction pooler is banned here."""
    made = _factory(monkeypatch, [])
    a = db_pool.get_db()
    b = db_pool.get_db()
    assert a._conn is not b._conn, "two simultaneous borrowers were handed the same session"
    assert len(made) == 2
    b.close()
    assert made[1].closed is True, "the extra, unpooled connection must be closed, not retained"
    a.close()


def test_a_connection_in_a_transaction_is_rolled_back_before_reuse(monkeypatch):
    """TRANSACTION STATE. Returning one mid-transaction hands the next borrower someone else's open
    transaction and its locks."""
    c = FakeConn(status=b"T")
    _factory(monkeypatch, [c])
    db = db_pool.get_db(); db.close()
    assert "rollback" in c.ran, "an open transaction must be rolled back before the connection is reused"
    assert c.closed is False, "a successful rollback makes it reusable, not disposable"


def test_a_connection_that_cannot_be_rolled_back_is_discarded(monkeypatch):
    class Stuck(FakeConn):
        def run(self, sql, **kw):
            if sql == "rollback":
                return []          # claims success but stays in a transaction
            return super().run(sql, **kw)
    c = Stuck(status=b"E")
    _factory(monkeypatch, [c])
    db = db_pool.get_db(); db.close()
    assert c.closed is True, "a connection that will not return to idle must never be handed on"


def test_a_dead_connection_is_replaced_rather_than_handed_out(monkeypatch):
    """LIVENESS. The pooler drops idle sessions."""
    dead, good = FakeConn(), FakeConn()
    made = _factory(monkeypatch, [dead, good])
    db = db_pool.get_db(); db.close()
    dead.alive = False
    dead._pool_idle_since = 0          # old enough that the ping runs
    db2 = db_pool.get_db()
    assert db2._conn is good, "a dead connection was handed to a caller"
    db2.close()


def test_an_old_connection_is_retired_even_while_healthy(monkeypatch):
    """AGE. A long-lived session accumulates server state and holds a pooler slot."""
    first, second = FakeConn(), FakeConn()
    _factory(monkeypatch, [first, second])
    db = db_pool.get_db(); db.close()
    first._pool_born = time.time() - db_pool._POOL_MAX_AGE - 1
    db2 = db_pool.get_db()
    assert db2._conn is second, "an over-age connection must be retired"
    assert first.closed is True
    db2.close()


def test_the_hot_path_does_not_pay_for_a_liveness_ping(monkeypatch):
    """A ping on every borrow cost as much as the query itself: 63 ms per cycle against a 22 ms query.
    A connection handed back moments ago has not died in the interim."""
    c = FakeConn()
    _factory(monkeypatch, [c])
    db = db_pool.get_db(); db.close()
    c.ran.clear()
    db2 = db_pool.get_db(); db2.close()
    assert "select 1" not in c.ran, "a just-returned connection must not be re-validated over the wire"


def test_pooling_can_be_switched_off_without_a_deploy(monkeypatch):
    """The escape hatch if pooling is ever suspected during an incident."""
    made = _factory(monkeypatch, [])
    monkeypatch.setenv("DB_POOL_DISABLED", "1")
    a = db_pool.get_db(); a.close()
    b = db_pool.get_db(); b.close()
    assert len(made) == 2, "with pooling off, every call must open its own connection as before"
    assert made[0].closed and made[1].closed, "unpooled connections must be closed on release"


def test_a_lease_behaves_like_the_connection_it_wraps(monkeypatch):
    """Several hundred call sites use the connection directly; the lease must be indistinguishable."""
    c = FakeConn()
    _factory(monkeypatch, [c])
    db = db_pool.get_db()
    assert db.run("select 42") == [[1]]
    assert db._transaction_status == b"I"
    db.close()


def test_closing_twice_is_harmless(monkeypatch):
    """`finally: db.close()` inside a with-block, or a retry wrapper, must not return one connection to
    the pool twice -- that would let two borrowers hold it at once."""
    made = _factory(monkeypatch, [])
    db = db_pool.get_db()
    db.close(); db.close()
    other = db_pool.get_db()
    assert other._conn is made[0]
    third = db_pool.get_db()
    assert third._conn is not other._conn, "the same connection was leased twice"
    third.close(); other.close()


# ── Waiting out a full pool (2026-10-08) ──────────────────────────────────────────────────────────────
# Eight scheduled jobs failed 2026-10-07/08 on EMAXCONNSESSION after the ~9 s three attempts allow, each
# emailing the owner. A batch job now waits for a slot; the web tier must still fail fast so it can fall
# back to the IONOS copy instead of hanging a page request. The message below is the one pg8000 raised in
# run 37743882711.

_POOL_FULL = Exception({"C": "XX000", "S": "FATAL",
                        "M": "(EMAXCONNSESSION) max clients reached in session mode - max clients are "
                             "limited to pool_size: 15"})


class _Clock:
    def __init__(self):
        self.now, self.slept = 0.0, []

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def _pooler(monkeypatch, fail_until, error=_POOL_FULL):
    """Connection() raises `error` until the fake clock reaches fail_until seconds, then succeeds."""
    clock, calls = _Clock(), []
    monkeypatch.setattr(db_pool.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(db_pool.time, "sleep", clock.sleep)
    monkeypatch.setenv("SUPABASE_USER", "u")
    monkeypatch.setenv("SUPABASE_DB_PASSWORD", "p")

    def connection(**kw):
        calls.append(clock.now)
        if clock.now < fail_until:
            raise error
        return FakeConn()
    monkeypatch.setattr(db_pool.pg8000.native, "Connection", connection)
    return clock, calls


def test_an_actions_job_waits_for_a_pool_slot_instead_of_failing(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    clock, calls = _pooler(monkeypatch, fail_until=60)
    assert isinstance(db_pool._connect(), FakeConn)
    assert clock.now >= 60 and len(calls) > 3


def test_an_actions_job_gives_up_once_the_patience_is_spent(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    clock, _ = _pooler(monkeypatch, fail_until=10_000)
    with pytest.raises(Exception, match="EMAXCONNSESSION"):
        db_pool._connect()
    assert 120 <= clock.now <= 121            # never longer than the 120 s that fits every job timeout


def test_the_web_tier_still_fails_fast_so_it_can_fall_back(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    clock, calls = _pooler(monkeypatch, fail_until=60)
    with pytest.raises(Exception, match="EMAXCONNSESSION"):
        db_pool._connect()
    assert len(calls) == 3 and clock.now == 9  # the original 3s + 6s, unchanged


def test_a_non_transient_error_is_not_waited_out(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    clock, calls = _pooler(monkeypatch, fail_until=10_000, error=Exception("password authentication failed"))
    with pytest.raises(Exception, match="password"):
        db_pool._connect()
    assert len(calls) == 3 and clock.now == 9


# ── Failing fast on the web tier during an outage (2026-10-08) ──────────────────────────────────────

def test_the_web_tier_stops_retrying_for_a_while_after_a_failed_connect(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    clock, calls = _pooler(monkeypatch, fail_until=10_000)
    with pytest.raises(Exception, match="EMAXCONNSESSION"):
        db_pool._connect()
    tried = len(calls)
    with pytest.raises(db_pool.DatabaseUnavailable):
        db_pool._connect()                       # inside the window: no new connection attempts at all
    assert len(calls) == tried


def test_the_web_tier_tries_again_once_the_window_has_passed(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    clock, calls = _pooler(monkeypatch, fail_until=20)
    with pytest.raises(Exception, match="EMAXCONNSESSION"):
        db_pool._connect()                       # fails 0..9 s, window now runs to ~39 s
    clock.now += db_pool._WEB_DOWN_SECS
    assert isinstance(db_pool._connect(), FakeConn)
    assert db_pool._down_until == 0.0            # a success closes the breaker


def test_a_batch_job_never_skips_the_database(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    clock, calls = _pooler(monkeypatch, fail_until=10_000, error=Exception("password authentication failed"))
    for _ in range(2):
        with pytest.raises(Exception, match="password"):
            db_pool._connect()
    assert len(calls) == 6                       # both calls really tried, 3 attempts each
