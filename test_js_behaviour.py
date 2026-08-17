# ======================================================================================================
# Executing client JavaScript, instead of reading it.
#
# WHY THIS EXISTS. The suite extracts functions from hvf_web/index.html in 19 places and asserts on their
# SOURCE TEXT. That catches a deleted line. It cannot catch wrong behaviour, and on 2026-08-17 two bugs
# walked straight through a fully green suite:
#
#   * Pre-orders to my IG showed no RVOL. The server was returning it correctly on 199 of 200 rows; the
#     browser then overwrote every value with `d?.rvol ?? null` from a snapshot that only carries rvol
#     for instruments triggering TODAY. Correct data, discarded on arrival. A source-text assertion could
#     not see it, because the line was present and looked reasonable.
#   * The snapshot date "moved" when the buttons moved. Source said the element was untouched -- and it
#     was. What changed was where it RENDERED.
#
# The gap is that no test ever ran the code. This harness executes an extracted function in Node against
# supplied inputs and returns what it actually produced, so a test can assert on the RESULT.
#
# No new dependency: Node is already required by test_performance_inline_javascript_parses, and the
# functions worth testing this way are the data-shaping ones, which need stubs rather than a real DOM.
# ======================================================================================================
import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

INDEX = Path(__file__).parent / "hvf_web" / "index.html"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _extract(name: str) -> str:
    """Raw source of a top-level `function NAME(...){...}` block, to the next top-level declaration."""
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(rf"\n(?:async )?function {re.escape(name)}\([^)]*\)\{{", html)
    assert m, f"function {name}(...) not found in hvf_web/index.html"
    nxt = re.search(r"\n(?:async )?function ", html[m.end():])
    return html[m.start(): m.end() + nxt.start() if nxt else len(html)]


def run_js(preamble: str, source: str, call: str):
    """Execute `source` in Node with `preamble` providing stubs, then evaluate `call` and return it.

    The result comes back as JSON on stdout, so anything the function produces can be asserted on
    directly rather than pattern-matched in its text.
    """
    script = f"{preamble}\n{source}\nconsole.log(JSON.stringify({call}));"
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout.strip() or "null")


# ------------------------------------------------------------------------------------------------------
# The RVOL clobber, as a behavioural test.
#
# paintOrderOps touches too much DOM to run whole, so the enrichment loop is lifted verbatim from it and
# executed. The test fails if that loop is ever restored to overwriting the server's values -- which is
# what shipped, and what no source-text assertion noticed.
# ------------------------------------------------------------------------------------------------------
_ENRICH = re.compile(r"rows\.forEach\(r=>\{const d=_ooRec\(r\.ticker\).*?\}\);", re.S)


def _enrichment_loop() -> str:
    """The loop PLUS the _ooRec lookup it depends on, both lifted from the real function.

    _ooRec is pulled from source rather than stubbed: it decides which snapshot record a row matches,
    and matching the wrong one is precisely how a stale value would get in.
    """
    src = _extract("paintOrderOps")
    rec = re.search(r"const _ooRec=[^\n]+", src)
    assert rec, "the _ooRec snapshot lookup could not be located - has paintOrderOps been restructured?"
    m = _ENRICH.search(src)
    assert m, "the paintOrderOps enrichment loop could not be located - has it been restructured?"
    return f"{rec.group(0)}\n{m.group(0)}"


def test_order_ops_enrichment_keeps_the_servers_values():
    """A historical order: the server resolved RVOL from the trigger that caused it, and today's
    snapshot has the instrument but no rvol (it did not trigger today). The server value must survive.

    This is the exact shape of the reported bug: IWG.L, server said 1.10, the screen showed nothing.
    """
    preamble = textwrap.dedent("""
        const DATA = [{ticker: "IWG.L", name: "International Workplace Group PLC",
                       current_price: 180, rvol: null, volume_score: null, rr: null, quality: null}];
        const disp = t => t;
        let rows = [{ticker: "IWG.L", entry: 190, rvol: 1.10, volume_score: 7,
                     rr: 13.81, quality: 62}];
    """)
    out = run_js(preamble, _enrichment_loop(), "rows[0]")

    assert out["rvol"] == 1.10, (
        "the browser overwrote the server's point-in-time RVOL with today's snapshot -- the reported bug"
    )
    assert out["volume_score"] == 7
    assert out["rr"] == 13.81
    assert out["quality"] == 62
    assert out["name"] == "International Workplace Group PLC", "name still comes from the snapshot"


