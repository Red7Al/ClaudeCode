# ======================================================================================================
# Field coverage audit (P-31 follow-up, user 2026-09-06).
#
# "it's very dull for me to keep reporting missing data - you need to manage this".
#
# Over one session the account owner reported blank sector, blank market cap, and blank VWAP / ATR /
# VolumeScore. Every one was found by a person looking at a screen. Nothing measured the fields the site
# actually renders, so a coverage regression stayed invisible until somebody noticed a dash.
#
# These tests pin the two decisions that make the audit worth having rather than noisy:
#   * a legitimately-blank field must not count against coverage, and
#   * a drop is only ALERTED when the two runs are actually comparable.
# ======================================================================================================

import pytest

import run_data_quality_audit as audit


@pytest.fixture
def store(monkeypatch):
    """An in-memory web_store, so the audit never touches the shared Supabase copy."""
    import web_store
    saved = {}
    monkeypatch.setattr(web_store, "load_json_store", lambda k: saved.get(k))
    monkeypatch.setattr(web_store, "save_json_store",
                        lambda k, v: (saved.__setitem__(k, v), True)[1])
    return saved


def _snap(records):
    return {"records": records}


def _patch_snapshot(monkeypatch, records, mcaps=None):
    from hvf_web import server
    monkeypatch.setattr(server, "_load_snapshot", lambda: _snap(records))
    monkeypatch.setattr(server, "_mcap_map", lambda: mcaps or {})


def test_a_market_that_never_has_a_sector_does_not_count_against_coverage(monkeypatch, store):
    """Government bonds, FX, commodities, indices and crypto have no sector AT ALL. Counting them as
    missing would park sector coverage permanently below any threshold, for a reason nobody can act on --
    and an alert nobody can act on is how a channel stops being read."""
    _patch_snapshot(monkeypatch, [
        {"ticker": "BP.L", "name": "BP", "market": "FTSE 100", "location": "UK", "sector": "Energy"},
        {"ticker": "GBPUSD", "name": "Cable", "market": "FX", "location": "UK", "sector": ""},
        {"ticker": "XAUUSD", "name": "Gold", "market": "Commodities", "location": "US", "sector": None},
    ], mcaps={"BP.L": 1, "GBPUSD": 1, "XAUUSD": 1})

    report = audit._audit_field_coverage(alert=False)

    assert report["coverage"]["sector"]["applicable"] == 1, "only the equity is judged on sector"
    assert report["coverage"]["sector"]["pct"] == 100.0
    assert report["coverage"]["name"]["applicable"] == 3, "every row is judged on name"


def test_a_genuine_blank_is_counted_and_an_example_is_kept(monkeypatch, store):
    """The count says something regressed; the examples say WHAT, so the next step does not begin with
    another manual hunt."""
    _patch_snapshot(monkeypatch, [
        {"ticker": "AAA", "name": "A", "market": "FTSE 100", "location": "UK", "sector": "Energy"},
        {"ticker": "BBB", "name": "B", "market": "FTSE 100", "location": "UK", "sector": ""},
    ], mcaps={"AAA": 5})

    report = audit._audit_field_coverage(alert=False)

    assert report["coverage"]["sector"]["pct"] == 50.0
    assert [e["ticker"] for e in report["blank_examples"]["sector"]] == ["BBB"]
    assert report["coverage"]["mcap"]["pct"] == 50.0, "a market cap of 0/None is not a market cap"


def test_a_drop_against_a_comparable_run_alerts(monkeypatch, store):
    _patch_snapshot(monkeypatch, [
        {"ticker": f"T{i}", "name": "n", "market": "FTSE 100", "location": "UK",
         "sector": "" if i < 10 else "Energy"} for i in range(100)],
        mcaps={f"T{i}": 1 for i in range(100)})
    store["field_coverage_audit"] = {"audit_date": "2026-09-05T00:00:00Z", "rows": 100,
                                     "coverage": {"sector": {"pct": 100.0}}}
    posted = []
    monkeypatch.setattr(audit, "_post_coverage_slack", lambda d, c: posted.append(d))

    report = audit._audit_field_coverage()

    assert report["comparable"] is True
    assert [d["field"] for d in report["drops"]] == ["sector"]
    assert posted, "a real regression must be reported without anyone having to look for it"


def test_a_drop_across_a_different_population_is_recorded_but_not_alerted(monkeypatch, store):
    """Coverage is a percentage, so it survives the universe growing a little. It does not survive the
    universe changing SHAPE -- a stale or partial snapshot has a different market mix, and comparing
    across that measures the mix, not a regression. This is a real hazard, not a theoretical one: the
    baseline was first written from a 1,421-row local snapshot while the live universe held 1,773."""
    _patch_snapshot(monkeypatch, [
        {"ticker": f"T{i}", "name": "n", "market": "FTSE 100", "location": "UK", "sector": ""}
        for i in range(50)], mcaps={f"T{i}": 1 for i in range(50)})
    store["field_coverage_audit"] = {"audit_date": "2026-09-05T00:00:00Z", "rows": 1773,
                                     "coverage": {"sector": {"pct": 100.0}}}
    posted = []
    monkeypatch.setattr(audit, "_post_coverage_slack", lambda d, c: posted.append(d))

    report = audit._audit_field_coverage()

    assert report["comparable"] is False
    assert report["drops"], "the drop is still RECORDED -- it is only the alert that is withheld"
    assert not posted, "alerting across populations is how a channel stops being read"


def test_an_empty_snapshot_raises_rather_than_storing_a_green_report(monkeypatch, store):
    """A zero-row audit that stores 100% coverage would conceal the fact that nothing was measured --
    the exact failure the sibling metric audit already guards against."""
    _patch_snapshot(monkeypatch, [])
    with pytest.raises(RuntimeError):
        audit._audit_field_coverage(alert=False)
