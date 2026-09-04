"""Regression coverage for the bridge's guarded Let Winners Run hand-off."""


def test_bridge_runs_guarded_stop_management_when_no_new_candidates(monkeypatch):
    """Existing positions still need a review even on a pass with no new signal candidates."""
    from hvf_web import order_bridge
    import ig_shim

    monkeypatch.setattr(order_bridge, "_candidates", lambda: [])
    monkeypatch.setattr(ig_shim, "run_let_winners_run", lambda: {"disabled": True, "checked": 0})

    summary = order_bridge.run_bridge()

    assert summary["candidates"] == 0
    assert summary["let_winners_run"] == {"disabled": True, "checked": 0}


# ======================================================================================================
# The duplicate guard must cover OPEN POSITIONS, not just working orders (2026-09-04).
#
# THE BUG THIS PREVENTS. _already_working() read working_orders in PENDING/WATCHING only. Nothing had
# reconciled that table since the session monitors were switched off, so filled orders sat there as
# PENDING for ever -- and those stale rows were the only thing keeping the bridge off instruments the
# account already held. Clearing 89 phantom rows removed 12 covering live holdings and exposed it:
# measured that day, none of the 14 open positions appeared on the skip-list.
# ======================================================================================================

def _stub_db(monkeypatch, wo_rows, epic_rows):
    class _Db:
        def run(self, sql, **k):
            if "from working_orders" in sql:
                return wo_rows
            if "from epic_lookup" in sql:
                return epic_rows
            raise AssertionError(sql)

        def close(self):
            pass

    monkeypatch.setattr("db_pool.get_db", lambda: _Db(), raising=False)


def test_an_instrument_already_held_is_not_ordered_again(monkeypatch):
    from hvf_web import order_bridge
    import ig_shim

    _stub_db(monkeypatch, [("PENDING.L",)], [("HELD.L", "KA.D.HELD.DAILY.IP")])
    monkeypatch.setattr(ig_shim, "get_open_positions",
                        lambda: [{"market": {"epic": "KA.D.HELD.DAILY.IP"}}])

    skip = order_bridge._already_working()

    assert "HELD.L" in skip, "an open position is a stronger reason not to re-order than a pending one"
    assert "PENDING.L" in skip, "the working-order half must still apply"


def test_an_unreadable_ig_does_not_halt_placement(monkeypatch):
    """Fails open, as the working_orders half always has: an IG hiccup must not stop every order. The
    weaker guard is logged at WARNING rather than passing silently."""
    from hvf_web import order_bridge
    import ig_shim

    _stub_db(monkeypatch, [("PENDING.L",)], [])
    monkeypatch.setattr(ig_shim, "get_open_positions",
                        lambda: (_ for _ in ()).throw(RuntimeError("IG unavailable")))

    assert order_bridge._already_working() == {"PENDING.L"}


def test_a_position_whose_epic_is_unknown_is_not_silently_dropped_from_the_db_half(monkeypatch):
    """An epic missing from epic_lookup cannot be resolved to a ticker, so it cannot be skipped -- but it
    must not take the working-order half down with it."""
    from hvf_web import order_bridge
    import ig_shim

    _stub_db(monkeypatch, [("PENDING.L",)], [])
    monkeypatch.setattr(ig_shim, "get_open_positions", lambda: [{"market": {"epic": "XX.D.NOPE.IP"}}])

    assert order_bridge._already_working() == {"PENDING.L"}
