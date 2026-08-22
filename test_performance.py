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


def test_current_rvol_uses_latest_usable_volume_bar(monkeypatch):
    """A latest close with null volume must not blank an otherwise well-covered equity."""
    dates = [dt.date(2026, 8, day) for day in range(1, 9)]
    bars = [(day, 11.0, 9.0, 10.0, 100) for day in dates[:-1]]
    bars.append((dates[-1], 11.0, 9.0, 10.0, None))

    class DummyDb:
        def close(self):
            pass

    import db_pool
    monkeypatch.setattr(db_pool, "get_db", lambda: DummyDb())
    monkeypatch.setattr(server, "_perf_bars", lambda db, cutoff, lookback_days=0: {"TEST": bars})
    monkeypatch.setattr(server, "_LIVE_INSTRUMENT_METRICS_CACHE", {"gen": None, "data": {}})
    metrics = server._live_instrument_metrics({"generated_utc": "test", "records": [{"ticker": "TEST"}]})

    assert metrics["TEST"]["rvol"] == 1.0
    assert metrics["TEST"]["rvol_date"] == "2026-08-07"
    assert metrics["TEST"]["status"] == "complete_latest_volume_bar"


def test_three_year_winners_request_uses_three_year_trigger_features(monkeypatch):
    """Older evidence must be enriched from its own review window, not a 12-month cache."""
    row = {
        "ticker": "OLDER", "name": "Older", "market": "Test", "mcap": None,
        "sector": "Test", "location": "Test", "direction": "BULLISH",
        "trig_date": (dt.date.today() - dt.timedelta(days=500)).isoformat(), "exit_date": None,
        "entry": 100.0, "stop": 90.0, "outcome": "OPEN", "return_pct": 2.0,
        "quality": 50.0, "rr": 3.0, "rvol": 1.5,
    }
    calls = []
    monkeypatch.setattr(server, "_sqa_all_rows", lambda: [row])
    monkeypatch.setattr(server, "_volscore_trigger_map",
                        lambda years=1: calls.append(("score", years)) or {("OLDER", row["trig_date"]): 9})
    monkeypatch.setattr(server, "_volscore_trigger_feature_map",
                        lambda years=1: calls.append(("feature", years)) or
                        {("OLDER", row["trig_date"]): {"above_vwap": True, "atr_expanding": True}})

    response = server.app.test_client().get("/api/winners?years=3")

    assert response.status_code == 200
    assert calls == [("score", 3), ("feature", 3)]
    assert response.get_json()["rows"][0]["volume_score"] == 9
    assert response.get_json()["rows"][0]["above_vwap"] is True


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
    # RESOLVED trades only since 2026-08-16. An open position's baseline is a mark-to-market on money
    # still at risk, so comparing it with a simulated realised exit is not like-for-like -- and it can
    # show a "loss" that breaks the invariant a banked target makes impossible.
    assert "const commonRows=rows.filter(r=>r.run_perf!=null&&r.outcome!=='OPEN'" in html
    # Scopeable to a Best Settings recommendation's own population (2026-08-16) — testing the exit theory
    # across the whole tradeable universe answers a question nobody asked.
    assert 'id="pf-run-scope"' in html
    assert "function _syncRunScopeOptions(prefix)" in html
    assert "const scoped=BEST_CHOICES.find(c=>c[0]===scopeLabel);" in html
    assert "const openRows=rows.filter(r=>r.run_perf!=null&&r.outcome==='OPEN').length;" in html
    assert 'Unresolved positions set aside' in html
    # The invariant is now checked across every resolved trade, not only target-hitters.
    assert "const back=commonRows.filter(r=>r.run_perf<r.perf-0.01).length;" in html
    assert 'Never worse than selling at target' in html
    assert '_combReplay(commonRows,WINNERS_STAKE,WINNERS_MAXOPEN,true,"perf",false)' in html
    assert '_combReplay(commonRows,WINNERS_STAKE,WINNERS_MAXOPEN,true,"run_perf",false)' in html
    assert "Evidence verdict: ${verdictTitle}" in html
    assert "function _winnerRunAttribution(plainReplay,runReplay,wallet)" in html
    assert "Evidence invalid — integrity check failed" in html
    assert "Exit-method impact · funded in both" in html
    assert "Capacity impact · funded in only one" in html
    # "Target-lock integrity" broadened on 2026-08-16 — the row now states the full invariant across
    # every resolved trade rather than only target-hitters.
    assert "Never worse than selling at target" in html
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
    assert 'WINNERS_MAXOPEN=20' in html
    assert 'id="ordp-exposure"' in html
    assert 'How this replay uses your £:' in html
    # "Leverage & the wallet" explanatory text removed per ChangeRequest 2026-08-07 P-06.
    assert 'Leverage &amp; the wallet' not in html
    assert 'id="ordp-maxopen" type="number" min="1" step="1" value="20"' in html
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
    assert "Calculating transaction evidence for ${_esc(lbl)}" in html
    assert "requestAnimationFrame(()=>setTimeout" in html
    assert 'data-choice-return="${x.ret}"' in html
    assert 'const maxRows=innerWidth>850?2:3' in html
    assert "let BEST_CHOICES=[], BEST_SELECTED=" in html
    assert "renderDecisionProof('best-proof',x.proof)" in html
    assert "renderDecisionProof(prefix+'-run-proof',runReplay.proof,{run:true,evidenceTitle:" in html
    assert "decisionProofFilter" in html
    assert "achievable P&amp;L" in html
    assert '"Balanced",best,"Best return relative to drawdown, with quarterly consistency included."' in html
    assert '"Growth",growth,"Highest-return alternative with a materially different configuration."' in html
    assert '"Defensive",defensive' in html
    assert '"Broad evidence",broad' in html
    assert 'trades125=_bestSettingsByFundedTrades(large125,125,150)' in html
    assert 'trades250=_bestSettingsByFundedTrades(large250,250,300)' in html
    assert 'largeSampleOptions=(min,max)=>' in html
    assert 'large125=largeSampleOptions(125,150),large250=largeSampleOptions(250,300)' in html
    assert 'b.seq.length-a.seq.length' in html
    assert 'largeOpens=[10,20,35,50,100,250,400]' in html
    assert 'MIN_TRADE/Math.max(1,WINNERS_WALLET)*100' in html
    assert '[">125 trades",trades125' in html
    assert '[">250 trades",trades250' in html
    assert '">500 trades"' not in html
    assert 'data-choice-unavailable="${label}"' in html
    assert 'const netGain=xs=>xs.filter(x=>x.placed).reduce' in html
    assert 'Net gain total' in html
    assert 'Visible Net gain subtotal' in html
    assert 'Settled wallet' in html
    # Three-year Best Settings must replay the complete generated grid rather than inheriting a small
    # finalist set selected by a different objective.
    assert 'for(const c of threeYearCandidates)for(const mo of OPENS)for(const st of STAKES)' in html
    assert 'threeShortlist' not in html
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
    assert '"9999-99-99" if row.get("run_outcome") == "OPEN"' in server
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
    # ONE slot-release rule across every wallet replay on the page (user 2026-08-14, P-03 "apply
    # configuration ... PROOF BACK TEST DOES NOT SHOW THE RESULTS EXPECTED"). Best Settings, the winners
    # ledger and Back Test's ledger each had their own convention for an unresolved trade, so an applied
    # recommendation could not reproduce its own headline numbers.
    assert 'function _pfExitDate(r,runner){' in html
    assert 'const exit=_pfExitDate(r,perfKey==="run_perf");' in html
    assert html.count('const exitOf=r=>_pfExitDate(r,false);') == 2
    assert '_pfAddDays(r.trig_date,r.days_open||0)' not in html
    # Best Settings trains on the population Back Test actually replays (same per-user trade gate).
    assert 'r.perf!=null&&r.trig_date&&tradeVisible(r));' in html
    # Applying a recommendation writes every saved filter, including the ones it CLEARS, and repaints the
    # P-08 multi-select buttons - otherwise the market never visibly applied (user 2026-08-14, P-03).
    assert "if(typeof msyncAll==='function')msyncAll();" in html
    assert 'if(el.multiple)[...el.options].forEach(o=>{o.selected=want.has(o.value);});' in html
    # Sector is not an actionable scope: the live trade gate cannot enforce it (user 2026-08-14, P-05).
    assert 'topScopes("sector","Sector")' not in html
    assert 'topScopes("market","Market")' in html
    assert 'sector=x.scope.kind==="sector"' not in html


