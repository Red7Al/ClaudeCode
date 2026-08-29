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

# The JS moved to hvf_web/app.js on 2026-08-23; client_source() returns markup+script together so
# these assertions keep meaning what they meant when it was one file.
from client_source import client_js, client_source
INDEX = type("_Src", (), {"read_text": staticmethod(lambda **kw: client_source())})()
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
    # encoding is explicit: without it `text=True` decodes with the locale codec, which on Windows is
    # cp1252, and ANY non-ASCII the client returns kills the harness with UnicodeDecodeError rather than
    # failing the assertion. The client is full of such characters -- the loading spinner, the lock icon,
    # currency symbols -- so this silently limited what could be executed here at all (found 2026-08-28
    # testing the logged-out Best settings history panel, whose placeholder is an hourglass emoji).
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=30)
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


def test_backtest_summary_requires_exact_transaction_evidence_reconciliation():
    source = _extract("pfLedgerReconciliation")
    preamble = ""
    rows = "[{perf:10,_stake:100,_net:10},{perf:-5,_stake:100,_net:-5},{perf:2,_stake:null,_net:null}]"
    ok = run_js(preamble, source, f"pfLedgerReconciliation({rows},{{wallet:1000,endWallet:1005,taken:2,skipped:1,netTotal:5}})")
    bad = run_js(preamble, source, f"pfLedgerReconciliation({rows},{{wallet:1000,endWallet:1006,taken:2,skipped:1,netTotal:5}})")
    assert ok["ok"] is True
    assert bad["ok"] is False


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


# ------------------------------------------------------------------------------------------------------
# Tab Visibility ordering (user 2026-08-22: "sort the tabs in visibility section in the order they are on
# the screen - I was confused by the order").
#
# Executed rather than pattern-matched, because the failure that matters is a tab going MISSING from the
# panel: a chip that is not rendered is never submitted by saveTabVis(), so that tab silently keeps
# whatever hidden/shown state it was last saved with and the user has no control left to change it.
# ------------------------------------------------------------------------------------------------------

def _const(name: str) -> str:
    """Raw source of a top-level `const NAME=...;` declaration (single statement, ends at the newline)."""
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(rf"\nconst {re.escape(name)}=.*?;\n", html, re.S)
    assert m, f"const {name}= not found in hvf_web/index.html"
    return m.group(0)


def _tab_order_source() -> str:
    return _const("TABS") + _const("SCREEN_TAB_ORDER") + _const("screenTabOrder")


def test_screen_tab_order_loses_no_tab():
    """Every tab in TABS must still get a chip — order may change, membership may not."""
    src = _tab_order_source()
    ordered = run_js("", src, "screenTabOrder()")
    tabs = run_js("", src, "TABS")

    assert sorted(ordered) == sorted(tabs), "screenTabOrder() must be a permutation of TABS"
    assert len(ordered) == len(set(ordered)), "a duplicated chip would submit the same tab twice"


def test_screen_tab_order_matches_the_navigation_bar():
    """The point of the change: the panel reads in the order the tab bar renders."""
    ordered = run_js("", _tab_order_source(), "screenTabOrder()")

    assert ordered[:6] == ["welcome", "whatwedo", "intro", "risk", "appendix", "performance"]
    # Grouped children follow their parent's position, expanded in place.
    assert ordered.index("config") < ordered.index("users"), "Settings group precedes Operations group"
    assert ordered.index("scanner") < ordered.index("config"), "top-level tabs precede the grouped ones"


def test_a_tab_missing_from_the_screen_order_still_gets_a_chip():
    """THE REGRESSION GUARD. A new tab added to TABS but not placed in SCREEN_TAB_ORDER must not vanish."""
    src = ('const TABS=["welcome","scanner","brandnew"];\n'
           'const SCREEN_TAB_ORDER=["welcome","scanner"];\n'
           + _const("screenTabOrder"))
    ordered = run_js("", src, "screenTabOrder()")

    assert ordered == ["welcome", "scanner", "brandnew"]

    naive = run_js("", 'const TABS=["welcome","scanner","brandnew"];\n'
                       'const SCREEN_TAB_ORDER=["welcome","scanner"];\n'
                       'const screenTabOrder=()=>SCREEN_TAB_ORDER.filter(t=>TABS.includes(t));', "screenTabOrder()")
    assert "brandnew" not in naive, "the reconstruction must drop the tab, or this test proves nothing"


# ------------------------------------------------------------------------------------------------------
# #pf-subnav must be hideable (user 2026-08-22: the Best Settings date window "was being removed").
#
# pfPanel() adds the 'hidden' class, but an INLINE display:flex on the element outranks
# .hidden{display:none}, so the row kept rendering. This is a CSS-specificity defect, so it is asserted
# on the markup rather than through Node — the same inline-style defeat that once made 'Filters' dead.
# ------------------------------------------------------------------------------------------------------

def test_pf_subnav_layout_is_not_inline_so_the_hidden_class_can_win():
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r'<nav class="confnav" id="pf-subnav" style="([^"]*)"', html)
    assert m, "#pf-subnav nav element not found"

    assert "display" not in m.group(1), (
        "an inline display on #pf-subnav outranks .hidden{display:none} and pfPanel() cannot hide it")
    assert re.search(r"#pf-subnav\{[^}]*display:flex", html), "#pf-subnav still needs its flex layout in CSS"
    assert re.search(r"#pf-subnav\.hidden\{display:none\}", html), "the hidden rule needs ID+class specificity"
    assert re.search(r"pfPanel[\s\S]{0,4000}?pf-subnav[\s\S]{0,200}?classList\.toggle\(\s*[\"']hidden[\"']", html), \
        "pfPanel() must still be the thing that toggles it"


