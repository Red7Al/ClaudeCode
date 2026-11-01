"""Independent QA of cbe6777: the risk cap on orders forced up to IG's minimum deal size (owner 2026-11-01).

Fixtures are IG's own figures as measured 2026-11-01: minDealSize CVX 0.04 (stop distance ~515 pts -> £20.60 at the
minimum), COHR 0.01 (~5,730 pts -> £57.30), 0700.HK 0.01, SYY / XOM / WY / DVN 0.04; available £175.99, so 2% risk is
£3.52 and the cap 2 x £3.52 = £7.04. MONEY PATH: no IG call -- the session, balance and placer are replaced.
"""

import threading

import pytest

import ig_shim
import notify

AVAILABLE = 175.99
RISK = AVAILABLE * 0.02                      # £3.5198
CAP = ig_shim.RISK_CAP_MULT * RISK           # £7.0396


class _Session:
    def __init__(self, min_size, offer=15000.0, margin=20, min_stop=0.0, bid=None):
        self.mkt = {"snapshot": {"offer": offer, "bid": bid if bid is not None else offer - 2},
                    "instrument": {"marginFactor": margin, "currencies": [{"code": "USD"}]},
                    "dealingRules": {"minDealSize": {"value": min_size},
                                     "minNormalStopOrLimitDistance": {"value": min_stop}}}

    def get(self, path, version=None):
        return self.mkt


def _size(monkeypatch, min_size, stop, risk=RISK, available=AVAILABLE, **kw):
    monkeypatch.setattr(ig_shim, "session", _Session(min_size, **kw))
    ig_shim.LAST_SIZE_SKIP.clear()
    return ig_shim.calculate_position_size("EP.TEST", stop, risk, available_funds=available)


# -- calculate_position_size ------------------------------------------------------------------------------

@pytest.mark.parametrize("min_size,stop,risk_at_min", [(0.04, 515.0, 20.6), (0.01, 5730.0, 57.3)])
def test_igs_minimum_over_twice_the_risk_is_not_placed(monkeypatch, min_size, stop, risk_at_min):
    """CVX and COHR as measured: the account can margin IG's minimum (the old code placed it), but it would
    risk £20.60 / £57.30 against a £7.04 cap."""
    size, dist = _size(monkeypatch, min_size, stop)
    assert (size, dist) == (0.0, stop)
    assert ig_shim.LAST_SIZE_SKIP.get("EP.TEST") == ("RISK_CAP", risk_at_min, round(CAP, 2), min_size)


def test_igs_minimum_within_the_cap_is_still_placed(monkeypatch):
    # 0.04 x 150 = £6.00 <= £7.04: the old behaviour -- IG's minimum, a little over the 2%
    assert _size(monkeypatch, 0.04, 150.0) == (0.04, 150.0)
    assert ig_shim.LAST_SIZE_SKIP.get("EP.TEST") is None


@pytest.mark.parametrize("available,stop", [(176.0, 176.0), (175.99, 175.99), (100.0, 100.0), (250.0, 250.0)])
def test_exactly_at_the_cap_is_placed(monkeypatch, available, stop):
    """0.04 x stop == 2 x 2% x available: at the cap, not over it. (Offer 1,000 so the margin never decides.)"""
    assert _size(monkeypatch, 0.04, stop, risk=available * 0.02, available=available, offer=1000.0) == (0.04, stop)


def test_one_point_over_the_cap_is_not_placed(monkeypatch):
    assert _size(monkeypatch, 0.04, 177.0, risk=176.0 * 0.02, available=176.0)[0] == 0.0


def test_a_risk_amount_of_zero_keeps_the_old_behaviour(monkeypatch):
    assert _size(monkeypatch, 0.04, 515.0, risk=0.0) == (0.04, 515.0)
    assert ig_shim.LAST_SIZE_SKIP.get("EP.TEST") is None


def test_a_risk_based_size_at_or_above_igs_minimum_is_never_capped(monkeypatch):
    # a large account: risk £100, stop 515 -> 0.19, above IG's 0.04 minimum
    size, _ = _size(monkeypatch, 0.04, 515.0, risk=100.0, available=5000.0)
    assert size == 0.19 and ig_shim.LAST_SIZE_SKIP.get("EP.TEST") is None


