"""The refresh progress total must be reachable.

user 2026-08-25: "the refresh of instruments has 1771 of 1856 .. where is this 1800 number come from?"

1,856 is the sum of all 18 market lists in UNIVERSE. 1,773 is the number of DISTINCT tickers: 83 appear
under both NASDAQ 100 and S&P 500, and the scan de-duplicates them (`_seen`, first wins). The
full-universe progress total counted the duplicates while the scan skipped them, so the bar could never
reach its own total -- it stopped at 1,773 of 1,856, or 95.5%, and looked like a refresh that had hung.

The partial-refresh branch already de-duplicated, and its comment already said "matches scan de-dup".
Two places computing one number with only one of them right, which is the same shape as the card-capacity
bug that capped a landscape iPad at eight cards.
"""

from pathlib import Path

import pytest

from run_hvf_report import UNIVERSE

ROOT = Path(__file__).parent


def _distinct(sel=None) -> int:
    """The de-duplicated count, first market wins — the rule the scan itself applies."""
    n, prior = 0, set()
    for market, tickers in UNIVERSE.items():
        for ticker in tickers:
            if ticker in prior:
                continue
            prior.add(ticker)
            if sel is None or market in sel:
                n += 1
    return n


def test_the_universe_really_does_contain_duplicates():
    """If this ever stops being true the bug cannot recur, but the fix should not be quietly removed."""
    listed = sum(len(t) for t in UNIVERSE.values())
    distinct = len({t for tickers in UNIVERSE.values() for t in tickers})

    assert listed > distinct, "expected overlapping market lists"
    assert listed - distinct == 83, f"the overlap changed: {listed - distinct} duplicates"


def test_every_duplicate_is_the_nasdaq_sp500_overlap():
    """Names the cause, so a NEW kind of overlap shows up as a failure rather than a silent shift."""
    import collections
    where = collections.defaultdict(list)
    for market, tickers in UNIVERSE.items():
        for ticker in tickers:
            where[ticker].append(market)

    combos = {tuple(sorted(m)) for m in where.values() if len(m) > 1}
    assert combos == {("NASDAQ 100", "S&P 500")}, f"a new overlap appeared: {combos}"


def test_the_full_universe_total_is_reachable():
    """THE BUG. The total must equal what the scan will actually process."""
    source = (ROOT / "hvf_web" / "build_snapshot.py").read_text(encoding="utf-8")
    # Comments explain the old expression, so strip them before looking for it as live code.
    head = source.split("PROGRESS.update(done=0")[0]
    code = "\n".join(l for l in head.splitlines() if not l.lstrip().startswith("#"))

    assert "_total = sum(len(t) for t in UNIVERSE.values())" not in code, (
        "the full-universe progress total counts duplicated tickers again; the bar cannot reach 100%")
    assert _distinct() == len({t for tickers in UNIVERSE.values() for t in tickers})


def test_both_paths_use_one_rule():
    """Full and partial refreshes must not drift apart -- that is how this happened."""
    source = (ROOT / "hvf_web" / "build_snapshot.py").read_text(encoding="utf-8")
    head = source.split("PROGRESS.update(done=0")[0]

    assert head.count("_prior") >= 1, "the de-duplicating loop is gone"
    assert "if sel is None or _mkt in sel" in head, (
        "the two branches have been separated again; one rule must serve both")


def test_a_partial_refresh_counts_only_what_it_owns():
    """NASDAQ 100 is listed before S&P 500, so it owns the 83 shared names."""
    assert _distinct({"NASDAQ 100"}) == 94
    assert _distinct({"S&P 500"}) == 422, "S&P 500 keeps 505 minus the 83 NASDAQ 100 already owns"


def test_the_old_total_was_unreachable():
    """A guard that has never failed proves nothing: compute the shape that shipped."""
    old_total = sum(len(t) for t in UNIVERSE.values())
    actually_scanned = _distinct()

    assert old_total == 1856
    assert actually_scanned == 1773
    assert actually_scanned < old_total, "the reconstruction must be unreachable, or this proves nothing"
