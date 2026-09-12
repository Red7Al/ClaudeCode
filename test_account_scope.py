"""account_scope: which rows belong to which user.

NO USER IS NAMED IN THESE TESTS. Every login comes from the real registry (hvf_web.web_users) and every
binding from user_profiles.login, so adding, removing or renaming a user cannot make a test wrong or
quietly vacuous. The first version of this file hard-coded a login and was rightly rejected: the system
has five logins and three trading profiles, and a test that knows one name tests one name.

The behavioural proof that the SQL actually filters lives in test_working_orders_tenant_scope.py.
"""
import pytest

import account_scope as A

# Registry-driven by design: these read the real web-user store and user_profiles. The CI-safe half --
# the isolation LOGIC over an injected multi-user map -- is test_account_scope_pure.py.
pytestmark = pytest.mark.live_state


@pytest.fixture(scope="module")
def logins():
    """Every real web login."""
    from hvf_web import web_users as wu
    names = [u["name"] for u in wu.list_users() if u.get("name")]
    assert names, "no web logins found; the registry is the input to every test here"
    return names


@pytest.fixture(scope="module")
def bound(logins):
    """Logins WITH a bound trading profile -- these may act on an account."""
    return [n for n in logins if A.profile_ids(n)]


@pytest.fixture(scope="module")
def unbound(logins):
    """Logins WITHOUT a bound trading profile -- they own web rows but no trading account."""
    return [n for n in logins if not A.profile_ids(n)]


# ----------------------------------------------------------------------------------------------
# The system is multi-user. These assert the premise rather than assuming it.
# ----------------------------------------------------------------------------------------------

def test_there_is_more_than_one_login(logins):
    assert len(logins) > 1, f"expected several logins, found {logins}"


def test_at_least_one_login_can_trade_and_the_binding_comes_from_data(bound):
    assert bound, "no login is bound to a trading profile; every scoping read would match nothing"
    assert A.trading_logins() == sorted(bound)


def test_an_unbound_profile_is_owned_by_nobody():
    """Unbound is deliberate, not an oversight: binding a profile authorises acting on its orders and
    its money. Wife and Son carry PLACEHOLDER IG account ids and stay unbound."""
    from db_pool import get_db
    with get_db() as db:
        rows = db.run("select id from user_profiles where login is null") or []
    for (pid,) in rows:
        assert A.login_for_profile(pid) is None, f"profile {pid} is unbound but resolved to a login"


# ----------------------------------------------------------------------------------------------
# Isolation. The account owner's rule: one user's rows are of no relationship to another's.
# ----------------------------------------------------------------------------------------------

def test_no_login_can_claim_another_logins_identity(logins):
    """The core property, checked for EVERY PAIR. If any login's identity set overlaps another's, their
    orders and money merge."""
    sets = {n: set(A.identities(n)) for n in logins}
    for a in logins:
        for b in logins:
            if a == b:
                continue
            overlap = sets[a] & sets[b]
            assert not overlap, f"{a} and {b} share identities {overlap}"


def test_no_login_can_claim_another_logins_trading_profile(logins):
    for a in logins:
        mine = set(A.profile_ids(a))
        for b in logins:
            if a == b:
                continue
            assert not (mine & set(A.profile_ids(b))), f"{a} and {b} share a trading profile"


def test_every_login_owns_its_own_name(logins):
    """Rows written by the web path are keyed on the login, so even a user with no trading account
    owns their own pre-order dismissals and watch entries."""
    for n in logins:
        assert A.owns_row(n, n), f"{n} does not own rows recorded under their own login"


def test_owns_row_is_false_across_every_pair(logins):
    for a in logins:
        for b in logins:
            if a == b:
                continue
            assert not A.owns_row(a, b), f"{a} owns a row recorded under {b}"
            for pid in A.profile_ids(b):
                assert not A.owns_row(a, pid), f"{a} owns {b}'s trading profile {pid}"


def test_an_unbound_login_gets_no_trading_profile(unbound):
    """Empty is the safe answer: `user_id = any('{}')` matches nothing, so an unbound login sees a zero
    fee basis rather than somebody else's realised profit."""
    if not unbound:
        pytest.skip("every login is currently bound to a trading profile")
    for n in unbound:
        assert A.profile_ids(n) == [], f"{n} is unbound but was given {A.profile_ids(n)}"


# ----------------------------------------------------------------------------------------------
# Shape and edge cases
# ----------------------------------------------------------------------------------------------

def test_a_login_appears_under_both_identity_namespaces(bound):
    """working_orders.user_id carries a login name OR a user_profiles UUID, and a trading login appears
    under both. An equality test on either alone loses half their rows, which would make the duplicate
    guard place a second order on an instrument already held."""
    for n in bound:
        ids = A.identities(n)
        assert n in ids
        for pid in A.profile_ids(n):
            assert pid in ids
        assert len(ids) == 1 + len(A.profile_ids(n))


def test_an_unknown_login_gets_only_itself_and_never_a_profile():
    assert A.identities("no-such-user-zzz") == ["no-such-user-zzz"]
    assert A.profile_ids("no-such-user-zzz") == []


