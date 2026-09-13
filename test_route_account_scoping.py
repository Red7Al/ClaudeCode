# ======================================================================================================================
# File:         test_route_account_scoping.py
# Created:      2026-09-12
#
# THE CLASS GATE for "one login sees another account's data".
#
# WHY THIS EXISTS. /api/positions has now failed this way TWICE. On 2026-08-25 it was found serving the real open book
# to anonymous visitors and an auth check was added. On 2026-09-12 it was found still serving the OWNER's book to every
# logged-in user, because it authenticates the caller and then reads IG on the module-global session instead of the
# caller's. Both times the instance was fixed and the class was never gated, so the second occurrence was invisible
# until someone swept by hand again.
#
# These tests are that gate. They read server.py as text and enumerate EVERY route, so a route added in six months is
# covered without anyone remembering this file exists.
#
# FAIL-CLOSED BY DESIGN. A new route that touches account state fails until it is either fixed or added to an allowlist
# WITH A WRITTEN REASON. "I did not think about it" cannot be the default, because that is precisely what happened.
# ======================================================================================================================

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "hvf_web" / "server.py"

# ig_shim calls that read or act on ONE BROKER ACCOUNT. Reading any of these without acting_session(caller) returns
# whichever account the module-global session happens to hold -- in practice the owner's.
ACCOUNT_IG_CALLS = (
    "get_open_positions", "get_working_orders", "get_account_balance", "get_account_info",
    "get_transactions", "get_closed_trades", "get_position_by_deal",
)

# Tables whose rows belong to ONE user. A route reading these must scope to the caller.
OWNED_TABLES = ("working_orders", "positions", "trade_log", "daily_pnl")

# Any of these in a route body counts as scoping the DB read to the caller.
DB_SCOPING_MARKERS = (
    "row_identities", "row_profile_ids", "profile_ids(", "identities(", "owns_row",
    "account_scope", "_ids", "user_id = any", "user_id=any",
)

# Deliberate exceptions. EVERY entry needs a reason, and adding one is a decision, not a formality.
# Keep this empty unless there is a real, stated justification.
ALLOWLIST_IG = {
    # "/api/example": "why this legitimately reads the global session",
}
ALLOWLIST_DB = {
    "/api/records": "aggregate scanner records, not per-account order or money rows",
    "/api/system-logs": "admin/support-gated health panel; reads count(*) only, returns no row data",
}

# A bare row COUNT discloses no one's data. Matched only when the table appears solely inside count(*).
_COUNT_ONLY = re.compile(r'count\(\s*\*\s*\)\s+from\s+(\w+)', re.I)


def _routes():
    """[(path, handler_name, body)] for every @app.route in server.py, body = up to the next decorator."""
    src = SERVER.read_text(encoding="utf-8", errors="replace")
    marks = [(m.start(), m.group(1)) for m in re.finditer(r'@app\.route\(\s*["\']([^"\']+)["\']', src)]
    out = []
    for i, (pos, path) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(src)
        body = src[pos:end]
        name = (re.search(r'def\s+(\w+)\s*\(', body) or [None, "?"])[1]
        out.append((path, name, body))
    return out


def _reads_token(body: str) -> bool:
    return "name_for_token" in body


def test_server_source_is_readable():
    """Guard the guard: if this file stops finding routes, every test below would pass vacuously."""
    routes = _routes()
    assert len(routes) > 40, f"only {len(routes)} routes parsed from server.py -- the parser has broken"
    assert any(r[0] == "/api/positions" for r in routes)


@pytest.mark.parametrize("path,name,body", _routes(), ids=lambda v: v if isinstance(v, str) else "")
def test_ig_reads_are_scoped_to_the_caller(path, name, body):
    """A route reading ONE broker account must read it AS THE CALLER.

    `acting_session(name)` swaps the session to the caller's own IG account. Without it, ig_shim.session_for
    returns the module-global session and the caller receives the OWNER's live data. That is the exact defect
    found on /api/positions on 2026-09-12.
    """
    used = [c for c in ACCOUNT_IG_CALLS if c in body]
    if not used:
        return
    if path in ALLOWLIST_IG:
        return
    assert "acting_session" in body, (
        f"{path} ({name}) calls {used} without acting_session(caller).\n"
        f"It therefore returns whichever account the global IG session holds -- the owner's -- to ANY caller.\n"
        f"Correct pattern, already used by /api/position-filter-audit:\n"
        f"    name = _wu.name_for_token(request.headers.get('X-Auth') or '')\n"
        f"    if not name: return jsonify({{'error': 'login required'}}), 401\n"
        f"    if ig_shim.session_for(name) is None: return jsonify({{...}})   # no creds for this user\n"
        f"    with ig_shim._IG_LOCK, ig_shim.acting_session(name):\n"
        f"        ...\n"
        f"If this route is a genuine exception, add it to ALLOWLIST_IG with a written reason."
    )


