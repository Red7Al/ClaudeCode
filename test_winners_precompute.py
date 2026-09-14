"""The precomputed winners payload must be the SAME answer as the live build, only faster.

WHY. Building one window costs ~33s and Best Settings asks for two on every visit, so the payloads are
built ahead of time and stored. The risk that matters is not slowness, it is a stored copy that quietly
disagrees with what the endpoint would have produced -- a lookalike build of a different population. That
is the failure memory results-winners-same-dataset exists to prevent, so these tests check the answer,
not just the plumbing.

The server accepts a stored payload only when it was built from the dataset now in play and is under a
day old, so a missed or failed precompute is slow, never wrong.
"""

import time

import pytest

import run_winners_precompute
from hvf_web import server


@pytest.fixture
def fake_dataset(monkeypatch):
    """A snapshot as the runner actually sees one: a generation time AND records.

    It carried only generated_utc until 2026-09-14, which is a shape the real thing never takes -- a
    published snapshot always has records. That mattered the moment build() started refusing to run
    without them: every test using this fixture looked like the very outage the new guard exists to
    stop. A fixture invented rather than derived certifies the assumption instead of testing it
    (AGENTS.md rule 3); this one now mirrors hvf_web/snapshot.json's real shape.
    """
    monkeypatch.setattr(server, "_load_snapshot", lambda: {
        "generated_utc": "2026-08-23T06:00:00Z", "count": 1,
        "records": [{"ticker": "RR.L", "name": "Rolls-Royce Holdings plc", "location": "UK"}]})
    return "2026-08-23T06:00:00Z"


@pytest.fixture
def built(monkeypatch):
    """A deterministic stand-in for the expensive build, so the tests exercise selection not replay.

    Stands in for the PERFORMANCE build too (added 2026-09-13, when build() began precomputing that
    payload alongside the windows). Without it every test in this file would run the real
    _build_perf_payload -- 34.5 seconds against the live database -- turning an offline suite into a
    live_state one by accident.
    """
    calls = []

    def fake(years):
        calls.append(years)
        return {"rows": [{"ticker": f"T{years}", "perf": 1.5 * years}], "months": years * 12,
                "generated": "2026-08-23 06:00 UTC"}

    def fake_perf():
        calls.append("perf")
        return {"rows": [{"ticker": "PERF", "perf": 2.5}], "generated": "2026-08-23 06:00 UTC"}

    monkeypatch.setattr(server, "_winners_payload", fake)
    monkeypatch.setattr(server, "_build_perf_payload", fake_perf)
    return calls


def _store(monkeypatch, docs):
    class _FakeStore:
        saved = {}

        @staticmethod
        def load_json_store(key):
            return docs.get(key)

        @staticmethod
        def save_json_store(key, payload):
            _FakeStore.saved[key] = payload
            docs[key] = payload
            return True

    import sys
    monkeypatch.setitem(sys.modules, "web_store", _FakeStore)
    return _FakeStore


# ------------------------------------------------------------------------------------------------------
# The answer must not change
# ------------------------------------------------------------------------------------------------------

def test_the_stored_payload_is_what_the_live_build_produced(monkeypatch, fake_dataset, built):
    docs = {}
    _store(monkeypatch, docs)

    assert run_winners_precompute.build([1, 3]) == 0

    for years in (1, 3):
        stored = server._winners_stored(years)
        assert stored == server._winners_payload(years), (
            f"the stored {years}-year payload differs from a live build of the same window")


def test_each_window_is_stored_under_its_own_key(monkeypatch, fake_dataset, built):
    """One window must never be served for another -- the blank-feature bug in a new disguise."""
    docs = {}
    _store(monkeypatch, docs)
    run_winners_precompute.build([1, 3])

    assert server._winners_stored(1)["rows"][0]["ticker"] == "T1"
    assert server._winners_stored(3)["rows"][0]["ticker"] == "T3"
    assert server._winners_store_key(1) != server._winners_store_key(3)


