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

import account_scope          # which working_orders rows belong to which trading account

log = logging.getLogger("order_bridge")

BRIDGE_INTERVAL_H = 2          # user 2026-06-30: "every couple of hours as live prices move"
BRIDGE_MAX_PER_RUN = 6         # safety: at most this many NEW IG placements per pass
# Owner 2026-10-31, choosing from a replay of every report trigger of the last 90 days on the verified price
# file: a report trigger is ordered only on its trigger day or the next trading day. Ordered at its original
# entry, the 89 triggers passing his limits averaged +1.29R on the trigger day, +0.14R one day late, and
# -0.23R to -0.51R at every delay of 2 to 10 days. Older triggers stay on the report, unordered. An ORDERING
# policy, so it lives here, not in the report's shared calculation (2026-10-31 code review).
BRIDGE_MAX_TRIGGER_AGE_TRADING_DAYS = 1
_SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshot.json")


def _candidates(login: str = None) -> list:
    """Snapshot records to hand to the order engine, each carrying `_sig` when it is a report trigger.

    TRIGGERED: the open triggers on the owner's Scanner Report, from the report's own calculation
    (server.report_order_sigs -> _report_pick; owner 2026-10-31: "never was more than one calculation
    needed"), no more than BRIDGE_MAX_TRIGGER_AGE_TRADING_DAYS old. Their levels and trigger-date measures go
    to the engine unchanged, with NO 1.5% pre-filter: each goes to IG at its own entry, as "Create an order"
    places it (a limit at entry once price is past it), so every report trigger inside the age rule leaves a
    working_orders row or a logged reason; an older one is listed in one warning line and never ordered by
    any path -- its ticker does not fall through to the READY setup (2026-10-31 code review). A TRIGGERED scan record the report hides
    is never ordered. Until 2026-10-31 this function judged the snapshot's current funnel on latest-bar
    values inside 1.5% of the twice-daily scan price, silently, and 40 of 66 report triggers got no order.

    READY (not yet triggered): the scan's own setup, quality over the floor and within 1.5% of entry."""
    # Supabase-first, exactly like hvf_web/server._load_snapshot (user 2026-08-15: "order bridge must
    # run"). Reading the local file directly only ever worked because this ran inside the laptop's Flask
    # process, which kept hvf_web/snapshot.json fresh. That file is gitignored and absent on a clean
    # runner, so a scheduled pass would have found zero candidates and reported success having done
    # nothing at all — the worst kind of silent failure on an order path. load_snapshot() verifies the
    # published object and still falls back to the local last-known-good copy on a configured machine.
    try:
        import scanner_snapshot_store
        _snap = scanner_snapshot_store.load_snapshot(_SNAPSHOT)
        records, snap_gen = _snap.get("records", []), _snap.get("generated_utc")
    except Exception as e:
        log.warning(f"bridge: Scanner snapshot store unavailable ({e}); trying the local file")
        try:
            with open(_SNAPSHOT, encoding="utf-8") as fh:
                _snap = json.load(fh)
            records, snap_gen = _snap.get("records", []), _snap.get("generated_utc")
        except Exception as e2:
            # Red, not green (2026-10-31 test review: an unreadable snapshot exited 0 having placed nothing).
            raise IdentityUnresolved(f"the Scanner snapshot is unreadable ({e2}) -- no pass without it")
    if not records:
        raise IdentityUnresolved("the Scanner snapshot contains no records -- refusing to treat that as "
                                 "'nothing to do'")
    try:
        from config_store import cfg_num
        _minq = float(cfg_num("bridge_min_quality", 50))
    except Exception:
        _minq = 50
    report = None
    try:
        from hvf_web.server import report_order_sigs
        report = report_order_sigs(login, {"generated_utc": snap_gen, "records": records})
    except Exception as e:
        log.warning(f"bridge: Scanner Report triggers unavailable ({e})")
    if report is None:
        # Fails closed and LOUD, as the duplicate guard does (2026-10-31 review): a green run that silently
        # ordered no trigger is how 40 of 66 went unordered. main() exits 1, so Cron Watch emails.
        raise IdentityUnresolved("the Scanner Report's trigger history cannot be read -- no pass without it")
    from hvf_web.server import _trading_days_since
    out, aged = [], []
    for r in records:
        tk = r.get("ticker")
        if tk in report:
            sig = report[tk]
            try:
                age = _trading_days_since(sig["trig_date"])
            except (TypeError, ValueError):
                # One bad row skips its own trigger, never the pass (2026-10-31 code review).
                log.warning(f"bridge: report trigger {tk} has an unreadable trigger date {sig.get('trig_date')!r} "
                            f"-- not ordered")
                continue
            if age > BRIDGE_MAX_TRIGGER_AGE_TRADING_DAYS:
                aged.append(f"{tk} {sig['trig_date']} ({age}d)")
                continue
            log.info(f"bridge: report trigger {tk} {sig['direction']} triggered {sig['trig_date']} "
                     f"entry {sig['hvf_h3_level']} -> order engine")
            out.append(dict(r, _sig=sig))
            continue
        if not r.get("has_signal") or r.get("status") != "READY":
            continue
        q, e, p = r.get("quality"), r.get("entry"), r.get("current_price")
        if not (isinstance(q, (int, float)) and q > _minq and e and p):
            continue
        if abs(e / p - 1) * 100 > 1.5:
            continue
        out.append(r)
    if aged:
        log.warning(f"bridge: {len(aged)} report trigger(s) older than {BRIDGE_MAX_TRIGGER_AGE_TRADING_DAYS} trading "
                    f"day(s) not ordered (owner 2026-10-31): {', '.join(aged)}")
    out.sort(key=_candidate_order)
    return out


