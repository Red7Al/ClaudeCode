# ======================================================================================================================
# File:         conftest.py
# Created:      2026-09-13
#
# TEST ISOLATION for module-level caches.
#
# account_scope caches the login -> trading-profile binding map for 300 seconds. That is right in production (the
# binding changes when an administrator adds a user, not per request) and wrong in a test session, where one test's
# stubbed database answer stays cached and silently becomes the next test's reality.
#
# THIS WAS ALWAYS BROKEN AND WAS INVISIBLE. Until 2026-09-13, _load_bindings merged the hard-coded _FLOOR over every
# result, so a polluted cache STILL contained Alex -> the Owner profile and any test depending on that binding passed
# regardless. Making the database authoritative (so that revoking a binding actually revokes it) removed the padding
# and test_trailing_stop.py::test_lwr_owner_profile_has_explicit_alex_binding_only began failing in the full suite
# while passing alone -- the classic signature of leaked state.
#
# The fix belongs here rather than in the individual tests: a test cannot be expected to know which module-level cache
# some other test warmed. Any future cache of this kind should be reset here too.
# ======================================================================================================================

import re

import pytest


@pytest.fixture(autouse=True)
def _clear_account_scope_cache():
    """Every test starts with a cold binding cache and leaves one behind."""
    try:
        import account_scope
    except Exception:              # the module is unavailable in some minimal environments
        yield
        return

    def _reset():
        with account_scope._lock:
            account_scope._cache["map"] = None
            account_scope._cache["at"] = 0.0

    _reset()
    yield
    _reset()


@pytest.fixture(autouse=True)
def _reset_db_outage_breaker():
    """db_pool stops trying for 30 s after a failed connect on the web tier (2026-10-08). One test's refused
    connection must not become the next test's instant DatabaseUnavailable, so it starts and ends closed."""
    try:
        import db_pool
    except Exception:
        yield
        return
    db_pool._down_until = 0.0
    yield
    db_pool._down_until = 0.0


# Hosts no test may reach, and what reaching them would mean. See _no_test_reaches_the_outside_world.
_BLOCKED_HOSTS = {"ig.com": "contact IG", "resend.com": "send email"}


@pytest.fixture(autouse=True)
def _no_test_reaches_the_outside_world(monkeypatch):
    """NO TEST MAY CONTACT IG OR SEND EMAIL.

    IG: ig_shim loads the owner's live credentials from .env (load_dotenv(override=True)), so on his machine an
    unstubbed IG call is a REAL login to his trading account. MEASURED 2026-10-18: a change made
    test_ig_allowance_stop.py log in for real, and two local suite runs each sent one refused login to the
    account -- while the owner feared repeated refused logins had suspended it.
    Email: an unstubbed alert would reach his inbox wherever a sender is configured. MEASURED 2026-10-18:
    refused-login tests called send_simple_email 3 times; nothing went out only because neither RESEND_API_KEY
    nor the Yahoo credentials were set in the test process -- luck, not design.

    Every requests call goes through Session.request (refused here for each _BLOCKED_HOSTS domain) and SMTP is
    refused outright. The code under test catches broad exceptions (the audits must survive IG failures;
    send_simple_email never raises), so a refusal alone could be swallowed and the test pass: every attempt is
    also RECORDED and the test FAILS at teardown."""
    import smtplib
    from urllib.parse import urlparse
    import requests
    real, attempts = requests.sessions.Session.request, []

    def guarded(self, method, url, *a, **k):
        host = (urlparse(str(url)).hostname or "").lower()
        for domain, meaning in _BLOCKED_HOSTS.items():
            if host == domain or host.endswith("." + domain):
                attempts.append(f"{meaning}: {method} {url}")
                raise ConnectionError(f"tests may not {meaning}: {method} {url}")
        return real(self, method, url, *a, **k)

    def smtp_connect(self, host="localhost", port=0, *a, **k):    # SMTP and SMTP_SSL both connect here
        attempts.append(f"send email: SMTP {host}:{port}")
        raise ConnectionError(f"tests may not send email (SMTP {host}:{port})")
    monkeypatch.setattr(requests.sessions.Session, "request", guarded)
    monkeypatch.setattr(smtplib.SMTP, "connect", smtp_connect)    # the classes stay classes
    yield
    if attempts:
        pytest.fail(f"this test reached the outside world {len(attempts)} time(s): {attempts[:3]}", pytrace=False)


