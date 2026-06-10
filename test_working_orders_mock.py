# =============================================================================
# File:         test_working_orders_mock.py
# Author:       Alex Hind
# Created:      2026-06-10
#
# Description:
# -----------------------------------------------------------------------------
# Offline unit tests for the HVF working-order path (ig_shim). Everything is
# monkeypatched — NO IG calls, NO Slack posts, NO database writes. Verifies:
#   1-4   Order-type selection: BUY/SELL × entry above/below market → STOP/LIMIT
#   5     Payload contract: /workingorders/otc v2 body fields
#   6     Level-geometry guard (BUY needs stop < entry < limit)
#   7     Sanity guard: entry too far from live price → refuse
#   8     Dedup: PENDING order at same levels → skip (no second order)
#   9     Update-not-duplicate: levels moved > threshold → PUT amend, no new POST
#   10    Reconcile: order gone from IG + matching position → FILLED + positions row
#   11    Reconcile: order gone, no position, good-till passed → EXPIRED
#   12    Reconcile: order gone, no position, good-till future → CANCELLED
#
# Usage:  python test_working_orders_mock.py   (exits non-zero on failure)
# =============================================================================

import types
from datetime import datetime, timedelta, timezone

import ig_shim as ig
import notify
import trade_email

PASS = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        raise SystemExit(f"FAILED: {name} {detail}")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeSession:
    """Stands in for ig_shim.session. Records every POST/DELETE body."""
    def __init__(self, bid, offer):
        self.bid, self.offer = bid, offer
        self.posted   = []      # (path, body, version)
        self.deleted  = []
        self.working  = []      # GET /workingorders payload

    def get(self, path, version="1", params=None):
        if path.startswith("/markets/"):
            return {"snapshot": {"bid": self.bid, "offer": self.offer,
                                 "decimalPlacesFactor": 2, "marketStatus": "TRADEABLE"}}
        if path.startswith("/confirms/"):
            return {"dealStatus": "ACCEPTED", "dealId": "DIAA_TEST_DEAL"}
        if path == "/workingorders":
            return {"workingOrders": self.working}
        if path == "/positions/otc":
            return {"positions": []}
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path, body, version="1"):
        self.posted.append((path, body, version))
        return {"dealReference": "REF_TEST"}

    def delete(self, path, body, version="1"):
        self.deleted.append((path, body, version))
        return {"dealReference": "REF_DEL"}

    def ensure_authenticated(self):
        pass

    def _headers(self, v):
        return {}


class FakeDB:
    """Query-aware stub for get_db(). Returns canned rows; records updates."""
    def __init__(self, pending_rows=None, position_rows=None):
        self.pending_rows  = pending_rows or []
        self.position_rows = position_rows if position_rows is not None else []
        self.updates       = []

    def run(self, sql, **kw):
        s = " ".join(sql.split()).lower()
        if "from working_orders where status = 'pending'" in s and "select deal_id, deal_ref" in s:
            return self.pending_rows
        if s.startswith("select deal_id from positions where ticker"):
            return self.position_rows
        if s.startswith("select deal_id from positions"):
            return []          # tracked-positions set in reconcile
        if s.startswith("update") or s.startswith("insert"):
            self.updates.append((s, kw))
            return []
        return []

    def close(self):
        pass


def quiet(*a, **k):
    return None


# ---------------------------------------------------------------------------
# Patch everything dangerous
# ---------------------------------------------------------------------------

fake_db = FakeDB()
ig.session = FakeSession(bid=99.0, offer=100.0)
ig.get_db  = lambda: fake_db
ig.check_circuit_breakers = lambda u, t, s=None: (True, "OK")
ig.get_epic = lambda t: "UC.D.TEST.DAILY.IP"
ig.time.sleep = lambda s: None

WO_LOGGED = []
ig._log_working_order_to_db = lambda *a, **k: WO_LOGGED.append(a)
POS_LOGGED = []
ig._log_position_to_db = lambda *a, **k: POS_LOGGED.append(a)
STATUS_SET = []
_real_set_status = ig._set_working_order_status
ig._set_working_order_status = lambda deal_id, status, fill_deal_id=None, notes=None: \
    STATUS_SET.append((deal_id, status, fill_deal_id, notes))

notify.working_order_placed  = quiet
notify.working_order_updated = quiet
notify.working_order_outcome = quiet
notify.trade_opened          = quiet
notify.alert_missed_trade    = quiet
trade_email.send_trade_email = quiet


# ---------------------------------------------------------------------------
# 1-4: order-type selection (market: bid 99 / offer 100)
# ---------------------------------------------------------------------------
print("Order-type selection:")

