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
            res = check_one(rec, stored.get(tk), limits, allows(rec) if rec else True)
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
