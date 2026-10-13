"""IG Account -> "Open over time" (owner 2026-10-10/11): EXECUTED in Node, not read as source.

"a graph to show what is open over time and the current pnl amount next to the instrument name - color
code the open period: red for loss and green for profit"; "should show history of those closed also -
with date filter to the right of screen on same row as legend"; "date filter should be "Last 7 days",
"Last month", "This month" etc"; "Also allow sort by figure e.g. -0.98". Fields as the routes return them:
/api/ig-account positions (name, direction, currency, opened, profit) and /api/ig-closed trades
(ig_shim.get_closed_trades: date, opened, instrument, direction, pnl, currency)."""
import io
import json
import re
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
APP = io.open("hvf_web/app.js", encoding="utf-8").read().replace("\r\n", "\n")
HTML = io.open("hvf_web/index.html", encoding="utf-8").read()
node_only = pytest.mark.skipif(NODE is None, reason="node is not installed")
NOW = "2026-10-11T12:00:00"     # the clock is pinned, so the periods do not depend on the day the suite runs


def _fn(name):
    a = APP.index(f"function {name}(")
    return APP[a:APP.index("\n}\n", a) + 3]


def _src():
    helpers = [l for l in APP.split("\n") if l.startswith(("const _esc=", "const _igCurrency=", "const _ymdLocal="))]
    return "\n".join(helpers) + "\n" + _fn("igtlRange") + _fn("igtlStartBalance") + _fn("igTimelineRows") + _fn("paintIgTimeline")


def _run(expr, pos, closed=None, period="12m", sort="opened", note="", small="", bal=None):
    clock = ("const _RD=Date;Date=class extends _RD{constructor(...a){super(...(a.length?a:[%s]))}"
             "static now(){return _RD.parse(%s)}};\n") % (json.dumps(NOW), json.dumps(NOW))
    inputs = {"igtl-period": {"value": period}, "igtl-sort": {"value": sort}, "igtl-small": {"value": small}}
    script = (clock + _src() + "\nconst _el={innerHTML:''};const _in=" + json.dumps(inputs) + ";"
              "const $=id=>id==='ig-timeline'?_el:(_in[id]||null);\n"
              f"let IG_POS={json.dumps(pos)}; let IGCLOSED={json.dumps(closed)}; let _igClosedNote={json.dumps(note)}; let IGBAL={json.dumps(bal)};\n"
              f"console.log(JSON.stringify({expr}));")
    p = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _paint(*a, **k):
    return _run("(paintIgTimeline(), _el.innerHTML)", *a, **k)


def _fills(html):
    return re.findall(r'height="12" rx="4" fill="([^"]+)"(?: opacity="([^"]+)")?', html)


def _order(html):
    return re.findall(r'font-size="12" fill="var\(--(?:fg|muted)\)">([^<]+)</text>', html)


POS = [{"ticker": "ZS", "name": "Zscaler", "direction": "BUY", "currency": "USD", "opened": "2026-09-15T14:02:11", "profit": 412.5},
       {"ticker": "ZS", "name": "Zscaler", "direction": "BUY", "currency": "USD", "opened": "2026-09-20T09:00:00", "profit": -12.5},
       {"ticker": "AAF.L", "name": "Airtel Africa", "direction": "BUY", "currency": "GBP", "opened": "2026-08-21T08:01:00", "profit": -88.0},
       {"ticker": "BP.L", "name": "BP", "direction": "SELL", "currency": "GBP", "opened": "2026-10-01T08:00:00", "profit": None}]
# Shaped as IG returned them on 2026-10-11 (open 2026-09-01T13:31:32 / close 2026-10-08T16:56:13).
CLOSED = [{"date": "2026-10-08T16:56:13", "opened": "2026-09-01T13:31:32", "instrument": "Rio Tinto", "direction": "BUY", "pnl": 120.0, "currency": "GBP"},
          {"date": "2026-10-08T16:56:14", "opened": "2026-09-01T13:31:32", "instrument": "Rio Tinto", "direction": "BUY", "pnl": 30.0, "currency": "GBP"},
          {"date": "2026-07-10T10:00:00", "opened": "2026-07-01T10:00:00", "instrument": "Old Co", "direction": "SELL", "pnl": -40.0, "currency": "GBP"}]


