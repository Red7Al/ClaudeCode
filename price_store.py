# ======================================================================================================================
# File:         price_store.py
# Author:       Alex Hind
# Created:      2026-06-29
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Supabase (Postgres) store for daily OHLCV price history — the "golden" price dataset (user 2026-06-29). One row per
# (ticker, bar_date); every row records its source (YF / IG / ...) and recorded_at / updated_at timestamps. Built for
# fast read+write: a (ticker, bar_date) primary key serves both the single-instrument range scans the charts need and
# the per-day cross-instrument scans the daily audit needs; bulk multi-row UPSERTs keep writes cheap.
#
# Public API:
#   ensure_schema(db=None)                       create table + indexes (idempotent)
#   upsert_bars(ticker, df, source, db=None)     bulk UPSERT a yfinance-shaped OHLCV frame; returns rows written
#   get_bars(ticker, start, end, db=None)        read OHLCV as a yfinance-shaped DataFrame (or None)
#   get_bars_or_fetch(ticker, yahoo_symbol, ...) Supabase first; on miss/stale, fetch YF + write-through
#   latest_bar_date(ticker, db=None)             most recent stored bar_date (or None)
#
# Environment: SUPABASE_USER, SUPABASE_DB_PASSWORD (via db_pool).
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-06-29  Alex Hind   Initial build — golden price_history store, bulk upsert, read, read-or-fetch helper.
# ======================================================================================================================

import logging
from datetime import date, timedelta

import pandas as pd

from db_pool import get_db

log = logging.getLogger("price_store")

_DDL = [
    """create table if not exists price_history (
        ticker      text             not null,
        bar_date    date             not null,
        open        double precision,
        high        double precision,
        low         double precision,
        close       double precision,
        volume      bigint,
        source      text             not null,
        recorded_at timestamptz      not null default now(),
        updated_at  timestamptz      not null default now(),
        primary key (ticker, bar_date)
    )""",
    # (ticker, bar_date desc) — fast "latest N bars for one instrument" + range scans for the charts.
    "create index if not exists idx_price_history_ticker_date on price_history (ticker, bar_date desc)",
    # (bar_date) — the daily audit scans every instrument for a given day.
    "create index if not exists idx_price_history_bar_date on price_history (bar_date)",
]

_OHLCV = ("Open", "High", "Low", "Close", "Volume")
_schema_ready = False


def ensure_schema(db=None):
    """Create the table + indexes if absent. Idempotent; runs the DDL only once per process."""
    global _schema_ready
    if _schema_ready:
        return
    own = db is None
    if own:
        db = get_db()
    try:
        for stmt in _DDL:
            db.run(stmt)
        _schema_ready = True
    finally:
        if own:
            db.close()


def _df_to_rows(df):
    """yfinance OHLCV frame -> list of (bar_date, o, h, l, c, volume), skipping rows with no close."""
    if df is None or len(df) == 0:
        return []
    d = df.copy()
    if getattr(d.columns, "nlevels", 1) > 1:        # single-ticker yfinance frames carry MultiIndex cols
        d.columns = d.columns.get_level_values(0)
    rows = []
    for idx, row in d.iterrows():
        try:
            bd = idx.date() if hasattr(idx, "date") else pd.to_datetime(idx).date()
            c = row.get("Close")
            if c is None or pd.isna(c):
                continue

            def g(k):
                v = row.get(k)
                return None if (v is None or pd.isna(v)) else float(v)

            vol = row.get("Volume")
            vol = None if (vol is None or pd.isna(vol)) else int(vol)
            rows.append((bd, g("Open"), g("High"), g("Low"), float(c), vol))
        except Exception:
            continue
    return rows