def test_best_settings_trade_count_cards_use_banded_funded_thresholds():
    """Exercise the browser helper behind the >125 (max 150) and >250 (max 300) recommendation cards.

    Banded rather than open-ended (user 2026-08-14, P-04): an open-ended floor always returned whichever
    tested configuration simply traded the most, which answers a different question from "the best setting
    at this sample size".
    """
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")
    match = re.search(
        r"function _bestSettingsByFundedTrades\(pool,min,max\)\{.*?\n\}",
        html,
        re.S,
    )
    assert match, "Best Settings funded-trade selector is missing"

    options = [
        {"id": "at-125", "n": 125, "score": 100, "ret": 100},
        {"id": "in-band-125", "n": 126, "score": 8, "ret": 4},
        {"id": "better-in-band-125", "n": 150, "score": 9, "ret": 2},
        {"id": "above-band-125", "n": 151, "score": 99, "ret": 99},
        {"id": "at-250", "n": 250, "score": 90, "ret": 90},
        {"id": "in-band-250", "n": 260, "score": 7, "ret": 3},
        {"id": "tie-higher-return", "n": 300, "score": 7, "ret": 5},
        {"id": "above-band-250", "n": 301, "score": 98, "ret": 98},
    ]
    script = (
        match.group(0)
        + f"\nconst pool={json.dumps(options)};"
        + "console.log(JSON.stringify({"
        + "band125:_bestSettingsByFundedTrades(pool,125,150)?.id||null,"
        + "band250:_bestSettingsByFundedTrades(pool,250,300)?.id||null,"
        + "uncapped:_bestSettingsByFundedTrades(pool,250)?.id||null,"
        + "none:_bestSettingsByFundedTrades(pool,1000,2000)?.id||null"
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
        "band125": "better-in-band-125",   # 126-150 only; the 151-trade 99-scorer is out of band
        "band250": "tie-higher-return",    # equal score inside 251-300, higher return wins
        "uncapped": "above-band-250",      # no ceiling supplied -> the old open-ended behaviour
        "none": None,
    }


def test_page_replays_share_one_slot_release_rule():
    """Every wallet replay on the page must free a slot on the same date (user 2026-08-14, P-03).

    An unresolved trade has no close date. It previously settled on ``_pfAddDays(trig_date, days_open||0)``
    inside ``_combReplay`` and the /api/winners rows carry no ``days_open``, so it settled on its own
    TRIGGER day - releasing its capital and booking its mark-to-market gain immediately - while Back Test's
    own ledger held the same trade to the window end.
    """
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")
    match = re.search(r"function _pfExitDate\(r,runner\)\{.*?\n\}", html, re.S)
    add_days = re.search(r"function _pfAddDays\(d,n\)\{.*?\}\n", html, re.S)
    assert match and add_days, "Canonical replay exit-date helper is missing"

    script = (
        add_days.group(0)
        + match.group(0)
        + "console.log(JSON.stringify({"
        + 'closed:_pfExitDate({exit_date:"2026-03-04"},false),'
        + 'stillOpen:_pfExitDate({trig_date:"2026-01-01",days_open:120},false),'
        + 'runnerClosed:_pfExitDate({exit_date:"2026-03-04",run_exit_date:"2026-05-06",run_outcome:"RAN"},true),'
        + 'runnerStillOpen:_pfExitDate({run_exit_date:"2026-05-06",run_outcome:"OPEN"},true),'
        + 'baselineIgnoresRunnerExit:_pfExitDate({run_exit_date:"2026-05-06",run_outcome:"RAN"},false),'
        + 'resolvedWithoutCloseDate:_pfExitDate({state:"TARGET",trig_date:"2026-01-01",days_open:120},false)'
        + "}));"
    )
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True,
                               text=True, timeout=20)

    assert json.loads(completed.stdout) == {
        "closed": "2026-03-04",
        "stillOpen": "9999-99-99",
        "runnerClosed": "2026-05-06",
        "runnerStillOpen": "9999-99-99",
        "baselineIgnoresRunnerExit": "9999-99-99",
        "resolvedWithoutCloseDate": "2026-05-01",   # same derivation Back Test's "Closed" column uses
    }


