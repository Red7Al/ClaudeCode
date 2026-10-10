"""IG Account -> "Open over time" (owner 2026-10-10): EXECUTED in Node, not read as source.

"a graph to show what is open over time and the current pnl amount next to the instrument name - color
code the open period: red for loss and green for profit". Fields as /api/ig-account returns them
(server.api_ig_account: name, direction, currency, opened 'YYYY-MM-DDTHH:MM:SS', profit)."""
import io
import json
import re
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
APP = io.open("hvf_web/app.js", encoding="utf-8").read()
HTML = io.open("hvf_web/index.html", encoding="utf-8").read()


def _src():
    a = APP.index("function igTimelineRows(")
    b = APP.index("\n}\n", APP.index("function paintIgTimeline(")) + 3
    helpers = [l for l in APP.split("\n") if l.startswith(("const _esc=", "const _igCurrency="))]
    return "\n".join(helpers) + "\n" + APP[a:b]


def _run(expr, pos):
    script = (_src() + "\nconst _el={innerHTML:''};const $=id=>id==='ig-timeline'?_el:null;\n"
              f"let IG_POS={json.dumps(pos)};\nconsole.log(JSON.stringify({expr}));")
    p = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


POS = [{"ticker": "ZS", "name": "Zscaler", "direction": "BUY", "currency": "USD", "opened": "2026-09-15T14:02:11", "profit": 412.5},
       {"ticker": "ZS", "name": "Zscaler", "direction": "BUY", "currency": "USD", "opened": "2026-09-20T09:00:00", "profit": -12.5},
       {"ticker": "AAF.L", "name": "Airtel Africa", "direction": "BUY", "currency": "GBP", "opened": "2026-08-21T08:01:00", "profit": -88.0},
       {"ticker": "BP.L", "name": "BP", "direction": "SELL", "currency": "GBP", "opened": "2026-10-01T08:00:00", "profit": None}]


def test_one_row_per_instrument_earliest_open_pnl_summed_oldest_first():
    rows = _run("igTimelineRows(IG_POS)", POS)
    assert [r["name"] for r in rows] == ["Airtel Africa", "Zscaler", "BP"]
    zs = rows[1]
    assert zs["opened"] == "2026-09-15" and zs["profit"] == 400.0 and zs["n"] == 2


def test_profit_is_green_loss_is_red_no_price_is_grey_and_the_pnl_sits_by_the_name():
    html = _run("(paintIgTimeline(), _el.innerHTML)", POS)
    fills = re.findall(r'<rect x="[\d.]+" y="[\d.]+" width="[\d.]+" height="12" rx="4" fill="([^"]+)"', html)
    assert fills == ["var(--bear)", "var(--bull)", "var(--muted)"]
    assert "+400.00" in html and "−88.00" in html, "signed P&L next to each name -- never colour alone"
    assert html.index("Airtel Africa") < html.index("−88.00") < html.index("Zscaler") < html.index("+400.00")
    assert "opened 2026-08-21" in html, "the hover says when it opened"


def test_no_positions_says_so():
    assert "No open positions" in _run("(paintIgTimeline(), _el.innerHTML)", [])


def test_the_tab_sits_right_of_the_other_ig_buttons():
    pills = HTML[HTML.index('<div id="ig-pills"'):HTML.index("</div>", HTML.index('<div id="ig-pills"'))]
    assert re.findall(r'data-igpanel="(\w+)"', pills)[-1] == "timeline"
    assert 'id="igpanel-timeline"' in HTML
