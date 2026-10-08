#!/usr/bin/env python3
# ======================================================================================================================
# File:         test_volscore_store.py
# Created:      2026-10-07
#
# volscore_store exists to stop the web tier pulling raw bars to compute three numbers per trigger, so the test that
# matters is a COST test asserted as a count: with the features stored, _volscore_scored must call _perf_bars ZERO
# times. MEASURED before it: 400,000 rows for the 1-year window and 1,102,245 for the 3-year one, per rebuild.
# ======================================================================================================================

import datetime as dt

import pytest

import volscore_store


def _row(ticker="AAA", trig="2026-09-01", direction="BULLISH", quality=70):
    return {"ticker": ticker, "trig_date": trig, "entry": 10.0, "direction": direction,
            "quality": quality, "name": f"{ticker} Co", "market": "FTSE 100", "sector": "X",
            "location": "UK", "timeframe": "daily-1", "rr": 3.0, "current_price": 11.0}


class _FakeDb:
    """Records every statement so a test can assert what was issued, not just what came back."""

    def __init__(self, rows=()):
        self.statements = []
        self._rows = list(rows)

    def run(self, sql, **params):
        self.statements.append((sql, params))
        if "select ticker, trig_date" in sql:
            return self._rows
        return []

    def close(self):
        pass


def test_load_returns_one_entry_per_trigger(monkeypatch):
    db = _FakeDb([("AAA", dt.date(2026, 9, 1), 6.0, True, False)])
    got = volscore_store.load("2026-01-01", db=db)
    assert got == {("AAA", "2026-09-01"): {"volume_score": 6.0, "above_vwap": True, "atr_expanding": False}}


def test_load_degrades_to_empty_rather_than_raising(monkeypatch):
    """A missing or broken store must make the caller SLOW, never WRONG -- it falls back to computing."""
    class _Boom:
        def run(self, *a, **k):
            raise RuntimeError("relation does not exist")

        def close(self):
            pass

    assert volscore_store.load("2026-01-01", db=_Boom()) == {}


def test_store_batches_rather_than_one_statement_per_row():
    """17,600 single-row upserts is the per-row defect this repo has already paid for twice
    (squeeze_history.refresh_daily was 4,287 round trips, instrument_metrics.record_daily 3,545)."""
    db = _FakeDb()
    rows = [{"ticker": f"T{i}", "trig_date": "2026-09-01", "volume_score": 5,
             "above_vwap": True, "atr_expanding": False} for i in range(1200)]

    written = volscore_store.store(rows, db=db)

    assert written == 1200
    inserts = [s for s, _ in db.statements if "insert into" in s]
    assert len(inserts) == 3, f"1200 rows at 500 per batch should be 3 statements, got {len(inserts)}"


def test_store_skips_rows_with_no_ticker_or_date():
    db = _FakeDb()
    assert volscore_store.store([{"ticker": None, "trig_date": "2026-09-01"},
                                 {"ticker": "AAA", "trig_date": None}], db=db) == 0


def test_volscore_scored_reads_no_bars_when_the_store_covers_the_window(monkeypatch):
    """THE COST TEST, and the reason this module exists. Asserted as a count of _perf_bars calls."""
    import hvf_web.server as S

    calls = {"perf_bars": 0}
    monkeypatch.setattr(S, "_perf_bars",
                        lambda *a, **k: calls.__setitem__("perf_bars", calls["perf_bars"] + 1) or {})
    monkeypatch.setattr(S, "_sqa_all_rows", lambda: [_row("AAA", "2026-09-01"), _row("BBB", "2026-09-02")])
    monkeypatch.setattr(volscore_store, "load", lambda cutoff, db=None: {
        ("AAA", "2026-09-01"): {"volume_score": 6, "above_vwap": True, "atr_expanding": False},
        ("BBB", "2026-09-02"): {"volume_score": 9, "above_vwap": False, "atr_expanding": True},
    })
    S._VSCORED_CACHE.clear()

    scored = S._volscore_scored(1)

    assert calls["perf_bars"] == 0, "the store covered every trigger; no bars should have been read"
    by = {r["ticker"]: r for r in scored}
    assert by["AAA"]["volume_score"] == 6 and by["AAA"]["above_vwap"] is True
    assert by["BBB"]["volume_score"] == 9 and by["BBB"]["atr_expanding"] is True
    S._VSCORED_CACHE.clear()


