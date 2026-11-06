"""The nightly Data Quality Audit emails the owner only a wrong-instrument finding on a live order or position (owner
2026-11-06: "resolve them yourself"), and a pinned ticker's cached row that is not its pin is a finding (2026-10-27).
Fixture rows are copied from epic_lookup and from run 40411009387's log."""
import run_data_quality_audit as dqa

GLD_ROW = ["GLD", "AR.D.GLDSJ.DAILY.IP", "NewGold Issuer Ltd"]          # epic_lookup, read 2026-10-27
SPCX_ROW = ["SPCX", "UD.D.SPCXUS.DAILY.IP", "SpaceX (24 Hours)"]       # on its pin
AAL = {"ticker": "AAL", "days_compared": 0, "close_max_dev_pct": None, "phantom_high_wicks": 0,
       "phantom_low_wicks": 0, "verdict": "IDENTITY_MISMATCH",
       "detail": "epic AB.D.AALAU.SEP.IP is 'Alfabs Australia Ltd' but Yahoo says AAL is "
                 "'American Airlines Group, Inc.' — WRONG COMPANY mapped"}


class _Db:
    def __init__(self, rows, live=()):
        self.rows, self.sql, self.live = rows, [], live

    def run(self, sql, **kw):
        self.sql.append(sql)
        if "working_orders" in sql:
            return [[t] for t in self.live]
        return [r for r in self.rows if r[0] in kw.get("t", [])]

    def close(self):
        pass


def test_a_pinned_ticker_cached_on_another_code_is_a_finding(monkeypatch):
    monkeypatch.setattr("db_pool.get_db", lambda: _Db([GLD_ROW, SPCX_ROW]))
    found = dqa._pin_sweep()
    assert [(f["ticker"], f["verdict"]) for f in found] == [("GLD", "PIN_MISMATCH")]
    assert "SI.D.GLDUS.DAILY.IP" in found[0]["detail"] and "NewGold" in found[0]["detail"]


def _run_main(monkeypatch, identity, pins, live=(), fresh=()):
    sent = []
    import trade_email
    monkeypatch.setattr(trade_email, "send_simple_email", lambda **k: sent.append(k) or True)
    monkeypatch.setattr(dqa.sys, "argv", ["run_data_quality_audit.py", "0"])
    monkeypatch.setattr(dqa, "_pick_batch", lambda cap: [])
    monkeypatch.setattr(dqa, "_identity_sweep_all_cached", lambda: list(identity))
    monkeypatch.setattr("db_pool.get_db", lambda: _Db(pins, live))
    monkeypatch.setattr(dqa, "_save", lambda rows: None)
    monkeypatch.setattr(dqa, "_post_slack", lambda *a: None)
    monkeypatch.setattr(dqa, "_absurd_outcomes", lambda *a, **k: list(fresh))
    dqa.main()
    return sent


def test_a_finding_on_a_live_order_or_position_is_emailed_and_only_that_one(monkeypatch):
    sent = _run_main(monkeypatch, [AAL], [GLD_ROW, SPCX_ROW], live=["AAL"])
    assert len(sent) == 1
    assert sent[0]["subject"] == "Data Quality Audit: 1 wrong-instrument finding(s) on a live order or position"
    assert "AAL: epic AB.D.AALAU.SEP.IP is 'Alfabs Australia Ltd'" in sent[0]["text"]
    assert "GLD" not in sent[0]["text"]


def test_findings_on_nothing_live_send_no_email(monkeypatch):
    assert _run_main(monkeypatch, [AAL], [GLD_ROW, SPCX_ROW], live=["ULVR.L"]) == []


def test_absurd_outcomes_alone_send_no_email(monkeypatch):
    import datetime
    row = {"ticker": "AGL.AX", "market": "ASX", "triggered_date": datetime.date.today().isoformat(),
           "outcome": "STOPPED", "faults": ["stop 0.20% from entry"]}
    assert _run_main(monkeypatch, [], [SPCX_ROW], live=["AGL.AX"], fresh=[row]) == []


def test_the_sweep_judges_on_get_epics_yahoo_name(monkeypatch):
    """Real row, names read 2026-11-06: shortName 'BEN ADE BK FPO [BEN]' failed, get_epic's long name passes."""
    monkeypatch.setattr("db_pool.get_db", lambda: type("D", (), {
        "run": lambda s, q, **k: [["BEN.AX", "AA.D.BEN.DAILY.IP", "Bendigo and Adelaide Bank Ltd"],
                                  ["AAL", "AB.D.AALAU.SEP.IP", "Alfabs Australia Ltd"]],
        "close": lambda s: None})())
    names = {"BEN.AX": "Bendigo and Adelaide Bank Limited", "AAL": "American Airlines Group, Inc."}
    monkeypatch.setattr("ig_shim._yahoo_name", lambda t: names[t])
    assert [f["ticker"] for f in dqa._identity_sweep_all_cached()] == ["AAL"]


def test_the_audit_workflow_carries_an_email_sender():
    """Without these the job logs "No email sender configured" and the email is skipped -- as the Closing Window's IG
    pause emails were on 2026-10-27 (runs 40491593154, 40506969984)."""
    import pathlib
    wf = pathlib.Path(__file__).with_name(".github") / "workflows" / "trading-data-quality.yml"
    text = wf.read_text(encoding="utf-8")
    for name in ("YAHOO_USER", "YAHOO_APP_PASSWORD", "RESEND_API_KEY"):
        assert f"{name}:" in text and "${{ secrets." + name + " }}" in text, name


def test_nothing_found_sends_nothing(monkeypatch):
    assert _run_main(monkeypatch, [], [SPCX_ROW]) == []
