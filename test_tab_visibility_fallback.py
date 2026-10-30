"""Tab Visibility survives a failed settings read (owner 2026-09-18: every tab visible "despite my settings hiding
some sections").

MEASURED 2026-10-30 on the deployed page with the owner's live payload: /api/config answering -> 10 tabs shown;
/api/config answering 500 -> 19, all 10 of his hidden tabs back, because the page load took a failed read as
"nothing hidden". These run the page load's own code in Node with the read failing."""
import json
import re

import pytest

from client_source import APP_JS
from test_js_behaviour import NODE, run_js_async

# The owner's saved choices, read from his live /api/config on 2026-10-30.
HIDDEN = ["preorders", "activity", "welcome", "whatwedo", "intro", "appendix", "fees", "mfm", "sysdocs", "terms"]


def _load_block():
    src = APP_JS.read_text(encoding="utf-8")
    m = re.search(r"\n// Resolve role first.*?\n    showTab\([^\n]*\n", src, re.S)
    assert m, "page-load settings read not found in app.js"
    d = re.search(r"\nconst _TABVIS_KEY=.*?\n  return null;\n\}\n", src, re.S)
    return (d.group(0) if d else "") + m.group(0)


def _run(config_status, remembered=None):
    store = {} if remembered is None else {"sq_tabvis:tok": json.dumps({"hidden": remembered, "shown": []})}
    pre = (
        "let AUTH='tok',ROLE,IS_ADMIN,IS_SUPPORT,HIDDEN_TABS=[],SHOWN_TABS=[],LEVERAGE,PINNED,OVERRIDES,FEATURES;"
        f"const _ls={json.dumps(store)};globalThis.localStorage={{getItem:k=>k in _ls?_ls[k]:null,setItem:(k,v)=>{{_ls[k]=v;}}}};"
        "globalThis.sessionStorage={getItem:()=>null};const $=()=>({});const tabAllowed=()=>true;"
        "let _applied=[],_done;const _fin=new Promise(r=>{_done=r;});"
        "const applyTabVisibility=()=>_applied.push([...HIDDEN_TABS]);const showTab=()=>_done();"
        "globalThis.setTimeout=()=>0;"
        f"globalThis.fetch=u=>Promise.resolve(u==='/api/me'?{{ok:true,json:()=>Promise.resolve({{name:'Alex'}})}}"
        f":{{ok:{'true' if config_status == 200 else 'false'},status:{config_status},"
        f"json:()=>Promise.resolve({{hidden_tabs:{json.dumps(HIDDEN)},shown_tabs:[]}})}});")
    return run_js_async(pre, _load_block(), "_fin.then(()=>({applied:_applied,stored:_ls['sq_tabvis:tok']||null}))")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_good_read_applies_the_choices_and_remembers_them():
    got = _run(200)
    assert got["applied"][-1] == HIDDEN
    assert json.loads(got["stored"])["hidden"] == HIDDEN


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_failed_read_keeps_the_remembered_choices_hidden():
    got = _run(500, remembered=HIDDEN)
    assert got["applied"][-1] == HIDDEN, "a failed /api/config read showed the owner's hidden tabs"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_failed_read_with_nothing_remembered_hides_nothing():
    assert _run(500)["applied"][-1] == []
