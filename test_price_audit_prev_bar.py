"""The daily price refresh checks every instrument got the previous weekday's bar, retries once, and records it.

MEASURED 2026-10-12: for each trading day 2026-09-29..10-08 about 600 instruments (FTSE 250 247, FTSE 100 ~100,
Euronext 100) had their day-D bar first written only after the next morning's refresh. The 03:31 UTC run of
2026-10-08 got 20.9 bars per UK instrument over the 30-day window and the 23:05 run 21.9, yet both reported
success. These tests pin that the run now counts the gap per market, re-asks Yahoo with a different request,
stores what that returns WITHOUT any partial bar for today, and writes the counts to price_audit_log."""

import sys
import types
from datetime import date

import pandas as pd

import price_audit

TODAY = date(2026, 10, 12)          # a Monday: the previous weekday is Friday 2026-10-09
PREV = date(2026, 10, 9)


def _frame(days, tz=None):
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    if tz:
        idx = idx.tz_localize(tz)
    n = len(days)
    return pd.DataFrame({"Open": [1.0] * n, "High": [1.0] * n, "Low": [1.0] * n, "Close": [1.0] * n,
                         "Volume": [100] * n}, index=idx)


class _Db:
    def __init__(self):
        self.inserts = []

    def run(self, sql, **kw):
        if "insert into price_audit_log" in sql:
            self.inserts.append(kw)
        return []

    def close(self):
        pass


def _wire(monkeypatch, download, history):
    db, upserts = _Db(), []
    fake = types.SimpleNamespace(download=lambda sym, **k: download[sym],
                                 Ticker=lambda sym: types.SimpleNamespace(history=lambda **k: history[sym]))
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    monkeypatch.setattr(price_audit, "_today", lambda: TODAY)
    monkeypatch.setattr(price_audit, "_universe", lambda: {t: t for t in download})
    monkeypatch.setattr(price_audit, "_markets",
                        lambda: {"FOUR.L": "FTSE 250", "AAS.L": "FTSE 250", "MC.PA": "Euronext", "NVDA": "NASDAQ 100"})
    monkeypatch.setattr(price_audit, "get_db", lambda *a, **k: db)
    monkeypatch.setattr(price_audit, "_ensure_audit_log", lambda d: None)
    monkeypatch.setattr(price_audit.price_store, "ensure_schema", lambda d: None)
    monkeypatch.setattr(price_audit.price_store, "prune_older_than", lambda: 0)
    monkeypatch.setattr(price_audit.price_store, "get_bars", lambda *a, **k: None)
    monkeypatch.setattr(price_audit.price_store, "upsert_bars",
                        lambda tk, df, src, db=None, yahoo_symbol=None: upserts.append((tk, [pd.Timestamp(i).date() for i in df.index])) or len(df))
    return db, upserts


def test_missing_previous_day_bar_is_counted_per_market_retried_and_recorded(monkeypatch):
    first = [date(2026, 10, 7), date(2026, 10, 8)]             # Yahoo left out Friday, as measured
    download = {"FOUR.L": _frame(first), "AAS.L": _frame(first), "MC.PA": _frame(first),
                "NVDA": _frame(first + [PREV])}
    history = {"FOUR.L": _frame([date(2026, 10, 8), PREV], tz="Europe/London"),
               "AAS.L": _frame([date(2026, 10, 8)], tz="Europe/London"),      # the retry does not have it either
               "MC.PA": _frame([date(2026, 10, 8), PREV, TODAY], tz="Europe/Paris")}  # plus today's partial bar
    db, upserts = _wire(monkeypatch, download, history)
    price_audit.run("daily", 30, "YF", use_ig=False)
    notes = db.inserts[-1]["no"]
    assert "prev-day bar 2026-10-09: missing after first fetch 3/4 (FTSE 250 2, Euronext 1)" in notes, notes
    assert "retry recovered 2 (Euronext 1, FTSE 250 1), still missing 1 (FTSE 250 1)" in notes, notes
    retried = {tk: days for tk, days in upserts[4:]}
    assert retried["MC.PA"] == [date(2026, 10, 8), PREV], "today's partial bar must never be stored"
    assert retried["FOUR.L"] == [date(2026, 10, 8), PREV]
    assert "NVDA" not in retried, "an instrument that already has the bar is not re-asked"


def test_nothing_missing_still_says_so(monkeypatch):
    download = {"NVDA": _frame([date(2026, 10, 8), PREV])}
    db, upserts = _wire(monkeypatch, download, {})
    price_audit.run("daily", 30, "YF", use_ig=False)
    assert db.inserts[-1]["no"].endswith("prev-day bar 2026-10-09: missing after first fetch 0/1")


def test_the_backfill_does_not_run_the_check(monkeypatch):
    db, _ = _wire(monkeypatch, {"FOUR.L": _frame([date(2026, 10, 8)])}, {})
    price_audit.run("backfill", 1100, "YF", use_ig=False)
    assert "prev-day" not in db.inserts[-1]["no"]


def test_previous_weekday_skips_the_weekend():
    assert price_audit._prev_weekday(TODAY) == PREV
    assert price_audit._prev_weekday(date(2026, 10, 13)) == TODAY


def test_a_run_that_crosses_midnight_judges_each_fetch_by_its_own_day(monkeypatch):
    # MEASURED 2026-10-13: the evening run started ~23:00 UTC on Monday 10-12 and ended after midnight. The
    # check used the date at the END (prev weekday 10-12), but every fetch before midnight asked Yahoo for
    # end=10-12 (exclusive) -- so 1,702 of 1,750 read as missing a bar the request excludes by design.
    clock = {"d": date(2026, 10, 12)}
    first = [date(2026, 10, 8), PREV]                 # what download(end=2026-10-12) returns: up to Friday
    download = {"FOUR.L": _frame(first), "NVDA": _frame(first)}
    db, upserts = _wire(monkeypatch, download, {})
    monkeypatch.setattr(price_audit, "_today", lambda: clock["d"])
    real = price_audit.audit_ticker

    def _tick(*a, **k):
        out = real(*a, **k)
        clock["d"] = date(2026, 10, 13)               # midnight passes after the first instrument
        return out
    monkeypatch.setattr(price_audit, "audit_ticker", _tick)
    price_audit.run("daily", 30, "YF", use_ig=False)
    notes = db.inserts[-1]["no"]
    assert "missing after first fetch 1/2 (NASDAQ 100 1)" in notes, notes
    assert "prev-day bar 2026-10-09/2026-10-12" in notes, notes