r = ig.place_working_order("u1", "TST", "BUY", 1.0, 105.0, 101.0, 117.0, "US_OPEN", "sig")
check("BUY entry 105 >= offer 100 → STOP", r and r["otype"] == "STOP", f"r={r}")

r = ig.place_working_order("u1", "TST", "BUY", 1.0, 95.0, 90.0, 110.0, "US_OPEN", "sig")
check("BUY entry 95 < offer 100 → LIMIT (pullback)", r and r["otype"] == "LIMIT", f"r={r}")

r = ig.place_working_order("u1", "TST", "SELL", 1.0, 95.0, 99.5, 84.0, "US_OPEN", "sig")
check("SELL entry 95 <= bid 99 → STOP", r and r["otype"] == "STOP", f"r={r}")

r = ig.place_working_order("u1", "TST", "SELL", 1.0, 103.0, 107.0, 91.0, "US_OPEN", "sig")
check("SELL entry 103 > bid 99 → LIMIT (pullback)", r and r["otype"] == "LIMIT", f"r={r}")

# ---------------------------------------------------------------------------
# 5: payload contract
# ---------------------------------------------------------------------------
print("Payload contract:")
path, body, version = ig.session.posted[0]
check("POST /workingorders/otc v2", path == "/workingorders/otc" and version == "2",
      f"{path} v{version}")
expected = {"epic": "UC.D.TEST.DAILY.IP", "direction": "BUY", "size": "1.0",
            "level": "105.0", "type": "STOP", "timeInForce": "GOOD_TILL_DATE",
            "guaranteedStop": False, "stopLevel": "101.0", "limitLevel": "117.0",
            "currencyCode": "GBP", "expiry": "DFB", "forceOpen": True}
mismatch = {k: (body.get(k), v) for k, v in expected.items() if body.get(k) != v}
check("body fields exact", not mismatch, str(mismatch))
check("goodTillDate format yyyy/MM/dd HH:mm:ss",
      len(body.get("goodTillDate", "")) == 19 and body["goodTillDate"][4] == "/",
      body.get("goodTillDate"))
check("working order logged to DB", len(WO_LOGGED) == 4, f"{len(WO_LOGGED)}")

# ---------------------------------------------------------------------------
# 6: geometry guard
# ---------------------------------------------------------------------------
print("Guards:")
n_posts = len(ig.session.posted)
r = ig.place_working_order("u1", "TST", "BUY", 1.0, 105.0, 106.0, 117.0, "US_OPEN", "sig")
check("BUY with stop above entry → refused, no POST",
      r is None and len(ig.session.posted) == n_posts)

# 7: sanity guard — entry 60% away from market
r = ig.place_working_order("u1", "TST", "BUY", 1.0, 160.0, 150.0, 190.0, "US_OPEN", "sig")
check("entry 60% from market → refused (stale/unit-mismatch guard)",
      r is None and len(ig.session.posted) == n_posts)

# ---------------------------------------------------------------------------
# 8: dedup — PENDING order at same levels → skip
# ---------------------------------------------------------------------------
print("Update-not-duplicate:")
ig._get_pending_working_order = lambda t, u: {
    "deal_id": "DIAA_OLD", "entry_level": 105.0, "stop_level": 101.0,
    "limit_level": 117.0, "direction": "BUY", "otype": "STOP",
    "good_till": datetime.now(timezone.utc) + timedelta(days=3), "size": 1.0}
n_posts = len(ig.session.posted)
r = ig.place_working_order("u1", "TST", "BUY", 1.0, 105.05, 101.0, 117.0, "US_OPEN", "sig")
check("re-signal at ~same levels (0.05%) → skipped, no duplicate",
      r is None and len(ig.session.posted) == n_posts)

# 9: levels moved 2% → amend via PUT (no new POST)
puts = []
class FakePutResp:
    status_code = 200
    def raise_for_status(self): pass
    def json(self): return {"dealReference": "REF_AMEND"}
ig.requests.put = lambda url, headers=None, json=None, timeout=15: (puts.append((url, json)) or FakePutResp())

r = ig.place_working_order("u1", "TST", "BUY", 1.0, 107.1, 103.0, 119.0, "US_OPEN", "sig")
check("levels moved 2% → amended (PUT), not re-placed",
      r is not None and r.get("updated") is True and len(ig.session.posted) == n_posts,
      f"r={r} puts={len(puts)}")
check("PUT hit /workingorders/otc/DIAA_OLD",
      puts and "/workingorders/otc/DIAA_OLD" in puts[0][0], str(puts[:1]))
