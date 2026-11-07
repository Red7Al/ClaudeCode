"""The nightly Data Quality Audit emails the owner only a wrong-instrument finding on a live order or position (owner
2026-11-06: "resolve them yourself"), and a pinned ticker's cached row that is not its pin is a finding (2026-10-27).
Fixture rows are copied from epic_lookup and from run 40411009387's log."""
import sqlite3
import sys

import pytest

import ig_shim
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


# Every scanned epic_lookup row the old shortName sweep flagged, read 2026-11-06: (ticker, epic, IG name, Yahoo
# shortName, Yahoo longName). Names and codes only -- the repository is public. RIGHT_ROWS are the right company, judged
# on the names. WRONG_ROWS are the wrong instrument (both pinned elsewhere) and must stay flagged. KNOWN GAP, left out on
# purpose: BZ=F, 7532.T, LLC.AX, BWZ, ^MXX and SPIP pass name matching on one shared word though they look wrong, so no
# name test can say they are right; they are not asserted either way.
RIGHT_ROWS = [
    ('0001.HK', 'AC.D.1HK.DAILY.IP', 'CK Hutchison Holdings Ltd - 1 HK',
     'CKH HOLDINGS', 'CK Hutchison Holdings Limited'),
    ('0016.HK', 'AC.D.16HK.DAILY.IP', 'Sun Hung Kai Properties Ltd',
     'SHK PPT', 'Sun Hung Kai Properties Limited'),
    ('0316.HK', 'AC.D.316HK.DAILY.IP', 'Orient Overseas International Ltd',
     'OOIL', 'Orient Overseas (International) Limited'),
    ('0386.HK', 'AC.D.386HK.DAILY.IP', 'China Petroleum & Chemical Corp',
     'SINOPEC CORP', 'China Petroleum & Chemical Corporation'),
    ('0388.HK', 'AC.D.388HK.DAILY.IP', 'Hong Kong Exchanges and Clearing Ltd',
     'HKEX', 'Hong Kong Exchanges and Clearing Limited'),
    ('1024.HK', 'AC.D.1024HK.DAILY.IP', 'Kuaishou Technology',
     'KUAISHOU-W', 'Kuaishou Technology'),
    ('1038.HK', 'AC.D.1038HK.DAILY.IP', 'Cheung Kong Infrastructure Holdings Ltd',
     'CKI HOLDINGS', 'CK Infrastructure Holdings Limited'),
    ('1177.HK', 'AC.D.1177HK.DAILY.IP', 'Sino Biopharmaceutical Limited',
     'SBP GROUP', 'Sino Biopharmaceutical Limited'),
    ('1378.HK', 'AC.D.1378HK.DAILY.IP', 'China Hongqiao Group Limited',
     'CHINAHONGQIAO', 'China Hongqiao Group Limited'),
    ('1398.HK', 'AC.D.1398HK.DAILY.IP', 'Industrial & Commercial Bank of China Ltd',
     'ICBC', 'Industrial and Commercial Bank of China Limited'),
    ('1810.HK', 'AC.D.1810HK.DAILY.IP', 'Xiaomi Corporation',
     'XIAOMI-W', 'Xiaomi Corporation'),
    ('2015.HK', 'AC.D.2015HK.DAILY.IP', 'Li Auto Inc (HK)',
     'LI AUTO-W', 'Li Auto Inc.'),
    ('3690.HK', 'AC.D.3690HK.DAILY.IP', 'Meituan',
     'MEITUAN-W', 'Meituan'),
    ('6823.HK', 'AC.D.6823HK.DAILY.IP', 'HKT Trust and HKT Ltd',
     'HKT-SS', 'HKT Trust and HKT Limited'),
    ('9618.HK', 'AC.D.9618HK.DAILY.IP', 'JD.com Inc',
     'JD-SW', 'JD.com, Inc.'),
    ('9868.HK', 'AC.D.9868HK.DAILY.IP', 'XPeng Inc (HK)',
     'XPENG-W', 'XPeng Inc.'),
    ('9888.HK', 'AC.D.9888HK.DAILY.IP', 'Baidu Inc (HK)',
     'BIDU-W', 'Baidu, Inc.'),
    ('9988.HK', 'AC.D.9988HK.DAILY.IP', 'Alibaba Group Holding Limited (HK)',
     'BABA-W', 'Alibaba Group Holding Limited'),
    ('9999.HK', 'AC.D.9999HK.DAILY.IP', 'NetEase Inc',
     'NTES', 'NetEase, Inc.'),
    ('BEN.AX', 'AA.D.BEN.DAILY.IP', 'Bendigo and Adelaide Bank Ltd',
     'BEN ADE BK FPO [BEN]', 'Bendigo and Adelaide Bank Limited'),
    ('BF-B', 'SA.D.BFB.DAILY.IP', 'Brown-Forman Corp - Class B (24 Hours)',
     'Brown Forman Inc', 'Brown-Forman Corporation'),
    ('BWP.AX', 'AA.D.BWP.DAILY.IP', 'BWP Trust',
     'BWPPROPGP STAPLED [BWP]', 'BWP Trust'),
    ('BXB.AX', 'AA.D.BXB.SEP.IP', 'Brambles Ltd',
     'BRAMB LTD FPO [BXB]', 'Brambles Limited'),
    ('CMM.AX', 'AB.D.CMMAU.DAILY.IP', 'Capricorn Metals Limited',
     'CAPMETALS FPO [CMM]', 'Capricorn Metals Ltd'),
    ('COL.AX', 'AA.D.BBG.SEP.IP', 'Coles Group Limited',
     'COLESGROUP FPO [COL]', 'Coles Group Limited'),
    ('CPU.AX', 'AA.D.CPU.SEP.IP', 'Computershare Ltd',
     'CSHARE FPO [CPU]', 'Computershare Limited'),
    ('CTD.AX', 'AB.D.CTDAU.SEP.IP', 'Corporate Travel Management Ltd',
     'CORP TRAV FPO [CTD]', 'Corporate Travel Management Limited'),
    ('DMP.AX', 'AF.D.DMP.DEC.IP', "Domino's Pizza Enterprises Ltd",
     'DOMINOS FPO [DMP]', "Domino's Pizza Enterprises Limited"),
    ('DSFIR.AS', 'EG.D.DSFIRNA.DAILY.IP', 'DSM-Firmenich AG',
     'DSM FIRMENICH AG', 'DSM-Firmenich AG'),
    ('GBLB.BR', 'EA.D.GBL.DAILY.IP', 'Groupe Bruxelles Lambert SA',
     'GBL', 'Groupe Bruxelles Lambert SA'),
    ('GMG.AX', 'AA.D.GMG.SEP.IP', 'Goodman Group',
     'GOOD GROUP STAPLED [GMG]', 'Goodman Group'),
    ('HGT.L', 'KA.D.HGT.DAILY.IP', 'HgCapital Trust PLC',
     'HG CAPITAL TRUST PLC ORD 2.5P', 'HgCapital Trust Ord'),
    ('HUB.AX', 'AB.D.HUBAU.SEP.IP', 'HUB24 Limited',
     'HUB24LTD FPO [HUB]', 'HUB24 Limited'),
    ('IAG.AX', 'AA.D.IAG.SEP.IP', 'Insurance Australia Group Ltd',
     'INSUR.AUST FPO [IAG]', 'Insurance Australia Group Limited'),
    ('IGO.AX', 'AA.D.IGO.DEC.IP', 'IGO Limited',
     'IGOLIMITED FPO [IGO]', 'IGO Limited'),
    ('MIN.AX', 'AF.D.MINAU.SEP.IP', 'Mineral Resources Ltd',
     'MINERALRES FPO [MIN]', 'Mineral Resources Limited'),
    ('MQG.AX', 'AA.D.MQG.SEP.IP', 'Macquarie Group Ltd',
     'MACQ GROUP FPO [MQG]', 'Macquarie Group Limited'),
    ('MTS.AX', 'AA.D.MTS.DAILY.IP', 'Metcash Ltd',
     'METCASHLTD FPO [MTS]', 'Metcash Limited'),
    ('PCG', 'SE.D.PCG.DAILY.IP', 'PG&E Corp (24 Hours)',
     'Pacific Gas & Electric Co.', 'PG&E Corporation'),
    ('PLS.AX', 'AB.D.PLSAU.DAILY.IP', 'PLS Group Limited',
     'PLSGROUP FPO [PLS]', 'PLS Group Limited'),
    ('PME.AX', 'AF.D.PME.DEC.IP', 'Pro Medicus Ltd',
     'PROMEDICUS FPO [PME]', 'Pro Medicus Limited'),
    ('PMV.AX', 'AF.D.PMV.SEP.IP', 'Premier Investments Ltd',
     'PREM INV. FPO [PMV]', 'Premier Investments Limited'),
    ('SUL.AX', 'AF.D.SUL.DAILY.IP', 'Super Retail Group Ltd',
     'SUPARETAIL FPO [SUL]', 'Super Retail Group Limited'),
    ('WES.AX', 'AA.D.WESAU.SEP.IP', 'Wesfarmers Ltd',
     'WESFARMER FPO [WES]', 'Wesfarmers Limited'),
]
WRONG_ROWS = [
    ('MSTR', 'AB.D.MSTRAU.SEP.IP', 'Morningstar International Shares Active ETF',
     'Strategy Inc', 'Strategy Inc'),
    ('PYPL', 'SI.D.PYPYUS.DAILY.IP', 'YieldMax PYPL Option Income Strategy ETF',
     'PayPal Holdings, Inc.', 'PayPal Holdings, Inc.'),
]


