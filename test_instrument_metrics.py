"""Daily RVOL / VWAP / ATR capture (user 2026-08-29: "above_vwap rvol should be recorded every day").

The metrics were computed and discarded: a schema query found the only persisted column among
above_vwap / rvol / atr_expanding / volume_score was squeeze_history.rvol, per-funnel at trigger time.
The cost, measured the same day: require_above_vwap was ON while above_vwap was None on all 55 pending
working orders, so no order from an earlier day could be checked against its own filter.

The most important test here is the AGREEMENT one. Storing a second, subtly different definition of
"above VWAP" or "RVOL" would be worse than storing nothing, because a stored number is trusted.
"""
import datetime as dt

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

class _FakeDb:
    def __init__(self):
        self.rows = {}
        self.statements = 0

    def run(self, sql, **p):
        self.statements += 1
        if sql.strip().lower().startswith("create table"):
            return []
        if "insert into" in sql:
            self.rows[(p["t"], p["d"])] = p          # primary key (ticker, as_of)
        return []

    def close(self):
        pass


def _snap(*tickers):
    return {"records": [{"ticker": t, "direction": "BULL"} for t in tickers]}


def test_records_one_row_per_instrument_per_day(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(im, "_bars", lambda t, end, d: _bars())

    out = im.record_daily(_snap("AAA", "BBB"), as_of=dt.date(2026, 8, 29), db=db)

    assert out["stored"] == 2 and out["failed"] == 0
    assert set(db.rows) == {("AAA", "2026-08-29"), ("BBB", "2026-08-29")}


def test_rerunning_the_same_day_overwrites_rather_than_duplicating(monkeypatch):
    """It rides on the daily report, which can be re-dispatched; a second run must not double the day."""
    db = _FakeDb()
    monkeypatch.setattr(im, "_bars", lambda t, end, d: _bars())

    im.record_daily(_snap("AAA"), as_of=dt.date(2026, 8, 29), db=db)
    im.record_daily(_snap("AAA"), as_of=dt.date(2026, 8, 29), db=db)

    assert len(db.rows) == 1, "the upsert key must be (ticker, as_of)"


def test_an_instrument_with_no_price_history_is_skipped_not_stored_blank(monkeypatch):
    """A blank row would look like a measured 'no' rather than an absence of data."""
    db = _FakeDb()
    monkeypatch.setattr(im, "_bars", lambda t, end, d: [])

    out = im.record_daily(_snap("AAA"), as_of=dt.date(2026, 8, 29), db=db)

    assert out["no_history"] == 1 and out["stored"] == 0
    assert db.rows == {}


def test_one_bad_instrument_does_not_lose_the_others(monkeypatch):
    db = _FakeDb()

    def _boom(ticker, end, d):
        if ticker == "BAD":
            raise RuntimeError("price read failed")
        return _bars()

    monkeypatch.setattr(im, "_bars", _boom)

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

    class _Db:
        def run(self, *a, **k):
            return []

        def close(self):
            pass

    monkeypatch.setattr("db_pool.get_db", lambda: _Db(), raising=False)
    # The live path slices by lookback itself; hand it the same window this module uses.
    cutoff = (dt.date.fromisoformat(bars[-1][0]) - dt.timedelta(days=im.WK52_LOOKBACK_DAYS)).isoformat()
    monkeypatch.setattr(server, "_perf_bars",
                        lambda db, cutoff_map, lookback_days=0: {"AAA": [b for b in bars if b[0] >= cutoff]})

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
    ddl = ddl[ddl.index("create table if not exists"):ddl.index("primary key (ticker, as_of)")]

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