def test_apply_this_configuration_is_withheld_when_logged_out():
    html = INDEX.read_text(encoding="utf-8")

    # Wrapped in .fcard-apply on 2026-08-23 to pin it to the bottom of the card; the AUTH gate is what
    # this test actually protects.
    assert re.search(r"\$\{AUTH\?`<div class=\"fcard-apply\"><button[^`]*Apply this configuration</button></div>`",
                     html), "the Apply button must be rendered only when AUTH is set"
    assert re.search(r"async function applyConfigFromReport\([^)]*\)\{\s*(?://[^\n]*\n\s*)*if\(!AUTH\)", html), \
        "applyConfigFromReport must fail closed for every other caller"


# ------------------------------------------------------------------------------------------------------
# Transaction-evidence render cap (user 2026-08-22: "This page isn't responding").
#
# paintOrdersPerf built EVERY ledger row into one innerHTML assignment. Measured against the live
# three-year payload (11,682 rows in, 11,669 ledger entries out) that is 7.5 MB of HTML and roughly
# 233,000 DOM elements, parsed synchronously. Building the string costs ~16 ms, so the DOM parse was the
# whole cost. The ledger itself is only ~198 ms per pass and is NOT the bottleneck.
# ------------------------------------------------------------------------------------------------------

def _evidence_cap_source() -> str:
    """The three lines that decide which ledger rows are materialised."""
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r"\n\s*const _evAll=.*?\n\s*const _evRows=[^\n]*\n", html, re.S)
    assert m, "the evidence render cap was not found in paintOrdersPerf"
    return "const takenRows=[];\n" + textwrap.dedent(m.group(0))


def test_evidence_table_caps_the_rows_it_materialises():
    src = _evidence_cap_source()
    preamble = ("let WINNERS_EVIDENCE_LIMIT=250, WINNERS_EVIDENCE_SHOW_ALL=false;\n"
                "const ledger=Array.from({length:11669},(_,i)=>({i}));\n")

    assert run_js(preamble, src, "_evRows.length") == 250
    assert run_js(preamble, src, "_evAll") is False


def test_evidence_cap_preserves_chronological_order_and_the_first_rows():
    """A cap that reordered or sampled rows would change what the evidence says."""
    src = _evidence_cap_source()
    preamble = ("let WINNERS_EVIDENCE_LIMIT=3, WINNERS_EVIDENCE_SHOW_ALL=false;\n"
                "const ledger=[{i:0},{i:1},{i:2},{i:3},{i:4}];\n")

    assert run_js(preamble, src, "_evRows.map(x=>x.i)") == [0, 1, 2]


def test_show_all_renders_every_row():
    src = _evidence_cap_source()
    preamble = ("let WINNERS_EVIDENCE_LIMIT=250, WINNERS_EVIDENCE_SHOW_ALL=true;\n"
                "const ledger=Array.from({length:11669},(_,i)=>({i}));\n")

    assert run_js(preamble, src, "_evRows.length") == 11669


def test_a_small_ledger_is_never_capped_or_annotated():
    """Below the cap, every row is drawn and no notice appears. 200 < the 250 limit."""
    src = _evidence_cap_source()
    preamble = ("let WINNERS_EVIDENCE_LIMIT=250, WINNERS_EVIDENCE_SHOW_ALL=false;\n"
                "const ledger=Array.from({length:200},(_,i)=>({i}));\n")

    assert run_js(preamble, src, "_evRows.length") == 200
    assert run_js(preamble, src, "_evAll") is True


def test_the_uncapped_render_is_what_produced_the_freeze():
    """A guard that has never failed proves nothing: run the line that actually shipped."""
    preamble = "const ledger=Array.from({length:11669},(_,i)=>({i}));\n"

    was = run_js(preamble, "const rendered=ledger;", "rendered.length")
    assert was == 11669, "the reconstruction must reproduce the unbounded render"

    now = run_js("let WINNERS_EVIDENCE_LIMIT=250, WINNERS_EVIDENCE_SHOW_ALL=false;\n" + preamble,
                 _evidence_cap_source(), "_evRows.length")
    assert now < was, "THE HARNESS CANNOT DISTINGUISH THE UNBOUNDED RENDER FROM THE CAPPED ONE"


def test_the_headline_figures_are_read_from_the_ledger_not_the_table():
    """The cap is only safe because nothing above the table counts DOM rows."""
    html = INDEX.read_text(encoding="utf-8")

    assert re.search(r"tc\.textContent=`\(\$\{takenRows\.length\} trades", html), \
        "the trade count must come from takenRows, not from the rendered table"
    assert "querySelectorAll" not in html[html.index("function paintOrdersPerf"):
                                          html.index("function paintOrdersPerf") + 6000], \
        "paintOrdersPerf must not derive figures by reading rendered rows"


# ------------------------------------------------------------------------------------------------------
# Best Settings card capacity across the device bands (user 2026-08-23: "ipad mini is meant to show 9
# cards, not 8").
#
# An iPad mini is 768 wide in PORTRAIT but 1024 in LANDSCAPE. The band ended at 850, so landscape fell
# into the laptop band and showed eight. The count and the row cap were also separate literals, which is
# how the two halves of one rule could disagree; they now share BEST_TABLET_MAX.
# ------------------------------------------------------------------------------------------------------

def _capacity_source() -> str:
    html = INDEX.read_text(encoding="utf-8")
    out = []
    for name in ("BEST_TABLET_MAX", "bestCardCapacity", "bestCardMaxRows"):
        m = re.search(rf"\nconst {name}=[^\n]*", html)
        assert m, f"const {name}= not found"
        out.append(m.group(0))
    return "\n".join(out)


