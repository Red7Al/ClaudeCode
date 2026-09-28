"""Daily RVOL / VWAP / ATR capture (user 2026-08-29: "above_vwap rvol should be recorded every day").

The metrics were computed and discarded: a schema query found the only persisted column among
above_vwap / rvol / atr_expanding / volume_score was squeeze_history.rvol, per-funnel at trigger time.
The cost, measured the same day: require_above_vwap was ON while above_vwap was None on all 55 pending
working orders, so no order from an earlier day could be checked against its own filter.

The most important test here is the AGREEMENT one. Storing a second, subtly different definition of
"above VWAP" or "RVOL" would be worse than storing nothing, because a stored number is trusted.
"""
import datetime as dt
import re

import pytest

import instrument_metrics as im


def _bars(n=80, *, volume=1000, rising=True):
    """(date, high, low, close, volume) — the shape volume_score expects."""
    out, day = [], dt.date(2026, 1, 1)
    for k in range(n):
        close = 100 + (k if rising else -k) * 0.5
        out.append(((day + dt.timedelta(days=k)).isoformat(),
                    close + 1.0, close - 1.0, close, volume + (k * 7 if volume else 0)))
    return out


# ------------------------------------------------------------------------------------------------------
# Agreement with the live path — the whole reason this module calls volume_score rather than
# reimplementing anything.
# ------------------------------------------------------------------------------------------------------

def test_compute_agrees_with_the_live_instrument_metrics_path(monkeypatch):
    from hvf_web import server

    bars = _bars()
    snap = {"generated_utc": "2026-08-29T00:00:00Z",
            "records": [{"ticker": "AAA", "direction": "BULL", "has_signal": True}]}
    monkeypatch.setitem(server._LIVE_INSTRUMENT_METRICS_CACHE, "gen", None)
    monkeypatch.setitem(server._LIVE_INSTRUMENT_METRICS_CACHE, "data", {})

    class _Db:
        def run(self, *a, **k):
            return []

        def close(self):
            pass

    monkeypatch.setattr("db_pool.get_db", lambda: _Db(), raising=False)
    monkeypatch.setattr(server, "_perf_bars", lambda db, cutoff, lookback_days=0: {"AAA": bars})

    live = server._live_instrument_metrics(snap)["AAA"]
    mine = im.compute("AAA", bars, "BULL")

    for field in ("rvol", "rvol_date", "above_vwap", "atr_expanding", "status"):
        assert mine[field] == live[field], (
            f"{field} disagrees with the live path: stored {mine[field]!r} vs shown {live[field]!r}")
    assert mine["bar_date"] == live["date"]


# ------------------------------------------------------------------------------------------------------
# The two VWAP semantics
# ------------------------------------------------------------------------------------------------------

def test_the_literal_vwap_metric_is_not_inverted_for_a_bear_instrument():
    """server.py is explicit that 'a BEAR row must not invert it' for the instrument metric."""
    bars = _bars()

    assert im.compute("AAA", bars, "BULL")["above_vwap"] is im.compute("AAA", bars, "BEAR")["above_vwap"]


def test_the_setup_metric_is_direction_aware():
    """A BEAR setup confirms on price BELOW its VWAP, which is the opposite answer -- and it is this
    one the trading filters are expressed against."""
    bars = _bars()

    bull = im.compute("AAA", bars, "BULL")
    bear = im.compute("AAA", bars, "BEAR")

    assert bull["above_vwap_setup"] is not bear["above_vwap_setup"]
    assert bull["above_vwap_setup"] is bull["above_vwap"], "for a BULL row the two agree"
    assert bear["direction"] == "BEAR", "the direction used must be recorded, or it is not reproducible"


# ------------------------------------------------------------------------------------------------------
# Storage behaviour
# ------------------------------------------------------------------------------------------------------

# Every :placeholder, ignoring ::type casts. pg8000 raises when one has no matching keyword argument;
# this fake used to accept anything, which is how a broken INSERT passed a green suite for six days.
_PLACEHOLDERS = re.compile(r"(?<!:):([a-zA-Z_]\w*)")


