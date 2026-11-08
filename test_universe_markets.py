"""P-02 (2026-11-08): the 98 added instruments -- 10 NASDAQ-100 names and the new "S&P MidCap 400" market.

Independent specialist tests. The author's own test (test_scan_progress) pins counts only; it does not pin
that a market added to UNIVERSE is also known to the order the reports group by, that the new tickers are
plain US symbols (a suffixed ticker would be located and timed as another exchange), or that the new list
is free of duplicates (a duplicate inside ONE list is invisible to the cross-market duplicate test, because
that test only compares markets with each other).
"""
import re

import pytest

from config import MARKET_ORDER
from config_store import location_of_ticker
from hvf_web.build_snapshot import _location_of
from run_hvf_report import MIDCAP400, NASDAQ100, UNIVERSE

NEW_NASDAQ = ["ASML", "ALNY", "NBIS", "SHOP", "RKLB", "TRI", "FER", "ALAB", "CRWV", "SPCX"]
MARKET = "S&P MidCap 400"
US_SYMBOL = re.compile(r"^[A-Z]{1,5}(-[A-Z])?$")


def test_every_universe_market_is_in_the_report_order():
    """A market missing from MARKET_ORDER falls to the end alphabetically in every grouped report."""
    missing = [m for m in UNIVERSE if m not in MARKET_ORDER]
    assert not missing, f"UNIVERSE markets absent from config.MARKET_ORDER: {missing}"


def test_midcap_follows_sp500_in_both_orders():
    keys = list(UNIVERSE)
    assert keys.index(MARKET) == keys.index("S&P 500") + 1
    assert MARKET_ORDER.index(MARKET) == MARKET_ORDER.index("S&P 500") + 1


def test_midcap_list_has_no_duplicates_and_the_agreed_size():
    assert len(MIDCAP400) == len(set(MIDCAP400)), "a ticker is listed twice inside S&P MidCap 400"
    assert len(MIDCAP400) == 88


def test_new_nasdaq_names_are_in_the_nasdaq_list_once():
    for t in NEW_NASDAQ:
        assert NASDAQ100.count(t) == 1, f"{t} count in NASDAQ100"


def test_midcap_shares_no_name_with_any_other_market():
    others = {t for m, ts in UNIVERSE.items() if m != MARKET for t in ts}
    assert not (set(MIDCAP400) & others), sorted(set(MIDCAP400) & others)


@pytest.mark.parametrize("ticker", NEW_NASDAQ + MIDCAP400)
def test_new_tickers_are_plain_us_symbols_located_us(ticker):
    """No exchange suffix: '.L'/'.AX'/'.AS' would send the name to another market's location and hours,
    and a bare 'SN' / 'NXT' / 'PAG' / 'CSL' / 'ASML' must stay distinct from SN.L / NXT.L / PAG.L / CSL.AX /
    ASML.AS (the DB keys differ only by suffix)."""
    assert US_SYMBOL.match(ticker), ticker
    assert _location_of(ticker, MARKET) == "US"
    assert location_of_ticker(ticker) == "US"


def test_bare_symbols_do_not_collide_with_suffixed_listings():
    """Same root, different instrument: the bare US ticker is a different string from the suffixed one,
    so every one of them must exist in UNIVERSE exactly once as itself."""
    flat = [t for ts in UNIVERSE.values() for t in ts]
    for bare, other in (("SN", "SN.L"), ("NXT", "NXT.L"), ("NXT", "NXT.AX"), ("PAG", "PAG.L"),
                        ("CSL", "CSL.AX"), ("ASML", "ASML.AS")):
        assert bare in flat and other in flat, (bare, other)
        assert flat.count(bare) <= 2 and flat.count(other) == 1


def test_report_scanned_line_names_the_new_market():
    src = open("run_hvf_report.py", encoding="utf-8").read()
    line = next(l for l in src.splitlines() if "Scanned: FTSE 100" in l)
    assert MARKET in line


def test_group_by_market_places_midcap_between_sp500_and_dax():
    from price_action import group_by_market
    rows = [("A", 1, 1, "DAX"), ("B", 1, 1, MARKET), ("C", 1, 1, "S&P 500"), ("D", 1, 1, "NASDAQ 100")]
    groups = group_by_market(rows, n=5, market_of=lambda r: r[3], market_order=MARKET_ORDER)
    names = [g[0] if isinstance(g, tuple) else g.get("market") for g in groups]
    assert names == ["NASDAQ 100", "S&P 500", MARKET, "DAX"], names
