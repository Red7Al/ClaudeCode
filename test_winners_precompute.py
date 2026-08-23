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
    docs = {}
    _store(monkeypatch, docs)
    run_winners_precompute.build([1])
    built.clear()

    response = server.app.test_client().get("/api/winners?years=1")

    assert response.status_code == 200
    assert response.get_json()["rows"][0]["ticker"] == "T1"
    assert built == [], "the endpoint rebuilt the payload despite a valid stored copy"
