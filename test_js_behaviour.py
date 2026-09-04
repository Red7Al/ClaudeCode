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
import pathlib
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

# The JS moved to hvf_web/app.js on 2026-08-23; client_source() returns markup+script together so
# these assertions keep meaning what they meant when it was one file.
from client_source import APP_JS, BEST_SETTINGS_JS, client_html, client_js, client_source
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
    return _run_node(f"{preamble}\n{source}\nconsole.log(JSON.stringify({call}));")


def run_js_async(preamble: str, source: str, call: str):
    """As run_js, but for a `call` that evaluates to a promise.

    run_js prints synchronously, so an async client function's result is still a bare Promise when it is
    stringified -- every assertion then runs against `{}` and passes for the wrong reason. Anything
    reached only after an `await` (a confirmation dialog, a save banner) needs this runner instead.
    """
    return _run_node(
        f"{preamble}\n{source}\n"
        f"Promise.resolve({call}).then(v=>console.log(JSON.stringify(v===undefined?null:v)),"
        f"e=>{{console.error((e&&e.stack)||e);process.exit(1);}});")


def _run_node(script: str):
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


def _apply_row(auth):
    """The apply row both card renderers emit, EXECUTED at the given auth state.

    The control moved into _bestApplyRow on 2026-09-03 when the logged-out cards were added, so both
    cards and both auth states now come from one function. Executed rather than pattern-matched: this is
    the gate that decides whether a signed-out visitor is offered an action that cannot work.
    """
    return run_js(f"const AUTH={auth};", _extract("_bestApplyRow"),
                  "_bestApplyRow({limits:{},filters:{}},false)")


def test_apply_this_configuration_is_withheld_when_logged_out():
    html = INDEX.read_text(encoding="utf-8")

    out = _apply_row('""')
    assert "applyConfigFromReport" not in out and "<button" not in out, (
        f"a signed-out visitor must not be offered the Apply control: {out}")
    assert "Log in to apply this configuration." in out, (
        "and must be offered the sign-in that would make it work")

    signed_in = _apply_row('"tok"')
    assert "applyConfigFromReport" in signed_in and "fcard-apply" in signed_in, (
        f"a signed-in user must still get the working control: {signed_in}")

    # Every card must reach the control through that one function, or a second copy could drift past it.
    js = client_js()
    assert "apply:_bestApplyRow(" in js, "the choice cards must build their apply row through _bestApplyRow"
    assert "_bestApplyRow(b.cfg,matches,{belowEvidence:b.n})" in js, "and so must the three-year card"

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

def test_the_performance_figures_are_never_read_from_rendered_rows():
    """Figures come from the ledger, never from the DOM.

    This test used to also assert the trade-count line `tc.textContent=...takenRows.length`. That line
    wrote into #ordp-count, an element deleted from the page on 2026-08-04, so that half was guarding
    code that could not run -- one of SIX such tests that were green while covering a dead renderer.
    The surviving half is the rule that mattered and still applies.
    """
    src = _extract("paintOrdersPerf")

    assert "querySelectorAll" not in src,         "paintOrdersPerf must not derive figures by reading rendered rows"
    assert "_winLedger(" in src, "the figures come from the ledger"


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


# None == Infinity here: JSON has no Infinity, so an unlimited row cap comes back as null.
@pytest.mark.parametrize("width,rows", [(390, None), (768, None), (1024, None), (1025, 2), (1440, 2)])
def test_row_cap_matches_the_same_band(width, rows):
    """A capacity the row cap cannot fit is trimmed straight back down — the halves must agree.

    Updated 2026-08-28: the tablet/phone band previously capped at THREE rows while offering nine cards,
    and nine cards at min-width 240px need four rows at an iPad mini's 768px, so the post-layout loop
    deleted cards until they fitted. On those devices the count is the constraint and rows follow it;
    the two-row cap remains on laptops, where it is the real limit.
    """
    got = run_js(f"var innerWidth={width};", _capacity_source(), "bestCardMaxRows()")

    assert got == rows


def test_the_tablet_band_reaches_ipad_mini_landscape():
    """THE REGRESSION. At the old 850 boundary a landscape iPad mini got the laptop count."""
    src = _capacity_source()
    old = "const bestCardCapacity=()=>innerWidth<=600?6:(innerWidth<=850?9:8);"

    assert run_js("var innerWidth=1024;", old, "bestCardCapacity()") == 8, \
        "the reconstruction must show 8, or this test proves nothing"
    assert run_js("var innerWidth=1024;", src, "bestCardCapacity()") == 9


