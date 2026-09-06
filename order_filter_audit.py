# ======================================================================================================================
# File:         order_filter_audit.py
# Created:      2026-08-29
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Which LIVE orders no longer match the user's own trading filters (user 2026-08-29: "We should have a method to identify
# orders that do NOT match current trading filters").
#
# WHY THIS IS NEEDED. Filters are checked when an order is PLACED and never again. There are two of them, enforced in
# different places, and only one is server-side:
#
#   * direction / location / market  -> hvf_web/server.py::_user_trade_allows, checked on pin and place
#   * R:R, quality, RVOL, VolumeScore, above-VWAP, ATR-expanding, instrument value
#                                    -> MY_LIMITS, applied CLIENT-side only, for display
#
# So once an order is live nothing re-checks it, and a filter the user tightens afterwards is not applied to what is
# already on the book. On 2026-08-29 the owner had 55 pending orders and no way to tell which still qualified.
#
# WHY IT COULD NOT BE BUILT BEFORE. above_vwap / atr_expanding / volume_score were computed on demand and discarded, so
# an order placed on an earlier day had no metrics to be judged against. A first attempt reported 52 of 55 orders in
# breach of require_above_vwap; that was an artefact of the missing data, not a finding, and was withheld. This module
# reads instrument_metrics, which now records them daily, and reports UNKNOWN rather than a breach when a metric is
# genuinely absent -- an absence of evidence must never be presented as evidence of a breach.
# ======================================================================================================================

import logging

log = logging.getLogger("order_filter_audit")

# (limit key, metric field, human label). The metric name is the one stored by instrument_metrics.
NUMERIC_FLOORS = (("min_risk_reward", "rr", "R:R"),
                  ("min_quality", "quality", "Quality"),
                  ("min_rvol", "rvol", "RVOL"),
                  ("min_volume_score", "volume_score", "VolumeScore"))

# The trading filters are expressed against the SETUP confirmation, which is direction-aware -- a BEAR setup confirms
# on price BELOW its VWAP. instrument_metrics stores that as above_vwap_setup, separately from the literal instrument
# metric the Instruments tab shows. Using the wrong one would invert every BEAR verdict.
BOOLEAN_REQUIREMENTS = (("require_above_vwap", "above_vwap_setup", "above VWAP"),
                        ("require_atr_expanding", "atr_expanding", "ATR expanding"))


# Break-bar measures. RVOL, VolumeScore, above-VWAP and ATR-expanding all describe the BREAKOUT BAR, and
# an order reaches IG a median of 8 days BEFORE that bar exists (measured 2026-09-03: 55.5% of the last
# 400 orders preceded their own break, and 23 of 59 pending orders had no break at all). So for a PENDING
# order these are not stale, not breaching and not decayed -- they are NOT APPLICABLE, because the event
# they measure has not happened. The account owner's wording: "RVOL, ATR and VWAP are only relevant at the
# break (so stale should not be an issue)".
#
# Judging a pending order on them produced 40 "failures" that were not failures. They are excluded here,
# and the STALE verdict disappears with them. See docs/ORDER_TIMING_AND_RVOL.md.
BREAK_BAR_LABELS = ("RVOL", "VolumeScore", "above VWAP", "ATR expanding")


def _durable_only(items):
    return [x for x in items if not any(m in x for m in BREAK_BAR_LABELS)]


def placement_setups(tickers, db=None, statuses=("PENDING",)):
    """{ticker: {rr, quality, ready_date}} for the setup each order was placed from.

    `statuses` selects which working_orders rows to read: PENDING for live orders, FILLED for the orders
    behind open positions. One definition for both, because the question -- which setup did this order
    come from -- is identical and a second copy is how two screens end up disagreeing.

    THE JOIN IS ready_date, NOT triggered_date. An order is placed when a setup becomes orderable; the
    break comes later, so a trigger-based join cannot select the setup the order came from -- that row's
    triggered_date is still null or later than placement, and an older, unrelated break gets used instead.
    Measured 2026-09-03: a trigger join attributed the wrong setup to 34 of 61 pending orders (55.7%), and
    left 5 unjudgeable that this join resolves cleanly.
    """
    from db_pool import get_db
    own = db is None
    db = db or get_db()
    try:
        placed = {}
        for tk, p in (db.run("select ticker, max(placed_at)::date from working_orders "
                             "where status = any(:st) and placed_at is not null group by ticker",
                             st=list(statuses)) or []):
            placed[tk] = p
        if not placed:
            return {}
        rows = db.run("select ticker, ready_date::date, risk_reward, quality from squeeze_history "
                      "where ready_date is not null and ticker = any(:tks)",
                      tks=list(placed)) or []
    finally:
        if own:
            db.close()
    best = {}
    for tk, rd, rr, q in rows:
        cutoff = placed.get(tk)
        if not rd or not cutoff or rd > cutoff:
            continue
        if tk not in best or rd > best[tk]["ready_date"]:
            best[tk] = {"ready_date": rd, "rr": rr, "quality": q}
    return best