def test_best_settings_replay_keeps_an_open_trades_capital_committed():
    """Best Settings' replay must hold an unresolved trade, exactly as Back Test's ledger does.

    /api/winners rows carry no ``days_open``, so the old fallback settled every still-open trade on its own
    trigger day: the capital came straight back AND the mark-to-market gain was banked immediately, so the
    optimiser scored configurations that Back Test - which holds the same trade to the window end - could
    never reproduce (user 2026-08-14, P-03).
    """
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")
    exit_rule = re.search(r"function _pfExitDate\(r,runner\)\{.*?\n\}", html, re.S)
    replay = re.search(
        r'function _combReplay\(seq,stakeFrac,maxopen,withProof=false,perfKey="perf",compound=true\)\{.*?\n\}',
        html, re.S)
    assert exit_rule and replay, "replay helpers are missing"

    script = (
        "const MIN_TRADE=0, WINNERS_WALLET=10000;"
        "const levOf=()=>1;"
        "const _fundedMaxOpen=f=>Math.max(1,Math.floor(1/Math.max(0.000001,+f||0.000001)));"
        + exit_rule.group(0) + replay.group(0)
        + 'const seq=[{trig_date:"2026-01-01",exit_date:null,perf:33.52},'
        + '{trig_date:"2026-02-01",exit_date:"2026-02-10",perf:10}];'
        + "const z=_combReplay(seq,1,1);"
        + "console.log(JSON.stringify({funded:z.n,ret:+z.ret.toFixed(4)}));"
    )
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True,
                               text=True, timeout=20)

    # The open trade owns the only slot for the whole window, so the second trade is never funded and
    # only the first contributes. Previously both were funded and both returns were banked.
    assert json.loads(completed.stdout) == {"funded": 1, "ret": 0.3352}


def test_winner_run_replay_holds_unresolved_positions_in_both_arms():
    """Let-winners-run must not fund its two arms from different books (user 2026-08-14, P-05).

    ``_run_path`` reports a ``run_exit_date`` on the last bar it walked even when the position is still
    OPEN. Settling the runner on that date while the baseline arm of the SAME row held its capital to the
    window end handed the baseline a free capacity advantage, which is how a +33.52% baseline came to be
    compared against a -3.58% runner.
    """
    rows = [
        {"ticker": "A", "trig_date": "2026-01-01", "exit_date": None, "run_exit_date": "2026-06-30",
         "market": "Equities", "outcome": "OPEN", "run_outcome": "OPEN",
         "perf": 33.52, "run_perf": 33.52},
        {"ticker": "B", "trig_date": "2026-02-01", "exit_date": "2026-02-10",
         "run_exit_date": "2026-02-10", "market": "Equities", "outcome": "TARGET",
         "perf": 10.0, "run_perf": 10.0},
    ]

    baseline = server._winner_run_replay(rows, wallet=10_000, position_pct=100, max_open=1,
                                         min_trade=25)
    runner = server._winner_run_replay(rows, wallet=10_000, position_pct=100, max_open=1,
                                       min_trade=25, perf_key="run_perf")

    # A never closes, so B is blocked by the max-open cap in BOTH arms and the two agree exactly.
    assert baseline["funded"] == 1
    assert runner["funded"] == 1
    assert baseline["end_wallet"] == runner["end_wallet"]


# ----------------------------------------------------------------------------------------------------
# Golden-number replay tests (user 2026-08-15: "how do I get confidence in the numbers you give me?").
#
# Every other Best Settings test in this file asserts SOURCE TEXT -- that index.html contains a given
# string. That can prove the code says what we wrote; it can never prove the arithmetic is right. The
# suite stayed green for weeks while the replay funded roughly three times more trades than the wallet
# could afford, because no test ever computed a return and checked it.
#
# These run the REAL _combReplay over a frozen slice of the real 12-month population and assert exact
# figures. Any change to the wallet maths fails with a number difference, naming the old and new value.
# If a change is intentional, update the golden values IN THE SAME COMMIT and say so -- that diff is the
# audit trail showing which numbers moved and by how much.
# ----------------------------------------------------------------------------------------------------
_REPLAY_FIXTURE = Path(__file__).parent / "tests_fixtures" / "replay_population.json"


# The exit rule as it stood BEFORE 2026-08-15, kept so the defect it caused stays measurable. Same
# function name, so _combReplay picks it up unchanged. Swapping the DATA cannot reproduce the old
# behaviour -- the current _pfExitDate short-circuits on outcome=="OPEN" before it ever reads exit_date.
_LEGACY_EXIT_RULE = """function _pfExitDate(r,runner){
  const _ad=(d,n)=>{if(!d)return "9999-99-99";const t=new Date(d+"T00:00:00Z");
    t.setUTCDate(t.getUTCDate()+(+n||0));return t.toISOString().slice(0,10);};
  return ((runner&&r.run_exit_date)||r.exit_date||_ad(r.trig_date,r.days_open||0)||"9999-99-99");
}"""


def _replay_harness(extra_js: str, exit_rule: str = None) -> dict:
    """Run the page's own _pfExitDate/_combReplay over the frozen population, in Node."""
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")

    def grab(pattern):
        match = re.search(pattern, html, re.S)
        assert match, f"replay source not found: {pattern}"
        return match.group(0)

    source = "\n".join([
        grab(r"const _fundedMaxOpen=[^\n]*\n"),
        exit_rule or grab(r"function _pfExitDate\(r,runner\)\{.*?\n\}"),
        grab(r'function _combReplay\(seq,stakeFrac,maxopen,withProof=false,perfKey="perf",compound=true\)\{.*?\n\}'),
    ])
    rows = json.loads(_REPLAY_FIXTURE.read_text(encoding="utf-8"))["rows"]
    script = (
        "const MIN_TRADE=25, WINNERS_WALLET=10000;"
        'const levOf=r=>r.market==="FX"?30:r.market==="Indices"?20:r.market==="Commodities"?10:5;'
        + source
        + f"\nconst seq={json.dumps(rows)};\n"
        + extra_js
    )
    # Written to a file rather than passed via `node -e`: the fixture inlines ~120 rows and Windows
    # rejects the resulting command line with "The filename or extension is too long" (WinError 206).
    with tempfile.TemporaryDirectory() as tmp:
        runner = Path(tmp) / "replay.js"
        runner.write_text(script, encoding="utf-8")
        done = subprocess.run(["node", str(runner)], check=True, capture_output=True,
                              text=True, timeout=60)
    return json.loads(done.stdout)


