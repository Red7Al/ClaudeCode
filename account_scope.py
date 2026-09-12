# ======================================================================================================================
# File:         account_scope.py
# Author:       Alex Hind
# Created:      2026-09-12
#
# Description:
#
# WHICH ROWS BELONG TO WHICH USER. One module, because eight call sites were each deciding it for themselves --
# the defect CLAUDE.md names: one fact, several pieces of code each deciding what it means.
#
# THIS SYSTEM HAS MANY USERS. Measured 2026-09-12: five web logins (Alex, Carl, Rich, Red7dp, KathrynH) and three
# trading profiles (Owner, Wife, Son). The account owner's rule is absolute: "Anything that Rich has is of no
# relationship to other users and vice versa" and "users tx and monies must NOT be mixed up". Nothing here may
# treat one user as the system and everyone else as an exception -- the first version of this module did exactly
# that, hard-coding a single login, and it was rightly rejected.
#
# TWO REGISTRIES, AND THE JOIN IS DATA, NOT CODE:
#   * web logins       -- hvf_web/web_users.py (the person signing in)
#   * trading profiles -- the `user_profiles` table (the account that holds positions and money)
# `user_profiles.name` is "Owner", not "Alex", so nothing in the data used to say which login may act on which
# account. `user_profiles.login` now carries that binding (run_schema). Adding a user is a data change.
#
# NULL login means DELIBERATELY UNBOUND: nobody may act on that profile. Wife and Son are unbound, both carrying
# PLACEHOLDER IG account ids. This keeps the rule ig_shim already stated -- "unknown profiles are deliberately
# unmanaged until an explicit binding is added" -- while making it a fact about data instead of a constant.
#
# TWO IDENTITY NAMESPACES IN ONE COLUMN. Measured over 482 working_orders rows: 265 hold a `user_profiles` UUID
# (written by the engine/IG path) and 217 hold a web LOGIN NAME (written by the web path). The same person appears
# under both, so scoping is always a SET membership test, never an equality test. `working_orders.user_id` is
# `text` and holds either; `positions.user_id`, `trade_log.user_id` and `daily_pnl.user_id` are `uuid` and hold
# only profile ids. That type difference is why the text column ended up carrying two kinds of value.
#
# Version History:
# Version Date        Author      Comment
# ------- ----------  ----------  ----------------------------------------------------------------------------------
# 1.1.0   2026-09-12  Alex Hind   Multi-user. The binding is read from user_profiles.login instead of a hard-coded
#                                 owner, so N users work without a code change. identities()/profile_ids() replace
#                                 the owner-only helpers.
# 1.0.0   2026-09-12  Alex Hind   Initial build: extracted the owner identity that run_session and ig_shim each
#                                 held separately.
# ======================================================================================================================

import logging
import threading
import time

log = logging.getLogger("account_scope")

# The one binding that existed in code before this module, kept as a FLOOR rather than as the definition.
#
# Why a floor and not just a database read: several scoping sites are duplicate guards. A reader that returns too
# FEW identities makes the order bridge think it holds no orders on an instrument and place a SECOND one. A
# transient database failure must therefore never be able to shrink this particular user's identity set. Extra
# users come from data and can be added freely; this pair cannot be lost.
_FLOOR = {"Alex": ("770a76b5-0e84-460b-b575-186c724dabdd",)}

_TTL = 300.0            # seconds; the binding changes when an administrator adds a user, not per request
_lock = threading.Lock()
_cache = {"at": 0.0, "map": None}


def _load_bindings() -> dict:
    """{login: (profile_id, ...)} read from user_profiles.login, merged over _FLOOR.

    Never raises. On any failure it returns _FLOOR, which keeps the pre-existing binding working and logs
    loudly -- a scoping read that silently returned nothing would unprotect a duplicate guard.
    """
    out = {k: tuple(v) for k, v in _FLOOR.items()}
    try:
        from db_pool import get_db
        db = get_db()
        try:
            rows = db.run("select login, id from user_profiles "
                          "where login is not null and btrim(login) <> ''") or []
        finally:
            db.close()
        for login, pid in rows:
            name = str(login).strip()
            ids = set(out.get(name, ()))
            ids.add(str(pid))
            out[name] = tuple(sorted(ids))
    except Exception as exc:
        log.warning("could not read the login->profile bindings (%s); using the built-in floor only. "
                    "Scoping stays correct for %s and any other user is treated as unbound.",
                    exc, ", ".join(sorted(_FLOOR)))
    return out


