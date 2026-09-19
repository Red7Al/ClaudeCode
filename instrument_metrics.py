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
           "mcap", "direction", "status")

# The 52-week range must use the SAME window as hvf_web/server.py::_snapshot_52wk
# (_WK52_LOOKBACK_DAYS = 365). A stored figure that disagreed with the displayed one would be
# worse than not storing it, so the window is pinned here and asserted in the tests.
WK52_LOOKBACK_DAYS = 365


def ensure_schema(db):
    db.run(f"""create table if not exists {TABLE} (
                  ticker            text not null,
                  as_of             date not null,
                  bar_date          date not null,
                  rvol              double precision,
                  rvol_date         date,
                  above_vwap        boolean,
                  above_vwap_setup  boolean,
                  atr_expanding     boolean,
                  volume_score      integer,
                  volume_score_max  integer,
                  wk52_low          double precision,
                  wk52_high         double precision,
                  mcap              double precision,
                  direction         text,
                  status            text,
                  source            text,
                  recorded_at       timestamptz default now(),
                  -- KEYED ON THE BAR, NOT ON WHEN WE LOOKED (2026-09-19). These four measures describe a
                  -- BAR; as_of is only when the row was computed. Keying on as_of meant one capture wrote
                  -- rows describing many different bars -- measured on as_of 2026-09-18, ten of them,
                  -- with only TWO describing the 18th -- so order_filter_audit, which looks a position's
                  -- opening bar up exactly, found nothing for 1,770 of 1,772 instruments and reported
                  -- "RVOL not recorded". It also left 24% of the table as repeat captures of a bar
                  -- already described. as_of stays as information, not identity.
                  primary key (ticker, bar_date))""")
    # `create table if not exists` does NOTHING when the table already exists, so a column added later
    # never appears and every read of it fails with "column does not exist" -- which is exactly what
    # happened when mcap was added on 2026-08-29. Adding them explicitly keeps this idempotent for both
    # a fresh database and an existing one.
    for column, ddl in (("above_vwap_setup", "boolean"), ("volume_score", "integer"),
                        ("volume_score_max", "integer"), ("wk52_low", "double precision"),
                        ("wk52_high", "double precision"), ("mcap", "double precision"),
                        ("rvol_date", "date"), ("direction", "text"), ("status", "text"),
                        ("source", "text")):
        try:
            db.run(f"alter table {TABLE} add column if not exists {column} {ddl}")
        except Exception as e:                      # a locked-down role must not break the whole read
            log.debug("could not ensure column %s: %s", column, e)


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


def bars_upto(ticker, end, db):
    """price_history up to and including `end`, as the tuples volume_score expects. NUMPY-FREE.

    _bars() above goes through price_store, which imports pandas at module level -- and pandas imports
    numpy, which is SIGSYS-killed on the IONOS host. This reads the same table with the same window
    directly, so record_for_bar can run inside a web request on that host. Same shape, same order, same
    NaN-volume-to-zero rule as _bars, because the two must not disagree about what a bar is.
    """
    start = (end if isinstance(end, dt.date) else dt.date.fromisoformat(str(end)[:10])) \
        - dt.timedelta(days=LOOKBACK_DAYS)
    rows = db.run("select bar_date, high, low, close, volume from price_history "
                  "where ticker = :t and bar_date >= :s and bar_date <= :e "
                  "order by bar_date", t=ticker, s=str(start), e=str(end)[:10]) or []
    out = []
    for bar_date, high, low, close, volume in rows:
        if high is None or low is None or close is None:
            continue                      # an incomplete bar is not a bar
        out.append((str(bar_date)[:10], float(high), float(low), float(close),
                    float(volume) if volume is not None else 0.0))
    return out


