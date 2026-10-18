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
    # squeeze_history.refreshed_at is timestamptz (id 80179 reads back tz-aware UTC)
    wired({"ARM": []}, history=("OPEN", dt.datetime(2026, 10, 12, 4, 39, tzinfo=dt.timezone.utc)))
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
    assert _why() == ["Excluded by your market switches or direction / location filters (BULL, NASDAQ 100)."]


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


# ── The REAL store functions (code review 2026-10-18: every earlier test replaced them with fakes) ─────────────
_REAL_LOAD, _REAL_SAVE = rse._load_sent, rse._save_sent


class _Store:
    """web_json_store in memory: (available, payload, revision) reads and revision-checked writes."""

    def __init__(self, payload=None):
        self.payload, self.rev, self.up, self.writes = payload, (1 if payload else 0), True, 0

    def read(self, key):
        return (True, self.payload, self.rev) if self.up else (False, None, None)

    def save(self, key, payload, expected_revision=None):
        if not self.up or expected_revision != self.rev:
            return None
        self.payload, self.rev, self.writes = payload, self.rev + 1, self.writes + 1
        return self.rev


@pytest.fixture
def store(monkeypatch):
    import web_store
    st = _Store({"Sam": {"sent_utc": "2026-10-17T05:53:10+00:00", "rows": PREV}})
    monkeypatch.setattr(web_store, "read_json_store_versioned", st.read)
    monkeypatch.setattr(web_store, "save_json_store_versioned", st.save)
    return st


def test_an_unreadable_store_is_never_overwritten_with_one_login(store):
    store.up = False
    with pytest.raises(RuntimeError):
        _REAL_SAVE("Alex", [{"ticker": "ARM", "trig_date": "2026-10-09"}], "g")
    store.up = True
    assert store.writes == 0 and list(store.payload) == ["Sam"]


def test_saving_one_login_keeps_every_other(store):
    assert _REAL_SAVE("Alex", [{"ticker": "ARM", "trig_date": "2026-10-09"}], "g") is True
    assert set(store.payload) == {"Sam", "Alex"} and store.payload["Sam"]["rows"] == PREV


def test_a_concurrent_writer_is_retried_not_overwritten(store, monkeypatch):
    import web_store
    real_read = store.read
    first = []

    def racing_read(key):
        out = real_read(key)
        if not first:                       # another job writes between this read and our save
            first.append(1)
            store.payload = dict(store.payload, Casey={"rows": []}); store.rev += 1
        return out
    monkeypatch.setattr(web_store, "read_json_store_versioned", racing_read)
    assert _REAL_SAVE("Alex", [], "g") is True
    assert set(store.payload) == {"Sam", "Casey", "Alex"}


def test_a_failed_write_raises_instead_of_returning_false(store, monkeypatch):
    import web_store
    monkeypatch.setattr(web_store, "save_json_store_versioned", lambda *a, **k: None)
    with pytest.raises(RuntimeError):
        _REAL_SAVE("Alex", [], "g")


def test_an_outage_is_not_read_as_never_sent(store):
    assert _REAL_LOAD("Sam")["rows"] == PREV and _REAL_LOAD("Alex") is None
    store.up = False
    with pytest.raises(RuntimeError):
        _REAL_LOAD("Sam")


def test_open_and_recently_rechecked_is_not_called_stale(wired):
    recent = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    wired({"ARM": []}, history=("OPEN", recent))
    why = _why()[0]
    assert why.startswith("Reason not determined") and "over 4 days" not in why


def test_never_rechecked_does_not_print_none(wired):
    wired({"ARM": []}, history=("OPEN", None))
    assert "re-checked never UTC" in _why()[0]


def test_direction_is_judged_on_the_setup_the_server_picks(wired, monkeypatch):
    """Server order: _report_setup picks, THEN the pick's direction is gated. A previous BULL trigger replaced
    by an open BEAR pick that the direction filter blocks is 'excluded', not 'now shows its BEAR trigger'."""
    bear = dict(ARM_18_SEP, trig_date="2026-10-09", direction="BEAR", outcome="OPEN")
    wired({"ARM": [bear, dict(ARM_18_SEP, outcome="TARGET")]})
    monkeypatch.setattr(server, "_user_trade_allows", lambda n, r: r["direction"] != "BEAR")
    assert _why()[0] == "Excluded by your market switches or direction / location filters (BEAR, NASDAQ 100)."


def test_a_row_with_no_trigger_date_is_not_sent_to_the_database(wired):
    wired({"ARM": [ARM_18_SEP]})
    assert _why(prev=[dict(PREV[0], trig_date=None)]) == [
        "Reason not determined: the previous report recorded no trigger date for it."]


def test_a_setup_read_failure_does_not_poison_later_rows(wired, monkeypatch):
    wired({"ARM": [ARM_18_SEP]}, allows=False)
    calls = []

    def flaky(n):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("pool full")
        return LIMITS
    monkeypatch.setattr(trading_limits, "user_limits", flaky)
    why = _why(prev=[PREV[0], dict(PREV[0])])
    assert why[0].startswith("Reason not determined: the check failed (RuntimeError: pool full)")
    assert why[1] == "Excluded by your market switches or direction / location filters (BULL, NASDAQ 100)."