class _FakeDb:
    """A stand-in that fails where the real driver fails.

    THE BUG THIS NOW CATCHES. record_daily's INSERT named `mcap` and `:mc` but never passed `mc`, so
    pg8000 raised on every single instrument: 1,772 failures a day, nothing stored, and the scheduled
    job still green. This fake accepted `**p` without ever comparing it to the SQL, so the test asserting
    `stored == 2` was asserting against a database that could not fail. A fake that cannot reject what
    the real one rejects is not a test double; it is a way of agreeing with yourself.
    """

    def __init__(self, fail_inserts_over=None):
        self.rows = {}
        self.statements = 0
        self.inserts = 0                  # INSERT statements issued, batched or not
        self.selects = 0                  # SELECT statements issued -- the per-ticker read count
        # Simulates a batch the real driver would reject (e.g. too many parameters), so the row-by-row
        # fallback can be tested rather than assumed.
        self.fail_inserts_over = fail_inserts_over

    def run(self, sql, **p):
        self.statements += 1
        missing = _PLACEHOLDERS.findall(re.sub(r"::\w+", "", sql))
        absent = sorted({m for m in missing} - set(p))
        if absent:
            raise RuntimeError(
                f"There's a placeholder '{absent[0]}' in the query, but no matching keyword argument.")
        low = sql.strip().lower()
        if low.startswith("create table") or low.startswith("create ") or low.startswith("alter table"):
            return []
        if low.startswith("select"):
            self.selects += 1
            return []
        if "insert into" in sql:
            self.inserts += 1
            # Batched now: parameters are suffixed per row (ticker0, as_of0, ticker1, ...).
            n = sum(1 for k in p if k.startswith("ticker"))
            if self.fail_inserts_over is not None and n > self.fail_inserts_over:
                raise RuntimeError(f"batch of {n} rejected")
            for i in range(n):
                self.rows[(p[f"ticker{i}"], p[f"as_of{i}"])] = {
                    k[:-len(str(i))] if k.endswith(str(i)) else k: v
                    for k, v in p.items() if k.endswith(str(i))}
        return []

    def close(self):
        pass


def _snap(*tickers):
    return {"records": [{"ticker": t, "direction": "BULL"} for t in tickers]}


def test_records_one_row_per_instrument_per_day(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(im, "_bars_bulk", lambda ts, end, d, **k: {t: _bars() for t in ts})

    out = im.record_daily(_snap("AAA", "BBB"), as_of=dt.date(2026, 8, 29), db=db)

    assert out["stored"] == 2 and out["failed"] == 0
    assert set(db.rows) == {("AAA", "2026-08-29"), ("BBB", "2026-08-29")}


def test_rerunning_the_same_day_overwrites_rather_than_duplicating(monkeypatch):
    """It rides on the daily report, which can be re-dispatched; a second run must not double the day."""
    db = _FakeDb()
    monkeypatch.setattr(im, "_bars_bulk", lambda ts, end, d, **k: {t: _bars() for t in ts})

    im.record_daily(_snap("AAA"), as_of=dt.date(2026, 8, 29), db=db)
    im.record_daily(_snap("AAA"), as_of=dt.date(2026, 8, 29), db=db)

    assert len(db.rows) == 1, "the upsert key must be (ticker, as_of)"


def test_an_instrument_with_no_price_history_is_skipped_not_stored_blank(monkeypatch):
    """A blank row would look like a measured 'no' rather than an absence of data."""
    db = _FakeDb()
    monkeypatch.setattr(im, "_bars_bulk", lambda ts, end, d, **k: {})

    out = im.record_daily(_snap("AAA"), as_of=dt.date(2026, 8, 29), db=db)

    assert out["no_history"] == 1 and out["stored"] == 0
    assert db.rows == {}


def test_one_bad_instrument_does_not_lose_the_others(monkeypatch):
    db = _FakeDb()

    monkeypatch.setattr(im, "_bars_bulk", lambda ts, end, d, **k: {t: _bars() for t in ts})
    _real_compute = im.compute

    def _boom(ticker, bars, direction=None):
        if ticker == "BAD":
            raise RuntimeError("metric computation failed")
        return _real_compute(ticker, bars, direction)

    monkeypatch.setattr(im, "compute", _boom)

    out = im.record_daily(_snap("AAA", "BAD", "BBB"), as_of=dt.date(2026, 8, 29), db=db)

    assert out["stored"] == 2 and out["failed"] == 1
    assert set(db.rows) == {("AAA", "2026-08-29"), ("BBB", "2026-08-29")}


def test_a_total_failure_never_raises(monkeypatch):
    """It runs beside the history refresh in the daily report. It must never cost a scan its publication."""
    monkeypatch.setattr(im, "_bars", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))

    class _Explode:
        def run(self, *a, **k):
            raise RuntimeError("db gone")

        def close(self):
            pass

    monkeypatch.setattr("db_pool.get_db", lambda: _Explode(), raising=False)

    out = im.record_daily(_snap("AAA"), as_of=dt.date(2026, 8, 29))

    assert out["stored"] == 0


