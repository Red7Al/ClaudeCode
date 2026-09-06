# ======================================================================================================
# The Insights page (P-37, user 2026-09-06).
#
# "Add an insights tab (that can be disabled in user settings - tab visibility) to give insights e.g.
# data and chart to illustrate the increase in bear direction squeezes at the moment. Also the analysis
# done on performance for mcap below 100bn and circa 10bn. Continue to check this insights as they are
# published. Keep this to just one page of insights at max to not overface us."
#
# Two requirements in that sentence are easy to lose and hard to notice losing:
#
#   * ONE PAGE. Enforced in code, not by editorial restraint, or it becomes a report nobody reads.
#   * "CONTINUE TO CHECK". An insight is a claim about the past. Recomputed nightly it will eventually
#     stop being true, and the dangerous failure is not that it disappears -- it is that it keeps its
#     confident wording after the evidence has gone. So every insight re-tests itself on every build and
#     publishes the verdict.
# ======================================================================================================

import pytest

from hvf_web import server


def test_the_page_is_capped_so_it_cannot_grow_into_a_report():
    assert server._INSIGHT_LIMIT <= 4, "one page was the requirement; this is how it stays one page"
    assert len(server._INSIGHT_BUILDERS) <= server._INSIGHT_LIMIT


def test_the_endpoint_never_returns_more_than_the_cap(monkeypatch):
    """The cap must bind the OUTPUT, not just the builder list -- otherwise adding a sixth builder
    quietly defeats it."""
    fake = lambda: {"id": "x", "title": "t", "headline": "h", "stat": "z = 9",
                    "significant": True, "n": 10, "detail": "d", "caveat": "c", "chart": []}
    monkeypatch.setattr(server, "_INSIGHT_BUILDERS", tuple([fake] * 9))
    monkeypatch.setattr(server, "_INSIGHT_LIMIT", 2)
    # Point the module at a THROWAWAY cache dict rather than trying to neuter the real one: the day-cache
    # would otherwise either serve a previous result or keep this test's fake insights for the rest of
    # the session.
    monkeypatch.setattr(server, "_INSIGHT_CACHE", {"day": None, "data": None})
    monkeypatch.setattr(server._wu, "name_for_token", lambda t: "Alex")

    with server.app.test_request_context("/api/insights", headers={"X-Auth": "x"}):
        body = server.api_insights().get_json()
    assert len(body["insights"]) == 2


def test_every_insight_publishes_its_own_verdict_and_the_statistic_behind_it():
    """A card that cannot say whether it is still supported is worse than no card."""
    for build in server._INSIGHT_BUILDERS:
        got = build()
        if not got:
            continue
        assert "significant" in got and isinstance(got["significant"], bool), \
            f"{build.__name__} does not publish a verdict"
        assert got.get("stat"), f"{build.__name__} publishes a verdict with no statistic behind it"
        assert got.get("caveat"), f"{build.__name__} publishes a claim with no stated limits"
        assert got.get("n"), f"{build.__name__} does not say how many trades it rests on"


def test_a_failing_insight_does_not_empty_the_page(monkeypatch):
    """One bad query must cost its own card, not the whole page."""
    def boom():
        raise RuntimeError("database is down")
    good = lambda: {"id": "ok", "title": "t", "headline": "h", "stat": "z = 3",
                    "significant": True, "n": 5, "detail": "d", "caveat": "c", "chart": []}
    monkeypatch.setattr(server, "_INSIGHT_BUILDERS", (boom, good))
    # Point the module at a THROWAWAY cache dict rather than trying to neuter the real one: the day-cache
    # would otherwise either serve a previous result or keep this test's fake insights for the rest of
    # the session.
    monkeypatch.setattr(server, "_INSIGHT_CACHE", {"day": None, "data": None})
    monkeypatch.setattr(server._wu, "name_for_token", lambda t: "Alex")

    with server.app.test_request_context("/api/insights", headers={"X-Auth": "x"}):
        body = server.api_insights().get_json()
    assert [i["id"] for i in body["insights"]] == ["ok"]


def test_insights_require_a_login():
    """These are aggregates over the whole trigger history -- the evidence a logged-out visitor must not
    receive, by the same rule that governs the Best Settings cards."""
    assert server.app.test_client().get("/api/insights").status_code == 401


@pytest.mark.live_state
def test_both_requested_insights_are_present_and_computable():
    """The two the requester actually asked for: the bear-direction shift, and market-cap performance."""
    ids = set()
    for build in server._INSIGHT_BUILDERS:
        got = build()
        if got:
            ids.add(got["id"])
    assert {"direction_mix", "mcap_bands"} <= ids


@pytest.mark.live_state
def test_the_market_cap_insight_excludes_unpriced_instruments_from_every_band():
    """Pooling instruments with no market cap into a band would invent a size for them. They are excluded
    from both sides of the comparison instead."""
    got = server._insight_mcap_bands()
    assert got, "the market-cap insight produced nothing"
    labels = {b["label"] for b in got["table"]}
    assert not any("no mcap" in l or "unknown" in l.lower() for l in labels), \
        f"an unpriced band leaked into the comparison: {labels}"
