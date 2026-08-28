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


# ======================================================================================================
# Squeeze History transfer cost (user 2026-08-25: "squeeze history is still very slow to load").
#
# The endpoint returned 14,927,347 bytes of UNCOMPRESSED JSON, measured against the live site, and
# roughly 21.7 s of a 25.9 s load was pure download on an already-warm server. Nothing can render until
# the last byte arrives, so a client-side "first hundred rows" strategy could not have helped.
# These tests pin the two fixes: gzip on the wire, and a shared short-lived payload cache.
# ======================================================================================================

def _sqh_rows(n):
    return [{"ticker": f"T{i}", "name": f"Instrument number {i}", "market": "LSE", "sector": "Industrials",
             "direction": "BULL", "timeframe": "daily-90", "outcome": "TARGET", "quality": 72,
             "rr": 3.4, "rvol": 1.8, "return_pct": 4.21} for i in range(n)]


def _serve_sqh(monkeypatch, rows, accept_encoding="gzip, deflate"):
    monkeypatch.setattr(server._wu, "name_for_token", lambda token: "alex")
    payload = {"rows": rows, "generated": "now", "refreshed_at": None, "data_through": "2026-08-25"}
    monkeypatch.setitem(server._SQH_CACHE, "ts", server._time.time())
    monkeypatch.setitem(server._SQH_CACHE, "data", payload)
    monkeypatch.setitem(server._SQH_CACHE, "gzip", None)
    headers = {"X-Auth": "t"}
    if accept_encoding:
        headers["Accept-Encoding"] = accept_encoding
    return server.app.test_client().get("/api/squeeze-history", headers=headers)


def test_squeeze_history_is_gzipped_and_round_trips_intact(monkeypatch):
    import gzip as _gz
    import json as _json
    rows = _sqh_rows(500)

    response = _serve_sqh(monkeypatch, rows)

    assert response.status_code == 200
    assert response.headers["Content-Encoding"] == "gzip"
    assert response.headers["Vary"] == "Accept-Encoding"
    # Lossless: every row must survive, or the table silently loses history.
    assert _json.loads(_gz.decompress(response.data))["rows"] == rows


def test_squeeze_history_compression_actually_shrinks_the_payload(monkeypatch):
    """The POINT is the transfer saving, so assert the saving — not merely that a header is present."""
    import json as _json
    rows = _sqh_rows(500)
    plain = len(_json.dumps({"rows": rows, "generated": "now", "refreshed_at": None,
                             "data_through": "2026-08-25"}).encode("utf-8"))

    compressed = len(_serve_sqh(monkeypatch, rows).data)

    assert compressed * 5 < plain, f"expected >5x saving, got {plain} -> {compressed}"


def test_squeeze_history_still_serves_plain_json_when_gzip_is_not_accepted(monkeypatch):
    response = _serve_sqh(monkeypatch, _sqh_rows(3), accept_encoding=None)

    assert response.status_code == 200
    assert "Content-Encoding" not in response.headers
    assert len(response.get_json()["rows"]) == 3


def test_squeeze_history_serves_the_cache_without_rebuilding(monkeypatch):
    """A warm cache must not touch the database — that 4.2 s rebuild is what the cache exists to avoid."""
    def _explode():
        raise AssertionError("the warm cache must not rebuild the payload")
    monkeypatch.setattr(server, "_load_snapshot", _explode)

    response = _serve_sqh(monkeypatch, _sqh_rows(2))

    assert response.status_code == 200


def test_squeeze_history_failure_is_never_cached(monkeypatch):
    """Reconstructs the bug the guard exists for: caching a failed build would replace good history with
    an empty table for every admin until the next rebuild."""
    import db_pool
    monkeypatch.setitem(server._SQH_CACHE, "ts", 0.0)
    monkeypatch.setitem(server._SQH_CACHE, "data", None)
    monkeypatch.setitem(server._SQH_CACHE, "gzip", None)
    monkeypatch.setitem(server._SQH_CACHE, "key", None)
    monkeypatch.setattr(db_pool, "get_db", lambda: (_ for _ in ()).throw(RuntimeError("db down")))

    assert server._build_sqh_payload() is None
    assert server._SQH_CACHE["data"] is None, "a failed build must not be cached"


