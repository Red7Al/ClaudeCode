# ======================================================================================================================
# File:         hvf_web/order_bridge.py
# Author:       Alex Hind
# Created:      2026-06-30
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Database -> IG working-order bridge for the FULL scanned universe (user 2026-06-30, Application Focus - Orders A/B).
#
# Why it exists: the session monitors (run_session / run_us_monitor) only consider SESSION_INSTRUMENTS — a curated
# subset — so a web Pre-order (snapshot, 600-instrument universe) within 1.5% of entry was never routed to IG unless
# it happened to be in a session list. This bridge closes that gap: every ~2h it walks the snapshot's READY setups
# and hands each candidate to ig_shim.place_hvf_order_from_sig — the SAME guarded path the sessions use, which
# enforces the quality floor (WO_MIN_QUALITY > 50), the proximity band (WO_PROXIMITY_PCT 1.5% -> PENDING on IG,
# further -> WATCHING only), tight-stop skip, direction agreement, margin-aware sizing and circuit breakers.
#
# Every decision lands in the Supabase working_orders table (via ig_shim), which the web app's "Order ops" tab shows.
#
# Usage:  python -m hvf_web.order_bridge          (one pass)
#         started every BRIDGE_INTERVAL_H hours by hvf_web/server.py's background thread.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-30  Alex Hind   Initial build — snapshot pre-orders -> place_hvf_order_from_sig, 2h cadence.
# ======================================================================================================================

import json
import logging
import os

log = logging.getLogger("order_bridge")

BRIDGE_INTERVAL_H = 2          # user 2026-06-30: "every couple of hours as live prices move"
BRIDGE_MAX_PER_RUN = 6         # safety: at most this many NEW IG placements per pass
_SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshot.json")


def _candidates() -> list:
    """READY pre-orders from the snapshot worth handing to the order engine: quality > 50 and price
    within 1.5% of entry (the engine re-checks both with live data — this is just the shortlist)."""
    # Supabase-first, exactly like hvf_web/server._load_snapshot (user 2026-08-15: "order bridge must
    # run"). Reading the local file directly only ever worked because this ran inside the laptop's Flask
    # process, which kept hvf_web/snapshot.json fresh. That file is gitignored and absent on a clean
    # runner, so a scheduled pass would have found zero candidates and reported success having done
    # nothing at all — the worst kind of silent failure on an order path. load_snapshot() verifies the
    # published object and still falls back to the local last-known-good copy on a configured machine.
    try:
        import scanner_snapshot_store
        records = scanner_snapshot_store.load_snapshot(_SNAPSHOT).get("records", [])
    except Exception as e:
        log.warning(f"bridge: Scanner snapshot store unavailable ({e}); trying the local file")
        try:
            with open(_SNAPSHOT, encoding="utf-8") as fh:
                records = json.load(fh).get("records", [])
        except Exception as e2:
            log.warning(f"bridge: snapshot unreadable: {e2}")
            return []
    if not records:
        log.warning("bridge: snapshot contains no records - refusing to treat that as 'nothing to do'.")
        return []
    try:
        from config_store import cfg_num
        _minq = float(cfg_num("bridge_min_quality", 50))
    except Exception:
        _minq = 50
    out = []
    for r in records:
        # READY and TRIGGERED both qualify (user 2026-07-03) — a fresh trigger still within 1.5% of
        # entry is pre-orderable at the same levels.
        if not r.get("has_signal") or r.get("status") not in ("READY", "TRIGGERED"):
            continue
        q, e, p = r.get("quality"), r.get("entry"), r.get("current_price")
        if not (isinstance(q, (int, float)) and q > _minq and e and p):
            continue
        if abs(e / p - 1) * 100 > 1.5:
            continue
        out.append(r)
    out.sort(key=lambda r: abs((r.get("entry") or 0) / (r.get("current_price") or 1) - 1))
    return out


