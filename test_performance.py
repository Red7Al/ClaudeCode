"""Offline regressions for Performance report calculations."""

import datetime as dt
import json
import re
import subprocess
import tempfile
from pathlib import Path

import ig_shim
from hvf_web import server


def test_performance_inline_javascript_parses():
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.I | re.S)
    for script in scripts:
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
            handle.write(script)
            path = handle.name
        try:
            subprocess.run(["node", "--check", path], check=True, capture_output=True, timeout=20)
        finally:
            Path(path).unlink(missing_ok=True)


def test_json_safe_converts_non_finite_numbers_for_browser_payloads():
    safe = server._json_safe({"rvol": float("nan"), "inf": float("inf"), "ok": 1.25,
                              "nested": [float("-inf"), 2]})
    assert safe == {"rvol": None, "inf": None, "ok": 1.25, "nested": [None, 2]}

    class ScalarWrapper:
        def item(self):
            return float("nan")

    assert server._json_safe(ScalarWrapper()) is None


def test_flask_json_provider_never_emits_non_finite_tokens():
    payload = server.app.json.dumps({"nan": float("nan"), "positive": float("inf"),
                                     "negative": float("-inf")})

    assert payload == '{"nan": null, "negative": null, "positive": null}'
    assert "NaN" not in payload
    assert "Infinity" not in payload


def test_winners_endpoint_never_emits_nan(monkeypatch):
    row = {
        "ticker": "TEST", "name": "Test", "market": "Test", "mcap": float("nan"),
        "sector": "Test", "location": "Test", "direction": "BULLISH",
        "trig_date": dt.date.today().isoformat(), "exit_date": None, "entry": 100.0,
        "stop": 90.0, "outcome": "OPEN", "return_pct": 2.0, "quality": 50.0,
        "rr": 3.0, "rvol": float("nan"),
    }
    monkeypatch.setattr(server, "_sqa_all_rows", lambda: [row])
    monkeypatch.setattr(server, "_volscore_trigger_map", lambda: {})
    monkeypatch.setattr(server, "_volscore_trigger_feature_map", lambda: {})

    response = server.app.test_client().get("/api/winners")

    assert response.status_code == 200
    assert b"NaN" not in response.data
    assert response.get_json()["rows"][0]["rvol"] is None
    assert response.get_json()["rows"][0]["mcap"] is None


def test_performance_has_dedicated_let_winners_run_tab():
    # NOTE (2026-08-07, ChangeRequest P-06 "Fix Let winners run navigation regression"): commit bb23c0f
    # (2026-08-05) removed the "Let winners run" pill and silently redirected pfPanel('run') to
    # 'settings' in the SAME commit that flipped these two assertions to match — with no change-request
    # note explaining an intentional removal, while the feature itself had just been built and marked
    # [Completed] the day before (20260805-ToDo-Claude.txt L154). The user re-filed the identical request
    # on 2026-08-07, confirming this was an accidental regression, not a deliberate removal. Restored.
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")

    assert 'data-pfpanel="run" onclick="pfPanel(\'run\')"' in html
    assert 'id="pf-panel-run" class="hidden"' in html
    assert 'if(run)run.classList.toggle("hidden",which!=="run")' in html
    assert 'if(which==="run")which="settings"' not in html
    assert 'id="pf-advanced-nav"' not in html
    assert 'Which market and signal attributes were associated with the strongest outcomes.' in html
    assert 'id="pf-pill-advanced" data-pfpanel="analysis"' in html
    assert 'id="pf-run-stop"' in html
    assert 'id="pf-run-in"' in html
    assert "winnersRunChange('pf')" in html
    assert "Let winners run is off" not in html
    assert 'const commonRows=rows.filter(r=>r.run_perf!=null)' in html
    assert '_combReplay(commonRows,WINNERS_STAKE,WINNERS_MAXOPEN,true,"perf",false)' in html
    assert '_combReplay(commonRows,WINNERS_STAKE,WINNERS_MAXOPEN,true,"run_perf",false)' in html
    assert "Evidence verdict: ${verdictTitle}" in html
    assert "function _winnerRunAttribution(plainReplay,runReplay,wallet)" in html
    assert "Evidence invalid — integrity check failed" in html
    assert "Exit-method impact · funded in both" in html
    assert "Capacity impact · funded in only one" in html
    assert "Target-lock integrity" in html
    assert "Attribution reconciliation" in html
    assert "Maximum drawdown" in html
    assert "Funded / eligible trades" in html
    assert "Missed through constraints" in html
    assert "No historical return improvement" in html
    assert "this is not evidence of improvement" in html
    assert "prefix+'-baseline-proof',plainReplay.proof" in html
    assert "prefix+'-run-proof',runReplay.proof" in html
    assert "delta>=0?'Letting winners run" not in html
    assert 'id="pf-summary-bt"' not in html
    assert "What separates the winners?</h2>" in html
    assert "What separates the winners — what's possible over 12 months" not in html
    assert 'id="ordp-ledger-q"' not in html
    assert "Every trade — oldest first, wallet after each" not in html
    assert html.index('data-pfpanel="settings"') < html.index('data-pfpanel="results"')
    assert ".doc .tclist li::marker{font-size:.65em;color:var(--muted)}" in html
    assert "top:calc(var(--hdr-h,49px) - 1px)" in html


