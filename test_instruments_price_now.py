"""Instruments tab: "Price now" before R:R (owner 2026-11-02: "instrument tab has a table - please add price now
before R:R column").

Price now = the scan's own current_price where it has one, else the summary's `latest_close` section (the latest
stored close no older than 7 days), else blank. MEASURED on the live snapshot 2026-11-02T04:50: 1,999 records,
381 with a scan price. Fixtures below are shaped like those records (AZN.L priced 13960.0; III.L / ADM.L unpriced)
and like the summary file the web tier reads.
"""
import datetime as dt
import json
import os
import re
from decimal import Decimal

import pytest

import trading_limits
from hvf_web import server

GEN = "2026-11-02T04:50:00.874601+00:00"


def _snap():
    return {"generated_utc": GEN, "records": [
        {"ticker": "AZN.L", "name": "AstraZeneca PLC", "market": "FTSE 100", "location": "UK", "sector": "Health",
         "status": "TRIGGERED", "has_signal": True, "current_price": 13960.0, "rr": 7.99, "quality": 62,
         "direction": "BULL"},
        {"ticker": "III.L", "name": "3i Group", "market": "FTSE 100", "location": "UK", "sector": "Financials",
         "status": None, "has_signal": False, "current_price": None, "rr": None, "quality": None,
         "direction": None},
        {"ticker": "ADM.L", "name": "Admiral Group PLC", "market": "FTSE 100", "location": "UK",
         "sector": "Financials", "status": None, "has_signal": False, "current_price": None, "rr": None,
         "quality": None, "direction": None},
    ]}


class _Db:
    """price_history as the query sees it: rows only for the tickers asked for. `close` is numeric (Decimal)."""

    def __init__(self, table, log):
        self.table, self.log = table, log

    def run(self, sql, **k):
        self.log.append((" ".join(sql.split()), k))
        return [(t, self.table[t]) for t in k["t"] if t in self.table]

    def close(self):
        pass


def _db(monkeypatch, table):
    log = []
    monkeypatch.setattr("db_pool.get_db", lambda: _Db(table, log))
    return log


# ── latest_close_section ─────────────────────────────────────────────────────────────────────────────

def test_latest_close_asks_for_every_unpriced_instrument_and_unpriced_open_setup(monkeypatch):
    import publish_report_summary as pub
    log = _db(monkeypatch, {"III.L": Decimal("2915.5"), "OLD.L": Decimal("101.25"), "AZN.L": Decimal("1")})
    # OLD.L: an open setup whose instrument is not in today's snapshot. AZN.L: priced by the scan, so never asked.
    # EMPTY.L: an empty setup list counts for nothing.
    out = pub.latest_close_section(_snap(), {"AZN.L": [{}], "OLD.L": [{}], "EMPTY.L": []})

    assert len(log) == 1
    sql, params = log[0]
    assert params["t"] == ["ADM.L", "III.L", "OLD.L"], "unpriced snapshot + unpriced open setups, sorted"
    assert "bar_date >= current_date - 7" in sql, "an instrument whose prices stopped must show no price"
    assert "distinct on (ticker)" in sql and "order by ticker, bar_date desc" in sql, "the LATEST close"
    assert "close is not null" in sql
    assert out == {"III.L": 2915.5, "OLD.L": 101.25}
    assert all(type(v) is float for v in out.values()), "JSON-safe floats, not Decimal"


def test_latest_close_needs_no_database_when_everything_is_priced(monkeypatch):
    import publish_report_summary as pub
    monkeypatch.setattr("db_pool.get_db", lambda: pytest.fail("database read with nothing to ask"))
    snap = {"generated_utc": GEN, "records": [{"ticker": "AZN.L", "current_price": 13960.0}]}
    assert pub.latest_close_section(snap, {"AZN.L": [{}]}) == {}
    assert pub.latest_close_section(snap) == {}


# ── report_extras: output unchanged ──────────────────────────────────────────────────────────────────

def _funds(monkeypatch):
    import web_store
    monkeypatch.setattr(web_store, "load_json_store", lambda k: {"records": {"OLD.L": {"kpis": {
        "trailingPE": 20.728573, "heldPercentInsiders": 0.79668}}}})