def _yahoo_answers(monkeypatch, rows):
    """A stand-in for yfinance holding the real pair of names, so a sweep reverted to shortName is judged on it."""
    import types
    info = {r[0]: {"shortName": r[3], "longName": r[4]} for r in rows}
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(
        Ticker=lambda t: types.SimpleNamespace(info=info[t])))
    monkeypatch.delenv("HVF_WEB_TIER", raising=False)
    monkeypatch.setattr(ig_shim, "_YNAME_CACHE", {})


def _sweep(monkeypatch, cached):
    monkeypatch.setattr("db_pool.get_db", lambda: type("D", (), {
        "run": lambda s, q, **k: [list(r) for r in cached], "close": lambda s: None})())
    return dqa._identity_sweep_all_cached()


def test_the_real_rows_are_what_they_are_said_to_be():
    """The fixture itself: the old name failed all 46 rows, the long name clears the 44 and only the 44."""
    for t, _, ig_name, short, long_name in RIGHT_ROWS:
        assert not ig_shim.instrument_names_match(t, ig_name, short), t
        assert ig_shim.instrument_names_match(t, ig_name, long_name), t
    for t, _, ig_name, short, long_name in WRONG_ROWS:
        assert not ig_shim.instrument_names_match(t, ig_name, short), t
        assert not ig_shim.instrument_names_match(t, ig_name, long_name), t
    assert (len(RIGHT_ROWS), len(WRONG_ROWS)) == (44, 2)


