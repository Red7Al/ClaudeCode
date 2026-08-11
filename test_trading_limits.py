"""Tests for trading_limits.py and its wiring into ig_shim.place_hvf_order_from_sig.

Context (user 2026-08-11): personal "My trading limits" (R:R, Quality, VWAP, ATR-expanding, RVOL,
VolumeScore, instrument value) used to gate only a user's own manual web actions
(/api/preorder-pin, /api/place-order) — never the automated engine (order_bridge.py's 2h sweep, and the
session-based cron jobs via run_session.py/intraday_signals.py), which all funnel through
ig_shim.place_hvf_order_from_sig. Confirmed by reading every caller before this fix. These tests check
the shared floor-check logic itself, and that the automated order-placement chokepoint now stops BEFORE
ever resolving an IG epic when a personal floor is violated (i.e. it can't quietly place a real order that
should have been blocked).
"""

import ig_shim
import trading_limits


# ── trading_limits.check_limits — pure logic ────────────────────────────────────────────────────────────

def test_check_limits_passes_when_nothing_set(monkeypatch):
    monkeypatch.setattr(trading_limits, "user_limits", lambda name: trading_limits.limit_defaults())
    assert trading_limits.check_limits("Alex", "AAPL", quality=90, rr=5.0) == ""


def test_check_limits_blocks_below_rr_floor(monkeypatch):
    monkeypatch.setattr(trading_limits, "user_limits",
                         lambda name: {**trading_limits.limit_defaults(), "min_risk_reward": 4.0})
    reason = trading_limits.check_limits("Alex", "AAPL", quality=90, rr=2.5)
    assert "R:R" in reason and "AAPL" in reason


def test_check_limits_blocks_below_quality_floor(monkeypatch):
    monkeypatch.setattr(trading_limits, "user_limits",
                         lambda name: {**trading_limits.limit_defaults(), "min_quality": 60})
    reason = trading_limits.check_limits("Alex", "AAPL", quality=40, rr=5.0)
    assert "Quality" in reason


def test_check_limits_blocks_when_vwap_required_and_below(monkeypatch):
    monkeypatch.setattr(trading_limits, "user_limits",
                         lambda name: {**trading_limits.limit_defaults(), "require_above_vwap": 1})
    reason = trading_limits.check_limits("Alex", "AAPL", quality=90, rr=5.0, above_vwap=False)
    assert "VWAP" in reason


def test_check_limits_does_not_block_when_vwap_unknown(monkeypatch):
    """No data (None) must fail OPEN, same as hvf_web/server.py's original _limit_block — a caller whose
    sig dict doesn't carry above_vwap (e.g. an older code path) must not have every order blocked."""
    monkeypatch.setattr(trading_limits, "user_limits",
                         lambda name: {**trading_limits.limit_defaults(), "require_above_vwap": 1})
    reason = trading_limits.check_limits("Alex", "AAPL", quality=90, rr=5.0, above_vwap=None)
    assert reason == ""


def test_check_limits_blocks_when_atr_required_and_not_expanding(monkeypatch):
    monkeypatch.setattr(trading_limits, "user_limits",
                         lambda name: {**trading_limits.limit_defaults(), "require_atr_expanding": 1})
    reason = trading_limits.check_limits("Alex", "AAPL", quality=90, rr=5.0, atr_expanding=False)
    assert "ATR" in reason


def test_check_limits_blocks_below_instrument_value_minimum(monkeypatch):
    monkeypatch.setattr(trading_limits, "user_limits",
                         lambda name: {**trading_limits.limit_defaults(), "min_instrument_value": 1_000_000_000})
    reason = trading_limits.check_limits("Alex", "AAPL", quality=90, rr=5.0, mcap=500_000_000)
    assert "instrument value" in reason


def test_user_limits_falls_back_to_defaults_for_unknown_user(monkeypatch):
    from hvf_web import web_users as _wu
    monkeypatch.setattr(_wu, "get_settings", lambda name: {})
    lim = trading_limits.user_limits("NobodyConfigured")
    assert lim == trading_limits.limit_defaults()


def test_user_limits_applies_saved_override(monkeypatch):
    from hvf_web import web_users as _wu
    monkeypatch.setattr(_wu, "get_settings", lambda name: {"limits": {"min_risk_reward": 7.5}})
    lim = trading_limits.user_limits("Alex")
    assert lim["min_risk_reward"] == 7.5
    assert lim["min_quality"] == trading_limits.limit_defaults()["min_quality"]   # untouched keys keep default


def test_user_limits_empty_name_returns_defaults():
    assert trading_limits.user_limits("") == trading_limits.limit_defaults()
    assert trading_limits.user_limits(None) == trading_limits.limit_defaults()


