# ======================================================================================================================
# File:         test_working_order_live.py
# Author:       Alex Hind
# Created:      2026-06-10
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# ONE-SHOT live verification of the IG working-order path. SAFE BY DESIGN:
# places a single BUY LIMIT order ~30% BELOW the live market (it cannot fill),
# confirms IG accepts it, verifies it in GET /workingorders + the DB row, runs
# the reconciler (must leave it pending), then DELETES it and verifies cleanup.
#
# Slack alerts are suppressed for the run (a deliberate test must not page).
# The order spends < 10 seconds alive and never has margin at risk.
#
# Usage:  python test_working_order_live.py
# ======================================================================================================================

import time
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

import notify
notify.alert_missed_trade = lambda *a, **k: print(f"   (suppressed Slack alert: {a})")
notify.alert_system_error = lambda *a, **k: print(f"   (suppressed Slack alert: {a}, {k})")

import ig_shim as ig
from db_pool import get_db

CANDIDATES = ["EURUSD", "AUDUSD", "GBPUSD", "USDJPY"]   # FX — open 24/5


def main():
    # ── Pick a clean instrument: no open position, no pending order, no trades today
    db = get_db()
    ticker = None
    for t in CANDIDATES:
        n = db.run(
            """select
                 (select count(*) from positions where ticker = :t)
               + (select count(*) from working_orders where ticker = :t and status = 'PENDING')
               + (select count(*) from trade_log where ticker = :t and date(opened_at) = current_date)""",
            t=t)[0][0]
        if int(n) == 0:
            ticker = t
            break
    prof = db.run("select id, name from user_profiles where paper_trade = false limit 1")
    db.close()
    if not ticker:
        raise SystemExit("No clean FX candidate found — aborting (nothing placed)")
    if not prof:
        raise SystemExit("No live user profile found — aborting (nothing placed)")
    user_id, user_name = prof[0][0], prof[0][1]
    print(f"1. Test instrument: {ticker} | user: {user_name}")

    # ── Live market context
    epic = ig.get_epic(ticker)
    assert epic, f"no epic for {ticker}"
    mkt   = ig.session.get(f"/markets/{epic}", version="3")
    snap  = mkt.get("snapshot", {})
    rules = mkt.get("dealingRules", {})
    bid, offer = float(snap.get("bid") or 0), float(snap.get("offer") or 0)
    dec      = int(snap.get("decimalPlacesFactor", 2) or 2)
    min_size = float((rules.get("minDealSize") or {}).get("value", 0.5))
    status   = snap.get("marketStatus")
    print(f"2. {epic}: status={status} bid={bid} offer={offer} minSize={min_size} decimals={dec}")
    assert status == "TRADEABLE", f"market not tradeable ({status}) — try during FX hours"

    # Entry 30% below market → BUY LIMIT that cannot fill today.
    entry  = round(offer * 0.70, dec)
    stop   = round(entry * 0.97, dec)
    target = round(entry * 1.09, dec)   # 3:1 on the 3% stop
    print(f"3. Placing SAFE far-from-market order: BUY LIMIT {min_size} x {ticker} "
          f"@ {entry} (market {offer}) stop {stop} target {target}")

    time.sleep(1.2)
    result = ig.place_working_order(
        user_id=user_id, ticker=ticker, direction="BUY", size=min_size,
        entry_level=entry, stop_level=stop, limit_level=target,
        session_name="WO_TEST", signal_summary="live working-order placement test",
        paper_trade=False, good_till_days=1, hvf_type="BULLISH",
        max_entry_distance_pct=0.45)
    assert result, "place_working_order returned None — IG did not accept"
    deal_id = result["deal_id"]
    print(f"4. IG ACCEPTED working order: deal_id={deal_id} otype={result['otype']} "
          f"(expected LIMIT) goodTill={result.get('good_till')}")
    assert result["otype"] == "LIMIT", f"expected LIMIT, got {result['otype']}"

    # ── Verify it shows in IG /workingorders and in our DB
    time.sleep(1.2)
    live = ig.get_working_orders()
    live_ids = [(w.get("workingOrderData") or {}).get("dealId") for w in live]
    print(f"5. GET /workingorders: {len(live)} order(s) on account; ours present: {deal_id in live_ids}")
    assert deal_id in live_ids, "order not visible in GET /workingorders"

    db = get_db()
    row = db.run("select status, entry_level, otype, session from working_orders where deal_id = :d", d=deal_id)
    db.close()
    print(f"6. DB row: {row}")
    assert row and row[0][0] == "PENDING", "DB row missing or not PENDING"

    # ── Reconciler must leave a live pending order untouched
    rec = ig.reconcile_working_orders()
    print(f"7. reconcile_working_orders: {rec}")
    assert rec["pending"] >= 1 and not rec["filled"] and not rec["cancelled"], \
        f"reconciler misbehaved on a live pending order: {rec}"

    # ── Delete + verify cleanup
    time.sleep(1.2)
    ok = ig.delete_working_order(deal_id, reason="live placement test — deleted immediately")
    assert ok, "delete_working_order failed — DELETE THE TEST ORDER MANUALLY IN IG"
    time.sleep(1.2)
    live_after = [(w.get("workingOrderData") or {}).get("dealId") for w in ig.get_working_orders()]
    db = get_db()
    row2 = db.run("select status, notes from working_orders where deal_id = :d", d=deal_id)
    db.close()
    print(f"8. After delete: still in IG? {deal_id in live_after} | DB row: {row2}")
    assert deal_id not in live_after, "order still on IG after delete!"
    assert row2 and row2[0][0] == "CANCELLED", "DB row not marked CANCELLED"

    print(f"\nLIVE TEST PASSED — {ticker} order {deal_id} placed (LIMIT, unfillable), "
          f"verified on IG + DB, reconciled as pending, deleted, cleanup confirmed. "
          f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")


if __name__ == "__main__":
    main()
