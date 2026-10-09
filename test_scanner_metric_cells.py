"""The Scanner Report's RVOL, VWAP and ATR columns -- AT TRIGGER and NOW -- EXECUTED rather than read.

Owner 2026-10-08: each measure is shown twice, at the trigger bar (what his filters judge, fixed once the
setup triggers) and on the latest bar (information only), in separate titled columns. Before that one cell
showed the trigger value and fell back to today's reading with a "now" mark when it was missing; the
dedicated now column replaces that fallback, so a trigger column can never show a borrowed value.

WHY EXECUTED. Most client-side checks here assert on source text, which is how a real bug once shipped
green. The cell helpers decide between a value and an em dash, so the HTML they produce is asserted.
"""

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


def _helper(name):
    line = next(l for l in APP.split("\n") if l.startswith(f"const {name}="))
    start = APP.index(line)
    end = APP.index(";\n", start) + 2
    return APP[start:end]


SRC = _helper("rvolCell") + "\n" + _helper("_tickCross")


def _render(expr):
    script = f"{SRC}\nconsole.log(JSON.stringify({expr}));"
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=30)
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout.strip() or "null")


def _scanner_row():
    j = APP.index("rvolCell(r.rvol)")
    return APP[APP.rindex("`<tr", 0, j):APP.index("</tr>", j)]


TRIGGER_THEN_NOW = [("rvolCell(r.rvol)", "rvolCell(r.current_rvol)"),
                    ("_tickCross(r.above_vwap)", "_tickCross(r.current_above_vwap)"),
                    ("_tickCross(r.atr_expanding)", "_tickCross(r.current_atr_expanding)")]


def test_each_measure_has_a_trigger_cell_then_a_now_cell():
    row = _scanner_row()
    pos = [row.index(c) for pair in TRIGGER_THEN_NOW for c in pair]
    assert pos == sorted(pos), "cells must run RVOL trigger, RVOL now, VWAP trigger, VWAP now, ATR trigger, ATR now"


def test_the_headings_say_trigger_and_now_in_the_same_order():
    i = HTML.index('<th data-k="rvol"')
    keys = re.findall(r'<th data-k="([^"]+)"[^>]*>[^<]*<span[^>]*>(trigger|now)</span>', HTML[i:i + 3000])[:6]
    assert keys == [("rvol", "trigger"), ("current_rvol", "now"), ("above_vwap", "trigger"),
                    ("current_above_vwap", "now"), ("atr_expanding", "trigger"),
                    ("current_atr_expanding", "now")]


def test_no_trigger_cell_falls_back_to_todays_value():
    row = _scanner_row()
    for trig, now in TRIGGER_THEN_NOW:
        cell = row[row.index(trig) - 20: row.index(trig) + len(trig)]
        assert "current_" not in cell, f"{trig} must not borrow today's value"


def test_false_is_a_cross_not_missing():
    """False means measured and not above VWAP / not expanding; an em dash would lose that measurement."""
    assert "✗" in _render("_tickCross(false)") and "—" not in _render("_tickCross(false)")


def test_never_measured_is_an_em_dash():
    assert "—" in _render("_tickCross(null)") and "—" in _render("rvolCell(null)")


def test_a_trigger_rvol_renders_its_value():
    assert "2.5" in _render("rvolCell(2.5)")
