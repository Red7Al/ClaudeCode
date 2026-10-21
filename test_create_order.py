"""The Scanner's "Create an order" button (owner 2026-10-10).

"Using the metrics on the screen for open, close etc - add a new IG order." The order must be EXACTLY the
levels the row showed: the Scanner Report's own setup (_report_setup -- possibly an older open setup than
today's scan carries), never the scan's different one, never an old Pre-orders level edit. A page whose
levels no longer match the server's view is refused, not placed at other levels. MONEY PATH: nothing here
reaches IG -- the placer is replaced and every call to it is recorded.
"""
import contextlib

import json

import pytest

import config_store
import trading_limits
from hvf_web import server

LIMITS = {**trading_limits.limit_defaults(), "min_risk_reward": 3.1, "max_risk_reward": 100.0,
          "min_quality": 45, "min_rvol": 1.7, "min_volume_score": 4, "min_instrument_value": 485e6,
          "max_instrument_value": 0, "require_above_vwap": 1, "require_atr_expanding": 1}
# Real case (test_report_trigger_setups.py): today's scan carries ARM's 29 Sep funnel, which fails the
# 1.7 RVOL floor; the report shows the still-open 18 Sep setup that passed on its trigger date.
ARM_29_SEP = {"trig_date": "2026-09-29", "direction": "BULL", "timeframe": "daily-240", "entry": 290.21,
              "stop": 278.64, "target": 518.02, "rr": 19.69, "quality": 45, "rvol": 1.08, "volume_score": 5,
              "above_vwap": True, "atr_expanding": True, "h1_date": None, "h3_date": None, "l3_date": None}
ARM_18_SEP = {**ARM_29_SEP, "trig_date": "2026-09-18", "entry": 280.5, "stop": 266.1, "target": 360.2,
              "rr": 5.77, "quality": 52, "rvol": 1.8, "volume_score": 6}
SNAP = {"generated_utc": "G", "records": [
    {"ticker": "ARM", "name": "Arm", "has_signal": True, "status": "TRIGGERED", "direction": "BULL",
     "market": "NASDAQ 100", "location": "US", "entry": 290.21, "stop": 278.64, "target": 518.02,
     "rr": 19.69, "quality": 45, "timeframe": "daily-240"}]}