# ------------------------------------------------------------------------------------------------------
# This repository's recurring defect: correct, tested code that nothing ever calls.
# ------------------------------------------------------------------------------------------------------

def test_the_daily_report_actually_invokes_the_recorder():
    from pathlib import Path

    src = Path(__file__).with_name("run_hvf_report.py").read_text(encoding="utf-8")

    assert "import instrument_metrics" in src and "record_daily(snapshot)" in src, (
        "nothing invokes the recorder — this is the repository's documented recurring defect")
    assert src.index("refresh_daily(snapshot)") < src.index("record_daily(snapshot)"), (
        "expected it beside the history refresh, on the daily path known to run")


def test_volume_score_is_recorded_daily_not_recomputed_per_request():
    """User 2026-08-29: "processing on the fly does not make sense when the data set changes so
    infrequently". VolumeScore is the most expensive of the four, so it is stored with the rest."""
    m = im.compute("AAA", _bars(), "BULL")

    assert "volume_score" in m and "volume_score_max" in m
    if m["volume_score"] is not None:
        assert isinstance(m["volume_score"], int)
        assert 0 <= m["volume_score"] <= (m["volume_score_max"] or 0)


def test_a_volume_score_failure_does_not_blank_the_other_metrics(monkeypatch):
    """A scoring error must cost only the score, never the RVOL/VWAP/ATR beside it."""
    import volume_score as vs
    monkeypatch.setattr(vs, "volume_score",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("scoring failed")))

    m = im.compute("AAA", _bars(), "BULL")

    assert m["volume_score"] is None
    assert m["rvol"] is not None and m["above_vwap"] is not None
    assert m["status"] == "complete"


def test_every_stored_metric_is_a_stored_type_not_a_recomputation():
    """The point of the table: booleans and integers a query can filter on directly."""
    m = im.compute("AAA", _bars(), "BULL")

    for field in ("above_vwap", "above_vwap_setup", "atr_expanding"):
        assert m[field] is None or isinstance(m[field], bool), f"{field} must be stored as a boolean"
    assert m["rvol"] is None or isinstance(m["rvol"], float)


def test_the_52_week_range_agrees_with_the_instruments_tab(monkeypatch):
    """A stored 52-week range that disagreed with the displayed one would be worse than none at all."""
    from hvf_web import server

    bars = _bars(n=500)
    snap = {"generated_utc": "2026-08-29T00:00:00Z", "records": [{"ticker": "AAA"}]}
    monkeypatch.setitem(server._WK52_CACHE, "gen", None)
    monkeypatch.setitem(server._WK52_CACHE, "data", {})

    # The live path slices by lookback itself; hand it the same window this module uses.
    cutoff = (dt.date.fromisoformat(bars[-1][0]) - dt.timedelta(days=im.WK52_LOOKBACK_DAYS)).isoformat()

    class _Db:
        def run(self, *a, **k):
            # _snapshot_52wk now asks the database to aggregate, so stand in for min(low)/max(high)
            # over exactly the same window rather than handing back raw bars.
            window = [b for b in bars if b[0] >= cutoff]
            if not window:
                return []
            return [("AAA", min(b[2] for b in window), max(b[1] for b in window))]

        def close(self):
            pass

    monkeypatch.setattr("db_pool.get_db", lambda: _Db(), raising=False)

    live_low, live_high = server._snapshot_52wk(snap)["AAA"]
    mine = im.compute("AAA", bars, "BULL")

    assert (mine["wk52_low"], mine["wk52_high"]) == (live_low, live_high), (
        f"stored ({mine['wk52_low']}, {mine['wk52_high']}) disagrees with shown ({live_low}, {live_high})")


def test_the_52_week_window_matches_the_servers_constant():
    from hvf_web import server

    assert im.WK52_LOOKBACK_DAYS == server._WK52_LOOKBACK_DAYS, (
        "the stored range would cover a different period from the displayed one")


def test_the_52_week_range_only_uses_the_trailing_window():
    """Bars older than the window must not widen the range."""
    old = [("2020-01-01", 9999.0, 0.01, 100.0, 1000)]
    recent = _bars(n=60)

    m = im.compute("AAA", old + recent, "BULL")

    assert m["wk52_high"] < 9999.0 and m["wk52_low"] > 0.01