def test_an_empty_login_scopes_to_nothing_rather_than_to_someone():
    """Ambiguity must not resolve in a user's favour. A web path with no authenticated login must
    match no rows at all."""
    for empty in (None, "", "   "):
        assert A.identities(empty) == []
        assert A.profile_ids(empty) == []


def test_surrounding_whitespace_does_not_create_a_second_identity(logins):
    for n in logins:
        assert A.identities(f"  {n}  ") == A.identities(n)


def test_owns_row_rejects_empty_and_null_user_ids(logins):
    for n in logins:
        assert not A.owns_row(n, None)
        assert not A.owns_row(n, "")


# ----------------------------------------------------------------------------------------------
# The scheduled-job default, and agreement with the rest of the system
# ----------------------------------------------------------------------------------------------

def test_the_job_default_profile_constant_agrees_with_the_binding_in_data():
    """DEFAULT_JOB_PROFILE_ID is a plain constant because it is a default argument and cannot read the
    database at import time. It must still be the value the binding read returns, or a scheduled job
    would act on a different profile than every run-time scoping decision."""
    assert A.DEFAULT_JOB_PROFILE_ID in A.profile_ids(A.DEFAULT_JOB_LOGIN)
    assert A.login_for_profile(A.DEFAULT_JOB_PROFILE_ID) == A.DEFAULT_JOB_LOGIN
    import run_session
    assert run_session.OWNER_USER_ID == A.DEFAULT_JOB_PROFILE_ID


def test_the_scheduled_job_default_is_a_real_bound_login(bound):
    """row_identities()/row_profile_ids() with no argument mean "the account this cron job runs as".
    That account must exist and be able to trade, or every scheduled scoping read matches nothing."""
    assert A.DEFAULT_JOB_LOGIN in bound
    assert A.row_identities() == A.identities(A.DEFAULT_JOB_LOGIN)
    assert A.row_profile_ids() == A.profile_ids(A.DEFAULT_JOB_LOGIN)


def test_the_job_default_agrees_with_the_rest_of_the_system():
    """Three places name the account a job runs as. If they drift, one module scopes to a different
    person than another and the disagreement is invisible."""
    import ig_shim
    from hvf_web import server
    assert A.DEFAULT_JOB_LOGIN == server._OWNER
    assert A.DEFAULT_JOB_LOGIN == ig_shim._OWNER_LOGIN


def test_ig_shim_resolves_profiles_through_account_scope():
    """ig_shim held its own copy of the binding as a hard-coded UUID until 2026-09-12."""
    import ig_shim
    from db_pool import get_db
    with get_db() as db:
        rows = db.run("select id, login from user_profiles") or []
    for pid, login in rows:
        assert ig_shim._web_login_for_trading_profile(pid) == A.login_for_profile(pid)
        assert A.login_for_profile(pid) == (str(login).strip() if login else None)


def test_the_floor_binding_survives_a_database_failure(monkeypatch):
    """A scoping read that returned nothing would unprotect a duplicate guard and let the bridge place
    a SECOND order on an instrument it already holds. So a DB failure must degrade to the built-in
    binding, not to an empty one."""
    def boom():
        raise RuntimeError("database unreachable")
    monkeypatch.setattr("db_pool.get_db", lambda *a, **k: boom())
    got = A._load_bindings()
    assert got == {k: tuple(v) for k, v in A._FLOOR.items()}
    for login in A._FLOOR:
        assert got[login], f"{login} lost their trading profile when the database failed"


def test_the_floor_is_a_floor_and_never_a_ceiling():
    """Data must be able to ADD users. If _load_bindings only ever returned the floor, adding a user
    would need a code change -- the defect this module exists to remove."""
    live = A.bindings(refresh=True)
    for login, ids in A._FLOOR.items():
        assert set(ids).issubset(set(live.get(login, ()))), \
            f"the built-in binding for {login} was lost when data was merged in"
    from db_pool import get_db
    with get_db() as db:
        bound_in_data = {str(r[0]).strip() for r in
                         (db.run("select login from user_profiles where login is not null") or [])}
    assert bound_in_data.issubset(set(live)), "a login bound in the data is missing from the bindings"


def test_no_module_hard_codes_a_trading_profile_uuid():
    """A test about the ABSENCE of duplication, the one thing source text can honestly prove. Three
    copies of the owner UUID existed; the binding now lives in user_profiles.login and the only
    remaining literal is account_scope's documented failure floor."""
    import pathlib
    from db_pool import get_db
    with get_db() as db:
        uuids = [str(r[0]) for r in (db.run("select id from user_profiles") or [])]
    root = pathlib.Path(__file__).parent
    # account_scope.py holds the documented failure floor. run_schema.py holds MIGRATIONS: a backfill
    # has to name the row it binds, because it cannot read the binding it is creating, and a migration
    # is a frozen historical statement that must not change when the data does.
    exempt = {"account_scope.py", "run_schema.py"}
    offenders = []
    for path in sorted(root.glob("*.py")) + sorted(root.glob("hvf_web/*.py")):
        if path.name.startswith("test_") or path.name in exempt:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for u in uuids:
                if u in line:
                    offenders.append(f"{path.name}:{n}")
    assert not offenders, ("a trading profile UUID must come from account_scope/user_profiles, but is "
                           "written out at: " + ", ".join(sorted(set(offenders))))