def test_performance_best_settings_is_a_dedicated_wallet_constrained_tab():
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")

    assert 'data-pfpanel="settings" onclick="pfPanel(\'settings\')"' in html
    assert 'id="pf-panel-settings" class="hidden"' in html
    assert 'id="ordp-bestcombo"' in html
    assert html.count('id="ordp-bestcombo"') == 1
    assert 'which==="analysis"||which==="settings"' in html
    assert 'used+margin>w+1e-9' in html
    assert 'effectiveMax=maxopen>0?Math.min(maxopen,_fundedMaxOpen(stakeFrac))' in html
    assert 'All recommendation cards use an explicit numeric Max open value.' in html
    assert 'OPENS=[3,5,8,12,20,25,50]' in html
    assert 'WINNERS_MAXOPEN=50' in html
    # "Leverage & the wallet" explanatory text removed per ChangeRequest 2026-08-07 P-06.
    assert 'Leverage &amp; the wallet' not in html
    assert 'id="ordp-maxopen" type="number" min="1" step="1" value="50"' in html
    assert 'id="pfw-maxopen" type="number" min="1" step="1" value="50"' in html
    assert "Below minimum trade" in html
    assert "stake<MIN_TRADE" in html
    assert 'id="pf-backtest-settings"' in html
    assert "Back Test settings used" in html
    assert "Market scope" in html
    assert "Markets kept" not in html
    assert "⏳ Data loading…" in html
    assert ".refreshing" in html
    # Detail card follows whichever choice card is clicked (ChangeRequest 2026-08-07 P-06) — previously
    # only "best" (Balanced) ever got a detail card, hardcoded via renderDecisionProof('best-proof',
    # best.proof) inside renderBestCombo; that's now selectBestChoice(label), called for whichever of the
    # 4 choices is selected, with the proof computed lazily per-choice.
    assert "function selectBestChoice(label)" in html
    assert "let BEST_CHOICES=[], BEST_SELECTED=" in html
    assert "renderDecisionProof('best-proof',x.proof)" in html
    assert "renderDecisionProof(prefix+'-run-proof',runReplay.proof,{run:true,evidenceTitle:" in html
    assert "decisionProofFilter" in html
    assert "achievable P&amp;L" in html
    assert '"Balanced",best,"Best return relative to drawdown, with quarterly consistency included."' in html
    assert '"Growth",growth,"Highest-return alternative with a materially different configuration."' in html
    assert '"Defensive",defensive' in html
    assert '"Broad evidence",broad' in html
    assert 'trades250=_bestSettingsByFundedTrades(basePool,250)' in html
    assert 'trades500=_bestSettingsByFundedTrades(basePool,500)' in html
    assert '[">250 trades",trades250' in html
    assert '[">500 trades",trades500' in html
    assert 'data-choice-unavailable="${label}"' in html
    assert "Evidence threshold not met by the current annual dataset." in html
    assert "No supported recommendation" in html
    assert '<b style="color:var(--fg)">Changes:</b>' in html
    assert "Apply this configuration" in html
    assert 'onclick="selectBestChoice(' in html
    assert "fcard-selected" in html
    assert "function _pfMatchesCurrentConfig(r)" in html
    assert '!floor("min_risk_reward",r.rr)' in html
    assert '!floor("min_rvol",r.rvol)' in html
    assert 'r.above_vwap!==true' in html
    assert 'r.atr_expanding!==true' in html
    assert 'const markets=_pfSavedScope("market"),sectors=_pfSavedScope("sector")' in html
    assert 'all=all.filter(_pfMatchesCurrentConfig)' in html
    assert 'if(MY_LIMITS.max_position_pct!=null)set("pfw-stake",MY_LIMITS.max_position_pct)' in html
    assert '✓ Applied — Back Test updated' in html
    assert 'seq.length<20' in html
    for metric in ("r.rr", "r.quality", "r.mcap", "r.sector", "r.market", "r.rvol",
                   "r.volume_score", "r.above_vwap", "r.atr_expanding"):
        assert metric in html

    server = (Path(__file__).parent / "hvf_web" / "server.py").read_text(encoding="utf-8")
    assert 'rr["above_vwap"] = components.get("above_vwap")' in server
    assert 'rr["atr_expanding"] = components.get("atr_expanding")' in server
    assert '"above_vwap": vf.get("above_vwap")' in server
    assert '"atr_expanding": vf.get("atr_expanding")' in server
    assert "new ResizeObserver(syncStickyOffsets)" in html
    assert "return jsonify(_json_safe(_best_settings()))" in server
    assert 'id="lim-min_rvol"' in html
    assert 'id="lim-require_above_vwap"' in html
    assert 'id="lim-require_atr_expanding"' in html
    assert 'min_rvol' in server and 'require_above_vwap' in server and 'require_atr_expanding' in server
    assert 'const limits=cfg.limits||cfg, filters=cfg.filters||{}' in html
    assert 'data-pfpanel="summary"' in html and 'style="display:none"' in html
    assert 'id="best-settings-history"' in html
    assert 'function recordBestSettingsSnapshot(snapshot)' in html
    assert 'function paintBestSettingsHistory(history)' in html
    assert 'Changes from previous snapshot' in html
    assert 'data_through:String((byDate[byDate.length-1]||{}).trig_date||"").slice(0,10)' in html
    assert 'id="best-settings-personalisation"' in html
    assert '<b>Personalised using:</b>' in html
    assert 'function paintBestSettingsPersonalisation()' in html
    assert 'paintBestSettingsPersonalisation();' in html


