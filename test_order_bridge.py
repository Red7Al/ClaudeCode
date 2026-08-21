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
