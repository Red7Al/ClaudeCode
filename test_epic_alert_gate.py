"""2026-11-09, owner: "I'm still getting many daily system alerts - please sort out the underlying issue".

MEASURED (Morning Chain run 42556806199, 03:30 UTC): the price step asks get_epic for every ticker; 42 had no IG epic
(FTSE 250 shares IG does not list, US names with no matching code). Each raised a "wrong-instrument risk" system alert --
10 emailed, 32 held back by the daily cap. None of the 42 was on a live order or position (21 live tickers), none had an
epic_lookup row. A miss is not cached, so it repeats every run (alert_emails 2026-11-02, 03, 07, 09 all hit the cap of 10).

Rule (owner 2026-11-06): only a wrong-instrument finding on a live order or position is emailed. get_epic's alerts now go
through ig_shim._epic_alert, which uses the same ig_shim.live_tickers as the Data Quality Audit."""
import re
from pathlib import Path

import pytest

import ig_shim
import notify

# The tickers that raised an alert in the 2026-11-09 03:30 Morning Chain, read from its log.
MORNING_ALERT_TICKERS = (
    "AGT.L AIE.L APN.L ATYM.L AUTO.L BREE.L BT-A.L CCEP CCEP.L CHRY.L COA.L FEML.L GBG.L GROW.L HVPE.L HWG.L ICE "
    "IGG.L III.L IPO.L ITH.L JTC.L KLAC MEGP.L MMM MNTN.L MTLN.L OCI.L OSB.L PAGE.L PINT.L POLN.L PPH.L PRN.L RPI.L "
    "RS1.L SHAW.L SRE.L SUPR.L TECH THRL.L VOF.L").split()

# 21 live tickers is the live count that day; these are the shape (the actual tickers are not needed: none overlapped).
LIVE_ELSEWHERE = {"AAPL", "MSFT", "BA.L", "RR.L", "4523.T", "MRO.L"}


@pytest.fixture
def alerts(monkeypatch):
    calls = []
    monkeypatch.setattr(notify, "alert_system_error",
                        lambda session, component, summary, detail="", email=True: calls.append(
                            {"summary": summary, "email": email}))
    return calls


def _live(monkeypatch, tickers):
    monkeypatch.setattr(ig_shim, "live_tickers", lambda: set(tickers))


def test_42_morning_alerts_none_on_a_live_ticker_none_emailed(monkeypatch, alerts):
    assert len(MORNING_ALERT_TICKERS) == 42
    _live(monkeypatch, LIVE_ELSEWHERE)
    for t in MORNING_ALERT_TICKERS:
        ig_shim._epic_alert(t, f"{t}: no IG search result matches this company's name -- wrong-instrument risk")
    assert len(alerts) == 42, "the run-log / Slack alert must still be raised"
    assert [a for a in alerts if a["email"]] == [], "an alert on a ticker with no live order was emailed"


def test_a_live_ticker_is_still_emailed(monkeypatch, alerts):
    _live(monkeypatch, {"SHAW.L"})
    ig_shim._epic_alert("SHAW.L", "SHAW.L resolved to a non-UK epic")
    assert alerts == [{"summary": "SHAW.L resolved to a non-UK epic", "email": True}]


def test_a_live_uk_order_is_found_under_the_bare_code_too(monkeypatch, alerts):
    """The cache is keyed on the normalised ticker; working_orders may hold either spelling."""
    _live(monkeypatch, {"SHAW"})
    ig_shim._epic_alert("SHAW.L", "x")
    assert alerts[0]["email"] is True


def test_if_the_live_set_cannot_be_read_nothing_is_emailed_and_the_error_is_logged(monkeypatch, alerts, caplog):
    def refused():
        raise RuntimeError("pool full")
    monkeypatch.setattr(ig_shim, "live_tickers", refused)
    with caplog.at_level("ERROR", logger=ig_shim.log.name):
        ig_shim._epic_alert("SHAW.L", "x")
    assert alerts == [{"summary": "x", "email": False}]
    assert "alert not checked against live orders" in caplog.text


class _Db:
    def __init__(self):
        self.sql = []

    def run(self, sql, **kw):
        self.sql.append(sql)
        return []

    def close(self):
        pass


def test_get_epic_itself_routes_an_unlisted_uk_share_through_the_gate(monkeypatch, alerts):
    """Real row, 2026-11-09: IG lists Shawbrook only as KC.D.SHAWLN.DAILY.IP (a spread-bet code, not KA.D.), so
    get_epic refuses it -- correctly -- and used to email about it every run."""
    db = _Db()
    monkeypatch.setattr(ig_shim, "get_db", lambda: db)
    monkeypatch.setattr(ig_shim, "_EPIC_VERIFIED_OVERRIDES", {})
    monkeypatch.setattr(ig_shim, "_heuristic_epic", lambda t: None)
    monkeypatch.setattr(ig_shim, "_yahoo_name", lambda t: "Shawbrook Group plc")
    monkeypatch.setattr(ig_shim, "_last_close", lambda t: None)
    monkeypatch.setattr(ig_shim.session, "get", lambda path, params=None, version=None: {"markets": [
        {"epic": "KC.D.SHAWLN.DAILY.IP", "instrumentName": "Shawbrook Group PLC", "expiry": "DFB",
         "instrumentType": "SHARES"}]})
    _live(monkeypatch, LIVE_ELSEWHERE)
    assert ig_shim.get_epic("SHAW.L") is None
    assert len(alerts) == 1 and alerts[0]["email"] is False
    assert "non-UK epic" in alerts[0]["summary"]
    _live(monkeypatch, {"SHAW.L"})
    ig_shim.get_epic("SHAW.L")
    assert alerts[-1]["email"] is True, "a live order on the same ticker must still be told to the owner"


def test_no_get_epic_alert_bypasses_the_gate():
    """Four sites used to call notify.alert_system_error directly. A fifth that does would email again."""
    src = Path(ig_shim.__file__).read_text(encoding="utf-8")
    body = src[src.index("def get_epic(") : src.index("\ndef ", src.index("def get_epic(") + 10)]
    assert not re.search(r'alert_system_error\(\s*"EPIC_LOOKUP"', body)
    assert body.count("_epic_alert(ticker") == 4


def test_the_audit_and_get_epic_share_one_definition_of_live(monkeypatch):
    import run_data_quality_audit as dqa
    monkeypatch.setattr(ig_shim, "live_tickers", lambda: {"X1", "X2"})
    assert dqa._live_tickers() == {"X1", "X2"}