def test_best_settings_trade_count_cards_use_strict_funded_thresholds():
    """Exercise the actual browser helper used by the >250 and >500 recommendation cards."""
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")
    match = re.search(
        r"function _bestSettingsByFundedTrades\(pool,min\)\{.*?\n\}",
        html,
        re.S,
    )
    assert match, "Best Settings funded-trade selector is missing"

    options = [
        {"id": "exact-250", "n": 250, "score": 100, "ret": 100},
        {"id": "over-250", "n": 251, "score": 8, "ret": 4},
        {"id": "better-over-250", "n": 400, "score": 9, "ret": 2},
        {"id": "exact-500", "n": 500, "score": 50, "ret": 50},
        {"id": "over-500", "n": 501, "score": 7, "ret": 3},
        {"id": "tie-higher-return", "n": 600, "score": 7, "ret": 5},
    ]
    script = (
        match.group(0)
        + f"\nconst pool={json.dumps(options)};"
        + "console.log(JSON.stringify({"
        + "over250:_bestSettingsByFundedTrades(pool,250)?.id||null,"
        + "over500:_bestSettingsByFundedTrades(pool,500)?.id||null,"
        + "none:_bestSettingsByFundedTrades(pool,1000)?.id||null"
        + "}));"
    )
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert json.loads(completed.stdout) == {
        "over250": "exact-500",
        "over500": "tie-higher-return",
        "none": None,
    }