def test_the_offered_card_count_is_always_achievable():
    """The rule this file exists for: a capacity the row cap cannot hold is not a capacity, because the
    post-layout loop deletes cards until the grid fits.

    Cards are min-width 240px with roughly a 10px gap, so the number per row scales with the viewport --
    a fixed "three per row" was wrong, since a 1440px laptop fits five.
    """
    src = _capacity_source()
    for width in (390, 768, 1024, 1025, 1440):
        cap = run_js(f"var innerWidth={width};", src, "bestCardCapacity()")
        rows = run_js(f"var innerWidth={width};", src, "bestCardMaxRows()")
        if rows is None:
            continue                      # unlimited rows: any count is achievable
        per_row = max(1, width // 250)
        assert cap <= rows * per_row, (
            f"{width}px offers {cap} cards but {rows} rows of ~{per_row} can only hold {rows * per_row}")


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
            if base == g or base.startswith(g + "/"):
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
const levOf = () => 1;
const _pfExitDate = r => r.exit_date;
"""


def _replay_source() -> str:
    """The REAL wallet replay, wired exactly the way app.js wires it.

    It moved into hvf_web/best_settings.js on 2026-09-03 so the server could run the page's own search
    under Node for the logged-out cards. It is produced by a factory there, so extracting the inner
    function alone would yield a closure with no environment -- the factory AND app.js's own wiring line
    are together what make `_combReplay` exist. Taking the wiring from the page keeps this harness
    running the shipped replay rather than a lookalike assembled by the test.
    """
    js = client_js()
    funded = re.search(r"^const _fundedMaxOpen=[^\n]*", js, re.M)
    wiring = re.search(r"^const _combReplay=makeCombReplay\(\{[\s\S]*?\}\);", js, re.M)
    assert funded and wiring, "app.js no longer builds its replay from makeCombReplay"
    return "\n".join([funded.group(0), _extract("makeCombReplay"), wiring.group(0)])

# r2 is blocked by a max-open cap of 1 while r1 is still open, so it is ELIGIBLE but never FUNDED.
_SEQ = ("[{trig_date:'2026-01-01',exit_date:'2026-06-01',perf:10},"
        " {trig_date:'2026-01-02',exit_date:'2026-02-01',perf:-5},"
        " {trig_date:'2026-07-01',exit_date:'2026-08-01',perf:-3}]")


def _replay(maxopen):
    return run_js(_REPLAY_PREAMBLE, _replay_source(),
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


# ======================================================================================================
# A failed three-year load must SAY so (user 2026-08-28: "Apply this configuration not always available
# for 3 years"; card observed reading "Evidence loading separately...").
#
# loadThreeYear gave up after two retries with nothing but a console.warn, leaving WIN_3Y at null. The
# status card keys its text on WIN_3Y===null, so a permanent failure and "still loading" were the same
# state -- the card sat on the loading message for ever, and the Apply button never arrived because the
# card only joins the applicable cards when threeYear is non-null.
#
# Third instance of this shape in one engagement (/api/rules, Best settings history, this).
# ======================================================================================================

def test_a_failed_three_year_load_is_distinguishable_from_still_loading():
    js = client_js()

    assert 'WIN_3Y_ERROR=""' in js or "let WIN_3Y_ERROR" in js, (
        "a failure must be held in its own state, not collapsed into WIN_3Y===null")
    loader = js[js.index("const loadThreeYear="):]
    loader = loader[:loader.index("const loadWinners=")]

    assert "WIN_3Y_ERROR=String(" in loader, "the final failure must record why"
    assert loader.count("winnersParamsChange()") >= 2, (
        "the failure path must re-render, or the card keeps showing the stale loading message")


def test_the_three_year_card_offers_a_retry_when_it_failed():
    markup_js = client_js()

    assert "retryThreeYear" in markup_js, "a failed card must offer a way to try again"
    assert "Evidence could not be loaded" in markup_js, (
        "the card must state the failure rather than continue to claim it is loading")
    assert "Three-year evidence could not be loaded" in markup_js


def test_still_loading_message_is_kept_for_the_genuine_loading_case():
    """The honest loading message must survive -- this fix must not turn a slow load into a false error."""
    js = client_js()

    assert "This card remains visible while the complete three-year evidence loads." in js


# ======================================================================================================
# The three-year memo key (user 2026-08-29: "'Apply this configuration' is timing out").
#
# The three-year grid is the freeze: 962,010 replays over 44.8M row-visits, 52-61 s of blocked main
# thread, ~90% of renderBestCombo. It was recomputed on every re-render, and Apply causes one --
# applyWinnersDefaults -> winnersParamsChange -> paintOrdersPerf -> renderBestCombo -- so saving froze
# the page for a minute and read as a timeout.
#
# A memo on a screen that drives trading decisions is a STALE-NUMBER risk: a wrong key shows a
# confident, wrong card. So the key is pinned by execution, both ways -- what must invalidate it, and
# what must NOT (which is the entire point, since Apply changes stake and max-open).
# ======================================================================================================

def _memo_guard() -> str:
    # The search moved to hvf_web/best_settings.js on 2026-09-03 and takes its model as parameters, so
    # the guard compares those rather than the page globals. The page still supplies them; the wiring is
    # asserted separately below, because a guard on the right names fed the wrong values would pass here.
    js = client_js()
    m = re.search(r"if\((memoIn\.rows===env\.rows3y[^)]*)\)\{", js)
    assert m, "the three-year memo guard was not found - has the search been restructured?"
    return m.group(1)               # the CONDITION only — `if(...)` is a statement, not an expression


def _memo_hit(**changed):
    """Seed the memo from a baseline, apply one change, and report whether the guard still hits."""
    base = {"rows": "ROWS", "wallet": 10000, "minTrade": 25,
            "stake": 0.05, "maxopen": 20}
    now = {**base, **changed}
    pre = f"""
const ROWS=['a'], OTHER=['b'];
const memoIn={{rows:ROWS,wallet:{base['wallet']},minTrade:{base['minTrade']},best:'CACHED'}};
const env={{rows3y:{now['rows']}}};
const wallet={now['wallet']}, minTrade={now['minTrade']};
const stake={now['stake']}, maxOpen={now['maxopen']};
"""
    return run_js(pre, "", f"({_memo_guard()})")


def test_the_page_still_feeds_the_memo_from_its_own_model():
    """The guard names parameters now, so the wiring is the other half of the same rule."""
    js = client_js()

    assert "rows3y:WIN_3Y,wallet:WINNERS_WALLET,minTrade:MIN_TRADE" in js, (
        "the search must be given the page's three-year rows, wallet and minimum trade")
    assert "memo:_3Y_MEMO" in js and "_3Y_MEMO=res.memo" in js, (
        "the memo must be handed in and handed back, or every render repeats the 52-61 second search")


def test_memo_is_reused_when_nothing_it_depends_on_changed():
    assert _memo_hit() is True, "an unchanged screen must not repeat a 52-61 second search"


def test_apply_changing_stake_or_max_open_still_reuses_the_memo():
    """THE POINT. The grid searches stake and max-open itself, so the user's values are not inputs to
    the expensive part -- which is why Apply can be fast."""
    assert _memo_hit(stake=0.10) is True
    assert _memo_hit(maxopen=3) is True


def test_a_changed_wallet_or_minimum_trade_invalidates_the_memo():
    """_combReplay derives its minimum-stake floor from MIN_TRADE / WINNERS_WALLET, so both change the
    funded population and therefore the answer. Missing these would show a stale card."""
    assert _memo_hit(wallet=50000) is False
    assert _memo_hit(minTrade=0) is False


def test_new_three_year_data_invalidates_the_memo():
    assert _memo_hit(rows="OTHER") is False


def test_the_memo_key_does_not_include_stake_or_max_open():
    """Source-level, deliberately: including them would silently reintroduce the freeze on every Apply."""
    guard = _memo_guard()

    assert "stake" not in guard and "maxOpen" not in guard, (
        f"stake/max-open must not be in the memo key or Apply freezes again: {guard}")
    assert "wallet" in guard and "minTrade" in guard and "rows3y" in guard


# ======================================================================================================
# Apply must confirm the save before it re-renders (user 2026-08-28: "'Apply this configuration' is not
# showing 'Saving' (AMBER) whilst running or 'Saved' (GREEN) once complete").
#
# The amber state was always set correctly. The GREEN one sat AFTER applyWinnersDefaults(), which
# re-runs the Best Settings render -- measured at 52-61 s before the three-year search was memoised. So
# a working save showed amber, froze the page for a minute, and only then said "Saved". The POST has
# already succeeded at that point, so the user was being made to wait for a repaint to be told something
# that was already true.
#
# Structural rather than executed: this is about ORDER within a promise callback, which is exactly what
# an execution test with stubbed renderers would hide.
# ======================================================================================================

def _apply_success_block() -> str:
    """The success callback with COMMENTS STRIPPED.

    Comments must go before any ordering check: the explanatory note added with this fix mentions
    applyWinnersDefaults by name, and a naive index() matched the comment rather than the call — which
    made the test fail against correct code.
    """
    src = _extract("applyConfigFromReport")
    i = src.index(".then(r=>{if(!r.ok)throw 0;")
    block = src[i:src.index(".catch(", i)]
    return "\n".join(l for l in block.split("\n") if not l.lstrip().startswith("//"))


def test_apply_confirms_the_save_before_any_re_render():
    block = _apply_success_block()

    done = block.index('_applyBtnState(btn,"done")')
    saved = block.index("_applyBanner(`Saved at")
    heavy = block.index("applyWinnersDefaults")

    assert done < heavy, "the button must turn green before the Best Settings re-render, not after it"
    assert saved < heavy, "the Saved banner must appear before the re-render, not after it"


def test_apply_still_shows_the_in_progress_state_before_the_request():
    src = _extract("applyConfigFromReport")

    saving = src.index('_applyBtnState(btn,"saving")')
    posting = src.index('fetch("/api/config"')

    assert saving < posting, "amber must be set before the request, or there is no in-progress state"
    assert 'var(--warn)' in src, "the in-progress banner must be amber"
    assert 'var(--bull)' in src and 'var(--bear)' in src, "saved is green and failed is red"


def test_a_failed_apply_still_reports_failure():
    """The reordering must not let a failure inherit the success state."""
    src = _extract("applyConfigFromReport")
    catch = src[src.index(".catch("):]

    assert '_applyBtnState(btn,"failed")' in catch
    assert "NOT saved" in catch


# ======================================================================================================
# Card count vs row cap (user 2026-08-28: "iPad mini not seeing 9 cards (typically 6,7, or 8)").
#
# bestCardCapacity allowed nine on a tablet while bestCardMaxRows capped the grid at three rows, and the
# post-layout loop DELETES cards until they fit. Nine cards at min-width 240px need four rows at an iPad
# mini's 768px, so the row cap silently overruled the count -- which is the six-to-eight actually seen.
# ======================================================================================================

def _capacity_and_rows(width):
    src = "\n".join(re.search(rf"const {n}=[^\n]+", client_js()).group(0)
                    for n in ("BEST_TABLET_MAX", "bestCardCapacity", "bestCardMaxRows"))
    return run_js(f"const innerWidth={width};", src,
                  "[bestCardCapacity(), bestCardMaxRows()===Infinity?'unlimited':bestCardMaxRows()]")


def test_a_tablet_may_use_as_many_rows_as_its_card_count_needs():
    for width in (768, 1024):          # iPad mini portrait and landscape
        cap, rows = _capacity_and_rows(width)
        assert cap == 9, f"{width}px should allow nine cards, got {cap}"
        assert rows == "unlimited", (
            f"{width}px caps rows at {rows}, so the trimming loop deletes cards the count allows")


def test_a_laptop_still_trims_to_two_rows():
    """The row cap exists for laptops and must survive: eight cards are only worth showing if they fit."""
    cap, rows = _capacity_and_rows(1440)

    assert cap == 8 and rows == 2


def test_a_phone_keeps_its_smaller_card_count():
    cap, rows = _capacity_and_rows(390)

    assert cap == 6, "phones stop at six"
    assert rows == "unlimited", "a phone scrolls; the count is the constraint"


# ======================================================================================================
# "This is your current configuration" (user 2026-08-28: "If one of the card configurations matches user
# configuration e.g. Capital Efficient - make it very clear").
#
# The information already existed -- changedFor() returned "No change from your current configuration" --
# but only as small muted text in the Changes line at the foot of the card. The risk in adding a louder
# claim is that it disagrees with that line, so both are derived from the SAME comparison.
# ======================================================================================================

def _match_fns():
    """From displayValue through changedFor. displayValue must be included: changedFor calls it when it
    finds a difference, so a slice starting at matchesCurrent passed the matching case and blew up on
    every differing one."""
    src = client_js()
    i = src.index("const _bsDisplayValue=")
    # They take the saved configuration as an argument since the card template was shared with the
    # logged-out page (2026-09-03); the old two-argument-free names are kept here so the cases below
    # still read as the question they are asking.
    return (src[i:src.index("const _bsWlLine=", i)]
            + "\nconst matchesCurrent=c=>bestSettingsMatchesCurrent(c,current);"
            + "\nconst changedFor=c=>bestSettingsChangedFor(c,current);")


def _matches(current, card):
    # WIN_3Y is stubbed because the slice now also carries _yrEdges/_cardYears (the rolling-year
    # breakdown added 2026-09-01), which read it. Empty is the right stub: this test is about the badge
    # and the Changes line, not the yearly figures.
    return run_js(f"const current={current}; const WIN_3Y=[]; const _combReplay=()=>({{ret:0,n:0}});",
                  _match_fns(),
                  f"[matchesCurrent({card}), changedFor({card})]")


_CARD = ("{rr:3,q:50,vs:4,rv:1.8,vw:true,atr:false,st:5,mo:20,"
         "scope:{label:'All markets'}}")
_SAME = ("{rr:3,q:50,vs:4,rv:1.8,vw:true,atr:false,st:5,mo:20,scope:'All markets'}")


def test_a_card_matching_the_saved_configuration_is_flagged():
    matched, changes = _matches(_SAME, _CARD)

    assert matched is True
    assert changes == "No change from your current configuration"


def test_a_card_differing_in_one_setting_is_not_flagged():
    """One differing value must clear the badge -- a near-match is not a match."""
    matched, changes = _matches(_SAME.replace("rr:3", "rr:5"), _CARD)

    assert matched is False
    assert "R:R" in changes


def test_the_badge_and_the_changes_line_can_never_disagree():
    """Both must come from the same comparison: a card claiming to match while listing differences
    would be worse than the small muted text it replaces."""
    for tweak in ("q:50", "vs:4", "rv:1.8", "st:5", "mo:20"):
        key, value = tweak.split(":")
        changed = _SAME.replace(tweak, f"{key}:999")
        matched, changes = _matches(changed, _CARD)
        assert matched is False, f"{key} differs but the card claims to match"
        assert changes != "No change from your current configuration"


def test_the_apply_button_says_so_rather_than_inviting_a_no_op():
    js = client_js()

    assert "Already applied" in js, "a matching card must not invite an apply that changes nothing"
    assert "fcard-current" in js and "This is your current configuration" in js
    assert ".fcard-current{" in client_source(), "the badge needs a style or it is invisible"


# ======================================================================================================
# Scanner RVOL fallback (user 2026-08-28: "Rows still has empty data e.g. RVOL!!!").
#
# Two RVOLs reach the client: `rvol`, measured on the setup's own break bar and present only for
# TRIGGERED rows, and `current_rvol`, today's value for every instrument. The Scanner rendered `rvol`
# alone, so 124 of 261 signal rows showed a dash -- and 114 of those had a live value sitting unused in
# the same payload.
#
# The fallback must be MARKED. Presenting today's RVOL as the trigger's is the clobber bug of 2026-08-17
# that this file already guards against: a value from the wrong moment is worse than a dash.
# ======================================================================================================

def _rvol_cells():
    """rvolCell through rvolScannerCell -- one slice; they are adjacent and the second calls the first."""
    js = client_js()
    i = js.index("const rvolCell=")
    return js[i:js.index("// VolumeScore cell", i)]


def _scanner_rvol(row):
    return run_js("", _rvol_cells(), f"rvolScannerCell({row})")


def test_a_triggered_row_shows_its_own_trigger_bar_rvol():
    out = _scanner_rvol("{rvol:2.4,current_rvol:0.9}")

    assert "2.4" in out and "0.9" not in out, "the trigger's own value must win"
    assert "now" not in out, "a trigger-time value must not be marked as current"


def test_a_row_without_a_trigger_rvol_falls_back_and_says_so():
    out = _scanner_rvol("{rvol:null,current_rvol:1.6,current_rvol_date:'2026-08-28'}")

    assert "1.6" in out, "the value exists in the payload and must be shown"
    assert "now" in out, "the fallback must be marked, not passed off as the trigger's"
    assert "2026-08-28" in out, "and dated, so the reader knows which bar it came from"


def test_a_row_with_neither_still_shows_a_dash():
    """FX has no reported volume, so a dash is the honest answer -- not a fabricated number."""
    out = _scanner_rvol("{rvol:null,current_rvol:null}")

    assert "—" in out and "now" not in out


def test_the_fallback_is_never_silent():
    """The whole risk: a value from the wrong moment presented as if it were the right one."""
    marked = _scanner_rvol("{rvol:null,current_rvol:1.6}")
    genuine = _scanner_rvol("{rvol:1.6,current_rvol:9.9}")

    assert "now" in marked and "now" not in genuine
    assert marked != genuine, "the two must be visually distinguishable"


# ======================================================================================================
# Scanner MCap column (user 2026-08-28: "Needs to see MCAP (to left of rvol)").
#
# A column added to one side only silently shifts every cell after it, so the header order and the row
# order are both pinned here.
#
# THESE THREE TESTS WERE GREEN WHILE THE BUG WAS LIVE, and how is the point. Every one of them located
# "the Scanner header" with html.index('data-pk="name"') -- but the Scanner's headings are data-k, and
# data-pk is MY PRE-ORDERS. So they read a different table's header, one that does carry a data-pk="mcap"
# heading, and asserted happily about it. The Scanner shipped 27 cells under 26 headings from 2026-08-29
# until the requester reported it on 2026-09-04: RVOL titled the market cap, VWAP titled RVOL, and so on
# to the far right of the table.
#
# So the Scanner header is now found through the Scanner's OWN tbody, and _scanner_header asserts it
# really is that table before returning it. A test that silently reads the wrong element is worse than no
# test: it occupies the place where a real guard would have gone.
# ======================================================================================================

def test_every_client_javascript_file_parses():
    """THE BUG THIS PREVENTS, and it reached production on 2026-09-04.

    A comment was written INSIDE the My Pre-orders template literal, and the pair of backticks in it
    closed the literal early. app.js stopped parsing, so the entire client died: no tab responded and two
    tabs' content rendered on top of each other. Reported from both Chrome and Edge.

    The whole suite stayed green through it. test_performance_inline_javascript_parses checks the
    <script> blocks inside index.html, and every other check reads app.js as TEXT or extracts one
    function from it -- so nothing ever asked Node whether the 4,800-line file it all comes from is
    valid JavaScript. That is what this does, for each client file, whole.
    """
    for path in (APP_JS, BEST_SETTINGS_JS):
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=30)

        assert result.returncode == 0, f"{path.name} is not valid JavaScript:\n{result.stderr}"


def _th_count(header):
    """Headings in a <thead> slice. NOT header.count("<th"), which also counts the <thead> tag itself."""
    return len(re.findall(r"<th[ >]", header))


def _header_for(html, tbody_id, marker):
    """The <thead> belonging to `tbody_id`, verified by a heading only that table has."""
    i = html.index(f'<tbody id="{tbody_id}"')
    start = html.rindex("<thead", 0, i)
    header = html[start:html.index("</thead>", start)]
    assert marker in header, f"this is not the {tbody_id} header"
    return header


def _scanner_header():
    """The Scanner's <thead>, located from its own <tbody id="rows"> so it cannot be another table's."""
    return _header_for(client_source(), "rows", 'data-k="dist_entry"')


def test_the_scanner_shows_mcap_immediately_left_of_rvol():
    order = re.findall(r'data-k="([a-z0-9_]+)"', _scanner_header())

    assert "mcap" in order, "the MCap column is missing from the Scanner header"
    assert order.index("mcap") == order.index("rvol") - 1, (
        f"MCap must sit immediately left of RVOL; order is {order[:6]}")


def test_every_scanner_heading_has_a_cell_under_it():
    """THE BUG THIS PREVENTS. Counting the two sides against each other is the only check that catches a
    column added to one side alone; every per-column assertion still passes while the table is shifted."""
    header = _scanner_header()
    js = client_js()
    j = js.index("rvolScannerCell(r)")
    row = js[js.rindex("`<tr", 0, j):js.index("</tr>", j)]

    # The row opens with _favCell(r.ticker), a helper that emits the ★ cell, so it is one <td> short of
    # the rendered column count.
    cells = len(re.findall(r"<td", row)) + 1

    assert cells == _th_count(header), (
        f"the Scanner renders {cells} cells under {_th_count(header)} headings; "
        "every column after the extra one displays under its neighbour's title")


def test_the_scanner_empty_row_spans_the_whole_table():
    """A short colspan is the cheap tell that a column was added to the row and nowhere else."""
    html = client_source()
    i = html.index('<tbody id="rows"')
    span = int(re.search(r'colspan="(\d+)"', html[i:i + 400]).group(1))
    headings = _th_count(_scanner_header())

    assert span == headings, (
        f'the "no setups" row spans {span} columns against {headings} headings')


def test_the_scanner_row_renders_mcap_in_the_same_position():
    js = client_js()
    j = js.index("rvolScannerCell(r)")
    row = js[js.rindex("`<tr", 0, j):js.index("</tr>", j)]
    cells = re.findall(r"<td[^>]*>\$\{[^}]*", row)

    mcap_at = next(i for i, c in enumerate(cells) if "_mcapFmt(r.mcap)" in c)
    rvol_at = next(i for i, c in enumerate(cells) if "rvolScannerCell(r)" in c)

    assert mcap_at == rvol_at - 1, "the row must place MCap where the header says it is"


def test_the_ig_positions_table_shows_mcap_next_to_market():
    """user 2026-09-04: "open transactions in IG still does not show MCAP (next to market)"."""
    order = re.findall(r'data-igp="([a-z0-9_]+)"', _header_for(client_source(), "ig-pos-rows", 'data-igp="profit_pct"'))

    assert "mcap" in order, "the MCap column is missing from the IG positions header"
    assert order.index("mcap") == order.index("market") + 1, (
        f"MCap must sit immediately right of Market; order is {order[:6]}")


def test_both_ig_position_rows_and_the_header_stay_in_step():
    """This table paints TWO row shapes into one header -- an open position and a closed trade -- so a
    column added to one alone shifts only half the table, which is harder to spot than shifting all of it.
    Both are counted, and the totals row is counted through its colspans for the same reason."""
    html, js = client_source(), client_js()
    headings = _th_count(_header_for(html, "ig-pos-rows", 'data-igp="profit_pct"'))
    j = js.index('$("ig-pos-rows").innerHTML=renderedRows')
    block = js[js.rindex("const renderedRows", 0, j):j]
    rows = [m.group(1) for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", block, re.S) if "colspan" not in m.group(1)]

    assert len(rows) == 2, f"expected the open and closed row templates, found {len(rows)}"
    for n, row in enumerate(rows):
        assert len(re.findall(r"<td", row)) == headings, (
            f"row template {n} renders {len(re.findall(r'<td', row))} cells under {headings} headings")

    total = re.search(r"const totalRow=.*?</tr>", js, re.S).group(0)
    spanned = sum(int(m) for m in re.findall(r'colspan="(\d+)"', total))
    plain = len(re.findall(r"<td(?![^>]*colspan)", total))

    assert spanned + plain == headings, (
        f"the totals row covers {spanned + plain} columns against {headings} headings, "
        "so the total lands under the wrong heading")


def test_the_preorders_header_and_row_stay_in_step():
    """The My Pre-orders table, which is what data-pk marks.

    This test used to take THIS header and the SCANNER's row and pin their difference at a constant 2 --
    two unrelated tables, so the "constant" was arbitrary and absorbed the Scanner's missing heading
    exactly. Each table is now measured against its own row.
    """
    html, js = client_source(), client_js()
    header = _header_for(html, "po-rows", 'data-pk="mcap"')
    j = js.index('$("po-rows").innerHTML')
    row = js[js.index("<tr", j):js.index("</tr>", j)]
    cells = len(re.findall(r"<td", row)) + (1 if "_favCell" in row else 0)   # the ★ cell comes from a helper

    assert cells == _th_count(header), (
        f"My Pre-orders renders {cells} cells under {_th_count(header)} headings")


# ======================================================================================================
# The three-year card may be APPLIED below the evidence rule (user 2026-08-28, raised twice: a card
# showing +183.6% on 122 funded trades still had no Apply button).
#
# The rule -- more than 125 funded trades, and within 20% of the best card's return -- decides whether
# this is a RECOMMENDATION. Withholding the control silently is not the same as advising against it.
# ======================================================================================================

def _three_year_status_card(auth, funded=122):
    """The three-year card BELOW its evidence rule, rendered for real at the given auth state."""
    stubs = f"""
      const AUTH={auth};
      const _esc=s=>String(s??"");
      const _bsPct=v=>(v>=0?"+":"")+(v*100).toFixed(1)+"%";
      const bestSettingsMatchesCurrent=()=>false;
    """
    src = _extract("_bestApplyRow") + "\n" + _extract("_bestThreeYearStatusCard")
    card = ("{loaded:true,error:'',current:null,best:{ret:1.836,dd:0.05,n:%d,"
            "settings:{rr:3,q:0,vs:0,rv:0,vw:false,atr:false,st:5,mo:20,scope:'All markets'},"
            "cfg:{limits:{},filters:{}}}}" % funded)
    return run_js(stubs, src, f"_bestThreeYearStatusCard({card})")


def test_the_three_year_card_offers_apply_below_the_evidence_rule():
    card = _three_year_status_card('"tok"')

    assert "applyConfigFromReport" in card, "a sub-threshold card must still be applicable"
    assert "belowEvidence" in card, "and must tell applyConfigFromReport that it is below the rule"
    assert '"belowEvidence":122' in card, "with the actual shortfall, so the dialog can name it"


def test_the_three_year_card_withholds_apply_when_logged_out():
    card = _three_year_status_card('""')

    assert "applyConfigFromReport" not in card and "<button" not in card
    assert "Log in to apply this configuration." in card


def _apply_dialog(opts_js):
    """Run applyConfigFromReport for real and return the dialog it actually built.

    Source-text assertions cannot tell "the warning is in the file" from "the warning is reached":
    flipping the guard to if(false) left every text assertion green (mutation test, 2026-08-28). So the
    function is EXECUTED with appConfirm stubbed to capture its arguments and decline, which stops the
    run before the fetch.
    """
    stubs = """
      const AUTH="tok", USER_FILTERS={};
      let SEEN=null;
      const appConfirm=async(msg,o)=>{SEEN={msg,...o};return false;};
      const _applyBtnState=()=>{}, _applyBanner=()=>{}, fetch=()=>{throw new Error("must not POST");};
    """
    call = (f'(async()=>{{await applyConfigFromReport({{min_quality:60,min_rvol:1.5}},null,{opts_js});'
            f'return SEEN;}})()')
    return run_js_async(stubs, _extract("applyConfigFromReport"), call)


def test_the_evidence_shortfall_reaches_the_dialog():
    """Applying it is allowed; applying it unknowingly is not."""
    dlg = _apply_dialog("{belowEvidence:122}")
    text = json.dumps(dlg)

    assert "122" in text, "the dialog must name the actual trade count, not a generic warning"
    assert "125" in text, "and the rule it falls short of"
    assert any("Evidence" in str(r[0]) for r in dlg["rows"]), "the shortfall belongs in the dialog rows"


def test_a_supported_configuration_is_not_labelled_below_the_rule():
    """The warning must be conditional, or every Apply cries wolf."""
    dlg = _apply_dialog("undefined")
    text = json.dumps(dlg)

    assert "125" not in text and "Evidence" not in text, (
        "a configuration that meets the evidence rule must not carry the shortfall warning")


def test_the_card_still_says_it_is_not_a_recommendation():
    """The advice must survive the button appearing next to it."""
    js = client_js()

    assert "this is information, not a recommendation" in js


# ======================================================================================================
# "All markets" must not claim markets the replay never saw (user 2026-08-28: the Balanced card said
# All markets while Shanghai was switched off in settings).
#
# Every card is computed from decisionRows, which tradeVisible has ALREADY filtered by the user's
# Markets (User) switches. So the scope means "no FURTHER market restriction", never "every market".
# ======================================================================================================

def _scope_label(markets_off):
    """The card's market-scope label, built the way the card builds it.

    _mktOff now derives from the ROWS actually replayed, via tradeExcludedValues -> tradeVisible, rather
    than re-reading MARKETS_OFF. So the stub supplies a WIN population containing every market, and the
    switches decide which of them come back as excluded.
    """
    js = client_js()
    src = (_extract("tradeVisible") + "\n" + _extract("tradeExcludedValues") + "\n"
           + "\n".join(re.search(rf"const {n}=[^\n]+", js).group(0) for n in ("marketsOff", "_allLabel")))
    rows = '[{market:"FTSE 100"},{market:"Shanghai"},{market:"Crypto"}]'
    return run_js(f"let MARKETS_OFF=new Set({markets_off}); let MARKETS_DISABLED=new Set([]);"
                  f"let TRADE_HIDE={{}}; const WIN={rows}, WIN_3Y=[];",
                  src, "_allLabel")


def test_the_scope_says_all_markets_only_when_none_are_off():
    assert _scope_label("[]") == "All markets"


def test_the_scope_admits_when_markets_are_switched_off():
    label = _scope_label("['Shanghai','Crypto']")

    assert label != "All markets", "claiming All markets while two are excluded is the reported bug"
    assert "2 off" in label


# ======================================================================================================
# Tick/cross cells: one definition, green tick and red cross, everywhere (user 2026-08-30).
#
# The Scanner, New orders and Squeeze History tables each carried their own UNCOLOURED copy of the same
# ternary while _tickCross sat top-level, already correct and already coloured. Same shape as the "All
# markets" label defect: the right code existed and was not reused, so the copies drifted.
# ======================================================================================================

def _tick(value_js):
    src = re.search(r"const _tickCross=[^\n]+", client_js()).group(0)
    return run_js("", src, f"_tickCross({value_js})")


def test_a_true_metric_renders_a_green_tick():
    html = _tick("true")

    assert "\u2713" in html
    assert "var(--bull)" in html, "a tick must be GREEN"


def test_a_false_metric_renders_a_red_cross():
    html = _tick("false")

    assert "\u2717" in html
    assert "var(--bear)" in html, "a cross must be RED"


def test_a_missing_metric_is_neither():
    """A metric we never recorded is UNKNOWN. Rendering it as a cross would assert a failure we
    never measured -- the same rule the order/filter audit follows."""
    for v in ("null", "undefined"):
        html = _tick(v)
        assert "\u2717" not in html and "\u2713" not in html, f"{v} must not claim a result"
        assert "muted" in html


def test_no_table_cell_hand_rolls_its_own_tick_cross():
    """The guard that stops a fifth copy appearing.

    Any <td> containing a tick must go through _tickCross. Two exemptions, both checked by hand and
    neither a boolean column: the Scanner's pre-order presence marker (a tick or nothing, no cross)
    and the 'Approve' button label on Access Requests.
    """
    # PER CELL, not per line. A line-level version of this check was written first and a mutation
    # restoring the raw ternary SURVIVED it: the same row's OTHER cell still said _tickCross, so the
    # whole line was skipped. One table row renders several cells, so the line is the wrong unit.
    offenders = []
    for i, ln in enumerate(client_js().split("\n"), 1):
        for cell in re.findall(r"<td[^>]*>.*?</td>", ln):
            if "\u2713" not in cell or "_tickCross" in cell:
                continue
            if "isPreorder" in cell or "Approve" in cell:   # presence marker / button label, not a column
                continue
            offenders.append((i, cell))

    assert not offenders, ("these table cells render a tick without the shared helper:\n" +
                           "\n".join(f"  line {i}: {s[:120]}" for i, s in offenders))


# ======================================================================================================
# The Introduction example image (user 2026-08-30: "the sample squeeze image in how it works never
# renders").
#
# /api/card/<ticker> was MEASURED on the host at HTTP 500 after 120.9 s and 120.3 s for two different
# tickers: it downloads from yfinance and renders matplotlib on the request thread. The picture is now a
# pre-rendered static file, and its failure must be visible rather than hidden.
# ======================================================================================================

def test_the_intro_image_is_a_static_file_not_a_live_render():
    html = client_source()
    tag = re.search(r"<img[^>]*id=\"intro-card\"[^>]*>", html, re.S)

    assert tag, "the Introduction example image is gone"
    assert 'src="/intro_card.png"' in tag.group(0), "it must load a pre-rendered file"
    assert "/api/card/" not in tag.group(0)


def test_nothing_points_the_intro_image_at_the_render_endpoint_any_more():
    """The old assignment lived in the data-load callback, not the markup."""
    js = client_js()

    assert not re.search(r'intro-card"\)\.src\s*=', js), (
        "the client still assigns a src to the intro image; that was the 120-second endpoint")


def test_the_intro_image_failure_is_visible():
    """A hidden failure and a page that never had a picture look identical. Four such defects have now
    been found on this site, so a silent onerror is not acceptable."""
    html = client_source()
    tag = re.search(r"<img[^>]*id=\"intro-card\"[^>]*>", html, re.S).group(0)

    assert "onerror" in tag
    assert "display='none'" not in tag.replace('"', "'"), "hiding it silently is the defect"
    assert "introCardFailed" in tag


def test_the_failure_handler_writes_a_message_the_reader_can_see():
    src = _extract("introCardFailed")

    assert "intro-card-cap" in src, "the message must land in the visible caption"
    assert "could not be loaded" in src
    assert "var(--bear)" in src, "a failure is flagged in the failure colour"


def test_the_pre_rendered_image_exists_and_is_a_real_png():
    """A test that would have caught shipping the markup without the file."""
    png = pathlib.Path(__file__).parent / "hvf_web" / "intro_card.png"

    assert png.exists(), "index.html points at /intro_card.png but the file is not in the build"
    head = png.read_bytes()[:8]
    assert head == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert png.stat().st_size > 20_000, "suspiciously small for a rendered chart"


# ======================================================================================================
# The Transaction evidence header must describe the rows ON SCREEN (user 2026-08-30: "i could see 50 rows
# in evidence and at the top i was told 50 had occured and then in card it said 17 missed").
#
# `missed` was computed from the unfiltered `proof` while the table renders `filtered`, so any chart
# filter left the header quoting a population the table no longer showed. Same class as the "All markets"
# card label. The header now also states the placed/missed split, which nothing on the page did once the
# old winners ledger table was removed in d686084.
# ======================================================================================================

def _proof_counts(filters_js, n_placed=33, n_missed=17, strip=True):
    """Run the real renderDecisionProof and read back the header it wrote."""
    rows = ("[" + ",".join(
        [f'{{placed:true,stake:0.05,r:{{trig_date:"2025-0{i%9+1}-01",ticker:"T{i}",name:"N{i}",'
         f'perf:1,quality:80,rvol:1,market:"FTSE 100",direction:"BULL",outcome:"TARGET"}}}}'
         for i in range(n_placed)] +
        [f'{{placed:false,reason:"Book full",r:{{trig_date:"2025-0{i%9+1}-01",ticker:"M{i}",name:"M{i}",'
         f'perf:1,quality:20,rvol:1,market:"DAX",direction:"BULL",outcome:"TARGET"}}}}'
         for i in range(n_missed)]) + "]")

    stubs = f"""
      const DECISION_PROOFS={{}}, LIMITED=false, PF_BE=0, WINNERS_WALLET=10000;
      let DECISION_SLICERS_OPEN=false;   // the evidence slicers start collapsed (2026-08-30)
      // The slicers now go through the shared barChart; stubbed here so this harness keeps testing the
      // HEADER counts rather than the chart component, which has its own tests.
      const barChart=(title)=>`<div class="vizbox"><h5>${{title}}</h5></div>`;
      const _esc=s=>String(s), locName=v=>v, _doBand=()=>"", $=()=>null;
      let CAPTURED="";
      // querySelector must exist: the renderer reorders columns in a queueMicrotask afterwards, and an
      // undefined method there throws AFTER the assertion has read the markup.
      const target={{style:{{}}, querySelector:()=>null, querySelectorAll:()=>[],
                    set innerHTML(v){{CAPTURED=v;}}, get innerHTML(){{return CAPTURED;}}}};
      const document={{getElementById:()=>target, querySelectorAll:()=>[]}};
      const PROOF={rows};
    """
    src = _extract("renderDecisionProof").replace("const target=$(id);if(!target)return;",
                                                  "const target=document.getElementById(id);")
    html = run_js(stubs, src,
                  f'(()=>{{renderDecisionProof("x",PROOF,{{filters:{filters_js}}});return CAPTURED;}})()')
    if not strip:
        return html          # markup, for assertions about attributes (hidden, class names)
    # Assert on the TEXT a reader sees, not the markup. The header renders "<b>50</b> triggers", so a
    # literal "50 triggers" match fails across the tags even though the screen reads exactly that --
    # which is how a correct renderer can fail a test and send you looking for a bug that is not there.
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html))


def test_the_evidence_header_states_the_placed_and_missed_split():
    html = _proof_counts("{}")

    assert "50 triggers" in html, "the header must say how many triggers the table covers"
    assert "33 placed" in html
    assert "17 missed" in html


def test_the_header_counts_follow_the_chart_filter():
    """The reported symptom: filter the charts and the header kept quoting the unfiltered total.

    The filter deliberately selects the PLACED market. Filtering to DAX instead proved nothing: all 17
    missed rows are DAX, so filtered and unfiltered `missed` are both 17 and the assertion passes either
    way -- a mutation restoring the bug survived that version of this test.
    """
    all_rows = _proof_counts("{}")
    just_ftse = _proof_counts('{market:"FTSE 100"}')   # the 33 PLACED rows; none of the missed ones
    just_dax = _proof_counts('{market:"DAX"}')         # the 17 missed rows

    assert "50 triggers" in all_rows and "33 placed" in all_rows and "17 missed" in all_rows

    # This is the discriminating case: unfiltered missed is 17, filtered missed is 0.
    assert "33 triggers" in just_ftse, "the header still counts rows the table is not showing"
    assert "33 placed" in just_ftse
    assert "0 missed" in just_ftse, "missed must be counted over the filtered rows, not the whole proof"

    assert "17 triggers" in just_dax and "0 placed" in just_dax and "17 missed" in just_dax


def test_a_narrowed_header_says_it_is_narrowed():
    just_dax = _proof_counts('{market:"DAX"}')

    assert "narrowed from 50" in just_dax, "a reduced count must say what reduced it"
    assert "narrowed" not in _proof_counts("{}"), "unfiltered must not claim to be narrowed"


# ======================================================================================================
# The Scanner must say WHY its table is short (user 2026-08-30: "i can see only 4 items in the table but
# items in sector card suggest there should be more").
#
# The counts were never wrong. MEASURED against the live 1,773-record snapshot with no filters: the table
# and the sector bars agree exactly at 346 each. The bars are BRUSHED -- counted over rows passing every
# filter except their own -- so selecting a sector leaves the other bars at full height while the table
# narrows. That is intended; not saying a filter was on is what made it look like lost rows.
# ======================================================================================================

def _scanner_filters(preamble_js):
    src = _extract("_activeScannerFilters")
    stubs = """
      const SEP="~|~";
      const FIELDS={};
      const $=id=>FIELDS[id]||(FIELDS[id]={value:""});
      function setOf(id){const el=$(id);if(!el)return null;const v=el.value||"";return v?new Set(v.split(SEP)):null;}
      let signalOnly=false;
    """ + preamble_js
    return run_js(stubs, src, "_activeScannerFilters()")


def test_nothing_is_listed_when_nothing_is_narrowing():
    assert _scanner_filters("") == []


def test_a_selected_sector_is_named():
    out = _scanner_filters('$("mf_sector").value="Technology";')

    assert out == ["Sector: Technology"]


def test_several_selections_in_one_dimension_are_all_named():
    out = _scanner_filters('$("mf_market").value="FTSE 100~|~DAX";')

    assert out == ["Market: FTSE 100, DAX"]


def test_the_squeeze_only_default_is_declared():
    """It is on by default and is what takes 1,773 scanned down to a few hundred, so it must be visible
    as a reason rather than left as an unexplained gap."""
    assert _scanner_filters("signalOnly=true;") == ["Squeeze only"]


def test_the_search_box_counts_as_narrowing():
    out = _scanner_filters('$("f_search").value=" howden ";')

    assert out == ['Search: "howden"']


def test_the_scanner_count_line_renders_the_reasons():
    js = client_js()

    assert "_activeScannerFilters()" in js, "the count line must ask what is narrowing the table"
    assert "Narrowed by" in js


# ======================================================================================================
# One derivation of "which values the user's filters exclude" (user 2026-08-30).
#
# A card said "All markets" while Shanghai was switched off. The label and the filter were separate
# implementations of one fact. The first fix re-read MARKETS_OFF/MARKETS_DISABLED, which was correct but
# still a SECOND copy -- the very shape that caused the bug. tradeExcludedValues asks tradeVisible, the
# one function that actually decides, so a rule added there is picked up everywhere automatically.
# ======================================================================================================

def _excluded(dim, values, *, markets_off=(), markets_disabled=(), trade_hide="{}"):
    src = _extract("tradeVisible") + "\n" + _extract("tradeExcludedValues")
    stubs = (f"let MARKETS_OFF=new Set({list(markets_off)!r});"
             f"let MARKETS_DISABLED=new Set({list(markets_disabled)!r});"
             f"let TRADE_HIDE={trade_hide};").replace("'", '"')
    return run_js(stubs, src, f"tradeExcludedValues({dim!r},{list(values)!r})".replace("'", '"'))


def test_nothing_is_excluded_when_no_filter_is_set():
    assert _excluded("market", ["FTSE 100", "Shanghai"]) == []


def test_a_market_the_user_switched_off_is_reported():
    assert _excluded("market", ["FTSE 100", "Shanghai"], markets_off=["Shanghai"]) == ["Shanghai"]


def test_a_market_the_admin_disabled_is_reported_too():
    """Two different switches, one rule. The label must not care which one fired."""
    assert _excluded("market", ["FTSE 100", "Crypto"], markets_disabled=["Crypto"]) == ["Crypto"]


def test_locations_are_governed_by_their_own_allow_list():
    """tradeVisible gates location through TRADE_HIDE.locations, which the Back Test summary never asked
    about -- it said "All locations" regardless."""
    out = _excluded("location", ["UK", "US", "Asia"], trade_hide='{"locations":["UK"]}')

    assert out == ["Asia", "US"], "everything outside the allow-list is excluded"


def test_one_dimension_never_contaminates_another():
    """A probe row carries only the field under test, so a switched-off market must not make a location
    look excluded. Without that isolation the counts would be nonsense.

    The value under test is deliberately one that IS switched off as a market. Using unrelated values
    proved nothing: a mutation probing every field at once survived, because "UK" and "US" are not in
    MARKETS_OFF either way and the answer was empty in both worlds.
    """
    out = _excluded("location", ["UK", "Shanghai"], markets_off=["Shanghai"])

    assert out == [], ("Shanghai is switched off as a MARKET; asked about locations the answer must be "
                       "empty, or every label built on this would over-report")


def test_an_empty_allow_list_means_no_restriction():
    assert _excluded("location", ["UK", "US"], trade_hide='{"locations":[]}') == []


def test_the_location_label_admits_when_locations_are_off():
    src = (_extract("tradeVisible") + "\n" + _extract("tradeExcludedValues") + "\n"
           + _extract("_locScopeLabel"))
    stubs = ('let MARKETS_OFF=new Set([]), MARKETS_DISABLED=new Set([]);'
             'let TRADE_HIDE={"locations":["UK"]};'
             'const uniq=()=>["UK","US","Asia"];')

    assert run_js(stubs, src, "_locScopeLabel()") == "All enabled locations (2 off)"


def test_the_location_label_says_all_only_when_all_are_on():
    src = (_extract("tradeVisible") + "\n" + _extract("tradeExcludedValues") + "\n"
           + _extract("_locScopeLabel"))
    stubs = ('let MARKETS_OFF=new Set([]), MARKETS_DISABLED=new Set([]); let TRADE_HIDE={};'
             'const uniq=()=>["UK","US","Asia"];')

    assert run_js(stubs, src, "_locScopeLabel()") == "All locations"


def test_no_label_re_implements_the_exclusion_rule():
    """The guard against a fourth copy. Only tradeVisible, the settings toggles and the config load may
    read the raw switches; every label must go through tradeExcludedValues."""
    js = client_js()
    offenders = []
    for i, ln in enumerate(js.split("\n"), 1):
        if "MARKETS_OFF" not in ln or ln.lstrip().startswith("//"):
            continue
        if any(tok in ln for tok in ("function tradeVisible", "MARKETS_OFF=new Set",
                                     "scope==='app'?MARKETS_DISABLED:MARKETS_OFF", "_mkUserSwitch")):
            continue
        if "r.market &&" in ln:          # the rule itself, inside tradeVisible
            continue
        offenders.append((i, ln.strip()[:120]))

    assert not offenders, ("these read the market switches directly instead of asking tradeVisible:\n" +
                           "\n".join(f"  line {i}: {s}" for i, s in offenders))


# ======================================================================================================
# Every Show/Hide toggle sits on the RIGHT of its count row (user 2026-08-30: "on some pages the
# HIDE/SHOW radio button is on the right and others on the left - make it consistent and have it on the
# right for all pages").
#
# House standard, index.html:692: count left, .grow spacer, control right.
#
# This check is ELEMENT-based and that is not incidental. A line-based version of it FALSE-POSITIVED on
# the IG Account row, which holds a "Close selected" button before the spacer and its radios after, and
# sent me looking for a defect that was not there. One row of markup spans several lines.
# ======================================================================================================

def _count_row_controls():
    """(line, row id, control id, side) for every input/button/select inside a <div class="count">."""
    html = client_source()
    div = re.compile(r"<div\b|</div>", re.I)

    def block_at(start):
        depth = 0
        for m in div.finditer(html, start):
            depth += 1 if m.group(0).lower().startswith("<div") else -1
            if depth == 0:
                return html[start:m.end()]
        return html[start:]

    out = []
    for m in re.finditer(r'<div class="count"', html):
        block = block_at(m.start())
        line = html[:m.start()].count("\n") + 1
        row = (re.search(r'id="([^"]+)"', block) or [None, "?"])[1]
        grow = block.find('class="grow"')
        for c in re.finditer(r"<(input|button|select)\b[^>]*>", block, re.I):
            ident = (re.search(r'(?:id|name)="([^"]+)"', c.group(0)) or [None, "?"])[1]
            out.append((line, row, ident, "right" if (grow != -1 and c.start() > grow) else "LEFT"))
    return out


def test_every_show_hide_toggle_sits_on_the_right():
    toggles = [c for c in _count_row_controls() if re.search(r"show|hide|closed-view", c[2], re.I)]

    assert toggles, "no Show/Hide toggles found at all — the check has lost its target"
    left = [t for t in toggles if t[3] == "LEFT"]
    assert not left, ("these Show/Hide toggles render on the left:\n" +
                      "\n".join(f"  index.html:{l} {row} / {ident}" for l, row, ident, _ in left))


def test_the_two_closed_order_toggles_use_the_same_control():
    """Order Ops used a checkbox in the left sidebar while IG Account used a Show/Hide radio pair on the
    right. Consistency is the control AND the position, not just the position."""
    html = client_source()

    for name in ("oo-closed-view", "ig-closed-view"):
        radios = re.findall(rf'<input type="radio" name="{name}" value="(\w+)"', html)
        assert sorted(radios) == ["hide", "show"], f"{name} is not a Show/Hide radio pair: {radios}"
    assert 'id="oo-showclosed"' not in html, "the old sidebar checkbox is still present"


def test_hiding_closed_orders_is_the_default():
    """Changing the control must not quietly change what the page shows on arrival."""
    html = client_source()
    hide_tag = re.search(r'<input type="radio" name="oo-closed-view" value="hide"[^>]*>', html).group(0)

    assert "checked" in hide_tag


def test_order_ops_reads_the_radio_not_the_old_checkbox():
    """Executed, not pattern-matched: the predicate must hide closed orders when 'hide' is selected and
    show them when 'show' is."""
    js = client_js()
    expr = re.search(r"\(document\.querySelector\('input\[name=\"oo-closed-view\"\]:checked'\)\|\|\{\}\)"
                     r"\.value==='hide'", js)
    assert expr, "paintOrderOps no longer reads the oo-closed-view radio"

    def hidden(selected):
        stub = (f'const document={{querySelector:()=>({selected})}};')
        return run_js(stub, "", expr.group(0))

    assert hidden("{value:'hide'}") is True, "'Hide' must hide closed orders"
    assert hidden("{value:'show'}") is False, "'Show' must show them"
    assert hidden("null") is False, "with no radio present, do not hide (fail open, as before)"


# ======================================================================================================
# The winners ledger table stays gone, and so does its builder (2026-08-30).
#
# The markup was removed deliberately on 2026-08-04 (d686084, which added tests asserting it stays gone)
# but the ~40-line builder was left behind guarded by if(tb)/if(tc), silently doing nothing on every
# render. Reading it cost half an hour and produced a bug report for a defect that did not exist.
# ======================================================================================================

def _code_only(js):
    """Client JavaScript with whole-line // comments dropped.

    The guard below names the removed identifiers, and the comment left in app.js EXPLAINING why they
    were removed names them too. Without this, documenting the removal would fail the test that pins it.
    """
    return "\n".join(l for l in js.splitlines() if not l.lstrip().startswith("//"))


def test_the_removed_ledger_table_has_no_builder_left_behind():
    code = _code_only(client_js())
    ghosts = [name for name in ("ordp-table", "ordp-count", "ordp-hide-missed", "ordp-missed-ctrl",
                                "ordp-missed-count", "ordp-ledger-q", "WINNERS_EVIDENCE_LIMIT",
                                "WINNERS_EVIDENCE_SHOW_ALL", "WINNERS_HIDE_MISSED",
                                "winnersToggleMissed", "winnersEvidenceShowAll", "winnersLedgerFilter")
              if name in code]

    assert not ghosts, ("code still references the deliberately-removed winners ledger table, which is "
                        f"how it became a trap the first time: {ghosts}")


def test_no_stylesheet_rule_targets_the_removed_table():
    html = pathlib.Path(__file__).parent / "hvf_web" / "index.html"

    assert "#ordp-table" not in html.read_text(encoding="utf-8"), "styling an element that cannot exist"


def test_the_performance_summary_cards_still_render():
    """The deletion must have taken the table only. paintOrdersPerf's real output is the summary card
    strip, and it is still assigned."""
    src = _extract("paintOrdersPerf")

    assert "box.innerHTML=" in src, "the summary cards are no longer painted"
    assert 'const box=$("ordp-summary")' in src
    assert "_winLedger(" in src, "the ledger is still computed - the cards read their figures from it"


# ======================================================================================================
# The Transaction evidence slicers (user 2026-08-30: "the slicers above the table are so ugly - either
# make them look good or remove them"; house style, collapsed by default was chosen).
#
# They were twelve hand-rolled cards: a 75px/bar/54px grid with ellipsised labels, no hover state, and
# flex:1 1 auto so twelve fought over one row. barChart -- the component behind ~50 chart strips here --
# already had profit mode, the eight-bar cap, the selection highlight and the clear badge. It lacked only
# a way to hook a click to something other than the shared data-fk dispatcher, which this panel does not
# use because it keeps its selection in opts.filters.
# ======================================================================================================

def test_the_slicers_use_the_shared_chart_component():
    src = _extract("renderDecisionProof")

    assert "barChart(" in src, "the slicers must use the house chart, not a private one"
    assert "grid-template-columns:75px" not in src, "the hand-rolled slicer markup is back"


def test_the_slicers_are_collapsed_on_arrival():
    """The transactions are what you came to see; the filters are one click away."""
    html = _proof_counts("{}", strip=False)
    strip_div = re.search(r'<div class="viz"[^>]*>', html).group(0)

    assert "hidden" in strip_div, "the slicer strip must start closed"
    assert "Filter transactions" in html, "and there must be a control that opens it"


def test_opening_the_slicers_reveals_them():
    """Pinning the other half: a toggle that never removes `hidden` is a button that does nothing."""
    src = _extract("renderDecisionProof")

    assert "DECISION_SLICERS_OPEN?'':' hidden'" in src, \
        "the hidden attribute must be driven by the toggle state"


def test_the_toggle_flips_the_state_and_repaints():
    src = _extract("decisionSlicersToggle")

    assert "DECISION_SLICERS_OPEN=!DECISION_SLICERS_OPEN" in src
    assert "renderDecisionProof(" in src, "flipping the flag must repaint, or nothing changes on screen"


def test_the_panel_selection_is_carried_into_the_chart():
    """barChart owns the selection highlight, but this panel keeps its selection in opts.filters. Without
    passing it across, a filtered chart would render as though nothing were selected."""
    src = _extract("renderDecisionProof")

    assert "selectedValue:filters[dim]" in src
    assert "onclickFor:" in src and "decisionProofFilter(" in src
    assert "clearOnclick:" in src, "the clear badge must clear THIS panel's filter, not a global set"


def test_barchart_still_uses_the_shared_dispatcher_for_everyone_else():
    """The extension must be opt-in. Around fifty callers rely on data-fk/data-fv and none were touched."""
    src = _extract("barChart")

    assert 'data-fk="${fk}" data-fv="${k}"' in src, "the default click path is gone"
    assert "onclickFor?" in src, "and the opt-in path is missing"


def test_the_bars_say_what_they_measure_exactly_once():
    """The old markup repeated 'achievable P&L' in all twelve titles. It states what the bars ARE -- pounds,
    not counts -- so it had to survive the rebuild, but once is enough."""
    html = _proof_counts("{}", strip=False)

    assert html.count("achievable P&amp;L") == 1


# ======================================================================================================
# Card Apply rows and the Transaction evidence controls (user 2026-08-30/31, both reported twice).
#
# .fcard-apply carries margin-top:auto (index.html:506), which only pushes to the bottom for a flex item
# of a COLUMN FLEX container. .fcard is one (index.html:304-305); .body is a plain block (index.html:311).
# Nested inside .body the rule was inert, and the four choice cards only LOOKED aligned because their
# bodies hold identical rows -- so the three-year card, whose body differs, sat at a different height.
# Matching its markup to the others (my first attempt) could never have fixed that.
# ======================================================================================================

def _body_of(card_start, card_end):
    """Markup of the card's <div class="body"> element, found by walking div DEPTH.

    Not by line, and not by counting tags on one line: a card template spans many lines and a line-based
    check has already produced a false positive on this very page.
    """
    js = client_js()
    i = js.index(card_start)
    blk = js[i:js.index(card_end, i)]
    b = blk.index('<div class="body"')
    depth = 0
    for m in re.finditer(r"<div\b|</div>", blk[b:]):
        depth += 1 if m.group(0) == "<div" else -1
        if depth == 0:
            return blk[b:b + m.end()]
    raise AssertionError("the card body never closes")


def test_the_choice_card_apply_row_is_not_buried_in_the_body():
    body = _body_of("function bestSettingsCardHTML(", "function bestSettingsUnsupportedCardHTML")

    assert "fcard-apply" not in body, \
        "margin-top:auto cannot bottom-align an element inside .body, which is a plain block"


def test_the_three_year_card_apply_row_is_not_buried_in_the_body():
    body = _body_of("function _bestThreeYearStatusCard(", "// Post-layout trimming")

    assert "fcard-apply" not in body


def test_every_apply_row_is_still_rendered_somewhere():
    """Moving it out must not lose it. Both states, both cards."""
    js = client_js()

    assert js.count("fcard-apply") >= 4, "logged-in and logged-out rows for both cards"
    assert "Log in to apply this configuration." in js
    # One builder, called from outside both card bodies (2026-09-03). Two copies could drift apart, and
    # one of them is the row the logged-out page shows.
    assert "function _bestApplyRow(" in js
    assert "${opts.apply||\"\"}" in js, "the choice card must emit its apply row outside .body"
    assert "${apply}" in js, "and so must the three-year card"


def test_the_evidence_controls_are_pinned_right_not_merely_placed_right():
    """justify-content:space-between puts a LONE item on a wrapped line at flex-start -- the left. An auto
    left margin absorbs the free space on whatever line the block lands on, so it is right-aligned
    whether or not the row wraps."""
    html = _proof_counts("{}", strip=False)

    assert "text-align:right;margin-left:auto" in html, \
        "the Hide/Show block relies on the parent's justify-content, which fails once the row wraps"


def test_the_evidence_note_wraps_instead_of_forcing_the_row_to_wrap():
    html = _proof_counts("{}", strip=False)

    assert "flex:1 1 320px;min-width:0" in html, \
        "an unconstrained text block is max-content wide and pushes the controls onto their own line"


# ======================================================================================================
# The three-year card ranks by RETURN (user 2026-09-01: "the cards are for return").
#
# It ranked by the risk-adjusted score, which on 2026-08-31 picked 109.2% over an available 161.0%: the
# two were within 3.7% of each other on SCORE while 52 percentage points apart on RETURN. A near-tie on
# the ranking metric was deciding a very different headline.
# ======================================================================================================

def test_the_three_year_card_ranks_by_return():
    src = _extract("computeBestSettings")
    m = re.search(r"bestThreeYear=threeEvaluated\.sort\(\(a,b\)=>([^)]+)\)", src)

    assert m, "the three-year ranking line is gone"
    assert m.group(1).startswith("b.ret-a.ret"), \
        f"the three-year card must rank by return first, not {m.group(1)!r}"


def test_a_tie_on_return_still_prefers_the_safer_configuration():
    """Return decides; score breaks ties. Without the fallback two equal-return configurations would be
    ordered arbitrarily, and the riskier one could win."""
    src = re.search(r"bestThreeYear=threeEvaluated\.sort\([^;]+;", client_js()).group(0)
    picked = run_js(
        "const threeEvaluated=[{ret:1.0,score:5},{ret:1.0,score:9},{ret:0.9,score:99}];"
        "let bestThreeYear=null;",
        src, "bestThreeYear.score")

    assert picked == 9, "on equal returns the higher-scoring (safer) configuration must win"


def test_return_beats_score_when_they_disagree():
    """The exact situation that produced the complaint: a lower-return configuration scoring higher."""
    src = re.search(r"bestThreeYear=threeEvaluated\.sort\([^;]+;", client_js()).group(0)
    picked = run_js(
        "const threeEvaluated=[{ret:1.092,score:22.49},{ret:1.610,score:21.69}];"
        "let bestThreeYear=null;",
        src, "bestThreeYear.ret")

    assert picked == 1.610, "the higher return must win even though it scores lower"


# ======================================================================================================
# LIMITED must FAIL CLOSED (user 2026-09-01: transaction evidence visible logged out -- "this is taboo").
#
# It defaulted to false and was only set inside the .then() of a Promise.all containing /api/records,
# measured at 25-35s logged out -- so an anonymous visitor was unrestricted for that window, and
# permanently unrestricted if the promise rejected. Observed live: LIMITED still false after a complete
# load with AUTH false.
# ======================================================================================================

def test_limited_defaults_to_restricted_for_an_anonymous_visitor():
    js = client_js()
    m = re.search(r"^let LIMITED=([^;]+);", js, re.M)

    assert m, "the LIMITED declaration is gone"
    assert m.group(1).strip() == "!AUTH", (
        f"LIMITED must be derived from AUTH so it is restrictive before any fetch resolves, not {m.group(1)!r}")


def test_the_server_answer_still_refines_it():
    """Failing closed must not mean ignoring the server: a logged-in user has to become unrestricted."""
    assert "LIMITED=!!j.limited;" in client_js()


def test_an_anonymous_visitor_is_limited_from_the_first_paint():
    """Executed, not read: with no token, LIMITED must be true immediately."""
    decl = re.search(r"^let LIMITED=[^;]+;", client_js(), re.M).group(0)

    assert run_js('const AUTH="";', decl, "LIMITED") is True
    assert run_js('const AUTH="a-token";', decl, "LIMITED") is False


# ======================================================================================================
# The logged-out Performance path must not throw (user 2026-09-01: "Annual settings could not be loaded:
# tb is not defined").
#
# MY REGRESSION, and two of my own changes compounding. Deleting the dead ledger renderer removed the
# tb/tc declarations, but two EARLY RETURNS still referenced them. Those paths only run when there are no
# rows -- which logged-out visitors now hit on every load, because /api/winners correctly returns none to
# them. The empty path was never executed by a test, so nothing caught it.
# ======================================================================================================

def _run_paint(win_js):
    """Execute paintOrdersPerf's no-data path with everything it touches stubbed. Returns any throw."""
    stubs = """
      const el = () => ({innerHTML:"", textContent:"", style:{}, classList:{toggle(){},add(){},remove(){}},
                         querySelectorAll:()=>[], querySelector:()=>null, appendChild(){}});
      const NODES = {};
      const $ = id => (NODES[id] = NODES[id] || el());
      const document = {getElementById:()=>el(), querySelectorAll:()=>[], querySelector:()=>null};
      let WINNERS_WALLET=10000, WINNERS_STAKE=0.05, WINNERS_MAXOPEN=20, MIN_TRADE=25;
      let MY_LIMITS={}, TRADE_HIDE={}, MARKETS_OFF=new Set(), MARKETS_DISABLED=new Set();
      let PF_DATE_FILTER_ENABLED=false, LIMITED=true, AUTH="";
      const pfDateOk=()=>true, _owPass=()=>true, paintWinnersDims=()=>{}, tradeVisible=()=>true;
      const _num=()=>null;
      const renderBestCombo=()=>{}, _winLedger=()=>({ledger:[],endWallet:10000});
    """ + win_js
    src = _extract("paintOrdersPerf")
    return run_js(stubs, src, '(()=>{try{paintOrdersPerf();return "ok";}'
                              'catch(e){return e.constructor.name+": "+e.message;}})()')


def test_the_no_rows_path_does_not_throw():
    """This is the exact state of a logged-out visitor now that /api/winners withholds the rows."""
    assert _run_paint("const WIN=[];") == "ok"


def test_the_null_winners_path_does_not_throw():
    """WIN is null before the fetch resolves, which every visitor passes through."""
    assert _run_paint("const WIN=null;") == "ok"


def test_no_deleted_ledger_variable_survives_in_code():
    """tb and tc were the removed table and its counter. They may appear in the comment explaining the
    removal, but never in executable code again."""
    src = _extract("paintOrdersPerf")
    code = "\n".join(l for l in src.split("\n") if not l.lstrip().startswith("//"))

    assert not re.search(r"\b(tb|tc)\b", code), "a deleted ledger variable is still referenced in code"


# ======================================================================================================
# Each card shows its return over three ROLLING 365-day windows (user 2026-09-01: "I want to see if these
# settings are only good for this year or all years").
#
# The cards are SELECTED on the last 365 days, so window 1 is IN-SAMPLE and windows 2 and 3 are the
# out-of-sample test. The configuration is held fixed and only the period changes -- otherwise it would be
# three separate optimisations and would prove nothing.
# ======================================================================================================

def _years_for(rows_js, card_js):
    # Both are consts inside computeBestSettings (hvf_web/best_settings.js). Bounded by the marker that
    # follows them rather than by "the next const": _cardYears is followed by a comment block, and a
    # lookahead for the next declaration swallowed the whole of the enclosing function's return.
    js = client_js()
    i = js.index("  const _yrEdges=")
    src = js[i:js.index("  // The serialisable summary.", i)]
    stubs = f"""
      let MIN_TRADE=25, WINNERS_WALLET=10000;
      const LEVERAGE={{fx:30,equities:5,commodities:10,indices:20}};
      function levType(r){{return "equities";}}
      const levOf=r=>LEVERAGE[levType(r)];
      const _pfExitDate=(r)=>String(r.exit_date||r.trig_date||"");
      const replay=(seq)=>({{ret:seq.length/10,n:seq.length}});
      const rows3y={rows_js};
    """
    return run_js(stubs, src, f"JSON.stringify(_cardYears({card_js}))")


def _rows(*dates):
    return "[" + ",".join(
        f'{{trig_date:"{d}",perf:1,rr:9,quality:99,volume_score:9,rvol:9,'
        f'above_vwap:true,atr_expanding:true,entry:100,stop:95,market:"M"}}' for d in dates) + "]"


CARD = ("{scope:{test:()=>true},rr:0,q:0,vs:0,rv:0,vw:false,atr:false,st:5,mo:20}")


def test_the_windows_are_365_days_not_calendar_years():
    out = json.loads(_years_for(_rows("2026-08-27"), CARD))

    assert [w["to"] for w in out] == ["2026-08-27", "2025-08-27", "2024-08-27"]
    assert [w["from"] for w in out] == ["2025-08-27", "2024-08-27", "2023-08-28"]


def test_each_window_counts_only_its_own_trades():
    """A trade must land in exactly one window, chosen by its trigger date."""
    out = json.loads(_years_for(_rows("2026-08-27", "2025-06-01", "2024-06-01"), CARD))

    assert [w["n"] for w in out] == [1, 1, 1]


def test_an_empty_window_reports_n_a_rather_than_zero_percent():
    """No trades is not a 0% year. Showing 0% would read as a flat year that was actually never traded."""
    out = json.loads(_years_for(_rows("2026-08-27"), CARD))

    assert out[0]["ret"] is not None
    assert out[1]["ret"] is None and out[1]["n"] == 0


def test_the_three_year_card_is_excluded():
    """It IS the three-year replay; a yearly breakdown of it would be circular."""
    js = client_js()

    assert 'c.label==="Best over 3 years")return ""' in js
    assert 'years:label==="Best over 3 years"?null:_cardYears(x)' in js, (
        "the summary must not carry a yearly breakdown for the three-year card either")
    assert "${_bsYearsLine(c)}" in js, "the breakdown must actually be rendered on the cards"


# ------------------------------------------------------------------------------------------------------
# The token has to be SENT, not just checked.
#
# /api/winners, -sl and -run were gated on X-Auth on 2026-09-01 and the server side was tested three ways.
# Nothing tested the caller. All four client fetches were plain `fetch("/api/winners")` with no headers,
# so the gate answered `{"rows": [], "limited": true}` to EVERYONE -- signed in or not -- and Best
# Settings rendered zero cards (measured live 2026-09-03: /api/winners?years=3 returned 0 rows without
# the header and 13,413 with it, for the same signed-in session).
#
# The call expressions are lifted verbatim and EXECUTED against a stubbed fetch, so what is asserted is
# the request the browser actually issues. A source-text check would pass on a headers object built
# wrongly; this one reads the header off the request.
# ------------------------------------------------------------------------------------------------------
_WINNERS_FETCH = re.compile(r'fetch\("/api/winners[^)]*\)')


def _winners_fetch_calls():
    calls = _WINNERS_FETCH.findall(client_js())
    assert len(calls) >= 4, f"expected the four winners fetches, found {len(calls)}: {calls}"
    return calls


def test_every_winners_fetch_sends_the_auth_token():
    stubs = textwrap.dedent("""
        const seen=[];
        const AUTH="tok", v=1, sv=0, evidenceQuery="";
        const fetch=(url,opt)=>{seen.push({url,headers:(opt&&opt.headers)||{}});
          return {then:()=>({then:()=>({catch:()=>{}}),catch:()=>{}}),catch:()=>{}};};
    """)
    body = "\n".join(f"({call});" for call in _winners_fetch_calls())

    sent = _run_node(f"{stubs}\n{body}\nconsole.log(JSON.stringify(seen));")

    assert len(sent) == len(_winners_fetch_calls())
    for req in sent:
        assert req["headers"].get("X-Auth") == "tok", (
            f"{req['url']} is issued without the token the server gate reads, so it receives no rows")


# ======================================================================================================
# The logged-out Best Settings cards (user 2026-09-03: "logged out users should see cards BUT NOT THE
# UNDERLYING EVIDENCE TABLE", and, when the first attempt did not reach the page, "i still cannot see
# cards on best settings when not logged in").
#
# The cards were computed in the browser FROM the per-trade rows, and /api/winners correctly serves none
# of those rows to an anonymous visitor -- so the page had nothing to compute from and drew nothing. The
# summaries now arrive precomputed and are rendered by the SAME card template. Two properties matter and
# both are executed here: the cards appear, and nothing that could rebuild the evidence comes with them.
# ======================================================================================================

_PUBLIC_CARD = ("{label:'Balanced',why:'why',colour:'var(--bull)',ret:1.23,dd:0.05,n:140,eligible:260,"
                "posPeriods:4,periods:4,wlEligible:{w:90,l:50,pct:64},wlActual:{w:88,l:52,pct:63},"
                "scope:{kind:'all',label:'All markets',display:'All markets',offList:[]},"
                "rr:3,q:50,vs:4,rv:0,vw:true,atr:false,st:5,mo:20,"
                "years:[{from:'2025-08-27',to:'2026-08-27',ret:1.23,n:140}]}")


def _public_grid(payload_js):
    """Run paintPublicBestCombo for real and return the markup it put on the page.

    The renderer is taken as two CONTIGUOUS slices rather than a dozen _extract() calls: the card
    template's helpers are interleaved consts and functions, and picking them out one at a time
    silently duplicated declarations until Node refused to parse (2026-09-03).
    """
    js = client_js()
    template = js[js.index("const _bsDisplayValue="):js.index("if(typeof module===")]
    a = js.index("function _bestApplyRow(")
    page = js[a:js.index('window.addEventListener("resize"', a)]
    stubs = """
      let PAINTED="";
      const $=id=>id==="ordp-bestcombo"?{set innerHTML(v){PAINTED=v;},get innerHTML(){return PAINTED;},
        querySelector:()=>null}:null;
      const requestAnimationFrame=()=>{};
      const bestCardCapacity=()=>8, bestCardMaxRows=()=>2;
      const _esc=s=>String(s??"");
      const AUTH="";
      let BEST_CHOICES=[], BEST_SELECTED="Balanced";
      const showLogin=()=>{}, selectBestChoice=()=>{}, fetch=()=>({then:()=>({then:()=>({catch:()=>{}})})});
    """
    src = "\n".join([
        re.search(r"^const _bsEsc=[^\n]*", js, re.M).group(0),
        re.search(r"^const _bsPct=[\s\S]*?';\n", js, re.M).group(0),
        template, page,
    ])
    return run_js(stubs, src, f"(()=>{{paintPublicBestCombo({payload_js});return PAINTED;}})()")


def test_a_logged_out_visitor_gets_the_cards():
    grid = _public_grid("{cards:[" + _PUBLIC_CARD + "],unsupported:[],recommended3y:true,"
                        "model:{wallet:10000,position_pct:5},data_through:'2026-08-27'}")

    assert 'data-choice="Balanced"' in grid, "the card must render for a visitor who is not signed in"
    assert "+123.0%" in grid and "140 funded of 260 eligible" in grid
    assert "Log in to apply this configuration." in grid
    assert "applyConfigFromReport" not in grid, "there is no configuration to apply to"


def test_the_logged_out_cards_bring_no_transaction_evidence():
    """THE REQUIREMENT. The grid is aggregates; the detail panel that holds the evidence is not built."""
    grid = _public_grid("{cards:[" + _PUBLIC_CARD + "],unsupported:[],recommended3y:true,model:{}}")

    assert 'id="best-detail"' not in grid, "the panel that holds the Transaction evidence must not exist"
    assert "renderDecisionProof" not in grid and "Transaction evidence" not in grid
    assert "selectBestChoice" not in grid, "a card must not offer to open evidence that is not there"
    assert grid.count("onclick") == 1, "the only control is the log-in link in the explanatory line"


def test_a_pending_recalculation_says_so_rather_than_showing_an_empty_grid():
    grid = _public_grid("{cards:[],unsupported:[],pending:true}")

    assert "recalculated" in grid, "an empty grid reads as a broken feature"
    assert "fcard" not in grid


def test_the_logged_out_page_never_runs_the_row_based_search():
    """It cannot: the rows are withheld. Delegating first is what stops it drawing an empty grid."""
    src = _extract("renderBestCombo")
    head = src[:src.index("computeBestSettings")]

    assert "if(LIMITED){renderPublicBestCombo();return;}" in head, (
        "renderBestCombo must hand over before it reaches the per-trade search")


def test_the_public_payload_is_fetched_without_a_token_and_only_summaries_are_read():
    js = client_js()
    m = re.search(r'fetch\("/api/best-settings-cards"\)', js)

    assert m, "the logged-out page must ask for the precomputed cards"
    body = js[js.index("function paintPublicBestCombo"):]
    body = body[:body.index("\n}")]
    for forbidden in (".seq", ".proof", ".rows", "trig_date", "ticker"):
        assert forbidden not in body, f"the logged-out renderer reads {forbidden}, which is evidence"


# ======================================================================================================
# The IG Account working-orders table gained MCAP / RVOL / VWAP / ATR / VolumeScore / R:R / Quality
# (user 2026-09-03). A column added to one side of a table silently shifts every cell after it, so the
# header and the row are counted against each other -- the same guard the Scanner table carries.
# ======================================================================================================

def _ig_orders_row() -> str:
    js = client_js()
    i = js.index('$("ig-ord-rows").innerHTML=ord.map')
    return js[i:js.index('.join("")', i)]


def test_the_ig_orders_header_and_row_still_line_up():
    header = re.findall(r'data-igo="([^"]+)"', client_html())
    cells = len(re.findall(r"<td", _ig_orders_row()))

    assert cells == len(header), (
        f"{cells} cells against {len(header)} headers; a column was added to one side only")
    assert re.search(r'colspan="%d"' % len(header), client_js()), (
        "the empty/loading/fault rows must span the real column count")


def test_the_metric_columns_are_actually_there():
    header = re.findall(r'data-igo="([^"]+)"', client_html())

    for column in ("mcap", "rvol", "above_vwap", "atr_expanding", "volume_score", "rr", "quality"):
        assert column in header, f"{column} is missing from the IG working-orders table"


def test_the_ig_orders_cells_use_the_shared_formatters():
    """Three tables had their own uncoloured copies of the tick/cross once; that is not repeated here."""
    row = _ig_orders_row()

    assert "_mcapFmt(o.mcap)" in row and "rvolCell(o.rvol)" in row
    assert row.count("_tickCross(") == 2, "VWAP and ATR both go through the one green/red helper"
    assert "volScoreCell(o.volume_score)" in row


# ======================================================================================================
# The breach panel pre-ticks by the rule the requester settled on (2026-09-03): every order the audit
# flags is ticked, because after break-bar measures stopped being judged the only failures left are
# durable ones and unjudgeable ones, and the requester has ruled on both. A verdict this screen has no
# ruling for must still be SHOWN -- silently dropping a new kind of failure is the worse error -- but
# never pre-ticked.
# ======================================================================================================

def _breach_panel(rows_js, orders_js):
    stubs = """
      let PANEL={style:{}}, BODY={innerHTML:""}, COUNT={innerHTML:""};
      const $=id=>id==="ig-breach-panel"?PANEL:id==="ig-breach-body"?BODY:id==="ig-breach-count"?COUNT:null;
      const _esc=s=>String(s??"");
      const disp=t=>String(t||"");
    """
    src = (f"let IG_AUDIT={{rows:{rows_js}}}, IGORD={orders_js};\n"
           + _extract("paintOrderFilterAudit"))
    return run_js(stubs, src,
                  '(()=>{paintOrderFilterAudit();return {html:BODY.innerHTML,count:COUNT.innerHTML,'
                  'shown:PANEL.style.display};})()')


_ORDERS = ('[{ticker:"AAF.L",deal_id:"D1",direction:"BUY",size:2},'
           ' {ticker:"VOD.L",deal_id:"D2",direction:"BUY",size:3},'
           ' {ticker:"DHI",deal_id:"D3",direction:"BUY",size:1}]')
# VOD.L carries a verdict the audit cannot currently produce, standing in for one added server-side after
# this screen was written. BREACH and UNKNOWN are the two the endpoint really returns.
_ROWS = ('[{ticker:"AAF.L",verdict:"BREACH",breaches:["R:R 2.88 < 5.0"],unknown:[]},'
         ' {ticker:"VOD.L",verdict:"SOMETHING_NEW",breaches:["a rule invented later"],unknown:[]},'
         ' {ticker:"DHI",verdict:"UNKNOWN",breaches:[],unknown:["R:R not recorded"]}]')


def test_durable_breaches_and_unjudgeable_orders_are_ticked():
    out = _breach_panel(_ROWS, _ORDERS)
    ticked = re.findall(r'data-cancel="(\w+)" checked', out["html"])

    assert set(ticked) == {"D1", "D3"}, (
        f"expected the BREACH and UNKNOWN orders ticked, got {ticked}")


def test_a_verdict_this_screen_has_no_ruling_for_is_shown_but_not_ticked():
    """A verdict added server-side later must not be cancelled on an old screen's assumption -- nor
    silently dropped, which would hide a live order failing a filter nobody has looked at."""
    out = _breach_panel(_ROWS, _ORDERS)

    assert 'data-cancel="D2" checked' not in out["html"], "never pre-tick an unruled verdict"
    assert 'data-cancel="D2"' in out["html"], "...but it must still be visible"


def test_the_panel_never_describes_break_bar_metrics_as_decayed():
    """user 2026-09-03: 'RVOL, ATR and VWAP are only relevant at the break'. For a PENDING order they are
    not applicable, not decayed -- and the audit stopped judging them, so a 'these decayed but you have
    accepted that' line would describe a category the screen can no longer be shown."""
    js = _extract("paintOrderFilterAudit")

    assert "STALE" not in js, "the STALE verdict is gone from the audit; the panel must not branch on it"
    assert "decay" not in js.lower(), "break-bar metrics are not applicable to a pending order, not decayed"


def test_the_count_reports_only_what_must_go():
    out = _breach_panel(_ROWS, _ORDERS)

    assert ">2</b> working orders no longer meet your settings" in out["count"]
    assert "1 flagged under a rule this screen has no ruling for" in out["count"]


def test_an_order_ig_is_no_longer_holding_is_not_offered():
    """The audit walks working_orders, which can lag a cancellation; only live orders may be ticked."""
    out = _breach_panel(_ROWS, '[{ticker:"AAF.L",deal_id:"D1",direction:"BUY",size:2}]')

    assert 'data-cancel="D1"' in out["html"]
    assert "D2" not in out["html"] and "D3" not in out["html"]


def test_the_audit_is_actually_invoked():
    """This repository's recurring defect is correct code nothing calls -- and this very panel was
    written, tested and nearly shipped with loadOrderFilterAudit defined and never called."""
    js = client_js()

    assert "loadOrderFilterAudit()" in js.replace("function loadOrderFilterAudit()", ""),         "loadOrderFilterAudit is defined but never called; the panel would never appear"
    account = _extract("renderIgAccount")
    assert account.index("paintIgAccount()") < account.index("loadOrderFilterAudit()"),         "the audit paints from IGORD, which paintIgAccount is what fills"


def test_a_clean_book_hides_the_panel():
    out = _breach_panel('[{ticker:"AAF.L",verdict:"OK",breaches:[],unknown:[]}]', _ORDERS)

    assert out["shown"] == "none"