@node_only
def test_one_row_per_instrument_earliest_open_pnl_summed_oldest_first():
    rows = _run("igTimelineRows(IG_POS,null)", POS)
    assert [r["name"] for r in rows] == ["Airtel Africa", "Zscaler", "BP"]
    zs = rows[1]
    assert zs["opened"] == "2026-09-15" and zs["profit"] == 400.0 and zs["n"] == 2 and zs["closed"] is None


@node_only
def test_profit_is_green_loss_is_red_no_price_is_grey_and_the_pnl_sits_by_the_name():
    html = _paint(POS, [])
    assert [f for f, _ in _fills(html)] == ["var(--bear)", "var(--bull)", "var(--muted)"]
    assert "+400.00" in html and "−88.00" in html, "signed P&L next to each name -- never colour alone"
    assert html.index("Airtel Africa") < html.index("−88.00") < html.index("Zscaler") < html.index("+400.00")
    assert "opened 2026-08-21" in html


@node_only
def test_closed_trades_show_opened_to_closed_with_realised_pnl_and_paler():
    rows = _run("igTimelineRows([],IGCLOSED)", [], CLOSED)
    rio = [r for r in rows if r["name"] == "Rio Tinto"]
    assert len(rio) == 1 and rio[0]["opened"] == "2026-09-01" and rio[0]["closed"] == "2026-10-08"
    assert rio[0]["profit"] == 150.0 and rio[0]["n"] == 2, "partial closes of one deal combine"
    html = _paint(POS, CLOSED)
    assert ("var(--bear)", ".45") in _fills(html) and ("var(--bull)", ".45") in _fills(html)
    assert "Rio Tinto (closed)" in html and "closed 2026-10-08" in html and "realised P&amp;L +150.00" in html


@node_only
def test_named_periods_are_the_right_dates():
    r = _run("['7d','30d','tm','lm','3m','12m'].map(v=>igtlRange(v))", [])
    assert r == [{"from": "2026-10-04", "to": None}, {"from": "2026-09-11", "to": None},
                 {"from": "2026-10-01", "to": None}, {"from": "2026-09-01", "to": "2026-09-30"},
                 {"from": "2026-07-11", "to": None}, {"from": "2025-10-11", "to": None}]


@node_only
def test_last_month_keeps_rows_whose_open_period_overlaps_september():
    html = _paint(POS, CLOSED, period="lm")
    assert "Old Co" not in html, "closed in July -- outside the period"
    assert "Rio Tinto" in html and "Airtel Africa" in html and "Zscaler" in html
    assert ">BP<" not in html, "opened 1 Oct -- after the period"


@node_only
def test_sort_by_the_pnl_figure_no_figure_last():
    assert _order(_paint(POS, CLOSED, sort="pnl_desc")) == ["Zscaler", "Rio Tinto (closed)", "Old Co (closed)", "Airtel Africa", "BP"]
    assert _order(_paint(POS, CLOSED, sort="pnl_asc")) == ["Airtel Africa", "Old Co (closed)", "Rio Tinto (closed)", "Zscaler", "BP"]
    assert _order(_paint(POS, CLOSED)) == ["Old Co (closed)", "Airtel Africa", "Rio Tinto (closed)", "Zscaler", "BP"]


@node_only
def test_the_routes_note_shows_so_a_fallback_never_reads_as_no_trades():
    # Measured 2026-10-11: IG answered 403 on the history, /api/ig-closed fell back to the ledger (last trade
    # 2026-08-06) with a note, and the tab showed "0 closed" for the last 30 days with no reason given.
    note = "IG returned no closed trades; showing the application ledger fallback."
    assert note in _paint(POS, CLOSED, period="30d", note=note)
    assert note not in _paint(POS, CLOSED, period="30d")


@node_only
def test_no_positions_says_so():
    assert "No positions in this period" in _paint([], [])


def test_the_tab_sits_right_of_the_other_ig_buttons():
    pills = HTML[HTML.index('<div id="ig-pills"'):HTML.index("</div>", HTML.index('<div id="ig-pills"'))]
    assert re.findall(r'data-igpanel="(\w+)"', pills)[-1] == "timeline"