@pytest.fixture
def wired(monkeypatch):
    import ig_shim
    import run_session
    placed = []
    monkeypatch.setattr(config_store, "PREORDERS_TO_IG_ENABLED", True)
    monkeypatch.setattr(server, "_load_snapshot", lambda: SNAP)
    monkeypatch.setattr(server._wu, "valid_tokens", lambda: {"tok"})
    monkeypatch.setattr(server._wu, "name_for_token", lambda t: "Alex")
    monkeypatch.setattr(server._wu, "get_subscription", lambda n: "pro")
    monkeypatch.setattr(server._wu, "get_settings", lambda n: {"pinned_overrides": {"ARM": {"entry": 1.0, "stop": 0.5, "target": 9.0}}})
    monkeypatch.setattr(server._wu, "email_for", lambda n: None)
    monkeypatch.setattr(server._wu, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(server, "_append_batch", lambda *a, **k: None)
    monkeypatch.setattr(server, "_user_trade_allows", lambda n, r: True)
    monkeypatch.setattr(server, "_user_limits", lambda s: {})
    monkeypatch.setattr(trading_limits, "user_limits", lambda n: LIMITS)
    monkeypatch.setattr(server, "_mcap_map", lambda: {"ARM": 248e9})
    monkeypatch.setattr(server, "_live_vwap_atr", lambda s: {"ARM": (True, True)})
    monkeypatch.setattr(server, "_open_trigger_setups", lambda s: {"ARM": [ARM_29_SEP, ARM_18_SEP]})
    monkeypatch.setattr(ig_shim, "session_for", lambda n=None: object())
    monkeypatch.setattr(ig_shim, "acting_session", lambda n=None: contextlib.nullcontext())
    monkeypatch.setattr(ig_shim, "place_hvf_order_from_sig",
                        lambda sig, prof, src, mult: placed.append((sig, src)) or {"status": "WORKING"})
    monkeypatch.setattr(run_session, "get_user_profile", lambda: {})
    return placed


def _post(levels):
    return server.app.test_client().post("/api/place-order", headers={"X-Auth": "tok"},
                                         json={"ticker": "ARM", "levels": levels})


def test_the_order_is_the_report_setup_on_screen_not_the_scans(wired):
    r = _post({"entry": 280.5, "stop": 266.1, "target": 360.2})
    assert r.get_json()["ok"] is True
    (sig, src), = wired
    assert (sig["hvf_h3_level"], sig["hvf_stop_level"], sig["hvf_target"]) == (280.5, 266.1, 360.2)
    assert sig["direction"] == "BUY" and src == "WEB_MANUAL"
    assert sig["rvol"] == 1.8, "the trigger-date values the report judged travel with the order"


def test_levels_that_differ_from_the_screen_are_refused_and_nothing_is_placed(wired):
    r = _post({"entry": 290.21, "stop": 278.64, "target": 518.02})     # the scan's setup, not the row's
    assert r.status_code == 409 and r.get_json()["placed"] is False
    assert wired == [], "no order may reach IG when the levels do not match the screen"


def test_no_levels_from_the_page_means_no_order(wired):
    assert _post({}).status_code == 409 and wired == []


def test_old_preorder_level_edits_never_change_an_order(wired, monkeypatch):
    monkeypatch.setattr(server, "_open_trigger_setups", lambda s: {"ARM": []})       # not a report row
    sig, from_report = server._order_sig("ARM", "Alex")
    assert from_report is False and sig["hvf_h3_level"] == 290.21, "the row's levels, not pinned_overrides"


def test_creating_orders_is_switched_on():
    assert config_store.PREORDERS_TO_IG_ENABLED is True


# ── "Create an order" goes to IG NOW, never the Pre-orders WATCHING deferral (owner 2026-10-10) ────────
# "Just be sure to NOT use the pre-order system, this renamed button is to create an order in IG".
# place_working_order defers any order further than the pre-order proximity band from entry as WATCHING
# (no IG order) for a later job to promote. The button must not; the automated bridge keeps it.

def _through_to_placement(monkeypatch, session):
    import ig_shim
    import config_store as cs
    import order_metrics
    monkeypatch.setattr(cs, "monitor_enabled", lambda session_name: True)
    monkeypatch.setattr(cs, "trade_allowed", lambda **kw: (True, ""))
    monkeypatch.setattr(cs, "cfg_num", lambda key, default: default)
    monkeypatch.setattr(order_metrics, "live_order_metrics", lambda *a, **kw: {})
    monkeypatch.setattr(trading_limits, "check_limits", lambda *a, **kw: "")
    monkeypatch.setattr(ig_shim, "get_epic", lambda tk: "UA.D.ARM.DAILY.IP")
    monkeypatch.setattr(ig_shim, "get_snapshot", lambda epic: {"bid": 250.0, "offer": 250.2})
    monkeypatch.setattr(ig_shim, "get_account_balance", lambda: {"available": 10_000.0})
    monkeypatch.setattr(ig_shim, "calculate_position_size", lambda *a, **kw: (1.0, 14.4))
    monkeypatch.setattr(ig_shim, "_lwr_cfg", lambda name: (False,))
    seen = {}
    monkeypatch.setattr(ig_shim, "place_working_order", lambda **kw: seen.update(kw) or {"updated": True})
    sig = {"ticker": "ARM", "direction": "BUY", "hvf_type": "BULLISH", "hvf_signal": "TRIGGERED",
           "hvf_h3_level": 280.5, "hvf_stop_level": 266.1, "hvf_target": 360.2, "hvf_quality": 90,
           "hvf_risk_reward": 5.77, "hvf_timeframe": "daily-240", "index": "NASDAQ 100", "location": "US"}
    ig_shim.place_hvf_order_from_sig(sig, {"name": "Alex", "user_id": "u1"}, session, 1.0)
    return seen


def test_create_an_order_is_never_deferred_as_watching(monkeypatch):
    assert _through_to_placement(monkeypatch, "WEB_MANUAL")["watch"] is False


def test_the_automatic_bridge_still_defers_far_orders(monkeypatch):
    assert _through_to_placement(monkeypatch, "WEB_BRIDGE")["watch"] is True


def test_the_watching_branch_obeys_the_switch():
    import inspect
    import ig_shim
    src = inspect.getsource(ig_shim.place_working_order)
    assert "if watch and dist_pct > proximity_pct:" in src


def test_the_route_does_not_pass_the_preorder_threshold(wired):
    import inspect
    assert "preorder_threshold_pct" not in inspect.getsource(server.api_place_order)


def test_no_order_when_the_trigger_history_cannot_be_read(wired, monkeypatch):
    """Code review 2026-10-10: the report hides TRIGGERED rows as unvetted when the history is unreadable;
    an order must not place one from the scan instead."""
    monkeypatch.setattr(server, "_open_trigger_setups", lambda s: None)
    r = _post({"entry": 290.21, "stop": 278.64, "target": 518.02})
    assert r.status_code == 400 and wired == []


def test_the_confirm_dialog_shows_the_exact_levels_sent():
    """f2() rounds to 2 dp, which misstates FX and sub-1 prices; the dialog shows the values sent."""
    js = open("hvf_web/app.js", encoding="utf-8").read()
    start = js.index("async function createOrder(")
    fn = js[start:js.index("\n}\n", start)]
    assert "f2(r.entry)" not in fn and 'ex(r.entry)' in fn and "String(+v)" in fn
    assert "Order (Operations)" not in fn, "that tab is hidden; the message must not send the owner there"


def _run_create_order(response, sel="NVDA"):
    """Run the page's createOrder in Node with IG's answer stubbed, recording what the user sees while it
    waits and after it answers."""
    from test_js_behaviour import NODE, run_js_async
    if NODE is None:
        pytest.skip("node is not installed")
    js = open("hvf_web/app.js", encoding="utf-8").read()
    start = js.index("async function createOrder(")
    fn = js[start:js.index("\n}\n", start) + 3]
    preamble = """
      const els={createorderbtn:{disabled:false,textContent:"Create an order"},orderstatus:{style:{},innerHTML:""}};
      const $=id=>els[id]; const seen=[]; const alerts=[];
      globalThis.document={body:{style:{cursor:""}}};
      globalThis.alert=m=>alerts.push(m);
      let SEL=%s; const AUTH="tok"; const IGWO=new Set(); const render=()=>{};
      const disp=t=>t; const appConfirm=async()=>true;
      const DATA=[{ticker:"NVDA",direction:"BULL",entry:190.5,stop:180,target:230,rr:3.9}];
      globalThis.fetch=()=>{seen.push({button:els.createorderbtn.textContent,disabled:els.createorderbtn.disabled,
                                        cursor:document.body.style.cursor,status:els.orderstatus.innerHTML});
                            return %s;};
    """ % (json.dumps(sel), response)
    call = ("createOrder('NVDA').then(()=>new Promise(r=>setTimeout(r,0))).then(()=>({seen,alerts,"
            "after:{button:els.createorderbtn.textContent,disabled:els.createorderbtn.disabled,"
            "cursor:document.body.style.cursor,status:els.orderstatus.innerHTML},igwo:[...IGWO]}))")
    return run_js_async(preamble, fn, call)


def test_create_order_shows_an_hourglass_then_the_status_ig_returned():
    """Owner 2026-10-21: "an hourglass would help to know processing is underway" and "a return status would
    help user know if it has gone in"."""
    out = _run_create_order('Promise.resolve({ok:true,json:()=>Promise.resolve({ok:true,status:"PENDING",placed:true})})')
    during = out["seen"][0]
    assert during["disabled"] is True and "⏳" in during["button"] and during["cursor"] == "wait"
    assert "⏳" in during["status"] and "Sending the BUY order to IG" in during["status"]
    after = out["after"]
    assert after["disabled"] is False and after["button"] == "Create an order" and after["cursor"] == ""
    assert "Order placed on IG" in after["status"] and "PENDING" in after["status"] and "✓" in after["status"]
    assert out["igwo"] == ["NVDA"] and out["alerts"] == []


def test_create_order_shows_why_it_was_not_placed():
    out = _run_create_order('Promise.resolve({ok:true,json:()=>Promise.resolve({ok:false,placed:false,error:"stop is too tight <5 pts"})})')
    assert "Not placed" in out["after"]["status"] and "stop is too tight &lt;5 pts" in out["after"]["status"]
    assert out["after"]["disabled"] is False and out["igwo"] == []


def test_create_order_says_so_when_the_server_never_answers():
    out = _run_create_order("Promise.reject(new Error('network'))")
    assert "Placement failed" in out["after"]["status"] and "Check IG" in out["after"]["status"]
    assert out["after"]["cursor"] == "" and out["after"]["disabled"] is False


def test_create_order_alerts_when_the_panel_moved_on():
    out = _run_create_order('Promise.resolve({ok:true,json:()=>Promise.resolve({ok:true,status:"PENDING"})})', sel="AAPL")
    assert len(out["alerts"]) == 1 and "Order placed on IG" in out["alerts"][0]