def record_for_bar(ticker, bar_date, direction=None, db=None):
    """Compute and STORE this instrument's metrics for ONE specific bar; return them, or None.

    WHY THIS EXISTS (owner 2026-09-19: "RVOL not recorded ... this has been covered so many times").
    record_daily captures each instrument's LATEST completed bar, and it runs at ~03:30 UTC, so one
    capture writes rows describing many different bar_dates -- measured on as_of 2026-09-18, ten of them,
    with only TWO describing the 18th itself. order_filter_audit.break_state looks up the position's
    opening bar EXACTLY, so a position opened that day was judgeable for 2 of 1,772 instruments. The data
    was never missing; the row for that particular bar had simply never been written.

    Storing rather than returning a computed value is the owner's standing rule -- "it should not need to
    be recomputed - it should be stored" -- so this writes the row it just worked out.

    RETURNS None WHEN THE INSTRUMENT DID NOT TRADE THAT DAY. That is not a failure: there is no break bar,
    so "not recorded" is the honest answer and the caller must keep treating it as unjudgeable rather than
    as a pass.
    """
    from db_pool import get_db
    want = str(bar_date)[:10]
    own = db is None
    db = db or get_db()
    try:
        ensure_schema(db)
        m = compute(ticker, bars_upto(ticker, want, db), direction)
        if m.get("status") == "no_price_history" or str(m.get("bar_date") or "")[:10] != want:
            return None          # no bar on that date -- genuinely unjudgeable
        # A PLAIN upsert on the bar. An earlier version of this needed a conditional
        # "...do update ... where bar_date = :bd" to avoid clobbering a row that described a DIFFERENT
        # bar under the same (ticker, as_of) key. Re-keying the table on (ticker, bar_date) removed the
        # collision and with it the workaround -- the identity is now the thing being written.
        db.run(f"""insert into {TABLE}
                     (ticker, as_of, bar_date, rvol, rvol_date, above_vwap, above_vwap_setup,
                      atr_expanding, volume_score, volume_score_max, wk52_low, wk52_high,
                      direction, status, source, recorded_at)
                   values (:t,:d,:bd,:rv,:rd,:av,:avs,:atr,:vs,:vsm,:lo,:hi,:dir,:st,'backfill', now())
                   on conflict (ticker, bar_date) do update set
                     as_of=:d, rvol=:rv, rvol_date=:rd, above_vwap=:av, above_vwap_setup=:avs,
                     atr_expanding=:atr, volume_score=:vs, volume_score_max=:vsm, wk52_low=:lo,
                     wk52_high=:hi, direction=:dir, status=:st, source='backfill', recorded_at=now()""",
               t=ticker, d=want, bd=m.get("bar_date"), rv=m.get("rvol"), rd=m.get("rvol_date"),
               av=m.get("above_vwap"), avs=m.get("above_vwap_setup"), atr=m.get("atr_expanding"),
               vs=m.get("volume_score"), vsm=m.get("volume_score_max"), lo=m.get("wk52_low"),
               hi=m.get("wk52_high"), dir=m.get("direction"), st=m.get("status"))
        return m
    except Exception as exc:
        log.warning("metrics backfill failed for %s on %s: %s", ticker, want, exc)
        return None
    finally:
        if own:
            db.close()


# Market cap is deliberately NOT captured here (user 2026-08-29: "we do not need mcap every day"). It
# moves slowly and is only used for wide bands (<2bn / 2-10bn / 10-100bn / 100bn+), so daily resolution
# would be storage for no gain on a 500 MB tier. The column remains for a future weekly writer.
#
# CONSEQUENCE, recorded rather than left implicit: trigger-date market cap stays UNANSWERABLE. Market cap
# lives only in instrument_mcap, which is one row per ticker that the weekly backfill OVERWRITES, so no
# history has ever existed. Giving it an as_of key on that weekly job -- not here -- is what would make
# "did this setup meet the MCap band when it fired" answerable. Backlogged.


def record_daily(snapshot, as_of=None, db=None, tickers=None):
    """Compute and UPSERT today's metrics for every instrument in the snapshot.

    Idempotent on (ticker, BAR_DATE), so re-capturing a bar updates that bar in place. It used to be keyed
    on as_of, which meant a second capture on a day the bar had not advanced wrote a SECOND row describing
    the same bar -- 24% of the table by 2026-09-19 -- while a bar captured under a later as_of could not
    be found by anything looking the bar up.
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
                # mcap is NOT written here -- see the note above: it moves slowly and daily resolution
                # would be storage for no gain. It was in this statement as `:mc` with no matching
                # argument, so EVERY insert raised "no matching keyword argument" and the table took
                # 1,772 failures a day while the job reported success. Removed rather than supplied,
                # because not capturing it is the decision on record.
                db.run(f"""insert into {TABLE}
                             (ticker, as_of, bar_date, rvol, rvol_date, above_vwap, above_vwap_setup,
                              atr_expanding, volume_score, volume_score_max, wk52_low, wk52_high,
                              direction, status, recorded_at)
                           values (:t,:d,:bd,:rv,:rd,:av,:avs,:atr,:vs,:vsm,:lo,:hi,:dir,:st, now())
                           on conflict (ticker, bar_date) do update set
                             as_of=:d, rvol=:rv, rvol_date=:rd, above_vwap=:av,
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
                # The FIRST failure is surfaced at WARNING with its reason. It used to be debug-only, so
                # a defect that failed every instrument looked identical to no defect at all: this job
                # wrote nothing for six days while its scheduled run reported success, because a
                # placeholder in the insert had no matching argument. One instrument failing is noise;
                # the first one failing is the only cheap signal that all of them are about to.
                if summary["failed"] == 1:
                    summary["first_error"] = f"{ticker}: {e}"
                    log.warning("metrics failed for %s: %s (further failures at debug)", ticker, e)
                else:
                    log.debug("metrics failed for %s: %s", ticker, e)
    except Exception as e:
        log.warning("daily instrument metrics failed: %s", e)
    finally:
        if own and db is not None:
            try:
                db.close()
            except Exception:
                pass
    # A TOTAL failure is not a per-instrument hiccup and must not read like one. Storing nothing while
    # attempting thousands is a defect in the writer every time, and the old code reported it at INFO in
    # the same shape as a healthy run -- which is how six days of empty capture went unnoticed behind a
    # green workflow. Callers key their own alerting off this level, not off the return value.
    if summary["attempted"] and not summary["stored"]:
        log.error("instrument metrics STORED NOTHING for %s: %s of %s failed, first error: %s",
                  as_of, summary["failed"], summary["attempted"],
                  summary.get("first_error") or "none recorded")
    else:
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
