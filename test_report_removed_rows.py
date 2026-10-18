"""Every row that leaves the Scanner Report is named in the next email with its reason.

Owner 2026-10-18: "Anything like items dropping out of any reports needs a full justification sent out - via
email." TSCO.L's 8 Oct trigger left his report on 15 Oct with no word. Setups are the table-sourced ARM fixtures
(test_report_trigger_setups.py); ARM_18_SEP passes LIMITS, ARM_29_SEP fails it on RVOL 1.08 < 1.7.
"""
import datetime as dt

import pytest

import run_scanner_report_email as rse
from hvf_web import server
import trading_limits
from test_report_trigger_setups import ARM_18_SEP, ARM_29_SEP, ARM_MCAP, LIMITS

TODAY = dt.date(2026, 10, 18)
SNAP = {"generated_utc": "t", "records": [{"ticker": "ARM", "market": "NASDAQ 100", "location": "US"}]}
PREV = [{"ticker": "ARM", "trig_date": "2026-09-18", "name": "Arm Holdings", "direction": "BULL"}]


@pytest.fixture(autouse=True)
def _no_live_sent_store(monkeypatch):
    """No test may read or write the live store of rows sent."""
    saved = []
    monkeypatch.setattr(rse, "_load_sent", lambda name: None)
    monkeypatch.setattr(rse, "_save_sent", lambda name, rows, gen: saved.append((name, len(rows))) or True)
    monkeypatch.setattr(rse, "_history_row", lambda t, d: (_ for _ in ()).throw(AssertionError("live DB")))
    return saved


@pytest.fixture
def wired(monkeypatch):
    def wire(setups=None, allows=True, history=None):
        monkeypatch.setattr(server, "_open_trigger_setups", lambda s: setups)
        monkeypatch.setattr(server, "_mcap_map", lambda: {"ARM": ARM_MCAP})
        monkeypatch.setattr(server, "_user_trade_allows", lambda n, r: allows)
        monkeypatch.setattr(trading_limits, "user_limits", lambda n: LIMITS)
        monkeypatch.setattr(rse, "_history_row", lambda t, d: history)
    return wire


def _why(prev=PREV, now=(), snap=SNAP):
    out = rse.removed_since_last("Alex", list(prev), list(now), snap, today=TODAY)
    return [r["reason"] for r in out]


def test_a_row_still_on_the_report_is_not_listed(wired):
    wired({"ARM": [ARM_18_SEP]})
    assert _why(now=[{"ticker": "ARM", "trig_date": "2026-09-18"}]) == []


def test_replaced_by_a_newer_trigger(wired):
    wired({"ARM": [ARM_18_SEP]})
    assert _why(now=[{"ticker": "ARM", "trig_date": "2026-10-09"}]) == [
        "Replaced by its trigger of 2026-10-09: the report shows one setup per instrument."]


def test_older_than_the_90_day_rule(wired):
    wired({"ARM": [ARM_18_SEP]})
    assert "more than 90 days ago" in _why(prev=[dict(PREV[0], trig_date="2026-07-10")])[0]


def test_not_in_todays_scan(wired):
    wired({"ARM": [ARM_18_SEP]})
    assert _why(snap={"generated_utc": "t", "records": []}) == ["Not in today's scan."]


def test_history_unreadable(wired):
    wired(None)
    assert "could not be read today" in _why()[0]


def test_open_but_not_rechecked_is_named_as_withheld(wired):
    wired({"ARM": []}, history=("OPEN", dt.datetime(2026, 10, 12, 4, 39)))
    assert "last re-checked 2026-10-12 04:39 UTC" in _why()[0]


def test_gone_from_history(wired):
    wired({"ARM": []}, history=None)
    assert _why() == ["No longer in the trigger history."]


def test_fails_a_filter_and_quotes_which(wired):
    wired({"ARM": [ARM_29_SEP]})
    why = _why(prev=[dict(PREV[0], trig_date="2026-09-29")])[0]
    assert why == "No longer passes your My Trading Filters: RVOL 1.08 is below the personal floor of 1.7."


def test_market_switched_off(wired):
    wired({"ARM": [ARM_18_SEP]}, allows=False)
    assert _why() == ["Excluded by your market switches or direction / location filters."]


def test_every_server_check_passes_is_said_not_hidden(wired):
    wired({"ARM": [ARM_18_SEP]})
    assert _why()[0].startswith("Reason not determined")


def test_a_failing_check_still_lists_the_row(wired, monkeypatch):
    wired({"ARM": [ARM_18_SEP]})
    monkeypatch.setattr(server, "_user_trade_allows", lambda n, r: (_ for _ in ()).throw(RuntimeError("db down")))
    assert _why() == ["Reason not determined: the check failed (RuntimeError: db down)."]


def test_the_email_carries_each_reason():
    removed = [{"ticker": "TSCO.L", "trig_date": "2026-10-08", "reason": "The trigger history now records it as TARGET."}]
    _s, text, html = rse.email_body("Alex", [], "2026-10-18T05:30:00Z", removed=removed,
                                    last_sent="2026-10-17T05:53:10+00:00")
    assert "Removed since your last report (sent 2026-10-17 05:53 UTC): 1" in text
    assert "TSCO.L (triggered 2026-10-08): The trigger history now records it as TARGET." in text
    assert "<b>TSCO.L</b> (triggered 2026-10-08): The trigger history now records it as TARGET." in html


def test_first_report_and_nothing_removed_are_both_stated():
    assert "none recorded yet" in rse.email_body("Alex", [], "g")[1]
    assert "Removed since your last report (sent 2026-10-17 05:53 UTC): nothing." in \
        rse.email_body("Alex", [], "g", removed=[], last_sent="2026-10-17T05:53:10+00:00")[1]


def test_main_compares_with_the_last_send_and_records_this_one(monkeypatch, _no_live_sent_store):
    from hvf_web import web_users as wu
    import web_store, trade_email
    monkeypatch.setattr(wu, "list_users", lambda: [{"name": "Alex", "email": "eahind@yahoo.co.uk", "enabled": True}])
    monkeypatch.setattr(wu, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(web_store, "append_batch", lambda *a, **k: None)
    monkeypatch.setattr(server, "_load_snapshot", lambda: SNAP)
    monkeypatch.setattr(rse, "screen_rows", lambda n: ([{"ticker": "ARM", "trig_date": "2026-10-09"}], False))
    monkeypatch.setattr(rse, "_load_sent", lambda n: {"sent_utc": "2026-10-17T05:53:10+00:00", "rows": PREV})
    bodies = []
    monkeypatch.setattr(trade_email, "send_simple_email", lambda s, t, html=None, recipients=None: bodies.append(t) or True)
    assert rse.main() == 0
    assert "ARM (triggered 2026-09-18): Replaced by its trigger of 2026-10-09" in bodies[0]
    assert _no_live_sent_store == [("Alex", 1)]
