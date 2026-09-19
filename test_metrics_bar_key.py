"""instrument_metrics_daily is keyed on the BAR, and a bar never captured is backfilled on demand.

THE DEFECT THIS EXISTS TO END (owner 2026-09-19: "RVOL not recorded ... this has been covered so many
times"). The table records what four break-bar measures were ON A BAR, but it was keyed on as_of -- the
day we happened to look. record_daily captures each instrument's LATEST bar at ~03:30 UTC, so ONE capture
wrote rows describing MANY bars: measured on as_of 2026-09-18, ten different bar_dates, only TWO of them
describing the 18th. order_filter_audit.break_state looks a position's opening bar up exactly, so a
position opened that day was judgeable for 2 of 1,772 instruments and everything else reported
"not recorded".

Two things had to be true, and both are tested here: the identity must be the bar, and a bar that was
never captured must be computed and STORED rather than reported as missing data we actually hold.
"""

import datetime as dt

import pytest

import instrument_metrics
import order_filter_audit


class FakeDb:
    """Records statements so the KEY the code writes against can be asserted without a database."""

    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []
        self.statements = []

    def run(self, sql, **params):
        self.statements.append((" ".join(sql.split()), params))
        low = sql.lower()
        if low.lstrip().startswith("select") and "instrument_metrics_daily" in low:
            return self.rows
        return []

    def close(self):
        pass


# ── the identity is the bar ───────────────────────────────────────────────────────────────────────────

def test_the_daily_capture_upserts_on_the_bar_not_on_when_we_looked(monkeypatch):
    """Keyed on as_of, a second capture on a day the bar had not advanced wrote a SECOND row describing
    the same bar -- 5,383 of 22,700 rows by 2026-09-19 -- while a bar captured under a later as_of could
    not be found by anything looking the bar up."""
    db = FakeDb()
    monkeypatch.setattr(instrument_metrics, "ensure_schema", lambda _db: None)
    monkeypatch.setattr(instrument_metrics, "_bars", lambda t, e, d: [("2026-09-17", 2.0, 1.0, 1.5, 10)])
    monkeypatch.setattr(instrument_metrics, "compute",
                        lambda t, bars, direction=None: {"ticker": t, "bar_date": "2026-09-17",
                                                         "status": "complete"})
    instrument_metrics.record_daily({"records": [{"ticker": "AAA.L", "direction": "BULL"}]},
                                    as_of=dt.date(2026, 9, 18), db=db)
    inserts = [s for s, _ in db.statements if s.lower().startswith("insert into")]
    assert inserts, "nothing was written"
    assert "on conflict (ticker, bar_date)" in inserts[0], inserts[0]
    assert "on conflict (ticker, as_of)" not in inserts[0]


def test_the_backfill_upserts_on_the_bar_with_no_conditional_workaround(monkeypatch):
    """The conditional "...do update ... where bar_date = :bd" existed ONLY to stop a write clobbering a
    row describing a different bar under the same key. Re-keying removed the collision; the workaround
    must not survive it, or the next reader will think it is load-bearing."""
    db = FakeDb()
    monkeypatch.setattr(instrument_metrics, "ensure_schema", lambda _db: None)
    monkeypatch.setattr(instrument_metrics, "bars_upto",
                        lambda t, e, d: [("2026-09-18", 2.0, 1.0, 1.5, 10)])
    monkeypatch.setattr(instrument_metrics, "compute",
                        lambda t, bars, direction=None: {"ticker": t, "bar_date": "2026-09-18",
                                                         "status": "complete", "rvol": 1.2})
    out = instrument_metrics.record_for_bar("AAA.L", "2026-09-18", db=db)
    assert out and out["rvol"] == 1.2
    ins = [s for s, _ in db.statements if s.lower().startswith("insert into")][0]
    assert "on conflict (ticker, bar_date)" in ins
    assert "where instrument_metrics_daily.bar_date" not in ins.lower(), \
        "the conditional-upsert workaround is still here"


def test_the_schema_declares_the_bar_as_the_key():
    src = open("instrument_metrics.py", encoding="utf-8").read()
    assert "primary key (ticker, bar_date)" in src
    assert "primary key (ticker, as_of)" not in src
    assert "bar_date          date not null" in src, "a key column cannot be nullable"