@pytest.mark.parametrize("width,cards", [
    (390, 6),     # phone
    (600, 6),     # phone, upper edge
    (601, 9),     # tablet band opens
    (768, 9),     # iPad mini PORTRAIT
    (1024, 9),    # iPad mini LANDSCAPE — the reported case
    (1025, 8),    # laptop
    (1440, 8),    # desktop
])
def test_card_capacity_by_viewport_width(width, cards):
    got = run_js(f"var innerWidth={width};", _capacity_source(), "bestCardCapacity()")

    assert got == cards, f"{width}px should offer {cards} cards, got {got}"


@pytest.mark.parametrize("width,rows", [(390, 3), (768, 3), (1024, 3), (1025, 2), (1440, 2)])
def test_row_cap_matches_the_same_band(width, rows):
    """A 9-card capacity with a 2-row cap would trim straight back to 8 — the halves must agree."""
    got = run_js(f"var innerWidth={width};", _capacity_source(), "bestCardMaxRows()")

    assert got == rows


def test_the_tablet_band_reaches_ipad_mini_landscape():
    """THE REGRESSION. At the old 850 boundary a landscape iPad mini got the laptop count."""
    src = _capacity_source()
    old = "const bestCardCapacity=()=>innerWidth<=600?6:(innerWidth<=850?9:8);"

    assert run_js("var innerWidth=1024;", old, "bestCardCapacity()") == 8, \
        "the reconstruction must show 8, or this test proves nothing"
    assert run_js("var innerWidth=1024;", src, "bestCardCapacity()") == 9


def test_nine_cards_fit_the_row_cap_they_are_given():
    """Capacity must be achievable: 9 cards over 3 rows needs at most 3 per row."""
    src = _capacity_source()
    for width in (768, 1024):
        cap = run_js(f"var innerWidth={width};", src, "bestCardCapacity()")
        rows = run_js(f"var innerWidth={width};", src, "bestCardMaxRows()")
        assert cap <= rows * 3, f"{width}px offers {cap} cards but only {rows} rows"


# ------------------------------------------------------------------------------------------------------
# Apply-button states and the admin loading rows (user 2026-08-23).
# ------------------------------------------------------------------------------------------------------

def test_the_apply_button_states_are_distinct_and_coloured():
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r"\nconst _APPLY_BTN=\{.*?\n?.*?failed:[^\n]*\n?", html, re.S)
    assert m, "_APPLY_BTN state table not found"
    states = m.group(0)

    assert "var(--warn)" in states, "the in-flight state must be amber"
    assert "var(--bull)" in states and "var(--bear)" in states
    assert re.search(r"--warn:#", html), "the --warn token must be defined"
    assert html.count("--warn:#") >= 2, "--warn needs a value in BOTH the dark and light themes"
    assert 'saving:{text:"⏳ Saving…"' in states


@pytest.mark.parametrize("tbody", ["ver-rows", "batch-rows", "act-rows", "sl-rows"])
def test_admin_tables_show_a_loading_row(tbody):
    """An empty admin table is indistinguishable from 'there is nothing here' while a slow query runs."""
    html = INDEX.read_text(encoding="utf-8")

    assert f'_rowsLoading("{tbody}"' in html, f"{tbody} has no loading state"


def test_the_loading_row_spans_the_real_column_count():
    """A hardcoded colspan silently under-spans as soon as a column is added."""
    html = INDEX.read_text(encoding="utf-8")
    # The span moved into _rowsCols on 2026-08-23, so the loading row and the fault row that replaces it
    # cannot disagree about the table's width.
    m = re.search(r"function _rowsCols\(body\)\{.*?\n\}", html, re.S)
    assert m, "_rowsCols not found"

    assert 'querySelectorAll("thead th").length' in m.group(0)
    assert "_rowsCols(body)" in html


def test_every_loading_message_is_the_same_wording():
    """One wording everywhere (user 2026-08-23: "'Data loading... Scanner Report' is too much info ...
    Also want consistency"). The page heading already says WHICH data set is loading, so naming it again
    in the spinner was duplication — and it differed subtly across 20 places.

    The only permitted suffixes are live STATUS: the numeric progress the user asked to keep ("retain
    numeric progress such as 230/1431") and the retry counter, which distinguishes "still working" from
    "stuck". Neither describes the data set.
    """
    html = INDEX.read_text(encoding="utf-8")
    allowed = {"${done}/${total}${eta}", "retry ${attempt+1} of 3."}

    tails = {tail.strip() for tail in re.findall(r"Data loading\u2026([^<\"'`]*)", html) if tail.strip()}
    offenders = tails - allowed

    assert not offenders, f"loading messages still name their data set: {sorted(offenders)}"
    assert "Loading instruments" not in html, "a second phrasing for the same state"


# ------------------------------------------------------------------------------------------------------
# Best Settings card + evidence fixes (user 2026-08-23).
# ------------------------------------------------------------------------------------------------------

def _stale_proof_source() -> str:
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r"\n\s*const _proofStale=[^\n]*\n\s*if\(_proofStale\)[^\n]*\n", html)
    assert m, "the empty-proof retry guard was not found"
    return textwrap.dedent(m.group(0))


