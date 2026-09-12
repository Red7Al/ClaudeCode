"""Tenant and money isolation, proved by CALLING THE PRODUCTION READS against the real tables.

The account owner's rule, 2026-09-12: "Anything that Rich has is of no relationship to other users and
vice versa" and "users tx and monies must NOT be mixed up".

NO USER IS NAMED HERE. Every login comes from the real registry, so adding or renaming a user cannot
make a test wrong or quietly vacuous.

TWO THINGS THIS FILE LEARNED THE HARD WAY:

1. It calls PRODUCTION functions, not copies of their SQL. The first version re-wrote each query inline,
   and a mutation deleting `and user_id = any(:ids)` from hvf_web/order_bridge left every test green.

2. The guards are asked FROM THE OTHER SIDE. A test needing a row that is both non-owner AND live can
   only run when such a row happens to exist -- the four that caused this were expired on 2026-09-11, so
   those tests skip, and a skipped test kills no mutation. Asking each read AS a different login removes
   the data dependency: whatever the table holds, one user's rows must never come back for another.

Measured 2026-09-12: 482 working_orders rows over 5 identities; 5 web logins; 3 trading profiles of which
1 is bound. Rich's four PENDING rows on DGE.L/SPX.L/^AXJO/WTB.L (good_till 2026-07-06/07) kept the order
bridge off those tickers from July until 2026-09-11 and hid them from every login's Pre-orders tab.
"""
import pytest

import account_scope as A

pytestmark = pytest.mark.live_state


@pytest.fixture(scope="module")
def db():
    from db_pool import get_db
    with get_db() as conn:
        yield conn


@pytest.fixture(scope="module")
def logins():
    from hvf_web import web_users as wu
    names = [u["name"] for u in wu.list_users() if u.get("name")]
    assert names, "no web logins found"
    return names


@pytest.fixture(scope="module")
def job_login():
    """The account the scheduled jobs act as -- the one whose IG book they read."""
    return A.DEFAULT_JOB_LOGIN


@pytest.fixture(scope="module")
def other_logins(logins, job_login):
    """Every login that is NOT the scheduled-job account. Each is asked, in turn, to confirm it cannot
    see the job account's rows."""
    others = [n for n in logins if n != job_login]
    assert others, "only one login exists; isolation cannot be verified"
    return others


# ----------------------------------------------------------------------------------------------
# The premise. If these fail, every other test here is testing nothing.
# ----------------------------------------------------------------------------------------------

def test_the_tables_really_are_multi_tenant(db, logins):
    assert len(logins) > 1
    n = db.run("select count(distinct user_id) from working_orders")[0][0]
    assert n > 1, f"working_orders holds one identity ({n}); scoping cannot be verified"


def test_the_job_account_has_rows_to_protect(db, job_login):
    n = db.run("select count(*) from working_orders where user_id = any(:ids)",
               ids=A.identities(job_login))[0][0]
    assert n > 0, f"{job_login} has no rows at all, so scoping proves nothing"


def test_both_identity_namespaces_hold_rows(db, job_login):
    """The reason scoping is a set test. If either namespace were empty this would be
    over-engineering; measured, both are populated."""
    for ident in A.identities(job_login):
        n = db.run("select count(*) from working_orders where user_id = :u", u=ident)[0][0]
        assert n > 0, f"no rows under identity {ident!r}; the scoping assumption is wrong"


# ----------------------------------------------------------------------------------------------
# Production reads, asked as the job account: only its own rows come back.
# ----------------------------------------------------------------------------------------------

def test_the_sweeps_own_read_returns_only_its_accounts_rows(job_login):
    """run_working_order_sweep.open_rows -- the read whose rows get marked EXPIRED."""
    import run_working_order_sweep as sweep
    for r in sweep.open_rows():
        assert A.owns_row(job_login, r[9]), f"row id={r[0]} belongs to {r[9]!r} and would be swept"


def test_reconcile_fills_own_read_returns_only_its_accounts_rows(db, job_login):
    """reconcile_fills.pending_rows -- the read whose rows can be marked FILLED."""
    import reconcile_fills
    for r in reconcile_fills.pending_rows(db):
        assert A.owns_row(job_login, r[8]), f"deal {r[0]} belongs to {r[8]!r}"


