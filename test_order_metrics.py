import order_metrics


class _Db:
    def __init__(self, bars, mcap):
        self.bars = bars
        self.mcap = mcap
        self.closed = False

    def run(self, sql, **params):
        if "from price_history" in sql:
            return list(reversed(self.bars))
        if "from instrument_mcap" in sql:
            return [(self.mcap,)]
        raise AssertionError(sql)

    def close(self):
        self.closed = True


def test_live_order_metrics_reads_current_supabase_bar_and_mcap(monkeypatch):
    bars = []
    for day in range(1, 81):
        close = 100.0 + day
        bars.append((f"2026-01-{day:02d}", close + 1, close - 1, close, 1_000 + day * 10))
    db = _Db(bars, 2_500_000_000)
    import db_pool
    import sector_cache
    monkeypatch.setattr(db_pool, "get_db", lambda: db)
    monkeypatch.setattr(sector_cache, "get_sector", lambda ticker: "Technology")
    order_metrics._CACHE.clear()

    result = order_metrics.live_order_metrics("TEST", bull=True, quality=75)

    assert result["metric_date"] == "2026-01-80"
    assert result["volume_score"] is not None
    assert result["rvol"] is not None
    assert result["mcap"] == 2_500_000_000
    assert result["sector"] == "Technology"
    assert db.closed is True
