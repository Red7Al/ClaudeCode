"""2026-10-26: 68 IG codes were each linked to several tickers. Two causes. social_monitor stored IG's FIRST search hit for
anything after a $ on X (APPX -> Apple's code, LIBRA -> GBP/USD, TSMC -> TomCo Energy). And get_epic's name check let
a non-London ticker take a London share code when one word matched (PRU, US Prudential Financial -> Prudential plc).
Even the same company's other listing is wrong for an order: 0005.HK closed 142.50 HKD the day HSBA.L closed 1,352.00p.

Fixture rows are the live epic_lookup rows and IG names of 2026-10-26."""
import ig_shim


class _Db:
    def __init__(self, cached=None, bars=()):
        self.cached, self.bars, self.sql = cached, list(bars), []

    def run(self, sql, **kw):
        self.sql.append(sql)
        if sql.startswith("select epic, description from epic_lookup"):
            return [self.cached] if self.cached else []
        if "from price_history" in sql:
            return self.bars
        return []

    def close(self):
        pass


def _wire(monkeypatch, db, markets, yahoo_name):
    monkeypatch.setattr(ig_shim, "get_db", lambda: db)
    monkeypatch.setattr(ig_shim, "_EPIC_VERIFIED_OVERRIDES", {})     # these tests are about the search, not the pins
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


# IG's search for "APA" and "ICGT", and the price bars file's closes, both 2026-10-26 (bar of 2026-10-23).
APA_GROUP = {"epic": "AA.D.APA.DAILY.IP", "instrumentName": "APA Group", "instrumentType": "SHARES", "expiry": "DFB",
             "bid": 1148.0, "offer": 1148.0}
APACHE = {"epic": "SA.D.APA.DAILY.IP", "instrumentName": "Apache Corp (24 Hours)", "instrumentType": "SHARES",
          "expiry": "DFB", "bid": 5167.8, "offer": 5179.2}
ICG_PLC = {"epic": "KA.D.ICP.DAILY.IP", "instrumentName": "ICG PLC", "instrumentType": "SHARES", "expiry": "DFB",
           "bid": 1799.0, "offer": 1799.0}


def _bar(close):
    import datetime as dt
    return [(dt.date.today(), close, close, close, close, 0)]


def test_a_same_named_listing_at_another_price_is_refused(monkeypatch):
    """APA scored APA Group and Apache equal. The live name "APA Corporation" matches APA Group and not "Apache Corp",
    so APA now gets no code rather than the Australian one."""
    db = _Db(bars=_bar(51.9000015258789))
    _wire(monkeypatch, db, [APA_GROUP, APACHE], "APA Corporation")
    assert ig_shim.get_epic("APA") is None
    assert not [s for s in db.sql if s.lstrip().lower().startswith("insert into epic_lookup")], "a wrong row was cached"


def test_of_the_named_listings_the_one_at_the_price_is_taken(monkeypatch):
    db = _Db(bars=_bar(51.9000015258789))
    _wire(monkeypatch, db, [APA_GROUP, APACHE], "APA Corporation")
    monkeypatch.setattr(ig_shim, "instrument_names_match", lambda *a: True)
    assert ig_shim.get_epic("APA") == "SA.D.APA.DAILY.IP"


def test_apa_is_pinned_to_apache():
    """Owner 2026-10-27: "yes" to the pin -- the search refuses APA (above), so only the pin gives it a code."""
    assert ig_shim.get_epic("APA") == APACHE["epic"]


def test_no_listing_near_the_price_is_refused_and_not_cached(monkeypatch):
    db = _Db(bars=_bar(1374.0))
    _wire(monkeypatch, db, [ICG_PLC], "ICG Enterprise Trust PLC")
    monkeypatch.setattr(ig_shim, "instrument_names_match", lambda *a: True)   # the name check let it through live
    assert ig_shim.get_epic("ICGT.L") is None
    assert not [s for s in db.sql if s.lstrip().lower().startswith("insert into epic_lookup")], "a wrong row was cached"


def test_without_a_stored_close_the_search_is_unchanged(monkeypatch):
    """A social pick that is never scanned has no close; nothing to check against."""
    db = _Db()
    _wire(monkeypatch, db, [PRU_PLC], "Prudential plc")
    assert ig_shim.get_epic("PRU.L") == "KA.D.PRU.DAILY.IP"
