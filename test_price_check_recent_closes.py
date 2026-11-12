"""P-05 (2026-11-11): the IG identity price check accepts a price within 8% of ANY of the last 5 closes. Fixtures are
the real rows: 360.AX (Life360) was refused by the one-close rule on 11-11; the two nearest wrong instruments from the
39 measured must still be refused."""
import ig_shim

LIFE360_CLOSES = [20.85, 22.87, 22.45, 22.33, 18.91]       # Yahoo 360.AX closes 2026-11-04..11-10
LIFE360_IG = {"bid": 2081.0, "offer": 2083.0}               # AB.D.360AU.DAILY.IP on 11-11, cents


def test_the_old_one_close_rule_refused_life360():
    assert ig_shim._price_matches(LIFE360_IG, LIFE360_CLOSES[-1]) is False


def test_life360_is_accepted_against_the_recent_closes():
    assert ig_shim._price_matches(LIFE360_IG, LIFE360_CLOSES) is True


def test_nearest_wrong_instruments_are_still_refused():
    # M&M.NS on PK.D.MHID (IG 3670 vs close 3290.6) and 3382.T on IX.D.MAGSEVEN (IG 1873.87 vs closes ending 2180.5)
    assert not ig_shim._price_matches({"bid": 3670.0, "offer": 3670.0}, [3290.6])
    assert not ig_shim._price_matches({"bid": 1873.87, "offer": 1873.87}, [2160.0, 2175.0, 2180.5])


def test_a_scalar_close_still_works_and_no_price_is_no_evidence():
    assert ig_shim._price_matches({"bid": 10.0, "offer": 10.0}, 10.3) is True
    assert ig_shim._price_matches({"bid": None, "offer": None}, [5.0]) is True


def test_last_close_returns_at_most_five_closes_oldest_first(monkeypatch):
    import price_bars
    bars = [(f"2026-11-0{i}", 1, 1, 1, float(i), 1) for i in range(1, 9)]
    monkeypatch.setattr(price_bars, "ohlcv", lambda db, t, s, e, fb: bars)
    class _Db:
        def close(self): pass
    monkeypatch.setattr(ig_shim, "get_db", lambda: _Db())
    assert ig_shim._last_close("X") == [4.0, 5.0, 6.0, 7.0, 8.0]


# ---- specialist additions -------------------------------------------------------------------------------------
import pytest


def _m(p):
    return {"bid": p, "offer": p}


def test_band_edges_are_8_percent_of_a_close():
    assert ig_shim._price_matches(_m(107.9), [100.0])
    assert ig_shim._price_matches(_m(92.1), [100.0])
    assert not ig_shim._price_matches(_m(108.5), [100.0])
    assert not ig_shim._price_matches(_m(91.5), [100.0])


def test_pence_and_cents_scales_apply_to_every_close_in_the_list():
    assert ig_shim._price_matches(_m(5100.0), [40.0, 51.0])      # x100
    assert ig_shim._price_matches(_m(0.51), [40.0, 51.0])        # x0.01
    assert not ig_shim._price_matches(_m(5100.0), [40.0, 30.0])
    assert not ig_shim._price_matches(_m(300.0), [51.0, 40.0])   # no scale of 3


def test_any_close_suffices_but_a_price_off_every_close_is_refused():
    assert ig_shim._price_matches(_m(100.0), [50.0, 70.0, 99.0])
    assert not ig_shim._price_matches(_m(100.0), [50.0, 70.0, 120.0])


def test_tuple_and_zero_or_none_entries_are_tolerated():
    assert ig_shim._price_matches(_m(100.0), (0, 99.0))
    assert not ig_shim._price_matches(_m(100.0), (0, 50.0))
    assert ig_shim._price_matches(_m(100.0), [])      # no closes = no evidence (guard skipped, as before)


def test_mid_of_bid_and_offer_is_used():
    assert ig_shim._price_matches({"bid": 90.0, "offer": 110.0}, [100.0])


def test_six_closes_back_is_not_considered(monkeypatch):
    import price_bars
    bars = [(f"2026-11-{i:02d}", 1, 1, 1, c, 1) for i, c in enumerate([100.0, 10, 10, 10, 10, 10], 1)]
    monkeypatch.setattr(price_bars, "ohlcv", lambda db, t, s, e, fb: bars)
    class _Db:
        def close(self): pass
    monkeypatch.setattr(ig_shim, "get_db", lambda: _Db())
    closes = ig_shim._last_close("X")
    assert closes == [10.0] * 5 and not ig_shim._price_matches(_m(100.0), closes)


def test_last_close_none_when_no_bars_or_error(monkeypatch):
    import price_bars
    monkeypatch.setattr(price_bars, "ohlcv", lambda *a: [])
    class _Db:
        def close(self): pass
    monkeypatch.setattr(ig_shim, "get_db", lambda: _Db())
    assert ig_shim._last_close("X") is None
    monkeypatch.setattr(price_bars, "ohlcv", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    assert ig_shim._last_close("X") is None