def upsert_bars(ticker, df, source, db=None, chunk=400):
    """Bulk-UPSERT a yfinance OHLCV frame for one ticker. ON CONFLICT updates the bar + source +
    updated_at (so an audit correction overwrites a stale value). Returns the number of rows written."""
    rows = _df_to_rows(df)
    if not rows:
        return 0
    own = db is None
    if own:
        db = get_db()
    written = 0
    try:
        ensure_schema(db)
        for i in range(0, len(rows), chunk):
            batch = rows[i:i + chunk]
            values, params = [], {"t": ticker, "s": source}
            for j, (bd, o, h, l, c, v) in enumerate(batch):
                values.append(f"(:t,:d{j},:o{j},:h{j},:l{j},:c{j},:v{j},:s)")
                params.update({f"d{j}": bd, f"o{j}": o, f"h{j}": h, f"l{j}": l, f"c{j}": c, f"v{j}": v})
            sql = ("insert into price_history "
                   "(ticker,bar_date,open,high,low,close,volume,source) values " + ",".join(values) +
                   " on conflict (ticker,bar_date) do update set "
                   "open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, "
                   "volume=excluded.volume, source=excluded.source, updated_at=now()")
            db.run(sql, **params)
            written += len(batch)
    finally:
        if own:
            db.close()
    return written


def get_bars(ticker, start=None, end=None, db=None):
    """Read OHLCV for a ticker as a yfinance-shaped DataFrame (DatetimeIndex + Open/High/Low/Close/Volume),
    or None when nothing is stored. start/end are date or 'YYYY-MM-DD'."""
    own = db is None
    if own:
        db = get_db()
    try:
        ensure_schema(db)
        sql = "select bar_date,open,high,low,close,volume from price_history where ticker=:t"
        params = {"t": ticker}
        if start is not None:
            sql += " and bar_date>=:start"
            params["start"] = str(start)
        if end is not None:
            sql += " and bar_date<=:end"
            params["end"] = str(end)
        sql += " order by bar_date"
        rows = db.run(sql, **params) or []
    finally:
        if own:
            db.close()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["bar_date", "Open", "High", "Low", "Close", "Volume"])
    df["bar_date"] = pd.to_datetime(df["bar_date"])
    return df.set_index("bar_date").astype({c: "float64" for c in ("Open", "High", "Low", "Close")})


def latest_bar_date(ticker, db=None):
    own = db is None
    if own:
        db = get_db()
    try:
        ensure_schema(db)
        r = db.run("select max(bar_date) from price_history where ticker=:t", t=ticker)
    finally:
        if own:
            db.close()
    return r[0][0] if (r and r[0]) else None


def get_bars_or_fetch(ticker, yahoo_symbol, start, end, source="YF", stale_days=5):
    """Supabase first (the golden store); fall back to a yfinance download — and write it through — when
    the stored series is missing, too short, or stale (last bar older than `stale_days` before `end`).
    Returns a yfinance-shaped DataFrame, or None if both Supabase and YF come back empty. Never raises."""
    end_d = end.date() if hasattr(end, "date") else pd.to_datetime(end).date()
    start_d = start.date() if hasattr(start, "date") else pd.to_datetime(start).date()

    stored = None
    try:
        stored = get_bars(ticker, start_d, end_d)
    except Exception as e:
        log.warning(f"price_store read failed for {ticker}: {e}")

    fresh_enough = (stored is not None and not stored.empty
                    and stored.index.max().date() >= (end_d - timedelta(days=stale_days)))
    if fresh_enough:
        return stored

    # Stale / missing -> refresh from yfinance and write through to the golden store.
    try:
        import yfinance as yf
        dl = yf.download(yahoo_symbol, start=start_d.strftime("%Y-%m-%d"),
                         end=end_d.strftime("%Y-%m-%d"), progress=False, auto_adjust=True)
        if dl is not None and not dl.empty:
            try:
                upsert_bars(ticker, dl, source)
            except Exception as e:
                log.warning(f"price_store write-through failed for {ticker}: {e}")
            if getattr(dl.columns, "nlevels", 1) > 1:
                dl = dl.copy()
                dl.columns = dl.columns.get_level_values(0)
            return dl
    except Exception as e:
        log.warning(f"yfinance fetch failed for {ticker}: {e}")

    return stored   # better a slightly-stale stored series than nothing
