"""The winners feature caches must hold one entry PER WINDOW, not one entry in total.

WHY. Best Settings requests the annual window and then, deliberately deferred, the three-year window --
the two calls the page always makes together. Both caches were single-slot with a "years" tag, so a hit
required the tag to match and a call for a different window EVICTED the previous one rather than sitting
beside it. Neither call was ever served warm. _volscore_trigger_feature_map had no cache at all, so it
rebuilt over the whole window on every request even when the scored rows underneath it were warm.

Measured on the live site 2026-08-22 before the fix: /api/winners 34.9 s and /api/winners?years=3 16.9 s
-- the LONGER window faster, only because the shared _sqa_all_rows population happened to be warm from
the call before it.

A regression here is invisible in behaviour: the figures stay correct and the page just goes slow again.
So these tests count how many times the expensive builder actually runs.
"""

import pytest

from hvf_web import server


@pytest.fixture(autouse=True)
def _clear_caches():
    server._VSCORED_CACHE.clear()
    server._VSMAP_CACHE.clear()
    server._VSFEAT_CACHE.clear()
    yield
    server._VSCORED_CACHE.clear()
    server._VSMAP_CACHE.clear()
    server._VSFEAT_CACHE.clear()


def _stub_scored(monkeypatch, calls):
    def fake(years=1):
        calls.append(years)
        return [{"ticker": f"T{years}", "trig_date": "2026-08-01",
                 "volume_score": years, "above_vwap": True, "atr_expanding": False}]
    monkeypatch.setattr(server, "_volscore_scored", fake)


def test_alternating_windows_do_not_evict_each_other(monkeypatch):
    """THE REGRESSION. years=1, 3, 1, 3 must build twice, not four times."""
    calls = []
    _stub_scored(monkeypatch, calls)

    for years in (1, 3, 1, 3):
        server._volscore_trigger_map(years)

    assert calls == [1, 3], f"each window must build once and stay cached; builds were {calls}"


def test_the_feature_map_is_cached_too(monkeypatch):
    calls = []
    _stub_scored(monkeypatch, calls)

    for years in (1, 3, 1, 3):
        server._volscore_trigger_feature_map(years)

    assert calls == [1, 3], f"the feature map rebuilt every call; builds were {calls}"


def test_each_window_keeps_its_own_values(monkeypatch):
    """Speed must not come from one window borrowing another's features — the blank-VWAP bug."""
    _stub_scored(monkeypatch, [])

    annual = server._volscore_trigger_map(1)
    three = server._volscore_trigger_map(3)

    assert annual == {("T1", "2026-08-01"): 1}
    assert three == {("T3", "2026-08-01"): 3}
    assert server._volscore_trigger_map(1) == annual, "the annual entry was evicted by the 3-year call"


def test_a_single_slot_cache_would_thrash(monkeypatch):
    """A guard that has never failed proves nothing: reconstruct the old shape and watch it thrash."""
    calls = []
    _stub_scored(monkeypatch, calls)
    single = {"ts": 0.0, "data": None, "years": None}
    import time

    def old_map(years):
        now = time.time()
        if single["data"] is not None and single["years"] == years and now - single["ts"] < server._SQA_TTL:
            return single["data"]
        m = {(r["ticker"], str(r["trig_date"])[:10]): r.get("volume_score")
             for r in server._volscore_scored(years)}
        single.update(ts=now, data=m, years=years)
        return m

    for years in (1, 3, 1, 3):
        old_map(years)

    assert calls == [1, 3, 1, 3], "the reconstruction must thrash, or this test proves nothing"


def test_the_cache_expires(monkeypatch):
    """Re-keying must not turn a TTL cache into a permanent one serving a stale trading dataset."""
    calls = []
    _stub_scored(monkeypatch, calls)

    server._volscore_trigger_map(1)
    server._VSMAP_CACHE[1]["ts"] -= server._SQA_TTL + 1
    server._volscore_trigger_map(1)

    assert calls == [1, 1], "an entry older than _SQA_TTL must be rebuilt"