def test_the_reader_columns_and_its_query_cannot_drift():
    """A real bug, found on 2026-08-29 by reading back a live row rather than trusting a green test.

    latest() had a hand-written SELECT of 10 columns zipped against a 14-name tuple. zip() truncates
    silently, so `direction` was returned under the volume_score key and `status` under its max --
    wrong values, confidently returned, with nothing raising.
    """
    from pathlib import Path

    src = Path(__file__).with_name("instrument_metrics.py").read_text(encoding="utf-8")

    assert "', '.join(COLUMNS)" in src, "the SELECT must be built from COLUMNS, not written out separately"
    assert "dict(zip(COLUMNS, r))" in src, "the same list must name the values it zips"
    for name in ("volume_score", "wk52_low", "wk52_high", "above_vwap_setup"):
        assert name in im.COLUMNS, f"{name} is stored but not readable"


def test_every_column_the_reader_names_exists_in_the_schema():
    from pathlib import Path

    ddl = Path(__file__).with_name("instrument_metrics.py").read_text(encoding="utf-8")
    # Sliced to the key line, which moved to (ticker, bar_date) on 2026-09-19 when the table was re-keyed
    # on the BAR it describes rather than on the day we happened to look.
    ddl = ddl[ddl.index("create table if not exists"):ddl.index("primary key (ticker, bar_date)")]

    for name in im.COLUMNS:
        assert name in ddl, f"latest() selects {name!r}, which the table does not define"


# ------------------------------------------------------------------------------------------------------
# Market cap history (user 2026-08-29: "we do not need mcap every day" / "have a table for mcap is fine
# with periodic new rows containing latest data").
# ------------------------------------------------------------------------------------------------------

def test_daily_metrics_do_not_capture_market_cap():
    """Deliberately absent: mcap moves slowly and is only used for wide bands, so a daily copy would be
    storage for no gain on a 500 MB tier."""
    m = im.compute("AAA", _bars(), "BULL")

    assert m.get("mcap") is None
    src = __import__("pathlib").Path(__file__).with_name("instrument_metrics.py").read_text(encoding="utf-8")
    assert "select ticker, mcap from instrument_mcap" not in src, "the daily job must not read mcap"


def test_the_weekly_backfill_appends_history_without_re_keying_the_current_table():
    """order_metrics.py and hvf_web/server.py both read instrument_mcap expecting ONE row per ticker.
    History therefore goes in its own table -- re-keying that one would silently hand them an arbitrary
    historical value instead of the current one."""
    from pathlib import Path

    src = Path(__file__).with_name("mcap_backfill.py").read_text(encoding="utf-8")

    assert "create table if not exists instrument_mcap_history" in src
    assert "primary key (ticker, as_of)" in src, "a same-day re-run must overwrite, not duplicate"
    assert "insert into instrument_mcap_history" in src, "the weekly job must actually write it"
    assert "ticker      text primary key" in src, "the current table must keep its one-row-per-ticker key"


# ======================================================================================================
# record_daily was 3,545 round trips a day.
#
# MEASURED 2026-09-28 on the live daily report: 1,773 per-ticker price_store.get_bars() SELECTs plus
# 1,772 per-row INSERTs, inside a phase that logged nothing for ~51 minutes at ~371 ms a round trip.
# On 2026-09-28 that phase ran into the workflow's 120-minute cap and Scanner report email, Winners
# precompute, Best settings audit and HVF orders were all skipped.
#
# These pin the SHAPE -- one bulk read, batched writes -- and the two fallbacks that keep the old
# promise that one bad instrument cannot lose the others.
# ======================================================================================================


def _many(n):
    return _snap(*[f"T{i:03d}" for i in range(n)])


def test_bars_are_read_in_bulk_not_once_per_instrument(monkeypatch):
    db = _FakeDb()
    calls = {"bulk": 0, "single": 0}

    def _bulk(ts, end, d, **k):
        calls["bulk"] += 1
        return {t: _bars() for t in ts}

    def _single(t, end, d):
        calls["single"] += 1
        return _bars()

    monkeypatch.setattr(im, "_bars_bulk", _bulk)
    monkeypatch.setattr(im, "_bars", _single)

    im.record_daily(_many(50), as_of=dt.date(2026, 8, 29), db=db)

    assert calls["bulk"] == 1, "the bars must be fetched once for everyone"
    assert calls["single"] == 0, f"{calls['single']} per-ticker reads survived the bulk path"