def test_replay_golden_numbers_over_the_frozen_population():
    """Exact returns from the real replay. A silent change to the wallet maths cannot pass this."""
    result = _replay_harness(
        "const out={};"
        "for(const [st,mo] of [[0.05,50],[0.02,20],[0.10,12]]){"
        "  const z=_combReplay(seq,st,mo);"
        "  out[st+'/'+mo]={ret:+z.ret.toFixed(6),funded:z.n,cap:z.cap,dd:+z.dd.toFixed(6)};"
        "}"
        "console.log(JSON.stringify(out));"
    )
    assert result == {
        "0.05/50": {"ret": 0.230815, "funded": 126, "cap": 20, "dd": 0.031791},
        "0.02/20": {"ret": 0.091064, "funded": 126, "cap": 20, "dd": 0.012932},
        # cap 10 against a requested 12: floor(1/0.10). The card must never claim 12 -- see below.
        "0.1/12":  {"ret": 0.170441, "funded": 70,  "cap": 10, "dd": 0.060713},
    }


def test_unresolved_trades_keep_their_capital_committed():
    """The defect that inflated every Best Settings return until 2026-08-15, pinned as numbers.

    /api/winners rows carry no ``days_open``, so the old rule settled an unresolved trade on its own
    TRIGGER day: capital returned and the mark-to-market gain banked instantly, then compounded into
    everything after it. The wallet funded trades it could not afford -- the funded count is the clearest
    evidence -- and the headline return was unachievable. Replays the identical population under both
    rules so the size of the distortion is a permanent record, not a memory.
    """
    probe = (
        "const out={unresolved:seq.filter(r=>!r.exit_date).length};"
        "for(const [st,mo] of [[0.05,50],[0.10,12],[0.25,4]]){const z=_combReplay(seq,st,mo);"
        "  out[st+'/'+mo]={ret:+z.ret.toFixed(6),funded:z.n};}"
        "console.log(JSON.stringify(out));"
    )
    now = _replay_harness(probe)
    was = _replay_harness(probe, exit_rule=_LEGACY_EXIT_RULE)

    assert now["unresolved"] == 45, "fixture must contain unresolved trades or it proves nothing"
    for key in ("0.05/50", "0.1/12", "0.25/4"):
        assert now[key]["ret"] < was[key]["ret"], (
            f"{key}: the old rule inflated the return ({was[key]['ret']} -> {now[key]['ret']})")
        assert now[key]["funded"] < was[key]["funded"], (
            f"{key}: the old rule funded trades the wallet could not afford "
            f"({was[key]['funded']} -> {now[key]['funded']})")

    # Worst observed case, held as a number so nobody has to take the scale on trust: at a 10% position
    # the old rule reported +49.7% over 118 funded trades where the truth is +17.0% over 70.
    assert was["0.1/12"] == {"ret": 0.497247, "funded": 118}
    assert now["0.1/12"] == {"ret": 0.170441, "funded": 70}


def test_max_open_is_never_reported_above_what_the_stake_can_fund():
    """A card must never advertise a configuration the engine did not run (user 2026-08-15).

    _combReplay clamps Max open to floor(1/stake), so a requested 12 at a 10% position runs as 10. The
    summary card printed the REQUEST, advertising "10% position - 12 max open" - 120% of the wallet, a
    setup that never existed and was never tested - and Apply wrote that untested value into the user's
    configuration. Both grids now normalise to the effective cap before recording an option.
    """
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")
    assert "const eff=Math.min(mo,_fundedMaxOpen(st/100));" in html
    assert "const option={...c,st,mo:eff,...z}" in html
    assert "options.push({...c,st,mo:eff,...z})" in html
    # The request must not survive into a recorded option under either grid.
    assert "const option={...c,st,mo,...z}" not in html
    assert "options.push({...c,st,mo,...z})" not in html

    result = _replay_harness(
        "const z=_combReplay(seq,0.10,12);"
        "console.log(JSON.stringify({requested:12,effective:Math.min(12,_fundedMaxOpen(0.10)),cap:z.cap}));"
    )
    assert result == {"requested": 12, "effective": 10, "cap": 10}


def test_scanner_still_hard_filters_on_my_trading_filters():
    """P-01 REGRESSION GUARD. The Scanner's filter sidebar moved to Squeeze History on 2026-08-16, but
    pass() was never only the sidebar: it also carries tradeVisible() and the MY_LIMITS hard-filter block
    added 2026-08-12, which hides setups failing My Trading Filters from the Scanner table AND excludes
    them from the daily email. Removing the sidebar must not take those with it.
    """
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")
    body = _extract_function(html, "pass")

    assert "tradeVisible(r)" in body, "per-user market/direction/location trade gate lost from pass()"
    for floor in ("min_risk_reward", "min_quality", "min_volume_score", "min_rvol",
                  "require_above_vwap", "require_atr_expanding",
                  "min_instrument_value", "max_instrument_value"):
        assert floor in body, f"MY_LIMITS hard filter lost {floor} — P-01 regression"