# ------------------------------------------------------------------------------------------------------
# The /api/performance payload (owner 2026-09-13: "performance cards not showing if user not logged on")
# ------------------------------------------------------------------------------------------------------

def test_the_performance_payload_is_precomputed_alongside_the_windows(monkeypatch, fake_dataset, built):
    """It must ride the SAME already-scheduled job. A payload built by a script nothing invokes is this
    repository's signature defect, and the endpoint would stay stuck on its warming marker."""
    docs = {}
    _store(monkeypatch, docs)

    assert run_winners_precompute.build([1, 3]) == 0
    assert "perf" in built, "build() did not precompute the performance payload"
    assert server._PERF_STORE_KEY in docs, "the performance payload was never stored"


def test_the_endpoint_serves_the_stored_performance_payload_without_warming(monkeypatch, fake_dataset, built):
    """THE ACTUAL BUG. /api/performance returned {"rows":[],"warming":true} forever, because its warm
    thread needs 34.5s and does not survive the response on the shared host. With a stored payload the
    endpoint must answer from it and never start a build at all."""
    docs = {}
    _store(monkeypatch, docs)
    run_winners_precompute.build([1])

    monkeypatch.setattr(server, "_PERF_CACHE", {"ts": 0.0, "data": None, "gzip": None})
    kicked = []
    monkeypatch.setattr(server, "_kick_perf_warm", lambda: kicked.append(1))

    stored = server._perf_stored()
    assert stored is not None and stored["rows"], "the stored performance payload was not readable"

    monkeypatch.setattr(server._wu, "name_for_token", lambda t: "Alex")
    with server.app.test_request_context("/api/performance", headers={"X-Auth": "x"}):
        body = server.api_performance()
    assert not kicked, "a build was started even though a precomputed payload was available"
    assert server._PERF_CACHE["data"] is not None, "the stored payload was not adopted into the cache"
    assert body is not None


def test_a_performance_payload_from_another_dataset_is_rejected(monkeypatch, fake_dataset, built):
    """Slow is acceptable; wrong is not. The same rule the winners windows follow."""
    docs = {}
    _store(monkeypatch, docs)
    run_winners_precompute.build([1])
    docs[server._PERF_STORE_KEY]["dataset"] = "2026-08-22T21:37:45Z"      # an earlier scan

    assert server._perf_stored() is None


def test_a_stale_performance_payload_is_rejected(monkeypatch, fake_dataset, built):
    docs = {}
    _store(monkeypatch, docs)
    run_winners_precompute.build([1])
    docs[server._PERF_STORE_KEY]["built_at"] = time.time() - (server._WINNERS_STORE_MAX_AGE + 60)

    assert server._perf_stored() is None


def test_an_empty_performance_population_is_never_stored(monkeypatch, fake_dataset):
    """Storing zero rows would serve "no trades" quickly instead of the truth slowly -- the page would
    look answered rather than broken, which is worse."""
    docs = {}
    _store(monkeypatch, docs)
    monkeypatch.setattr(server, "_build_perf_payload", lambda: {"rows": [], "generated": ""})

    assert run_winners_precompute.build_performance(fake_dataset) is False
    assert server._PERF_STORE_KEY not in docs


def test_a_failed_performance_build_does_not_stop_the_windows(monkeypatch, fake_dataset, built):
    """One payload failing must not cost the others. build() reports the failure and stores the rest."""
    docs = {}
    _store(monkeypatch, docs)

    def boom():
        raise RuntimeError("replay exploded")
    monkeypatch.setattr(server, "_build_perf_payload", boom)

    assert run_winners_precompute.build([1, 3]) == 1        # exactly one failure, the performance one
    assert server._winners_stored(1) is not None
    assert server._winners_stored(3) is not None
    assert server._PERF_STORE_KEY not in docs


# ------------------------------------------------------------------------------------------------------
# Fall back to the live build rather than serve something wrong
# ------------------------------------------------------------------------------------------------------

