"""Correcting a price must not destroy that bar's volume.

WHY (all measured 2026-09-26). The IG-as-truth cross-check overwrites a stored bar when IG disagrees with
Yahoo by more than the tolerance. It built the replacement frame with `fdf["Volume"] = float("nan")`, and
price_store.upsert_bars sets `volume=excluded.volume` unconditionally, so every price correction NULLED the
bar volume.

Not writing IG's OWN volume is correct -- it is not comparable with Yahoo's and for some instruments IG
reports none. Discarding the volume ALREADY STORED was a different decision, and it is the one that
shipped.

WHAT IT COST. Of the 5,102 IG-source bars inside the trailing 30 days, 5,102 had NULL volume and 0 had any;
all 31,484 YF bars in the same window had it. A single pass on 2026-09-26 stripped volume from 5,116 bars
across 1,127 of 1,773 tickers -- 64% of the universe -- because on a non-trading day the cross-check finds a
discrepancy on nearly every ticker (5 IG fixes on the Friday, 4,938 on the Saturday, at the same point in
the run). RVOL and VolumeScore are computed FROM volume, and blank RVOL/VolumeScore columns are the owner's
standing complaint. The correction was breaking the volume metrics on the very bar it corrected.

AND FOR NOTHING DURABLE: 4 of the 5,126 IG bars ever written predate 2026-09-19, because each daily YF pass
re-fetches the trailing window and overwrites them. The volume was destroyed for a price change the next
pass undid.

These tests pin the split: IG decides the price, Yahoo remains the only source of volume.
"""

import pandas as pd
import pytest

import price_audit


class _Store:
    """Stands in for price_store, capturing what the correction would write."""

    def __init__(self, stored):
        self.stored = stored
        self.upserted = None
        self.double_checked = []

    def get_bars(self, ticker, start=None, end=None, db=None):
        return self.stored

    def upsert_bars(self, ticker, df, source, db=None):
        self.upserted = df.copy()
        return len(df)

    def set_double_checked(self, ticker, dates, db=None):
        self.double_checked = list(dates)
        return len(dates)


def _frame(closes, volumes=None):
    idx = pd.to_datetime([f"2026-09-{d:02d}" for d in range(14, 14 + len(closes))])
    data = {"Open": closes, "High": closes, "Low": closes, "Close": closes}
    if volumes is not None:
        data["Volume"] = volumes
    return pd.DataFrame(data, index=idx)


def _wire(monkeypatch, stored_closes, ig_closes, stored_volumes):
    store = _Store(_frame(stored_closes, stored_volumes))
    monkeypatch.setattr(price_audit, "price_store", store)
    monkeypatch.setitem(__import__("sys").modules, "ig_shim_stub", None)

    import ig_shim
    monkeypatch.setattr(ig_shim, "get_epic", lambda t: "EPIC.X", raising=False)
    monkeypatch.setattr(ig_shim, "get_prices_df",
                        lambda epic, resolution=None, count=None: (_frame(ig_closes), 9999),
                        raising=False)
    return store


def test_a_corrected_bar_keeps_its_stored_volume(monkeypatch):
    """THE DEFECT. The third bar disagrees by 10%, well over the tolerance, so it is corrected -- and its
    volume must survive the correction."""
    store = _wire(monkeypatch,
                  stored_closes=[100.0, 101.0, 102.0],
                  ig_closes=[100.0, 101.0, 112.0],
                  stored_volumes=[1_000, 2_000, 3_000])
    corrected, _dc, _rem = price_audit._ig_verify("AAA.L", db=None)
    assert corrected == 1, "the disagreeing bar should have been corrected"
    assert store.upserted is not None
    assert "Volume" in store.upserted.columns, "the correction frame must carry a Volume column"
    assert store.upserted["Volume"].isna().sum() == 0, "a corrected bar must not be left without volume"
    assert list(store.upserted["Volume"]) == [3_000], "it must keep the volume already stored"


def test_the_price_is_still_taken_from_ig(monkeypatch):
    """Preserving volume must not weaken the correction itself -- IG remains truth for price."""
    store = _wire(monkeypatch,
                  stored_closes=[100.0, 101.0, 102.0],
                  ig_closes=[100.0, 101.0, 112.0],
                  stored_volumes=[1_000, 2_000, 3_000])
    price_audit._ig_verify("AAA.L", db=None)
    assert float(store.upserted["Close"].iloc[0]) == pytest.approx(112.0), "IG's price must win"


def test_bars_that_agree_are_not_rewritten(monkeypatch):
    """Nothing to correct means nothing written, so no volume is at risk in the common case."""
    store = _wire(monkeypatch,
                  stored_closes=[100.0, 101.0, 102.0],
                  ig_closes=[100.0, 101.0, 102.0],
                  stored_volumes=[1_000, 2_000, 3_000])
    corrected, dc, _rem = price_audit._ig_verify("AAA.L", db=None)
    assert corrected == 0 and store.upserted is None
    assert dc == 3, "all three agreed, so all three are marked double-checked"


def test_several_corrected_bars_each_keep_their_own_volume(monkeypatch):
    """The volumes must be matched per date, not broadcast or reordered."""
    store = _wire(monkeypatch,
                  stored_closes=[100.0, 200.0, 300.0],
                  ig_closes=[150.0, 200.0, 450.0],
                  stored_volumes=[1_111, 2_222, 3_333])
    corrected, _dc, _rem = price_audit._ig_verify("AAA.L", db=None)
    assert corrected == 2
    assert list(store.upserted["Volume"]) == [1_111, 3_333], "each bar keeps ITS OWN volume"


def test_a_wild_disagreement_is_still_refused(monkeypatch):
    """The sanity ceiling must be untouched: a unit or epic mismatch must never rewrite the row, with or
    without volume. 100 -> 100000 is a units error, not a price move."""
    store = _wire(monkeypatch,
                  stored_closes=[100.0, 101.0, 102.0],
                  ig_closes=[100.0, 101.0, 102.0 * (price_audit.IG_SANITY_MAX_DRIFT_PCT / 100 + 2)],
                  stored_volumes=[1_000, 2_000, 3_000])
    corrected, _dc, _rem = price_audit._ig_verify("AAA.L", db=None)
    assert corrected == 0, "a drift past the sanity ceiling must not be written"
    assert store.upserted is None