def test_order_ops_enrichment_falls_back_to_the_snapshot():
    """The snapshot is still the fallback: where the server returned nothing, use it."""
    preamble = textwrap.dedent("""
        const DATA = [{ticker: "AAA", name: "Alpha", current_price: 100,
                       rvol: 2.5, volume_score: 9, rr: 4.0, quality: 80}];
        const disp = t => t;
        let rows = [{ticker: "AAA", entry: 105, rvol: null, volume_score: null,
                     rr: null, quality: null}];
    """)
    out = run_js(preamble, _enrichment_loop(), "rows[0]")
    assert (out["rvol"], out["volume_score"], out["rr"], out["quality"]) == (2.5, 9, 4.0, 80)


def test_order_ops_enrichment_computes_distance_to_entry():
    """dist_pct is derived, not passed through -- so it is worth executing rather than reading."""
    preamble = textwrap.dedent("""
        const DATA = [{ticker: "AAA", name: "Alpha", current_price: 100}];
        const disp = t => t;
        let rows = [{ticker: "AAA", entry: 110, rvol: null, volume_score: null, rr: null, quality: null}];
    """)
    out = run_js(preamble, _enrichment_loop(), "rows[0]")
    assert out["dist_pct"] == 10.0, "entry 110 against a live 100 is +10%"


def test_valid_email_rejects_what_it_should():
    """validEmail is pure, so it can simply be run. Executing it beats asserting on the regex text."""
    src = _extract("validEmail")
    cases = ["a@A", "a@b", "no-at-sign", "trailing@dot.", "@nolocal.com", "spaces in@x.com", ""]
    for bad in cases:
        assert run_js("", src, f"validEmail({json.dumps(bad)})") is False, f"{bad!r} should be rejected"
    for good in ["name@example.com", "first.last+tag@sub.domain.co.uk"]:
        assert run_js("", src, f"validEmail({json.dumps(good)})") is True, f"{good!r} should be accepted"


def test_the_harness_would_have_caught_the_original_bug():
    """A guard that has never failed proves nothing. This runs the line that actually shipped.

    The old enrichment was `r.rvol = d?.rvol ?? null`, unconditional. Executed against a snapshot record
    that exists but carries no rvol -- a historical order whose instrument did not trigger today -- it
    returns null and destroys the server's value. If this harness cannot tell that apart from the fixed
    version, it is not testing anything.
    """
    buggy = ("const _ooRec=t=>DATA.find(x=>x.ticker===t);\n"
             "rows.forEach(r=>{const d=_ooRec(r.ticker),p=d&&d.current_price;"
             "r.name=d?.name||'';r.rvol=d?.rvol??null;r.volume_score=d?.volume_score??null;"
             "r.rr=d?.rr??null;r.quality=d?.quality??null;"
             "r.dist_pct=(r.entry!=null&&p)?+(((r.entry-p)/p)*100).toFixed(2):null;});")
    preamble = textwrap.dedent("""
        const DATA = [{ticker: "IWG.L", name: "IWG", current_price: 180,
                       rvol: null, volume_score: null, rr: null, quality: null}];
        const disp = t => t;
        let rows = [{ticker: "IWG.L", entry: 190, rvol: 1.10, volume_score: 7, rr: 13.81, quality: 62}];
    """)

    was = run_js(preamble, buggy, "rows[0]")
    assert was["rvol"] is None, "the reconstruction must reproduce the bug, or this test proves nothing"

    now = run_js(preamble, _enrichment_loop(), "rows[0]")
    assert now["rvol"] == 1.10
    assert was["rvol"] != now["rvol"], "THE HARNESS CANNOT DISTINGUISH THE BUG FROM THE FIX"
