"""Independent QA of the Order Bridge's report-trigger path (1c09b03, 85bd7f7, b076d75, 46f6d87; b14e936 moved
the age rule into order_bridge._candidates and judges the through-stop guard on the bid for a BUY, the offer
for a SELL).

Written by a tester who did not write the code, to close gaps where a deliberately broken version still passed
the author's tests (2026-10-31 mutation run): the age rule's weekend and UTC boundaries, equality and unit
conversion at the through-stop guard, the SELL side, a ticker with two report setups, BRIDGE_MAX_PER_RUN, the
duplicate guard's same-day and _already_working cases, main()'s exit codes, and "Create an order is not
age-limited". MONEY PATH: nothing here reaches IG or the database -- every placer and reader is replaced.

Fixtures: MET's live report row of 2026-10-30 (test_bridge_report_triggers.py). Where several instruments are
needed they are MET's row under other tickers -- the levels are real, only the names differ.
"""

import datetime
import types

import pytest

import ig_shim
import trading_limits
from hvf_web import order_bridge, server
from test_bridge_report_triggers import (LIMITS, MET_MCAP, MET_SCAN, MET_SETUP, _REAL_ORDERED_TRIGGERS,
                                         _wire)

D = datetime.date


def _today(monkeypatch, iso):
    monkeypatch.setattr(server, "_utc_today", lambda: D.fromisoformat(iso))


def _sigs(monkeypatch, setups, records=None, today="2026-10-26"):
    """report_order_sigs on MET's row: the report's calculation, with no age rule (b14e936)."""
    _wire(monkeypatch, records or [MET_SCAN], setups)
    _today(monkeypatch, today)
    return server.report_order_sigs("Alex", {"records": records or [MET_SCAN]})


def _bridged(monkeypatch, setups, records=None, today="2026-10-26"):
    """{ticker: sig} the bridge sent to the order engine this pass -- the age rule lives in _candidates."""
    placed = _wire(monkeypatch, records or [MET_SCAN], setups)
    _today(monkeypatch, today)
    order_bridge.run_bridge()
    return {s["ticker"]: s for s, kw in placed}


# -- the 0-1 trading-day rule (owner 2026-10-31), through run_bridge / _candidates ---------------------------

@pytest.mark.parametrize("today,ordered", [
    ("2026-10-23", True),    # Friday, the trigger day
    ("2026-10-24", True),    # Saturday: no trading day has passed
    ("2026-10-25", True),    # Sunday
    ("2026-10-26", True),    # Monday: 1 trading day
    ("2026-10-27", False),   # Tuesday: 2 trading days
    ("2026-10-30", False),
])
def test_a_friday_trigger_is_orderable_until_monday_only(monkeypatch, today, ordered):
    assert ("MET" in _bridged(monkeypatch, {"MET": [MET_SETUP]}, today=today)) is ordered


@pytest.mark.parametrize("today,ordered", [("2026-10-22", True), ("2026-10-23", True), ("2026-10-26", False)])
def test_a_thursday_trigger_is_not_orderable_on_monday(monkeypatch, today, ordered):
    thu = dict(MET_SETUP, trig_date="2026-10-22")
    assert ("MET" in _bridged(monkeypatch, {"MET": [thu]}, today=today)) is ordered


def test_the_report_calculation_keeps_every_open_trigger_whatever_its_age(monkeypatch):
    """b14e936: the age rule is the bridge's ordering policy, not part of the report's one calculation."""
    assert "MET" in _sigs(monkeypatch, {"MET": [MET_SETUP]}, today="2026-10-30")


def test_an_aged_triggers_ticker_never_falls_through_to_the_ready_path(monkeypatch, caplog):
    """The scan now calls MET READY, near entry and over the quality floor -- the READY path would order it
    from the scan's levels. Its report trigger is 5 trading days old: nothing may be ordered on MET."""
    ready = dict(MET_SCAN, status="READY", current_price=98.6)
    caplog.set_level("WARNING", logger="order_bridge")
    assert _bridged(monkeypatch, {"MET": [MET_SETUP]}, records=[ready], today="2026-10-30") == {}
    lines = [r.getMessage() for r in caplog.records if r.levelname == "WARNING" and "MET 2026-10-23" in r.getMessage()]
    assert len(lines) == 1 and "(5d)" in lines[0], "aged triggers are listed in one WARNING line"