def test_report_extras_is_identical_given_the_section_or_computing_it(monkeypatch):
    import publish_report_summary as pub
    _funds(monkeypatch)
    table = {"III.L": Decimal("2915.5"), "OLD.L": Decimal("101.25")}
    setups = {"AZN.L": [{}], "III.L": [{}], "OLD.L": [{}], "ADM.L": [{}], "NONE": []}
    log = _db(monkeypatch, table)
    computed = pub.report_extras(_snap(), setups)
    assert len(log) == 1
    closes = pub.latest_close_section(_snap(), setups)
    log.clear()
    given = pub.report_extras(_snap(), setups, closes)

    assert log == [], "given the section, report_extras must not query again"
    assert computed == given == {
        "ADM.L": {"pe": None, "insider_pct": None},                 # no recent close: no Now, as before
        "III.L": {"current_price": 2915.5, "pe": None, "insider_pct": None},
        "OLD.L": {"current_price": 101.25, "pe": 20.7, "insider_pct": 79.668},
    }


def test_an_open_setup_outside_the_snapshot_keeps_its_now_through_build(monkeypatch):
    """Regression caught during authoring: the section is built for the whole universe; an open setup whose
    instrument is not in today's snapshot must still be in it, or its Scanner Report Now goes blank."""
    import publish_report_summary as pub
    _funds(monkeypatch)
    _db(monkeypatch, {"OLD.L": Decimal("101.25"), "III.L": Decimal("2915.5")})
    _stub_build(monkeypatch, open_setups={"OLD.L": [{}]})
    out = pub.build(_snap())
    assert out["sections"]["latest_close"]["OLD.L"] == 101.25
    assert out["sections"]["report_extras"]["OLD.L"]["current_price"] == 101.25


# ── build() ─────────────────────────────────────────────────────────────────────────────────────────

def _stub_build(monkeypatch, open_setups):
    import publish_report_summary as pub
    for name in ("_stored_metrics", "_live_instrument_metrics", "_snapshot_52wk", "_snapshot_trigger_dates",
                 "_snapshot_rvol", "_snapshot_volscore", "_live_vwap_atr"):
        monkeypatch.setattr(server, name, lambda snap, _n=name: {"from": _n})
    monkeypatch.setattr(server, "_open_trigger_setups", lambda snap: open_setups)
    monkeypatch.setattr(server, "_mcap_map", lambda: {"AZN.L": 1.0})
    monkeypatch.setattr(pub, "squeeze_history_section", lambda: {"rows": []})
    monkeypatch.setattr(pub, "last_bar_section", lambda snap: {"III.L": "2026-10-31"})


def test_build_writes_the_section_with_one_price_query(monkeypatch):
    import publish_report_summary as pub
    _funds(monkeypatch)
    log = _db(monkeypatch, {"III.L": Decimal("2915.5")})
    _stub_build(monkeypatch, open_setups={"III.L": [{}]})
    out = pub.build(_snap())
    assert out["sections"]["latest_close"] == {"III.L": 2915.5}
    assert out["sections"]["report_extras"]["III.L"]["current_price"] == 2915.5
    assert len([s for s, _ in log if "price_history" in s]) == 1, "one query feeds both sections"
    json.dumps(out)   # the file is JSON


def test_a_failing_latest_close_leaves_the_section_none_and_the_file_intact(monkeypatch):
    import publish_report_summary as pub
    _funds(monkeypatch)
    _stub_build(monkeypatch, open_setups={"III.L": [{}]})

    def boom(*a, **k):
        raise RuntimeError("pool full")
    monkeypatch.setattr(pub, "latest_close_section", boom)
    out = pub.build(_snap())
    s = out["sections"]
    assert "latest_close" in s and s["latest_close"] is None
    assert s["open_setups"] == {"III.L": [{}]} and s["wk52"] == {"from": "_snapshot_52wk"}
    assert s["last_bar"] == {"III.L": "2026-10-31"} and s["mcap"] == {"AZN.L": 1.0}
    assert s["report_extras"] == {}, "report_extras' own fallback query failed the same way: published empty"
    assert out["generated_utc"] == GEN


