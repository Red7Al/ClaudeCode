# ======================================================================================================================
# File:         squeeze_history.py
# Author:       Alex Hind
# Created:      2026-07-17
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# The SQUEEZE HISTORY store + the 15-month engine replay (user 2026-07-17, P-09 / P-21a): "move through 15 months of
# price history for each instrument and add all the squeeze to the squeeze history", and "for all instruments find all
# the funnels (along with entry, stop and target price) over the last 15 months".
#
# hvf_triggers only holds what a LIVE scan happened to witness, so it starts on 2026-06-30 and misses every funnel that
# formed before the recorder existed — or while an instrument was outside the universe. This replays the engine over
# price_history to reconstruct them, giving P-21b a population big enough to draw conclusions from.
#
# HOW THE REPLAY STAYS HONEST
#   * Trend is recomputed AS OF each step via price_action._trend_from_weekly over a weekly frame built from bars up to
#     that date. get_trend_structure() fetches its own data and only ever knows "now"; using it would score every
#     historical bar with today's trend and fabricate a plausible-looking history. That split is why P-09 was blocked.
#   * Only bars up to the step date are passed to the engine. No lookahead.
#   * The step is WEEKLY (every 5 trading bars): a funnel persists for weeks, so weekly discovery finds the same
#     instances at ~1/5 the cost (~55 min vs 4.6 h for the universe). It only affects first_seen (±4 days) — NOT the
#     trigger date or the outcome, which are derived exactly from price afterwards.
#   * Funnel identity is (ticker, timeframe, h3_date, l3_date) — the SAME key hvf_recorder uses, so a replayed funnel
#     and a live-recorded one are the same row, and re-running is a no-op.
#
# Usage:
#   python squeeze_history.py --months 15                 # whole universe
#   python squeeze_history.py --months 15 --tickers HWDN.L,BARC.L
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-07-17  Alex Hind   Initial build — squeeze_history table + 15-month weekly-step engine replay.
# ======================================================================================================================

import argparse
import datetime as dt
import logging
from collections import defaultdict

log = logging.getLogger("squeeze_history")

_DDL = [
    """create table if not exists squeeze_history (
        id             bigserial primary key,
        ticker         text not null,
        market         text,
        timeframe      text,
        hvf_type       text,
        h1_level double precision, h2_level double precision, h3_level double precision,
        l1_level double precision, l2_level double precision, l3_level double precision,
        h1_date date, h2_date date, h3_date date, l1_date date, l2_date date, l3_date date,
        entry_level    double precision,
        stop_level     double precision,
        target_level   double precision,
        quality        double precision,
        risk_reward    double precision,
        long_trend     text,
        first_seen     date,      -- first replay step at which this funnel existed
        last_seen      date,      -- last replay step at which it was still the current funnel
        first_signal   text,      -- DEVELOPING / READY / TRIGGERED when first seen
        ready_date     date,      -- first step seen READY
        triggered_date date,      -- DERIVED from price (first close beyond entry after the pivots), not a step
        outcome        text,      -- TARGET / STOPPED / OPEN / NEVER_TRIGGERED
        outcome_date   date,
        return_pct     double precision,
        rvol           double precision,
        source         text default 'replay',
        refreshed_at   timestamptz not null default now(),
        recorded_at    timestamptz not null default now()
    )""",
    "alter table squeeze_history add column if not exists refreshed_at timestamptz not null default now()",
    # Same funnel-instance identity as hvf_triggers, so replay and live recording agree on what "one funnel" is.
    """create unique index if not exists uq_squeeze_history_instance
       on squeeze_history (ticker, coalesce(timeframe,''), coalesce(h3_date,'1900-01-01'),
                           coalesce(l3_date,'1900-01-01'))""",
    "create index if not exists idx_squeeze_history_ticker on squeeze_history (ticker, triggered_date desc)",
    "create index if not exists idx_squeeze_history_outcome on squeeze_history (outcome)",
]

RVOL_BARS = 20          # mirrors hvf_web/server.py::RVOL_BARS
STEP_BARS = 5           # weekly step


def ensure_schema(db):
    for stmt in _DDL:
        db.run(stmt)


def _f(v):
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def _d(v):
    return str(v)[:10] if v else None


