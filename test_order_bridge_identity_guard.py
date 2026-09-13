# ======================================================================================================================
# File:         test_order_bridge_identity_guard.py
# Created:      2026-09-13
#
# THE OTHER HALF OF THE REVOCATION PAIR.
#
# account_scope._load_bindings stopped merging the hard-coded floor over a successful database read, so revoking a
# binding now actually revokes it. That is only safe because the bridge refuses to place orders when no trading
# profile resolves for the acting account.
#
# WHY REFUSING IS THE SAFE ANSWER, and why it is the OPPOSITE of the money reads. working_orders carries two identity
# namespaces; measured 2026-09-12, 263 of 482 rows are keyed on the profile UUID rather than the login name. If the
# profile binding is lost, those rows become invisible to the duplicate guard while the identity set still looks
# populated (identities() always includes the login name, so it is never empty). The guard would then report "no
# working orders" for an account holding several, and the bridge would place a SECOND live order on an instrument it
# already holds. For a money read an empty scope safely shows nothing; for this guard it doubles real exposure.
# ======================================================================================================================

import pytest

import account_scope
from hvf_web import order_bridge


def test_no_trading_profile_refuses_to_place(monkeypatch):
    """The guard raises rather than returning an empty skip set."""
    monkeypatch.setattr(account_scope, "row_profile_ids", lambda owner=None: [])
    with pytest.raises(order_bridge.IdentityUnresolved):
        order_bridge._already_working("nobody")


def test_a_failed_working_orders_read_refuses_to_place(monkeypatch):
    """A database failure must not be read as 'this account holds nothing'.

    This path used to log a warning and continue with an empty skip set, which is the same hazard as a
    missing binding: the bridge places on an instrument the account already has an order for.
    """
    monkeypatch.setattr(account_scope, "row_profile_ids", lambda owner=None: ["p1"])
    monkeypatch.setattr(account_scope, "row_identities", lambda owner=None: ["Someone", "p1"])
    monkeypatch.setattr("db_pool.get_db",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    with pytest.raises(order_bridge.IdentityUnresolved):
        order_bridge._already_working("Someone")


def test_run_bridge_places_nothing_when_identity_is_unresolved(monkeypatch):
    """The abort must reach the caller: a pass that cannot build the guard places NO orders.

    Asserting on the real run_bridge rather than on _already_working alone, because the defect that
    matters is an order reaching IG -- not an exception being raised somewhere inside.
    """
    placed = []
    monkeypatch.setattr(order_bridge, "_candidates",
                        lambda *a, **k: [{"ticker": "AAA", "quality": 99}])
    monkeypatch.setattr(order_bridge, "_already_working",
                        lambda *a, **k: (_ for _ in ()).throw(
                            order_bridge.IdentityUnresolved("no profile")))
    monkeypatch.setattr("ig_shim.place_hvf_order_from_sig",
                        lambda *a, **k: placed.append(a) or {"ok": True})

    summary = order_bridge.run_bridge()

    assert placed == [], "the bridge placed an order despite an unusable duplicate guard"
    assert summary.get("placed", 0) == 0
    assert "aborted" in summary, "the abort was not reported in the pass summary"