def test_legend_sort_and_period_share_one_row_controls_on_the_right():
    panel = HTML[HTML.index('<div id="igpanel-timeline"'):HTML.index('<div id="ig-timeline"')]
    row = panel[panel.index('<div style="display:flex'):]
    for label in ("In profit", "At a loss", "No current price", "Last 7 days", "Last month", "This month"):
        assert label in row
    assert row.index("No current price") < row.index("flex:1") < row.index('id="igtl-sort"') < row.index('id="igtl-period"')
    assert 'value="30d" selected' in row and 'value="opened" selected' in row


def test_closed_trades_carry_igs_open_date(monkeypatch):
    import ig_shim
    monkeypatch.setattr(ig_shim, "get_transactions", lambda frm: [
        {"kind": "TRADE", "date": "2026-10-08T16:56:13", "open_date": "2026-09-01T13:31:32", "instrument": "Rio Tinto",
         "open_level": 50.0, "close_level": 55.0, "pnl": 120.0, "size": 1, "currency": "GBP"}])
    monkeypatch.setattr(ig_shim.session, "get", lambda *a, **k: {})
    (t,) = ig_shim.get_closed_trades("2026-07-01T00:00:00")
    assert t["opened"] == "2026-09-01T13:31:32" and t["date"] == "2026-10-08T16:56:13"


@node_only
def test_small_figures_within_plus_or_minus_the_value_are_hidden():
    # Owner 2026-10-12: "Allow filter of small figures in "Open over time" e.g. +/- 1.25".
    pos = [{"ticker": t, "name": t, "direction": "BUY", "currency": "GBP", "opened": "2026-10-01T08:00:00", "profit": p}
           for t, p in (("EDGEUP", 1.25), ("EDGEDN", -1.25), ("SMALL", -0.98), ("JUSTOVER", 1.26), ("BIGLOSS", -40.0), ("NOPRICE", None))]
    html = _paint(pos, [], small="1.25")
    names = _order(html)
    assert sorted(names) == ["BIGLOSS", "JUSTOVER", "NOPRICE"], names
    assert "3 hidden as within ±1.25" in html
    assert sorted(_order(_paint(pos, []))) == sorted(t for t, *_ in [(p["ticker"],) for p in pos]), "empty = show all"


def test_the_small_figure_box_sits_on_the_controls_row():
    panel = HTML[HTML.index('<div id="igpanel-timeline"'):HTML.index('<div id="ig-timeline"')]
    row = panel[panel.index('<div style="display:flex'):]
    assert row.index("flex:1") < row.index('id="igtl-small"') < row.index('id="igtl-sort"')



def _totals(html):
    return re.findall(r'<g class="igtl-total"><text[^>]*>([^<]+)</text><text[^>]*>([^<]+) <tspan[^>]*>([^<]+)</tspan>', html)


@node_only
def test_a_total_sits_under_the_figures_one_per_currency():
    # Owner 2026-10-12: "at bottom of figures on "open over time" add a total". GBP and USD are never added.
    html = _paint(POS, CLOSED)
    assert _totals(html) == [("Total (GBP)", "+22.00", "GBP"), ("Total (USD)", "+400.00", "USD")]
    assert html.index("Old Co") < html.index("igtl-total"), "the total is the last thing in the chart"


@node_only
def test_the_total_counts_only_the_rows_shown():
    gbp = [p for p in POS if p["currency"] == "GBP"]
    bp = [p for p in POS if p["name"] == "BP"]
    assert _totals(_paint(bp, [], period="30d")) == [], "BP has no figure -- nothing to total"
    assert _totals(_paint(gbp, CLOSED, period="30d")) == [("Total", "+62.00", "GBP")], "one currency: plain 'Total'"
    assert _totals(_paint(gbp, CLOSED, small="100")) == [("Total", "+150.00", "GBP")], "hidden small figures are left out"


def _pcts(html):
    return re.findall(r'<text class="igtl-pct"[^>]*>([^<]+)<tspan[^>]*>([^<]+)</tspan>', html)


# Shaped as /api/ig-closed "balance": balance now, the history's first day, and each day's net of ALL
# transactions (trades, charges, deposits). Clock pinned at 2026-10-11; "Last 30 days" starts 2026-09-11.
BAL = {"now": 588.90, "currency": "GBP", "from": "2025-10-11",
       "daily": {"2026-09-10": 500.0, "2026-09-11": -10.0, "2026-10-08": 150.0, "2026-10-09": -6.0}}