def test_the_cap_is_judged_on_the_stop_widened_to_igs_minimum_distance(monkeypatch):
    """Stop 100 pts would risk £4.00 at 0.04, inside the cap; IG's minimum stop distance 600 widens it to 630,
    which risks £25.20 -- over the cap. The widened distance is what is returned."""
    size, dist = _size(monkeypatch, 0.04, 100.0, min_stop=600.0)
    assert (size, dist) == (0.0, 630.0)
    assert ig_shim.LAST_SIZE_SKIP.get("EP.TEST")[:2] == ("RISK_CAP", 25.2)


def test_the_cap_is_checked_before_the_margin(monkeypatch):
    """Over the cap AND unaffordable: the refusal is the cap, not "account too small"."""
    _size(monkeypatch, 0.04, 515.0, offer=150000.0)          # margin 0.04 x 150,000 x 20% = £1,200
    assert ig_shim.LAST_SIZE_SKIP.get("EP.TEST")[0] == "RISK_CAP"


def test_within_the_cap_but_unaffordable_is_still_account_too_small(monkeypatch):
    assert _size(monkeypatch, 0.04, 150.0, offer=150000.0)[0] == 0.0
    assert ig_shim.LAST_SIZE_SKIP.get("EP.TEST")[0] == "ACCOUNT_TOO_SMALL"


def test_the_refusal_is_private_to_the_thread_that_sized(monkeypatch):
    monkeypatch.setattr(ig_shim, "session", _Session(0.04))
    ig_shim.LAST_SIZE_SKIP.clear()
    seen = {}

    def other():
        ig_shim.calculate_position_size("EP.OTHER", 515.0, RISK, available_funds=AVAILABLE)
        seen["there"] = ig_shim.LAST_SIZE_SKIP.get("EP.OTHER")
    t = threading.Thread(target=other)
    t.start()
    t.join()
    assert seen["there"][0] == "RISK_CAP"
    assert ig_shim.LAST_SIZE_SKIP.get("EP.OTHER") is None, "another thread's refusal must be invisible here"


# -- the wording and its alert class ------------------------------------------------------------------------

@pytest.mark.parametrize("ticker,min_size,stop", [("CVX", 0.04, 515.0), ("COHR", 0.01, 5730.0), ("0700.HK", 0.01, 2000.0),
                                                   ("SYY", 0.04, 300.0), ("XOM", 0.04, 400.0), ("WY", 0.04, 250.0),
                                                   ("DVN", 0.04, 200.0)])
def test_the_reason_says_over_the_risk_cap_and_is_a_silent_class(monkeypatch, ticker, min_size, stop):
    _size(monkeypatch, min_size, stop)
    text = ig_shim.describe_size_skip(ticker, "EP.TEST")
    assert text.startswith(f"{ticker}: over the risk cap")
    assert f"size {min_size:g}" in text and f"£{min_size * stop:.2f}" in text and "£7.04" in text
    cls, _action = notify._classify_missed_trade(text)
    assert cls == "RISK_CAP" and cls in notify._SILENT_MISSED_TRADE_CLASSES


def test_the_other_size_classes_are_unchanged():
    assert notify._classify_missed_trade("XOM: calculated size is 0")[0] == "SIZE_ZERO"
    assert notify._classify_missed_trade("daily cap reached")[0] == "CAP"
    assert "SIZE_ZERO" not in notify._SILENT_MISSED_TRADE_CLASSES and "CAP" not in notify._SILENT_MISSED_TRADE_CLASSES


def test_a_risk_cap_alert_is_not_paged(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "_send", lambda *a, **k: sent.append(a) or True, raising=False)
    monkeypatch.setattr(notify, "send_alert", lambda *a, **k: sent.append(a) or True, raising=False)
    monkeypatch.setattr("db_pool.get_db", lambda: (_ for _ in ()).throw(RuntimeError("no db in tests")))
    _size(monkeypatch, 0.04, 515.0)
    notify.alert_missed_trade("CVX", "BUY", ig_shim.describe_size_skip("CVX", "EP.TEST"), "")
    assert sent == []


# -- the callers ------------------------------------------------------------------------------------------

def _wire_ig(monkeypatch, min_size=0.04, offer=15000.0):
    monkeypatch.setattr(ig_shim, "session", _Session(min_size, offer=offer))
    monkeypatch.setattr(ig_shim, "get_epic", lambda tk: "EP.TEST")
    monkeypatch.setattr(ig_shim, "get_snapshot", lambda epic: {"bid": offer - 2, "offer": offer})
    monkeypatch.setattr(ig_shim, "get_account_balance", lambda: {"available": AVAILABLE, "balance": AVAILABLE})


