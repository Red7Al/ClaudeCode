"""Every save button reports its own outcome, in its own place, and nowhere else.

WHY THIS FILE EXISTS. Owner, 2026-09-26: "When a save button is run and if save has failed it is shown on
other save buttons as a failure despite them being separate buttons. This is so poor." It was not one bug
but FIVE of the same shape, all from status elements being wired to handlers by hand across two files with
nothing checking the wiring:

  1. saveExec() wrote into #cfg-msg, while #cfg-msg2 sat beside its own button UNREFERENCED (0 uses).
  2. saveFilterDefaults() wrote into #cfg-msg, which lives in a DIFFERENT panel from its button -- whoever
     clicked saw nothing, and the message appeared against the trade-filters save instead.
  3. saveTradeFilters() shared #cfg-msg with both of the above, so one failure read as three.
  4. clearFilterDefaults() had NO status element and NO failure path -- a rejected clear looked successful.
  5. saveLimits() targeted #lim-msg2, which does not exist: it belonged to the Adaptive Filters card that
     was deleted. saveStatus does .filter(Boolean), so the dangling id was dropped in silence.

THE POINT IS THE CLASS, NOT THE FIVE. Hand-wired string ids spanning two files cannot be verified by
reading them, which is how five accumulated unnoticed. They can be verified mechanically, so they are.

These tests are deliberately PRECISE rather than broad. A first version flagged four handlers that do
report correctly, and one shared element that is shared on purpose. A test that cries wolf gets ignored,
and then the real one is ignored too -- the same defect this repository fixed in its cron watcher on the
same day.
"""

import io
import re

import pytest

APP = io.open("hvf_web/app.js", encoding="utf-8").read()
HTML = io.open("hvf_web/index.html", encoding="utf-8").read()

HTML_IDS = set(re.findall(r'id="([A-Za-z0-9_\-]+)"', HTML))

# Elements app.js CREATES rather than the page declaring them: renderConfig builds one credential card per
# secret, each id="cred-msg-<secId>" (app.js:1806). An id whose prefix app.js concatenates onto is real at
# runtime even though it never appears in index.html.
# Taken ONLY from prefixes saveStatus itself concatenates -- saveStatus("cred-msg-"+secId). A first version
# scraped every concatenated string prefix in app.js, which picked up "lim-" from $("lim-"+id) (an INPUT
# field prefix) and thereby whitelisted every lim-* id, masking the real #lim-msg2 defect. A check with a
# false negative is worse than no check: it reports the class as covered when it is not.
RUNTIME_PREFIXES = tuple(re.findall(r'saveStatus\(\s*"([A-Za-z0-9_\-]+)"\s*\+', APP))

# Two handlers MAY share one status line when they are two actions on ONE setting, side by side in the same
# card -- whoever clicks either looks at the same spot. Each pair is named with its reason, so a NEW shared
# element fails and has to be justified rather than slipping in.
INTENTIONALLY_SHARED = {
    frozenset({"saveFilterDefaults", "clearFilterDefaults"}):
        "Save and Clear act on one setting, in one card, either side of #cfg-defaults-msg",
}

# Writes settings but reports through something other than a status element, each for a stated reason.
# Adding a genuinely silent handler means naming it here and defending it.
NO_STATUS_ELEMENT = {
    "renderConfig":          "reads the config, never writes it",
    "saveSlackChannel":      "per-channel toggle; reports into the Slack card via #cred-msg-Slack",
    "mkToggle":              "market on/off toggle; repaints the row it lives in",
    "applyConfigFromReport": "drives its own button label and a confirm dialog",
    "winnersParamsChange":   "live re-render of the replay model, not a save",
}


def _body(src, brace_at):
    """One function body, by matching braces.

    A regex boundary such as "the next function keyword" bleeds across nested and arrow functions, and that
    alone produced three false positives in the first version of this file.
    """
    depth, i = 0, brace_at
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[brace_at:i + 1]
        i += 1
    return src[brace_at:]


