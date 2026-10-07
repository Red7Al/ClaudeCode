#!/usr/bin/env python3
# ======================================================================================================================
# File:         test_snapshot_summary.py
# Created:      2026-10-07
#
# The point of snapshot_summary is a COST reduction, so the test that matters is a cost test, not a shape test:
# with the values in the file, /api/records must read ZERO price bars. The account owner asked for exactly that
# after the data_freshness incident -- "anything on a request path gets a resource-cost test, connections opened and
# queries issued, stated as a number and asserted" (docs/HANDOVER-20260928.md section 5).
# ======================================================================================================================

import json

import pytest

import snapshot_summary


def _snap(**over):
    rec = {"ticker": "AAA", "name": "A Co", "market": "FTSE 100", "status": "TRIGGERED", "has_signal": True,
           "quality": 70, "entry": 10.0, "h3_date": "2026-09-01", "l3_date": "2026-09-02",
           "rvol": None, "volume_score": None, "above_vwap": None, "atr_expanding": None, "mcap": None}
    rec.update(over)
    return {"generated_utc": "2026-10-07T05:04:07+00:00", "records": [rec]}


def test_enrich_fills_every_summary_field(monkeypatch):
    """All five fields, from the server's OWN helpers so a stored value cannot drift from the endpoint."""
    import hvf_web.server as S
    monkeypatch.setattr(S, "_snapshot_rvol", lambda snap: {"AAA": 1.43})
    monkeypatch.setattr(S, "_snapshot_volscore", lambda snap: {"AAA": {"score": 6}})
    monkeypatch.setattr(S, "_live_vwap_atr", lambda snap: {"AAA": (True, False)})
    monkeypatch.setattr(S, "_mcap_map", lambda: {"AAA": 21_256_938_650.0})

    snap = snapshot_summary.enrich(_snap())
    r = snap["records"][0]

    assert r["rvol"] == 1.43
    assert r["volume_score"] == 6
    assert r["above_vwap"] is True
    assert r["atr_expanding"] is False
    assert r["mcap"] == 21_256_938_650.0
    assert snapshot_summary.carries_summary(snap), "the marker must be set, or the server will recompute"


def test_enrich_is_fail_soft_so_a_publication_never_dies_for_a_summary(monkeypatch):
    """A helper that cannot reach the database must not abort the publish.

    A snapshot published with an empty column degrades to the server recomputing it. A snapshot that fails
    to publish takes the site down. That trade is not close, so this is asserted rather than left to review.
    """
    import hvf_web.server as S

    def _boom(*a, **k):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(S, "_snapshot_rvol", _boom)
    monkeypatch.setattr(S, "_snapshot_volscore", _boom)
    monkeypatch.setattr(S, "_live_vwap_atr", _boom)
    monkeypatch.setattr(S, "_mcap_map", _boom)

    snap = snapshot_summary.enrich(_snap())          # must not raise
    assert snap["records"][0]["rvol"] is None


def test_a_stored_summary_is_not_overwritten_by_a_blank_recompute(monkeypatch):
    """A helper returning nothing for a ticker must leave an existing stored value alone."""
    import hvf_web.server as S
    monkeypatch.setattr(S, "_snapshot_rvol", lambda snap: {})
    monkeypatch.setattr(S, "_snapshot_volscore", lambda snap: {})
    monkeypatch.setattr(S, "_live_vwap_atr", lambda snap: {})
    monkeypatch.setattr(S, "_mcap_map", lambda: {})

    snap = snapshot_summary.enrich(_snap(rvol=1.9, mcap=5.0))
    assert snap["records"][0]["rvol"] == 1.9
    assert snap["records"][0]["mcap"] == 5.0


def test_carries_summary_is_not_fooled_by_a_day_with_no_triggers():
    """Keyed on the marker, never on 'does any record have a value'.

    RVOL is legitimately None on every non-TRIGGERED row, so on a quiet day a value-sniffing check would
    fall back to recomputing -- the expensive path -- on exactly the days there is least to compute.
    """
    enriched_but_quiet = {"records": [{"ticker": "AAA", "rvol": None, "volume_score": None}],
                          "summary_fields": list(snapshot_summary.SUMMARY_FIELDS)}
    assert snapshot_summary.carries_summary(enriched_but_quiet) is True
    assert snapshot_summary.carries_summary({"records": [{"ticker": "AAA", "rvol": 1.4}]}) is False
    assert snapshot_summary.carries_summary({}) is False
    assert snapshot_summary.carries_summary(None) is False