def test_the_sweep_over_the_real_rows_flags_only_the_wrong_instruments(monkeypatch):
    _yahoo_answers(monkeypatch, RIGHT_ROWS + WRONG_ROWS)
    found = _sweep(monkeypatch, [r[:3] for r in RIGHT_ROWS + WRONG_ROWS])
    assert [f["ticker"] for f in found] == ["MSTR", "PYPL"]
    assert {f["verdict"] for f in found} == {"IDENTITY_MISMATCH"}
    assert "Morningstar" in found[0]["detail"] and "'Strategy Inc'" in found[0]["detail"]


def test_a_row_yahoo_cannot_name_is_counted_and_not_a_finding(monkeypatch, caplog):
    monkeypatch.setattr("ig_shim._yahoo_name", lambda t: "")
    with caplog.at_level("INFO", logger=dqa.log.name):
        assert _sweep(monkeypatch, [r[:3] for r in WRONG_ROWS]) == []
    assert "Identity sweep: 2 cached epics, 2 with no Yahoo name, 0 mismatches" in caplog.text


def _live_db():
    """_live_tickers' own SQL, executed. Statuses are those in working_orders on 2026-11-06."""
    con = sqlite3.connect(":memory:")
    con.execute("create table working_orders (ticker text, status text)")
    con.execute("create table positions (ticker text)")
    con.executemany("insert into working_orders values (?, ?)",
                    [("PEND.L", "PENDING"), ("WATCH.AX", "WATCHING"), ("BOTH", "PENDING"), ("FILL.L", "FILLED"),
                     ("GONE", "DELETED"), ("CANC", "CANCELLED"), ("OLD", "EXPIRED")])
    con.executemany("insert into positions values (?)", [("OPEN",), ("BOTH",)])
    return type("D", (), {"run": lambda s, q, **k: [list(r) for r in con.execute(q)], "close": lambda s: None})()


