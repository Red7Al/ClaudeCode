# ======================================================================================================
# Brute-force protection for /api/login (user 2026-08-18: "Throttling is a good idea, please proceed").
#
# These run against the real Supabase table, because the whole point of the feature is that the counter
# PERSISTS ACROSS PROCESSES. Under CGI every request is a fresh Python process, which is why the previous
# advice -- an in-memory counter -- would have counted to one forever and protected nothing. A test with
# a mocked store would re-introduce exactly the assumption that made the old design useless.
#
# Marked live_state so CI, which has no database, skips them.
# ======================================================================================================
import os
import uuid

import pytest

os.environ.setdefault("IG_API_KEY", "test")
os.environ.setdefault("IG_USERNAME", "test")
os.environ.setdefault("IG_PASSWORD", "test")
os.environ.setdefault("IG_ACCOUNT_ID", "test")

import login_throttle  # noqa: E402

pytestmark = pytest.mark.live_state


@pytest.fixture
def scratch():
    """A unique IP/name pair per test, removed afterwards, so nothing touches a real login's counter."""
    ip, name = f"203.0.113.{uuid.uuid4().int % 250}", f"pytest-{uuid.uuid4().hex[:8]}"
    yield ip, name
    from db_pool import get_db
    db = get_db()
    try:
        db.run("delete from login_attempts where ip = :ip or name = :n", ip=ip, n=name)
    finally:
        db.close()


def test_a_clean_pair_is_allowed(scratch):
    ip, name = scratch
    assert login_throttle.check(ip, name) == (True, 0)


def test_it_locks_after_the_configured_failures(scratch):
    """The core behaviour: repeated failures lock the pair, and the lock is visible to the NEXT process.

    Each check()/record_failure() opens its own connection, which is the closest this can get to the CGI
    reality of a brand-new interpreter per request.
    """
    ip, name = scratch
    max_acct, _ip, _win, _lock = login_throttle.settings()

    for i in range(max_acct - 1):
        login_throttle.record_failure(ip, name)
        allowed, _ = login_throttle.check(ip, name)
        assert allowed, f"locked early, after {i + 1} of {max_acct} failures"

    login_throttle.record_failure(ip, name)          # the one that trips it
    allowed, retry = login_throttle.check(ip, name)
    assert allowed is False, f"still open after {max_acct} failures"
    assert retry > 0, "a lock must report how long it lasts"


def test_the_counter_survives_a_new_connection(scratch):
    """The property the in-memory version could never have. Count, drop every connection, count again."""
    ip, name = scratch
    login_throttle.record_failure(ip, name)
    login_throttle.record_failure(ip, name)

    from db_pool import get_db
    db = get_db()
    try:
        rows = db.run("select attempts from login_attempts where ip = :ip and name = :n", ip=ip, n=name)
    finally:
        db.close()
    assert rows and int(rows[0][0]) == 2, "the count did not persist outside the process that made it"


def test_success_clears_the_counter(scratch):
    """A correct password proves it was not an attack, so the slate is wiped -- otherwise a user who
    mistypes twice a day would eventually lock themselves out of their own trading account."""
    ip, name = scratch
    login_throttle.record_failure(ip, name)
    login_throttle.record_failure(ip, name)
    login_throttle.record_success(ip, name)

    from db_pool import get_db
    db = get_db()
    try:
        rows = db.run("select attempts from login_attempts where ip = :ip and name = :n", ip=ip, n=name)
    finally:
        db.close()
    assert not rows, "the counter row should be gone after a successful login"
    assert login_throttle.check(ip, name) == (True, 0)


def test_spraying_many_usernames_from_one_ip_is_caught(scratch):
    """A per-account cap alone lets an attacker try 4 passwords each against unlimited usernames forever.
    The per-IP ceiling closes that, which is why both scopes exist."""
    ip, _ = scratch
    _acct, max_ip, _win, _lock = login_throttle.settings()

    for i in range(max_ip + 1):
        login_throttle.record_failure(ip, f"sprayed-{i}")

    allowed, _ = login_throttle.check(ip, "someone-entirely-new")
    assert allowed is False, "one IP failing across many usernames must be blocked"

    from db_pool import get_db
    db = get_db()
    try:
        db.run("delete from login_attempts where ip = :ip", ip=ip)
    finally:
        db.close()


def test_it_fails_open_when_the_database_is_unreachable(monkeypatch):
    """Deliberate trade-off: an unreachable database must not lock the owner out of their own system.

    Being unable to log in during a Supabase blip is worse, for a private trading site, than a brief
    unthrottled window -- and the failure is logged. Pinned as a test so the choice is explicit and
    cannot be reversed by accident; revisit it if the site ever takes public signups.
    """
    def _boom():
        raise RuntimeError("database down")
    monkeypatch.setattr("db_pool.get_db", _boom)

    assert login_throttle.check("1.2.3.4", "anyone") == (True, 0)
    assert login_throttle.record_failure("1.2.3.4", "anyone") == 0      # no crash, no lock
    login_throttle.record_success("1.2.3.4", "anyone")                  # must not raise