# PERSISTENT criteria are properties of the setup itself: they do not change after the order is placed, so a breach
# means the order does not belong on the book at all. POINT-IN-TIME criteria are market state on a given day -- RVOL
# especially decays after the volume spike that produced the setup, so a pending order showing sub-floor RVOL days
# later is NORMAL, not a placement error. Reporting the two together produces an alarming and useless number: on
# 2026-08-29 that would have read "51 of 55 orders in breach" when only 8 were breaching anything durable.
PERSISTENT = {"R:R", "Quality", "instrument value", "direction/location/market"}


def _kind(breach):
    return "persistent" if any(p in breach for p in PERSISTENT) else "point_in_time"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def check_one(rec, metrics, limits, gate_ok, at_trigger=False):
    """Verdict for one order. `rec` is its snapshot record, `metrics` its latest stored daily metrics."""
    breaches, unknown = [], []
    rec = rec or {}
    metrics = metrics or {}

    if not gate_ok:
        breaches.append("blocked by direction/location/market filter")

    def value(field):
        # The snapshot carries the setup's own trigger-time figures; the daily metrics carry current ones.
        # Prefer the snapshot where it has the field, so a setup is judged on what it was recorded with.
        v = rec.get(field)
        return v if v is not None else metrics.get(field)

    for key, field, label in NUMERIC_FLOORS:
        want = _num(limits.get(key))
        if not want or want <= 0:
            continue
        have = _num(value(field))
        if have is None:
            unknown.append(f"{label} not recorded")
        elif have < want:
            breaches.append(f"{label} {have} < {want}")

    for key, field, label in BOOLEAN_REQUIREMENTS:
        if str(limits.get(key) or "") in ("", "0", "False", "None"):
            continue
        have = value(field)
        if have is None:
            unknown.append(f"{label} not recorded")
        elif have is not True:
            breaches.append(f"{label} required but not met")

    lo, hi = _num(limits.get("min_instrument_value")), _num(limits.get("max_instrument_value"))
    mcap = _num(value("mcap"))
    if mcap is not None:
        if lo and lo > 0 and mcap < lo:
            breaches.append(f"instrument value {mcap:,.0f} < {lo:,.0f}")
        if hi and hi > 0 and mcap > hi:
            breaches.append(f"instrument value {mcap:,.0f} > {hi:,.0f}")
    elif (lo and lo > 0) or (hi and hi > 0):
        unknown.append("instrument value not recorded")

    # AT THE TRIGGER DATE nothing is decayed: RVOL and VWAP describe the moment the decision was made, so
    # a miss there is a genuine finding, not market drift. Classifying them as point-in-time in this mode
    # would bury exactly what the requester asked for -- "at the trigger date if they met all criteria".
    persistent = breaches if at_trigger else [b for b in breaches if _kind(b) == "persistent"]
    transient = [] if at_trigger else [b for b in breaches if _kind(b) != "persistent"]
    return {"breaches": breaches, "persistent": persistent, "point_in_time": transient,
            "unknown": unknown,
            # The headline verdict follows the PERSISTENT breaches only. A point-in-time miss is reported
            # separately as "stale" rather than counted as a fault, because it usually is not one.
            "verdict": ("BREACH" if persistent else
                        ("STALE" if transient else ("UNKNOWN" if unknown else "OK")))}