def test_scanner_filters_moved_and_left_nothing_dangling():
    """Every loop over F / MSEL_IDS / FILTER_IDS dereferences $(id) unguarded, so an id that no longer
    has an element throws on load. Assert the removed ids are gone from BOTH the markup and the wiring.
    """
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")

    for gone in ("f_dir", "f_stat", "f_loc", "f_tf", "f_qmin", "f_qmax", "f_rrmin", "f_rrmax",
                 "f_demin", "f_demax", "f_dsmin", "f_dsmax", "f_rvmin", "f_rvmax",
                 "f_pemin", "f_pemax", "f_inmin", "f_inmax"):
        assert f'"{gone}"' not in html and f"'{gone}'" not in html, f"{gone} still referenced"
        assert f'id="{gone}"' not in html, f"{gone} element still present"

    # f_days joined them on 2026-08-17 (user: "the date filter on scanner report is of no use as the
    # LOGIC for squeeze has a date limit anyway"). It never filtered rows -- it set the row-detail price
    # chart's window -- but the squeeze engine already bounds how far back a setup can form, so the
    # slider changed the picture without changing what qualifies. showDetail() read it unguarded, so the
    # window had to become a constant rather than the element simply being deleted.
    assert 'id="f_days"' not in html, "the f_days slider was removed"
    assert '$("f_days")' not in html, "nothing may still dereference f_days"
    assert "const PRICE_CHART_DAYS=365;" in html
    assert "const days=PRICE_CHART_DAYS;" in html, "showDetail must use the constant"

    # Kept deliberately: the search box and the hidden scope carriers.
    assert 'id="f_search"' in html
    assert 'id="f_mkt" multiple hidden' in html and 'id="f_sec" multiple hidden' in html
    assert 'const F=["f_search","f_mkt","f_sec"];' in html
    # dm()/sel() combined a sidebar dropdown with a chart set; with no sidebar they are dead.
    assert "const dm=" not in html


def test_back_test_saved_scope_survives_the_filter_move():
    """_pfSavedScope and applyConfigFromReport read USER_FILTERS.f_mkt / f_sec. Dropping those ids would
    silently cost Back Test its market/sector scope and break Apply-this-configuration, which is why the
    two selects are retained (hidden) rather than deleted.
    """
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")
    assert 'const keys=kind==="market"?["pof_market","f_mkt"]:["pof_sector","f_sec"];' in html
    assert 'fillSel("f_mkt","market");fillSel("f_sec","sector");' in html, (
        "the hidden scope selects still need their options, or Apply-config cannot select one")


def test_squeeze_history_owns_the_filters_now():
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")

    assert 'id="sqh-filters"' in html and 'class="sidefilt"' in html
    for control in ("sqf_dir", "sqf_loc", "sqf_mkt", "sqf_sec", "sqf_tf", "sqf_out",
                    "sqfr_qmin", "sqfr_rrmax", "sqfr_rvmin", "sqfr_retmax", "sqfr_dmin",
                    "sqfr_vsmax", "sqf_vwap", "sqf_atr"):
        assert f'id="{control}"' in html, f"{control} missing from the Squeeze History sidebar"

    # Header buttons act on this tab. "Show Squeeze Only" was hidden outright on 2026-08-16 at the user's
    # request; the button stays in the DOM and signalOnly stays false, so pass() behaves as it does with
    # the toggle showing "off" — the full monitored universe.
    assert 'if(CUR_TAB==="squeezehist")return toggleSqhFilters();' in html
    assert 'if(CUR_TAB==="squeezehist")return sqhReset();' in html
    assert '$("signalonly").style.display="none";' in html
    # One rebuild scope control, not two: a location is a group of markets.
    assert 'id="refresh-mkt-wrap"' not in html
    assert 'id="refresh-loc-wrap"' in html

    # The dim cache is keyed on the sidebar too, or it serves rows the filters just excluded.
    assert "_sqDvSig===_sqSig" in html
    # Options come from the history, not the current snapshot.
    assert "function fillSqhFilterOptions()" in html
    # Saved defaults cover the moved controls.
    assert "SQH_FILTER_IDS());" in html


def test_order_ops_rows_carry_the_setup_metrics_that_caused_them(monkeypatch):
    """Pre-orders to my IG showed "—" for RVOL / VolumeScore / Quality / R:R on EVERY row, always.

    working_orders has no such columns, so /api/order-ops never sent them and the six metric columns had
    never worked (user 2026-08-16). They are resolved from the setup that caused the order -- the latest
    squeeze_history trigger at or before placement -- so the figures are point-in-time correct rather
    than today's snapshot, and historical rows are filled without a migration.
    """
    class _Cur:
        def run(self, sql, **kw):
            assert "squeeze_history" in sql and "tks" in kw
            return [("AAA", "2026-01-10", 60.0, 4.0, 1.2),      # older setup
                    ("AAA", "2026-03-02", 80.0, 6.5, 2.4),      # the one that caused the order
                    ("AAA", "2026-07-01", 30.0, 3.1, 0.8),      # AFTER placement - must not be used
                    ("BBB", "2026-05-05", 55.0, 3.4, 1.1)]

        def close(self):
            pass

    import db_pool
    monkeypatch.setattr(db_pool, "get_db", lambda: _Cur())
    monkeypatch.setattr(server, "_volscore_trigger_map", lambda: {("AAA", "2026-03-02"): 9})
    monkeypatch.setattr(server, "_volscore_trigger_feature_map",
                        lambda: {("AAA", "2026-03-02"): {"above_vwap": True, "atr_expanding": False}})

    rows = [{"ticker": "AAA", "placed_at": "2026-03-04 09:15:00"},
            {"ticker": "BBB", "placed_at": "2026-05-06 10:00:00"},
            {"ticker": "ZZZ", "placed_at": "2026-05-06 10:00:00"}]   # no history at all
    server._attach_setup_metrics(rows)

    aaa = rows[0]
    assert aaa["setup_date"] == "2026-03-02", "must use the trigger at or before placement, not a later one"
    assert (aaa["quality"], aaa["rr"], aaa["rvol"]) == (80.0, 6.5, 2.4)
    assert aaa["volume_score"] == 9
    assert aaa["above_vwap"] is True and aaa["atr_expanding"] is False

    assert rows[1]["quality"] == 55.0 and rows[1]["volume_score"] is None
    assert "quality" not in rows[2], "a ticker with no setup history must be left untouched, not zeroed"


