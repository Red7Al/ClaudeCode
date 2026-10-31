"""The Order Bridge orders exactly the open triggers on the owner's Scanner Report (owner 2026-10-31:
"never was more than one calculation needed").

Measured 2026-10-30: 40 of the 66 triggers on his report had no working_orders row. The bridge kept its own
definition -- the snapshot's current funnel, judged on latest-bar values, inside 1.5% of the scan price,
rejects dropped without a log line. Fixture: MET's row on his live report that day (snapshot 19:08:13), which
the bridge blocked on VolumeScore 3 against 9 at its trigger; its scan price sits 1.52% past entry.
"""

import trading_limits
from hvf_web import order_bridge, server

_REAL_ORDERED_TRIGGERS = order_bridge._ordered_triggers

LIMITS = {**trading_limits.limit_defaults(), "min_risk_reward": 3.1, "max_risk_reward": 100.0,
          "min_quality": 45, "min_rvol": 1.7, "min_volume_score": 4, "min_instrument_value": 485e6,
          "max_instrument_value": 0, "require_above_vwap": 1, "require_atr_expanding": 1}

MET_SETUP = {"trig_date": "2026-10-23", "direction": "BULL", "timeframe": "daily-240", "entry": 98.449997,
             "stop": 97.464684, "target": 105.614998, "rr": 7.27, "quality": 55, "rvol": 1.91,
             "volume_score": 9.0, "above_vwap": True, "atr_expanding": True, "h1_date": "2026-08-06",
             "h3_date": "2026-09-25", "l3_date": "2026-10-23", "outcome": "OPEN"}
MET_MCAP = 46955193443.80129
MET_SCAN = {"ticker": "MET", "has_signal": True, "status": "TRIGGERED", "direction": "BULL", "quality": 55,
            "entry": 98.449997, "stop": 97.464684, "target": 105.614998, "rr": 7.27, "timeframe": "daily-240",
            "current_price": 99.949997, "market": "S&P 500", "location": "US", "_card": {}}


def _wire(monkeypatch, records, setups):
    import scanner_snapshot_store
    monkeypatch.setattr(scanner_snapshot_store, "load_snapshot",
                        lambda path: {"generated_utc": "2026-10-30T19:08:13.687802+00:00", "records": records})
    monkeypatch.setattr(server, "_open_trigger_setups", lambda snap: setups)
    monkeypatch.setattr(trading_limits, "user_limits", lambda name: LIMITS)
    monkeypatch.setattr(server, "_mcap_map", lambda: {"MET": MET_MCAP})
    monkeypatch.setattr(server, "_user_trade_allows", lambda name, rec: True)
    monkeypatch.setattr("config_store.cfg_num", lambda key, default=None: 30)
    monkeypatch.setattr("run_session.get_user_profile", lambda: {"name": "Owner", "user_id": "u1"})
    monkeypatch.setattr(trading_limits, "login_for_profile", lambda profile: "Alex")
    monkeypatch.setattr(order_bridge, "_already_working", lambda: set())
    monkeypatch.setattr(order_bridge, "_ordered_triggers", lambda sigs, owner=None: set())
    monkeypatch.setattr(server, "_live_vwap_atr", lambda snap: {})
    monkeypatch.setattr("ig_shim.run_let_winners_run", lambda: None)
    placed = []
    monkeypatch.setattr("ig_shim.place_hvf_order_from_sig",
                        lambda sig, profile, source, mult, **kw: placed.append((sig, kw)) or None)
    return placed


def test_a_report_trigger_goes_to_the_engine_with_its_trigger_date_values(monkeypatch):
    placed = _wire(monkeypatch, [MET_SCAN], {"MET": [MET_SETUP]})
    order_bridge.run_bridge()
    assert len(placed) == 1, "MET is on the owner's report and must reach the order engine"
    sig, kw = placed[0]
    assert (sig["ticker"], sig["direction"], sig["hvf_h3_level"], sig["hvf_stop_level"], sig["hvf_target"]) == \
        ("MET", "BUY", 98.449997, 97.464684, 105.614998)
    assert (sig["volume_score"], sig["rvol"], sig["above_vwap"], sig["atr_expanding"], sig["mcap"]) == \
        (9.0, 1.91, True, True, MET_MCAP), "judged on the trigger date, not the latest bar"
    assert kw.get("watch") is False, "a WATCHING row is never promoted while the session monitors are off"


def test_a_triggered_scan_record_the_report_hides_is_never_ordered(monkeypatch):
    """Inside 1.5% of entry and over the quality floor -- the old bridge ordered it -- but no setup passed the
    owner's limits on its trigger date, so his report hides it."""
    near = dict(MET_SCAN, current_price=98.6)
    placed = _wire(monkeypatch, [near], {"MET": [dict(MET_SETUP, volume_score=3.0)]})
    order_bridge.run_bridge()
    assert placed == []


def test_nothing_triggered_is_ordered_when_the_trigger_history_cannot_be_read(monkeypatch):
    near = dict(MET_SCAN, current_price=98.6)
    placed = _wire(monkeypatch, [near], None)
    assert order_bridge.main() == 1, "an unreadable report must turn the run red, so Cron Watch emails"
    assert placed == []