def test_an_empty_proof_from_a_populated_seq_is_retried():
    """THE BUG. `[]` is TRUTHY, so a proof computed before seq was populated was cached forever and the
    evidence table rendered empty with no way back. Observed live on "Capital efficient": seq 64 rows,
    45 funded, 42.4% return, stored proof 0 rows, while a fresh replay of the same inputs returned 64.
    """
    src = _stale_proof_source() + "\nconst out={stale:_proofStale, proof:x.proof};"

    stale = run_js("const x={proof:[],seq:new Array(64).fill(0),proofAttempts:0};", src, "out.stale")
    assert stale is True, "an empty proof over a non-empty population must be retried"
    assert run_js("const x={proof:[],seq:new Array(64).fill(0),proofAttempts:0};", src, "out.proof") is None


def test_a_genuinely_empty_population_is_not_retried():
    """No eligible trades is a real answer, and must fall through to the table's own empty state."""
    src = _stale_proof_source() + "\nconst out={stale:_proofStale};"

    assert run_js("const x={proof:[],seq:[],proofAttempts:0};", src, "out.stale") is False


def test_the_retry_is_bounded():
    """A retry that could not succeed must stop, not loop forever."""
    src = _stale_proof_source() + "\nconst out={stale:_proofStale};"

    assert run_js("const x={proof:[],seq:new Array(9).fill(0),proofAttempts:3};", src, "out.stale") is False


def test_a_populated_proof_is_left_alone():
    src = _stale_proof_source() + "\nconst out={stale:_proofStale, proof:x.proof};"

    assert run_js("const x={proof:[1,2],seq:[1,2],proofAttempts:0};", src, "out.stale") is False


def test_no_template_debris_survives_in_a_loading_message():
    """REGRESSION. Normalising the loading wording on 2026-08-23 cut at the first quote and left
    `" "+label:""}` rendering literally, reported as 'Data loading - " " variable'."""
    html = INDEX.read_text(encoding="utf-8")

    assert "+label:" not in html
    for tail in re.findall(r"Data loading\u2026(.{0,30})", html):
        assert not re.match(r'^["\']\s*[+:]', tail), f"template debris after the message: {tail!r}"


def test_a_loading_row_can_always_become_a_fault():
    """A "Data loading" message must never be the last thing a user sees."""
    html = INDEX.read_text(encoding="utf-8")

    assert "function _rowsFault(" in html
    assert "_ROWS_WATCHDOG" in html, "a request that hangs rather than rejects still needs to report"
    for tbody in ("ver-rows", "batch-rows", "act-rows"):
        assert f'_rowsFault("{tbody}"' in html, f"{tbody} cannot report a fault"
        assert f'_rowsLoaded("{tbody}")' in html, f"{tbody} never cancels its watchdog on success"


def test_the_card_win_loss_matches_the_detail_panel_definition():
    """Clicking a card opens the detail panel beneath it; two different counts would read as a bug."""
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r"const _cardWL=x=>\{[^;]*;", html)
    assert m, "_cardWL not found"

    assert "r.perf>0" in m.group(0) and "r.perf<0" in m.group(0), (
        "the card must use the same >0/<0 split as the detail panel, not the ranking dead-band")
    assert "Win : Loss" in html


def test_the_apply_button_is_pinned_to_the_bottom_and_centred():
    html = INDEX.read_text(encoding="utf-8")

    assert re.search(r"\.fcard-apply\{[^}]*margin-top:auto", html), "the button must sit at the card bottom"
    assert re.search(r"\.fcard-apply\{[^}]*justify-content:center", html), "and be centred"
    assert re.search(r"\.fcard-choice \.body\{[^}]*flex:1", html), (
        "the body must stretch, or margin-top:auto has nothing to push against")


def test_the_save_outcome_survives_the_re_render():
    """The button reverts after 4s and applying triggers heavy re-renders; the confirmation must persist."""
    html = INDEX.read_text(encoding="utf-8")

    assert "function _applyBanner(" in html
    assert "best-apply-banner" in html
    assert 'aria-live","polite"' in html.replace("'", '"')
    assert "NOT saved." in html, "a failed apply must say so in the same place"


# ------------------------------------------------------------------------------------------------------
# Every tab that fetches must show a loading state (user 2026-08-23: "check performance on each tab and
# if a 'Data loading' message is required, it is implemented").
#
# Four had none when this was audited -- Documents, Order Operations, User Management and X Posts -- so
# each showed an empty table or a bare page while its request was in flight, which during a slow admin
# query is indistinguishable from "there is nothing here".
# ------------------------------------------------------------------------------------------------------

TAB_RENDERERS = {
    "activity": "renderActivity", "batch": "renderBatch", "changereq": "renderCR",
    "config": "renderConfig", "docs": "renderDocs", "fees": "renderFees",
    "igaccount": "renderIgAccount", "instruments": "renderInstruments", "jobs": "renderJobs",
    "orderops": "renderOrderOps", "performance": "renderPerformance", "squeezehist": "renderSqueezeHist",
    "syslogs": "renderSyslogs", "users": "renderUsers", "version": "renderVersion",
    "xposts": "renderXposts",
}


def _fn_body(html: str, name: str) -> str:
    m = re.search(rf"function {re.escape(name)}\(", html)
    if not m:
        return ""
    i, depth = html.index("{", m.start()), 0
    for k in range(i, len(html)):
        if html[k] == "{":
            depth += 1
        elif html[k] == "}":
            depth -= 1
            if depth == 0:
                return html[m.start():k + 1]
    return ""


def _shows_loading(html: str, name: str, seen=None) -> bool:
    """True if the renderer, or something it delegates to, puts a loading state on screen."""
    seen = seen or set()
    if name in seen:
        return False
    seen.add(name)
    body = _fn_body(html, name)
    if re.search(r"Data loading|_rowsLoading|sqh-loading|refreshing", body):
        return True
    for callee in set(re.findall(r"\b(render[A-Z]\w*|load[A-Z]\w*|paint[A-Z]\w*)\(", body)):
        if callee != name and _shows_loading(html, callee, seen):
            return True
    return False


