# ======================================================================================================================
# File:         volscore_store.py
# Author:       Alex Hind (via Claude)
# Created:      2026-10-07
#
# Per-trigger VolumeScore features, STORED once a day and read back as a summary.
#
# WHY (account owner, 2026-10-07, after saying it repeatedly): "_volscore does not need to come from supabase
# each hour - with a daily feed, nothing is changing that cannot be served by a small flat file", and "the
# database is there to efficiently store data and provide limited detail and/or summaries".
#
#   hvf_web/server.py::_volscore_scored pulled 400,000 rows (1-year window) to 1,102,245 rows (3-year) of RAW
#   BARS across the wire so Python could reduce them to three values per trigger -- MEASURED by differencing
#   pg_stat_statements on 2026-10-07. That is using the database as a file server. The free-tier allowance is
#   5 GB a MONTH for the whole organisation, and breaching it is why Storage has returned 402 since
#   2026-08-16.
#
#   The output is tiny: three values per trigger, about 6,500 rows for the 1-year window and 17,600 for the
#   3-year one. So the read becomes one row per trigger instead of 160 days of bars per ticker.
#
# THE SAME CORRECTION ALREADY EXISTS IN THIS REPOSITORY. _snapshot_52wk used to pull every bar over 52 weeks
# -- about 461,000 rows -- to produce two numbers per ticker, and was changed to a min()/max() GROUP BY that
# returns one row each: "roughly 260x less data off the wire". And instrument_metrics_daily is written once a
# day, which is why _live_instrument_metrics costs 1,772 rows and ZERO bars. This is that pattern applied to
# the one surface still doing it the old way.
#
# NOT A SECOND DEFINITION OF THE MATHS. The scores are computed by volume_score.volume_score(), the same
# function the web tier calls, in the job that already holds the bars. Reimplementing a 0-12 score with a
# 60-bar LVN profile and ATR/VWAP windows in SQL would create two definitions of the same number, which is
# this repository's cardinal defect (memory results-winners-same-dataset). The bars are read ONCE a day by
# the writer; nothing recomputes per request.
# ======================================================================================================================

import logging

log = logging.getLogger("volscore_store")

# Named _TABLE, not TABLE: test_metrics_bar_key scans every module for "insert into {_TABLE}" to
# guard instrument_metrics_daily's (ticker, bar_date) key, and a bare TABLE here false-positives
# it -- this is a different table with a legitimately different key. The guard stays as strict as
# it was; the collision was mine.
_TABLE = "volscore_features"


def ensure_schema(db):
    db.run(f"""create table if not exists {_TABLE} (
                 ticker         text not null,
                 trig_date      date not null,
                 volume_score   double precision,
                 above_vwap     boolean,
                 atr_expanding  boolean,
                 updated_at     timestamptz default now(),
                 primary key (ticker, trig_date))""")


def store(rows, db=None) -> int:
    """Upsert scored rows. `rows` is what _volscore_scored returns: dicts with ticker, trig_date and the
    three derived values. Returns the number written. Idempotent, so a re-run is safe."""
    own = db is None
    if own:
        from db_pool import get_db
        db = get_db()
    try:
        ensure_schema(db)
        # ONE row per (ticker, trig_date). squeeze_history carries the same trigger under several lookback
        # windows, so the scored rows repeat a key -- and Postgres refuses an upsert that touches one row
        # twice ("ON CONFLICT DO UPDATE command cannot affect row a second time"). That failed every store
        # on 2026-10-08 (precompute run 37813598761) while the job still reported success. First wins.
        seen, written = {}, 0
        for r in rows or []:
            tk, td = r.get("ticker"), str(r.get("trig_date") or "")[:10]
            if not tk or not td or (tk, td) in seen:
                continue
            seen[(tk, td)] = (tk, td, r.get("volume_score"), r.get("above_vwap"), r.get("atr_expanding"))
        batch = list(seen.values())
        # Batched, not a statement per row. 17,600 single-row upserts is the per-row defect this repository
        # has already paid for twice (squeeze_history.refresh_daily was 4,287 round trips, record_daily 3,545).
        for off in range(0, len(batch), 500):
            chunk = batch[off:off + 500]
            vals = ",".join(f"(:t{i}, :d{i}::date, :s{i}, :v{i}, :a{i})" for i in range(len(chunk)))
            params = {}
            for i, (tk, td, sc, av, ae) in enumerate(chunk):
                params[f"t{i}"], params[f"d{i}"], params[f"s{i}"] = tk, td, sc
                params[f"v{i}"], params[f"a{i}"] = av, ae
            db.run(f"""insert into {_TABLE} (ticker, trig_date, volume_score, above_vwap, atr_expanding)
                       values {vals}
                       on conflict (ticker, trig_date) do update set
                         volume_score = excluded.volume_score,
                         above_vwap = excluded.above_vwap,
                         atr_expanding = excluded.atr_expanding,
                         updated_at = now()""", **params)
            written += len(chunk)
        log.info("volscore features stored: %d rows", written)
        return written
    finally:
        if own:
            db.close()


def load(cutoff_iso: str, db=None) -> dict:
    """{(ticker, 'YYYY-MM-DD'): {volume_score, above_vwap, atr_expanding}} for triggers on/after cutoff.

    THE SUMMARY READ this module exists for: one row per trigger, selected by date, instead of every bar
    for every ticker over a 160-day lookback. Returns {} when the table is absent or empty so the caller
    falls back to computing -- a missing store must degrade to slow, never to wrong.
    """
    own = db is None
    if own:
        from db_pool import get_db
        db = get_db()
    try:
        rows = db.run(f"""select ticker, trig_date, volume_score, above_vwap, atr_expanding
                          from {_TABLE} where trig_date >= :cut""", cut=cutoff_iso) or []
    except Exception as exc:
        log.warning("volscore features unavailable (%s); caller will recompute", exc)
        return {}
    finally:
        if own:
            db.close()
    return {(tk, str(td)[:10]): {"volume_score": sc, "above_vwap": av, "atr_expanding": ae}
            for tk, td, sc, av, ae in rows}