def test_size_working_order_returns_zero_with_the_cap_reason(monkeypatch):
    """CVX in USD (150.00 / 144.85) against IG's cents (15,000): 515 points of stop at IG's 0.04 minimum."""
    _wire_ig(monkeypatch)
    out = ig_shim.size_working_order("CVX", "BUY", 150.0, 144.85, 170.0, {"risk_per_trade": 0.02})
    assert out["size"] == 0.0 and out["min_size"] == 0.04
    assert out["reason"].startswith("CVX: over the risk cap") and "£20.60" in out["reason"]


def _bridge_place(monkeypatch, size_override=None):
    import config_store
    import order_metrics
    import trading_limits
    _wire_ig(monkeypatch)
    monkeypatch.setattr(config_store, "monitor_enabled", lambda s: True)
    monkeypatch.setattr(config_store, "trade_allowed", lambda **k: (True, ""))
    monkeypatch.setattr(config_store, "cfg_num", lambda k, d=None: 30)
    monkeypatch.setattr(order_metrics, "live_order_metrics", lambda *a, **k: {})
    monkeypatch.setattr(trading_limits, "login_for_profile", lambda p: "Alex")
    monkeypatch.setattr(trading_limits, "check_limits", lambda *a, **k: None)
    monkeypatch.setattr(trading_limits, "user_limits", lambda n: {"min_rvol": 1.7})
    placed, alerts = [], []
    monkeypatch.setattr(ig_shim, "place_working_order", lambda **k: placed.append(k) or {"deal_id": "D1"})
    monkeypatch.setattr(notify, "alert_missed_trade", lambda tk, d, reason, *a, **k: alerts.append(reason))
    for name in ("working_order_placed", "send_investment_case_email"):
        monkeypatch.setattr(notify, name, lambda *a, **k: None, raising=False)
    sig = {"ticker": "CVX", "direction": "BUY", "hvf_type": "BULLISH", "hvf_signal": "TRIGGERED",
           "hvf_h3_level": 150.0, "hvf_stop_level": 144.85, "hvf_target": 170.0, "hvf_quality": 60,
           "hvf_risk_reward": 3.9, "index": "S&P 500", "location": "US", "above_vwap": True, "atr_expanding": True,
           "rvol": 2.0, "volume_score": 8, "mcap": 3e11}
    ig_shim.LAST_ORDER_SKIP.clear()
    out = ig_shim.place_hvf_order_from_sig(sig, {"user_id": "u1", "name": "Owner", "risk_per_trade": 0.02},
                                           "WEB_MANUAL" if size_override is not None else "WEB_BRIDGE", 1.0,
                                           size_override=size_override, watch=False)
    return out, placed, alerts


def test_the_bridge_places_nothing_and_records_the_cap_reason(monkeypatch):
    out, placed, alerts = _bridge_place(monkeypatch)
    assert out is None and placed == []
    assert alerts and alerts[0].startswith("CVX: over the risk cap")
    assert ig_shim.order_skip_reason("CVX").startswith("CVX: over the risk cap")


def test_create_an_order_with_the_owners_size_still_places_at_that_size(monkeypatch):
    out, placed, alerts = _bridge_place(monkeypatch, size_override=0.04)
    assert out and out.get("deal_id") == "D1" and len(placed) == 1
    assert placed[0]["size"] == 0.04 and placed[0]["entry_level"] == 15000.0
    assert alerts == []


def test_create_an_order_below_igs_minimum_is_still_refused(monkeypatch):
    out, placed, _alerts = _bridge_place(monkeypatch, size_override=0.01)
    assert out is None and placed == []
    assert "below IG's minimum" in ig_shim.order_skip_reason("CVX")


def test_the_session_paths_report_the_cap_reason(monkeypatch):
    """run_session.py:322 and :680 report a size<=0 through _size_skip_reason -> describe_size_skip."""
    import run_session
    _size(monkeypatch, 0.04, 515.0)
    reason = run_session._size_skip_reason("EP.TEST", "CVX")
    assert reason.startswith("CVX: over the risk cap")
    assert notify._classify_missed_trade(reason)[0] == "RISK_CAP"