def test_let_winners_run_verdict_requires_a_strict_return_improvement():
    """Exercise the browser verdict helper: an equal result must never be labelled an improvement."""
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")
    match = re.search(
        r"function _winnerRunComparison\(plainReplay,runReplay,wallet\)\{.*?\n\}",
        html,
        re.S,
    )
    assert match, "Let Winners Run comparison helper is missing"

    script = (
        match.group(0)
        + "\nconsole.log(JSON.stringify(["
        + "_winnerRunComparison({ret:.10,dd:.08},{ret:.15,dd:.06},1000),"
        + "_winnerRunComparison({ret:.10,dd:.08},{ret:.10,dd:.12},1000),"
        + "_winnerRunComparison({ret:.10,dd:.08},{ret:.05,dd:.10},1000),"
        + "_winnerRunComparison({ret:.10,dd:.08},{ret:.100001,dd:.08},1000)"
        + "]));"
    )
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    improved, equal, worse, below_half_penny = json.loads(completed.stdout)

    assert improved == {
        "plainFinal": 1100,
        "runFinal": 1150,
        "returnDelta": 0.04999999999999999,
        "drawdownDelta": -0.020000000000000004,
        "verdict": "improved",
    }
    assert equal["plainFinal"] == equal["runFinal"] == 1100
    assert equal["returnDelta"] == 0
    assert equal["verdict"] == "equal"
    assert worse["runFinal"] == 1050
    assert worse["returnDelta"] < 0
    assert worse["verdict"] == "worse"
    assert below_half_penny["runFinal"] - below_half_penny["plainFinal"] < 0.005
    assert below_half_penny["verdict"] == "equal"


def test_let_winners_run_attribution_separates_exit_and_capacity_effects():
    """The displayed decomposition keeps exit effects separate from funding-capacity effects."""
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")
    match = re.search(
        r"function _winnerRunAttribution\(plainReplay,runReplay,wallet\)\{.*?\n\}",
        html,
        re.S,
    )
    assert match, "Let Winners Run attribution helper is missing"
    script = (
        match.group(0)
        + "\nconst common={perf:10,run_perf:15},plainOnly={perf:20,run_perf:25},runOnly={perf:5,run_perf:30};"
        + "const plain={ret:.06,proof:[{r:common,placed:true,stake:.02},{r:plainOnly,placed:true,stake:.02},{r:runOnly,placed:false,stake:.02}]};"
        + "const run={ret:.09,proof:[{r:common,placed:true,stake:.02},{r:plainOnly,placed:false,stake:.02},{r:runOnly,placed:true,stake:.02}]};"
        + "console.log(JSON.stringify(_winnerRunAttribution(plain,run,1000)));"
    )
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True, timeout=20)
    result = json.loads(completed.stdout)

    assert result["commonCount"] == result["plainOnlyCount"] == result["runOnlyCount"] == 1
    assert abs(result["commonExitDelta"] - 1) < 1e-9
    assert abs(result["capacityDelta"] - 2) < 1e-9
    assert abs(result["totalDelta"] - 30) < 1e-9
    # Deliberately inconsistent synthetic totals prove that reconciliation is explicit and visible to tests.
    assert abs(result["reconciliation"] - 27) < 1e-9
    assert result["reconciled"] is False


def test_let_winners_run_never_gives_back_below_bull_target(monkeypatch):
    """After a bull target is reached, its simulated stop is floored at that target."""
    monkeypatch.setattr(ig_shim, "compute_trailing_stop", lambda *args, **kwargs: 108.0)
    bars = [
        (dt.date(2026, 1, 2), 111.0, 100.0, 110.0),  # target reached; stop floors at 110
        (dt.date(2026, 1, 3), 112.0, 109.0, 109.5),  # reversal exits at the target floor
    ]

    outcome, exit_price = server._run_path("BULLISH", 100.0, 90.0, 110.0, bars, 0.25)

    assert outcome == "RAN"
    assert exit_price == 110.0

    outcome, exit_price, exit_date = server._run_path(
        "BULLISH", 100.0, 90.0, 110.0, bars, 0.25, return_date=True
    )
    assert (outcome, exit_price, exit_date) == ("RAN", 110.0, "2026-01-03")


