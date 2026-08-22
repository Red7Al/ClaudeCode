from run_hvf_report import SP500


def test_sp500_universe_retains_the_complete_current_constituent_scale():
    """The 2026-08-22 public constituent review contains 503 listed share classes.

    The scanner preserves two earlier tickers additively, so the check deliberately permits a larger
    list but never a regression back to a hand-picked subset.
    """
    assert len(set(SP500)) >= 503
    assert len(SP500) == len(set(SP500))
    assert {"MMM", "BRK-B", "BF-B", "NVDA", "PSKY", "VMRK", "ZTS"} <= set(SP500)
