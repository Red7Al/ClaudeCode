#!/usr/bin/env python3
# ======================================================================================================================
# File:         closing_bar_capture.py
# Author:       Alex Hind (via Claude)
# Created:      2026-09-07
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Stores each instrument's break-bar metrics for TODAY, retrieved in the last minutes of its own session (user
# 2026-09-07: "retrieve more data towards the end of the working day - does that not solve this?").
#
# IT DOES. The auto-closer judges a position on the break bar, the break bar is the day the position opened, and
# instrument_metrics_daily only learns about day D on the morning of D+1 -- so a position opened today was unjudgeable
# at the only moment it could still be traded. Pulling the bar near the close fills exactly that gap, and it fills it by
# STORING a row, so the closer keeps reading the store and nothing is recomputed at decision time (user 2026-09-04:
# "it should not need to be recomputed - it should be stored").
#
# WHY NOT A FIXED SET OF END-OF-DAY RUNS. Measured across the live universe on 2026-09-07 there are THIRTEEN distinct
# closing instants, not four or five: the obvious ones plus Oslo 14:20, Brussels 15:40 and Dublin 16:30 UTC. A fixed
# schedule would need re-cutting twice a year for daylight saving -- separately for the northern and southern
# hemispheres -- and re-cut again whenever a market is added. This asks market_hours which instruments are in their
# closing window RIGHT NOW, so one job covers all thirteen and stays correct on its own.
#
# THE BAR IS UNFINISHED, AND THAT BIAS RUNS TOWARD SELLING. An incomplete bar has less volume, so RVOL and VolumeScore
# read low, so a test fails, so the closer closes a position that the finished bar would have kept. Measured over 472
# sessions: 30 minutes before the close a median 86.4% of the day's volume has traded, but only 57.8% at the 5th
# percentile and a median 59.3% on the ASX, whose closing auction is a large part of the day. The day's HIGH-LOW RANGE
# is far better behaved -- already complete in 79% of sessions -- so ATR is barely affected, and the provisional close
# sits within about 8% of the day's range, so VWAP is barely affected. Volume is the problem, and volume alone is
# corrected: it is divided by the exchange's own 5th-percentile completion share, which OVER-states the full session in
# ~95% of sessions so the correction can only ever make a test PASS.
#
# Measured effect on the decision itself, 910 comparisons of a closing-window bar against the same day finished:
#   * raw partial bar        3.08% of verdicts disagree, 1.98% of them FALSE CLOSES
#   * with the projection    4.07% disagree, but only 1.21% false closes -- the rest became false KEEPS,
#                            which cost nothing because leaving a position open is the status quo.
#
# THE ROW IS MARKED PARTIAL AND SUPERSEDES ITSELF. It is written as (as_of = today, bar_date = today) with
# status 'partial_closing_bar'. Tomorrow's Morning Chain writes (as_of = tomorrow, bar_date = today) from the finished
# bar, and order_filter_audit.break_state selects `where bar_date = :d order by as_of desc limit 1` -- so the complete
# row wins automatically the moment it exists. Nothing has to clean this up.
#
# Usage:  python closing_bar_capture.py [--tickers A,B] [--all] [--window 30] [--dry-run]
# ======================================================================================================================

import argparse
import datetime as dt
import logging

log = logging.getLogger("closing_bar_capture")

STATUS = "partial_closing_bar"


def due(tickers, now=None, minutes=None):
    """Which of `tickers` are inside their own exchange's closing window right now."""
    import market_hours
    mins = minutes if minutes is not None else market_hours.CLOSING_WINDOW_MINUTES
    return [t for t in tickers if market_hours.in_closing_window(t, now=now, minutes=mins)]


def held_today(user=None, on_date=None):
    """Tickers with a position opened TODAY — the only ones the closer can act on, so the only ones worth
    a live fetch. Usually none, occasionally one or two; the whole point of scoping it this way is that the
    job costs nothing on the days it has nothing to do."""
    from hvf_web import server
    import ig_shim
    from db_pool import get_db
    user = user or server._OWNER
    on_date = str(on_date or dt.datetime.now(dt.timezone.utc).date())
    db = get_db()
    try:
        epic2tk = {str(r[1]): r[0] for r in (db.run("select ticker, epic from epic_lookup") or []) if r[1]}
    finally:
        db.close()
    with ig_shim._IG_LOCK, ig_shim.acting_session(user):
        raw = ig_shim.get_open_positions() or []
    out = []
    for p in raw:
        mk, pd = (p.get("market") or {}), (p.get("position") or {})
        tk = epic2tk.get(str(mk.get("epic") or ""))
        opened = str(pd.get("createdDateUTC") or pd.get("createdDate") or "")[:10]
        if tk and opened == on_date:
            out.append(tk)
    return sorted(set(out))


def todays_bar(ticker):
    """Today's partial daily bar from the exchange, with its volume projected to a full session.

    (date, high, low, close, projected_volume) or None. The projection factor is the exchange's own
    5th-percentile completion share; where that is unknown the bar is REFUSED rather than passed through
    uncorrected, because an uncorrected volume is the one that closes a position it should not.
    """
    import market_hours
    import yfinance as yf
    factor = market_hours.volume_projection_factor(ticker)
    if not factor:
        log.warning("%s: no measured volume-completion share for its exchange; not capturing", ticker)
        return None
    df = yf.Ticker(market_hours.yahoo_symbol(ticker)).history(period="5d", interval="1d")
    if df is None or df.empty:
        return None
    idx = df.index[-1]
    session_date = idx.date()
    # The exchange's own date, not UTC's: Sydney closes at 06:12 UTC and Yahoo stamps the bar in local time.
    row = df.iloc[-1]
    vol = float(row.get("Volume") or 0)
    if vol <= 0:
        log.warning("%s: today's bar reports no volume yet; not capturing", ticker)
        return None
    return (session_date.isoformat(), float(row["High"]), float(row["Low"]), float(row["Close"]),
            vol / factor)