def test_let_winners_run_never_gives_back_above_bear_target(monkeypatch):
    """The target-lock invariant is mirrored correctly for bearish trades."""
    monkeypatch.setattr(ig_shim, "compute_trailing_stop", lambda *args, **kwargs: 92.0)
    bars = [
        (dt.date(2026, 1, 2), 100.0, 89.0, 90.0),   # target reached; stop floors at 90
        (dt.date(2026, 1, 3), 91.0, 88.0, 90.5),    # reversal exits at the target floor
    ]

    outcome, exit_price = server._run_path("BEARISH", 100.0, 110.0, 90.0, bars, 0.25)

    assert outcome == "RAN"
    assert exit_price == 90.0


def _portfolio_trade(trigger, exit_date, return_pct=10.0):
    return {
        "ticker": "TEST.L", "market": "FTSE 100", "sector": "Test", "direction": "BULLISH",
        "quality": 70, "rr": 4.0, "outcome": "TARGET", "r_mult": 4.0,
        "return_pct": return_pct, "trig_date": trigger, "exit_date": exit_date,
    }


def test_server_wallet_uses_stake_times_return_not_r_multiple(monkeypatch):
    monkeypatch.setattr(server, "_sqa_bridge_min_quality", lambda: 0)

    result = server._sqa_compound([_portfolio_trade("2026-01-01", "2026-01-02")],
                                  start=10_000, max_concurrent=1, position_pct=2)

    assert result["final"] == 10_020  # £200 position × 10%; never £200 × 4R = £800
    assert result["ledger"][0]["pnl"] == 20.0


def test_server_wallet_enforces_max_open_and_available_margin(monkeypatch):
    monkeypatch.setattr(server, "_sqa_bridge_min_quality", lambda: 0)
    rows = [
        _portfolio_trade("2026-01-01", "2026-01-10"),
        _portfolio_trade("2026-01-02", "2026-01-10"),
    ]

    capped = server._sqa_compound(rows, max_concurrent=1)
    no_cash = server._sqa_compound(rows, max_concurrent=2, position_pct=100,
                                   leverage={"fx": 1, "equities": 1, "commodities": 1, "indices": 1})

    assert capped["trades"] == 1 and capped["skipped"] == 1
    assert no_cash["trades"] == 1 and no_cash["skipped"] == 1


def test_server_wallet_auto_caps_max_open_from_position_size(monkeypatch):
    monkeypatch.setattr(server, "_sqa_bridge_min_quality", lambda: 0)
    rows = [
        _portfolio_trade("2026-01-01", "2026-01-10"),
        _portfolio_trade("2026-01-02", "2026-01-10"),
        _portfolio_trade("2026-01-03", "2026-01-10"),
    ]

    result = server._sqa_compound(rows, start=10_000, position_pct=50, max_concurrent=0)

    assert result["max_concurrent"] == 2
    assert result["trades"] == 2
    assert result["skipped"] == 1


def test_server_wallet_enforces_minimum_trade(monkeypatch):
    monkeypatch.setattr(server, "_sqa_bridge_min_quality", lambda: 0)

    result = server._sqa_compound([_portfolio_trade("2026-01-01", "2026-01-02")],
                                  start=1_000, position_pct=2, min_trade=25)

    assert result["trades"] == 0
    assert result["skipped"] == 1
    assert result["final"] == 1_000


def _extract_function(html, name):
    """Return the raw source of a top-level `function NAME(...){...}` block in index.html -- from its
    declaration up to (but not including) the next top-level `function` declaration. This file writes
    every top-level function starting at column 0, so "the next line starting with 'function '" is a
    reliable boundary even though this isn't a real JS parser (inline arrow functions inside the body
    don't start a line with 'function ', so they don't false-trigger the boundary)."""
    m = re.search(rf"\nfunction {re.escape(name)}\([^)]*\)\{{", html)
    assert m, f"function {name}(...) not found in hvf_web/index.html"
    start = m.start()
    nxt = html.find("\nfunction ", m.end())
    return html[start: nxt if nxt != -1 else len(html)]