def _candidate_order(r: dict):
    """Report triggers before READY setups -- a trigger has at most one more trading day to be ordered, so a
    READY setup must never take its place under BRIDGE_MAX_PER_RUN (2026-10-31 test review) -- then nearest
    entry first; a record with no scan price goes last within its group, not first (it was sorted as entry/1)."""
    e, p = (r.get("_sig") or {}).get("hvf_h3_level") or r.get("entry") or 0, r.get("current_price")
    return (not r.get("_sig"), not p, abs(e / p - 1) if p else 0.0)


class IdentityUnresolved(RuntimeError):
    """A guard the pass depends on cannot be read -- the acting account's identity set is empty, or (since
    2026-10-31) the Scanner Report's trigger history -- so the pass must not place anything.

    Raised rather than returned, because every historical bug in this area came from an empty result being
    read as "nothing to avoid". A caller that must place orders has to handle this explicitly.
    """


def _already_working(owner: str = None) -> set:
    """Tickers we must not place another order on: a live working order, OR an open position.

    THE POSITION HALF WAS MISSING UNTIL 2026-09-04, and was being covered by accident. This read only
    working_orders in PENDING/WATCHING. Nothing had reconciled that table since the session monitors were
    switched off, so filled orders sat there as PENDING for ever -- and those stale rows were what kept
    the bridge off instruments the account already held. Clearing 89 phantom rows (run_working_order_sweep)
    removed 12 rows covering live holdings and exposed the gap: measured that day, none of the 14 open
    positions appeared on this list.

    An open position is a stronger reason not to re-order than a pending order is. Reading IG directly
    rather than trusting our own table, because it was precisely our table being wrong that hid this.
    """
    skip = set()
    ids = account_scope.row_identities(owner)
    # NO TRADING PROFILE, NO TRADING (2026-09-13).
    #
    # The test is the PROFILE binding, not the identity set. identities() always includes the login NAME,
    # so it is never empty for a known login -- checking `if not ids` would never fire. But working_orders
    # carries TWO namespaces, and the account's broker-placed rows (measured 2026-09-12: 263 of 482) are
    # keyed on the profile UUID. Lose the binding and those rows become invisible to this guard while the
    # set still looks populated, and the bridge places a SECOND live order on an instrument already held.
    #
    # Seeing too FEW rows is the dangerous direction here -- the opposite of the money reads, where an
    # empty set safely shows nothing. A bridge that places real orders acts for a trading profile; with no
    # profile resolved it has no business placing at all, so refusing is right on both counts.
    if not account_scope.row_profile_ids(owner):
        raise IdentityUnresolved(
            f"no trading profile bound for owner={owner!r}; refusing to place. The duplicate guard cannot "
            f"see this account's broker-keyed rows without it, so it cannot prove the account is clear")
    try:
        from db_pool import get_db
        db = get_db()
        try:
            # SCOPED TO THE ACCOUNT THIS BRIDGE PLACES FOR (2026-09-12). It was unscoped, and
            # working_orders is multi-tenant: ig_shim.session_for gives every login its own IG session.
            # Measured -- user 'Rich' held four PENDING rows (DGE.L, SPX.L, ^AXJO, WTB.L) with good_till
            # 2026-07-06/07 that were never expired, and this guard skipped those four tickers for the
            # owner from July until 2026-09-11. Another user's order is not a reason not to place ours.
            rows = db.run("select distinct ticker from working_orders "
                          "where status in ('PENDING','WATCHING') and user_id = any(:ids)",
                          ids=account_scope.row_identities(owner))
            skip |= {r[0] for r in (rows or [])}
        finally:
            db.close()
    except Exception as e:
        # NO LONGER FAILS OPEN (2026-09-13). This used to log and continue with an empty skip set, which is
        # the same hazard as an empty identity set above: the bridge places on an instrument the account
        # already has a working order for. Not placing is the safe failure for a duplicate guard; the pass
        # is skipped and the next one picks the setups up. The open-position half below still fails open by
        # design, because it is a SECOND, independent line of defence rather than the primary one.
        raise IdentityUnresolved(f"working_orders lookup failed ({e}); refusing to place this pass")
    # Open positions, resolved to tickers through the same epic_lookup the account screens use.
    try:
        import ig_shim
        raw = ig_shim.get_open_positions() or []
        epic2tk = ig_shim.tickers_for_epics((p.get("market") or {}).get("epic") for p in raw)
        held = set()
        for p in raw:
            epic = str((p.get("market") or {}).get("epic") or "")
            tk = epic2tk.get(epic)
            if tk:
                held.add(tk)
        if held:
            log.info(f"bridge: skipping {len(held)} instrument(s) already held as open positions")
        skip |= held
    except Exception as e:
        # Fail open, as the working_orders half always has: an IG hiccup must not halt every placement.
        # It is logged at WARNING because for this pass the duplicate guard is weaker than intended.
        log.warning(f"bridge: open-position lookup failed, duplicate guard is weaker this pass: {e}")
    return skip


