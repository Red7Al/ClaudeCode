"""2026-10-26: 68 IG codes were each linked to several tickers. Two causes. social_monitor stored IG's FIRST search hit for
anything after a $ on X (APPX -> Apple's code, LIBRA -> GBP/USD, TSMC -> TomCo Energy). And get_epic's name check let
a non-London ticker take a London share code when one word matched (PRU, US Prudential Financial -> Prudential plc).
Even the same company's other listing is wrong for an order: 0005.HK closed 142.50 HKD the day HSBA.L closed 1,352.00p.

Fixture rows are the live epic_lookup rows and IG names of 2026-10-26."""
import ig_shim


class _Db:
    def __init__(self, cached=None):
        self.cached, self.sql = cached, []

    def run(self, sql, **kw):
        self.sql.append(sql)
        if sql.startswith("select epic, description from epic_lookup"):
            return [self.cached] if self.cached else []
        return []

    def close(self):
        pass


def _wire(monkeypatch, db, markets, yahoo_name):
    monkeypatch.setattr(ig_shim, "get_db", lambda: db)
    monkeypatch.setattr(ig_shim, "_heuristic_epic", lambda t: None)
    monkeypatch.setattr(ig_shim, "_yahoo_name", lambda t: yahoo_name)
    monkeypatch.setattr(ig_shim.session, "get", lambda path, params=None, version=None: {"markets": markets})


PRU_PLC = {"epic": "KA.D.PRU.DAILY.IP", "instrumentName": "Prudential PLC", "expiry": "DFB", "instrumentType": "SHARES"}


def test_a_us_ticker_never_takes_a_london_share_code_from_the_search(monkeypatch):
    db = _Db()
    _wire(monkeypatch, db, [PRU_PLC], "Prudential Financial, Inc.")
    assert ig_shim.get_epic("PRU") is None
    assert not [s for s in db.sql if s.lstrip().lower().startswith("insert into epic_lookup")], "a wrong row was cached"


def test_a_cached_wrong_listing_is_refused_and_not_deleted(monkeypatch):
    """Deleting the existing rows is the owner's decision; the code refuses them meanwhile."""
    db = _Db(cached=("KA.D.FOUR.DAILY.IP", "4imprint Group PLC"))
    _wire(monkeypatch, db, [], "Shift4 Payments, Inc.")
    assert ig_shim.get_epic("FOUR") is None
    assert not [s for s in db.sql if "delete" in s.lower()]


def test_a_london_ticker_still_takes_its_london_code(monkeypatch):
    db = _Db()
    _wire(monkeypatch, db, [PRU_PLC], "Prudential plc")
    assert ig_shim.get_epic("PRU.L") == "KA.D.PRU.DAILY.IP"


def test_the_social_feed_uses_the_one_resolver(monkeypatch):
    """It used to store IG's first search hit with currency forced to USD."""
    import social_monitor
    asked, writes = [], []
    monkeypatch.setattr(social_monitor, "_ensure_direction_column", lambda: None)
    monkeypatch.setattr(social_monitor, "db_run",
                        lambda sql, **kw: writes.append(sql) or ([] if sql.lstrip().startswith("select") else None))
    monkeypatch.setattr(ig_shim.session, "ensure_authenticated", lambda: None)
    monkeypatch.setattr(ig_shim, "get_epic", lambda t: asked.append(t) or None)
    monkeypatch.setattr(ig_shim.session, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("searched IG itself")))
    social_monitor.save_new_pick("APPX", "someone", "rss", "https://x.com/a/status/1")
    assert asked == ["APPX"]
    assert not [s for s in writes if "insert into epic_lookup" in s]
