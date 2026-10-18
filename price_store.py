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
#   upsert_bars(ticker, df, source, db=None)     bulk UPSERT a yfinance-shaped OHLCV frame (Yahoo wicks verified
#                                                against hourly bars, corrections kept); returns rows written
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
        ticker         text             not null,
        bar_date       date             not null,
        open           double precision,
        high           double precision,
        low            double precision,
        close          double precision,
        volume         bigint,
        source         text             not null,
        double_checked boolean          not null default false,
        recorded_at    timestamptz      not null default now(),
        updated_at     timestamptz      not null default now(),
        primary key (ticker, bar_date)
    )""",
    # double_checked may be missing on a table created before 2026-06-29 — add it idempotently.
    "alter table price_history add column if not exists double_checked boolean not null default false",
    # No (ticker, bar_date desc) index: the primary key serves the same reads scanned backwards. Removed
    # 2026-10-18 (62 MB of a 500 MB database, owner: more instruments without the paid plan). MEASURED with the
    # index dropped inside a rolled-back transaction: every live read shape within a few ms of before except
    # server.py's `distinct on` source lookup (12x slower), which was rewritten first. This line used to
    # re-create it on every run, so dropping it alone would have been undone overnight.
    # (bar_date) — the daily audit scans every instrument for a given day.
    "create index if not exists idx_price_history_bar_date on price_history (bar_date)",
    # Every daily high/low _verify_wicks replaced, with the value Yahoo's daily bar carried (owner 2026-10-18:
    # originals are kept, never silently overwritten).
    """create table if not exists price_corrections (
        id           bigserial        primary key,
        ticker       text             not null,
        bar_date     date             not null,
        field        text             not null,
        original     double precision,
        corrected    double precision,
        evidence     text,
        recorded_at  timestamptz      not null default now()
    )""",
]

# A daily wick reaching this far beyond the bar's open/close body is checked against Yahoo's own hourly bars
# before it is stored. MEASURED 2026-10-15: Yahoo's daily bars for 10 FTSE 100 stocks carried highs 15-18%
# above the body (TSCO.L 622.5, close 534.6) that its 5-minute and hourly bars for the same day do not
# contain (max 535.6). The bad high closed TSCO.L's 8 Oct trigger as TARGET and dropped it from the owner's
# report (owner 2026-10-18: "If there is a trigger it must stay in the history of triggers").
WICK_CHECK_PCT = 6.0
# Hourly bars disagreeing by less than this are rounding / adjustment noise, not a bad print.
WICK_AGREE_PCT = 1.0
# ONLY where Yahoo's hourly bars are a proven referee. MEASURED 2026-10-18 over every bar of the last 30 days:
# every proven bad print was London (10 FTSE 100 highs 10-15, RPI.L / BPT.L / ONT.L lows), and London hourly
# coverage is complete (9 bars a day). Australia's hourly bars disagree with its daily bars in BOTH directions
# (XRO.AX hourly high 61.57 vs daily 58.96), and Japan was not proven; the first backfill under a universal
# check changed 42 Japanese / Australian lows by up to 7% on that unproven evidence and was reverted.
WICK_CHECK_SUFFIXES = (".L",)

# How much history to keep (user 2026-06-29: record 3 years; delete anything older).
# Raised to 5 years (user 2026-08-02): the squeeze-history replay wants the deepest window the price
# data allows, so retention must not prune below what we backfill. The nightly audit prunes to this
# same window, so a 5-year backfill is now durable instead of being cut back to 3y overnight.
#
# READ FROM config SINCE 2026-09-20 -- this used to be its own literal 5, and that mattered more than a
# duplicated constant usually does. prune_older_than() takes it as a DEFAULT ARGUMENT, and price_audit.py
# calls prune_older_than() with no argument on a NIGHTLY schedule, so this value -- not the 4.5 recorded
# in config and used by run_price_history_prune.py -- was the cutoff actually governing the live table.
# The disagreement was invisible because a 5-year cutoff predates the oldest bar (2022-02-17), so the
# nightly prune deleted nothing and neither number was ever tested. config is now the only definition;
# the fallback exists solely so importing this module can never fail on a missing setting.
try:
    import config as _config
    RETENTION_YEARS = float(getattr(_config, "PRICE_HISTORY_RETENTION_YEARS", 4.05))
except Exception:                                    # config must never break this import
    RETENTION_YEARS = 4.05

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


def _intraday_extremes(yahoo_symbol, dates):
    """{bar_date: (high, low, last close)} from Yahoo's hourly bars, for the days asked. Hourly covers ~730
    days; an older or unanswered day is simply absent."""
    import yfinance as yf
    lo_d, hi_d = min(dates), max(dates)
    if lo_d < date.today() - timedelta(days=720):
        return {}
    h = yf.Ticker(yahoo_symbol).history(start=lo_d.strftime("%Y-%m-%d"),
                                        end=(hi_d + timedelta(days=1)).strftime("%Y-%m-%d"),
                                        interval="1h", auto_adjust=True)
    out = {}
    if h is None or h.empty:
        return out
    for d, g in h.groupby([pd.Timestamp(i).date() for i in h.index]):
        if d in dates and not g["Close"].dropna().empty:
            out[d] = (float(g["High"].max()), float(g["Low"].min()), float(g["Close"].dropna().iloc[-1]))
    return out


def _verify_wicks(ticker, yahoo_symbol, rows, intraday=None):
    """(rows, corrections). A daily high/low more than WICK_CHECK_PCT beyond the body that Yahoo's own hourly
    bars contradict is replaced by the hourly extreme. Not rescaled: the daily close is the closing auction,
    not the last hourly close (TSCO.L 10-15: 534.6 vs 533.4), and a dividend adjustment only LOWERS a daily
    bar, so it can never read as a high the hourly bars do not reach. A wick the hourly bars confirm, or that
    cannot be checked, is stored as given and logged."""
    if not str(ticker).endswith(WICK_CHECK_SUFFIXES):
        return rows, []
    suspect = {bd for bd, o, h, l, c, _v in rows if o is not None and h is not None and l is not None and (
        h > max(o, c) * (1 + WICK_CHECK_PCT / 100) or l < min(o, c) * (1 - WICK_CHECK_PCT / 100))}
    if not suspect:
        return rows, []
    try:
        ext = (intraday or _intraday_extremes)(yahoo_symbol or ticker, suspect)
    except Exception as e:
        ext = {}
        log.warning(f"{ticker}: wick check could not read hourly bars: {e}")
    out, fixes = [], []
    for bd, o, h, l, c, v in rows:
        if bd in suspect:
            if bd not in ext:
                log.warning(f"{ticker} {bd}: wick H {h:g} L {l:g} vs body {o:g}/{c:g} UNVERIFIED (no hourly bars)")
            else:
                ih, il, _ic = ext[bd]
                if h > ih * (1 + WICK_AGREE_PCT / 100):
                    fixes.append((bd, "high", h, max(ih, o, c)))
                    h = max(ih, o, c)
                if l < il * (1 - WICK_AGREE_PCT / 100):
                    fixes.append((bd, "low", l, min(il, o, c)))
                    l = min(il, o, c)
        out.append((bd, o, h, l, c, v))
    for bd, f, was, now in fixes:
        log.warning(f"{ticker} {bd}: daily {f} {was:g} contradicted by Yahoo's hourly bars -> {now:g}")
    return out, fixes


def upsert_bars(ticker, df, source, db=None, chunk=400, yahoo_symbol=None):
    """Bulk-UPSERT a yfinance OHLCV frame for one ticker. ON CONFLICT updates the bar + source +
    updated_at (so an audit correction overwrites a stale value). Returns the number of rows written.
    Yahoo bars pass _verify_wicks first; IG bars (the cross-check's truth) do not."""
    rows = _df_to_rows(df)
    if not rows:
        return 0
    fixes = []
    if source != "IG":
        rows, fixes = _verify_wicks(ticker, yahoo_symbol, rows)
    own = db is None
    if own:
        db = get_db()
    written = 0
    try:
        ensure_schema(db)
        for bd, f, was, now in fixes:
            # once per bar and field: the nightly refresh re-fetches the same bad bar for several days
            db.run("insert into price_corrections (ticker,bar_date,field,original,corrected,evidence) "
                   "select :t,:d,:f,:o,:n,'Yahoo hourly bars' where not exists (select 1 from price_corrections "
                   "where ticker=:t and bar_date=:d and field=:f)", t=ticker, d=bd, f=f, o=was, n=now)
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


def set_double_checked(ticker, bar_dates, db=None):
    """Flag bars as verified by >1 source (user 2026-06-29). `bar_dates` is an iterable of date/'YYYY-MM-DD'."""
    dates = [str(d) for d in bar_dates]
    if not dates:
        return 0
    own = db is None
    if own:
        db = get_db()
    try:
        ensure_schema(db)
        params = {"t": ticker}
        ph = []
        for i, d in enumerate(dates):
            params[f"d{i}"] = d
            ph.append(f":d{i}")
        db.run("update price_history set double_checked=true, updated_at=now() "
               f"where ticker=:t and bar_date in ({','.join(ph)})", **params)
    finally:
        if own:
            db.close()
    return len(dates)


def retention_cutoff(db, years=None):
    """The date before which bars may be deleted. THE ONLY definition of that boundary.

    MUST NOT USE make_interval(years => ...). That function takes an INTEGER, so the moment retention
    became fractional (4.05 on 2026-09-20) every call raised
    `invalid input syntax for type integer: "4.05"` -- and price_audit.py calls prune_older_than inside
    a bare `except Exception` that logs a warning, so the nightly prune would have failed EVERY NIGHT
    while the job stayed green. Before the change it was a working no-op; after it, a silent error.
    Proven against the live database 2026-09-21: years=5 gives 2021-09-21, years=4.05 raises.

    `('4.05' || ' years')::interval` does accept a fraction, and Postgres rounds it to 4 years 1 month,
    i.e. slightly MORE history than asked for -- which errs towards keeping data, the safe direction,
    and stays above the 4.0-year floor the replay needs. run_price_history_prune.py computed its own
    cutoff this way while this function used the other form, so the two disagreed by 13 days
    (2022-08-21 against 2022-09-03) on the same constant. One definition now, used by both.
    """
    y = RETENTION_YEARS if years is None else years
    return db.run("select (current_date - (:y || ' years')::interval)::date", y=str(y))[0][0]


def prune_older_than(years=RETENTION_YEARS, db=None):
    """Delete bars older than `years` (user 2026-06-29: keep 3y, drop the rest). Returns rows deleted."""
    own = db is None
    if own:
        db = get_db()
    try:
        ensure_schema(db)
        cutoff = retention_cutoff(db, years)
        before = db.run("select count(*) from price_history where bar_date < :c", c=cutoff)[0][0]
        db.run("delete from price_history where bar_date < :c", c=cutoff)
    finally:
        if own:
            db.close()
    return int(before or 0)


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
                upsert_bars(ticker, dl, source, yahoo_symbol=yahoo_symbol)
            except Exception as e:
                log.warning(f"price_store write-through failed for {ticker}: {e}")
            if getattr(dl.columns, "nlevels", 1) > 1:
                dl = dl.copy()
                dl.columns = dl.columns.get_level_values(0)
            return dl
    except Exception as e:
        log.warning(f"yfinance fetch failed for {ticker}: {e}")

    return stored   # better a slightly-stale stored series than nothing