@pytest.mark.parametrize("tab,renderer", sorted(TAB_RENDERERS.items()))
def test_every_fetching_tab_shows_a_loading_state(tab, renderer):
    html = INDEX.read_text(encoding="utf-8")
    body = _fn_body(html, renderer)
    assert body, f"{renderer} not found — the showTab dispatch map may have changed"

    if "fetch(" not in body and not re.search(r"\b(render|load)[A-Z]\w*\(", body):
        pytest.skip(f"{tab} renders from data already in memory")

    assert _shows_loading(html, renderer), (
        f"the {tab} tab fetches but shows no loading state; an empty table during a slow query is "
        f"indistinguishable from 'there is nothing here'")


def test_the_showTab_dispatch_map_still_matches_this_audit():
    """If a tab is added to the dispatch map, it must be added here too or it escapes the check."""
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r"const R=\{(.*?)\};", html, re.S)
    assert m, "the showTab renderer map was not found"
    mapped = set(re.findall(r"([a-z0-9]+):render", m.group(1)))

    missing = mapped - set(TAB_RENDERERS) - {"marketsadmin", "markets", "preorders", "configadmin"}
    assert not missing, f"tabs in the dispatch map but absent from this audit: {sorted(missing)}"


# ------------------------------------------------------------------------------------------------------
# A tab can depend on data it does not fetch itself (user 2026-08-24: "We are still missing Data loading
# messages e.g. Markets (Admin)").
#
# The first audit only asked "does this renderer call fetch()". Markets, Markets (Admin) and My Pre-orders
# all derive from the shared DATA snapshot, which /api/records loads asynchronously -- measured at 14.4 s
# -- so they passed that check while still rendering an empty table for fourteen seconds. Depending on
# async data needs a loading state just as much as fetching it does.
# ------------------------------------------------------------------------------------------------------

DATA_DEPENDENT = ["renderMarkets", "renderMarketsAdmin", "renderPreorders"]


@pytest.mark.parametrize("renderer", DATA_DEPENDENT)
def test_renderers_that_depend_on_the_shared_snapshot_show_a_loading_state(renderer):
    html = INDEX.read_text(encoding="utf-8")
    body = _fn_body(html, renderer)
    assert body, f"{renderer} not found"

    assert "_awaitingData(" in body, (
        f"{renderer} reads DATA but shows nothing while it loads; an empty table during a 14-second "
        f"fetch is indistinguishable from 'there is nothing here'")


def test_the_data_guard_actually_paints_a_loading_row():
    html = INDEX.read_text(encoding="utf-8")
    guard = _fn_body(html, "_awaitingData")

    assert "_rowsLoading(" in guard, "the guard must show the message, not just return early"
    assert "DATA" in guard and "length" in guard


def test_no_renderer_reads_DATA_without_either_fetching_or_guarding():
    """THE CORRECTED AUDIT. Catches the next tab added with this shape."""
    html = INDEX.read_text(encoding="utf-8")
    gaps = []
    for renderer in set(list(TAB_RENDERERS.values()) + DATA_DEPENDENT):
        body = _fn_body(html, renderer)
        if not body:
            continue
        reads_data = re.search(r"\bDATA\b", body)
        if reads_data and "fetch(" not in body and not re.search(
                r"Data loading|_rowsLoading|_awaitingData|sqh-loading", body):
            gaps.append(renderer)

    assert not gaps, f"these renderers depend on the async snapshot with no loading state: {sorted(gaps)}"


# ------------------------------------------------------------------------------------------------------
# Instruments (user 2026-08-24: "why is the initial click of each tab so slow e.g. instruments").
# The render cap this section originally tested was replaced by virtualisation on 2026-08-25; the
# windowing itself is covered by the virtual-table tests above. What remains here is the promise that
# capping or windowing the ROWS never caps the NUMBERS.
#
# Measured on the live site: all 1,773 rows is about 25,000 DOM elements -- 65% of the entire page --
# torn down and rebuilt on every paint. Successive paints took 2,201 / 2,940 / 18,568 ms. An 18-second
# synchronous block is precisely why a click elsewhere appears ignored.
# ------------------------------------------------------------------------------------------------------

def test_the_counts_still_report_the_true_totals():
    """Capping the rows must not cap the numbers: the count and the tab badge report what MATCHED."""
    html = INDEX.read_text(encoding="utf-8")

    assert "${shown.length.toLocaleString()}</b> instruments" in html
    assert '$("instrtab-count").textContent=`(${shown.length.toLocaleString()})`' in html


def test_the_instruments_tab_button_has_a_count_like_the_others():
    """user 2026-08-24: "instruments tab does not have table row count in it - unlike most other tabs"."""
    html = INDEX.read_text(encoding="utf-8")

    assert 'id="instrtab-count"' in html
    for sibling in ("sctab-count", "potab-count", "ootab-count"):
        assert f'id="{sibling}"' in html, "the sibling tab counts this was matched to have moved"


# ------------------------------------------------------------------------------------------------------
# Virtual table rows (user 2026-08-25: "not all rows are visible at once - why not deal with what is
# visible first?").
#
# Only the rows on screen exist in the DOM; everything above and below is two spacer rows of the right
# height, so the scrollbar stays honest. Unlike a render cap, the DOM stays small however far you scroll.
#
# Executed against a fake DOM rather than asserted on source text: the windowing arithmetic is the part
# that can be wrong, and a wrong window shows the user the wrong rows.
# ------------------------------------------------------------------------------------------------------