def test_the_bulk_read_is_one_query_per_chunk_not_one_per_ticker():
    class _CountingDb:
        def __init__(self):
            self.n = 0

        def run(self, sql, **p):
            self.n += 1
            assert "in (" in sql and "price_history" in sql
            return []

    db = _CountingDb()
    im._bars_bulk([f"T{i}" for i in range(450)], dt.date(2026, 8, 29), db, chunk=200)

    assert db.n == 3, f"450 tickers in chunks of 200 is 3 queries, got {db.n}"


def test_the_bulk_read_returns_the_same_shape_the_per_ticker_read_did():
    """compute() is shared with the live web path, so a different shape here would make the stored
    metric and the displayed metric disagree about the same instrument."""
    class _Db:
        def run(self, sql, **p):
            return [("AAA", dt.date(2026, 1, 2), 11.0, 9.0, 10.0, None),
                    ("AAA", dt.date(2026, 1, 3), 12.0, 10.0, 11.0, 500)]

    out = im._bars_bulk(["AAA"], dt.date(2026, 8, 29), _Db())

    assert out["AAA"] == [("2026-01-02", 11.0, 9.0, 10.0, 0.0),      # NaN/None volume -> 0.0
                          ("2026-01-03", 12.0, 10.0, 11.0, 500.0)]


def test_fifty_instruments_are_written_in_one_statement(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(im, "_bars_bulk", lambda ts, end, d, **k: {t: _bars() for t in ts})

    out = im.record_daily(_many(50), as_of=dt.date(2026, 8, 29), db=db)

    assert out["stored"] == 50
    assert db.inserts == 1, f"expected one batched INSERT, got {db.inserts}"
    assert len(db.rows) == 50


def test_writes_are_split_once_they_exceed_the_batch_size(monkeypatch):
    monkeypatch.setattr(im, "_WRITE_BATCH", 20)
    db = _FakeDb()
    monkeypatch.setattr(im, "_bars_bulk", lambda ts, end, d, **k: {t: _bars() for t in ts})

    im.record_daily(_many(50), as_of=dt.date(2026, 8, 29), db=db)

    assert db.inserts == 3, f"50 rows at 20 a batch is 3 statements, got {db.inserts}"
    assert len(db.rows) == 50


def test_a_rejected_batch_falls_back_to_one_row_at_a_time(monkeypatch):
    """One bad instrument must not lose the others -- the promise that per-row writes gave for free."""
    db = _FakeDb(fail_inserts_over=1)
    monkeypatch.setattr(im, "_bars_bulk", lambda ts, end, d, **k: {t: _bars() for t in ts})

    out = im.record_daily(_snap("AAA", "BBB", "CCC"), as_of=dt.date(2026, 8, 29), db=db)

    assert out["stored"] == 3, "the fallback must still store every good row"
    assert set(db.rows) == {("AAA", "2026-08-29"), ("BBB", "2026-08-29"), ("CCC", "2026-08-29")}
    assert db.inserts == 4, "one rejected batch then three single rows"


def test_a_bulk_read_failure_degrades_to_per_ticker_rather_than_to_nothing(monkeypatch):
    """A bulk problem must cost speed, not the whole day's metrics."""
    db = _FakeDb()

    def _boom(*a, **k):
        raise RuntimeError("bulk read gone")

    monkeypatch.setattr(im, "_bars_bulk", _boom)
    monkeypatch.setattr(im, "_bars", lambda t, end, d: _bars())

    out = im.record_daily(_snap("AAA", "BBB"), as_of=dt.date(2026, 8, 29), db=db)

    assert out["stored"] == 2 and out["failed"] == 0
    assert set(db.rows) == {("AAA", "2026-08-29"), ("BBB", "2026-08-29")}


def test_the_same_instrument_twice_cannot_break_the_upsert(monkeypatch):
    """ON CONFLICT DO UPDATE cannot touch one key twice in a statement; the writer must dedupe."""
    rows = [{c: None for c in im._METRIC_COLS} for _ in range(2)]
    for r in rows:
        r["ticker"], r["bar_date"], r["as_of"] = "AAA", "2026-08-29", "2026-08-29"
    rows[1]["rvol"] = 2.5
    db = _FakeDb()

    stored, failures = im._write_metrics(db, rows)

    assert failures == []
    assert db.inserts == 1 and stored == 1, "the duplicate key must collapse to one row"
    assert db.rows[("AAA", "2026-08-29")]["rvol"] == 2.5, "last write wins, as the loop did"
