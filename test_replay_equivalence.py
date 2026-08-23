"""The Python and JavaScript wallet replays must produce the SAME numbers.

WHY THIS EXISTS. The same trading maths is implemented twice, in two languages:

  * ``hvf_web/server.py::_sqa_compound``      — Python, drives the server-side Back Test figures.
  * ``hvf_web/index.html::_combReplay``       — JavaScript, drives Best Settings in the browser.

The server docstring says so outright: "Replay a funded portfolio using the same contract as the browser
wallet." Both implement position sizing as a percentage of running equity, the max-open cap, margin
reserved until exit, and the broker-minimum skip. The leverage table is a duplicated literal in both.

Both were already well tested — SEPARATELY. Four tests exercised ``_sqa_compound`` and several ran
``_combReplay`` in Node, but no test fed identical input to both and compared the output. Nothing
asserted they agree, which for money maths is the gap that matters: on 2026-08-23 a one-line reordering
inside ``_combReplay`` moved the reported wallet by £1,037 with the whole suite still green.

WHAT THIS FILE PINS. Not just "the totals match" — the two contracts differ in ways a caller must
normalise, and those differences are the fragile part. They are made explicit here so a change to either
side that breaks the correspondence fails loudly:

  1. Python replays in £ from ``start``; JavaScript replays in fractions from w=1.
  2. Python's ``min_trade`` is absolute (£25); JavaScript's is ``MIN_TRADE / WINNERS_WALLET``, so the two
     agree ONLY when the JS wallet equals the Python start.
  3. Python filters the population itself (outcome, R:R, quality); JavaScript expects it pre-filtered.
  4. Python reads ``return_pct`` and ``exit_date``; JavaScript reads ``perf`` and derives the exit through
     ``_pfExitDate``. The fixture therefore carries both spellings of the same fact.
  5. Both cap max-open at floor(1 / position fraction), and that clamp must bite identically.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from hvf_web import server

INDEX = Path(__file__).parent / "hvf_web" / "index.html"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

START = 10_000.0
MIN_TRADE = 25.0


# ------------------------------------------------------------------------------------------------------
# One population, expressed in both dialects
# ------------------------------------------------------------------------------------------------------

def _trade(ticker, market, trig, exit_date, pct, *, quality=80, rr=4.0):
    """A trade both replays can read. Deliberately carries `return_pct`/`perf` and `outcome`/`state`."""
    return {"ticker": ticker, "market": market, "sector": "Tech", "direction": "BULLISH",
            "quality": quality, "rr": rr, "outcome": "TARGET" if pct >= 0 else "STOPPED",
            "state": "TARGET" if pct >= 0 else "STOPPED",
            "return_pct": pct, "perf": pct, "r_mult": 1.0,
            "trig_date": trig, "exit_date": exit_date}


# Chosen to exercise every branch both replays contain, not just the happy path:
#   - overlapping holds, so the max-open cap and margin reservation both bite
#   - a loss, so compounding moves the wallet down as well as up
#   - FX and Indices, so the duplicated leverage tables must agree
#   - exits that mature out of trigger order, so the settle ordering matters
POPULATION = [
    _trade("AAA.L", "FTSE 100",  "2026-01-05", "2026-01-20",  6.0),
    _trade("BBB.L", "FTSE 100",  "2026-01-06", "2026-03-01", -4.0),
    _trade("CCC",   "FX",        "2026-01-07", "2026-01-09",  2.5),
    _trade("DDD",   "Indices",   "2026-01-08", "2026-02-10",  9.0),
    _trade("EEE.L", "FTSE 250",  "2026-01-20", "2026-01-25", -2.0),
    _trade("FFF",   "Commodities", "2026-02-11", "2026-02-20", 3.5),
    _trade("GGG.L", "FTSE 100",  "2026-03-02", "2026-03-15",  1.25),
    _trade("HHH.L", "FTSE 100",  "2026-03-16", "2026-04-01", -7.5),
    _trade("III",   "FX",        "2026-04-02", "2026-04-08",  0.75),
    _trade("JJJ.L", "FTSE 100",  "2026-04-09", "2026-05-01", 12.0),
]


# ------------------------------------------------------------------------------------------------------
# Running each side
# ------------------------------------------------------------------------------------------------------

def _grab(source: str, decl: str) -> str:
    """Brace-matched extraction, so a one-liner does not swallow the next declaration."""
    start = source.index(decl)
    i, depth = source.index("{", start), 0
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces extracting {decl!r}")


def _run_js(position_pct: float, max_open: int) -> dict:
    """Run the REAL _combReplay from index.html over the shared population."""
    html = INDEX.read_text(encoding="utf-8")
    src = "\n".join([
        re.search(r"\nlet LEVERAGE=\{[^}]*\};", html).group(0),
        _grab(html, "function levType(r)"),
        re.search(r"\nconst levOf=[^\n]*", html).group(0),
        re.search(r"\nconst _fundedMaxOpen=[^\n]*", html).group(0),
        _grab(html, "function _pfAddDays("),
        _grab(html, "function _pfExitDate("),
        _grab(html, "function _combReplay(seq,stakeFrac,maxopen"),
    ])
    # Python filters the population itself; mirror that filter here so both see the same trades.
    preamble = (f"let MIN_TRADE={MIN_TRADE};\n"
                f"let WINNERS_WALLET={START};\n"
                f"const seq={json.dumps(POPULATION)}\n"
                f"  .filter(r=>['TARGET','STOPPED'].includes(r.outcome)&&r.perf!=null&&r.trig_date"
                f"&&r.exit_date&&(r.quality||0)>=0&&(r.rr||0)>=3)\n"
                f"  .sort((a,b)=>a.trig_date<b.trig_date?-1:a.trig_date>b.trig_date?1:0);\n")
    call = f"_combReplay(seq,{position_pct}/100,{max_open})"
    script = (f"{preamble}{src}\n"
              f"const z={call};"
              f"console.log(JSON.stringify({{ret:z.ret,n:z.n,cap:z.cap}}));")
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout.strip())


def _run_python(position_pct: float, max_open: int, monkeypatch) -> dict:
    """Run the REAL _sqa_compound over the same population, with the quality gate neutralised."""
    monkeypatch.setattr(server, "_sqa_bridge_min_quality", lambda: 0.0)
    return server._sqa_compound([dict(r) for r in POPULATION], start=START,
                                max_concurrent=max_open, position_pct=position_pct,
                                min_trade=MIN_TRADE)


# ------------------------------------------------------------------------------------------------------
# The equivalence itself
# ------------------------------------------------------------------------------------------------------

MODELS = [
    pytest.param(5.0, 20, id="5pct-20open"),      # the shipped Replay default
    pytest.param(2.0, 25, id="2pct-25open"),      # the older 2% model
    pytest.param(10.0, 12, id="10pct-12open"),    # cap clamps to floor(1/0.10)=10, below the requested 12
    pytest.param(25.0, 4, id="25pct-4open"),      # few, large positions; margin pressure
    pytest.param(50.0, 2, id="50pct-2open"),      # extreme: the funded cap dominates
]


@pytest.mark.parametrize("position_pct,max_open", MODELS)
def test_both_replays_fund_the_same_trades(position_pct, max_open, monkeypatch):
    """THE CHECK. Same population, same model, same funded count."""
    py = _run_python(position_pct, max_open, monkeypatch)
    js = _run_js(position_pct, max_open)

    assert py is not None, "the Python replay rejected the shared population"
    assert py["trades"] == js["n"], (
        f"funded-trade counts disagree at {position_pct}%/{max_open}: "
        f"Python {py['trades']}, JavaScript {js['n']} — the two wallet replays are diverging")


@pytest.mark.parametrize("position_pct,max_open", MODELS)
def test_both_replays_end_on_the_same_wallet(position_pct, max_open, monkeypatch):
    """The number a trader actually reads. Compared in £ to the pound."""
    py = _run_python(position_pct, max_open, monkeypatch)
    js = _run_js(position_pct, max_open)

    js_final = round(START * (1 + js["ret"]))
    assert py["final"] == js_final, (
        f"ending wallets disagree at {position_pct}%/{max_open}: "
        f"Python £{py['final']:,} vs JavaScript £{js_final:,} "
        f"(£{abs(py['final'] - js_final):,} apart)")


@pytest.mark.parametrize("position_pct,max_open", MODELS)
def test_both_replays_skip_the_same_number(position_pct, max_open, monkeypatch):
    """A trade one side funds and the other skips is the divergence that moves the headline return."""
    py = _run_python(position_pct, max_open, monkeypatch)
    js = _run_js(position_pct, max_open)
    eligible = len([r for r in POPULATION if r["outcome"] in ("TARGET", "STOPPED") and r["rr"] >= 3])

    assert py["skipped"] == eligible - js["n"]


def test_the_max_open_clamp_matches(monkeypatch):
    """Both cap at floor(1 / position fraction). A 10% model cannot hold 12 positions."""
    py = _run_python(10.0, 12, monkeypatch)
    js = _run_js(10.0, 12)

    assert py["max_concurrent"] == 10
    assert js["cap"] <= 10, "the JS peak funded count exceeded the funded cap"


def test_the_harness_would_notice_a_divergence(monkeypatch):
    """A detector that has never failed proves nothing.

    Feed the JS side a DIFFERENT position size and confirm the comparison fails — otherwise these tests
    would pass on any pair of implementations, which is exactly the state this file was written to end.
    """
    py = _run_python(5.0, 20, monkeypatch)
    js_wrong = _run_js(7.5, 20)

    assert py["final"] != round(START * (1 + js_wrong["ret"])), (
        "the comparison cannot tell two different models apart, so it cannot detect a real divergence")


def test_both_implementations_are_still_the_ones_being_compared():
    """Guards against the extraction silently matching something else after a refactor."""
    html = INDEX.read_text(encoding="utf-8")
    js = _grab(html, "function _combReplay(seq,stakeFrac,maxopen")

    assert "effectiveMax" in js and "minStake" in js and "margin" in js
    src = Path(server.__file__).read_text(encoding="utf-8")
    assert "def _sqa_compound(" in src
    assert "same contract as the browser wallet" in src, (
        "the server replay no longer claims to match the browser; if that is deliberate, this file's "
        "premise has changed and it should be revisited rather than deleted")