# State of _no_test_reaches_the_live_database. A module imported DURING a test (notify, price_store...) binds whatever
# db_pool.get_db is at that moment and keeps it after monkeypatch restores db_pool, so the stand-in must stay safe for
# every later test: it records only while a guarded test is running and otherwise calls the real function.
_DB_GUARD = {"attempts": None, "real": None, "stand_in": None}


class _EmptyDb:
    """The database a test gets from the `empty_database` fixture: UNREACHABLE, exactly as in CI where the placeholder
    credentials cannot connect. Every statement is kept in `executed` (so a test can assert what it WOULD have written)
    and then refused with db_pool.DatabaseUnavailable, so the code under test takes the same failure paths as in CI.
    (Returning [] instead was wrong: account_scope treats an empty read as authoritative, and a write that returns
    nothing fails web_users' read-back check -- measured 2026-11-21, 8 CI failures.)"""
    columns, row_count = [], 0

    def __init__(self, executed):
        self.executed = executed

    def run(self, sql, *a, **k):
        import db_pool
        self.executed.append((" ".join(str(sql).split()), k))
        raise db_pool.DatabaseUnavailable("tests may not reach the live database (empty_database): "
                                          + " ".join(str(sql).split())[:60])

    def prepare(self, sql, *a, **k):
        return self.run(sql)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RecordingDb:
    """What a guarded test gets from get_db(): every call is recorded (SQL verb and table) and refused."""
    columns, row_count = [], 0

    def __init__(self, attempts):
        self._attempts = attempts

    def run(self, sql, *a, **k):
        text = " ".join(str(sql).split()).lower()
        m = re.match(r"(insert into|update|delete from|select .*? from|with|create table(?: if not exists)?|alter table|drop table)\s+(\S+)", text)
        self._attempts.append(f"{m.group(1).split()[0]} {m.group(2)}" if m else text[:40])
        raise ConnectionError("tests may not reach the live database: " + text[:60])

    def prepare(self, sql, *a, **k):
        self._attempts.append("prepare " + " ".join(str(sql).split())[:60])
        raise ConnectionError("tests may not reach the live database (prepare)")

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _guarded_get_db(*a, **k):
    attempts = _DB_GUARD["attempts"]
    if _DB_GUARD["stand_in"] is not None:      # the test asked for the empty_database fixture
        return _DB_GUARD["stand_in"]
    if attempts is None:                       # no guarded test is running: behave as the real function
        return _DB_GUARD["real"](*a, **k)
    attempts.append("get_db()")
    return _RecordingDb(attempts)


@pytest.fixture(autouse=True)
def _no_test_reaches_the_live_database(request, monkeypatch):
    """NO TEST MAY READ OR WRITE THE LIVE DATABASE.

    db_pool loads the production credentials from .env (load_dotenv(override=True)), so an unstubbed get_db() in a test
    is a real query against Supabase. MEASURED 2026-11-21 (spy): one run of test_trading_limits.py made 6
    `insert into missed_trade_log` calls (AAPL BUY SIZE_ZERO) and 133 tests in 23 files reached get_db unstubbed
    (writers: missed_trade_log, web_batch_activity, web_json_store).

    db_pool.get_db and every module-level alias of it (notify._pool_get_db, price_store.get_db ...) become a recorder,
    and pg8000.native.Connection is refused as a backstop. The code under test catches broad exceptions, so a refusal
    alone could be swallowed and the test pass: every attempt is RECORDED and the test FAILS at teardown naming them.
    Opt out: @pytest.mark.live_state (needs the real database anyway) or @pytest.mark.real_db_pool (the test is about
    db_pool itself). A test that stubs db_pool.get_db itself replaces the recorder and is not affected."""
    if request.node.get_closest_marker("live_state") or request.node.get_closest_marker("real_db_pool"):
        yield
        return
    import sys
    try:
        import db_pool, pg8000.native
    except Exception:                  # a minimal environment without the database driver has nothing to guard
        yield
        return
    real = db_pool.get_db
    if real is not _guarded_get_db:
        _DB_GUARD["real"] = real
    attempts = []
    for name, mod in list(sys.modules.items()):
        if mod is None or name == "db_pool":
            continue
        try:
            names = list(vars(mod).items())
        except TypeError:
            continue
        for attr, val in names:
            if val is real:
                monkeypatch.setattr(mod, attr, _guarded_get_db, raising=False)
    monkeypatch.setattr(db_pool, "get_db", _guarded_get_db)

    def refused(*a, **k):
        attempts.append("pg8000 Connection()")
        raise ConnectionError("tests may not open a database connection")
    monkeypatch.setattr(pg8000.native, "Connection", refused)
    _DB_GUARD["attempts"] = attempts
    try:
        yield
    finally:
        _DB_GUARD["attempts"] = None
        _DB_GUARD["stand_in"] = None
    if attempts:
        pytest.fail(f"this test reached the live database {len(attempts)} time(s): {sorted(set(attempts))[:6]}",
                    pytrace=False)


