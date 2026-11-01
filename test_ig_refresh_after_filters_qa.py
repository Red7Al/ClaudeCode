"""IG Account: after My Trading Filters are saved, "TX not meeting criteria" and "Orders not meeting criteria" show
the NEW verdicts without a page reload (owner CR, 2026-09-18). EXECUTED in Node, not read as source text: the real
saveLimits, saveStatus, renderIgAccount, showIgPanel, loadOrderFilterAudit, loadPositionFilterAudit,
paintOrderFilterAudit and paintPositionBreach from hvf_web/app.js run against a stubbed fetch whose audit answers
change when the filters are saved. Only presentation helpers (escaping, sorting, charts) and the account table
painter are stubbed.
"""

import textwrap

from test_js_behaviour import _bulk_controls_src, _extract, run_js_async

_FUNCS = ("saveStatus", "saveLimits", "renderIgAccount", "showIgPanel", "loadOrderFilterAudit",
          "paintOrderFilterAudit", "loadPositionFilterAudit", "paintPositionBreach", "_whyHead")

_PREAMBLE = textwrap.dedent("""
    global.AUTH = "token"; global.MY_LIMITS = {}; global.WIN = null;
    const EL = {};
    const mk = id => ({id, innerHTML: "", textContent: "", value: "", checked: false, disabled: false,
                       className: "", style: {}, classList: {toggle(){}, contains(){ return false; }}});
    global.$ = id => EL[id] || (EL[id] = mk(id));
    global.document = {querySelectorAll: () => []};
    const _esc = s => String(s ?? ""), disp = t => String(t || ""), nm40 = n => String(n || "");
    const _igDtag = d => String(d || ""), _igSz = v => String(v ?? "");
    const genSort = rows => rows, _sortArrows = () => {}, _breachViz = () => {};
    let IG_AUDIT = null, IGORD = [], igbSortK = "verdict", igbSortDir = -1;
    let IG_PANEL = "open", IG_AUTO = null, igaSortK = "closed_at", igaSortDir = -1;
    let IG_TXAUDIT = null, igtSortK = "verdict", igtSortDir = -1;
    let IG_POS = [], IG_ORD = [], IGCLOSED = null, _igClosedNote = "", _igNoCreds = false;
    let igOpenMonthInitialised = false; const IG_CLOSE_SELECTION = new Set();
    function updateIgCloseButton(){}
    function paintIgAccount(){ IGORD = IG_ORD; }          // the account tables; only IGORD matters to the audit
    function loadIgClosed(){}
    function loadAutoClosed(){}
    function paintIgTimeline(){}
    // The server: the audits answer against the filters as they are now. Saving them changes the answer.
    let FILTERS = "old"; const CALLS = [];
    const AUDIT = {
      old: {ord: [{ticker: "SAP.DE", verdict: "BREACH", breaches: ["Quality 44 < 45"], unknown: []}],
            tx: [{deal_id: "DIAAAA1", ticker: "MET", name: "MetLife", direction: "BUY", size: 0.04,
                  verdict: "BREACH", breaches: ["RVOL 1.2 < 1.7"], unknown: []}]},
      new: {ord: [{ticker: "SAP.DE", verdict: "OK", breaches: [], unknown: []}],
            tx: [{deal_id: "DIAAAA1", ticker: "MET", name: "MetLife", direction: "BUY", size: 0.04,
                  verdict: "OK", breaches: [], unknown: []}]}};
    const reply = (body, ok = true) => Promise.resolve({ok, status: ok ? 200 : 500, json: () => Promise.resolve(body)});
    global.fetch = (url, opt) => {
      CALLS.push(url);
      if (url === "/api/config") { if (SAVE_OK) FILTERS = "new"; return reply({}, SAVE_OK); }
      if (url === "/api/ig-account") return reply({positions: [{deal_id: "DIAAAA1", ticker: "MET"}],
                                                   orders: [{deal_id: "DIBBBB2", ticker: "SAP.DE", direction: "BUY", size: 0.04}]});
      if (url === "/api/order-filter-audit") return reply({rows: AUDIT[FILTERS].ord});
      if (url === "/api/position-filter-audit") return reply({rows: AUDIT[FILTERS].tx});
      return reply({});
    };
    let SAVE_OK = true;
    const settle = () => new Promise(r => setTimeout(r, 30));
    const n = url => CALLS.filter(u => u === url).length;
    const tx = () => EL["ig-txbreach-rows"].innerHTML, ord = () => EL["ig-breach-rows"].innerHTML;
""") + _bulk_controls_src()


def _run(body):
    src = "\n".join(_extract(f) for f in _FUNCS)
    return run_js_async(_PREAMBLE, src, "(async () => {" + textwrap.dedent(body) + "})()")


def test_refresh_after_saving_filters_shows_the_new_verdicts_on_both_panels():
    out = _run("""
        IG_PANEL = "txbreach";
        renderIgAccount(); await settle();
        const before = {tx: tx(), ord: ord()};
        saveLimits(); await settle();
        const cleared = {audit: IG_AUDIT, txaudit: IG_TXAUDIT};
        renderIgAccount({target: $("ig-refresh")}); await settle();
        return {before, cleared, after: {tx: tx(), ord: ord()},
                fetched: {ord: n("/api/order-filter-audit"), tx: n("/api/position-filter-audit")},
                button: $("ig-refresh").disabled};
    """)
    assert "RVOL 1.2 < 1.7" in out["before"]["tx"]
    assert "Quality 44" in out["before"]["ord"]
    assert out["cleared"] == {"audit": None, "txaudit": None}, "a saved filter makes both verdicts stale"
    assert out["fetched"] == {"ord": 2, "tx": 2}, "Refresh must re-ask both audits"
    assert "Every open position meets your criteria." in out["after"]["tx"] and "RVOL" not in out["after"]["tx"]
    assert "Every working order meets your settings." in out["after"]["ord"] and "Quality 44" not in out["after"]["ord"]
    assert out["button"] is False, "the Refresh button is given back"


def test_opening_the_tx_panel_after_a_save_re_reads_it_without_a_refresh():
    out = _run("""
        IG_PANEL = "open";
        renderIgAccount(); await settle();
        const lazy = n("/api/position-filter-audit");          // not read until its panel is opened
        showIgPanel("txbreach"); await settle();
        const before = tx();
        saveLimits(); await settle();
        showIgPanel("open"); showIgPanel("txbreach"); await settle();
        return {lazy, before, after: tx(), fetched: n("/api/position-filter-audit")};
    """)
    assert out["lazy"] == 0
    assert "RVOL 1.2" in out["before"]
    assert out["fetched"] == 2 and "Every open position meets your criteria." in out["after"]


def test_reopening_the_tx_panel_without_a_save_answers_from_the_cache():
    out = _run("""
        IG_PANEL = "open";
        renderIgAccount(); await settle();
        showIgPanel("txbreach"); await settle();
        showIgPanel("open"); showIgPanel("txbreach"); await settle();
        return n("/api/position-filter-audit");
    """)
    assert out == 1


def test_a_failed_save_leaves_the_verdicts_as_they_were():
    out = _run("""
        IG_PANEL = "txbreach";
        renderIgAccount(); await settle();
        SAVE_OK = false;
        saveLimits(); await settle();
        return {txaudit: IG_TXAUDIT !== null, audit: IG_AUDIT !== null, tx: tx()};
    """)
    assert out["txaudit"] and out["audit"], "nothing was saved, so nothing is stale"
    assert "RVOL 1.2" in out["tx"]