def test_the_audits_own_read_returns_only_its_accounts_rows(job_login):
    """audit_trading_state.open_working_rows -- feeds the findings that turn the nightly run red."""
    import audit_trading_state as ats
    for rec in ats.open_working_rows():
        assert A.owns_row(job_login, rec["user_id"]), \
            f"row id={rec['id']} belongs to {rec['user_id']!r}"


# ----------------------------------------------------------------------------------------------
# The same guards asked as EVERY OTHER LOGIN -- no dependence on today's data.
# ----------------------------------------------------------------------------------------------

def _job_live_tickers(db, job_login):
    rows = db.run("select distinct ticker from working_orders "
                  "where status in ('PENDING','WATCHING') and user_id = any(:ids)",
                  ids=A.identities(job_login)) or []
    return sorted({r[0] for r in rows})


def test_the_sweep_asked_as_another_login_sees_none_of_the_job_accounts_rows(job_login, other_logins):
    import run_working_order_sweep as sweep
    assert sweep.open_rows(), f"{job_login} has no live rows, so this would prove nothing"
    for who in other_logins:
        leaked = [r[0] for r in sweep.open_rows(who) if A.owns_row(job_login, r[9])]
        assert not leaked, (f"asked as {who}, the sweep returned {job_login}'s rows {leaked} -- they "
                            f"would be judged against {who}'s IG book and expired")


def test_reconcile_fills_asked_as_another_login_sees_nothing_of_the_job_accounts(db, job_login,
                                                                                 other_logins):
    import reconcile_fills
    assert reconcile_fills.pending_rows(db), f"{job_login} has no PENDING rows with a deal id"
    for who in other_logins:
        leaked = [r[0] for r in reconcile_fills.pending_rows(db, who) if A.owns_row(job_login, r[8])]
        assert not leaked, (f"asked as {who}, reconcile_fills returned {job_login}'s orders {leaked} "
                            f"-- they could be marked FILLED against {who}'s positions")


def test_the_audit_asked_as_another_login_sees_nothing_of_the_job_accounts(job_login, other_logins):
    import audit_trading_state as ats
    assert ats.open_working_rows(), f"{job_login} has no live rows"
    for who in other_logins:
        leaked = [r["id"] for r in ats.open_working_rows(owner=who)
                  if A.owns_row(job_login, r["user_id"])]
        assert not leaked, f"asked as {who}, the audit returned {job_login}'s rows {leaked}"


def test_the_bridge_asked_as_another_login_does_not_inherit_the_job_skip_list(db, monkeypatch,
                                                                             job_login, other_logins):
    """The measured defect, from the other direction. If the predicate goes, one account's live
    tickers are withheld from another -- which is what happened for two months."""
    import ig_shim
    from hvf_web import order_bridge
    monkeypatch.setattr(ig_shim, "get_open_positions", lambda: [])
    mine = set(_job_live_tickers(db, job_login))
    assert mine, f"{job_login} has no live rows, so this would prove nothing"
    for who in other_logins:
        leaked = mine & order_bridge._already_working(who)
        assert not leaked, (f"asked as {who}, the bridge inherited {job_login}'s skip-list "
                            f"{sorted(leaked)} and would refuse to place {who}'s orders on them")


def test_the_bridge_still_protects_the_job_accounts_own_orders(db, monkeypatch, job_login):
    """The other half: scoping must not have switched the duplicate protection off. Placing a second
    order on an instrument already held is the failure this guard exists to prevent."""
    import ig_shim
    from hvf_web import order_bridge
    monkeypatch.setattr(ig_shim, "get_open_positions", lambda: [])
    mine = set(_job_live_tickers(db, job_login))
    assert mine, f"{job_login} has no live rows, so this would prove nothing"
    missing = mine - order_bridge._already_working()
    assert not missing, f"{job_login}'s own live orders are no longer protected: {sorted(missing)}"


