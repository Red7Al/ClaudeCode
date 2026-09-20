"""The price_history bar cache, keyed on the DATA's version rather than on a clock.

WHY THIS FILE EXISTS. Supabase's free tier allows 5 GB of egress a MONTH, shared across the whole
organisation and every service. Measured from pg_stat_statements on 2026-09-20, never reset since the
project was created 112 days earlier, price_history reads ran at ~36.5 GB per 30 days -- 7.3x the
allowance -- and _perf_bars alone was 1,379,932,644 rows over 9,016 calls, ~30 GB of it. That is what
latched Supabase Storage off with exceed_egress_quota on 2026-08-16.

price_history is written twice a day (the 05:00 refresh and the 18:30 scan), so nearly every one of
those fetches pulled bars identical to the previous fetch. These tests hold the fix in place.
"""

import datetime as dt

import hvf_web.server as server

D = dt.date(2026, 9, 18)


class _Db:
    """Counts the BULK bar fetches separately from the cheap version probe."""

    def __init__(self, version=("2026-09-18", 10262), bars=None):
        self.version = version
        self.bars = bars if bars is not None else [("ABC", D, 2.0, 1.0, 1.5, 100)]
        self.bulk = 0
        self.probes = 0

    def run(self, sql, **kw):
        if "max(bar_date)" in sql:
            self.probes += 1
            return [[self.version[0], self.version[1]]] if self.version else []
        self.bulk += 1
        return self.bars

    def close(self):
        pass


def _reset():
    server._PB_CACHE.clear()
    server._PB_ORDER.clear()
    server._PB_VERSION.update(at=0.0, value="")


def test_identical_bars_are_not_fetched_twice(monkeypatch):
    _reset()
    db = _Db()
    cut = {"ABC": D}

    first = server._perf_bars(db, cut, lookback_days=40)
    second = server._perf_bars(db, cut, lookback_days=40)

    assert db.bulk == 1, f"the bars were pulled {db.bulk} times for one unchanged version"
    assert first == second
    assert first is second, "a hit must reuse the stored dict, not rebuild it"


def test_a_write_to_price_history_invalidates_the_cache(monkeypatch):
    """THE HALF THAT A TTL CANNOT DO. Serving yesterday's bars after the 05:00 refresh would be a wrong
    answer, not a slow one, so the version moving MUST force a refetch."""
    _reset()
    db = _Db()
    cut = {"ABC": D}
    server._perf_bars(db, cut, lookback_days=40)

    db.version = ("2026-09-19", 1773)          # the refresh landed a new bar
    server._PB_VERSION.update(at=0.0, value="")   # probe TTL expires
    server._perf_bars(db, cut, lookback_days=40)

    assert db.bulk == 2, "a new price_history version must refetch, not serve the previous bars"


def test_more_tickers_for_the_same_bar_also_invalidate(monkeypatch):
    """A bare max(bar_date) would MISS this: same latest bar, but more instruments landed against it.
    That is why the probe carries a count as well as a date."""
    _reset()
    db = _Db(version=("2026-09-18", 900))
    cut = {"ABC": D}
    server._perf_bars(db, cut, lookback_days=40)

    db.version = ("2026-09-18", 1773)          # same bar_date, more tickers
    server._PB_VERSION.update(at=0.0, value="")
    server._perf_bars(db, cut, lookback_days=40)

    assert db.bulk == 2, "more rows for the same bar must still invalidate"


def test_a_different_request_is_never_served_from_another_entry(monkeypatch):
    """Serving one caller's bars to a caller that asked for different tickers or a different lookback
    would silently truncate a window -- the same class of bug _window_bars guards with its coverage
    check. Keys must separate them."""
    _reset()
    db = _Db()
    server._perf_bars(db, {"ABC": D}, lookback_days=40)
    server._perf_bars(db, {"ABC": D}, lookback_days=160)   # different lookback
    server._perf_bars(db, {"XYZ": D}, lookback_days=40)    # different ticker

    assert db.bulk == 3, "each distinct request must fetch its own bars"


def test_ticker_order_does_not_split_the_cache(monkeypatch):
    """The cutoff arrives as a dict, whose order follows the caller's construction. Two callers asking
    for the SAME tickers must share one entry, or the warm loop misses every time and the saving is
    lost. The query carries its own ORDER BY, so sorting cannot affect the result."""
    _reset()
    db = _Db()
    server._perf_bars(db, {"ABC": D, "XYZ": D}, lookback_days=40)
    server._perf_bars(db, {"XYZ": D, "ABC": D}, lookback_days=40)

    assert db.bulk == 1, "the same request written in a different order must hit the cache"


def test_the_cache_is_bounded(monkeypatch):
    """This runs on shared hosting. Measured live 2026-09-20 the largest entry is ~10.3 MB, so the
    ceiling is tens of MB -- but only because it is enforced."""
    _reset()
    db = _Db()
    for i in range(server._PB_MAX_ENTRIES + 3):
        server._perf_bars(db, {f"T{i}": D}, lookback_days=40)

    assert len(server._PB_CACHE) <= server._PB_MAX_ENTRIES
    assert len(server._PB_ORDER) <= server._PB_MAX_ENTRIES


def test_an_unreadable_version_disables_the_cache_rather_than_guessing(monkeypatch):
    """Failing to an extra fetch costs egress. Failing to a stale answer costs a wrong number on the
    site. When the probe cannot answer, take the first one."""
    _reset()

    class _Broken(_Db):
        def run(self, sql, **kw):
            if "max(bar_date)" in sql:
                raise RuntimeError("probe down")
            return super().run(sql, **kw)

    db = _Broken()
    cut = {"ABC": D}
    server._perf_bars(db, cut, lookback_days=40)
    server._perf_bars(db, cut, lookback_days=40)

    assert db.bulk == 2, "with no version to key on, every call must fetch"
    assert not server._PB_CACHE, "nothing may be cached under an unknown version"


def test_an_empty_cutoff_still_short_circuits(monkeypatch):
    _reset()
    db = _Db()

    assert server._perf_bars(db, {}, lookback_days=40) == {}
    assert db.bulk == 0 and db.probes == 0, "no tickers means no database work at all"
