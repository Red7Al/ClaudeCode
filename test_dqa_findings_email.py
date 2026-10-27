"""The nightly Data Quality Audit's findings reach the owner by EMAIL, and a pinned ticker's cached row that is not its
pin is a finding (2026-10-27). Both Slack posters return early -- Slack is not used -- so the 10-26 run's 94
wrong-company codes reached nobody. Fixture rows are copied from epic_lookup and from run 40411009387's log."""
import run_data_quality_audit as dqa

GLD_ROW = ["GLD", "AR.D.GLDSJ.DAILY.IP", "NewGold Issuer Ltd"]          # epic_lookup, read 2026-10-27
SPCX_ROW = ["SPCX", "UD.D.SPCXUS.DAILY.IP", "SpaceX (24 Hours)"]       # on its pin
AAL = {"ticker": "AAL", "days_compared": 0, "close_max_dev_pct": None, "phantom_high_wicks": 0,
       "phantom_low_wicks": 0, "verdict": "IDENTITY_MISMATCH",
       "detail": "epic AB.D.AALAU.SEP.IP is 'Alfabs Australia Ltd' but Yahoo says AAL is "
                 "'American Airlines Group, Inc.' — WRONG COMPANY mapped"}


class _Db:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def run(self, sql, **kw):
        self.sql.append(sql)
        return [r for r in self.rows if r[0] in kw.get("t", [])]

    def close(self):
        pass


def test_a_pinned_ticker_cached_on_another_code_is_a_finding(monkeypatch):
    monkeypatch.setattr("db_pool.get_db", lambda: _Db([GLD_ROW, SPCX_ROW]))
    found = dqa._pin_sweep()
    assert [(f["ticker"], f["verdict"]) for f in found] == [("GLD", "PIN_MISMATCH")]
    assert "SI.D.GLDUS.DAILY.IP" in found[0]["detail"] and "NewGold" in found[0]["detail"]


def _run_main(monkeypatch, identity, pins):
    sent = []
    import trade_email
    monkeypatch.setattr(trade_email, "send_simple_email", lambda **k: sent.append(k) or True)
    monkeypatch.setattr(dqa.sys, "argv", ["run_data_quality_audit.py", "0"])
    monkeypatch.setattr(dqa, "_pick_batch", lambda cap: [])
    monkeypatch.setattr(dqa, "_identity_sweep_all_cached", lambda: list(identity))
    monkeypatch.setattr("db_pool.get_db", lambda: _Db(pins))
    monkeypatch.setattr(dqa, "_save", lambda rows: None)
    monkeypatch.setattr(dqa, "_post_slack", lambda *a: None)
    monkeypatch.setattr(dqa, "_absurd_outcomes", lambda *a, **k: [])
    dqa.main()
    return sent


def test_the_audit_emails_its_wrong_instrument_findings(monkeypatch):
    sent = _run_main(monkeypatch, [AAL], [GLD_ROW, SPCX_ROW])
    assert len(sent) == 1
    assert sent[0]["subject"] == "Data Quality Audit: 2 wrong-instrument finding(s)"
    assert "AAL: epic AB.D.AALAU.SEP.IP is 'Alfabs Australia Ltd'" in sent[0]["text"]
    assert "GLD: epic_lookup has AR.D.GLDSJ.DAILY.IP" in sent[0]["text"]


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