def test_only_the_triggers_the_store_lacks_cause_a_bar_fetch(monkeypatch):
    """A trigger created today must still be scored, and must not drag the rest back through the bars.

    Fetching for ALL triggers whenever ANY was missing would throw the whole saving away on every day
    that produced a trigger -- which is most days -- so the cutoff map is built from the gaps only.
    """
    import hvf_web.server as S

    seen = {}

    def _bars(db, cut, lookback_days=0):
        seen.update(cut)
        return {}

    monkeypatch.setattr(S, "_perf_bars", _bars)
    monkeypatch.setattr(S, "_sqa_all_rows", lambda: [_row("OLD", "2026-09-01"), _row("NEW", "2026-09-30")])
    monkeypatch.setattr(volscore_store, "load", lambda cutoff, db=None: {
        ("OLD", "2026-09-01"): {"volume_score": 6, "above_vwap": True, "atr_expanding": False}})
    # "db_pool.get_db", NOT setattr(S, "get_db"). _volscore_scored does `from db_pool import get_db`
    # INSIDE the function, so the name resolves against db_pool at call time; setting an attribute on the
    # server module just creates an unused one and the REAL connection is opened. That is what reached
    # Supabase from CI on run 37696686522 and failed with ENOIDENTIFIER on placeholder credentials.
    monkeypatch.setattr("db_pool.get_db", lambda: _FakeDb())
    S._VSCORED_CACHE.clear()

    S._volscore_scored(1)

    assert list(seen) == ["NEW"], f"bars were fetched for {list(seen)}; only the uncovered trigger should be"
    S._VSCORED_CACHE.clear()


def test_use_store_false_forces_a_recompute(monkeypatch):
    """The WRITER must not read the store, or a trigger created today would never be scored at all."""
    import hvf_web.server as S

    calls = {"load": 0, "perf_bars": 0}
    monkeypatch.setattr(volscore_store, "load",
                        lambda cutoff, db=None: calls.__setitem__("load", calls["load"] + 1) or {})
    monkeypatch.setattr(S, "_perf_bars",
                        lambda *a, **k: calls.__setitem__("perf_bars", calls["perf_bars"] + 1) or {})
    monkeypatch.setattr(S, "_sqa_all_rows", lambda: [_row("AAA", "2026-09-01")])
    monkeypatch.setattr("db_pool.get_db", lambda: _FakeDb())   # or the real connection is opened
    S._VSCORED_CACHE.clear()

    S._volscore_scored(1, use_store=False)

    assert calls["load"] == 0, "the writer must not consult the store it is about to fill"
    assert calls["perf_bars"] == 1, "the writer is the one place that should pay for the bars"
    S._VSCORED_CACHE.clear()


def test_store_survives_a_trigger_repeated_across_lookback_windows():
    """Precompute run 37813598761, 2026-10-08: every store failed with Postgres's "ON CONFLICT DO UPDATE
    command cannot affect row a second time", because squeeze_history carries one trigger under several
    lookback windows and the batch repeated the key. The fake refuses exactly what Postgres refuses."""
    class _PgLike(_FakeDb):
        def run(self, sql, **params):
            if "insert into" in sql:
                keys = [(params[k], params["d" + k[1:]]) for k in params if k.startswith("t")]
                if len(keys) != len(set(keys)):
                    raise RuntimeError("ON CONFLICT DO UPDATE command cannot affect row a second time")
            return super().run(sql, **params)

    db = _PgLike()
    row = {"ticker": "ARM", "trig_date": "2026-09-18", "volume_score": 6, "above_vwap": True, "atr_expanding": True}
    written = volscore_store.store([row, dict(row), {**row, "ticker": "ZS"}], db=db)
    assert written == 2
