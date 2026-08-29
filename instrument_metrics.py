# ======================================================================================================================
# File:         instrument_metrics.py
# Created:      2026-08-29
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# A DAILY, PERSISTED record of each instrument's RVOL / VWAP / ATR state (user 2026-08-29: "above_vwap rvol should be
# recorded every day - if this is not the case then please implement").
#
# WHY THIS EXISTS. These metrics were computed but never kept. _live_instrument_metrics and _snapshot_rvol derive them
# from price_history, cache them in memory against the snapshot's generated_utc, and discard them. A schema query on
# 2026-08-29 confirmed the only persisted column among above_vwap / rvol / atr_expanding / volume_score anywhere in the
# database was squeeze_history.rvol, which is per-funnel at trigger time rather than a daily per-instrument record.
#
# What that cost, measured the same day: the owner's require_above_vwap trading filter is ON, and above_vwap was None on
# all 55 PENDING working orders, so no order placed on an earlier day could be checked against that filter at all. It is
# also the root cause behind "Rows still has empty data e.g. RVOL".
#
# TWO VWAP SEMANTICS, both recorded, because the codebase legitimately has both and storing one would guarantee a
# mismatch with whichever screen used the other:
#   * above_vwap        - the LITERAL instrument metric, always computed with bull=True. This is what the Instruments
#                         tab shows; hvf_web/server.py says explicitly that "a BEAR row must not invert it".
#   * above_vwap_setup  - the DIRECTION-AWARE setup confirmation, which is what a BEAR setup's own confirmation means
#                         and what the trading filters are expressed against. `direction` records which way it was run.
#
# The maths is NOT reimplemented here. volume_score._rvol_at / _above_vwap / _atr_expanding are called directly, so
# there is exactly one definition of each in the project, and test_instrument_metrics asserts this module agrees with
# hvf_web/server.py's live path on identical bars.
# ======================================================================================================================

import datetime as dt
import logging

log = logging.getLogger("instrument_metrics")

TABLE = "instrument_metrics_daily"

# Enough history for the longest window any metric needs (ATR uses 2 x ATR_PERIOD bars) plus weekends/holidays.
LOOKBACK_DAYS = 400

# Single source for the stored columns: the SELECT in latest() is built from this, so the query
# and the names it is zipped against can never disagree.
COLUMNS = ("ticker", "as_of", "bar_date", "rvol", "rvol_date", "above_vwap", "above_vwap_setup",
           "atr_expanding", "volume_score", "volume_score_max", "wk52_low", "wk52_high",
           "direction", "status")

# The 52-week range must use the SAME window as hvf_web/server.py::_snapshot_52wk
# (_WK52_LOOKBACK_DAYS = 365). A stored figure that disagreed with the displayed one would be
# worse than not storing it, so the window is pinned here and asserted in the tests.
WK52_LOOKBACK_DAYS = 365


def ensure_schema(db):
    db.run(f"""create table if not exists {TABLE} (
                  ticker            text not null,
                  as_of             date not null,
                  bar_date          date,
                  rvol              double precision,
                  rvol_date         date,
                  above_vwap        boolean,
                  above_vwap_setup  boolean,
                  atr_expanding     boolean,
                  volume_score      integer,
                  volume_score_max  integer,
                  wk52_low          double precision,
                  wk52_high         double precision,
                  direction         text,
                  status            text,
                  source            text,
                  recorded_at       timestamptz default now(),
                  primary key (ticker, as_of))""")


def _bars(ticker, end, db):
    """price_history as the (date, high, low, close, volume) tuples volume_score expects."""
    import price_store
    df = price_store.get_bars(ticker, end - dt.timedelta(days=LOOKBACK_DAYS), end, db=db)
    if df is None or getattr(df, "empty", True):
        return []
    out = []
    for idx, row in df.iterrows():
        vol = row.get("Volume")
        out.append((idx.date().isoformat(), float(row["High"]), float(row["Low"]), float(row["Close"]),
                    float(vol) if vol == vol and vol is not None else 0.0))     # NaN -> 0, matching the live path
    return out


