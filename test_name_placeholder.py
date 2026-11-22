"""A failed name lookup must not be cached as the ticker for ever (2026-11-22: 110 of 648 no-epic tickers carried
their ticker as their name, e.g. HAB.DE -> 'HAB.DE', PH -> 'PH' in the 2026-11-21 snapshot)."""
import logging

import pytest

import instrument_name
from instrument_name import is_placeholder_name
from hvf_web import build_snapshot


@pytest.mark.parametrize("name,ticker,expected", [
    ("PH", "PH.L", True),             # bare-symbol form
    ("HAB.DE", "HAB.DE", True),       # real snapshot row: ticker as name
    ("601377.SS", "601377.SS", True),
    ("XYZ", "XYZ", True),
    ("xyz", "XYZ", True),
    ("", "PH.L", True),
    (None, "PH.L", True),
    ("USDJPY", "USDJPY=X", True),
    ("GDAXI", "^GDAXI", True),
    ("DCC plc", "DCC.L", False),
    ("DAX (Deutscher Aktienindex, Germany 40)", "^GDAXI", False),
])
def test_is_placeholder_name(name, ticker, expected):
    assert is_placeholder_name(name, ticker) is expected


def test_cached_placeholder_is_looked_up_again_and_replaced():
    names, unnamed, calls = {"PH.L": "PH"}, [], []
    def lookup(t):
        calls.append(t)
        return "Parker-Hannifin"
    assert build_snapshot._resolve_cached_name("PH.L", names, lookup, unnamed) == ("Parker-Hannifin", True)
    assert calls == ["PH.L"] and names == {"PH.L": "Parker-Hannifin"} and unnamed == []


def test_a_real_cached_name_is_not_looked_up():
    names = {"DCC.L": "DCC plc"}
    def lookup(t):
        raise AssertionError("looked up a cached real name")
    assert build_snapshot._resolve_cached_name("DCC.L", names, lookup, []) == ("DCC plc", False)


def test_placeholder_result_is_not_cached_and_is_listed():
    names, unnamed = {"PH.L": "PH"}, []
    assert build_snapshot._resolve_cached_name("PH.L", names, lambda t: t, unnamed) == ("PH.L", False)
    assert names == {"PH.L": "PH"} and unnamed == ["PH.L"]


def test_failing_lookup_is_logged_with_ticker_and_not_cached(caplog):
    names, unnamed = {}, []
    def lookup(t):
        raise ConnectionError("yahoo down")
    with caplog.at_level(logging.WARNING, logger="hvf_web.build"):
        assert build_snapshot._resolve_cached_name("HAB.DE", names, lookup, unnamed) == ("HAB.DE", False)
    assert "name lookup failed for HAB.DE: ConnectionError" in caplog.text
    assert names == {} and unnamed == ["HAB.DE"]


def test_company_name_never_returns_or_caches_a_placeholder(monkeypatch):
    monkeypatch.setattr(instrument_name, "_yf_info", lambda t: {"shortName": "PH"})
    monkeypatch.setattr(instrument_name, "_from_epic_lookup", lambda t: "")
    instrument_name._NAME_CACHE.clear()
    assert instrument_name.company_name("PH.L") == ""
    assert instrument_name._NAME_CACHE["PH.L"] == ""
    instrument_name._NAME_CACHE.clear()


def test_build_import_failure_raises_not_placeholder_fallback():
    src = open(build_snapshot.__file__, encoding="utf-8").read()
    assert "company_name = lambda" not in src
    assert "instrument_name import failed" in src


# ---- added by the independent test specialist 2026-11-22: mutants of scan_name and _from_epic_lookup no test killed -------
class _Conn:
    def __init__(self, desc):
        self.desc = desc

    def run(self, sql, **kw):
        return [[self.desc]]

    def close(self):
        pass


@pytest.mark.parametrize("desc,ticker,expected", [
    ("PH", "PH.L", ""),                                  # IG description is the bare symbol: no name (the old rule returned 'PH')
    ("PH.L", "PH.L", ""),
    ("DAX - Germany 40 (cash)", "^GDAXI", "DAX"),
    ("Parker-Hannifin Corp (24 Hours)", "PH.L", "Parker-Hannifin Corp"),
])
def test_the_epic_lookup_fallback_never_returns_a_placeholder(monkeypatch, desc, ticker, expected):
    import db_pool
    monkeypatch.setattr(db_pool, "get_db", lambda: _Conn(desc))
    assert instrument_name._from_epic_lookup(ticker) == expected


@pytest.mark.parametrize("stored,ticker,expected", [
    ("PH", "PH.L", ""), ("PH.L", "PH.L", ""), ("USDJPY", "USDJPY=X", ""), ("Parker-Hannifin", "PH.L", "Parker-Hannifin"),
])
def test_scan_name_treats_the_bare_symbol_as_no_name(monkeypatch, stored, ticker, expected):
    from hvf_web import server
    monkeypatch.setattr(server, "_record", lambda t: {"name": stored})
    assert instrument_name.scan_name(ticker) == expected


def test_a_failed_lookup_log_carries_the_type_only(caplog):
    def lookup(t):
        raise ConnectionError("host=db.example.invalid password=s3cret")
    with caplog.at_level(logging.WARNING, logger="hvf_web.build"):
        build_snapshot._resolve_cached_name("HAB.DE", {}, lookup, [])
    assert "ConnectionError" in caplog.text and "s3cret" not in caplog.text and "db.example" not in caplog.text


@pytest.mark.usefixtures("empty_database")
def test_build_re_resolves_a_cached_placeholder_saves_the_cache_and_lists_the_unnamed(monkeypatch, tmp_path, caplog):
    """The whole build() path with a stubbed universe, scan and name lookup (no network, no database)."""
    import run_hvf_report
    saved = []
    monkeypatch.setattr(run_hvf_report, "UNIVERSE", {"M": ["PH.L", "HAB.DE", "DCC.L"]})
    monkeypatch.setattr(build_snapshot, "SNAPSHOT", str(tmp_path / "snapshot.json"))
    monkeypatch.setattr(build_snapshot, "_load_name_cache", lambda: {"PH.L": "PH", "DCC.L": "DCC plc"})
    monkeypatch.setattr(build_snapshot, "_save_name_cache", lambda names: saved.append(dict(names)) or True)
    monkeypatch.setattr(instrument_name, "company_name", lambda t: {"PH.L": "Parker-Hannifin"}.get(t, ""))
    with caplog.at_level(logging.INFO, logger="hvf_web.build"):
        build_snapshot.build(scan_results={})
    import json
    recs = {r["ticker"]: r["name"] for r in json.load(open(tmp_path / "snapshot.json", encoding="utf-8"))["records"]}
    assert recs == {"PH.L": "Parker-Hannifin", "HAB.DE": "HAB.DE", "DCC.L": "DCC plc"}
    assert saved == [{"PH.L": "Parker-Hannifin", "DCC.L": "DCC plc"}]          # the placeholder replaced; HAB.DE not written
    assert "names: 1 instruments have no name: HAB.DE" in caplog.text
