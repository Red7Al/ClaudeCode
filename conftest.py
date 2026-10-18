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
