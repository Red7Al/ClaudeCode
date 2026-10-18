"""A Yahoo daily high or low its own hourly bars contradict is never stored.

WHY (owner 2026-10-18: "If there is a trigger it must stay in the history of triggers or I cannot trust what
you are telling me"). MEASURED: Yahoo's daily bar for TSCO.L on 2026-10-15 carried a high of 622.5 with a
close of 534.6; its 5-minute and hourly bars for that day peak at 535.6. The bad high crossed the 541.00
target of TSCO.L's 8 Oct trigger, squeeze_history marked it TARGET, and it left the Scanner Report and the
morning email. Ten FTSE 100 bars carried the same print that day; nothing checked them, and the nightly
refresh re-wrote 622.5 every night because Yahoo still serves it.

Fixtures are the stored price_history row for TSCO.L 2026-10-15 and the hourly extremes measured for it.
"""
from datetime import date

import pandas as pd

import price_store

D = date(2026, 10, 15)
TSCO_ROW = (D, 526.400024414062, 622.5, 525.799987792969, 534.599975585938, 1000)   # price_history, as stored
TSCO_HOURLY = {D: (535.5999755859375, 525.799988, 533.400024)}                         # yfinance 1h, measured


def test_a_high_the_hourly_bars_contradict_is_replaced_and_reported():
    rows, fixes = price_store._verify_wicks("TSCO.L", "TSCO.L", [TSCO_ROW], intraday=lambda s, d: TSCO_HOURLY)
    assert rows[0][2] == TSCO_HOURLY[D][0]            # 535.6, below the 541.00 target -- the trade is still open
    assert rows[0][3] == TSCO_ROW[3]                  # the low agreed, untouched
    assert fixes == [(D, "high", 622.5, TSCO_HOURLY[D][0])]


def test_a_wick_the_hourly_bars_confirm_is_kept():
    real = {D: (622.0, 525.8, 534.0)}
    rows, fixes = price_store._verify_wicks("TSCO.L", "TSCO.L", [TSCO_ROW], intraday=lambda s, d: real)
    assert rows == [TSCO_ROW] and fixes == []


def test_an_unverifiable_wick_is_stored_as_given_and_an_ordinary_bar_costs_no_request():
    asked = []
    rows, fixes = price_store._verify_wicks("TSCO.L", "TSCO.L", [TSCO_ROW], intraday=lambda s, d: asked.append(d) or {})
    assert rows == [TSCO_ROW] and fixes == [] and asked == [{D}]
    plain = (date(2026, 10, 14), 518.6, 527.8, 516.2, 526.4, 1000)
    asked.clear()
    assert price_store._verify_wicks("TSCO.L", "TSCO.L", [plain], intraday=lambda s, d: asked.append(d) or {})[0] == [plain]
    assert asked == []


class _DB:
    def __init__(self):
        self.sql = []

    def run(self, sql, **k):
        self.sql.append((sql, k))
        return []


def test_upsert_writes_the_corrected_high_and_keeps_the_original(monkeypatch):
    monkeypatch.setattr(price_store, "_intraday_extremes", lambda s, d: TSCO_HOURLY)
    monkeypatch.setattr(price_store, "ensure_schema", lambda db: None)
    db = _DB()
    df = pd.DataFrame({"Open": [TSCO_ROW[1]], "High": [TSCO_ROW[2]], "Low": [TSCO_ROW[3]], "Close": [TSCO_ROW[4]],
                       "Volume": [TSCO_ROW[5]]}, index=pd.to_datetime([D]))
    price_store.upsert_bars("TSCO.L", df, "YF", db=db, yahoo_symbol="TSCO.L")
    corr = [k for s, k in db.sql if "price_corrections" in s]
    assert corr == [dict(t="TSCO.L", d=D, f="high", o=622.5, n=TSCO_HOURLY[D][0])]
    bar = [k for s, k in db.sql if "insert into price_history" in s][0]
    assert bar["h0"] == TSCO_HOURLY[D][0]


def test_ig_bars_are_not_second_guessed(monkeypatch):
    monkeypatch.setattr(price_store, "_verify_wicks", lambda *a, **k: (_ for _ in ()).throw(AssertionError("checked")))
    monkeypatch.setattr(price_store, "ensure_schema", lambda db: None)
    df = pd.DataFrame({"Open": [1.0], "High": [2.0], "Low": [1.0], "Close": [1.0], "Volume": [1]}, index=pd.to_datetime([D]))
    assert price_store.upsert_bars("X", df, "IG", db=_DB()) == 1



def test_markets_without_a_proven_hourly_referee_are_left_as_yahoo_gives_them():
    """2026-10-18: the backfill changed 42 Japanese / Australian lows on hourly bars proven unreliable there.
    Fixture: NWS.AX 2026-07-10 from price_history (open/high/close/volume) with the low Yahoo gave
    (price_corrections.original 41.0325...), and the hourly low it was moved to (44.0200...)."""
    d = date(2026, 7, 10)
    row = (d, 44.5350423660742, 45.023997667441, 41.0325084180364, 45.0040397644043, 111946)
    asked = []
    rows, fixes = price_store._verify_wicks(
        "NWS.AX", "NWS.AX", [row],
        intraday=lambda s, ds: asked.append(ds) or {d: (45.023997667441, 44.0200004577637, 45.0040397644043)})
    assert rows == [row] and fixes == [] and asked == []