@node_only
def test_the_total_carries_its_percentage_of_the_balance_at_the_start_of_the_period():
    # Owner 2026-10-13: "along with total (add %age gain\loss)", "against full value at begining of reporting
    # period". Balance at the start of 2026-09-11 = 588.90 - (-10 + 150 - 6) = 454.90; GBP total +62.00.
    gbp = [p for p in POS if p["currency"] == "GBP"]
    html = _paint(gbp, CLOSED, period="30d", bal=BAL)
    assert _totals(html) == [("Total", "+62.00", "GBP")]
    assert _pcts(html) == [("+13.63%", " of account balance 454.90 GBP on 2026-09-11")]


@node_only
def test_no_percentage_when_the_start_balance_is_unknown_or_zero():
    gbp = [p for p in POS if p["currency"] == "GBP"]
    assert _pcts(_paint(gbp, CLOSED, period="30d")) == [], "no balance from the route -- no %"
    assert _pcts(_paint(gbp, CLOSED, period="30d", bal=dict(BAL, **{"from": "2026-09-20"}))) == [], \
        "a start before the history returned would miss transactions"
    zero = dict(BAL, now=134.0)                 # 134 - 134 = 0 at the start: no money in the account yet
    assert _pcts(_paint(gbp, CLOSED, period="30d", bal=zero)) == [("no %", " — account balance 0.00 GBP on 2026-09-11")]
    usd = [p for p in POS if p["currency"] == "USD"]
    assert _pcts(_paint(usd, [], period="30d", bal=BAL)) == [], "not in the account's own currency"


def test_the_closed_route_sends_the_balance_history_and_asks_ig_for_transactions_once(monkeypatch):
    import contextlib
    import ig_shim
    from hvf_web import server
    calls = []
    tx = [{"kind": "TRADE", "date": "2026-10-08T16:56:13", "open_date": "2026-09-01T13:31:32", "instrument": "Rio Tinto",
           "open_level": 50.0, "close_level": 55.0, "pnl": 120.0, "size": 1, "currency": "GBP"},
          {"kind": "CHARGE", "date": "2026-10-08T22:00:00", "open_date": "2026-10-08T22:00:00", "instrument": "Funding",
           "open_level": None, "close_level": None, "pnl": -0.4, "size": None, "currency": "GBP"}]
    monkeypatch.setattr(server._wu, "name_for_token", lambda t: "Alex")
    monkeypatch.setattr(ig_shim, "session_for", lambda n: object())
    monkeypatch.setattr(ig_shim, "acting_session", lambda n: contextlib.nullcontext())
    monkeypatch.setattr(ig_shim, "get_transactions", lambda frm, *a, **k: calls.append(frm) or tx)
    monkeypatch.setattr(ig_shim.session, "get", lambda *a, **k: {})
    monkeypatch.setattr(ig_shim, "get_account_info",
                        lambda: {"account_id": "X", "account_name": "n", "balance": 588.9, "currency": "GBP"})
    monkeypatch.setattr(server, "_mcap_map", lambda: {})
    j = server.app.test_client().get("/api/ig-closed", headers={"X-Auth": "t"}).get_json()
    assert len(calls) == 1, "IG history is fetched once per load"
    b = j["balance"]
    assert b["now"] == 588.9 and b["currency"] == "GBP" and b["daily"] == {"2026-10-08": 119.6}
    assert b["from"] == calls[0][:10]


def test_account_info_carries_the_balance(monkeypatch):
    import ig_shim
    monkeypatch.setattr(ig_shim.session, "get", lambda *a, **k: {"accounts": [
        {"accountId": "A1", "accountName": "Spread bet", "preferred": True, "currency": "GBP",
         "balance": {"balance": 588.9, "available": 588.9, "profitLoss": 0.0, "deposit": 0.0}}]})
    monkeypatch.setattr(ig_shim.session, "_account_id", "A1", raising=False)
    ai = ig_shim.get_account_info()
    assert ai["balance"] == 588.9 and ai["currency"] == "GBP" and ai["account_id"] == "A1"