def trigger_state(tickers, db=None):
    """Each order's setup AS IT WAS ON ITS TRIGGER DATE, from squeeze_history.

    This is the question that actually matters (user 2026-08-29: "as volumes change after the trigger it
    is fine - I'm more interested at the trigger date if they met all criteria e.g. mcap"). RVOL and VWAP
    decay after the trigger, so judging a live order on today's values answers the wrong question.

    squeeze_history is the authoritative trigger-time record: quality, risk_reward and rvol are stored as
    they were when the funnel fired. The most recent triggered row per ticker is used.

    MARKET CAP IS THE ONE GAP, and it is not recoverable retrospectively: instrument_mcap holds a single
    row per ticker which the weekly backfill overwrites, so only today's value has ever existed. From
    2026-08-29 instrument_metrics copies it daily, so trigger-date market cap becomes answerable for
    setups triggering from now on -- but not for anything already on the book.
    """
    from db_pool import get_db
    own = db is None
    if own:
        db = get_db()
    try:
        # The trigger must be the one the ORDER CAME FROM: the latest at or before it was placed.
        # Taking simply the most recent trigger per ticker judged 22 of 55 live orders against a trigger
        # that had not happened yet when they were placed -- including several dated the day AFTER -- and
        # produced a confident, wrong count of breaches. A LATERAL join per order is the fix.
        rows = db.run("""select w.ticker, s.triggered_date, s.quality, s.risk_reward, s.rvol,
                                s.timeframe, s.hvf_type, s.outcome
                         from (select distinct on (ticker) ticker, placed_at
                                 from working_orders
                                where status = 'PENDING' and ticker = any(:t)
                                order by ticker, placed_at desc) w
                         left join lateral (
                                select triggered_date, quality, risk_reward, rvol, timeframe,
                                       hvf_type, outcome
                                  from squeeze_history h
                                 where h.ticker = w.ticker and h.triggered_date is not null
                                   and h.triggered_date <= w.placed_at::date
                                 order by h.triggered_date desc limit 1) s on true""",
                      t=list(tickers)) or []
    finally:
        if own:
            db.close()
    cols = ("ticker", "triggered_date", "quality", "rr", "rvol", "timeframe", "hvf_type", "outcome")
    # A row with no triggered_date means no trigger preceded the order -- unknowable, not a pass.
    return {r[0]: dict(zip(cols, r)) for r in rows if r[1] is not None}


def break_state(pairs, db=None):
    """{ticker: stored metrics ON THE DAY THE POSITION OPENED}, from instrument_metrics_daily.

    `pairs` is [(ticker, open_date)]. READ, never recomputed (user 2026-09-04: "it should not need to be
    recomputed - it should be stored"). instrument_metrics writes these daily from the same volume_score
    functions the live path uses, so a stored figure and a screen figure cannot disagree.

    A missing row means the capture did not run that day and the position is UNJUDGEABLE on break-bar
    criteria -- never a pass. That table stored nothing at all between 2026-08-29 and 2026-09-04 because
    its INSERT carried a placeholder with no argument, so absence here is a real possibility and must not
    be read as approval.
    """
    from db_pool import get_db
    own = db is None
    db = db or get_db()
    try:
        out = {}
        for ticker, opened in pairs:
            if not ticker or not opened:
                continue
            rows = db.run("select rvol, above_vwap_setup, atr_expanding, volume_score, as_of, status, "
                          "bar_date from instrument_metrics_daily where ticker = :t and as_of = :d",
                          t=ticker, d=str(opened)[:10]) or []
            if rows:
                rv, avs, atr, vs, as_of, status, bar_date = rows[0]
                # THE ROW'S as_of IS NOT THE BAR IT DESCRIBES (measured 2026-09-06).
                #
                # instrument_metrics.record_daily runs inside the daily scan, which fires in the Morning
                # Chain at ~03:30 UTC -- before any market opens. The row it writes under as_of = today is
                # therefore computed from YESTERDAY'S completed bar. Measured on the live table: of the
                # rows written for as_of 2026-09-05, 1,761 carry bar_date 2026-09-04, and a tail of them
                # are older still (one is 33 days behind).
                #
                # These four measures describe the BREAK bar, and for a position opened today the break
                # IS today. Judging it on yesterday's reading would close a real position on the wrong
                # day's evidence -- worse than closing on no evidence, because it looks like evidence.
                #
                # So the bar must match the day being judged. Anything else is UNJUDGEABLE, which the
                # caller already treats as "leave the position alone" rather than as a pass or a fail.
                if str(bar_date)[:10] != str(opened)[:10]:
                    out[ticker] = {"rvol": None, "above_vwap_setup": None, "atr_expanding": None,
                                   "volume_score": None, "as_of": str(as_of),
                                   "status": f"stale_bar:{str(bar_date)[:10]}"}
                    continue
                out[ticker] = {"rvol": rv, "above_vwap_setup": avs, "atr_expanding": atr,
                               "volume_score": vs, "as_of": str(as_of), "status": status}
    finally:
        if own:
            db.close()
    return out


