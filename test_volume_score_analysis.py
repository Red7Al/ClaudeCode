import analyze_volume_score_windows as analysis
import volume_score as vs


def test_candidate_experiment_restores_production_constants():
    before = tuple(getattr(vs, name) for name in analysis.WINDOW_NAMES)
    with analysis.score_windows(analysis.CANDIDATES["early-5"]):
        assert vs.RVOL_BARS == 5
        assert vs.ATR_PERIOD == 5
    assert tuple(getattr(vs, name) for name in analysis.WINDOW_NAMES) == before


def test_candidates_include_baseline_and_shorter_windows():
    assert analysis.CANDIDATES["production"] == (20, 20, 20, 14, 60)
    for name in ("early-5", "early-8", "early-10", "early-15"):
        assert all(a < b for a, b in zip(analysis.CANDIDATES[name], analysis.CANDIDATES["production"]))


def test_metrics_uses_scored_return_values():
    result = analysis._metrics([
        {"return_pct": 10.0, "score": 8},
        {"return_pct": -5.0, "score": 7},
    ])
    assert result["resolved"] == 2
    assert result["passed"] == 1
    assert result["return_separation"] == 15.0


def test_wallet_uses_stake_times_return_not_r_multiple():
    rows = [
        {"trig_date": "2026-01-01", "exit_date": "2026-01-02", "return_pct": 10.0},
        {"trig_date": "2026-01-03", "exit_date": "2026-01-04", "return_pct": -5.0},
    ]
    result = analysis._stake_compound(rows, start=10_000, stake_fraction=0.02, max_open=1)
    # £200 × 10% = +£20; next stake £200.40 × -5% = -£10.02.
    assert result["final"] == 10_010


def test_wallet_enforces_user_max_open_and_available_margin():
    rows = [
        {"trig_date": "2026-01-01", "exit_date": "2026-01-10", "return_pct": 10.0, "market": "Equities"},
        {"trig_date": "2026-01-02", "exit_date": "2026-01-10", "return_pct": 10.0, "market": "Equities"},
    ]
    capped = analysis._stake_compound(rows, max_open=1)
    no_cash = analysis._stake_compound(rows, stake_fraction=1.0, max_open=2,
                                       leverage={k: 1.0 for k in analysis.DEFAULT_LEVERAGE})
    assert capped["trades"] == 1 and capped["skipped"] == 1
    assert no_cash["trades"] == 1 and no_cash["skipped"] == 1
