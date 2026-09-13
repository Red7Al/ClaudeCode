"""account_scope isolation logic, with the binding map INJECTED -- no database, so CI runs it.

test_account_scope.py proves the real registry is wired up correctly; that needs the live Supabase user
store and is deselected in CI. This file proves the property that matters -- one user's rows are of no
relationship to another's -- over a synthetic map with SEVERAL users, INCLUDING a user bound to two
trading profiles and a user bound to none. Neither case exists in today's data, which is exactly why it
has to be tested here rather than against the table.
"""
import pytest

import account_scope as A


# Deliberately not the real users: three bound (one with TWO profiles), two unbound.
FAKE = {
    "ann":   ("11111111-1111-1111-1111-111111111111",),
    "bob":   ("22222222-2222-2222-2222-222222222222",
              "33333333-3333-3333-3333-333333333333"),   # one login, two trading accounts
    "cerys": ("44444444-4444-4444-4444-444444444444",),
}
UNBOUND = ("dai", "eve")
ALL_LOGINS = tuple(FAKE) + UNBOUND


@pytest.fixture(autouse=True)
def injected(monkeypatch):
    """Freeze the binding map so no test here touches a database."""
    monkeypatch.setattr(A, "_cache", {"at": float("inf"), "map": dict(FAKE)})
    yield


def test_identities_cover_the_login_name_and_every_bound_profile():
    for login, pids in FAKE.items():
        ids = A.identities(login)
        assert ids[0] == login
        for p in pids:
            assert p in ids
        assert len(ids) == 1 + len(pids)


def test_a_login_with_two_trading_profiles_scopes_to_BOTH():
    """A login bound to two accounts must see both, or half its orders vanish and the duplicate guard
    stops protecting the other half."""
    assert set(A.profile_ids("bob")) == set(FAKE["bob"])
    assert len(A.profile_ids("bob")) == 2
    for p in FAKE["bob"]:
        assert A.owns_row("bob", p)


def test_an_unbound_login_owns_its_own_name_but_no_trading_profile():
    for n in UNBOUND:
        assert A.identities(n) == [n]
        assert A.profile_ids(n) == []
        assert A.owns_row(n, n)


def test_no_two_logins_share_an_identity():
    sets = {n: set(A.identities(n)) for n in ALL_LOGINS}
    for a in ALL_LOGINS:
        for b in ALL_LOGINS:
            if a != b:
                assert not (sets[a] & sets[b]), f"{a} and {b} share {sets[a] & sets[b]}"


def test_owns_row_is_false_for_every_other_users_rows():
    for a in ALL_LOGINS:
        for b in ALL_LOGINS:
            if a == b:
                continue
            assert not A.owns_row(a, b)
            for p in FAKE.get(b, ()):
                assert not A.owns_row(a, p), f"{a} claimed {b}'s trading profile"


def test_login_for_profile_maps_each_profile_to_exactly_one_login():
    for login, pids in FAKE.items():
        for p in pids:
            assert A.login_for_profile(p) == login


def test_login_for_profile_is_None_for_an_unbound_profile():
    assert A.login_for_profile("99999999-9999-9999-9999-999999999999") is None
    assert A.login_for_profile(None) is None
    assert A.login_for_profile("") is None


def test_trading_logins_lists_exactly_the_bound_ones():
    assert A.trading_logins() == sorted(FAKE)


def test_an_empty_login_scopes_to_nothing():
    """Ambiguity must not resolve in a user's favour: a request with no authenticated login matches
    no rows rather than somebody's."""
    for empty in (None, "", "   "):
        assert A.identities(empty) == []
        assert A.profile_ids(empty) == []
        assert not A.owns_row(empty, "ann")
        assert not A.owns_row(empty, "11111111-1111-1111-1111-111111111111")


def test_an_unknown_login_never_inherits_a_profile():
    assert A.identities("mallory") == ["mallory"]
    assert A.profile_ids("mallory") == []
    for pids in FAKE.values():
        for p in pids:
            assert not A.owns_row("mallory", p)


def test_whitespace_is_not_a_different_user():
    for n in ALL_LOGINS:
        assert A.identities(f"  {n} ") == A.identities(n)
        assert A.profile_ids(f"  {n} ") == A.profile_ids(n)


def test_owns_row_rejects_null_and_empty_user_ids():
    for n in ALL_LOGINS:
        assert not A.owns_row(n, None)
        assert not A.owns_row(n, "")


def test_the_floor_degrades_rather_than_empties_when_the_database_fails(monkeypatch):
    """A scoping read that returned nothing would unprotect a duplicate guard and let the bridge place
    a SECOND order on an instrument it already holds."""
    monkeypatch.setattr("db_pool.get_db",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unreachable")))
    got = A._load_bindings()
    assert got == {k: tuple(v) for k, v in A._FLOOR.items()}
    assert all(ids for ids in got.values()), "a floor binding lost its trading profile"


def _stub_db(monkeypatch, rows):
    """Make _load_bindings' read return exactly `rows`, successfully."""
    class _FakeDB:
        def run(self, *a, **k):
            return list(rows)

        def close(self):
            pass

    monkeypatch.setattr("db_pool.get_db", lambda *a, **k: _FakeDB())


def test_data_can_add_users_without_a_code_change(monkeypatch):
    """The bindings must not be a ceiling: adding a user is a data change, not a code change."""
    _stub_db(monkeypatch, [("newcomer", "55555555-5555-5555-5555-555555555555")])
    got = A._load_bindings()
    assert "newcomer" in got, "a login bound in data did not reach the bindings"
    assert got["newcomer"] == ("55555555-5555-5555-5555-555555555555",)


def test_revoking_a_binding_in_data_actually_revokes_it(monkeypatch):
    """A SUCCESSFUL read that omits a login means that login is NOT bound. The floor must not resurrect it.

    Until 2026-09-13 the floor was merged over the database's answer, so an administrator could delete
    every binding row and the floor login stayed bound: revocation silently did nothing, while reporting
    success. Measured 2026-09-12 by exercising the merge.

    This is safe ONLY because the order bridge now refuses to place when no trading profile resolves
    (order_bridge.IdentityUnresolved). The two are a pair -- see the companion test in
    test_order_bridge_identity_guard.py. Breaking either one re-opens a real defect.
    """
    _stub_db(monkeypatch, [])                       # the table answers, and says nobody is bound
    got = A._load_bindings()
    for login in A._FLOOR:
        assert login not in got or not got.get(login), (
            f"{login} stayed bound after the database reported no bindings -- revocation is a no-op")


def test_an_outage_is_not_a_revocation(monkeypatch):
    """A read that FAILS must fall back to the floor, not report everyone revoked.

    The distinction this test pins: 'the database says no binding' is an answer and is obeyed; 'the
    database did not answer' is an outage and must not shrink anyone's identity set.
    """
    monkeypatch.setattr("db_pool.get_db",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unreachable")))
    got = A._load_bindings()
    assert got == {k: tuple(v) for k, v in A._FLOOR.items()}, "an outage did not fall back to the floor"