check("PUT body has level/stopLevel/limitLevel/type",
      puts and all(k in puts[0][1] for k in ("level", "stopLevel", "limitLevel", "type")),
      str(puts[:1]))

ig._get_pending_working_order = lambda t, u: None   # reset for reconcile tests

# ---------------------------------------------------------------------------
# 9b: unit alignment — Yahoo-unit FX levels scaled into IG points
# ---------------------------------------------------------------------------
print("Unit alignment (FX Yahoo → IG points):")
check("EURUSD 1.15386 vs IG 11538.6 → ×10000", ig.detect_ig_scale(11538.6, 1.15386) == 10000.0)
check("USDJPY 155.123 vs IG 15512.3 → ×100",   ig.detect_ig_scale(15512.3, 155.123) == 100.0)
check("equity 1:1 → ×1",                        ig.detect_ig_scale(100.0, 95.0) == 1.0)
check("non-power-of-ten mismatch → ×1 (guard refuses downstream)",
      ig.detect_ig_scale(10000.0, 37.0) == 1.0)

fx = FakeSession(bid=11537.9, offer=11538.6)
ig.session = fx
r = ig.place_working_order("u1", "EURUSD", "BUY", 1.0, 1.16, 1.13, 1.25, "UK_OPEN", "sig")
check("Yahoo-unit entry 1.16 placed as IG 11600 STOP",
      r and r["otype"] == "STOP" and fx.posted and fx.posted[-1][1]["level"] == "11600.0",
      f"r={r} body={fx.posted[-1][1] if fx.posted else None}")

# ---------------------------------------------------------------------------
# 10-12: reconcile — FILLED / EXPIRED / CANCELLED
# ---------------------------------------------------------------------------
print("Reconcile:")
future = datetime.now(timezone.utc) + timedelta(days=2)
past   = datetime.now(timezone.utc) - timedelta(days=1)

def pending_row(deal, ticker, till):
    # (deal_id, deal_ref, user_id, ticker, epic, direction, size, entry, stop,
    #  limit, otype, session, signal_summary, good_till, hvf_type, paper)
    return (deal, "REF", "u1", ticker, "UC.D.TEST.DAILY.IP", "BUY", 1.0,
            105.0, 101.0, 117.0, "STOP", "US_OPEN", "HVF test", till, "BULLISH", False)

# 10: FILLED — gone from /workingorders, matching untracked position exists
fake_db.pending_rows = [pending_row("DIAA_F", "FILLT", future)]
ig.session.working = []
ig.get_open_positions = lambda: [{
    "position": {"dealId": "POSD_NEW", "direction": "BUY", "size": 1.0,
                 "level": 105.2, "stopLevel": 101.0, "limitLevel": 117.0},
    "market": {"epic": "UC.D.TEST.DAILY.IP"}}]
POS_LOGGED.clear(); STATUS_SET.clear()
s = ig.reconcile_working_orders()
check("fill detected", s["filled"] == ["FILLT"], str(s))
check("fill → positions row inserted (monitor takes over)",
      len(POS_LOGGED) == 1 and POS_LOGGED[0][8] == "POSD_NEW", str(POS_LOGGED))
check("fill → status FILLED with fill_deal_id",
      STATUS_SET and STATUS_SET[0][:3] == ("DIAA_F", "FILLED", "POSD_NEW"), str(STATUS_SET))

# 11: EXPIRED — gone, no position, good-till passed
fake_db.pending_rows = [pending_row("DIAA_E", "EXPT", past)]
ig.get_open_positions = lambda: []
STATUS_SET.clear()
s = ig.reconcile_working_orders()
check("expiry detected", s["expired"] == ["EXPT"] and STATUS_SET[0][1] == "EXPIRED", str(s))

# 12: CANCELLED — gone, no position, good-till still in the future
fake_db.pending_rows = [pending_row("DIAA_C", "CANT", future)]
STATUS_SET.clear()
s = ig.reconcile_working_orders()
check("cancel detected", s["cancelled"] == ["CANT"] and STATUS_SET[0][1] == "CANCELLED", str(s))

# Still-pending order stays untouched
fake_db.pending_rows = [pending_row("DIAA_P", "PENDT", future)]
ig.session.working = [{"workingOrderData": {"dealId": "DIAA_P"}}]
STATUS_SET.clear()
s = ig.reconcile_working_orders()
check("still-pending order left alone", s["pending"] == 1 and not STATUS_SET, str(s))

print(f"\nALL {len(PASS)} MOCK TESTS PASSED")