def test_placement_setups_asked_as_another_login_reports_nothing_of_the_job_accounts(db, job_login,
                                                                                    other_logins):
    import order_filter_audit as ofa
    tickers = _job_live_tickers(db, job_login)
    if not tickers:
        pytest.skip(f"{job_login} has no live rows")
    for who in other_logins:
        view = ofa.placement_setups(tickers, db=db, owner=who)
        assert view == {}, (f"asked as {who}, the placement audit attributed {job_login}'s orders "
                            f"to them: {view}")


# ----------------------------------------------------------------------------------------------
# MONEY. The writes were already per-user (ig_shim inserts trade_log with the position's own user_id
# and upserts daily_pnl on (user_id, trade_date)). The defects were the READ paths that DISPLAY or
# BILL: the /api/fees fallback (1%/mo of AUM + 10%/mo of profits) and the closed-trade ledger both
# summed every account together whenever IG's own history was unavailable.
# ----------------------------------------------------------------------------------------------

MONEY_TABLES = ("daily_pnl", "trade_log", "positions")


def test_every_money_row_has_an_owner(db):
    """A money row with no owner cannot be attributed, and all three columns are nullable."""
    for t in MONEY_TABLES:
        orphans = db.run(f"select count(*) from {t} where user_id is null")[0][0]
        assert orphans == 0, f"{t} has {orphans} row(s) with no user_id -- unattributable money"


def test_every_money_row_belongs_to_a_bound_trading_profile(db):
    """Money must be attributable to a login that may act on it. A row under an unbound profile could
    never be billed or displayed to anyone."""
    for t in MONEY_TABLES:
        rows = db.run(f"select distinct user_id from {t} where user_id is not null") or []
        for (uid,) in rows:
            assert A.login_for_profile(uid), \
                f"{t} has money under profile {uid}, which is bound to no login"


def test_no_login_can_see_another_logins_money(db, logins):
    """Every pair: no money identity is shared, so no row can be billed to two logins."""
    for a in logins:
        a_ids = set(A.profile_ids(a))
        if not a_ids:
            continue
        for b in logins:
            if a != b:
                assert not (a_ids & set(A.profile_ids(b))), f"{a} and {b} share a money identity"


def test_the_per_login_money_totals_partition_the_ledger_exactly(db, logins):
    """The property the pairwise check implies but does not demonstrate: summing each login's own
    scoped total must reproduce the whole ledger with nothing double-counted and nothing orphaned.
    If two logins shared a profile the parts would exceed the whole; if a profile were unbound the
    parts would fall short."""
    for t, col in (("trade_log", "pnl"), ("daily_pnl", "total_pnl")):
        whole = db.run(f"select count(*), coalesce(sum({col}),0) from {t}")[0]
        rows = total = 0
        for n in logins:
            ids = A.profile_ids(n)
            if not ids:
                continue
            part = db.run(f"select count(*), coalesce(sum({col}),0) from {t} "
                          f"where user_id = any(:ids)", ids=ids)[0]
            rows += part[0]
            total += part[1]
        assert rows == whole[0], (f"{t}: the per-login parts cover {rows} of {whole[0]} rows -- "
                                  f"either a row is counted twice or it belongs to no login")
        assert total == whole[1], f"{t}: per-login {col} sums to {total}, the ledger holds {whole[1]}"


def test_an_unbound_login_gets_a_zero_money_basis_not_somebody_elses(db, logins, job_login):
    """An unbound login has no trading profile, so `user_id = any('{}')` matches nothing -- a zero fee
    basis rather than another account's profit."""
    unbound = [n for n in logins if not A.profile_ids(n)]
    if not unbound:
        pytest.skip("every login is bound to a trading profile")
    for who in unbound:
        for t in ("daily_pnl", "trade_log"):
            n = db.run(f"select count(*) from {t} where user_id = any(:ids)",
                       ids=A.profile_ids(who))[0][0]
            assert n == 0, f"unbound login {who} matched {n} row(s) in {t}"
    owner_n = db.run("select count(*) from trade_log where user_id = any(:ids)",
                     ids=A.profile_ids(job_login))[0][0]
    assert owner_n > 0, f"{job_login} has no trade_log rows, so this would prove nothing"
