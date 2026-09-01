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
    monkeypatch.setattr(server, "_load_snapshot", lambda: {"generated_utc": "2026-08-23T06:00:00Z"})
    return "2026-08-23T06:00:00Z"


@pytest.fixture
def built(monkeypatch):
    """A deterministic stand-in for the expensive build, so the tests exercise selection not replay."""
    calls = []

    def fake(years):
        calls.append(years)
        return {"rows": [{"ticker": f"T{years}", "perf": 1.5 * years}], "months": years * 12,
                "generated": "2026-08-23 06:00 UTC"}

    monkeypatch.setattr(server, "_winners_payload", fake)
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

def test_an_empty_population_is_never_stored(monkeypatch, fake_dataset):
    """Storing zero rows would serve "no trades" quickly instead of the truth slowly."""
    monkeypatch.setattr(server, "_winners_payload", lambda years: {"rows": [], "months": 12})
    docs = {}
    _store(monkeypatch, docs)

    assert run_winners_precompute.build([1]) == 1
    assert docs == {}


def test_a_failed_build_is_reported_and_not_stored(monkeypatch, fake_dataset):
    monkeypatch.setattr(server, "_winners_payload",
                        lambda years: (_ for _ in ()).throw(RuntimeError("database down")))
    docs = {}
    _store(monkeypatch, docs)

    assert run_winners_precompute.build([1, 3]) == 2
    assert docs == {}


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