# ── /api/records ────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def summary_file(tmp_path, monkeypatch):
    path = tmp_path / "report_summary.json"
    monkeypatch.setattr(server, "SUMMARY_FILE", str(path))
    monkeypatch.setattr(server, "_SUMMARY_DISABLED", False)
    server._SUMMARY_CACHE.update(mtime=None, data=None)

    def write(gen, **sections):
        built = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        path.write_text(json.dumps({"generated_utc": gen, "built_at": built, "sections": sections}))
        os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 1))
    yield write
    server._SUMMARY_CACHE.update(mtime=None, data=None)


def _route(monkeypatch, snap, auth):
    monkeypatch.setattr(server, "_load_snapshot", lambda: snap)
    monkeypatch.setattr(server._wu, "valid_tokens", lambda: {"tok"})
    monkeypatch.setattr(server._wu, "name_for_token", lambda t: "Alex" if t == "tok" else "")
    monkeypatch.setattr(trading_limits, "user_limits", lambda name: trading_limits.limit_defaults())
    monkeypatch.setattr(server, "_snapshot_52wk", lambda s: {"AZN.L": (10000.0, 14500.0)})
    monkeypatch.setattr(server, "_live_instrument_metrics", lambda s: {})
    monkeypatch.setattr(server, "_mcap_map", lambda: {})
    monkeypatch.setattr(server, "_snapshot_trigger_dates", lambda s: {})
    monkeypatch.setattr(server, "_open_trigger_setups", lambda s: {})
    monkeypatch.setattr("db_pool.get_db", lambda: pytest.fail("the route read the database"))
    headers = {"X-Auth": "tok"} if auth else {}
    body = server.app.test_client().get("/api/records", headers=headers).get_json()
    return {r["ticker"]: r for r in body["records"]}, body


def test_logged_in_price_now_is_the_scan_price_else_the_latest_close_else_blank(monkeypatch, summary_file):
    # AZN.L's section value differs from its scan price to prove which one wins.
    summary_file(GEN, latest_close={"AZN.L": 13000.0, "III.L": 2915.5})
    rows, body = _route(monkeypatch, _snap(), auth=True)
    assert body["limited"] is False
    assert rows["AZN.L"]["price_now"] == 13960.0, "the scan's own price wins"
    assert rows["III.L"]["price_now"] == 2915.5, "no scan price: the latest stored close"
    assert rows["ADM.L"]["price_now"] is None, "neither: blank"
    assert rows["AZN.L"]["current_price"] == 13960.0 and rows["III.L"]["current_price"] is None, "scan untouched"


def test_a_summary_for_another_snapshot_is_not_used(monkeypatch, summary_file):
    summary_file("2026-11-01T18:30:00+00:00", latest_close={"III.L": 2900.0})
    rows, _ = _route(monkeypatch, _snap(), auth=True)
    assert rows["AZN.L"]["price_now"] == 13960.0
    assert rows["III.L"]["price_now"] is None


def test_no_latest_close_section_still_shows_the_scan_price(monkeypatch, summary_file):
    summary_file(GEN, latest_close=None)
    rows, _ = _route(monkeypatch, _snap(), auth=True)
    assert rows["AZN.L"]["price_now"] == 13960.0 and rows["III.L"]["price_now"] is None


def test_logged_out_records_carry_no_price(monkeypatch, summary_file):
    summary_file(GEN, latest_close={"AZN.L": 13000.0, "III.L": 2915.5})
    rows, body = _route(monkeypatch, _snap(), auth=False)
    assert body["limited"] is True and len(rows) == 3
    for r in rows.values():
        assert "price_now" not in r and "current_price" not in r
        assert set(r) == set(server._PUBLIC_FIELDS) | {"wk52_low", "wk52_high"}


# ── Client: the rendered Instruments row ─────────────────────────────────────────────────────────────

NODE = __import__("shutil").which("node")


def _headers():
    from client_source import client_html
    html = client_html()
    head = re.search(r'<tbody id="instr-rows">', html)
    thead = html[html.rfind("<thead>", 0, head.start()): head.start()]
    return re.findall(r'<th[^>]*data-instrk="([a-z0-9_]+)"[^>]*>(.*?)</th>', thead)