def _already_working() -> set:
    """Tickers that already have a live/watched working order (no duplicates)."""
    try:
        from db_pool import get_db
        db = get_db()
        try:
            rows = db.run("select distinct ticker from working_orders where status in ('PENDING','WATCHING')")
            return {r[0] for r in (rows or [])}
        finally:
            db.close()
    except Exception as e:
        log.warning(f"bridge: working_orders lookup failed: {e}")
        return set()


def run_bridge() -> dict:
    """One bridge pass. Returns a summary dict {candidates, attempted, placed}."""
    cands = _candidates()
    summary = {"candidates": len(cands), "attempted": 0, "placed": 0, "let_winners_run": None}
    if not cands:
        log.info("bridge: no READY setups within 1.5% with quality > 50 - nothing to do.")
        # Stop management is independent of whether this pass found a new candidate.  The function is
        # currently safety-disabled in ig_shim, so this is a no-op until the full per-user live path is
        # explicitly enabled after end-to-end verification.
        try:
            from ig_shim import run_let_winners_run
            summary["let_winners_run"] = run_let_winners_run()
        except Exception as exc:
            log.warning(f"bridge: let-winners-run pass failed safely: {exc}")
        return summary
    skip = _already_working()

    from run_session import get_user_profile
    from ig_shim import place_hvf_order_from_sig
    profile = get_user_profile()

    # Real above_vwap/atr_expanding (user 2026-08-11, data-completeness audit): each record's OWN fields
    # from hvf_web/build_snapshot.py are ALWAYS None — get_hvf_signal_mtf() never computes them. server.py's
    # api_records() avoids this by recomputing live from volume_score.py; this bridge (which runs in the
    # same Flask process — see the module docstring) reuses that exact source so "Require above VWAP" /
    # "Require ATR expanding" actually gate the 2h automated sweep, not just the session monitors.
    try:
        from hvf_web.server import _live_vwap_atr, _load_snapshot
        vwap_atr = _live_vwap_atr(_load_snapshot())
    except Exception as e:
        log.warning(f"bridge: live VWAP/ATR lookup unavailable, falling back to snapshot fields (likely "
                    f"None — see hvf_web/server.py _live_vwap_atr docstring): {e}")
        vwap_atr = {}

    for r in cands:
        if summary["placed"] >= BRIDGE_MAX_PER_RUN:
            log.info(f"bridge: per-run cap ({BRIDGE_MAX_PER_RUN}) reached - remaining candidates wait for the next pass.")
            break
        tk = r["ticker"]
        if tk in skip:
            continue
        card = r.get("_card") or {}
        sig = {
            "ticker": tk,
            "direction": "BUY" if r.get("direction") == "BULL" else "SELL",
            "hvf_type": card.get("hvf_type") or ("BULLISH" if r.get("direction") == "BULL" else "BEARISH"),
            "hvf_signal": r.get("status"),
            "hvf_h3_level": r.get("entry"), "hvf_stop_level": r.get("stop"), "hvf_target": r.get("target"),
            "hvf_quality": r.get("quality"), "hvf_risk_reward": r.get("rr"),
            "hvf_timeframe": r.get("timeframe"),
            "index": r.get("market"), "location": r.get("location"),   # for the Config trade filters
            # For the owner's personal trading-limit floors (user 2026-08-11) — real values from
            # _live_vwap_atr above, NOT the snapshot record's own (always-None) fields.
            "above_vwap": vwap_atr.get(tk, (None, None))[0],
            "atr_expanding": vwap_atr.get(tk, (None, None))[1],
        }
        summary["attempted"] += 1
        try:
            wo = place_hvf_order_from_sig(sig, profile, "WEB_BRIDGE", 1.0)
            if wo:
                summary["placed"] += 1
                log.info(f"bridge: {tk} -> working order ({wo.get('status', 'ok')})")
        except Exception as e:
            log.warning(f"bridge: {tk} failed: {e}")

    try:
        from ig_shim import run_let_winners_run
        summary["let_winners_run"] = run_let_winners_run()
    except Exception as exc:
        log.warning(f"bridge: let-winners-run pass failed safely: {exc}")
    log.info(f"bridge pass done: {summary}")
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run_bridge()
