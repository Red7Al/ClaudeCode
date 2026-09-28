# ======================================================================================================================
# File:         data_freshness.py
# Created:      2026-09-28
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# ONE answer to "is what you are looking at current?" (owner 2026-09-28: "there needs to be a mechanism to eradicate
# stale data", after the live site served a snapshot 18.5 hours old with nothing on screen to say so).
#
# WHY THIS EXISTS. Every staleness failure this project has had was invisible for the SAME reason -- the job that fed
# the data reported success, and nothing measured the data itself. Measured on 2026-09-28:
#
#   live site snapshot          18.5 hours stale   found by the owner
#   Supabase snapshot copy      42 days stale      found only when the owner asked which store was primary
#   signal_log                  7.5 weeks stale    found by a table scan during an unrelated review
#   squeeze_history (August)    8 days stale       found by the owner
#   instrument_metrics          6 days empty       found behind a green workflow
#
# Not one was caught by the system.
#
# THREE DESIGN RULES, chosen because the owner asked for something well engineered that will not itself need repeated
# fixing:
#
#   1. THE WEBSITE IS THE TRIGGER, not a cron job. This project's recurring defect is correct code that nothing ever
#      calls -- a scheduled freshness checker would be one more thing to notice had stopped. /api/freshness is read by
#      every page load, so if this breaks it is visible immediately rather than in a log nobody reads.
#   2. FAIL CLOSED. A surface whose measurement RAISES is reported stale with its error, never skipped and never
#      assumed healthy. "I could not tell" and "it is fine" must not look the same -- that confusion IS the bug above.
#   3. GENEROUS, FIXED LIMITS RATHER THAN A TRADING CALENDAR. Every limit below clears a normal weekend by design. A
#      calendar-aware threshold is where a checker like this starts crying wolf on bank holidays and gets muted, and a
#      muted alarm is worse than none. If a limit proves wrong, widen the number -- do not add a calendar.
#
# Adding a surface is ONE entry in SURFACES. A surface with no entry is a visible omission, not an oversight.
# ======================================================================================================================

import datetime as dt
import logging

log = logging.getLogger("data_freshness")

_UTC = dt.timezone.utc


def _age_hours(when) -> float:
    """Hours between `when` and now. Accepts a datetime, date, or ISO-ish string."""
    if when is None:
        raise ValueError("no timestamp recorded")
    if isinstance(when, str):
        text = when.strip().replace("Z", "+00:00")
        when = dt.datetime.fromisoformat(text)
    if isinstance(when, dt.datetime):
        moment = when if when.tzinfo else when.replace(tzinfo=_UTC)
    elif isinstance(when, dt.date):
        # A bare date means "that day's data"; treat it as end of that day so a same-day bar reads as 0 hours old
        # rather than up to 24, which would otherwise make every surface look half a day stale by lunchtime.
        moment = dt.datetime.combine(when, dt.time(23, 59), tzinfo=_UTC)
    else:
        raise TypeError(f"cannot read a timestamp from {type(when).__name__}")
    # CLAMPED AT ZERO. A bare date is read as end-of-day above, so today's date is in the FUTURE for most
    # of the day -- the first run of this module reported instrument_metrics at -9.8 hours, which reads as
    # "fresher than now" and is meaningless. Nothing can be newer than the moment we ask.
    return max(0.0, (dt.datetime.now(_UTC) - moment).total_seconds() / 3600.0)


# ----------------------------------------------------------------------------------------------------------------------
# The measurements. Each returns the moment its surface was last genuinely updated, or raises.
# Each must be CHEAP -- an indexed max() or an already-loaded value -- because a page load waits on it.
# ----------------------------------------------------------------------------------------------------------------------

def _snapshot_generated():
    from hvf_web import server
    return (server._load_snapshot() or {}).get("generated_utc")


def _squeeze_history_refreshed():
    from db_pool import get_db
    db = get_db()
    try:
        return (db.run("select max(refreshed_at) from squeeze_history") or [[None]])[0][0]
    finally:
        db.close()


def _instrument_metrics_recorded():
    from db_pool import get_db
    db = get_db()
    try:
        return (db.run("select max(as_of) from instrument_metrics_daily") or [[None]])[0][0]
    finally:
        db.close()


def _market_data_worst():
    """The OLDEST of each market's newest bar, plus which market that is.

    NOT a global max(bar_date) (owner 2026-09-28: "not all markets are open monday to friday"). MEASURED
    the same day, the newest bar per market spanned THREE DAYS:

        Commodities 2026-09-27 | FX and Crypto 2026-09-26 | every equity market 2026-09-25 |
        SSE and SZSE (China) 2026-09-24

    A global max would report 2026-09-27 and hide the other three days entirely, so markets could stop
    updating behind whichever one happens to be furthest ahead. Taking the WORST market cannot be masked
    by the best. NOTE what this does and does not claim: the spread above is measured from this
    database on one day. WHY each market sits where it does -- trading days, holidays, feed timing -- is
    NOT established here and must not be inferred from it.

    Read from instrument_metrics_daily (26k rows, indexed) rather than price_history (1.7M rows, where a
    group-by times out on this tier), and mapped to a market through the snapshot already in memory.
    """
    from db_pool import get_db
    from hvf_web import server
    snap = server._load_snapshot() or {}
    market_of = {r.get("ticker"): r.get("market") for r in snap.get("records", []) if r.get("ticker")}
    db = get_db()
    try:
        rows = db.run("select ticker, max(bar_date) from instrument_metrics_daily group by ticker") or []
    finally:
        db.close()
    newest = {}
    for ticker, bar_date in rows:
        market = market_of.get(ticker)
        if not market or not bar_date:
            continue
        if market not in newest or bar_date > newest[market]:
            newest[market] = bar_date
    if not newest:
        raise ValueError("no market could be matched to a bar date")
    market, bar_date = min(newest.items(), key=lambda kv: kv[1])
    return bar_date, f"oldest market: {market} at {bar_date}"