def _vtable_env(top_px: int, rows: int, viewport: int = 800) -> str:
    """Stubs enough DOM for the helper to run: a tbody whose top edge sits at `top_px`."""
    return textwrap.dedent(f"""
        let HTML="";
        const body={{
          set innerHTML(v){{HTML=v;}}, get innerHTML(){{return HTML;}},
          firstElementChild:{{getBoundingClientRect:()=>({{height:28}})}},
          getBoundingClientRect:()=>({{top:{top_px}}}),
          closest:()=>null
        }};
        const $=()=>body;
        const window={{innerHeight:{viewport},addEventListener(){{}}}};
        const document={{documentElement:{{clientHeight:{viewport}}}}};
        const requestAnimationFrame=f=>f();
        const ROWS=Array.from({{length:{rows}}},(_,i)=>({{i}}));
        const RENDER=r=>`<tr data-i="${{r.i}}"></tr>`;
    """)


def _vtable_src() -> str:
    js = client_js()
    consts = re.search(r"\nconst VTABLE=\{\};[^\n]*\nconst VTABLE_OVERSCAN=\d+;", js)
    assert consts, "the virtual-table state declarations were not found"

    def grab(decl):
        s = js.index(decl)
        i, d = js.index("{", s), 0
        while i < len(js):
            if js[i] == "{":
                d += 1
            elif js[i] == "}":
                d -= 1
                if d == 0:
                    return js[s:i + 1]
            i += 1
        raise AssertionError(decl)

    return "\n".join([consts.group(0), grab("function _vtableWindow("),
                      grab("function _vtablePaint("), grab("function vtable(")])


def _drawn(html: str) -> int:
    return len(re.findall(r'data-i="', html))


def test_only_a_screenful_of_rows_is_drawn():
    """THE POINT. 1,773 matching rows must not become 1,773 rows of DOM."""
    out = run_js(_vtable_env(0, 1773), _vtable_src(),
                 '(vtable("t",ROWS,RENDER,14), body.innerHTML)')

    drawn = _drawn(out)
    assert 20 < drawn < 120, f"expected roughly a screenful, drew {drawn}"
    assert drawn < 1773 / 10, "virtualisation is not reducing the DOM materially"


def test_the_window_stays_small_wherever_you_scroll():
    """A render cap shrinks the first page; virtualisation must hold at any scroll position."""
    counts = []
    for top in (0, -28 * 400, -28 * 900, -28 * 1500):
        out = run_js(_vtable_env(top, 1773), _vtable_src(),
                     '(vtable("t",ROWS,RENDER,14), body.innerHTML)')
        counts.append(_drawn(out))

    assert all(c < 120 for c in counts), f"the window grew while scrolling: {counts}"


def test_spacers_preserve_the_scroll_height():
    """Without spacers the scrollbar would shrink to the visible rows and scrolling would break."""
    mid = run_js(_vtable_env(-28 * 900, 1773), _vtable_src(),
                 '(vtable("t",ROWS,RENDER,14), body.innerHTML)')

    assert mid.count("aria-hidden") == 2, "midway through, there must be a spacer above AND below"
    heights = [int(h) for h in re.findall(r"height:(\d+)px", mid)]
    drawn = _drawn(mid)
    assert sum(heights) + drawn * 28 == 1773 * 28, (
        "spacers plus drawn rows must account for every row, or the scrollbar lies")


def test_the_top_of_the_list_has_no_spacer_above_it():
    out = run_js(_vtable_env(0, 1773), _vtable_src(),
                 '(vtable("t",ROWS,RENDER,14), body.innerHTML)')

    assert out.count("aria-hidden") == 1, "at the top only the below-spacer is needed"
    assert out.index('data-i="0"') < out.index("aria-hidden"), "row 0 must come before the spacer"


def test_a_short_list_is_drawn_whole_with_no_spacers():
    out = run_js(_vtable_env(0, 12), _vtable_src(),
                 '(vtable("t",ROWS.slice(0,12),RENDER,14), body.innerHTML)')

    assert _drawn(out) == 12
    assert "aria-hidden" not in out


def test_an_empty_list_shows_the_caller_s_message():
    out = run_js(_vtable_env(0, 0), _vtable_src(),
                 '(vtable("t",[],RENDER,14,"No instruments match."), body.innerHTML)')

    assert "No instruments match." in out
    assert _drawn(out) == 0


def test_instruments_uses_the_virtual_table():
    html = INDEX.read_text(encoding="utf-8")
    body = _fn_body(html, "paintInstruments")

    assert 'vtable("instr-rows"' in body, "Instruments is not virtualised"
    assert "INSTR_ROW_LIMIT" not in html, "the old render cap is still present alongside virtualisation"
    assert "shown" in body, "the window must be taken from the sorted/filtered list"


# ------------------------------------------------------------------------------------------------------
# Every auth-gated endpoint must be called WITH the token (user 2026-08-25: "we have items listed as
# Triggered but when I look at details to see squeeze rules it says no rule detail - this must exist if
# triggered").
#
# /api/rules is admin-gated and reads X-Auth. The client fetched it without the header, so it always came
# back 403 {"error":"admin only"}; j.rules was undefined, `(j.rules||[])` rendered the empty state, and
# the card read "no rule detail" for every instrument. It had never worked. The sibling /api/volscore
# fetch three lines below always sent the header.
#
# The 403 was invisible precisely because it is valid JSON, so .catch() never ran. A refusal must never
# be able to impersonate "there is no data".
# ------------------------------------------------------------------------------------------------------