def test_squeeze_history_cold_request_never_builds_on_the_request_thread(monkeypatch):
    """The symptom that forced this: on 2026-08-25 a cold request was left outstanding for over ten
    minutes without returning a byte. A cold caller must be answered immediately instead."""
    monkeypatch.setattr(server._wu, "name_for_token", lambda token: "alex")
    monkeypatch.setitem(server._SQH_CACHE, "ts", 0.0)
    monkeypatch.setitem(server._SQH_CACHE, "data", None)
    monkeypatch.setitem(server._SQH_CACHE, "gzip", None)
    kicked = []
    monkeypatch.setattr(server, "_kick_sqh_warm", lambda: kicked.append(True))
    monkeypatch.setattr(server, "_build_sqh_payload",
                        lambda: pytest.fail("the cold build must not run on the request thread"))

    response = server.app.test_client().get("/api/squeeze-history", headers={"X-Auth": "t"})

    assert response.status_code == 200
    assert response.get_json() == {"rows": [], "warming": True, "generated": ""}
    assert kicked == [True]


def test_squeeze_history_unchanged_data_is_not_rebuilt(monkeypatch):
    """squeeze_history is rewritten ONCE A DAY, so an unchanged freshness key must reuse the cached
    payload rather than spend the full build producing a byte-identical answer."""
    import db_pool
    kept = {"rows": [{"ticker": "KEEP"}], "refreshed_at": "2026-08-25 09:00",
            "data_through": "2026-08-25"}
    monkeypatch.setitem(server._SQH_CACHE, "data", kept)
    monkeypatch.setitem(server._SQH_CACHE, "key", ("2026-08-25 09:00", "2026-08-25"))
    monkeypatch.setitem(server._SQH_CACHE, "ts", 0.0)

    class _DB:
        def run(self, sql):
            if "max(refreshed_at)" in sql:
                return [("2026-08-25 09:00", "2026-08-25")]
            raise AssertionError("unchanged data must not run the expensive whole-table query")

        def close(self):
            pass

    monkeypatch.setattr(db_pool, "get_db", lambda: _DB())
    monkeypatch.setattr(server, "_load_snapshot",
                        lambda: pytest.fail("unchanged data must not rebuild the payload"))

    assert server._build_sqh_payload() is kept, "the cached payload object itself must be reused"


def test_squeeze_history_client_handles_the_warming_reply():
    """Source check, deliberately: the branch lives inside a fetch callback. Without it a {warming:true}
    reply would paint an EMPTY history and never poll again — a silent wrong answer, not a slow one."""
    from client_source import client_js

    handler = client_js().split("function renderSqueezeHist(")[1].split("\nfunction ")[0]

    assert "j.warming" in handler, "renderSqueezeHist must recognise the warming reply"
    assert "renderSqueezeHist,3000" in handler.replace(" ", ""), "it must poll again while warming"


# ======================================================================================================
# /api/positions must not disclose the live book to an unauthenticated caller (user 2026-08-25:
# "Performance tab showing 4,145 in tab name - if not loggedin - how can that tab have 4,145 rows?").
#
# Sweeping every /api route for a missing auth check found this one had none at all: an unauthenticated
# request returned the account's REAL open IG positions -- 17 instruments including 2202.HK, 4503.T,
# ASL.L, BP and BRWM.L. No sizes or P&L, but it discloses which instruments the account is in, live.
# ======================================================================================================

def test_positions_are_not_served_without_a_token(monkeypatch):
    monkeypatch.setattr(server._wu, "name_for_token", lambda token: "")

    def _explode():
        raise AssertionError("IG must not be contacted for an unauthenticated caller")
    monkeypatch.setattr(server, "_ig_shim_guard", _explode, raising=False)

    response = server.app.test_client().get("/api/positions")

    assert response.status_code == 401
    assert response.get_json() == {"positions": {}}, (
        "an unauthenticated caller must get an EMPTY map, so the page still renders but discloses "
        "nothing about the live book")


def test_performance_is_not_served_without_a_token(monkeypatch):
    """/api/performance returned all 4,932 recorded triggers -- entry, stop, target, R:R, quality, RVOL
    and outcome -- to anyone (user 2026-08-28). Its rows are only ever narrowed by the VIEWER'S OWN saved
    limits, which load with the token, so a logged-out visitor got the UNFILTERED superset: about ten
    times what the account owner sees on the same screen."""
    monkeypatch.setattr(server._wu, "name_for_token", lambda token: "")
    monkeypatch.setattr(server, "_kick_perf_warm",
                        lambda: pytest.fail("no build may be kicked for an anonymous caller"))

    response = server.app.test_client().get("/api/performance")

    assert response.status_code == 401
    assert response.get_json()["rows"] == [], "no trade rows may reach an unauthenticated caller"