def bindings(refresh: bool = False) -> dict:
    """The cached {login: (profile_id, ...)} map."""
    with _lock:
        now = time.time()
        if refresh or _cache["map"] is None or (now - _cache["at"]) > _TTL:
            _cache["map"] = _load_bindings()
            _cache["at"] = now
        return dict(_cache["map"])


def profile_ids(login: str = None) -> list:
    """The `user_profiles` ids this login may act on. Empty when the login has no bound profile.

    For the UUID-typed columns -- positions, trade_log, daily_pnl. Empty is the safe answer: an empty
    `user_id = any(:ids)` matches no row, so an unbound login sees a zero fee basis rather than somebody
    else's realised profit.
    """
    name = (login or "").strip()
    if not name:
        return []
    return list(bindings().get(name, ()))


def identities(login: str = None) -> list:
    """Every `working_orders.user_id` value that means this login -- their login name plus any profile
    ids bound to them, because that column carries both namespaces.

    An unknown login still gets its own name: rows written by the web path are keyed on the login, so a
    user with no trading profile still owns their own pre-order dismissals and watch entries.
    """
    name = (login or "").strip()
    if not name:
        return []
    return [name] + profile_ids(name)


def owns_row(login: str, user_id) -> bool:
    """True when `user_id` (from either namespace) belongs to `login`."""
    return str(user_id or "") in set(identities(login))


def login_for_profile(profile_id) -> str:
    """The web login bound to a `user_profiles` id, or None when that profile is unbound.

    Unbound is the safe answer and stays deliberate: binding a profile means somebody may act on its
    orders and its money.
    """
    want = str(profile_id or "")
    if not want:
        return None
    for name, ids in bindings().items():
        if want in ids:
            return name
    return None


def trading_logins() -> list:
    """Every login with a bound trading profile, i.e. everyone whose rows a broker-driven job may judge."""
    return sorted(name for name, ids in bindings().items() if ids)


# ----------------------------------------------------------------------------------------------------------------------
# The account a SCHEDULED JOB acts as
# ----------------------------------------------------------------------------------------------------------------------
# This is configuration, not an assumption that one user is the system. The scheduled jobs (order bridge, sweep,
# fill reconciler, reports) each run for one account and already resolve it the same way -- hvf_web/server._OWNER
# and ig_shim._OWNER_LOGIN. Named here so `row_identities()` with no argument has ONE meaning instead of each
# caller re-deciding, and a test asserts it agrees with those two. Pass an explicit login for any per-user path.
DEFAULT_JOB_LOGIN = "Alex"

# The trading profile that login acts on, as a plain constant. Needed because it is a DEFAULT ARGUMENT
# (run_session.get_user_profile) and a default cannot perform a database read at import time -- a job
# that failed to start because the binding table was briefly unreachable would be worse than a stale
# default. Taken from the floor, so it is the same value the binding read would return, and a test
# asserts the two agree. Anything resolving a profile at RUN time must use profile_ids().
DEFAULT_JOB_PROFILE_ID = _FLOOR[DEFAULT_JOB_LOGIN][0]


def row_identities(login: str = None) -> list:
    """`identities()` for a scheduled-job caller: no argument means DEFAULT_JOB_LOGIN.

    Kept as a separate name from `identities()` so the difference is visible at the call site: this one
    silently defaults to an account, which is right for a cron job and wrong for a web request.
    """
    return identities(login or DEFAULT_JOB_LOGIN)


def row_profile_ids(login: str = None) -> list:
    """`profile_ids()` for a scheduled-job caller: no argument means DEFAULT_JOB_LOGIN.

    For the UUID-typed money columns. Always used with `user_id = any(:ids)` and never with a single
    bound value -- a login with two bound profiles must scope to both, and an unbound login must scope
    to none rather than falling back to somebody else's.
    """
    return profile_ids(login or DEFAULT_JOB_LOGIN)