def _weekly(daily):
    return daily.resample("W").agg({"Open": "first", "High": "max", "Low": "min",
                                    "Close": "last", "Volume": "sum"}).dropna()


def _exit_outcome(bull, entry, stop, target, bars):
    """(outcome, outcome_date, return_pct) walking bars from the trigger. Stop wins a same-bar tie —
    the worst case, and the same convention the Performance report uses."""
    for bd, hi, lo, _cl, _v in bars:
        if hi is None or lo is None:
            continue
        hit_stop = (lo <= stop) if bull else (hi >= stop)
        hit_tgt = (hi >= target) if bull else (lo <= target)
        if hit_stop:
            return "STOPPED", bd, ((stop - entry) / entry * 100 if bull else (entry - stop) / entry * 100)
        if hit_tgt:
            return "TARGET", bd, ((target - entry) / entry * 100 if bull else (entry - target) / entry * 100)
    last = next((c for (_b, _h, _l, c, _v) in reversed(bars) if c is not None), None)
    if last is None:
        return "OPEN", None, None
    return "OPEN", None, ((last - entry) / entry * 100 if bull else (entry - last) / entry * 100)


def _trigger_date(bull, entry, ready, bars):
    """First bar on/after the funnel's last pivot whose CLOSE breaks the entry — hvf_clean's own trigger
    rule. None = the funnel never triggered inside the window."""
    for bd, _hi, _lo, cl, _v in bars:
        if cl is None or (ready and bd < ready):
            continue
        if (cl > entry) if bull else (cl < entry):
            return bd
    return None


def _rvol_at(bars, td):
    i = next((k for k, b in enumerate(bars) if b[0] == td), None)
    if i is None:
        return None
    vol = bars[i][4]
    prior = [b[4] for b in bars[max(0, i - RVOL_BARS):i] if b[4]]
    if not vol or len(prior) < 5:
        return None
    avg = sum(prior) / len(prior)
    return round(vol / avg, 2) if avg > 0 else None


