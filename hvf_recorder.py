# ======================================================================================================================
# File:         hvf_recorder.py
# Author:       Alex Hind
# Created:      2026-06-30
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Records every TRIGGERED HVF setup to Supabase (user 2026-06-30, Application Focus - HVF status): the full pattern —
# H1/H2/H3 + L1/L2/L3 levels AND dates, quality, entry/stop/target, R:R, timeframe, market, trend, live price at
# detection — plus the complete raw engine dict (JSONB) so nothing is lost. Purpose: measure how these tips perform
# (join hvf_triggers x price_history — the golden daily OHLC store — to score outcome vs target/stop over time).
#
# One row per FUNNEL INSTANCE: unique on (ticker, timeframe, h3_date, l3_date) with ON CONFLICT DO NOTHING, so the
# 12-hourly snapshot builds and daily report can all call record_triggers() freely — re-seeing the same trigger is a
# no-op; a NEW funnel (pivots moved) records a new row.
#
# Called from: hvf_web/build_snapshot.build() (12h + manual refresh, full 600-instrument universe) and
# run_hvf_report.main() (daily Actions report). Both wrapped so a recording failure never breaks the caller.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-30  Alex Hind   Initial build — hvf_triggers table, dedup on funnel instance, raw JSONB sidecar.
# ======================================================================================================================

import json
import logging

log = logging.getLogger("hvf_recorder")

_DDL = [
    """create table if not exists hvf_triggers (
        id            bigserial primary key,
        recorded_at   timestamptz not null default now(),
        ticker        text        not null,
        market        text,
        hvf_type      text,
        timeframe     text,
        quality       double precision,
        risk_reward   double precision,
        entry_level   double precision,
        stop_level    double precision,
        target_level  double precision,
        current_price double precision,
        h1_level double precision, h2_level double precision, h3_level double precision,
        l1_level double precision, l2_level double precision, l3_level double precision,
        h1_date date, h2_date date, h3_date date, l1_date date, l2_date date, l3_date date,
        long_trend    text,
        source        text,
        raw           jsonb
    )""",
    # One row per funnel instance — re-recording the same trigger is a no-op.
    """create unique index if not exists uq_hvf_triggers_instance
       on hvf_triggers (ticker, coalesce(timeframe,''), coalesce(h3_date,'1900-01-01'), coalesce(l3_date,'1900-01-01'))""",
    "create index if not exists idx_hvf_triggers_ticker on hvf_triggers (ticker, recorded_at desc)",
]

_schema_ready = False


def _ensure_schema(db):
    global _schema_ready
    if _schema_ready:
        return
    for stmt in _DDL:
        db.run(stmt)
    _schema_ready = True


def _d(v):
    """Date-ish -> 'YYYY-MM-DD' or None."""
    return str(v)[:10] if v else None


def _f(v):
    return float(v) if isinstance(v, (int, float)) else None


def record_triggers(engine_rows: list, source: str) -> int:
    """Record every TRIGGERED row from a scan (raw engine dicts with hvf_* / *_level / *_date keys).
    Returns the number of NEW funnel instances stored. Never raises."""
    rows = [r for r in (engine_rows or []) if r.get("hvf_signal") == "TRIGGERED"]
    if not rows:
        return 0
    new = 0
    try:
        from db_pool import get_db
        db = get_db()
        try:
            _ensure_schema(db)
            for r in rows:
                res = db.run(
                    """insert into hvf_triggers
                       (ticker, market, hvf_type, timeframe, quality, risk_reward,
                        entry_level, stop_level, target_level, current_price,
                        h1_level, h2_level, h3_level, l1_level, l2_level, l3_level,
                        h1_date, h2_date, h3_date, l1_date, l2_date, l3_date,
                        long_trend, source, raw)
                       values (:tk,:mk,:ht,:tf,:q,:rr,:e,:s,:t,:cp,
                               :h1,:h2,:h3,:l1,:l2,:l3,
                               :h1d,:h2d,:h3d,:l1d,:l2d,:l3d,:tr,:src,:raw)
                       on conflict (ticker, coalesce(timeframe,''), coalesce(h3_date,'1900-01-01'),
                                    coalesce(l3_date,'1900-01-01')) do nothing
                       returning id""",
                    tk=r.get("ticker"), mk=r.get("index"), ht=r.get("hvf_type"),
                    tf=r.get("hvf_timeframe"), q=_f(r.get("pattern_quality")),
                    rr=_f(r.get("risk_reward")), e=_f(r.get("h3_level")), s=_f(r.get("stop_level")),
                    t=_f(r.get("target")), cp=_f(r.get("current_price")),
                    h1=_f(r.get("h1_level")), h2=_f(r.get("h2_level")), h3=_f(r.get("h3_level")),
                    l1=_f(r.get("l1_level")), l2=_f(r.get("l2_level")), l3=_f(r.get("l3_level")),
                    h1d=_d(r.get("h1_date")), h2d=_d(r.get("h2_date")), h3d=_d(r.get("h3_date")),
                    l1d=_d(r.get("l1_date")), l2d=_d(r.get("l2_date")), l3d=_d(r.get("l3_date")),
                    tr=r.get("long_trend"), src=source,
                    raw=json.dumps(r, default=str))
                if res:
                    new += 1
        finally:
            db.close()
    except Exception as e:
        log.warning(f"hvf_triggers recording failed ({source}): {e}")
        return new
    if new:
        log.info(f"hvf_triggers: {new} new funnel instance(s) recorded ({source}, {len(rows)} triggered seen)")
    return new
