"""The Scanner's RVOL, VWAP and ATR cells, EXECUTED rather than read.

WHY EXECUTED. Most client-side checks in this repository assert on source text, and CLAUDE.md records that
this is how a real bug once shipped green. These cells decide whether a column shows a value or an em dash,
so the thing worth asserting is the HTML they actually produce for a given row.

WHAT WAS WRONG (owner, 2026-09-26: blank RVOL / VWAP / ATR / Vol columns). r.above_vwap and
r.atr_expanding come from the server's _live_vwap_atr, which is scoped to has_signal rows -- MEASURED that
day, 415 of 1,773. Every other row got null, _tickCross(null) renders an em dash, and so most of both
columns read as "no data". The server was ALREADY sending a stored daily reading for all of them
(current_above_vwap / current_atr_expanding in api_records) and these two cells never looked at it, while
the RVOL cell immediately to their left had exactly that fallback -- added 2026-08-28 for the same
complaint ("Rows still has empty data e.g. RVOL!!!"). Two of the three columns were simply never finished.

MEASURED in instrument_metrics_daily for as_of 2026-09-25: above_vwap non-null on 1,739 of 1,773 rows,
atr_expanding on 1,773 of 1,773. The values existed; nothing read them.

A STORED READING IS NEVER PRESENTED AS THE SETUP'S OWN. Each fallback is wrapped with a "now" superscript
and its date, because a today's-state reading silently shown as the break bar's would be a worse defect
than the blank it replaces -- the whole point of ORDER_TIMING_AND_RVOL.md is that those are different
things.
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


def _region():
    """The contiguous cell-helper region of app.js, plus _tickCross, as executable source.

    Sliced by line rather than parsed: a brace-balance scanner has to model template literals and quoted
    semicolons to find the end of an arrow const, and getting that subtly wrong yields source that runs
    but is not the source under test.
    """
    lines = APP.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("const rvolCell="))
    end = next(i for i, l in enumerate(lines) if l.startswith("const _nowCell="))
    tick = next(l for l in lines if l.startswith("const _tickCross="))
    src = "\n".join(lines[start:end]) + "\n" + tick
    for name in ("rvolCell", "_currentReadingCell", "rvolScannerCell",
                 "vwapScannerCell", "atrScannerCell", "_tickCross"):
        assert f"const {name}=" in src, f"{name} missing from the extracted region"
    return src


SRC = _region()


def _render(fn, row):
    """Run one cell function in Node against `row` and return the HTML string it produces."""
    script = f"{SRC}\nconsole.log(JSON.stringify({fn}({json.dumps(row)})));"
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=30)
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout.strip() or "null")


DASH = '<span class="muted">—</span>'
TICK = "✓"
CROSS = "✗"


# ── the setup's own value always wins ──────────────────────────────────────────────────────────────────

def test_a_setups_own_vwap_reading_is_shown_plainly():
    """When the break bar has a value there is no fallback and no "now" label -- it IS the setup's."""
    out = _render("vwapScannerCell", {"above_vwap": True, "current_above_vwap": False,
                                      "current_metric_date": "2026-09-25"})
    assert TICK in out
    assert "now" not in out, "a break-bar reading must not be labelled as today's"


def test_a_setups_own_atr_reading_is_shown_plainly():
    out = _render("atrScannerCell", {"atr_expanding": False, "current_atr_expanding": True,
                                     "current_metric_date": "2026-09-25"})
    assert CROSS in out
    assert "now" not in out


def test_false_is_shown_as_a_cross_not_as_missing():
    """THE DISTINCTION THAT MAKES THE COLUMN USEFUL. False means "measured, and it is not above VWAP".
    Rendering that as an em dash would lose a real measurement."""
    assert CROSS in _render("vwapScannerCell", {"above_vwap": False})
    assert DASH not in _render("vwapScannerCell", {"above_vwap": False})


# ── the fallback: what was missing until 2026-09-26 ────────────────────────────────────────────────────

def test_vwap_falls_back_to_the_stored_daily_reading():
    """THE DEFECT. 1,358 of 1,773 rows have no has_signal VWAP, and the stored reading was being ignored."""
    out = _render("vwapScannerCell", {"above_vwap": None, "current_above_vwap": True,
                                      "current_metric_date": "2026-09-25"})
    assert TICK in out, "the stored reading must be shown, not discarded"
    assert "now" in out, "and it must be labelled as today's, not the break bar's"
    assert "2026-09-25" in out, "with its date, so the reader knows how current it is"


def test_atr_falls_back_to_the_stored_daily_reading():
    out = _render("atrScannerCell", {"atr_expanding": None, "current_atr_expanding": True,
                                     "current_metric_date": "2026-09-25"})
    assert TICK in out and "now" in out and "2026-09-25" in out


def test_a_stored_false_reading_falls_back_too():
    """A stored False is as much a measurement as a stored True, and must not be dropped as falsy."""
    out = _render("vwapScannerCell", {"above_vwap": None, "current_above_vwap": False,
                                      "current_metric_date": "2026-09-25"})
    assert CROSS in out and "now" in out


def test_the_fallback_survives_a_missing_date():
    """The date is a nicety; the value is the point. A row without one must still show its reading."""
    out = _render("atrScannerCell", {"atr_expanding": None, "current_atr_expanding": True})
    assert TICK in out and "now" in out


# ── with nothing to show, say nothing ─────────────────────────────────────────────────────────────────

def test_with_neither_reading_the_cell_is_an_em_dash():
    """An honest "no data" is still required when there genuinely is none -- inventing one would be worse."""
    assert _render("vwapScannerCell", {"above_vwap": None, "current_above_vwap": None}) == DASH
    assert _render("atrScannerCell", {"atr_expanding": None, "current_atr_expanding": None}) == DASH


def test_an_empty_row_does_not_raise():
    """Logged out, api_records strips these fields entirely, so every key is undefined."""
    assert _render("vwapScannerCell", {}) == DASH
    assert _render("atrScannerCell", {}) == DASH


# ── the RVOL cell this was modelled on must be unchanged ───────────────────────────────────────────────

def test_rvol_still_prefers_the_break_bar_value():
    out = _render("rvolScannerCell", {"rvol": 2.5, "current_rvol": 1.1})
    assert "2.5" in out and "now" not in out


def test_rvol_still_labels_its_fallback():
    out = _render("rvolScannerCell", {"rvol": None, "current_rvol": 1.1,
                                      "current_rvol_date": "2026-09-25"})
    assert "1.1" in out and "now" in out and "2026-09-25" in out


def test_rvol_with_nothing_is_still_an_em_dash():
    assert _render("rvolScannerCell", {"rvol": None, "current_rvol": None}) == DASH


def test_all_three_cells_share_one_fallback_presentation():
    """The wrapper was extracted rather than copied three times. If they ever diverge, the reader learns to
    read "now" as meaning three different things."""
    rv = _render("rvolScannerCell", {"rvol": None, "current_rvol": 1.1, "current_rvol_date": "2026-09-25"})
    vw = _render("vwapScannerCell", {"above_vwap": None, "current_above_vwap": True,
                                     "current_metric_date": "2026-09-25"})
    at = _render("atrScannerCell", {"atr_expanding": None, "current_atr_expanding": True,
                                    "current_metric_date": "2026-09-25"})
    for out in (rv, vw, at):
        assert 'vertical-align:super">now</span>' in out
        assert "not the trigger bar" in out
        assert "on 2026-09-25" in out