def _auth_gated_endpoints() -> set:
    """Endpoints whose handler rejects a request without a valid token.

    Detected by BEHAVIOUR — the handler reads the token and can answer 401/403 — not by the wording of
    its refusal. The original version required the literal text "login required", "is_admin" or
    "admin only", so on 2026-08-25 it silently failed to recognise a newly gated /api/positions that
    returns `jsonify({"positions": {}}), 401`: the guard passed while the client still called it bare.
    A guard for "the next gated endpoint" must not depend on how the refusal happens to be phrased.

    The body is read to the NEXT route decorator rather than a fixed number of characters. The fixed
    900-char window this replaced was defeated by nothing more exotic than a long docstring: the
    explanatory comment added above the /api/positions auth check pushed `name_for_token` past the end
    of the window, so the endpoint again went undetected. A detector must read the whole handler.
    """
    server = (Path(__file__).parent / "hvf_web" / "server.py").read_text(encoding="utf-8")
    starts = [(m.start(), m.end(), m.group(1)) for m in re.finditer(r'@app\.route\("(/api/[^"]+)"', server)]
    gated = set()
    for i, (_, end, path) in enumerate(starts):
        stop = starts[i + 1][0] if i + 1 < len(starts) else len(server)
        body = server[end:stop]
        refuses = ("is_admin" in body) or re.search(r",\s*(401|403)\b", body)
        if "name_for_token" in body and refuses:
            gated.add(path.split("<")[0].rstrip("/"))
    return gated


def test_no_gated_endpoint_is_fetched_without_the_token():
    """THE BUG, as a rule rather than a single case."""
    js = client_js()
    gated = _auth_gated_endpoints()
    assert gated, "no auth-gated endpoints found — has the server been restructured?"

    missing = []
    for m in re.finditer(r'fetch\(\s*[`"\']([^`"\']*?/api/[^`"\'?]*)', js):
        url = m.group(1)
        base = re.sub(r"\$\{[^}]*\}", "", url).split("?")[0].rstrip("/")
        window = js[m.start():m.start() + 260]
        for g in gated:
            if base == g or base.startswith(g):
                if "X-Auth" not in window:
                    missing.append(url)
                break

    assert not missing, f"these gated endpoints are fetched without X-Auth: {sorted(set(missing))}"


def test_the_rules_card_sends_the_token():
    js = client_js()
    m = re.search(r"fetch\(`/api/rules/\$\{r\.ticker\}`[^)]*\)", js)
    assert m, "the rules fetch was not found"

    assert "X-Auth" in m.group(0), "/api/rules is admin-gated and must send the token"


def test_a_refused_rules_request_does_not_look_like_missing_data():
    """A 403 is valid JSON, so it reaches .then() and used to render the empty state silently."""
    js = client_js()
    i = js.index("fetch(`/api/rules/${r.ticker}`")
    block = js[i:i + 1400]

    assert "x.ok?" in block, "a non-OK response must be rejected rather than parsed as data"
    assert "could not be loaded" in block, "a refusal must say so, not show an empty-state message"
    assert "no rule detail</span>" not in block, (
        "the old wording implied the setup HAS no rules; the empty state must not be reachable by a 403")


# ======================================================================================================
# A suppressed fetch must still resolve its loading state (user 2026-08-28: "Best settings history -
# Filter changes... - Data loading... - NEVER COMPLETES - when user not logged in").
#
# loadBestSettingsHistory began `if(BEST_HISTORY_REQUESTED||!AUTH)return;`. index.html ships the panel
# with a STATIC `class="sqh-loading">Data loading...` placeholder, so logged out the function returned
# before doing anything and nothing was ever going to replace it. The spinner was permanent by
# construction — not slow, unresolvable.
#
# Same family as the /api/rules defect: a refusal impersonating work in progress.
# ======================================================================================================

_BSH_PREAMBLE = """
let fetched = 0;
const boxes = {"best-settings-history": {cls: ["sqh-loading"], html: "\u23f3 Data loading\u2026", text: ""},
               "best-history-count":    {cls: [], html: "", text: "seeded"}};
const $ = id => {
  const b = boxes[id]; if (!b) return null;
  return {classList: {remove: c => { b.cls = b.cls.filter(x => x !== c); }},
          set innerHTML(v) { b.html = v; }, get innerHTML() { return b.html; },
          set textContent(v) { b.text = v; }, get textContent() { return b.text; }};
};
const fetch = () => { fetched++; return {then: () => ({then: () => ({catch: () => {}})})}; };
function paintBestSettingsHistory() {}
let AUTH = "", BEST_HISTORY_REQUESTED = false;
"""


def _run_bsh(auth: str):
    return run_js(_BSH_PREAMBLE + f'\nAUTH = {auth!r};',
                  _extract("loadBestSettingsHistory"),
                  "(loadBestSettingsHistory(), {loading: boxes['best-settings-history'].cls,"
                  " html: boxes['best-settings-history'].html,"
                  " count: boxes['best-history-count'].text, fetched, requested: BEST_HISTORY_REQUESTED})")


def test_logged_out_best_settings_history_resolves_instead_of_spinning_for_ever():
    out = _run_bsh("")

    assert out["loading"] == [], "the sqh-loading class must be removed, or the spinner never resolves"
    assert "Data loading" not in out["html"], "THE BUG: the loading placeholder was left in place"
    assert "Log in" in out["html"], "a logged-out visitor must be told why there is nothing to show"
    assert out["fetched"] == 0, "no request should be made without a token"
    assert out["requested"] is False, "logging in later must still be able to load the history"


