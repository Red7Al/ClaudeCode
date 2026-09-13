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