def test_api_records_reads_no_price_bars_when_the_file_carries_the_summary(monkeypatch):
    """THE COST TEST, and the reason this change exists.

    MEASURED before it: one call of the three-year map read 1,102,245 rows and the one-year map about
    400,000, per cold worker, against a 5 GB/month organisation-wide allowance. With the values in the file
    the endpoint must call NONE of the bar-reading builders. Asserted as a count, not as a timing.
    """
    import hvf_web.server as S

    calls = {"rvol": 0, "volscore": 0, "vwap_atr": 0, "mcap": 0}
    monkeypatch.setattr(S, "_snapshot_rvol", lambda snap: calls.__setitem__("rvol", calls["rvol"] + 1) or {})
    monkeypatch.setattr(S, "_snapshot_volscore", lambda snap: calls.__setitem__("volscore", calls["volscore"] + 1) or {})
    monkeypatch.setattr(S, "_live_vwap_atr", lambda snap: calls.__setitem__("vwap_atr", calls["vwap_atr"] + 1) or {})
    monkeypatch.setattr(S, "_mcap_map", lambda: calls.__setitem__("mcap", calls["mcap"] + 1) or {})
    monkeypatch.setattr(S, "_live_instrument_metrics", lambda snap: {})
    monkeypatch.setattr(S, "_snapshot_52wk", lambda snap: {})
    monkeypatch.setattr(S, "_snapshot_trigger_dates", lambda snap: {})

    snap = _snap(rvol=1.43, volume_score=6, above_vwap=True, atr_expanding=False, mcap=1.0)
    snap["summary_fields"] = list(snapshot_summary.SUMMARY_FIELDS)
    monkeypatch.setattr(S, "_load_snapshot", lambda: snap)
    monkeypatch.setattr(S._wu, "valid_tokens", lambda: {"tok"})

    client = S.app.test_client()
    body = json.loads(client.get("/api/records", headers={"X-Auth": "tok"}).data)

    assert calls == {"rvol": 0, "volscore": 0, "vwap_atr": 0, "mcap": 0}, \
        f"the file already holds these; {calls} means bars were read anyway"
    row = body["records"][0]
    assert (row["rvol"], row["volume_score"], row["above_vwap"], row["atr_expanding"], row["mcap"]) \
        == (1.43, 6, True, False, 1.0), "the stored values must reach the response unchanged"


def test_an_older_snapshot_without_the_marker_still_gets_computed(monkeypatch):
    """Backward compatibility: a snapshot published before this change must not lose its columns."""
    import hvf_web.server as S

    calls = {"n": 0}
    monkeypatch.setattr(S, "_snapshot_rvol", lambda snap: calls.__setitem__("n", calls["n"] + 1) or {"AAA": 2.2})
    monkeypatch.setattr(S, "_snapshot_volscore", lambda snap: {})
    monkeypatch.setattr(S, "_live_vwap_atr", lambda snap: {})
    monkeypatch.setattr(S, "_mcap_map", lambda: {})
    monkeypatch.setattr(S, "_live_instrument_metrics", lambda snap: {})
    monkeypatch.setattr(S, "_snapshot_52wk", lambda snap: {})
    monkeypatch.setattr(S, "_snapshot_trigger_dates", lambda snap: {})
    monkeypatch.setattr(S, "_load_snapshot", lambda: _snap())          # no summary_fields marker
    monkeypatch.setattr(S._wu, "valid_tokens", lambda: {"tok"})

    body = json.loads(S.app.test_client().get("/api/records", headers={"X-Auth": "tok"}).data)

    assert calls["n"] == 1, "without the marker the endpoint must still compute"
    assert body["records"][0]["rvol"] == 2.2