def _enclosing_function(pos):
    """Name of the function containing pos -- the last `function NAME(` whose body still encloses it."""
    best = "?"
    for m in re.finditer(r'function\s+([A-Za-z0-9_]+)\s*\([^)]*\)\s*\{', APP):
        if m.end() > pos:
            break
        if m.start() + len(_body(APP, m.end() - 1)) >= pos:
            best = m.group(1)
    return best


def _exists(i):
    return i in HTML_IDS or (bool(RUNTIME_PREFIXES) and i.startswith(RUNTIME_PREFIXES))


def _call_sites():
    """[(function, [literal ids])] for every saveStatus(...) call with literal arguments.

    One call site MAY list several ids: saveLimits drives a single logical form split across three panels
    and reports into whichever the reader is looking at. What must never happen is two DIFFERENT handlers
    writing to one element.
    """
    out = []
    for m in re.finditer(r'saveStatus\(\s*(\[[^\]]*\]|"[^"]*")\s*\)', APP):
        ids = re.findall(r'"([^"]*)"', m.group(1))
        if not all(re.fullmatch(r'[A-Za-z0-9_\-]+', i) for i in ids):
            continue                       # assembled at runtime; not checkable from source
        out.append((_enclosing_function(m.start()), ids))
    return out


CALL_SITES = _call_sites()


def test_there_are_call_sites_to_check():
    """A regex that silently matched nothing would make every test below pass vacuously."""
    assert len(CALL_SITES) >= 8, CALL_SITES


@pytest.mark.parametrize("fn,ids", CALL_SITES, ids=[f for f, _ in CALL_SITES])
def test_every_status_target_exists(fn, ids):
    """DEFECT 5. saveStatus drops missing elements via .filter(Boolean), so a target that does not exist is
    not an error -- it is silence. #lim-msg2 went that way when the card owning it was deleted."""
    missing = [i for i in ids if not _exists(i)]
    assert not missing, f"{fn}() writes to {missing}, which exist neither in index.html nor at runtime"


def test_no_two_handlers_share_a_status_element():
    """DEFECTS 1-3, THE BLEED THE OWNER REPORTED. Separate buttons must report separately: two handlers on
    one element make a failure in either indistinguishable from a failure in the other."""
    owner, clashes = {}, []
    for fn, ids in CALL_SITES:
        for i in ids:
            if i in owner and owner[i] != fn and frozenset({owner[i], fn}) not in INTENTIONALLY_SHARED:
                clashes.append(f"#{i} written by both {owner[i]}() and {fn}()")
            owner[i] = fn
    assert not clashes, "separate save buttons sharing one status line:\n  " + "\n  ".join(clashes)


def test_no_status_element_is_orphaned():
    """DEFECT 1 FROM THE OTHER SIDE. #cfg-msg2 sat beside the execution-switches button with zero
    references while that button reported into another card. An unreferenced status element is either a
    save reporting in the wrong place, or dead markup."""
    orphans = sorted(i for i in HTML_IDS if "msg" in i.lower() and i not in APP)
    assert not orphans, f"status elements in index.html that no code writes to: {orphans}"


def test_every_settings_save_reports_an_outcome():
    """DEFECT 4. clearFilterDefaults() posted to /api/config and reported nothing either way, so a rejected
    clear was invisible. Any handler that writes user settings must say what happened.

    Any status surface counts, not only saveStatus -- winnersSaveDefaults writes $("ordp-save-msg")
    directly, which informs the reader just as well.
    """
    silent = []
    for m in re.finditer(r'function\s+([A-Za-z0-9_]+)\s*\([^)]*\)\s*\{', APP):
        name = m.group(1)
        body = _body(APP, m.end() - 1)
        if name in NO_STATUS_ELEMENT or '"/api/config"' not in body:
            continue
        if not ("saveStatus(" in body or re.search(r'\$\("[A-Za-z0-9_\-]*msg[0-9]*"\)', body)):
            silent.append(name)
    assert not silent, f"these write settings but report no outcome: {silent}"