def audit_positions(user, positions, record_for=None, allows=None, limits=None, db=None):
    """Audit OPEN POSITIONS against the user's current filters, judged at the bar each one opened on.

    THE RULE IS THE OPPOSITE OF THE ORDER AUDIT'S, and deliberately (user 2026-09-04: "rvol vwap and atr
    are not used for open positions UNLESS they have just opened and we are checking these for the first
    time", and "you can look at the rvol on the date it was opened"). A pending order has not broken, so
    its break-bar measures are not applicable. A position HAS broken -- that is what filled it -- so RVOL,
    VWAP, ATR and VolumeScore are exactly what should be judged, as they were on its opening day.

    `positions` is [{"ticker", "deal_id", "opened", "direction"}]. Durable criteria (R:R, Quality,
    instrument value, the direction/location/market gate) are judged too, from the setup the order was
    placed from, so one position is measured against every filter that applies to it.
    """
    if record_for is None or allows is None or limits is None:
        from hvf_web import server, web_users as _wu
        record_for = record_for or server._record
        allows = allows or (lambda rec: server._user_trade_allows(user, rec))
        limits = limits if limits is not None else ((_wu.get_settings(user) or {}).get("limits") or {})
    positions = [p for p in (positions or []) if p.get("ticker")]
    if not positions:
        return {"user": user, "positions": 0, "counts": {}, "rows": []}
    tickers = [p["ticker"] for p in positions]
    breaks = break_state([(p["ticker"], p.get("opened")) for p in positions], db=db)
    try:
        setups = placement_setups(tickers, db=db, statuses=("FILLED",))
    except Exception as exc:
        log.warning("placement setups unavailable for positions (%s)", exc)
        setups = {}
    try:
        from hvf_web import server as _srv
        mcaps = _srv._mcap_map()
    except Exception:
        mcaps = {}
    out = []
    for p in positions:
        tk = p["ticker"]
        rec = record_for(tk) or {}
        setup = setups.get(tk) or {}
        judged = {"rr": setup.get("rr", rec.get("rr")),
                  "quality": setup.get("quality", rec.get("quality")),
                  "mcap": rec.get("mcap") if rec.get("mcap") is not None else mcaps.get(tk)}
        # at_trigger=True: nothing here is "decayed". These are the measurements from the day the
        # position opened, so a miss is a genuine finding rather than market drift.
        res = check_one(judged, breaks.get(tk), limits, allows(rec) if rec else True, at_trigger=True)
        res.update(ticker=tk, deal_id=p.get("deal_id"), name=p.get("name"),
                   opened=str(p.get("opened") or "")[:10],
                   direction=p.get("direction"), size=p.get("size"),
                   break_as_of=(breaks.get(tk) or {}).get("as_of"),
                   in_snapshot=bool(rec))
        if tk not in breaks:
            res["unknown"] = list(res["unknown"]) + ["no stored metrics for the day it opened"]
            res["verdict"] = "BREACH" if res["breaches"] else "UNKNOWN"
        out.append(res)
    counts = {v: sum(1 for r in out if r["verdict"] == v) for v in ("OK", "BREACH", "STALE", "UNKNOWN")}
    return {"user": user, "positions": len(out), "counts": counts, "rows": out}