def capture(tickers, as_of=None, dry_run=False, db=None):
    """Compute and store the closing-window metrics for `tickers`. Returns a summary; never raises."""
    import instrument_metrics
    import price_store
    from db_pool import get_db
    summary = {"as_of": None, "attempted": 0, "stored": 0, "skipped": 0, "rows": []}
    if not tickers:
        return summary
    as_of = as_of or dt.datetime.now(dt.timezone.utc).date()
    summary["as_of"] = str(as_of)
    own = db is None
    try:
        if own:
            db = get_db()
        instrument_metrics.ensure_schema(db)
        for ticker in tickers:
            summary["attempted"] += 1
            try:
                partial = todays_bar(ticker)
                if not partial:
                    summary["skipped"] += 1
                    continue
                # The stored daily history, with today's partial bar appended -- so VWAP, ATR and
                # VolumeScore are computed over exactly the window they use every other day.
                hist = instrument_metrics._bars(ticker, as_of, db)
                hist = [b for b in hist if str(b[0])[:10] < partial[0]]
                if len(hist) < 2 * 14 + 1:
                    log.warning("%s: not enough history to judge a break bar", ticker)
                    summary["skipped"] += 1
                    continue
                m = instrument_metrics.compute(ticker, hist + [partial])
                m["status"] = STATUS
                if dry_run:
                    summary["rows"].append({"ticker": ticker, **{k: m.get(k) for k in
                                            ("bar_date", "rvol", "above_vwap_setup", "atr_expanding",
                                             "volume_score")}})
                    continue
                db.run(f"""insert into {instrument_metrics.TABLE}
                             (ticker, as_of, bar_date, rvol, rvol_date, above_vwap, above_vwap_setup,
                              atr_expanding, volume_score, volume_score_max, wk52_low, wk52_high,
                              direction, status, recorded_at)
                           values (:t,:d,:bd,:rv,:rd,:av,:avs,:atr,:vs,:vsm,:lo,:hi,:dir,:st, now())
                           on conflict (ticker, as_of) do update set
                             bar_date=:bd, rvol=:rv, rvol_date=:rd, above_vwap=:av,
                             above_vwap_setup=:avs, atr_expanding=:atr, volume_score=:vs,
                             volume_score_max=:vsm, wk52_low=:lo, wk52_high=:hi,
                             direction=:dir, status=:st, recorded_at=now()""",
                       t=ticker, d=str(as_of), bd=m.get("bar_date"), rv=m.get("rvol"),
                       rd=m.get("rvol_date"), av=m.get("above_vwap"), avs=m.get("above_vwap_setup"),
                       atr=m.get("atr_expanding"), vs=m.get("volume_score"),
                       vsm=m.get("volume_score_max"), lo=m.get("wk52_low"), hi=m.get("wk52_high"),
                       dir=m.get("direction"), st=STATUS)
                summary["stored"] += 1
                summary["rows"].append({"ticker": ticker, "bar_date": m.get("bar_date"),
                                        "volume_score": m.get("volume_score"),
                                        "above_vwap_setup": m.get("above_vwap_setup"),
                                        "atr_expanding": m.get("atr_expanding")})
                log.info("captured %s for %s: %s", ticker, m.get("bar_date"), summary["rows"][-1])
            except Exception as exc:
                summary["skipped"] += 1
                log.warning("closing capture failed for %s: %s", ticker, exc)
    except Exception as exc:
        log.error("closing capture pass failed: %s", exc)
    finally:
        if own and db is not None:
            try:
                db.close()
            except Exception:
                pass
    return summary


def run(user=None, tickers=None, whole_universe=False, minutes=None, dry_run=False, now=None):
    """One pass: whoever is in their closing window right now, and holds a position opened today."""
    summary = {"considered": 0, "due": 0}
    try:
        if tickers:
            candidates = list(tickers)
        elif whole_universe:
            import run_hvf_report
            candidates = [t for ts in run_hvf_report.UNIVERSE.values() for t in ts]
        else:
            candidates = held_today(user)
        summary["considered"] = len(candidates)
        wanted = due(candidates, now=now, minutes=minutes)
        summary["due"] = len(wanted)
        if not wanted:
            log.info("nothing in a closing window (%d considered)", len(candidates))
            return summary
        summary.update(capture(wanted, dry_run=dry_run))
    except Exception as exc:
        log.error("closing capture run failed: %s", exc)
    return summary


if __name__ == "__main__":
    from dotenv import load_dotenv; load_dotenv(override=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Store closing-window break-bar metrics.")
    ap.add_argument("--user", help="acting user (default: the account owner)")
    ap.add_argument("--tickers", help="comma-separated subset, bypassing the held-today scope")
    ap.add_argument("--all", action="store_true", help="consider the whole universe, not just held positions")
    ap.add_argument("--window", type=int, help="closing-window minutes (default: market_hours')")
    ap.add_argument("--dry-run", action="store_true", help="compute and print, store nothing")
    a = ap.parse_args()
    print(run(user=a.user,
              tickers=[t.strip() for t in a.tickers.split(",")] if a.tickers else None,
              whole_universe=a.all, minutes=a.window, dry_run=a.dry_run))