def test_aged_triggers_are_listed_in_one_warning_line(monkeypatch, caplog):
    recs = [MET_SCAN, dict(MET_SCAN, ticker="MET2")]
    _wire(monkeypatch, recs, {"MET": [MET_SETUP], "MET2": [MET_SETUP]})
    monkeypatch.setattr(server, "_mcap_map", lambda: {"MET": MET_MCAP, "MET2": MET_MCAP})
    _today(monkeypatch, "2026-10-28")
    caplog.set_level("WARNING", logger="order_bridge")
    order_bridge.run_bridge()
    lines = [r.getMessage() for r in caplog.records if r.levelname == "WARNING" and "older than" in r.getMessage()]
    assert len(lines) == 1 and "MET 2026-10-23 (3d)" in lines[0] and "MET2 2026-10-23 (3d)" in lines[0]


@pytest.mark.parametrize("bad", [None, "", "23/10/2026", "2026-10-23T00:00:00"])
def test_an_unreadable_trigger_date_skips_only_that_trigger(monkeypatch, bad):
    recs = [MET_SCAN, dict(MET_SCAN, ticker="MET2", status="READY", current_price=98.6)]
    placed = _wire(monkeypatch, recs, {"MET": [MET_SETUP], "MET2": [dict(MET_SETUP, trig_date=bad)]})
    monkeypatch.setattr(server, "_mcap_map", lambda: {"MET": MET_MCAP, "MET2": MET_MCAP})
    _today(monkeypatch, "2026-10-26")
    assert order_bridge.main() == 0, "one bad row must not turn the whole pass red"
    assert [s["ticker"] for s, kw in placed] == ["MET"], "MET2 is not ordered -- not even by the READY path"


def test_a_weekend_trigger_date_counts_from_the_next_weekday():
    """A Saturday trig_date (a market whose bar is stamped Saturday) is 1 old on Monday, 2 on Tuesday."""
    for today, n in (("2026-10-24", 0), ("2026-10-25", 0), ("2026-10-26", 1), ("2026-10-27", 2)):
        server_today = D.fromisoformat(today)
        orig = server._utc_today
        server._utc_today = lambda t=server_today: t
        try:
            assert server._trading_days_since("2026-10-24") == n, today
        finally:
            server._utc_today = orig


def test_a_trigger_date_in_the_future_is_age_zero_not_negative(monkeypatch):
    _today(monkeypatch, "2026-10-26")
    assert server._trading_days_since("2026-10-28") == 0