def test_a_report_calculation_that_raises_aborts_the_pass(monkeypatch):
    """2026-10-31 review: any exception from the report was logged as a warning and the pass went on green."""
    placed = _wire(monkeypatch, [dict(MET_SCAN, current_price=98.6)], {"MET": [MET_SETUP]})
    monkeypatch.setattr(server, "report_order_sigs", lambda login, snap: 1 / 0)
    assert order_bridge.run_bridge().get("aborted")
    assert placed == []


def test_a_record_with_no_scan_price_sorts_last():
    assert [r["ticker"] for r in sorted(
        [{"ticker": "NOPX", "entry": 10.0, "current_price": None}, {"ticker": "FAR", "entry": 10.0, "current_price": 12.0}],
        key=order_bridge._candidate_order)] == ["FAR", "NOPX"]


def test_a_finished_trigger_is_not_ordered(monkeypatch):
    near = dict(MET_SCAN, current_price=98.6)
    placed = _wire(monkeypatch, [near], {"MET": [dict(MET_SETUP, outcome="TARGET")]})
    order_bridge.run_bridge()
    assert placed == []


def test_the_report_create_an_order_and_the_bridge_share_one_pick(monkeypatch):
    """One calculation: replace _report_pick and all three consumers must follow it."""
    _wire(monkeypatch, [MET_SCAN], {"MET": [MET_SETUP]})
    calls = []
    monkeypatch.setattr(server, "_report_pick", lambda name, rec, setups, limits, mcap:
                        calls.append(rec.get("ticker")) or None)
    assert server.report_order_sigs("Alex", {"records": [MET_SCAN]}) == {}
    monkeypatch.setattr(server, "_record", lambda tk: MET_SCAN)
    monkeypatch.setattr(server, "_load_snapshot", lambda: {"records": [MET_SCAN]})
    monkeypatch.setattr(server, "_sig_from_snapshot", lambda tk, user=None: None)
    assert server._order_sig("MET", "Alex") == (None, False)
    assert calls == ["MET", "MET"]


def test_a_trigger_already_ordered_is_never_ordered_again(monkeypatch):
    """Ordered on 10-24, stopped out at IG, still OPEN on the report: placing it again every pass would
    re-enter a stopped trade. Any status counts -- the row is the record that the trigger was acted on."""
    placed = _wire(monkeypatch, [MET_SCAN], {"MET": [MET_SETUP]})
    import datetime

    class _Db:
        def run(self, sql, **k):
            assert "from working_orders" in sql
            return [["MET", "BUY", datetime.datetime(2026, 10, 24, 14, 0, tzinfo=datetime.timezone.utc), None]]

        def close(self):
            pass

    monkeypatch.setattr("db_pool.get_db", lambda: _Db())
    monkeypatch.setattr(order_bridge.account_scope, "row_identities", lambda owner=None: ["u1"])
    monkeypatch.setattr(order_bridge, "_ordered_triggers", _REAL_ORDERED_TRIGGERS)
    order_bridge.run_bridge()
    assert placed == []


def test_an_order_placed_before_the_trigger_does_not_count(monkeypatch):
    placed = _wire(monkeypatch, [MET_SCAN], {"MET": [MET_SETUP]})
    import datetime

    class _Db:
        def run(self, sql, **k):
            return [["MET", "BUY", datetime.datetime(2026, 9, 2, 9, 0, tzinfo=datetime.timezone.utc), None],
                    ["MET", "SELL", datetime.datetime(2026, 10, 25, 9, 0, tzinfo=datetime.timezone.utc), None]]

        def close(self):
            pass

    monkeypatch.setattr("db_pool.get_db", lambda: _Db())
    monkeypatch.setattr(order_bridge.account_scope, "row_identities", lambda owner=None: ["u1"])
    monkeypatch.setattr(order_bridge, "_ordered_triggers", _REAL_ORDERED_TRIGGERS)
    order_bridge.run_bridge()
    assert [s["ticker"] for s, kw in placed] == ["MET"]


def test_an_unreadable_trigger_guard_aborts_the_pass(monkeypatch):
    placed = _wire(monkeypatch, [MET_SCAN], {"MET": [MET_SETUP]})

    def _down():
        raise RuntimeError("database refused")
    monkeypatch.setattr("db_pool.get_db", _down)
    monkeypatch.setattr(order_bridge.account_scope, "row_identities", lambda owner=None: ["u1"])
    monkeypatch.setattr(order_bridge, "_ordered_triggers", _REAL_ORDERED_TRIGGERS)
    summary = order_bridge.run_bridge()
    assert placed == [] and "aborted" in summary


def test_a_preorder_that_filled_on_the_trigger_date_is_that_triggers_order(monkeypatch):
    """Placed the day before MET triggered (2026-10-22), filled on the trigger date: ordered, never again."""
    placed = _wire(monkeypatch, [MET_SCAN], {"MET": [MET_SETUP]})
    import datetime

    class _Db:
        def run(self, sql, **k):
            return [["MET", "BUY", datetime.datetime(2026, 10, 22, 15, 0, tzinfo=datetime.timezone.utc),
                     datetime.datetime(2026, 10, 23, 14, 0, tzinfo=datetime.timezone.utc)]]

        def close(self):
            pass

    monkeypatch.setattr("db_pool.get_db", lambda: _Db())
    monkeypatch.setattr(order_bridge.account_scope, "row_identities", lambda owner=None: ["u1"])
    monkeypatch.setattr(order_bridge, "_ordered_triggers", _REAL_ORDERED_TRIGGERS)
    order_bridge.run_bridge()
    assert placed == []
