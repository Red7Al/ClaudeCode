# ======================================================================================================
# The Methodology page (P-42, user 2026-09-06).
#
# "please write a new page within in performance that explains how this analysis is done - I can then
# double check it with my quant resource".
#
# The risk with a page like this is not that it is wrong today. It is that it stays the same while the
# code changes, and an external reviewer has no way to tell. So the page READS the search's own grid
# rather than re-stating it, and these tests hold that join in place -- plus the honesty requirement that
# it names the method's weaknesses, because a page listing only strengths is not reviewable.
# ======================================================================================================

import json
import re
import shutil
import subprocess

import pytest

from client_source import APP_JS, BEST_SETTINGS_JS, client_html, client_js

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _render() -> str:
    """Render the page exactly as the browser does, from the real grid."""
    # BEST_GRID comes from the module by require, not by pasting the file in: the page must be rendered
    # from the SAME object the search consumes, which is the entire point of the join being tested.
    src = ("const BEST_GRID=require(" + json.dumps(str(BEST_SETTINGS_JS).replace("\\", "/")) + ").BEST_GRID;\n"
           + _method_fn() + "\nconsole.log(methodologyHTML());")
    return _run_node_file(src)


def _run_node_file(src: str) -> str:
    """Written to a file rather than passed with `node -e`: Windows caps a command line at 32k and this
    source is well past it, which surfaces as "filename or extension is too long" rather than a JS error."""
    import os
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".js")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(src)
    try:
        proc = subprocess.run([NODE, path], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
    finally:
        os.unlink(path)
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    return proc.stdout


def _method_fn() -> str:
    js = APP_JS.read_text(encoding="utf-8")
    i = js.index("function methodologyHTML(){")
    return js[i:js.index("\n}", i) + 2]


def _grid() -> dict:
    out = _run_node_file("const B=require(" + json.dumps(str(BEST_SETTINGS_JS).replace("\\", "/")) + ");"
                         "console.log(JSON.stringify(B.BEST_GRID));")
    return json.loads(out)


def test_the_search_and_the_page_share_one_copy_of_the_grid():
    """If the page re-typed these, it would eventually describe a search the code no longer performs and
    a reviewer could not tell. This is the whole reason BEST_GRID exists."""
    bs = BEST_SETTINGS_JS.read_text(encoding="utf-8")
    assert "const STAKES=BEST_GRID.STAKES" in bs, "the search must consume the exported grid"
    assert not re.search(r"const STAKES=\[", bs), "a second literal copy of the grid has reappeared"


# The grid table row each searched dimension must appear in. Checking the ROW, not the whole page, is
# deliberate: a bare `"12" in html` passes on "12.5px", "125 trades" and "1,095", so it would accept a
# page that had hard-coded the list while the search moved on. That mutation was tried and the weak
# version did not catch it.
_ROW_LABEL = {"RRS": "Minimum R:R", "QUALS": "Minimum Quality", "VSCORES": "Minimum VolumeScore",
              "RVOLS": "Minimum RVOL", "STAKES": "Position size (% of wallet)",
              "OPENS": "Max open positions"}


def _grid_row(html: str, label: str) -> str:
    m = re.search(r"<tr><td><b>" + re.escape(label) + r"</b></td><td>(.*?)</td></tr>", html, re.S)
    assert m, f"the page has no grid row for {label!r}"
    return m.group(1)


def test_every_searched_dimension_is_stated_on_the_page():
    html, grid = _render(), _grid()
    assert set(_ROW_LABEL) == set(grid), (
        f"the searched grid is {sorted(grid)} but the page checks {sorted(_ROW_LABEL)} -- "
        "a new dimension must be added to the page and to this test")
    for name, values in grid.items():
        # Strip the trailing note some rows carry (e.g. Quality's "(0 = no floor)") before comparing.
        row = re.sub(r"<span.*?</span>", "", _grid_row(html, _ROW_LABEL[name]), flags=re.S)
        shown = [t.strip() for t in row.split(",") if t.strip()]
        expected = [str(int(v)) if float(v).is_integer() else str(v) for v in values]
        assert shown[:len(expected)] == expected, (
            f"{name} is searched as {expected} but the page states {shown} -- "
            "the page must read the grid, never re-state it")


def test_the_search_size_is_derived_and_not_asserted():
    """A reviewer's first question is how large the search is, because that is what multiple-comparison
    risk scales with. Deriving it means it cannot be quoted stale."""
    grid = _grid()
    configs = 10 * len(grid["RRS"]) * len(grid["QUALS"]) * len(grid["VSCORES"]) * len(grid["RVOLS"]) * 4
    replays = configs * len(grid["STAKES"]) * len(grid["OPENS"])
    html = _render()
    assert f"{configs:,}" in html, f"the configuration count {configs:,} is not stated"
    assert f"{replays:,}" in html, f"the replay count {replays:,} is not stated"


def test_the_page_states_the_ranking_score_exactly_as_the_code_computes_it():
    bs = BEST_SETTINGS_JS.read_text(encoding="utf-8")
    assert "score:(x.ret/(x.dd+.02))*(.5+.5*cons)*Math.min(1,x.n/40)" in bs.replace(" ", ""), \
        "the score changed; the Methodology page must be updated with it"
    html = _render()
    for part in ("maxDrawdown + 0.02", "0.5 + 0.5", "fundedTrades / 40"):
        assert part in html, f"the page does not state {part!r}"


def test_the_page_names_the_methods_weaknesses():
    """A methodology page that lists only strengths cannot be reviewed, and review is its only purpose.
    These four are the ones a quant will ask about first."""
    html = _render().lower()
    for topic in ("in-sample", "multiple comparison", "survivorship", "costs are not modelled"):
        assert topic in html, f"the page does not disclose: {topic}"


def test_it_says_the_bands_select_by_funded_trades_and_not_by_sampling():
    """The requester challenged the word 'sample' directly on 2026-09-06 and was right: every
    configuration is replayed over the same full period."""
    html = _render()
    assert "never by sampling the data" in html
    assert "same full period" in html


def test_the_page_is_reachable_and_is_a_reading_page():
    html = client_html()
    assert 'data-pfpanel="method"' in html, "there is no way to open it"
    assert 'id="pf-panel-method"' in html, "the panel it opens does not exist"
    js = client_js()
    assert 'if(which==="method"&&!meth.innerHTML)meth.innerHTML=methodologyHTML();' in js, \
        "the panel is never filled"