def test_scanner_rerenders_after_every_my_limits_mutation():
    """Regression guard (2026-08-11, user report): the Scanner table hard-filters its rows on
    MY_LIMITS via pass() (P-01, 2026-08-11 -- "the scanner report MUST also match the user trading
    filter settings"), but an audit triggered by the user seeing stale ATR-failing rows found THREE
    separate places that change MY_LIMITS without re-rendering the Scanner table to match:
      1. saveLimits() -- the Configuration -> My Trading Filters Save button. Updated MY_LIMITS and
         re-rendered My Pre-orders + Performance, but never the Scanner table itself, so a just-saved
         floor (e.g. "Require ATR expanding") left stale rows on screen until something else (a sort
         click, a search, a page reload) happened to trigger a re-render.
      2. applyConfigFromReport() -- the Best Settings "Apply this configuration" button. Identical gap.
      3. The initial page-load boot sequence (the big Promise.all fetching /api/records + /api/config
         together) -- this NEVER loaded cfg.limits into MY_LIMITS at all (only cfg.filters/cfg.trade/
         cfg.markets_off), so MY_LIMITS stayed {} for the entire session unless the user happened to
         visit the Configuration tab (the only OTHER place that read cfg.limits). This is almost
         certainly what the user actually hit: a previously-saved "Require ATR expanding" floor from
         an earlier session had no effect on a fresh page load, no Save click involved at all.
    All three are fixed. This test guards against any of them regressing, and against a future
    MY_LIMITS-mutating function being added with the same gap. It was written because the bug shipped
    without one -- this repo's client-JS has no execution test harness, so these are static structural
    checks against the real index.html source, following the pattern already established by
    test_performance_best_settings_is_a_dedicated_wallet_constrained_tab above (assertions on function
    bodies, not a full behavioural test) -- not a substitute for one, but real coverage where none
    existed before.
    """
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")

    save_limits = _extract_function(html, "saveLimits")
    assert "if(typeof render==='function')render();" in save_limits, (
        "saveLimits() must re-render the Scanner table (render()) after saving My Trading Filters, "
        "not just renderPreorders()/_renderPerformance() -- otherwise a just-saved floor leaves stale "
        "rows on screen (user 2026-08-11)."
    )

    apply_cfg = _extract_function(html, "applyConfigFromReport")
    assert "if(typeof render==='function')render();" in apply_cfg, (
        "applyConfigFromReport() ('Apply this configuration' on a Best Settings card) must also "
        "re-render the Scanner table after copying a config into MY_LIMITS -- same gap as saveLimits()."
    )

    # Boot sequence: the Promise.all handler must load cfg.limits into MY_LIMITS BEFORE calling
    # render(), not just cfg.filters/cfg.trade/cfg.markets_off -- otherwise the Scanner's very first
    # paint of the session ignores every saved My Trading Filters floor, with no Save click involved.
    boot_start = html.index('Promise.all([fetch("/api/records"')
    boot_end = html.index("\nfunction ", boot_start)
    boot = html[boot_start:boot_end]
    assert "if(cfg&&cfg.limits){MY_LIMITS=cfg.limits" in boot, (
        "the initial-load Promise.all handler must populate MY_LIMITS from cfg.limits, like it "
        "already does for USER_FILTERS/TRADE_HIDE/MARKETS_DISABLED/MARKETS_OFF from the same response."
    )
    assert boot.index("MY_LIMITS=cfg.limits") < boot.index("render();"), (
        "MY_LIMITS must be assigned BEFORE the first render() call in the boot sequence, or the "
        "Scanner's first paint still runs against an empty {} and this test would be a false pass."
    )


def test_pass_still_hard_filters_on_my_limits_atr_and_vwap():
    """Companion to the regression guard above: confirms pass() -- the Scanner's single filter
    chokepoint -- still contains the ATR/VWAP floor checks this whole bug class depends on, so the
    re-render fixes above are guarding something real and can't quietly become a no-op if pass()
    itself is ever refactored."""
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")
    pass_fn = _extract_function(html, "pass")
    assert "if(+MY_LIMITS.require_above_vwap&&r.above_vwap===false)return false;" in pass_fn
    assert "if(+MY_LIMITS.require_atr_expanding&&r.atr_expanding===false)return false;" in pass_fn