def _paint(rows, limited=False):
    """Run the real paintInstruments in Node with the real f2 / ob / disp; return each row's <td> contents."""
    import test_js_behaviour as h
    from client_source import client_js
    js = client_js()
    consts = "\n".join(re.search(rf"^const {n}=.*$", js, re.M).group(0) for n in ("f2", "ob", "disp", "qcol"))
    pre = f"""
    let DATA_LOADED=true, DATA={json.dumps(rows)}, LIMITED={str(limited).lower()}, AUTH={str(not limited).lower()};
    let INSTR_LASTTRIG={{}}, instrSorts=[], OUT=null;
    const $=id=>(id==="instr-rows"?{{}}:null), LOC={{UK:"United Kingdom"}};
    const locName=v=>LOC[v]||v||"", nm40=n=>String(n??""), inSet=()=>true, _esc=s=>String(s);
    const barChart=()=>"", pieChart=()=>"", packViz=()=>{{}}, paintInstrFunnel=()=>{{}}, _stcol=()=>"";
    const instrRvolCell=()=>"rv", _tickCross=v=>"tc", volScoreCell=v=>"vs";
    function vtable(id, rs, render, cols, empty){{ OUT={{cols, html: rs.map((r,i)=>render(r,i))}}; }}
    {consts}
    """
    out = h.run_js(pre, h._fn_body(js, "paintInstruments"), "(paintInstruments(), OUT)")
    cells = [re.findall(r"<td[^>]*>(.*?)</td>", row, re.S) for row in out["html"]]
    return out["cols"], cells


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_price_now_renders_immediately_before_rr_in_header_order():
    heads = _headers()
    keys = [k for k, _ in heads]
    assert len(keys) == 16 and keys.index("price_now") + 1 == keys.index("rr")
    assert dict(heads)["price_now"] == "Price now"
    rows = [{"ticker": "AZN.L", "name": "AstraZeneca PLC", "location": "UK", "market": "FTSE 100",
             "sector": "Health", "direction": "BULL", "quality": 62, "wk52_low": 10000.0, "wk52_high": 14500.0,
             "price_now": 13960.0, "rr": 7.99},
            {"ticker": "III.L", "name": "3i Group", "location": "UK", "market": "FTSE 100", "price_now": 2915.456},
            {"ticker": "ADM.L", "name": "Admiral Group PLC", "location": "UK", "price_now": None},
            {"ticker": "ZERO", "name": "Boundary: a price of 0 is a price, not a blank", "price_now": 0}]
    cols, cells = _paint(rows)
    assert cols == 16 and all(len(c) == len(keys) for c in cells), "one cell per header"
    azn = dict(zip(keys, cells[0]))
    assert azn["price_now"] == "13960.00" and azn["rr"] == "8.0" and azn["wk52_high"] == "14500.00"
    assert azn["name"] == "AstraZeneca PLC" and azn["ticker"] == "<b>AZN</b>", "cells line up end to end"
    assert dict(zip(keys, cells[1]))["price_now"] == "2915.46"
    assert dict(zip(keys, cells[2]))["price_now"] == "—"
    assert dict(zip(keys, cells[3]))["price_now"] == "0.00", "the cell tests != null, as the server's is not None"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_price_now_is_obscured_for_a_logged_out_visitor():
    keys = [k for k, _ in _headers()]
    _, cells = _paint([{"ticker": "AZN.L", "name": "AstraZeneca PLC", "price_now": 13960.0}], limited=True)
    assert "•••" in dict(zip(keys, cells[0]))["price_now"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_price_now_sorts_numerically_with_blanks_last():
    import test_js_behaviour as h
    from client_source import client_js
    js = client_js()
    src = h._fn_body(js, "_instrSortValue") + "\n" + h._fn_body(js, "_instrCompare")
    rows = [{"ticker": "A", "price_now": None}, {"ticker": "B", "price_now": 95.5}, {"ticker": "C", "price_now": 1200.0}]
    order = h.run_js(f"let INSTR_LASTTRIG={{}}, instrSorts=[{{k:'price_now',d:-1}}]; const R={json.dumps(rows)};",
                     src, "R.slice().sort(_instrCompare).map(r=>r.ticker)")
    assert order == ["C", "B", "A"]