def replay_ticker(ticker: str, market: str, months: int = 15):
    """Every distinct funnel the engine would have seen for `ticker` over the window, with its levels,
    the date it really triggered and how it resolved. Returns a list of dicts (one per funnel)."""
    import price_store
    from hvf_clean import detect_hvf
    from price_action import _trend_from_weekly

    end = dt.date.today()
    # The engine's longest daily window is 240 bars and the trend needs ~30 weekly bars, so fetch a year
    # more than the reporting window or the earliest steps would be scored on truncated history.
    bars_df = price_store.get_bars(ticker, end - dt.timedelta(days=months * 31 + 420), end)
    if bars_df is None or len(bars_df) < 80:
        return []
    window_start = end - dt.timedelta(days=months * 31)

    tuples = [(i.date(), r.High, r.Low, r.Close, r.Volume) for i, r in bars_df.iterrows()]
    by_date = {t[0]: k for k, t in enumerate(tuples)}

    funnels = {}
    idx = [k for k, t in enumerate(tuples) if t[0] >= window_start]
    for k in idx[::STEP_BARS]:
        step_date = tuples[k][0]
        daily = bars_df.iloc[:k + 1]
        if len(daily) < 60:
            continue
        wk = _weekly(daily)
        sig = _trend_from_weekly(wk, ticker).get("signal", "SIDEWAYS")
        if sig == "SIDEWAYS":
            continue                                    # the engine rejects these outright — skip the work
        # Pick the BEST timeframe for this step, exactly as get_hvf_signal_mtf does live
        # (TRIGGERED > READY > DEVELOPING, then quality). Recording all four would triple-count one
        # funnel: the same pivots surface under daily-240/180/90 at once (HWDN.L h3=2025-12-22
        # l3=2026-01-05 appeared 3x), which would bias any analysis built on these rows.
        cands = []
        for tf, frame in (("daily-240", daily.tail(240)), ("daily-180", daily.tail(180)),
                          ("daily-90", daily.tail(90)), ("weekly", wk.tail(120))):
            try:
                r = detect_hvf(ticker, frame, sig, weekly=(tf == "weekly"))
            except Exception:
                continue
            if r and r.get("hvf_type") and r.get("h3_date") and r.get("l3_date"):
                cands.append((tf, r))
        _rank = {"TRIGGERED": 3, "READY": 2, "DEVELOPING": 1}
        for tf, r in sorted(cands, key=lambda c: (-_rank.get(c[1].get("hvf_signal"), 0),
                                                  -(c[1].get("pattern_quality") or 0)))[:1]:
            key = (_d(r.get("h3_date")), _d(r.get("l3_date")))
            f = funnels.get(key)
            if f is None:
                funnels[key] = f = {
                    "ticker": ticker, "market": market, "timeframe": tf, "hvf_type": r.get("hvf_type"),
                    "entry_level": _f(r.get("h3_level")), "stop_level": _f(r.get("stop_level")),
                    "target_level": _f(r.get("target")), "quality": _f(r.get("pattern_quality")),
                    "risk_reward": _f(r.get("risk_reward")), "long_trend": sig,
                    "first_seen": step_date, "first_signal": r.get("hvf_signal"), "ready_date": None,
                    **{f"{p}_level": _f(r.get(f"{p}_level")) for p in ("h1", "h2", "h3", "l1", "l2", "l3")},
                    **{f"{p}_date": _d(r.get(f"{p}_date")) for p in ("h1", "h2", "h3", "l1", "l2", "l3")},
                }
            f["last_seen"] = step_date
            if r.get("hvf_signal") in ("READY", "TRIGGERED") and f["ready_date"] is None:
                f["ready_date"] = step_date

    # Resolve each funnel against price: the exact break, then the outcome from there.
    out = []
    for f in funnels.values():
        e, s, t = f["entry_level"], f["stop_level"], f["target_level"]
        if e is None or s is None or t is None:
            continue
        bull = f["hvf_type"] == "BULLISH"
        ready = max([d for d in (f["h3_date"], f["l3_date"]) if d], default=None)
        ready_d = dt.date.fromisoformat(ready) if ready else None
        td = _trigger_date(bull, e, ready_d, tuples)
        if td is None:
            f.update(triggered_date=None, outcome="NEVER_TRIGGERED", outcome_date=None,
                     return_pct=None, rvol=None)
        else:
            # Walk from the bar AFTER the trigger: entry is the trigger bar's CLOSE, so that bar's own
            # intraday high/low happened before you were in the trade. Including it counted same-bar
            # stops that could not have hit you and over-stated the loss rate (user 2026-07-17; the
            # Performance report was fixed the same way, P-01).
            i = by_date.get(td, 0)
            oc, od, ret = _exit_outcome(bull, e, s, t, tuples[i + 1:])
            f.update(triggered_date=td, outcome=oc, outcome_date=od,
                     return_pct=(round(ret, 2) if ret is not None else None), rvol=_rvol_at(tuples, td))
        out.append(f)
    return out


def store(db, rows: list, update_existing: bool = False) -> int:
    """Store funnels without duplicating an instance.

    The historical backfill keeps its original insert-only behaviour. The daily current-snapshot path uses
    ``update_existing`` to advance mutable lifecycle fields while preserving the earliest first-seen/ready
    evidence already recorded for that funnel.
    """
    changed = 0
    cols = ("ticker", "market", "timeframe", "hvf_type", "h1_level", "h2_level", "h3_level",
            "l1_level", "l2_level", "l3_level", "h1_date", "h2_date", "h3_date", "l1_date", "l2_date",
            "l3_date", "entry_level", "stop_level", "target_level", "quality", "risk_reward",
            "long_trend", "first_seen", "last_seen", "first_signal", "ready_date", "triggered_date",
            "outcome", "outcome_date", "return_pct", "rvol")
    ph = ",".join(f":{c}" for c in cols)
    conflict = ("on conflict (ticker, coalesce(timeframe,''), coalesce(h3_date,'1900-01-01'), "
                "coalesce(l3_date,'1900-01-01')) ")
    if update_existing:
        conflict += """do update set
            market=coalesce(excluded.market,squeeze_history.market),
            hvf_type=coalesce(excluded.hvf_type,squeeze_history.hvf_type),
            entry_level=coalesce(excluded.entry_level,squeeze_history.entry_level),
            stop_level=coalesce(excluded.stop_level,squeeze_history.stop_level),
            target_level=coalesce(excluded.target_level,squeeze_history.target_level),
            quality=coalesce(excluded.quality,squeeze_history.quality),
            risk_reward=coalesce(excluded.risk_reward,squeeze_history.risk_reward),
            last_seen=case when squeeze_history.last_seen is null then excluded.last_seen
                           when excluded.last_seen is null then squeeze_history.last_seen
                           else greatest(squeeze_history.last_seen,excluded.last_seen) end,
            first_seen=case when squeeze_history.first_seen is null then excluded.first_seen
                            when excluded.first_seen is null then squeeze_history.first_seen
                            else least(squeeze_history.first_seen,excluded.first_seen) end,
            first_signal=coalesce(squeeze_history.first_signal,excluded.first_signal),
            ready_date=case when squeeze_history.ready_date is null then excluded.ready_date
                            when excluded.ready_date is null then squeeze_history.ready_date
                            else least(squeeze_history.ready_date,excluded.ready_date) end,
            refreshed_at=now() returning id"""
    else:
        conflict += "do nothing returning id"
    sql = f"insert into squeeze_history ({','.join(cols)}) values ({ph}) {conflict}"
    for r in rows:
        try:
            if db.run(sql, **{c: r.get(c) for c in cols}):
                changed += 1
        except Exception as e:
            log.warning(f"store failed {r.get('ticker')}: {e}")
    return changed


