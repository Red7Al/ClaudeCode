"""Scanner Report: Status sits five columns right of its old place (owner 2026-10-22: "On the scanner report
the triggered status can move 5 columns to the right") -- after To target, before Market. The header and the
row cell move together, and every header still lines up with its cell."""
import re


def _headers():
    html = open("hvf_web/index.html", encoding="utf-8").read()
    start = html.index('<th data-k="_fav"')
    head = html[start:html.index("In Pre-orders?</th>", start)]
    return [m.group(1) or m.group(2) for m in re.finditer(r'<th (?:data-k="([^"]+)"|style="cursor:default" title="([^"]+)")', head)]


def _cells():
    js = open("hvf_web/app.js", encoding="utf-8").read()
    start = js.index('$("rows").innerHTML=rows.map(r=>`<tr data-t=')
    return js[start:js.index("</tr>`).join(\"\")", start)]


def test_status_follows_to_target_and_precedes_market():
    h = _headers()
    assert h[h.index("tgt_months") + 1] == "status" and h[h.index("status") + 1] == "market"
    assert h[h.index("dist_entry") + 1] == "trig_date", "Triggered now follows Dist->Entry"


def test_the_status_cell_moved_with_its_header():
    c = _cells()
    assert c.index("r.dist_entry") < c.index("r.trig_date") < c.index("r.tgt_str") < c.index("r.status||''") < c.index("r.market")


def test_headers_and_cells_count_the_same():
    assert len(_headers()) == _cells().count("<td") + 1, "the star cell comes from _favCell()"