@pytest.fixture
def empty_database(request):
    """For a test that is not about the database but whose code path reads config, web_store or a log table: every
    get_db() (db_pool's and every module alias) returns an UNREACHABLE database -- each statement is recorded and then
    raises db_pool.DatabaseUnavailable, as in CI where the placeholder credentials cannot connect -- so the code takes its
    real failure paths. (The name is historical.) Returns the list of (sql, params) executed.
    It does nothing for a live_state / real_db_pool test, which the guard leaves alone."""
    executed = []
    if _DB_GUARD["attempts"] is not None:
        _DB_GUARD["stand_in"] = _EmptyDb(executed)
    return executed


@pytest.fixture(autouse=True)
def _ig_login_pause_in_memory(monkeypatch):
    """ig_shim keeps its IG-login pause (owner 2026-10-19) in app_config under "ig_login_pause:<account>". No test
    may read or write those live rows: each test gets an empty in-memory store for those keys (every other key
    reaches config_store as before) and an empty process record. config_store is patched rather than ig_shim so
    this fixture never imports ig_shim, whose import loads .env."""
    import sys
    try:
        import config_store
    except Exception:
        yield
        return
    store, real_get, real_set = {}, config_store.get_value, config_store.set_value

    def get_value(key, default=""):
        return store.get(key, default) if key.startswith("ig_login_pause:") else real_get(key, default)

    def set_value(key, value, updated_by=""):
        if key.startswith("ig_login_pause:"):
            store[key] = value
            return True
        return real_set(key, value, updated_by)
    monkeypatch.setattr(config_store, "get_value", get_value)
    monkeypatch.setattr(config_store, "set_value", set_value)

    def _forget():                 # some tests put a stand-in module at sys.modules["ig_shim"]
        blocks = getattr(sys.modules.get("ig_shim"), "_LOCAL_BLOCKS", None)
        if isinstance(blocks, dict):
            blocks.clear()
    _forget()
    yield store
    _forget()


@pytest.fixture(autouse=True)
def _scanner_report_sent_store_in_memory(monkeypatch):
    """No test may read or write the live record of Scanner Report rows sent ("scanner_report_sent").
    MEASURED 2026-10-20: tests calling run_scanner_report_email.main() wrote fake logins "B" and "Silver" into the
    live store on 2026-10-18 17:05. Each test gets an empty in-memory store for that key; every other key reaches
    web_store as before. A test that needs its own store (test_report_removed_rows.store) patches over this."""
    try:
        import web_store
    except Exception:
        yield
        return
    key, mem = "scanner_report_sent", {"payload": None, "rev": 0}
    real_read, real_save = web_store.read_json_store_versioned, web_store.save_json_store_versioned

    def read(store_key):
        return (True, mem["payload"], mem["rev"]) if store_key == key else real_read(store_key)

    def save(store_key, payload, expected_revision=None):
        if store_key != key:
            return real_save(store_key, payload, expected_revision=expected_revision)
        if expected_revision != mem["rev"]:
            return None
        mem["payload"], mem["rev"] = payload, mem["rev"] + 1
        return mem["rev"]
    monkeypatch.setattr(web_store, "read_json_store_versioned", read)
    monkeypatch.setattr(web_store, "save_json_store_versioned", save)
    yield mem


@pytest.fixture(autouse=True)
def _bridge_unaccounted_alert_offline(request, monkeypatch):
    """order_bridge._alert_unaccounted (P-04 check 1) reads missed_trade_log and emails. MEASURED 2026-11-11 in a clean
    worktree: with the CI placeholder credentials the existing bridge tests, whose stubbed engine returns no order, reached
    the real get_db and hung the suite for over an hour. Every test gets a no-op; test_bridge_accounting.py tests the real one."""
    if request.module.__name__ == "test_bridge_accounting":
        return
    try:
        from hvf_web import order_bridge
    except Exception:
        return
    monkeypatch.setattr(order_bridge, "_alert_unaccounted", lambda unordered, sigs: [])
