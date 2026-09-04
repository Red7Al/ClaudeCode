import order_metrics


class _Db:
    def __init__(self, bars, mcap, currency="GBP"):
        self.bars = bars
        self.mcap = mcap
        self.currency = currency
        self.closed = False

    def run(self, sql, **params):
        if "from price_history" in sql:
            return list(reversed(self.bars))
        if "from instrument_mcap" in sql:
            return [(self.mcap, self.currency)]
        raise AssertionError(sql)

    def close(self):
        self.closed = True


def _bars():
    out = []
    for day in range(1, 81):
        close = 100.0 + day
        out.append((f"2026-01-{day:02d}", close + 1, close - 1, close, 1_000 + day * 10))
    return out


def _run(monkeypatch, db, rates=None):
    import db_pool
    import sector_cache
    import fx_rates
    monkeypatch.setattr(db_pool, "get_db", lambda: db)
    monkeypatch.setattr(sector_cache, "get_sector", lambda ticker: "Technology")
    monkeypatch.setattr(fx_rates, "rates", lambda: dict(rates if rates is not None else {"GBP": 1.0}))
    order_metrics._CACHE.clear()
    return order_metrics.live_order_metrics("TEST", bull=True, quality=75)


def test_live_order_metrics_reads_current_supabase_bar_and_mcap(monkeypatch):
    db = _Db(_bars(), 2_500_000_000)

    result = _run(monkeypatch, db)

    assert result["metric_date"] == "2026-01-80"
    assert result["volume_score"] is not None
    assert result["rvol"] is not None
    assert result["mcap"] == 2_500_000_000
    assert result["sector"] == "Technology"
    assert db.closed is True


# ======================================================================================================
# This mcap goes straight to trading_limits.check_limits in ig_shim -- the last gate before an order is
# sent to IG. Read raw, a JPY instrument was compared against a GBP min_instrument_value floor on a
# number about 150x too large, so it cleared a mega-cap floor on denomination alone (user 2026-09-04:
# "MCAP is expected to be in GBP in our system").
# ======================================================================================================

def test_the_placement_gate_sees_market_cap_in_gbp(monkeypatch):
    result = _run(monkeypatch, _Db(_bars(), 11_573_000_000_000, "JPY"),
                  rates={"GBP": 1.0, "JPY": 0.004722})

    assert 54e9 < result["mcap"] < 56e9, (
        f"JPY 11,573bn is about GBP 55bn, not 11,573bn: got {result['mcap']:,.0f}")


def test_an_unconvertible_currency_blocks_rather_than_passes(monkeypatch):
    """check_limits is called with require_data=True, so None blocks. Better to skip an order than to
    place one on a number we cannot read."""
    result = _run(monkeypatch, _Db(_bars(), 2_500_000_000, "XYZ"), rates={"GBP": 1.0})

    assert result["mcap"] is None