def test_logged_in_best_settings_history_still_fetches():
    out = _run_bsh("tok")

    assert out["fetched"] == 1, "a logged-in user must still get their history"
    assert out["requested"] is True, "the once-only guard must still latch"


# ======================================================================================================
# Funded ("actual") win:loss alongside eligible (user 2026-08-28: "Win:Loss is for Eligible - show this
# ratio for Actual also").
#
# The distinction is not cosmetic. ELIGIBLE counts every trade matching the configuration; ACTUAL counts
# only those the wallet could fund. Capacity decides WHICH eligible trades get taken, so the two splits
# can disagree — which is the whole reason the requester wanted both on screen.
#
# _combReplay is the core wallet replay, so these also pin that the change was ADDITIVE: ret, dd and n
# must be untouched by the two counters added beside them.
# ======================================================================================================

_REPLAY_PREAMBLE = """
let MIN_TRADE = 0, WINNERS_WALLET = 10000;
const _fundedMaxOpen = f => Math.max(1, Math.floor(1 / Math.max(0.000001, +f || 0.000001)));
const levOf = () => 1;
const _pfExitDate = r => r.exit_date;
"""

# r2 is blocked by a max-open cap of 1 while r1 is still open, so it is ELIGIBLE but never FUNDED.
_SEQ = ("[{trig_date:'2026-01-01',exit_date:'2026-06-01',perf:10},"
        " {trig_date:'2026-01-02',exit_date:'2026-02-01',perf:-5},"
        " {trig_date:'2026-07-01',exit_date:'2026-08-01',perf:-3}]")


def _replay(maxopen):
    return run_js(_REPLAY_PREAMBLE, _extract("_combReplay"),
                  "(()=>{const x=_combReplay(" + _SEQ + f",0.05,{maxopen});"
                  " return {ret:x.ret,dd:x.dd,n:x.n,wins:x.wins,losses:x.losses};})()")


def test_funded_win_loss_differs_from_eligible_when_capacity_binds():
    out = _replay(1)

    # Eligible over the same three rows is 1 win : 2 losses. Funded excludes the row capacity blocked.
    assert out["n"] == 2, "a max-open cap of 1 must fund only two of the three trades"
    assert (out["wins"], out["losses"]) == (1, 1), (
        "the funded split must exclude the capacity-blocked loss; got "
        f"{out['wins']}:{out['losses']}")


def test_funded_win_loss_accounts_for_every_funded_trade():
    out = _replay(50)

    assert out["n"] == 3, "with ample capacity every eligible trade is funded"
    assert out["wins"] + out["losses"] == out["n"], (
        "every funded trade must land in exactly one bucket unless it is break-even")
    assert (out["wins"], out["losses"]) == (1, 2)


def test_adding_the_counters_did_not_move_the_replay_numbers():
    """The counters sit beside ret/dd/n in the core replay. Adding them must change none of those."""
    capped, ample = _replay(1), _replay(50)

    # Independently derived: 5% of the wallet on +10% then -3%, compounding, with r2 blocked.
    assert round(capped["ret"], 10) == round((1 + 0.05 * 0.10) * (1 - 0.05 * 0.03) - 1, 10)
    assert capped["dd"] >= 0 and ample["dd"] >= 0
    assert ample["n"] == 3 and capped["n"] == 2


# ======================================================================================================
# The date window must not be able to affect results (user 2026-08-28: "Date filter should be hidden -
# it is of little value currently - Also, must not have any values in it that affects results").
#
# pfDateOk runs FIRST in the Performance pipeline -- all = PERF_DATA.filter(r=>pfDateOk(r.trig_date)) --
# so a lingering From/To value silently shrinks EVERY figure on the tab: row count, win:loss, wallet.
# Hiding the control is not enough on its own; a value already set would keep filtering. The second
# assertion is the one that matters: even WITH both bounds set, nothing is excluded while it is off.
# ======================================================================================================

def _date_ok(from_, to, dates):
    """The REAL flag is lifted from source, never stubbed -- stubbing it would test the stub."""
    js = client_js()
    flag = re.search(r"const PF_DATE_FILTER_ENABLED=(?:true|false);", js)
    assert flag, "PF_DATE_FILTER_ENABLED was not found - has the date window been restructured?"
    pre = f"let PF_DATE_FROM={from_!r}, PF_DATE_TO={to!r};" + "\n" + flag.group(0)
    return run_js(pre, _extract("pfDateOk"), "[" + ",".join(f"pfDateOk({d!r})" for d in dates) + "]")


def test_date_window_cannot_exclude_anything_while_disabled():
    """Reconstructs the reported risk: bounds are present, and must still filter nothing."""
    out = _date_ok("2026-06-01", "2026-06-30", ["2026-01-15", "2026-06-15", "2026-12-31", ""])

    assert out == [True, True, True, True], (
        "a date value is still excluding rows -- every Performance figure would be silently reduced")


def test_the_date_filter_is_switched_off_at_source_not_merely_hidden():
    js = client_js()

    assert "const PF_DATE_FILTER_ENABLED=false" in js, "the window must be disabled explicitly"
    assert 'class="pf-date-group" style="display:none"' in client_source(), (
        "the control must also be hidden, per the request")


def test_the_date_preset_does_not_claim_a_window_it_is_not_applying():
    """It shipped defaulting to 'Last 12 months' while PF_DATE_FROM was "" -- the control displayed a
    window the pipeline was not using, because pfDatePreset only ever ran on change."""
    markup = client_source()

    assert '<option value="" selected>Custom / all</option>' in markup, (
        "the selected option must match the unfiltered state the code actually applies")
