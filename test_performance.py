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