def test_the_analysis_population_enforces_the_documented_rr_cap():
    """config.MAX_RISK_REWARD must actually be ENFORCED, not merely declared.

    It was defined on 2026-06-09 with a comment describing this exact failure -- "ratios above this are
    treated as bad level geometry rather than an advantage. A very distant target combined with a tight
    stop can produce a mathematically valid but non-actionable setup" -- and referenced nowhere. 1,183 of
    4,385 deduped 12-month trades sat above it, and Best Settings SELECTED for them because its R:R
    filters reward a high ratio, which is where "9844% growth" came from.

    A constant that documents a rule nobody applies is worse than no constant: it reads as a guarantee.
    """
    import config
    assert config.MAX_RISK_REWARD == 10.0

    server_src = (Path(__file__).parent / "hvf_web" / "server.py").read_text(encoding="utf-8")
    assert "from config import MAX_RISK_REWARD as _MAX_RR" in server_src
    assert "and risk_reward >= 3 and risk_reward <= :maxrr" in server_src
    assert "maxrr=_MAX_RR" in server_src


def test_the_analysis_population_matches_the_engines_tight_stop_guard():
    """A setup the order path would refuse must not appear in the numbers that recommend settings.

    ig_shim skips a trade when the stop is under 0.5% of price -- inside spread and normal noise. The
    analysis population had no such rule, so 209 of the 12-month rows carried a stop tighter than that
    and the reports were recommending configurations built on trades the engine would never place.
    """
    server_src = (Path(__file__).parent / "hvf_web" / "server.py").read_text(encoding="utf-8")
    assert "_MIN_STOP_DISTANCE = 0.005" in server_src
    assert "< _MIN_STOP_DISTANCE" in server_src

    ig_src = (Path(__file__).parent / "ig_shim.py").read_text(encoding="utf-8")
    assert "0.5%" in ig_src, "the engine-side guard this mirrors has moved -- keep the two in step"


def test_back_test_and_best_settings_produce_the_same_numbers():
    """THE P-03 invariant: the two views must agree, or an applied recommendation is a lie.

    "When apply configuration is required it does not seem to do all the config e.g. markets -- PROOF
    BACK TEST DOES NOT SHOW THE RESULTS EXPECTED". The two surfaces ran different maths, so a
    configuration copied out of Best Settings never reproduced its own headline in Back Test. Structural
    checks cannot catch that; only running both and comparing can. Back Test reads its wallet model from
    DOM inputs, so those are stubbed and the SAME population and configuration go through each.
    """
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")

    def grab(pattern):
        match = re.search(pattern, html, re.S)
        assert match, f"replay source not found: {pattern}"
        return match.group(0)

    source = "\n".join([
        grab(r"const _fundedMaxOpen=[^\n]*\n"),
        grab(r"function _pfAddDays\([^\n]*\n"),
        grab(r"function _pfExitDate\(r,runner\)\{.*?\n\}"),
        grab(r'function _combReplay\(seq,stakeFrac,maxopen,withProof=false,perfKey="perf",compound=true\)\{.*?\n\}'),
        grab(r"function _pfWalletLedger\(sel\)\{.*?\n\}"),
    ])
    rows = json.loads(_REPLAY_FIXTURE.read_text(encoding="utf-8"))["rows"]
    script = (
        "const WALLET=10000, MIN_TRADE=25, WINNERS_WALLET=10000;"
        'const levOf=r=>r.market==="FX"?30:r.market==="Indices"?20:r.market==="Commodities"?10:5;'
        "let CFG={}; const $=id=>({value:CFG[id]});"
        + source
        + f"\nconst seq={json.dumps(rows)};\n"
        + "const out=[];"
        "for(const [stakePct,maxopen] of [[5,50],[2,20],[10,12],[25,4]]){"
        '  CFG={"pfw-wallet":WALLET,"pfw-stake":stakePct,"pfw-maxopen":maxopen};'
        "  const back=_pfWalletLedger(seq.slice()), best=_combReplay(seq,stakePct/100,maxopen);"
        "  const br=back.endWallet/back.wallet-1;"
        "  out.push({cfg:stakePct+'%/'+maxopen,backTest:+br.toFixed(9),bestSettings:+best.ret.toFixed(9),"
        "            btFunded:back.taken,bsFunded:best.n});}"
        "console.log(JSON.stringify(out));"
    )
    with tempfile.TemporaryDirectory() as tmp:
        runner = Path(tmp) / "recon.js"
        runner.write_text(script, encoding="utf-8")
        done = subprocess.run(["node", str(runner)], check=True, capture_output=True,
                              text=True, timeout=60)

    for row in json.loads(done.stdout):
        assert row["backTest"] == row["bestSettings"], (
            f"{row['cfg']}: Back Test {row['backTest']} != Best Settings {row['bestSettings']} -- "
            "an applied recommendation will not reproduce its headline")
        assert row["btFunded"] == row["bsFunded"], (
            f"{row['cfg']}: funded {row['btFunded']} vs {row['bsFunded']}")


def test_stake_exposure_never_exceeds_the_wallet():
    """No configuration may stake more than 100% of the wallet (user 2026-08-15: "5% / 50 WITH 250%?").

    5% x 50 positions is 250% of the wallet. It never ran - _fundedMaxOpen clamps concurrency to
    floor(1/stake), so 50 becomes 20 and exposure lands exactly on 100%. But the REQUEST was what got
    displayed and stored, so the interface advertised 250% and Apply wrote it into the user's limits.
    This asserts the invariant against the real replay across the whole search grid: whatever is asked
    for, peak funded concurrency x stake can never exceed the wallet.
    """
    result = _replay_harness(
        "const out=[];"
        "for(const st of [1,2,3,5,7.5,10,25]) for(const mo of [3,5,8,12,20,25,50,100,250,400]){"
        "  const z=_combReplay(seq,st/100,mo);"
        "  out.push({st,mo,eff:Math.min(mo,_fundedMaxOpen(st/100)),cap:z.cap,"
        "            exposure:+((z.cap*st)/100).toFixed(4)});}"
        "console.log(JSON.stringify(out));"
    )
    worst = max(result, key=lambda r: r["exposure"])
    for row in result:
        assert row["exposure"] <= 1.0 + 1e-9, (
            f"{row['st']}% x {row['cap']} concurrent = {row['exposure'] * 100:.0f}% of the wallet")
        assert row["cap"] <= row["eff"], (
            f"{row['st']}%/{row['mo']}: funded {row['cap']} concurrently but the cap is {row['eff']}")
    assert worst["exposure"] <= 1.0, f"worst case {worst}"


