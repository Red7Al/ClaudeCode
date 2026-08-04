# ======================================================================================================================
# File:         test_bounce_monitor.py
# Author:       Alex Hind
# Created:      2026-06-26
#
# Description:  Unit tests for backlog E (bounce_monitor) — pure logic + the injectable orchestrator. No IG / email /
#               network. Run: python -m pytest test_bounce_monitor.py
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-26  Alex Hind   Initial tests.
# ======================================================================================================================

from datetime import datetime, timedelta, timezone

import bounce_monitor as bm

NOW = datetime(2026, 6, 26, 18, 0, 0, tzinfo=timezone.utc)


def _act(epic, direction, level, dt, name=None):
    return {"epic": epic, "marketName": name or epic, "date": dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "details": {"direction": direction, "level": level}}


def _activities():
    return [
        _act("IX.D.NIKKEI.IP", "SELL", 38000, NOW - timedelta(hours=40)),
        _act("IX.D.NIKKEI.IP", "SELL", 37500, NOW - timedelta(hours=2)),
        _act("IX.D.NIKKEI.IP", "BUY", 39000, NOW - timedelta(hours=1)),
        _act("CS.D.GBPUSD.IP", "SELL", 1.34, NOW - timedelta(hours=60)),
        _act("UC.D.AAPL.IP", "SELL", 210, NOW - timedelta(hours=5)),
    ]


def test_parse_ig_time():
    assert bm.parse_ig_time("2026-06-26T14:03:11").tzinfo is not None
    assert bm.parse_ig_time("2026-06-26T14:03:11+00:00").hour == 14
    assert bm.parse_ig_time("not-a-date") is None
    assert bm.parse_ig_time("") is None


def test_sold_from_activity():
    assert bm._sold_from_activity(_act("IX.D.NIKKEI.IP", "SELL", 38000, NOW)) is not None
    assert bm._sold_from_activity(_act("IX.D.NIKKEI.IP", "BUY", 38000, NOW)) is None
    assert bm._sold_from_activity(_act("IX.D.NIKKEI.IP", "SELL", 0, NOW)) is None
    sold = bm._sold_from_activity({"epic": "E", "date": NOW.strftime("%Y-%m-%dT%H:%M:%S"),
                                   "details": {"direction": "SELL", "level": "38,000"}})
    assert sold.sold_level == 38000.0


def test_recent_sells_filters_and_deduplicates():
    recent = bm.recent_sells(_activities(), NOW)
    epics = {sold.epic: sold.sold_level for sold in recent}
    assert epics == {"IX.D.NIKKEI.IP": 37500, "UC.D.AAPL.IP": 210}


def test_is_bounce_boundaries():
    assert bm.is_bounce(100, 102, 0.02) is True
    assert bm.is_bounce(100, 101.9, 0.02) is False
    assert bm.is_bounce(100, 98, 0.02) is False
    assert bm.is_bounce(100, float("nan"), 0.02) is False
    assert bm.is_bounce(0, 50, 0.02) is False


def test_check_bounces_alerts_once(tmp_path):
    activities = _activities()
    sent = []
    prices = {"IX.D.NIKKEI.IP": 38400, "UC.D.AAPL.IP": 211}

    def send(sold, current):
        sent.append((sold.epic, current))
        return True

    state = str(tmp_path / "bounce_state.json")
    first = bm.check_bounces(now=NOW, fetch_activities=lambda now: activities,
                             fetch_price=prices.get, send=send, state_path=state)
    assert [sold.epic for sold in first] == ["IX.D.NIKKEI.IP"]
    assert all(epic != "UC.D.AAPL.IP" for epic, _ in sent)

    sent.clear()
    second = bm.check_bounces(now=NOW + timedelta(minutes=15), fetch_activities=lambda now: activities,
                              fetch_price=prices.get, send=send, state_path=state)
    assert second == []
    assert sent == []
