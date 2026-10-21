"""The instrument detail's Yahoo Finance button (owner 2026-10-21: "add a button in detail of instrument to open
the yahoo finance page for this instrument").

Most tickers are Yahoo symbols already; 21 in the 2026-10-20 snapshot are IG-style names (SPX500, XAUUSD, USDJPY,
BTCUSD...) whose Yahoo symbol is in config.YAHOO_MAP -- the map every price fetch uses, served to the page so
it never keeps a second copy."""
import re

import pytest

from client_source import APP_JS
from config import YAHOO_MAP
from hvf_web import server
from test_js_behaviour import NODE, run_js


def test_the_endpoint_serves_config_yahoo_map_entries_that_differ():
    body = server.app.test_client().get("/api/yahoo_symbols").get_json()
    assert body["SPX500"] == "^GSPC" and body["USDJPY"] == "USDJPY=X" and body["BTCUSD"] == "BTC-USD"
    assert body == {t: y for t, y in YAHOO_MAP.items() if t != y}
    assert "RR.L" not in body, "a ticker that is its own symbol needs no entry"


def _yahoo_block():
    src = APP_JS.read_text(encoding="utf-8")
    m = re.search(r"\nlet YSYM=\{\};\n.*?\nconst yahooUrl=[^\n]*\n", src, re.S)
    assert m, "yahoo link helper not found in app.js"
    return src, m.group(0)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_link_uses_the_mapped_symbol_and_falls_back_to_the_ticker():
    _, block = _yahoo_block()
    stub = "globalThis.fetch=()=>new Promise(()=>{});"
    got = run_js(stub, block, '(YSYM={"SPX500":"^GSPC","USDJPY":"USDJPY=X"},'
                              '[yahooUrl("SPX500"),yahooUrl("USDJPY"),yahooUrl("RR.L"),yahooUrl("BT-A.L")])')
    assert got == ["https://finance.yahoo.com/quote/%5EGSPC", "https://finance.yahoo.com/quote/USDJPY%3DX",
                   "https://finance.yahoo.com/quote/RR.L", "https://finance.yahoo.com/quote/BT-A.L"]


def test_the_detail_panel_carries_the_button():
    src, _ = _yahoo_block()
    detail = src[src.index("function showDetail(t){"):]
    detail = detail[:detail.index("\nfunction ")]
    assert 'href="${yahooUrl(r.ticker)}" target="_blank" rel="noopener"' in detail