def test_every_wallet_replay_shares_one_exit_rule():
    """Best Settings, the winners ledger and Back Test must agree on when a trade frees its capital.

    They did not, which is why an applied recommendation never reproduced its own headline figure -- the
    complaint recorded as P-03. Structural rather than numeric: three ledgers, one rule.
    """
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")
    assert 'const exit=_pfExitDate(r,perfKey==="run_perf");' in html      # _combReplay
    assert html.count("const exitOf=r=>_pfExitDate(r,false);") == 2       # _winLedger + _pfWalletLedger
    # No ledger may reconstruct its own convention from days_open again.
    assert "_pfAddDays(r.trig_date,r.days_open||0)" not in html


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


def test_let_winners_run_recovers_missing_historical_target_levels():
    """Legacy target winners and unresolved rows retain a usable target in the runner replay."""
    assert server._winner_run_target({
        "direction": "BULLISH", "entry": 100.0, "stop": 95.0, "target": None,
        "rr": 4.0, "outcome": "TARGET", "return_pct": 12.5,
    }) == 112.5
    assert server._winner_run_target({
        "direction": "BEARISH", "entry": 100.0, "stop": 105.0, "target": None,
        "rr": 4.0, "outcome": "TARGET", "return_pct": 12.5,
    }) == 87.5
    assert server._winner_run_target({
        "direction": "BULLISH", "entry": 100.0, "stop": 95.0, "target": None,
        "rr": 4.0, "outcome": "OPEN", "return_pct": 3.0,
    }) == 120.0


def test_let_winners_run_starts_after_trigger_bar():
    """The entry-bar high/low cannot stop a trade entered at that bar's close."""
    bars = {"TEST.L": [
        ("2026-01-02", 111.0, 89.0, 100.0),
        ("2026-01-03", 112.0, 99.0, 110.0),
    ]}

    assert server._winner_run_bars(bars, "TEST.L", "2026-01-02") == [bars["TEST.L"][1]]


def test_let_winners_run_historical_target_starts_with_target_locked(monkeypatch):
    """A recorded target winner continues after its known target event; revised earlier bars are irrelevant."""
    monkeypatch.setattr(ig_shim, "compute_trailing_stop", lambda *args, **kwargs: 108.0)
    bars = [
        (dt.date(2026, 1, 4), 112.0, 109.0, 110.0),
    ]

    outcome, exit_price, exit_date = server._run_path(
        "BULLISH", 100.0, 90.0, 110.0, bars, 0.25, return_date=True,
        target_already_hit=True,
    )

    assert (outcome, exit_price, exit_date) == ("RAN", 110.0, "2026-01-04")