def test_a_payload_from_another_dataset_is_rejected(monkeypatch, fake_dataset, built):
    docs = {}
    _store(monkeypatch, docs)
    run_winners_precompute.build([1])
    docs[server._winners_store_key(1)]["dataset"] = "2026-08-22T21:37:45Z"   # an earlier scan

    assert server._winners_stored(1) is None, "a payload built from a different scan must not be served"


def test_a_stale_payload_is_rejected(monkeypatch, fake_dataset, built):
    docs = {}
    _store(monkeypatch, docs)
    run_winners_precompute.build([1])
    docs[server._winners_store_key(1)]["built_at"] = time.time() - server._WINNERS_STORE_MAX_AGE - 1

    assert server._winners_stored(1) is None


def test_a_missing_or_unreadable_store_falls_back(monkeypatch, fake_dataset, built):
    _store(monkeypatch, {})
    assert server._winners_stored(1) is None

    class _Broken:
        @staticmethod
        def load_json_store(_key):
            raise RuntimeError("Supabase unavailable")

    import sys
    monkeypatch.setitem(sys.modules, "web_store", _Broken)
    assert server._winners_stored(1) is None, "an unreachable store must degrade to the live build"


def test_garbage_in_the_store_is_ignored(monkeypatch, fake_dataset, built):
    for junk in ({"payload": "not a dict"}, {"no_payload": 1}, [], "text", None):
        _store(monkeypatch, {server._winners_store_key(1): junk})
        assert server._winners_stored(1) is None


# ------------------------------------------------------------------------------------------------------
# The precompute must not store a wrong answer
# ------------------------------------------------------------------------------------------------------

# These two use `built` only for its performance stub: without it build() would run the REAL
# _build_perf_payload against the live database (measured at 34.5s), quietly turning this offline test
# into a live_state one. Each then overrides the WINDOW build with the failure it is actually testing,
# and asserts on the window keys rather than on `docs == {}`, because the performance payload is
# legitimately stored in both cases -- one payload failing must not suppress another.

def test_nothing_is_stored_when_no_snapshot_is_loaded(monkeypatch, built):
    """A runner with no snapshot must not overwrite good payloads with degraded ones.

    MEASURED IN PRODUCTION 2026-09-14: the 05:24 run stored 6,444 annual and 17,477 three-year rows with
    ZERO real company names and ZERO locations, replacing 6,252 and 6,441 from the evening before. The
    live snapshot matched its dataset key, so the degraded copy was SERVED -- all working day, until the
    evening snapshot job rebuilt it correctly. _sqa_all_rows takes name, sector, location and
    current_price from the snapshot, and a bare runner has none: snapshot.json is not in git and Supabase
    Storage has returned 402 since 2026-08-16.

    The dataset key is deliberately left VALID here. The point is that a correct key is not sufficient --
    it was the reassuring signal that let this through.
    """
    monkeypatch.setattr(server, "_load_snapshot", lambda: {"generated_utc": "2026-09-14T04:28:59Z",
                                                           "count": 0, "records": []})
    docs = {}
    _store(monkeypatch, docs)

    assert run_winners_precompute.build([1, 3]) == 3      # both windows AND the performance payload
    assert docs == {}, "a payload was stored despite there being no snapshot to name or locate its rows"
    assert not built, "nothing should even be BUILT once we know it cannot be stored"


def test_an_empty_population_is_never_stored(monkeypatch, fake_dataset, built):
    """Storing zero rows would serve "no trades" quickly instead of the truth slowly."""
    monkeypatch.setattr(server, "_winners_payload", lambda years: {"rows": [], "months": 12})
    docs = {}
    _store(monkeypatch, docs)

    assert run_winners_precompute.build([1]) == 1
    assert server._winners_store_key(1) not in docs


def test_a_failed_build_is_reported_and_not_stored(monkeypatch, fake_dataset, built):
    monkeypatch.setattr(server, "_winners_payload",
                        lambda years: (_ for _ in ()).throw(RuntimeError("database down")))
    docs = {}
    _store(monkeypatch, docs)

    assert run_winners_precompute.build([1, 3]) == 2
    assert server._winners_store_key(1) not in docs
    assert server._winners_store_key(3) not in docs


