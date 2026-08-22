"""Backend tests for the Instruments tab (ChangeRequest P-08, 2026-08-07)."""

import pytest

from hvf_web import server


def test_snapshot_52wk_computes_trailing_low_and_high(monkeypatch):
    snap = {"generated_utc": "2026-08-07T05:30:00Z",
            "records": [{"ticker": "ABC"}, {"ticker": "XYZ"}]}
    monkeypatch.setitem(server._WK52_CACHE, "gen", None)
    monkeypatch.setitem(server._WK52_CACHE, "data", {})

    class FakeDb:
        def close(self):
            pass

    monkeypatch.setattr("db_pool.get_db", lambda: FakeDb(), raising=False)
    monkeypatch.setattr(server, "_perf_bars", lambda db, cutoff, lookback_days=0: {
        "ABC": [("2026-01-01", 12.0, 8.0, 10.0, 1000), ("2026-06-01", 20.0, 15.0, 18.0, 2000)],
        "XYZ": [("2026-01-01", 5.0, 3.0, 4.0, 500)],
    })

    out = server._snapshot_52wk(snap)

    assert out["ABC"] == (8.0, 20.0)
    assert out["XYZ"] == (3.0, 5.0)


def test_snapshot_52wk_is_cached_per_snapshot_generation(monkeypatch):
    snap = {"generated_utc": "2026-08-07T05:30:00Z", "records": [{"ticker": "ABC"}]}
    monkeypatch.setitem(server._WK52_CACHE, "gen", None)
    monkeypatch.setitem(server._WK52_CACHE, "data", {})
    calls = []

    class FakeDb:
        def close(self):
            pass

    monkeypatch.setattr("db_pool.get_db", lambda: FakeDb(), raising=False)

    def _fake_bars(db, cutoff, lookback_days=0):
        calls.append(1)
        return {"ABC": [("2026-01-01", 12.0, 8.0, 10.0, 1000)]}

    monkeypatch.setattr(server, "_perf_bars", _fake_bars)

    server._snapshot_52wk(snap)
    server._snapshot_52wk(snap)   # same generated_utc -> cache hit, no second DB round trip

    assert len(calls) == 1


def _identity(monkeypatch, name, *, admin=False):
    monkeypatch.setattr(server._wu, "name_for_token", lambda token: name if token == "token" else "")
    monkeypatch.setattr(server._wu, "valid_tokens", lambda: {"token"} if name else set())
    monkeypatch.setattr(server._wu, "is_admin", lambda candidate: admin and candidate == name)


def _fake_snapshot():
    return {"generated_utc": "2026-08-07T05:30:00Z",
            "records": [{"ticker": "ABC", "name": "Alpha Beta Corp", "market": "US", "sector": "Tech",
                        "direction": "BULL", "h3_date": "2026-08-01", "l3_date": None,
                        "has_signal": True, "status": "TRIGGERED", "quality": 70, "rr": 4.0,
                        "location": "US"}]}


def test_api_records_includes_52wk_fields_when_logged_in(monkeypatch):
    monkeypatch.setattr(server, "_load_snapshot", _fake_snapshot)
    monkeypatch.setattr(server, "_snapshot_52wk", lambda snap: {"ABC": (8.0, 20.0)})
    monkeypatch.setattr(server, "_snapshot_rvol", lambda snap: {})
    monkeypatch.setattr(server, "_snapshot_volscore", lambda snap: {})
    monkeypatch.setattr(server, "_live_vwap_atr", lambda snap: {})
    monkeypatch.setattr(server, "_live_instrument_metrics", lambda snap: {})
    _identity(monkeypatch, "Silver")

    response = server.app.test_client().get("/api/records", headers={"X-Auth": "token"})

    assert response.status_code == 200
    row = response.get_json()["records"][0]
    assert row["wk52_low"] == 8.0 and row["wk52_high"] == 20.0
    assert row["direction"] == "BULL"   # authed sees everything, including the restricted-when-public fields


# Needs live runtime state that a clean CI checkout does not have (user 2026-08-15).
@pytest.mark.live_state
def test_api_records_includes_52wk_fields_when_logged_out(monkeypatch):
    """Public teaser payload strips direction/quality/etc via _PUBLIC_FIELDS, but 52wk Low/High is
    deliberately public (Instruments tab shows it to logged-out visitors too)."""
    monkeypatch.setattr(server, "_load_snapshot", _fake_snapshot)
    monkeypatch.setattr(server, "_snapshot_52wk", lambda snap: {"ABC": (8.0, 20.0)})

    response = server.app.test_client().get("/api/records")

    assert response.status_code == 200
    j = response.get_json()
    assert j["limited"] is True
    row = j["records"][0]
    assert row["wk52_low"] == 8.0 and row["wk52_high"] == 20.0
    assert row["market"] == "US" and row["location"] == "US"
    assert "quality" not in row and "rr" not in row   # unrelated to this feature, still correctly stripped
    assert "current_rvol" not in row and "current_above_vwap" not in row


def test_live_instrument_metrics_cover_rows_without_squeezes_and_vwap_is_literal(monkeypatch):
    import volume_score

    snap = {"generated_utc": "2026-08-13T10:00:00Z",
            "records": [{"ticker": "BEARISH", "direction": "BEAR", "has_signal": False}]}
    monkeypatch.setitem(server._LIVE_INSTRUMENT_METRICS_CACHE, "gen", None)
    monkeypatch.setitem(server._LIVE_INSTRUMENT_METRICS_CACHE, "data", {})

    class FakeDb:
        def close(self):
            pass

    bars = [("2026-08-12", 11.0, 9.0, 10.0, 1000),
            ("2026-08-13", 12.0, 10.0, 11.0, 1200)]
    monkeypatch.setattr("db_pool.get_db", lambda: FakeDb(), raising=False)
    monkeypatch.setattr(server, "_perf_bars", lambda db, cutoff, lookback_days=0: {"BEARISH": bars})
    monkeypatch.setattr(volume_score, "_rvol_at", lambda got, index: 1.2)
    vwap_directions = []
    monkeypatch.setattr(volume_score, "_above_vwap",
                        lambda got, index, bull: vwap_directions.append(bull) or True)
    monkeypatch.setattr(volume_score, "_atr_expanding", lambda got, index: False)

    result = server._live_instrument_metrics(snap)["BEARISH"]

    # RVOL is only actionable when its provenance and completeness state are
    # explicit.  Keep this contract aligned with the public Instruments API;
    # a stale exact-dictionary expectation previously made the CI gate red
    # despite the production response being correctly enriched.
    assert result == {"rvol": 1.2, "rvol_date": "2026-08-13",
                      "above_vwap": True, "atr_expanding": False,
                      "date": "2026-08-13", "source": None,
                      "status": "complete"}
    assert vwap_directions == [True]


def test_squeeze_history_requires_a_login(monkeypatch):
    monkeypatch.setattr(server._wu, "name_for_token", lambda token: "")

    response = server.app.test_client().get("/api/squeeze-history")

    assert response.status_code == 401
    assert response.get_json()["error"] == "login required"
