# ======================================================================================================================
# File:         test_bounce_monitor.py
# Author:       Alex Hind
# Created:      2026-06-26
#
# Description:  Unit tests for backlog E (bounce_monitor) — pure logic + the injectable orchestrator. No IG / email /
#               network. Run: python test_bounce_monitor.py
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-26  Alex Hind   Initial tests.
# ======================================================================================================================

import os
import tempfile
from datetime import datetime, timedelta, timezone

import bounce_monitor as bm

NOW = datetime(2026, 6, 26, 18, 0, 0, tzinfo=timezone.utc)
_fail = 0


def check(name, cond):
    global _fail
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        _fail += 1


def _act(epic, direction, level, dt, name=None):
    return {"epic": epic, "marketName": name or epic, "date": dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "details": {"direction": direction, "level": level}}


# ── parse_ig_time ─────────────────────────────────────────────────────────────────────────────────────────────────────
check("parse naive ISO -> UTC", bm.parse_ig_time("2026-06-26T14:03:11").tzinfo is not None)
check("parse offset ISO",       bm.parse_ig_time("2026-06-26T14:03:11+00:00").hour == 14)
check("parse junk -> None",     bm.parse_ig_time("not-a-date") is None)
check("parse empty -> None",    bm.parse_ig_time("") is None)

# ── _sold_from_activity ───────────────────────────────────────────────────────────────────────────────────────────────
check("SELL maps to SoldPosition", bm._sold_from_activity(_act("IX.D.NIKKEI.IP", "SELL", 38000, NOW)) is not None)
check("BUY -> None",               bm._sold_from_activity(_act("IX.D.NIKKEI.IP", "BUY", 38000, NOW)) is None)
check("level 0 -> None",           bm._sold_from_activity(_act("IX.D.NIKKEI.IP", "SELL", 0, NOW)) is None)
check("comma level parsed",        bm._sold_from_activity({"epic": "E", "date": NOW.strftime("%Y-%m-%dT%H:%M:%S"),
                                                           "details": {"direction": "SELL", "level": "38,000"}}).sold_level == 38000.0)

# ── recent_sells: window + SELL-only + dedup per epic (most recent) ───────────────────────────────────────────────────
acts = [
    _act("IX.D.NIKKEI.IP", "SELL", 38000, NOW - timedelta(hours=40)),   # in window (older)
    _act("IX.D.NIKKEI.IP", "SELL", 37500, NOW - timedelta(hours=2)),    # in window (newer -> wins)
    _act("IX.D.NIKKEI.IP", "BUY",  39000, NOW - timedelta(hours=1)),    # not a sell
    _act("CS.D.GBPUSD.IP", "SELL", 1.34,  NOW - timedelta(hours=60)),   # outside 48h window
    _act("UC.D.AAPL.IP",   "SELL", 210,   NOW - timedelta(hours=5)),    # in window
]
rs = bm.recent_sells(acts, NOW)
epics = {s.epic: s.sold_level for s in rs}
check("dedup keeps newest Nikkei sell",  epics.get("IX.D.NIKKEI.IP") == 37500)
check("old GBPUSD excluded (>48h)",      "CS.D.GBPUSD.IP" not in epics)
check("AAPL sell included",              epics.get("UC.D.AAPL.IP") == 210)
check("two unique epics",                len(rs) == 2)

# ── is_bounce ─────────────────────────────────────────────────────────────────────────────────────────────────────────
check("2% exactly -> bounce",   bm.is_bounce(100, 102, 0.02) is True)
check("just under 2% -> no",    bm.is_bounce(100, 101.9, 0.02) is False)
check("below sell -> no",       bm.is_bounce(100, 98, 0.02) is False)
check("NaN price -> no",        bm.is_bounce(100, float("nan"), 0.02) is False)
check("zero sell -> no",        bm.is_bounce(0, 50, 0.02) is False)

# ── check_bounces orchestrator (injected fakes) ───────────────────────────────────────────────────────────────────────
_state = os.path.join(tempfile.gettempdir(), "test_bounce_state.json")
if os.path.exists(_state):
    os.remove(_state)

sent = []
def _send(sp, cur): sent.append((sp.epic, cur)); return True

# Nikkei sold 37500, now 38400 (+2.4% -> bounce). AAPL sold 210, now 211 (+0.5% -> no).
prices = {"IX.D.NIKKEI.IP": 38400, "UC.D.AAPL.IP": 211}
def _fetch_acts(now): return acts
def _fetch_price(epic): return prices.get(epic)

r1 = bm.check_bounces(now=NOW, fetch_activities=_fetch_acts, fetch_price=_fetch_price,
                      send=_send, state_path=_state)
check("one bounce alerted (Nikkei only)", [s.epic for s in r1] == ["IX.D.NIKKEI.IP"])
check("AAPL not alerted (only +0.5%)",    all(e != "UC.D.AAPL.IP" for e, _ in sent))

# Second run, same state -> spam guard suppresses the repeat.
sent.clear()
r2 = bm.check_bounces(now=NOW + timedelta(minutes=15), fetch_activities=_fetch_acts,
                      fetch_price=_fetch_price, send=_send, state_path=_state)
check("spam-guard: no repeat alert", r2 == [] and sent == [])

if os.path.exists(_state):
    os.remove(_state)

print()
print(f"{'ALL PASS' if _fail == 0 else str(_fail) + ' FAILED'}")
raise SystemExit(1 if _fail else 0)