def test_dry_run_writes_nothing(monkeypatch, fake_dataset, built):
    docs = {}
    _store(monkeypatch, docs)

    assert run_winners_precompute.build([1, 3], dry_run=True) == 0
    assert docs == {}


def test_the_endpoint_prefers_the_store_and_skips_the_build(monkeypatch, fake_dataset, built):
    """The whole point: a warm store must not call the expensive builder at all."""
    # /api/winners now serves per-trade rows only to a signed-in caller (user 2026-09-01: the
    # transaction evidence must not reach anyone logged out). This test is about the PAYLOAD, so it
    # authenticates rather than asserting the anonymous shape.
    monkeypatch.setattr(server._wu, "name_for_token", lambda token: "tester")
    docs = {}
    _store(monkeypatch, docs)
    run_winners_precompute.build([1])
    built.clear()

    response = server.app.test_client().get("/api/winners?years=1")

    assert response.status_code == 200
    assert response.get_json()["rows"][0]["ticker"] == "T1"
    assert built == [], "the endpoint rebuilt the payload despite a valid stored copy"


# ------------------------------------------------------------------------------------------------------
# The dataset key must be the one the SERVER compares against.
#
# THE REGRESSION (2026-08-23). The first live run stored 3,782 and 11,672 rows with dataset="" because a
# GitHub runner has no built snapshot, and the server -- correctly -- rejected both. The precompute
# reported success while achieving nothing, which is the worst kind of failure: silent.
# ------------------------------------------------------------------------------------------------------

def test_the_local_snapshot_is_preferred_for_the_key(monkeypatch):
    """Straight after a snapshot build the runner HAS the file, and it cannot race a later publish."""
    monkeypatch.setattr(server, "_load_snapshot", lambda: {"generated_utc": "2026-08-23T09:00:00Z"})

    assert run_winners_precompute._dataset_key() == "2026-08-23T09:00:00Z"


def test_the_live_site_is_the_fallback_key(monkeypatch):
    """The standalone scheduled run has no snapshot, so it asks the site what it is serving."""
    monkeypatch.setattr(server, "_load_snapshot", lambda: {})

    import io, json, urllib.request
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _ctx(io.BytesIO(json.dumps(
                            {"generated_utc": "2026-08-23T10:00:00Z"}).encode())))

    assert run_winners_precompute._dataset_key() == "2026-08-23T10:00:00Z"


class _ctx:
    def __init__(self, fh): self.fh = fh
    def __enter__(self): return self.fh
    def __exit__(self, *a): return False


def test_nothing_is_stored_without_a_dataset_key(monkeypatch, built):
    """Storing a payload the server will always reject is worse than not storing one: it reports success."""
    monkeypatch.setattr(run_winners_precompute, "_dataset_key", lambda: "")
    docs = {}
    _store(monkeypatch, docs)

    assert run_winners_precompute.build([1, 3]) == 2, "a missing key must be reported as failure"
    assert docs == {}, "a payload with no dataset key must never be stored"


def test_a_stored_key_of_empty_string_is_rejected_by_the_server(monkeypatch, fake_dataset, built):
    """Belt and braces: even if such a document existed, the server must not serve it."""
    _store(monkeypatch, {server._winners_store_key(1): {
        "payload": {"rows": [{"ticker": "T1"}]}, "dataset": "", "built_at": time.time()}})

    assert server._winners_stored(1) is None


def test_the_snapshot_workflow_warms_the_payloads():
    """The daily job alone is not enough: ANY new snapshot invalidates the stored copies."""
    from pathlib import Path
    wf = Path(__file__).parent / ".github" / "workflows" / "trading-scanner-snapshot.yml"
    text = wf.read_text(encoding="utf-8")

    assert "run_winners_precompute.py" in text, (
        "the snapshot publish must refresh the payloads it invalidates")
    assert "continue-on-error: true" in text, "a cache warm-up must not fail the snapshot publication"
