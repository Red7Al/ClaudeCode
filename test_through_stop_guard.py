"""An order is never placed when live price is already through the pattern's own stop (2026-10-31 review).

The pattern-invalidation guard allowed price up to 1.2x the stop distance from entry on EITHER side, so a
setup whose stop had already been hit still went to IG. Fixture: 0016.HK on the owner's report 2026-10-31 --
SELL, entry 111.6, stop 117.2, scan price 117.6 (1.07x the stop distance).
"""

import pytest

import ig_shim


class _Session:
    def __init__(self, bid, offer):
        self.snap, self.posted = {"bid": bid, "offer": offer, "decimalPlacesFactor": 2}, []

    def get(self, path, version=None):
        return {"snapshot": self.snap, "instrument": {"currencies": [{"code": "HKD"}]}}

    def post(self, path, body=None, version=None):
        self.posted.append(body)
        return {"dealReference": "REF1"}


class _Db:
    def run(self, *a, **k):
        return []

    def close(self):
        pass


def _place(monkeypatch, direction, entry, stop, target, bid, offer):
    sess = _Session(bid, offer)
    monkeypatch.setattr(ig_shim, "session", sess)
    monkeypatch.setattr(ig_shim, "get_db", lambda: _Db())
    monkeypatch.setattr(ig_shim, "_get_pending_working_order", lambda tk, uid: None)
    monkeypatch.setattr(ig_shim, "get_epic", lambda tk: "EP.D.TEST.DAILY.IP")
    monkeypatch.setattr(ig_shim, "_log_working_order_to_db", lambda *a, **k: None)
    monkeypatch.setattr("notify.alert_missed_trade", lambda *a, **k: None)
    ig_shim.place_working_order(user_id="u1", ticker="0016.HK", direction=direction, size=1.0,
                                entry_level=entry, stop_level=stop, limit_level=target,
                                session_name="WEB_BRIDGE", signal_summary="t", proximity_pct=1.5, watch=False)
    return sess.posted


def test_a_sell_whose_price_is_through_its_stop_is_not_placed(monkeypatch):
    assert _place(monkeypatch, "SELL", 111.6, 117.2, 90.0, bid=117.6, offer=117.7) == []


def test_a_buy_whose_price_is_through_its_stop_is_not_placed(monkeypatch):
    # entry 100, stop 95: price 94.5 is 1.1x the stop distance below entry -- inside the old 1.2x band
    assert _place(monkeypatch, "BUY", 100.0, 95.0, 130.0, bid=94.4, offer=94.5) == []


@pytest.mark.parametrize("direction,entry,stop,target,bid,offer", [
    ("SELL", 111.6, 117.2, 90.0, 115.0, 115.1),   # above entry, short of the stop
    ("BUY", 100.0, 95.0, 130.0, 96.0, 96.1),      # below entry, short of the stop
])
def test_a_setup_short_of_its_stop_still_reaches_ig(monkeypatch, direction, entry, stop, target, bid, offer):
    assert len(_place(monkeypatch, direction, entry, stop, target, bid, offer)) == 1
