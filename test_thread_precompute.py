"""/api/thread serves PRECOMPUTED text and must never try to build it on the web host.

WHY. The endpoint used to call _generate_x_drafts and publish_long_report_for, both of which reach
numpy. On IONOS numpy is SIGSYS-killed — the seccomp filter kills any process calling mbind(2), which
the bundled OpenBLAS does — and it kills the PROCESS, so the try/except around those calls could never
fire. The request died and the gateway returned HTTP 500 after ~120 seconds, measured 2026-09-18 and
again 2026-09-20. The X-thread card on the instrument panel rendered as nothing.

The text is now built nightly by run_thread_precompute.py where numpy works. These tests hold that
split in place: a regression that reintroduces the build on the request path takes the card down again,
and it fails as a 120-second timeout rather than as anything obviously wrong.
"""

import json

import pytest

import run_thread_precompute as rtp
from hvf_web import server


def test_the_endpoint_reads_the_store_and_never_builds(monkeypatch):
    """The whole point: serving must be a store read."""
    monkeypatch.setattr("web_store.load_json_store",
                        lambda key: {"records": {"ABC": ["lead tweet", "1/2 body", "2/2 body"]}}
                        if key == server._THREAD_STORE_KEY else None,
                        raising=False)
    server._PNG_CACHE.pop("thread:ABC", None)

    parts = server._thread_stored("ABC")

    assert parts == ["lead tweet", "1/2 body", "2/2 body"]


def test_a_ticker_with_no_stored_thread_returns_empty_not_an_error(monkeypatch):
    """Only carded instruments get a thread — 422 of 1,773 on 2026-09-20 — so absence is NORMAL and
    must not look like a failure."""
    monkeypatch.setattr("web_store.load_json_store", lambda key: {"records": {}}, raising=False)
    server._PNG_CACHE.pop("thread:NONE", None)

    assert server._thread_stored("NONE") == []


def test_an_unreachable_store_degrades_instead_of_raising(monkeypatch):
    """A Supabase blip must make the card empty, never 500 the request."""
    def _boom(key):
        raise RuntimeError("store down")
    monkeypatch.setattr("web_store.load_json_store", _boom, raising=False)
    server._PNG_CACHE.pop("thread:ABC", None)

    assert server._thread_stored("ABC") == []


def test_the_request_path_imports_nothing_numpy_bearing():
    """THE GUARD THAT MATTERS. Naming either generator in this function is what killed the endpoint.

    Checked as source text rather than by importing them, because importing is precisely the fatal act
    and a test that reproduced it would kill the test runner on the host it is protecting.
    """
    import inspect
    src = inspect.getsource(server._thread_stored) + inspect.getsource(server.api_thread)
    for banned in ("intraday_signals", "publish_long_report_for", "_generate_x_drafts",
                   "price_action", "yfinance", "matplotlib"):
        assert banned not in src.split('"""')[-1], (
            f"/api/thread must not reach {banned} on the request path — numpy is SIGSYS-killed on IONOS")


def test_the_precompute_refuses_to_store_an_empty_result(monkeypatch, tmp_path):
    """A bad run must leave the card STALE, never blank — the rule the fundamentals and winners
    precomputes both apply."""
    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps({"generated_utc": "2026-09-20T19:18:08Z", "records": []}), encoding="utf-8")
    monkeypatch.setattr(rtp, "SNAPSHOT", str(snap))
    saved = []
    monkeypatch.setattr("web_store.save_json_store",
                        lambda k, v: saved.append(k) or True, raising=False)

    assert rtp.build() == 2, "an empty snapshot must fail the job"
    assert not saved, "nothing may be written when there is nothing to write"


def test_the_precompute_only_takes_records_that_have_a_card(monkeypatch, tmp_path):
    """`_card` is what the panel's thread describes; a record without one has no thread to build."""
    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps({"generated_utc": "x", "records": [
        {"ticker": "WITH", "_card": {"ticker": "WITH"}, "name": "With Card", "market": "FTSE 100"},
        {"ticker": "WITHOUT", "name": "No Card", "market": "FTSE 100"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(rtp, "SNAPSHOT", str(snap))

    recs, generated = rtp._records(str(snap))

    assert [r["ticker"] for r in recs] == ["WITH"]
    assert generated == "x"


def test_the_stored_document_is_keyed_to_the_scan_it_describes(monkeypatch, tmp_path):
    """Text built from a different scan would describe levels the site is no longer showing, so the
    dataset travels with the payload."""
    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps({"generated_utc": "2026-09-20T19:18:08Z", "records": [
        {"ticker": "ABC", "_card": {"ticker": "ABC"}, "name": "ABC plc", "market": "FTSE 100"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(rtp, "SNAPSHOT", str(snap))
    monkeypatch.setattr(rtp, "_parts_for", lambda rec: ["a part"])
    stored = {}
    monkeypatch.setattr("web_store.save_json_store",
                        lambda k, v: stored.update({k: v}) or True, raising=False)

    assert rtp.build() == 0
    doc = stored[rtp.STORE_KEY]
    assert doc["dataset"] == "2026-09-20T19:18:08Z"
    assert doc["count"] == 1
    assert doc["records"]["ABC"] == ["a part"]