def compute(ticker, bars, direction=None):
    """One instrument's metrics from its bars. Mirrors hvf_web/server.py::_live_instrument_metrics."""
    import volume_score as _vs
    if not bars:
        return {"ticker": ticker, "status": "no_price_history"}
    i = len(bars) - 1
    # Some otherwise complete daily bars carry a close but no volume. RVOL is a volume measure, so fall back to the
    # newest bar that actually reports volume rather than blanking the instrument -- and keep that bar's date, so a
    # reader can see price metrics are current to a later close than RVOL is.
    rvol_i = next((k for k in range(i, -1, -1) if bars[k][4] and _vs._rvol_at(bars, k) is not None), None)
    rvol = _vs._rvol_at(bars, rvol_i) if rvol_i is not None else None
    has_volume = any(b[4] for b in bars)
    status = ("complete" if rvol is not None and rvol_i == i else
              ("complete_latest_volume_bar" if rvol is not None else
               ("no_reported_volume" if not has_volume else "insufficient_volume_history")))
    bull = str(direction or "").upper() != "BEAR"
    # VolumeScore as at the latest bar -- "what this instrument would score if it broke out today".
    # Computed here rather than on demand for the same reason as the rest: the inputs only change when a
    # new daily bar lands, so recomputing it per request is work repeated against unchanged data.
    vs_score = vs_max = None
    try:
        _v = _vs.volume_score(bars, bars[i][0], bull) or {}
        vs_score, vs_max = _v.get("score"), _v.get("max")
    except Exception:                                    # a scoring failure must not blank the rest
        pass
    # 52-week range, matching _snapshot_52wk: min low / max high over the trailing window. Another
    # metric derived from price_history that was recomputed for all ~1,773 instruments per snapshot and
    # persisted nowhere (user 2026-08-29: "processing on the fly does not make sense when the data set
    # changes so infrequently").
    cutoff = (dt.date.fromisoformat(str(bars[i][0])[:10]) - dt.timedelta(days=WK52_LOOKBACK_DAYS)).isoformat()
    window = [b for b in bars if str(b[0])[:10] >= cutoff]
    highs = [b[1] for b in window if b[1] is not None]
    lows = [b[2] for b in window if b[2] is not None]
    return {"ticker": ticker,
            "bar_date": str(bars[i][0])[:10],
            "wk52_low": (min(lows) if highs and lows else None),
            "wk52_high": (max(highs) if highs and lows else None),
            "rvol": rvol,
            "rvol_date": str(bars[rvol_i][0])[:10] if rvol_i is not None else None,
            "above_vwap": _vs._above_vwap(bars, i, True),          # literal instrument metric
            "above_vwap_setup": _vs._above_vwap(bars, i, bull),    # direction-aware confirmation
            "atr_expanding": _vs._atr_expanding(bars, i),
            "volume_score": vs_score,
            "volume_score_max": vs_max,
            "direction": (direction or None),
            "status": status}


def record_daily(snapshot, as_of=None, db=None, tickers=None):
    """Compute and UPSERT today's metrics for every instrument in the snapshot.

    Idempotent on (ticker, as_of), so a re-run overwrites the same day rather than duplicating it.
    Returns a summary; never raises, because this must not be able to cost a good scan its publication.
    """
    from db_pool import get_db
    as_of = as_of or dt.date.today()
    own = db is None
    summary = {"as_of": str(as_of), "attempted": 0, "stored": 0, "no_history": 0, "failed": 0}
    try:
        records = [r for r in (snapshot or {}).get("records", []) if r.get("ticker")]
        wanted = {r["ticker"]: (r.get("direction") or None) for r in records}
        if tickers:
            wanted = {t: wanted.get(t) for t in tickers}
        if not wanted:
            return summary
        if own:
            db = get_db()
        ensure_schema(db)
        for ticker, direction in wanted.items():
            summary["attempted"] += 1
            try:
                m = compute(ticker, _bars(ticker, as_of, db), direction)
                if m.get("status") == "no_price_history":
                    summary["no_history"] += 1
                    continue
                db.run(f"""insert into {TABLE}
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
                       dir=m.get("direction"), st=m.get("status"))
                summary["stored"] += 1
            except Exception as e:                       # one bad instrument must not lose the rest
                summary["failed"] += 1
                log.debug("metrics failed for %s: %s", ticker, e)
    except Exception as e:
        log.warning("daily instrument metrics failed: %s", e)
    finally:
        if own and db is not None:
            try:
                db.close()
            except Exception:
                pass
    log.info("instrument metrics %s: %s", as_of, summary)
    return summary


def latest(tickers, db=None):
    """{ticker: row} of the most recent stored metrics — what an order placed days ago can be judged against."""
    from db_pool import get_db
    own = db is None
    if own:
        db = get_db()
    try:
        ensure_schema(db)
        rows = db.run(f"select distinct on (ticker) {', '.join(COLUMNS)} "
                      f"from {TABLE} where ticker = any(:t) order by ticker, as_of desc",
                      t=list(tickers)) or []
    finally:
        if own:
            db.close()
    # zip() over a hand-written SELECT and a separate name tuple silently TRUNCATES when they disagree,
    # which on 2026-08-29 mapped `direction` onto the volume_score key and `status` onto its max -- wrong
    # values, confidently returned. One list now drives both, so they cannot drift.
    return {r[0]: dict(zip(COLUMNS, r)) for r in rows}


if __name__ == "__main__":                                # manual/backfill use
    import argparse
    import json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Record daily instrument metrics from the current snapshot.")
    ap.add_argument("--tickers", help="comma-separated subset (default: the whole snapshot)")
    ap.add_argument("--date", help="as-of date, YYYY-MM-DD (default: today)")
    a = ap.parse_args()
    import json as _json
    import os as _os
    from hvf_web import build_snapshot as _bs
    with open(_bs.SNAPSHOT, encoding="utf-8") as fh:      # the snapshot the site is serving
        snap = _json.load(fh)
    when = dt.date.fromisoformat(a.date) if a.date else None
    picked = [t.strip() for t in a.tickers.split(",")] if a.tickers else None
    print(_json.dumps(record_daily(snap, as_of=when, tickers=picked), indent=1))