def test_today_is_the_utc_date_not_the_machines(monkeypatch):
    """00:30 UTC Monday is still Sunday evening in New York: the rule counts the UTC date."""
    class _FakeDT(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            utc = datetime.datetime(2026, 10, 26, 0, 30, tzinfo=datetime.timezone.utc)
            return utc.astimezone(tz) if tz else datetime.datetime(2026, 10, 25, 20, 30)

    monkeypatch.setattr(server, "_dt", types.SimpleNamespace(datetime=_FakeDT, timezone=datetime.timezone,
                                                             date=datetime.date, timedelta=datetime.timedelta))
    assert server._utc_today() == D(2026, 10, 26)


def test_create_an_order_is_not_age_limited(monkeypatch):
    """The age rule is the bridge's; the owner can still place a 5-trading-day-old report trigger by hand."""
    _wire(monkeypatch, [MET_SCAN], {"MET": [MET_SETUP]})
    _today(monkeypatch, "2026-10-30")
    monkeypatch.setattr(server, "_record", lambda tk: MET_SCAN)
    monkeypatch.setattr(server, "_load_snapshot", lambda: {"records": [MET_SCAN]})
    sig, from_report = server._order_sig("MET", "Alex")
    assert from_report is True and sig["hvf_h3_level"] == MET_SETUP["entry"]
    assert _bridged(monkeypatch, {"MET": [MET_SETUP]}, today="2026-10-30") == {}, "but the bridge does not order it"


# -- SELL and a ticker with two report setups ---------------------------------------------------------------

def test_a_bear_report_trigger_goes_to_the_engine_as_a_sell(monkeypatch):
    bear = dict(MET_SETUP, direction="BEAR", entry=97.464684, stop=98.449997, target=90.3)
    placed = _wire(monkeypatch, [dict(MET_SCAN, direction="BEAR")], {"MET": [bear]})
    order_bridge.run_bridge()
    (sig, kw), = placed
    assert (sig["direction"], sig["hvf_type"], sig["hvf_h3_level"], sig["hvf_stop_level"]) == \
        ("SELL", "BEARISH", 97.464684, 98.449997)
    assert kw.get("watch") is False


def test_of_two_open_setups_the_newest_passing_one_is_ordered_with_its_own_date(monkeypatch):
    newer = dict(MET_SETUP, trig_date="2026-10-26", entry=99.1, stop=98.0, target=107.0)
    sigs = _sigs(monkeypatch, {"MET": [newer, MET_SETUP]}, today="2026-10-26")
    assert (sigs["MET"]["trig_date"], sigs["MET"]["hvf_h3_level"]) == ("2026-10-26", 99.1)


def test_when_the_newest_setup_fails_the_limits_the_older_passing_one_is_judged_on_its_own_age(monkeypatch):
    newer_fails = dict(MET_SETUP, trig_date="2026-10-27", volume_score=3.0)
    assert _bridged(monkeypatch, {"MET": [newer_fails, MET_SETUP]}, today="2026-10-27") == {}, \
        "the report shows the 23rd's setup; it is 2 trading days old on the 27th"
    sigs = _bridged(monkeypatch, {"MET": [newer_fails, MET_SETUP]}, today="2026-10-26")
    assert sigs["MET"]["trig_date"] == "2026-10-23"


# -- BRIDGE_MAX_PER_RUN and ordering ------------------------------------------------------------------------

def _many(monkeypatch, n, prices):
    tks = [f"MET{i}" for i in range(n)]
    recs = [dict(MET_SCAN, ticker=t, current_price=p) for t, p in zip(tks, prices)]
    placed = _wire(monkeypatch, recs, {t: [MET_SETUP] for t in tks})
    monkeypatch.setattr(server, "_mcap_map", lambda: {t: MET_MCAP for t in tks})
    calls = []
    monkeypatch.setattr("ig_shim.place_hvf_order_from_sig",
                        lambda sig, profile, source, mult, **kw: calls.append(sig["ticker"]) or {"status": "OK"})
    return calls


def test_the_per_run_cap_applies_to_report_triggers_nearest_entry_first(monkeypatch):
    # entry 98.45; prices step further away with i, given in reverse so the sort must reorder them
    prices = [98.45 * (1 + 0.002 * i) for i in range(8)][::-1]
    calls = _many(monkeypatch, 8, prices)
    summary = order_bridge.run_bridge()
    assert summary["placed"] == order_bridge.BRIDGE_MAX_PER_RUN == 6
    assert calls == [f"MET{i}" for i in (7, 6, 5, 4, 3, 2)], "nearest entry first, the farthest two wait"


def test_a_report_trigger_is_sorted_by_its_report_entry_not_the_scans():
    """The scan's entry can be a different (newer) funnel; the order goes in at the report's entry."""
    near_by_report = {"ticker": "A", "entry": 50.0, "current_price": 100.0, "_sig": {"hvf_h3_level": 100.1}}
    near_by_scan = {"ticker": "B", "entry": 100.5, "current_price": 100.0}
    assert [r["ticker"] for r in sorted([near_by_scan, near_by_report], key=order_bridge._candidate_order)] == \
        ["A", "B"]


def test_report_triggers_take_the_per_run_cap_before_a_nearer_ready_setup(monkeypatch):
    """Six report triggers 1.0% from entry and one READY setup 0.1% away: under a cap of 6 the triggers go --
    each has at most one more trading day -- and the READY setup waits for the next pass."""
    tks = [f"MET{i}" for i in range(6)]
    recs = [dict(MET_SCAN, ticker=t, current_price=98.45 * 1.01) for t in tks]
    recs.append(dict(MET_SCAN, ticker="RDY", status="READY", current_price=98.45 * 1.001))
    _wire(monkeypatch, recs, {t: [MET_SETUP] for t in tks})
    monkeypatch.setattr(server, "_mcap_map", lambda: {t: MET_MCAP for t in tks})
    calls = []
    monkeypatch.setattr("ig_shim.place_hvf_order_from_sig",
                        lambda sig, profile, source, mult, **kw: calls.append(sig["ticker"]) or {"status": "OK"})
    order_bridge.run_bridge()
    assert sorted(calls) == tks and "RDY" not in calls


def test_a_report_trigger_with_no_price_still_sorts_before_a_ready_setup():
    trig_no_px = {"ticker": "T", "entry": 10.0, "current_price": None, "_sig": {"hvf_h3_level": 10.0}}
    trig_far = {"ticker": "F", "entry": 10.0, "current_price": 12.0, "_sig": {"hvf_h3_level": 10.0}}
    ready_near = {"ticker": "R", "entry": 10.0, "current_price": 10.01}
    ready_no_px = {"ticker": "N", "entry": 10.0, "current_price": None}
    order = sorted([ready_no_px, ready_near, trig_no_px, trig_far], key=order_bridge._candidate_order)
    assert [r["ticker"] for r in order] == ["F", "T", "R", "N"], "triggers first; no price last within each group"


# -- the duplicate guards -----------------------------------------------------------------------------------

class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def run(self, sql, **k):
        return self.rows

    def close(self):
        pass


def _ordered(monkeypatch, rows, trig="2026-10-23", direction="BUY"):
    monkeypatch.setattr("db_pool.get_db", lambda: _Rows(rows))
    monkeypatch.setattr(order_bridge.account_scope, "row_identities", lambda owner=None: ["u1"])
    return _REAL_ORDERED_TRIGGERS([{"ticker": "MET", "direction": direction, "trig_date": trig}])


UTC = datetime.timezone.utc


def test_an_order_placed_on_the_trigger_date_itself_counts(monkeypatch):
    assert _ordered(monkeypatch, [["MET", "BUY", datetime.datetime(2026, 10, 23, 21, 5, tzinfo=UTC), None]]) == {"MET"}


def test_a_preorder_placed_and_filled_before_the_trigger_does_not_count(monkeypatch):
    rows = [["MET", "BUY", datetime.datetime(2026, 10, 20, 9, tzinfo=UTC), datetime.datetime(2026, 10, 22, 15, tzinfo=UTC)]]
    assert _ordered(monkeypatch, rows) == set()


def test_another_tickers_order_does_not_count(monkeypatch):
    assert _ordered(monkeypatch, [["MET.L", "BUY", datetime.datetime(2026, 10, 24, tzinfo=UTC), None]]) == set()


def test_a_sell_trigger_is_matched_on_sell_rows(monkeypatch):
    rows = [["MET", "SELL", datetime.datetime(2026, 10, 24, tzinfo=UTC), None]]
    assert _ordered(monkeypatch, rows, direction="SELL") == {"MET"}
    assert _ordered(monkeypatch, rows, direction="BUY") == set()


def test_a_report_trigger_on_an_instrument_already_working_is_skipped(monkeypatch):
    placed = _wire(monkeypatch, [MET_SCAN], {"MET": [MET_SETUP]})
    monkeypatch.setattr(order_bridge, "_already_working", lambda: {"MET"})
    order_bridge.run_bridge()
    assert placed == []


def test_a_ready_setup_keeps_the_old_watch_default(monkeypatch):
    """Only a report trigger is sent with watch=False; a READY pre-order keeps the engine's default."""
    ready = dict(MET_SCAN, status="READY", current_price=98.6)
    placed = _wire(monkeypatch, [ready], {})
    order_bridge.run_bridge()
    (sig, kw), = placed
    assert sig["hvf_signal"] == "READY" and kw.get("watch") is None


# -- main()'s exit code -------------------------------------------------------------------------------------

def test_main_exits_0_on_a_normal_pass(monkeypatch):
    _wire(monkeypatch, [MET_SCAN], {"MET": [MET_SETUP]})
    assert order_bridge.main() == 0


def test_main_exits_0_when_there_is_nothing_to_order(monkeypatch):
    _wire(monkeypatch, [MET_SCAN], {})
    assert order_bridge.main() == 0


def test_main_exits_1_when_the_snapshot_cannot_be_read(monkeypatch, tmp_path):
    import scanner_snapshot_store
    placed = _wire(monkeypatch, [MET_SCAN], {"MET": [MET_SETUP]})

    def _down(path):
        raise RuntimeError("store unavailable")
    monkeypatch.setattr(scanner_snapshot_store, "load_snapshot", _down)
    monkeypatch.setattr(order_bridge, "_SNAPSHOT", str(tmp_path / "absent.json"))
    assert order_bridge.main() == 1 and placed == []


def test_main_exits_1_when_the_snapshot_has_no_records(monkeypatch):
    placed = _wire(monkeypatch, [], {"MET": [MET_SETUP]})
    assert order_bridge.main() == 1 and placed == []


def test_the_local_snapshot_file_is_used_when_the_store_is_down(monkeypatch, tmp_path):
    import json
    import scanner_snapshot_store
    placed = _wire(monkeypatch, [MET_SCAN], {"MET": [MET_SETUP]})

    def _down(path):
        raise RuntimeError("store unavailable")
    f = tmp_path / "snapshot.json"
    f.write_text(json.dumps({"generated_utc": "2026-10-30T19:08:13", "records": [MET_SCAN]}), encoding="utf-8")
    monkeypatch.setattr(scanner_snapshot_store, "load_snapshot", _down)
    monkeypatch.setattr(order_bridge, "_SNAPSHOT", str(f))
    assert order_bridge.main() == 0 and [s["ticker"] for s, kw in placed] == ["MET"]


def test_main_exits_1_when_the_working_order_guard_cannot_be_read(monkeypatch):
    placed = _wire(monkeypatch, [MET_SCAN], {"MET": [MET_SETUP]})

    def _raise():
        raise order_bridge.IdentityUnresolved("no profile")
    monkeypatch.setattr(order_bridge, "_already_working", _raise)
    assert order_bridge.main() == 1 and placed == []


def test_main_exits_1_when_the_trigger_guard_cannot_be_read(monkeypatch):
    placed = _wire(monkeypatch, [MET_SCAN], {"MET": [MET_SETUP]})

    def _down():
        raise RuntimeError("database refused")
    monkeypatch.setattr("db_pool.get_db", _down)
    monkeypatch.setattr(order_bridge.account_scope, "row_identities", lambda owner=None: ["u1"])
    monkeypatch.setattr(order_bridge, "_ordered_triggers", _REAL_ORDERED_TRIGGERS)
    assert order_bridge.main() == 1 and placed == []


# -- the through-stop guard at equality and after unit conversion ----------------------------------------------

class _Session:
    def __init__(self, bid, offer, currencies):
        self.snap, self.cur, self.posted = {"bid": bid, "offer": offer, "decimalPlacesFactor": 2}, currencies, []

    def get(self, path, version=None):
        return {"snapshot": self.snap, "instrument": {"currencies": self.cur}}

    def post(self, path, body=None, version=None):
        self.posted.append(body)
        return {"dealReference": "REF1"}


def _place(monkeypatch, direction, entry, stop, target, bid, offer, currencies=({"code": "USD"},)):
    sess = _Session(bid, offer, list(currencies))
    monkeypatch.setattr(ig_shim, "session", sess)
    monkeypatch.setattr(ig_shim, "get_db", lambda: _Rows([]))
    monkeypatch.setattr(ig_shim, "_get_pending_working_order", lambda tk, uid: None)
    monkeypatch.setattr(ig_shim, "get_epic", lambda tk: "EP.D.TEST.DAILY.IP")
    monkeypatch.setattr(ig_shim, "_log_working_order_to_db", lambda *a, **k: None)
    monkeypatch.setattr("notify.alert_missed_trade", lambda *a, **k: None)
    ig_shim.place_working_order(user_id="u1", ticker="MET", direction=direction, size=1.0,
                                entry_level=entry, stop_level=stop, limit_level=target,
                                session_name="WEB_MANUAL", signal_summary="t", proximity_pct=1.5, watch=False)
    return sess.posted


# b14e936: a long's stop is hit on the BID, a short's on the OFFER.

def test_a_buy_with_the_bid_exactly_at_the_stop_is_not_placed(monkeypatch):
    assert _place(monkeypatch, "BUY", 98.45, 97.46, 105.61, bid=97.46, offer=97.50) == []


def test_a_buy_with_the_bid_through_the_stop_and_the_offer_above_it_is_not_placed(monkeypatch):
    assert _place(monkeypatch, "BUY", 98.45, 97.46, 105.61, bid=97.40, offer=97.50) == []


def test_a_sell_with_the_offer_exactly_at_the_stop_is_not_placed(monkeypatch):
    assert _place(monkeypatch, "SELL", 97.46, 98.45, 90.3, bid=98.40, offer=98.45) == []


def test_a_sell_with_the_offer_through_the_stop_and_the_bid_below_it_is_not_placed(monkeypatch):
    assert _place(monkeypatch, "SELL", 97.46, 98.45, 90.3, bid=98.40, offer=98.50) == []


def test_one_tick_short_of_the_stop_on_the_stop_side_is_placed(monkeypatch):
    assert len(_place(monkeypatch, "BUY", 98.45, 97.46, 105.61, bid=97.47, offer=97.52)) == 1
    assert len(_place(monkeypatch, "SELL", 97.46, 98.45, 90.3, bid=98.39, offer=98.44)) == 1


def test_the_1_2x_band_still_reads_the_offer_for_a_buy_and_the_bid_for_a_sell(monkeypatch):
    """Entry 100, stop 95/105: the band is 6 points either side of entry. Offer 106.1 is outside it for a BUY
    even though the bid 105.9 is inside; mirrored for a SELL."""
    assert _place(monkeypatch, "BUY", 100.0, 95.0, 130.0, bid=105.9, offer=106.1) == []
    assert len(_place(monkeypatch, "BUY", 100.0, 95.0, 130.0, bid=105.7, offer=105.9)) == 1
    assert _place(monkeypatch, "SELL", 100.0, 105.0, 70.0, bid=93.9, offer=94.1) == []
    assert len(_place(monkeypatch, "SELL", 100.0, 105.0, 70.0, bid=94.1, offer=94.3)) == 1


GBX = ({"code": "GBX", "baseExchangeRate": 78.5},)   # pence per USD; levels are converted x78.5 before the guard


def test_gbx_buy_through_its_converted_stop_is_not_placed(monkeypatch):
    # stop 95 USD = 7457.5p; offer 7450p is 1.02x the stop distance -- inside the 1.2x band, through the stop
    assert _place(monkeypatch, "BUY", 100.0, 95.0, 130.0, bid=7440, offer=7450, currencies=GBX) == []


def test_gbx_sell_short_of_its_converted_stop_is_placed(monkeypatch):
    # stop 105 USD = 8242.5p; bid 8200p is short of it. Compared with the unconverted 105 it would be refused.
    assert len(_place(monkeypatch, "SELL", 100.0, 105.0, 80.0, bid=8200, offer=8210, currencies=GBX)) == 1


def test_cents_scaled_buy_through_its_stop_is_not_placed(monkeypatch):
    # IG quotes in cents (x100 by detect_ig_scale): stop 95 USD = 9500; offer 9450 is through it
    assert _place(monkeypatch, "BUY", 100.0, 95.0, 130.0, bid=9440, offer=9450) == []
    assert len(_place(monkeypatch, "BUY", 100.0, 95.0, 130.0, bid=9590, offer=9600)) == 1