def _ordered_triggers(sigs: list, owner: str = None) -> set:
    """Tickers whose report trigger this account has ALREADY ordered: a working_orders row for the same ticker
    and direction placed -- or filled -- on or after the trigger date, in ANY status. A trigger is ordered once. Without this,
    the bridge would re-place a trigger every pass for its 90 days on the report after its order was stopped
    out, expired or deleted -- _already_working sees only PENDING/WATCHING orders and open positions.
    placed_at is set on every row (516 of 516, default now(), measured 2026-10-31). Fails closed, as
    _already_working does: an unreadable guard aborts the pass rather than risk a second order.
    A pre-order placed before the trigger that FILLED on or after it is that trigger's order (2026-10-31 code
    review): judged on placed_at alone, the trigger would be placed again once that trade had closed."""
    if not sigs:
        return set()
    try:
        from db_pool import get_db
        db = get_db()
        try:
            rows = db.run("select ticker, direction, placed_at, filled_at from working_orders "
                          "where ticker = any(:tks) and user_id = any(:ids)",
                          tks=sorted({s["ticker"] for s in sigs}), ids=account_scope.row_identities(owner))
        finally:
            db.close()
    except Exception as e:
        raise IdentityUnresolved(f"working_orders trigger lookup failed ({e}); refusing to place this pass")
    done = set()
    for s in sigs:
        for tk, direction, placed, filled in rows or []:
            if tk == s["ticker"] and direction == s["direction"] and (
                    str(placed)[:10] >= s["trig_date"] or (filled is not None and str(filled)[:10] >= s["trig_date"])):
                log.info(f"bridge: report trigger {tk} {s['trig_date']} already ordered (placed {str(placed)[:16]}, "
                         f"filled {str(filled)[:16] if filled is not None else '-'}) -- a trigger is ordered once")
                done.add(tk)
                break
    return done


def run_bridge() -> dict:
    """One bridge pass. Returns a summary dict {candidates, attempted, placed}."""
    from run_session import get_user_profile
    import trading_limits
    profile = get_user_profile()
    try:
        cands = _candidates(trading_limits.login_for_profile(profile))
    except IdentityUnresolved as exc:
        log.error(f"bridge: ABORTED before placing any order - {exc}")
        return {"candidates": 0, "attempted": 0, "placed": 0, "let_winners_run": None, "aborted": str(exc)}
    summary = {"candidates": len(cands), "attempted": 0, "placed": 0, "let_winners_run": None}
    if not cands:
        log.info("bridge: no report triggers and no READY setups within 1.5% - nothing to do.")
        # Stop management is independent of whether this pass found a new candidate.  The function is
        # currently safety-disabled in ig_shim, so this is a no-op until the full per-user live path is
        # explicitly enabled after end-to-end verification.
        try:
            from ig_shim import run_let_winners_run
            summary["let_winners_run"] = run_let_winners_run()
        except Exception as exc:
            log.warning(f"bridge: let-winners-run pass failed safely: {exc}")
        return summary
    try:
        skip = _already_working() | _ordered_triggers([r["_sig"] for r in cands if r.get("_sig")])
    except IdentityUnresolved as exc:
        # The duplicate guard could not be built, so we cannot prove this account is not already working
        # these instruments. Placing anyway risks a second live order. Abort the pass and say so loudly.
        log.error(f"bridge: ABORTED before placing any order - {exc}")
        summary["aborted"] = str(exc)
        return summary

    from ig_shim import place_hvf_order_from_sig

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
        sig = dict(r["_sig"]) if r.get("_sig") else {
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
            # A report trigger goes to IG now, as "Create an order" places it from the same row: a far one
            # deferred as WATCHING would never be promoted (reconcile_fills.py header) and would block the
            # ticker from every later pass.
            wo = place_hvf_order_from_sig(sig, profile, "WEB_BRIDGE", 1.0,
                                          watch=False if r.get("_sig") else None)
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


def main() -> int:
    """The workflow's entry. An ABORTED pass exits 1 so the run shows red and Cron Watch emails: 2026-10-26 the
    06:00 and 08:00 passes aborted (no database login) and the runs reported success, so nobody was told."""
    summary = run_bridge()
    return 1 if summary.get("aborted") else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    raise SystemExit(main())
