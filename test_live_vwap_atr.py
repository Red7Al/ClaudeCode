"""Tests for hvf_web.server._live_vwap_atr and its wiring into api_records / _sig_from_snapshot /
order_bridge.py.

Context (user 2026-08-11, data-completeness audit — "check all instruments have current rvol,
volumescore, above VWAP and above ATR metrics"): every has_signal record in hvf_web/snapshot.json
carries above_vwap/atr_expanding = None, 100% of the time. Root cause: price_action.get_hvf_signal_mtf()
(the function build_snapshot.py actually uses to generate signal rows) never computes VWAP position and
never merges in atr_expanding from the separate analyse_price_action() helper — those static fields have
never been populated.

First fix (same day) routed this through volume_score.py's per-ticker "components" breakdown, which is
what api_records() already used for the Scanner's VWAP/ATR ticks — correct, but that breakdown is only
ever computed for TRIGGERED setups (RVOL/VolumeScore are inherently about the break bar). READY/
DEVELOPING setups — about 45% of a typical day's has_signal rows — got above_vwap=atr_expanding=None
regardless of their true state, which the user caught by directly reporting the Scanner Report still
showing setups that shouldn't pass their "Require ATR expanding" floor. Their pushback ("for any one day
you should be able to calculate these values, shouldn't you?") was correct: volume_score._above_vwap and
_atr_expanding take ANY bar index, not specifically a trigger bar, so this now computes both directly from
fresh bars for EVERY has_signal ticker using the latest bar (today) as the reference point — no TRIGGERED
requirement at all. Wired into api_records() (so the Scanner's own display is correct for READY/DEVELOPING
too) as well as _sig_from_snapshot()/order_bridge.py (so READY pre-orders — the only status the automated
bridge ever considers per _candidates() — get real values before the personal-limit gate checks them,
not just TRIGGERED ones).
"""

import hvf_web.server as server
import volume_score


class _FakeDB:
    def close(self):
        pass


def test_live_vwap_atr_computes_for_every_has_signal_status_not_just_triggered(monkeypatch):
    """The whole point of this fix: READY/DEVELOPING rows must get real values too, not just TRIGGERED."""
    monkeypatch.setattr("db_pool.get_db", lambda: _FakeDB())
    monkeypatch.setattr(server, "_perf_bars",
                         lambda db, cutoff, lookback_days=0: {tk: [("2026-08-01", 10, 9, 9.5, 1000)] * 60
                                                               for tk in cutoff})
    monkeypatch.setattr(volume_score, "_above_vwap", lambda bars, i, bull: True)
    monkeypatch.setattr(volume_score, "_atr_expanding", lambda bars, i: False)

    snap = {"generated_utc": "test-gen-1", "records": [
        {"ticker": "AAA", "has_signal": True, "status": "TRIGGERED", "direction": "BULL"},
        {"ticker": "BBB", "has_signal": True, "status": "READY", "direction": "BULL"},
        {"ticker": "CCC", "has_signal": True, "status": "DEVELOPING", "direction": "BEAR"},
        {"ticker": "DDD", "has_signal": False, "status": None, "direction": None},
    ]}
    out = server._live_vwap_atr(snap)

    assert out == {"AAA": (True, False), "BBB": (True, False), "CCC": (True, False)}
    assert "DDD" not in out   # no-signal rows never need VWAP/ATR — not fetched at all


def test_live_vwap_atr_ticker_with_no_bars_is_skipped_not_crashed(monkeypatch):
    monkeypatch.setattr("db_pool.get_db", lambda: _FakeDB())
    monkeypatch.setattr(server, "_perf_bars", lambda db, cutoff, lookback_days=0: {})   # no bars for anyone
    snap = {"generated_utc": "test-gen-2",
            "records": [{"ticker": "AAA", "has_signal": True, "status": "READY", "direction": "BULL"}]}
    assert server._live_vwap_atr(snap) == {}


def test_live_vwap_atr_no_has_signal_records_is_empty_without_db_call(monkeypatch):
    def _boom():
        raise AssertionError("get_db() should never be called when there is nothing to fetch")
    monkeypatch.setattr("db_pool.get_db", _boom)
    snap = {"generated_utc": "test-gen-3",
            "records": [{"ticker": "AAA", "has_signal": False, "status": None, "direction": None}]}
    assert server._live_vwap_atr(snap) == {}