@pytest.mark.parametrize("path,name,body", _routes(), ids=lambda v: v if isinstance(v, str) else "")
def test_owned_table_reads_are_scoped(path, name, body):
    """A route reading a per-user table must scope it to the caller, not return every account's rows."""
    hit = [t for t in OWNED_TABLES if re.search(r'\b(from|into|update)\s+' + t + r'\b', body)]
    if not hit or path in ALLOWLIST_DB:
        return

    # A write that STAMPS the caller as the owner is correctly scoped -- that is how a row acquires an
    # owner in the first place. /api/preorder-delete does exactly this (`uid=name`), and an earlier
    # version of this test flagged it. A gate with false positives gets disabled, so this is precise:
    # the route must insert into the table AND bind the authenticated caller into that statement.
    inserts = bool(re.search(r'insert\s+into\s+(' + "|".join(OWNED_TABLES) + r')\b', body, re.I))
    reads_or_mutates = bool(re.search(
        r'\b(select[^;"\']{0,400}?\bfrom|update|delete\s+from)\s+(' + "|".join(OWNED_TABLES) + r')\b', body, re.I))
    stamps_caller = bool(re.search(r'\b(uid|user_id)\s*=\s*name\b', body))
    if inserts and stamps_caller and not reads_or_mutates:
        return

    assert any(m in body for m in DB_SCOPING_MARKERS), (
        f"{path} ({name}) reads {hit} with no account scoping.\n"
        f"Every row of every user is returned. Scope with account_scope (row_identities / profile_ids),\n"
        f"remembering working_orders.user_id carries BOTH a login name and a profile uuid, so it is a SET test."
    )


@pytest.mark.parametrize("path,name,body", _routes(), ids=lambda v: v if isinstance(v, str) else "")
def test_account_routes_identify_the_caller(path, name, body):
    """A route touching account state must know who is asking. It cannot scope otherwise."""
    touches = any(c in body for c in ACCOUNT_IG_CALLS) or any(
        re.search(r'\b(from|into|update)\s+' + t + r'\b', body) for t in OWNED_TABLES)
    if not touches or path in ALLOWLIST_IG or path in ALLOWLIST_DB:
        return
    assert _reads_token(body), (
        f"{path} ({name}) touches account state without reading X-Auth, so it cannot know whose data to return."
    )


def test_every_client_fetch_of_an_account_route_sends_the_token():
    """The server-side gate is only half of it -- the CALLER must send its token.

    /api/working-orders was correctly scoped server-side while its only caller (app.js) fetched it with no
    X-Auth header. The route then scoped to a sentinel, returned zero rows, and the duplicate-order
    suppression silently switched off for everyone -- which risks a SECOND live order on an instrument the
    account already holds. Seeing too FEW rows is the dangerous direction for a duplicate guard.
    """
    app_js = (ROOT / "hvf_web" / "app.js").read_text(encoding="utf-8", errors="replace")
    account_routes = ("/api/working-orders", "/api/positions", "/api/ig-account", "/api/fees", "/api/order-ops")
    bad = []
    for m in re.finditer(r'fetch\(\s*(["\'`])([^"\'`]*?/api/[^"\'`?]*)[^)]*\)', app_js):
        route = m.group(2)
        if not any(route.endswith(r) for r in account_routes):
            continue
        window = app_js[m.start():m.start() + 420]          # the fetch call plus its options object
        if "X-Auth" not in window:
            line = app_js[:m.start()].count("\n") + 1
            bad.append(f"app.js:{line} fetch({route}) sends no X-Auth header")
    assert not bad, (
        "Account data fetched without the caller's token:\n  " + "\n  ".join(bad) +
        "\nThe route cannot scope to a user it cannot identify, so it returns nothing -- and a duplicate\n"
        "guard that sees nothing stops suppressing, which places a second real order."
    )