def test_live_means_pending_watching_or_a_position(monkeypatch):
    monkeypatch.setattr("db_pool.get_db", _live_db)
    assert dqa._live_tickers() == {"PEND.L", "WATCH.AX", "BOTH", "OPEN"}


def test_every_emailed_verdict_on_a_live_ticker_is_sent_and_no_other(monkeypatch):
    sent = []
    import trade_email
    monkeypatch.setattr(trade_email, "send_simple_email", lambda **k: sent.append(k) or True)
    monkeypatch.setattr(dqa, "_live_tickers", lambda: {"A", "B", "C", "D"})
    rows = [{"ticker": "A", "verdict": "PIN_MISMATCH", "detail": "pin a"},
            {"ticker": "B", "verdict": "IDENTITY_MISMATCH", "detail": "identity b"},
            {"ticker": "C", "verdict": "CRITICAL_MISMATCH", "detail": "critical c"},
            {"ticker": "D", "verdict": "OK", "detail": "fine d"},
            {"ticker": "E", "verdict": "IDENTITY_MISMATCH", "detail": "not live e"}]
    assert dqa._email_findings(rows) is True
    assert len(sent) == 1 and sent[0]["subject"].startswith("Data Quality Audit: 3 wrong-instrument")
    for line in ("A: pin a", "B: identity b", "C: critical c"):
        assert line in sent[0]["text"]
    assert "fine d" not in sent[0]["text"] and "not live e" not in sent[0]["text"]


def test_no_finding_reads_nothing_and_sends_nothing(monkeypatch):
    monkeypatch.setattr(dqa, "_live_tickers", lambda: pytest.fail("no finding, so no reason to read the database"))
    assert dqa._email_findings([{"ticker": "D", "verdict": "OK", "detail": ""}]) is False


def test_an_absurd_outcome_stays_out_of_the_email_and_in_the_run_log(monkeypatch, caplog):
    import datetime
    row = {"ticker": "AGL.AX", "market": "ASX", "triggered_date": datetime.date.today().isoformat(),
           "outcome": "STOPPED", "faults": ["stop 0.20% from entry"]}
    with caplog.at_level("WARNING", logger=dqa.log.name):
        sent = _run_main(monkeypatch, [AAL], [SPCX_ROW], live=["AAL"], fresh=[row])
    assert len(sent) == 1 and "AGL.AX" not in sent[0]["text"] and "absurd" not in sent[0]["subject"].lower()
    assert "Absurd outcomes: 1 new" in caplog.text and "AGL.AX" in caplog.text


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