def audit(user, tickers=None, record_for=None, allows=None, limits=None, at_trigger=False):
    """Audit every live PENDING order against `user`'s current filters.

    The server's own helpers are injected rather than imported, so this module stays testable without a
    Flask app and cannot drift into a second definition of the trade gate.
    """
    from db_pool import get_db
    import instrument_metrics

    if record_for is None or allows is None or limits is None:          # default to the live server helpers
        from hvf_web import server, web_users as _wu
        record_for = record_for or server._record
        allows = allows or (lambda rec: server._user_trade_allows(user, rec))
        limits = limits if limits is not None else ((_wu.get_settings(user) or {}).get("limits") or {})

    if tickers is None:
        db = get_db()
        try:
            rows = db.run("select distinct ticker from working_orders "
                          "where status = 'PENDING' order by ticker") or []
        finally:
            db.close()
        tickers = [r[0] for r in rows]

    stored = instrument_metrics.latest(tickers) if tickers else {}
    # The setup each order was PLACED FROM -- the basis a pending order must be judged on. Reading R:R
    # from today's snapshot instead reports "not recorded" for every instrument without a live setup
    # today, which on 2026-09-03 was 45 of 61 orders: an artefact, not a finding.
    try:
        placements = {} if at_trigger else placement_setups(tickers)
    except Exception as exc:
        # Degrade to the snapshot record rather than failing the whole audit; the verdict is then based
        # on today's setup, which is weaker but not wrong, and the log says so.
        log.warning("placement setups unavailable (%s); judging on the snapshot record instead", exc)
        placements = {}
    try:
        from hvf_web import server as _srv
        mcaps = _srv._mcap_map()
    except Exception:
        mcaps = {}
    fired = trigger_state(tickers) if (at_trigger and tickers) else {}
    out = []
    for tk in tickers:
        rec = record_for(tk) or {}
        if at_trigger:
            # Judge the setup on what it was when it fired, not on today. Anything squeeze_history does
            # not hold for that date stays absent, and therefore UNKNOWN rather than a breach.
            trig = fired.get(tk) or {}
            rec = {k: v for k, v in trig.items() if v is not None and k in ("quality", "rr", "rvol")}
            res = check_one(rec, {}, limits, allows(trig) if trig else True, at_trigger=True)
            res.update(triggered_date=str(trig.get("triggered_date") or "") or None,
                       outcome=trig.get("outcome"))
        else:
            # The gate is asked about the REAL record: direction, location and market are properties of
            # the instrument, and a reduced record would silently answer "allowed" for everything.
            gate_ok = allows(rec) if rec else True
            setup = placements.get(tk)
            judged = rec
            if setup:
                # R:R and Quality come from the setup the order was placed from. Market cap comes from
                # the instrument_mcap map, not the snapshot record, which does not carry it for every
                # ticker -- reading it from there reported "instrument value not recorded" for 40 of 61
                # orders, an artefact of the wrong source rather than absent data.
                judged = {"rr": setup["rr"], "quality": setup["quality"],
                          "mcap": rec.get("mcap") if rec.get("mcap") is not None else mcaps.get(tk)}
            res = check_one(judged, stored.get(tk), limits, gate_ok)
            # Break-bar metrics are not applicable to an order that has not broken.
            res["breaches"] = _durable_only(res["breaches"])
            res["persistent"] = _durable_only(res["persistent"])
            res["point_in_time"] = []
            res["unknown"] = _durable_only(res["unknown"])
            res["verdict"] = ("BREACH" if res["persistent"] else
                              ("UNKNOWN" if res["unknown"] else "OK"))
            if setup:
                res["setup_ready_date"] = str(setup["ready_date"])
        res.update(ticker=tk, in_snapshot=bool(rec),
                   metrics_as_of=(stored.get(tk) or {}).get("as_of"))
        if not rec:
            res["unknown"] = list(res["unknown"]) + [
                "no triggered row in squeeze_history" if at_trigger else "not in the current snapshot"]
            res["verdict"] = "BREACH" if res["breaches"] else "UNKNOWN"
        out.append(res)

    counts = {v: sum(1 for r in out if r["verdict"] == v)
              for v in ("OK", "BREACH", "STALE", "UNKNOWN")}
    return {"user": user, "orders": len(out), "counts": counts, "rows": out}


if __name__ == "__main__":
    import json
    from dotenv import load_dotenv
    load_dotenv(".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    from hvf_web import server
    import sys
    result = audit(server._OWNER, at_trigger="--at-trigger" in sys.argv)
    print(f"\n{result['orders']} live orders for {result['user']}: {result['counts']}\n")
    for row in sorted(result["rows"], key=lambda r: (r["verdict"] != "BREACH", r["ticker"])):
        detail = "; ".join(row["breaches"] or row["unknown"]) or "-"
        print(f"  {row['ticker']:12} {row['verdict']:8} {detail}")