def test_let_winners_run_portfolio_evidence_reconciles_exit_and_capacity():
    rows = [
        {"ticker": "A", "trig_date": "2026-01-01", "exit_date": "2026-01-03",
         "run_exit_date": "2026-01-03", "market": "Equities", "outcome": "TARGET",
         "perf": 10.0, "run_perf": 15.0},
        {"ticker": "B", "trig_date": "2026-01-02", "exit_date": "2026-01-04",
         "run_exit_date": "2026-01-04", "market": "Equities", "outcome": "STOPPED",
         "perf": -5.0, "run_perf": -5.0},
    ]

    evidence = server._winner_run_portfolio_evidence(
        rows, wallet=10_000, position_pct=5, max_open=20, min_trade=25)

    assert evidence["eligible"] == 2
    assert evidence["target_lock"] == {"target_hits": 1, "breaches": 0}
    assert evidence["attribution"]["common_funded"] == 2
    assert evidence["attribution"]["exit_impact"] == 25.0
    assert abs(evidence["attribution"]["unexplained"]) < .005
    assert evidence["valid"] is True
    assert evidence["verdict"] == "improved"


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
    don't start a line with 'function ', so they don't false-trigger the boundary).

    An `async ` prefix is allowed: applyConfigFromReport became async on 2026-08-15 when its native
    confirm() was replaced with the awaited appConfirm dialog, and without this the helper stopped
    finding it. The boundary search has to know about async declarations for the same reason."""
    m = re.search(rf"\n(?:async )?function {re.escape(name)}\([^)]*\)\{{", html)
    assert m, f"function {name}(...) not found in hvf_web/index.html"
    start = m.start()
    nxt = re.search(r"\n(?:async )?function ", html[m.end():])
    return html[start: m.end() + nxt.start() if nxt else len(html)]


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


def test_approved_ui_report_backlog_is_wired_to_live_render_paths():
    """Structural regression coverage for the 2026-08-13 approved UI/report stage."""
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")

    assert 'id="scanner-name-search"' in html and 'syncScannerNameSearch(this.value)' in html
    assert 'let INSTR_FUNNEL=null, instrSorts=[];' in html
    assert 'e.shiftKey' in html and 'class="sarr"' in _extract_function(html, "instrSort")
    instrument_sort = _extract_function(html, "_instrSortValue")
    assert "row.current_rvol" in instrument_sort
    assert "row.current_above_vwap" in instrument_sort
    assert "row.current_atr_expanding" in instrument_sort
    instrument_rvol = _extract_function(html, "instrRvolCell")
    assert "insufficient_volume_history" in instrument_rvol
    assert "Data issue" in instrument_rvol

    best_history = _extract_function(html, "paintBestSettingsHistory")
    assert "BEST_HISTORY_ROWS=history" in best_history
    assert "_bestHistoryChanges(row,all[index+1]).toLowerCase().includes(query)" in best_history
    assert "No settings changes match that search" in best_history

    backtest = _extract_function(html, "_pfBacktestSettingsCard")
    assert "Back Test summary" in backtest
    assert "Actual Win : Loss" in backtest and "Model return" in backtest

    choice = _extract_function(html, "selectBestChoice")
    evidence = _extract_function(html, "renderDecisionProof")
    assert 'if(LIMITED){detail.style.display="none";return;}' in choice
    assert 'if(LIMITED){target.style.display="none";target.innerHTML="";return;}' in evidence
    assert '$("instr-funnel-wrap").style.display=AUTH?"":"none"' in _extract_function(html, "renderInstruments")

    assert 'const SUPPORT_TABS=["batch","syslogs","jobs","sysdocs"]' in html
    assert '.subnav-operations{gap:3mm}' in html
    # Gap widened 8px -> 10px/20px on 2026-08-15: at 8px the IG group card sat hard against Preferences
    # and Tab visibility, and its ::before background used inset:0 -5%, bleeding the card OUTSIDE the
    # group and into the neighbouring pills (user: "too close - especially the card to right of tab
    # visibility"). The inset is now 0 and the group has real horizontal padding.
    assert '.confnav{display:flex;flex-wrap:wrap;gap:10px 20px;margin:0 0 20px;justify-content:center;align-items:center}' in html
    assert '.confnav-group::before{content:"";position:absolute;z-index:0;inset:0;' in html
    assert 'padding:9px 12px;background:transparent' in html
    assert '_igNoCreds?' in html and 'No IG account data — add credentials' in html
    assert "_ag.style.display='none'" in html


def test_order_ops_keeps_the_servers_point_in_time_metrics():
    """Regression guard (2026-08-17, user: "on 'pre-orders to my ig' there are still missing RVOL
    figures - these should be available for any date and for any screen").

    /api/order-ops resolves RVOL / VolumeScore / R:R / Quality from the squeeze_history trigger that
    CAUSED each working order -- point-in-time correct, and measured at 200/200 rows populated. The
    browser then threw it away: paintOrderOps enriched each row from the current scanner snapshot with

        r.rvol = d?.rvol ?? null

    which is unconditional. _snapshot_rvol only carries rvol for instruments that triggered TODAY, so
    for a historical order the snapshot record was found (hence the Name rendered fine) but rvol came
    back null and overwrote the correct figure with a blank. IWG.L placed 2026-08-11 was the reported
    case: server said 1.10, screen said nothing.

    The snapshot must stay a FALLBACK. Name and dist_pct legitimately come from it -- neither is
    returned by /api/order-ops -- so those are excluded from the check.
    """
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")
    paint = _extract_function(html, "paintOrderOps")

    for field in ("rvol", "volume_score", "rr", "quality"):
        assert f"r.{field}=r.{field}??d?.{field}??null" in paint, (
            f"paintOrderOps must prefer the server's r.{field} and fall back to the snapshot, not "
            f"overwrite it. Writing `r.{field}=d?.{field}??null` blanks every order whose instrument "
            f"did not trigger today (user 2026-08-17)."
        )
        assert f"r.{field}=d?.{field}??null" not in paint, (
            f"paintOrderOps still clobbers r.{field} with the snapshot value."
        )


def test_order_ops_backfills_rvol_from_price_history():
    """The other half of the same report: 815 of 30,408 triggered squeeze_history rows store rvol NULL.
    Most are FX and indices, which have no real volume and must stay blank -- _rvol_at returns None
    there on purpose, because a fabricated 1.0 would read as 'average participation' rather than 'not
    applicable'. The rest are equities whose row was written before the volume bar landed (IWG.L
    2026-08-04: stored NULL, computes to 1.10 from bars already held).

    _fill_missing_rvol must reuse server._rvol_at rather than carry a second formula, so the Scanner
    column, volume_score._rvol_at and this backfill cannot drift apart.
    """
    src = (Path(__file__).parent / "hvf_web" / "server.py").read_text(encoding="utf-8")
    assert "def _fill_missing_rvol" in src
    body = src[src.index("def _fill_missing_rvol"):]
    body = body[:body.index("\n@app.route") if "\n@app.route" in body else len(body)]
    assert "_rvol_at(" in body, "_fill_missing_rvol must call the canonical _rvol_at, not reimplement RVOL."
    assert "_perf_bars(" in body, "_fill_missing_rvol must batch its bars via _perf_bars (one round trip)."
    assert 'row.get("rvol") is not None' in body, (
        "_fill_missing_rvol must only touch rows the trigger left blank -- a stored rvol is the "
        "point-in-time value and wins."
    )
    assert "_fill_missing_rvol(matched)" in src, "_attach_setup_metrics must invoke the backfill."


def test_snapshot_storage_round_trips_through_gzip():
    """Snapshot objects are gzipped in Supabase Storage from 2026-08-17 (user: "ok let's do zip file
    for now").

    The encoded snapshot is ~816 KB of repetitive JSON that compresses to ~77 KB, 10.6x (measured on
    the 2026-08-12 snapshot, 1,421 records). Supabase's free tier allows 5 GB of egress a month;
    Storage started returning HTTP 402 when that ran out and froze the live Scanner on 12 August data.
    Uncompressed that budget is ~6,580 downloads; compressed, ~69,900.

    Three properties matter and each is checked:
      1. Round trip is lossless.
      2. Pre-2026-08-17 objects are plain JSON and MUST keep loading -- the reader sniffs the gzip magic
         number rather than trusting the extension, so old and new both work.
      3. Compression is deterministic. Publication uses immutable content-addressed names and tolerates
         a 409 "already exists" on retry; that is only safe if identical input gives identical bytes,
         which the gzip default (current time in the header) would break.
    """
    import scanner_snapshot_store as store

    raw = json.dumps({"generated_utc": "2026-08-17T10:00:00+00:00", "count": 2,
                      "records": [{"ticker": "AAA"}, {"ticker": "BBB"}]}).encode("utf-8")

    packed = store._compress(raw)
    assert packed[:2] == b"\x1f\x8b", "compressed payload must be gzip"
    assert store._decompress(packed) == raw, "gzip round trip must be lossless"
    assert store._decompress(raw) == raw, (
        "an uncompressed object published before 2026-08-17 must still read back unchanged"
    )
    assert store._compress(raw) == packed, (
        "compression must be deterministic (mtime=0) or retrying an immutable publish is unsafe"
    )
    assert len(packed) < len(raw)

    # The digest identifies the RAW json, never the stored bytes: _matches_digest hashes the web host's
    # uncompressed snapshot.json against it to decide whether a download is needed at all.
    assert store._digest(raw) != store._digest(packed)
    assert store._object_path("2026-08-17T10:00:00+00:00", "abc123", compressed=True).endswith(".json.gz")
    assert store._object_path("2026-08-17T10:00:00+00:00", "abc123", compressed=False).endswith(".json")