def _snapshot_rows(snapshot: dict) -> list:
    """Convert the current Scanner's signal records into history-compatible funnel instances."""
    generated = str((snapshot or {}).get("generated_utc") or dt.datetime.now(dt.timezone.utc).isoformat())[:10]
    rows = []
    for record in (snapshot or {}).get("records", []):
        if not record.get("has_signal"):
            continue
        card = record.get("_card") or {}
        status = str(record.get("status") or card.get("hvf_signal") or "DEVELOPING").upper()
        row = {
            "ticker": record.get("ticker"), "market": record.get("market"),
            "timeframe": record.get("timeframe") or card.get("hvf_timeframe"),
            "hvf_type": card.get("hvf_type") or ("BULLISH" if record.get("direction") == "BULL" else "BEARISH"),
            "entry_level": record.get("entry") or card.get("h3_level"),
            "stop_level": record.get("stop") or card.get("stop_level"),
            "target_level": record.get("target") or card.get("target"),
            "quality": record.get("quality"), "risk_reward": record.get("rr") or card.get("risk_reward"),
            "long_trend": None, "first_seen": generated, "last_seen": generated,
            "first_signal": status, "ready_date": generated if status in {"READY", "TRIGGERED"} else None,
            "triggered_date": None, "outcome": "NEVER_TRIGGERED", "outcome_date": None,
            "return_pct": None, "rvol": None,
        }
        for pivot in ("h1", "h2", "h3", "l1", "l2", "l3"):
            row[f"{pivot}_level"] = card.get(f"{pivot}_level")
            row[f"{pivot}_date"] = card.get(f"{pivot}_date") or record.get(f"{pivot}_date")
        pivot_dates = [row[f"{pivot}_date"] for pivot in ("h3", "l3") if row[f"{pivot}_date"]]
        if status in {"READY", "TRIGGERED"} and pivot_dates:
            row["ready_date"] = max(pivot_dates)
        if row["ticker"] and row["h3_date"] and row["l3_date"]:
            rows.append(row)
    return rows


def _price_bars(db, tickers: list, start: str) -> dict:
    """Fetch active-history bars in bounded batches to avoid one enormous IN expression."""
    by_ticker = defaultdict(list)
    for offset in range(0, len(tickers), 100):
        batch = tickers[offset:offset + 100]
        params = {f"t{i}": ticker for i, ticker in enumerate(batch)}
        slots = ",".join(f":t{i}" for i in range(len(batch)))
        raw = db.run(
            f"select ticker,bar_date,high,low,close,volume from price_history "
            f"where bar_date >= :start and ticker in ({slots}) order by ticker,bar_date",
            start=start, **params) or []
        for ticker, bar_date, high, low, close, volume in raw:
            by_ticker[ticker].append((bar_date, high, low, close, volume))
    return by_ticker