# ── a bar never captured is computed and stored ───────────────────────────────────────────────────────

def test_a_bar_that_was_never_captured_is_backfilled_and_stored(monkeypatch):
    """Measured rescue for 2026-09-18: 1,102 instruments whose bar price_history already held but the
    metrics table did not."""
    db = FakeDb(rows=[])                       # no metrics row for that bar
    called = {}

    def fake_record(ticker, bar_date, direction=None, db=None):
        called["args"] = (ticker, bar_date)
        return {"rvol": 3.14, "above_vwap_setup": False, "atr_expanding": True,
                "volume_score": 7, "status": "complete"}

    monkeypatch.setattr(instrument_metrics, "record_for_bar", fake_record)
    out = order_filter_audit.break_state([("AAA.L", "2026-09-18")], db=db)

    assert called["args"] == ("AAA.L", "2026-09-18"), "the missing bar was not backfilled"
    assert out["AAA.L"]["rvol"] == 3.14
    assert out["AAA.L"]["volume_score"] == 7


def test_an_instrument_that_did_not_trade_stays_unjudgeable(monkeypatch):
    """NOT a failure. With no bar there is no break, so "not recorded" is the honest answer and the caller
    must keep treating it as unjudgeable -- never as a pass. This is the line between the defect and the
    truth, and inventing a number here would close real positions on evidence that does not exist."""
    db = FakeDb(rows=[])
    monkeypatch.setattr(instrument_metrics, "record_for_bar",
                        lambda ticker, bar_date, direction=None, db=None: None)
    out = order_filter_audit.break_state([("AAA.L", "2026-09-18")], db=db)
    assert "AAA.L" not in out, "an instrument with no bar must not appear as judgeable"


def test_the_backfill_refuses_a_bar_the_instrument_does_not_have(monkeypatch):
    """compute() measures at the LAST bar it is given, so asking for a date the instrument did not trade
    returns the previous bar. Storing that under the requested date would be a fabricated measurement."""
    db = FakeDb()
    monkeypatch.setattr(instrument_metrics, "ensure_schema", lambda _db: None)
    monkeypatch.setattr(instrument_metrics, "bars_upto",
                        lambda t, e, d: [("2026-09-16", 2.0, 1.0, 1.5, 10)])
    monkeypatch.setattr(instrument_metrics, "compute",
                        lambda t, bars, direction=None: {"ticker": t, "bar_date": "2026-09-16",
                                                         "status": "complete"})
    assert instrument_metrics.record_for_bar("AAA.L", "2026-09-18", db=db) is None
    assert not [s for s, _ in db.statements if s.lower().startswith("insert into")], \
        "nothing may be stored for a bar the instrument does not have"


# ── the backfill must stay runnable on the web host ───────────────────────────────────────────────────

def test_the_bar_reader_never_pulls_numpy_back_in():
    """bars_upto exists because _bars goes through price_store, which imports pandas at module level --
    and numpy is SIGSYS-killed on the IONOS host, so it would take the worker down rather than degrade.
    This backfill runs inside a web request, so it must stay numpy-free."""
    import ast
    import inspect
    import textwrap
    fn = ast.parse(textwrap.dedent(inspect.getsource(instrument_metrics.bars_upto))).body[0]
    if ast.get_docstring(fn):
        fn.body = fn.body[1:]
    code = ast.unparse(fn)
    for banned in ("price_store", "pandas", "numpy"):
        assert banned not in code, f"bars_upto must not use {banned}"


@pytest.mark.live_state
def test_the_live_table_is_keyed_on_the_bar():
    """The migration is only real if the live table carries it."""
    from db_pool import get_db
    db = get_db()
    try:
        idx = db.run("select indexdef from pg_indexes where tablename='instrument_metrics_daily' "
                     "and indexdef like '%UNIQUE%'") or []
        defs = " ".join(r[0] for r in idx)
        assert "(ticker, bar_date)" in defs, defs
        dupes = db.run("select count(*) from (select ticker, bar_date from instrument_metrics_daily "
                       "group by ticker, bar_date having count(*) > 1) x")[0][0]
        assert dupes == 0
    finally:
        db.close()
