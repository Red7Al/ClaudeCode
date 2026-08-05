"""Offline regressions for Performance report calculations."""

import datetime as dt
from pathlib import Path

import ig_shim
from hvf_web import server


def test_performance_has_dedicated_let_winners_run_tab():
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")

    assert 'data-pfpanel="run" onclick="pfPanel(\'run\')"' in html
    assert 'id="pf-panel-run" class="hidden"' in html
    assert 'if(run)run.classList.toggle("hidden",which!=="run")' in html
    assert 'id="pf-run-stop"' in html
    assert 'id="pf-run-in"' in html
    assert "winnersRunChange('pf')" in html
    assert "Let winners run is off" not in html
    assert 'id="pf-summary-bt"' not in html
    assert "What separates the winners?</h2>" in html
    assert "What separates the winners — what's possible over 12 months" not in html
    assert 'id="ordp-ledger-q"' not in html
    assert "Every trade — oldest first, wallet after each" not in html
    assert html.index('data-pfpanel="results"') < html.index('data-pfpanel="summary"')
    assert ".doc .tclist li::marker{font-size:.65em;color:var(--muted)}" in html
    assert "top:calc(var(--hdr-h,49px) - 1px)" in html


def test_performance_best_settings_is_a_dedicated_wallet_constrained_tab():
    html = (Path(__file__).parent / "hvf_web" / "index.html").read_text(encoding="utf-8")

    assert 'data-pfpanel="settings" onclick="pfPanel(\'settings\')"' in html
    assert 'id="pf-panel-settings" class="hidden"' in html
    assert 'id="ordp-bestcombo"' in html
    assert html.count('id="ordp-bestcombo"') == 1
    assert 'which==="analysis"||which==="settings"' in html
    assert 'Math.floor(1/stakeFrac)' in html
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