def _supabase_snapshot_copy():
    import scanner_snapshot_store
    meta = scanner_snapshot_store.current_metadata() or {}
    return meta.get("generated_utc")


# ----------------------------------------------------------------------------------------------------------------------
# THE REGISTRY. name, what it is in the owner's words, the measurement, the limit in hours, and whether a breach is
# CUSTOMER FACING (drives the on-site banner) or internal (reported, but the site stays quiet).
#
# Limits are deliberately round and generous enough to clear a weekend. See design rule 3.
# ----------------------------------------------------------------------------------------------------------------------

SURFACES = (
    {"name": "scanner_snapshot", "label": "Scanner data",
     "measure": _snapshot_generated, "max_age_hours": 30, "customer_facing": True,
     "owner": "Morning Chain / Scanner Snapshot Publish"},
    {"name": "market_data", "label": "Market data",
     # 168h (7 days). NOT derived from a trading calendar -- no calendar has been verified here. It is
     # set wider than the largest gap MEASURED on 2026-09-28 (the worst market, China, was 4 days behind)
     # with margin, so a genuine outage is caught while a normal market pause is not announced. If a
     # market legitimately pauses longer than this, WIDEN THE NUMBER after measuring it; do not add a
     # calendar. A limit that fires on ordinary days gets muted, and a muted alarm is worse than none.
     "measure": _market_data_worst, "max_age_hours": 168, "customer_facing": True,
     "owner": "Price Data Refresh"},
    {"name": "squeeze_history", "label": "Squeeze History",
     # 96h, not 48: fed by the Morning Chain, so Friday's run to Monday's is ~60 hours and a 48h limit
     # would fire every Monday morning. A test pins this against the weekend gap.
     "measure": _squeeze_history_refreshed, "max_age_hours": 96, "customer_facing": True,
     "owner": "Morning Chain (refresh_daily)"},
    {"name": "instrument_metrics", "label": "Daily instrument metrics",
     "measure": _instrument_metrics_recorded, "max_age_hours": 96, "customer_facing": False,
     "owner": "Morning Chain (record_daily)"},
    {"name": "supabase_snapshot_copy", "label": "Supabase snapshot backup",
     "measure": _supabase_snapshot_copy, "max_age_hours": 96, "customer_facing": False,
     "owner": "Scanner Snapshot Publish"},
)


def check() -> dict:
    """Measure every surface. Never raises -- a checker that can fail the page it protects is not a safeguard.

    Returns {"checked_utc", "stale": [names], "customer_facing_stale": [names], "surfaces": [ ... ]} where each
    surface carries name/label/owner/age_hours/max_age_hours/stale/error.
    """
    out, stale, facing = [], [], []
    for s in SURFACES:
        row = {"name": s["name"], "label": s["label"], "owner": s["owner"],
               "max_age_hours": s["max_age_hours"], "customer_facing": s["customer_facing"],
               "age_hours": None, "stale": True, "error": None, "note": None}
        try:
            measured = s["measure"]()
            # A measure may return either a moment, or (moment, note) when it has something worth naming
            # -- e.g. WHICH market is furthest behind. One return shape, no second mechanism.
            moment, note = measured if isinstance(measured, tuple) else (measured, None)
            row["note"] = note
            row["age_hours"] = round(_age_hours(moment), 1)
            row["stale"] = row["age_hours"] > s["max_age_hours"]
        except Exception as exc:
            # FAIL CLOSED (design rule 2): unmeasurable is reported stale, with the reason, never as healthy.
            row["error"] = f"{type(exc).__name__}: {exc}"
            log.warning("freshness: %s could not be measured: %s", s["name"], row["error"])
        if row["stale"]:
            stale.append(s["name"])
            if s["customer_facing"]:
                facing.append(s["name"])
        out.append(row)
    return {"checked_utc": dt.datetime.now(_UTC).isoformat(),
            "stale": stale, "customer_facing_stale": facing, "surfaces": out}


def banner(result: dict = None) -> str:
    """One sentence for the site to show, or "" when every customer-facing surface is current."""
    result = result or check()
    bad = [s for s in result["surfaces"] if s["stale"] and s["customer_facing"]]
    if not bad:
        return ""
    parts = []
    for s in sorted(bad, key=lambda r: r["name"]):
        if s["error"] or s["age_hours"] is None:
            parts.append(f"{s['label']} (age unknown)")
        elif s["age_hours"] >= 48:
            parts.append(f"{s['label']} is {int(s['age_hours'] // 24)} days old")
        else:
            parts.append(f"{s['label']} is {int(s['age_hours'])} hours old")
    return "Some data on this page is not current: " + "; ".join(parts) + "."


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print(json.dumps(check(), indent=2, default=str))
    print("\nbanner:", banner() or "(everything current)")