def test_sig_from_snapshot_uses_live_values_not_dead_static_field(monkeypatch):
    """The snapshot record itself carries above_vwap=None/atr_expanding=None (the real-world bug) —
    _sig_from_snapshot must still surface the correct live values, not that dead None."""
    rec = {"has_signal": True, "ticker": "AAPL", "direction": "BULL", "status": "TRIGGERED",
           "entry": 100.0, "stop": 95.0, "target": 120.0, "quality": 80, "rr": 4.0,
           "timeframe": "daily-240", "market": "S&P 500", "location": "US",
           "above_vwap": None, "atr_expanding": None,   # the always-dead static field
           "_card": {"hvf_type": "BULLISH"}}
    monkeypatch.setattr(server, "_record", lambda tk: rec)
    monkeypatch.setattr(server, "_load_snapshot", lambda: {"records": [rec]})
    monkeypatch.setattr(server, "_live_vwap_atr", lambda snap: {"AAPL": (True, True)})

    sig = server._sig_from_snapshot("AAPL")

    assert sig["above_vwap"] is True
    assert sig["atr_expanding"] is True


def test_sig_from_snapshot_unmatched_ticker_fails_open(monkeypatch):
    rec = {"has_signal": True, "ticker": "AAPL", "direction": "BULL", "status": "TRIGGERED",
           "entry": 100.0, "stop": 95.0, "target": 120.0, "quality": 80, "rr": 4.0,
           "timeframe": "daily-240", "market": "S&P 500", "location": "US",
           "_card": {}}
    monkeypatch.setattr(server, "_record", lambda tk: rec)
    monkeypatch.setattr(server, "_load_snapshot", lambda: {"records": [rec]})
    monkeypatch.setattr(server, "_live_vwap_atr", lambda snap: {})   # ticker not TRIGGERED / no volscore

    sig = server._sig_from_snapshot("AAPL")

    assert sig["above_vwap"] is None
    assert sig["atr_expanding"] is None


def test_order_bridge_candidates_use_live_vwap_atr_not_dead_field(monkeypatch):
    """The bridge's sig dict for a placed candidate must carry the LIVE above_vwap/atr_expanding, not
    the record's own always-None fields — this is the exact real-world scenario that let VWAP/ATR floors
    silently never block the automated 2h sweep."""
    import hvf_web.order_bridge as order_bridge

    record = {"ticker": "AAPL", "has_signal": True, "status": "READY", "quality": 90, "entry": 100.0,
              "current_price": 100.5, "direction": "BULL", "stop": 95.0, "target": 120.0, "rr": 4.0,
              "timeframe": "daily-240", "market": "S&P 500", "location": "US",
              "above_vwap": None, "atr_expanding": None, "_card": {}}   # dead static fields, real-world shape

    monkeypatch.setattr(order_bridge, "_candidates", lambda: [record])
    monkeypatch.setattr(order_bridge, "_already_working", lambda: set())
    monkeypatch.setattr("run_session.get_user_profile", lambda: {"name": "Alex", "user_id": "u1"})
    monkeypatch.setattr("hvf_web.server._live_vwap_atr", lambda snap: {"AAPL": (True, False)})
    monkeypatch.setattr("hvf_web.server._load_snapshot", lambda: {"records": [record]})

    seen_sigs = []

    def _fake_place(sig, profile, session_name, stress_mult):
        seen_sigs.append(sig)
        return None   # stop here — we only care what sig was built, not the rest of the placement path

    monkeypatch.setattr("ig_shim.place_hvf_order_from_sig", _fake_place)

    order_bridge.run_bridge()

    assert len(seen_sigs) == 1
    assert seen_sigs[0]["above_vwap"] is True
    assert seen_sigs[0]["atr_expanding"] is False


def test_order_bridge_falls_back_gracefully_if_live_lookup_unavailable(monkeypatch):
    """If hvf_web.server can't be imported/queried for any reason, the bridge must still place orders
    (VWAP/ATR just fail open, like every other optional field) rather than crash the whole pass."""
    import hvf_web.order_bridge as order_bridge

    record = {"ticker": "AAPL", "has_signal": True, "status": "READY", "quality": 90, "entry": 100.0,
              "current_price": 100.5, "direction": "BULL", "stop": 95.0, "target": 120.0, "rr": 4.0,
              "timeframe": "daily-240", "market": "S&P 500", "location": "US",
              "above_vwap": None, "atr_expanding": None, "_card": {}}

    monkeypatch.setattr(order_bridge, "_candidates", lambda: [record])
    monkeypatch.setattr(order_bridge, "_already_working", lambda: set())
    monkeypatch.setattr("run_session.get_user_profile", lambda: {"name": "Alex", "user_id": "u1"})

    def _boom(snap):
        raise RuntimeError("no DB in this test")
    monkeypatch.setattr("hvf_web.server._live_vwap_atr", _boom)

    seen_sigs = []
    monkeypatch.setattr("ig_shim.place_hvf_order_from_sig",
                         lambda sig, *a, **kw: seen_sigs.append(sig) or None)

    order_bridge.run_bridge()   # must not raise

    assert len(seen_sigs) == 1
    assert seen_sigs[0]["above_vwap"] is None
    assert seen_sigs[0]["atr_expanding"] is None