# ── ig_shim.place_hvf_order_from_sig — the automated engine's chokepoint ───────────────────────────────

def _base_sig(**over):
    sig = {"ticker": "AAPL", "direction": "BUY", "hvf_type": "BULLISH", "hvf_signal": "TRIGGERED",
           "hvf_h3_level": 100.0, "hvf_stop_level": 95.0, "hvf_target": 120.0,
           "hvf_quality": 90, "hvf_risk_reward": 5.0, "hvf_timeframe": "daily-240",
           "index": "S&P 500", "location": "US"}
    sig.update(over)
    return sig


def _pass_the_earlier_gates(monkeypatch):
    """Neutralise the gates that run BEFORE the personal-limits check, so a test can isolate it."""
    import config_store
    monkeypatch.setattr(config_store, "monitor_enabled", lambda session_name: True)
    monkeypatch.setattr(config_store, "trade_allowed", lambda **kw: (True, ""))
    monkeypatch.setattr(config_store, "cfg_num", lambda key, default: default)


def test_place_order_blocked_by_personal_limits_never_resolves_an_epic(monkeypatch):
    """The whole point of this fix: a setup failing the owner's personal floor must be rejected BEFORE
    the function ever calls get_epic / touches the live IG API — not just logged and placed anyway."""
    _pass_the_earlier_gates(monkeypatch)
    monkeypatch.setattr(trading_limits, "check_limits", lambda *a, **kw: "AAPL: R:R 5.0 is below the personal floor of 6.0")
    epic_calls = []
    monkeypatch.setattr(ig_shim, "get_epic", lambda tk: epic_calls.append(tk) or "CS.D.AAPL.CFD.IP")

    result = ig_shim.place_hvf_order_from_sig(_base_sig(), {"name": "Alex", "user_id": "u1"}, "WEB_BRIDGE", 1.0)

    assert result is None
    assert epic_calls == []   # never reached the IG-facing part of the function


def test_place_order_not_blocked_when_limits_pass(monkeypatch):
    """Sanity check the inverse: when check_limits reports no violation, the function proceeds past the
    personal-limits gate (on to get_epic) rather than being blocked by this new check."""
    _pass_the_earlier_gates(monkeypatch)
    monkeypatch.setattr(trading_limits, "check_limits", lambda *a, **kw: "")
    epic_calls = []

    def _fake_get_epic(tk):
        epic_calls.append(tk)
        return None   # stop the test here — "no epic" is an existing, unrelated early-return

    monkeypatch.setattr(ig_shim, "get_epic", _fake_get_epic)

    ig_shim.place_hvf_order_from_sig(_base_sig(), {"name": "Alex", "user_id": "u1"}, "WEB_BRIDGE", 1.0)

    assert epic_calls == ["AAPL"]   # reached the IG-facing part — personal-limits gate let it through


def test_place_order_passes_profile_name_to_check_limits(monkeypatch):
    """profile["name"] must be the identity used to look up personal limits — not a hardcoded owner, so a
    non-owner's own manual place-order (which passes their own profile) is checked against THEIR limits."""
    _pass_the_earlier_gates(monkeypatch)
    seen = {}

    def _fake_check_limits(name, ticker, **kw):
        seen["name"] = name
        return ""

    monkeypatch.setattr(trading_limits, "check_limits", _fake_check_limits)
    monkeypatch.setattr(ig_shim, "get_epic", lambda tk: None)

    ig_shim.place_hvf_order_from_sig(_base_sig(), {"name": "Rich", "user_id": "u2"}, "WEB_MANUAL", 1.0)

    assert seen["name"] == "Rich"


def test_place_order_translates_signals_py_field_names_for_vwap_and_atr(monkeypatch):
    """signals.scan_instrument (the session monitors' sig source) uses vwap_position ("ABOVE"/"BELOW") and
    pa_atr_expanding, NOT build_snapshot.py's above_vwap/atr_expanding booleans — the session monitors
    (AUS/UK/US Open + *_MONITOR) must not silently skip these checks just because of the naming difference."""
    _pass_the_earlier_gates(monkeypatch)
    seen = {}

    def _fake_check_limits(name, ticker, **kw):
        seen.update(kw)
        return ""

    monkeypatch.setattr(trading_limits, "check_limits", _fake_check_limits)
    monkeypatch.setattr(ig_shim, "get_epic", lambda tk: None)

    sig = _base_sig(vwap_position="BELOW", pa_atr_expanding=True)
    sig.pop("above_vwap", None)   # confirm it's genuinely absent, not just unset
    ig_shim.place_hvf_order_from_sig(sig, {"name": "Alex", "user_id": "u1"}, "WEB_BRIDGE", 1.0)

    assert seen["above_vwap"] is False
    assert seen["atr_expanding"] is True
