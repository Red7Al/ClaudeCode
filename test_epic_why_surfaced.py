"""2026-11-22: WHY get_epic returned None reaches the places that report a missing epic (Architect ruling).

Before: describe_size_skip alone read ig_shim.LAST_EPIC_WHY. open_trade and place_working_order raised the failed-order
alert with the fixed text "No IG epic found -- check epic_lookup / config.EPIC_MAP" whatever get_epic had found, and the
Data Quality Audit, price validation and the social feed logged "no epic" with no cause."""
import sys
import threading
import types

import pytest

import ig_shim
import notify

pytestmark = pytest.mark.usefixtures("empty_database")

CAUSE = "the best IG match is not at the last close or has no IG price, so its identity is not confirmed"


def _refuse(ticker):
    return ig_shim._no_epic(ticker, CAUSE)


def _alerts(monkeypatch):
    got = []
    monkeypatch.setattr(notify, "alert_missed_trade", lambda t, d, reason, *a, **k: got.append(reason))
    return got


def _open(monkeypatch, get_epic):
    monkeypatch.setattr(ig_shim, "get_epic", get_epic)
    monkeypatch.setattr(ig_shim, "check_circuit_breakers", lambda *a, **k: (True, ""))
    return ig_shim.open_trade(user_id="u1", ticker="TST", direction="BUY", size=1.0, stop_distance=1.0,
                              limit_distance=2.0, session_name="WEB_BRIDGE", signal_summary="s")


def _place(monkeypatch, get_epic):
    monkeypatch.setattr(ig_shim, "get_epic", get_epic)
    monkeypatch.setattr(ig_shim, "_get_pending_working_order", lambda t, u: None)
    return ig_shim.place_working_order(user_id="u1", ticker="TST", direction="BUY", size=1.0, entry_level=100.0,
                                       stop_level=95.0, limit_level=130.0, session_name="WEB_BRIDGE",
                                       signal_summary="s", watch=False)


@pytest.mark.parametrize("driver,tag", [(_open, ""), (_place, "[working order] ")])
def test_the_failed_order_alert_carries_the_cause_and_keeps_its_prefix(monkeypatch, driver, tag):
    got = _alerts(monkeypatch)
    assert driver(monkeypatch, _refuse) is None
    assert len(got) == 1 and got[0].startswith(f"{tag}No IG epic found -- ")
    assert CAUSE in got[0]
    assert notify._classify_missed_trade(got[0])[0] == "OTHER"          # the class it had before: not newly silenced or paged


@pytest.mark.parametrize("driver", [_open, _place])
def test_no_recorded_reason_says_so_instead_of_staying_blank(monkeypatch, driver):
    got = _alerts(monkeypatch)
    assert driver(monkeypatch, lambda t: None) is None
    assert "recorded no reason (it did not raise)" in got[0]


def test_every_literal_reason_get_epic_gives_keeps_the_alert_in_the_same_class():
    """A cause with a class needle in it ('cap', 'reject', 'size is 0') would re-class the alert."""
    import re
    src = open(ig_shim.__file__, encoding="utf-8").read()
    reasons = re.findall(r'_no_epic\(ticker,\s*(?:f)?"([^"]+)"', src)
    assert len(reasons) >= 8
    for r in reasons:
        assert notify._classify_missed_trade(f"No IG epic found -- {r}. Check epic_lookup")[0] == "OTHER", r


def test_the_size_path_still_classes_size_zero_with_the_cause():
    ig_shim._no_epic("AAPL", CAUSE)
    text = ig_shim.describe_size_skip("AAPL", None)
    assert CAUSE in text and notify._classify_missed_trade(text)[0] == "SIZE_ZERO"


def test_two_tickers_failing_in_turn_do_not_read_each_others_reason():
    ig_shim._no_epic("AAA", "reason A")
    ig_shim._no_epic("BBB", "reason B")
    assert ig_shim.epic_missing_why("BBB") == "reason B"
    assert ig_shim.epic_missing_why("AAA") == "reason A"
    assert "recorded no reason" in ig_shim.epic_missing_why("AAA")      # consumed once
    ig_shim._no_epic("CCC", "reason C")
    seen = {}
    t = threading.Thread(target=lambda: seen.update(w=ig_shim.epic_missing_why("CCC")))
    t.start(); t.join()
    assert "reason C" not in seen["w"] and ig_shim.epic_missing_why("CCC") == "reason C"


def test_the_data_quality_audit_row_says_why_there_is_no_epic(monkeypatch):
    import run_data_quality_audit as dqa
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace())
    monkeypatch.setattr(ig_shim, "get_epic", _refuse)
    row = dqa._audit_ticker("TST")
    assert row["verdict"] == "NO_EPIC" and CAUSE in row["detail"]


def test_the_price_validation_and_the_social_feed_log_the_cause(monkeypatch, caplog):
    import logging
    import price_action
    monkeypatch.setattr(ig_shim, "get_epic", _refuse)
    monkeypatch.setattr(price_action, "_ig_validation_cache_get", lambda *a, **k: None, raising=False)
    with caplog.at_level(logging.INFO):
        price_action.validate_hvf_with_ig("TST", {"hvf_type": "BULLISH"})
    assert CAUSE in caplog.text
    import social_monitor
    caplog.clear()
    monkeypatch.setattr(ig_shim.session, "ensure_authenticated", lambda: None)
    monkeypatch.setattr(social_monitor, "_ensure_direction_column", lambda: None)
    monkeypatch.setattr(social_monitor, "db_run", lambda sql, **kw: [] if sql.lstrip().startswith("select") else None)
    with caplog.at_level(logging.INFO):
        social_monitor.save_new_pick("TSTX", "someone", "rss", "https://x.com/a/status/1")
    assert CAUSE in caplog.text


# ---- added by the independent test specialist 2026-11-22: the three log sites no test read ---------------------------------
@pytest.mark.parametrize("driver", [_open, _place])
def test_the_error_log_line_carries_the_cause_too(monkeypatch, caplog, driver):
    import logging
    _alerts(monkeypatch)
    with caplog.at_level(logging.ERROR):
        assert driver(monkeypatch, _refuse) is None
    assert CAUSE in caplog.text


def test_the_pin_check_prints_why_no_epic_resolved(monkeypatch, capsys):
    import run_data_quality_audit as dqa
    monkeypatch.setattr(ig_shim, "get_epic", _refuse)
    dqa._verify_epics(["TST"])
    assert CAUSE in capsys.readouterr().out