def refresh_daily(snapshot: dict) -> dict:
    """Incrementally refresh current funnels plus all unresolved lifecycle rows from price_history."""
    from db_pool import get_db
    db = get_db()
    try:
        ensure_schema(db)
        current = _snapshot_rows(snapshot)
        current_changed = store(db, current, update_existing=True)
        active = db.run(
            "select id,ticker,hvf_type,entry_level,stop_level,target_level,h3_date,l3_date,ready_date,"
            "triggered_date,outcome from squeeze_history where outcome is null or outcome in ('OPEN','NEVER_TRIGGERED')") or []
        tickers = sorted({row[1] for row in active if row[1]})
        start = (dt.date.today() - dt.timedelta(days=18 * 31)).isoformat()
        bars_by_ticker = _price_bars(db, tickers, start) if tickers else {}
        refreshed = 0
        data_through = None
        for row_id, ticker, hvf_type, entry, stop, target, h3d, l3d, ready_date, triggered_date, _outcome in active:
            bars = bars_by_ticker.get(ticker) or []
            if not bars or None in (entry, stop, target):
                continue
            last_bar = str(bars[-1][0])[:10]
            data_through = max(data_through or last_bar, last_bar)
            pivot_dates = [v for v in (ready_date, h3d, l3d) if v]
            ready = max(pivot_dates) if pivot_dates else None
            ready = dt.date.fromisoformat(str(ready)[:10]) if ready else None
            td = triggered_date
            if td is None:
                td = _trigger_date(hvf_type == "BULLISH", float(entry), ready, bars)
            rvol = None
            if td is None:
                outcome, outcome_date, return_pct = "NEVER_TRIGGERED", None, None
            else:
                idx = next((i for i, bar in enumerate(bars) if str(bar[0])[:10] == str(td)[:10]), None)
                if idx is None:
                    continue
                outcome, outcome_date, return_pct = _exit_outcome(
                    hvf_type == "BULLISH", float(entry), float(stop), float(target), bars[idx + 1:])
                rvol = _rvol_at(bars, td)
            db.run("update squeeze_history set triggered_date=:td,outcome=:outcome,outcome_date=:od,"
                   "return_pct=:ret,rvol=coalesce(:rvol,rvol),refreshed_at=now() where id=:id",
                   td=td, outcome=outcome, od=outcome_date,
                   ret=(round(return_pct, 2) if return_pct is not None else None), rvol=rvol, id=row_id)
            refreshed += 1
        return {"current_funnels": len(current), "current_upserts": current_changed,
                "active_refreshed": refreshed, "data_through": data_through}
    finally:
        db.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Replay the HVF engine over price history into squeeze_history.")
    ap.add_argument("--months", type=int, default=15)
    ap.add_argument("--tickers", type=str, help="comma-separated subset (default: whole universe)")
    a = ap.parse_args()

    from price_audit import _universe
    from db_pool import get_db
    uni = _universe()
    tickers = [t.strip() for t in a.tickers.split(",")] if a.tickers else list(uni)

    # market per ticker, from the scan universe
    from run_hvf_report import UNIVERSE
    mkt = {t: m for m, ts in UNIVERSE.items() for t in ts}

    db = get_db()
    try:
        ensure_schema(db)
    finally:
        db.close()

    t0 = dt.datetime.now()
    total_f = total_new = 0
    for i, tk in enumerate(tickers, 1):
        try:
            rows = replay_ticker(tk, mkt.get(tk), a.months)
        except Exception as e:
            log.warning(f"{tk}: replay failed: {e}")
            continue
        if rows:
            db = get_db()
            try:
                total_new += store(db, rows)
            finally:
                db.close()
            total_f += len(rows)
        if i % 25 == 0 or i == len(tickers):
            el = (dt.datetime.now() - t0).total_seconds()
            rate = i / el if el else 0
            log.info(f"  {i}/{len(tickers)} — {total_f} funnels found, {total_new} new — "
                     f"{el/60:.1f}m elapsed, ~{(len(tickers)-i)/rate/60:.0f}m left" if rate else "")
    log.info(f"Squeeze history replay done: {len(tickers)} instruments, {total_f} funnels, "
             f"{total_new} new, {(dt.datetime.now()-t0).total_seconds()/60:.1f} minutes")


if __name__ == "__main__":
    main()
