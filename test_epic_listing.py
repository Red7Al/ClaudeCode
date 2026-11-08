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


# 2026-11-08: IG does not index Tokyo codes -- a search for '5233.T' returns nothing -- and get_epic returned None
# there, before its company-name search. 5233.T and 7211.T passed every limit on 2026-11-06 and got no order; 77 of 228
# Nikkei tickers had no epic. Fixtures: IG's answers to the name searches and the stored closes, both 2026-11-08.
def _jt(code, name, bid, expiry="DFB", month="DAILY"):
    return {"epic": f"AJ.D.{code}JT.{month}.IP", "instrumentName": name, "instrumentType": "SHARES",
            "expiry": expiry, "bid": bid, "offer": bid}


TAIHEIYO = [_jt("5233", "Taiheiyo Cement Corp", 4236.0), _jt("5233", "Taiheiyo Cement Corp", 4228.0, "DEC-26", "DEC")]
SOFTBANK = [_jt("9434", "SoftBank Corporation", 235.4), _jt("9434", "SoftBank Corporation", 234.9, "DEC-26", "DEC"),
            _jt("9984", "SoftBank Group Corporation", 3549.0), _jt("9984", "SoftBank Group Corporation", 3542.0, "DEC-26", "DEC")]
SUBARU_ENTERPRISE = [_jt("9632", "Subaru Enterprise Company Limited", 5050.0, "DEC-26", "DEC")]


def _wire_by_term(monkeypatch, db, by_term, yahoo_name):
    """IG answers only the searches in by_term; every other term finds nothing, as '5233.T' does."""
    _wire(monkeypatch, db, [], yahoo_name)
    asked, alerts = [], []
    monkeypatch.setattr(ig_shim.session, "get", lambda path, params=None, version=None:
                        asked.append(params["searchTerm"]) or {"markets": by_term.get(params["searchTerm"], [])})
    import notify
    monkeypatch.setattr(notify, "alert_system_error", lambda *a, **k: alerts.append((a, k)))
    return asked, alerts


def _cached(db):
    return [s for s in db.sql if s.lstrip().lower().startswith("insert into epic_lookup")]


def test_a_tokyo_code_ig_does_not_index_is_found_by_its_name(monkeypatch):
    db = _Db(bars=_bar(4236.0))
    asked, alerts = _wire_by_term(monkeypatch, db, {"taiheiyo cement": TAIHEIYO}, "Taiheiyo Cement Corporation")
    assert ig_shim.get_epic("5233.T") == "AJ.D.5233JT.DAILY.IP"
    assert asked == ["5233.T", "taiheiyo cement"]
    assert _cached(db) and not alerts


def test_of_two_softbanks_the_exact_tokyo_code_is_taken(monkeypatch):
    """'softbank' also finds SoftBank Corp (9434), which passes the one-word name check."""
    db = _Db(bars=_bar(3549.0))
    _wire_by_term(monkeypatch, db, {"softbank": SOFTBANK}, "SoftBank Group Corp.")
    assert ig_shim.get_epic("9984.T") == "AJ.D.9984JT.DAILY.IP"


def test_without_a_close_the_exact_tokyo_code_still_decides(monkeypatch):
    db = _Db()
    _wire_by_term(monkeypatch, db, {"softbank": SOFTBANK}, "SoftBank Group Corp.")
    assert ig_shim.get_epic("9984.T") == "AJ.D.9984JT.DAILY.IP"


def test_a_namesake_at_another_price_is_refused_quietly(monkeypatch):
    """7270.T Subaru Corp: IG lists only Subaru Enterprise (9632), at 5,050 against Subaru's 2,718."""
    db = _Db(bars=_bar(2718.0))
    _, alerts = _wire_by_term(monkeypatch, db, {"subaru": SUBARU_ENTERPRISE}, "Subaru Corporation")
    assert ig_shim.get_epic("7270.T") is None
    assert not _cached(db)
    assert alerts and all(k.get("email") is False for _, k in alerts), "the name-only refusal must not email"


def test_nothing_found_by_ticker_or_name_is_none_with_no_alert(monkeypatch):
    db = _Db(bars=_bar(100.0))
    asked, alerts = _wire_by_term(monkeypatch, db, {}, "Nowhere Listed Industries Ltd")
    assert ig_shim.get_epic("9999.T") is None
    assert asked == ["9999.T", "nowhere listed industries"] and not alerts and not _cached(db)


def test_a_ticker_search_with_wrong_results_still_emails_as_before(monkeypatch):
    """Unchanged path: the ticker search found markets, none of them this company."""
    db = _Db()
    _, alerts = _wire_by_term(monkeypatch, db, {"FOUR": [PRU_PLC]}, "Shift4 Payments, Inc.")
    assert ig_shim.get_epic("FOUR") is None
    assert alerts and all(k.get("email", True) is True for _, k in alerts)


def test_the_exact_tokyo_code_outranks_a_rolling_namesake(monkeypatch):
    """The exact code is worth more than DFB: when IG answers only SoftBank Corp's DFB and SoftBank Group's December
    contract (two rows of the real 'softbank' answer), the scanned company's own code is taken, as for any exact
    ticker."""
    db = _Db()
    _wire_by_term(monkeypatch, db, {"softbank": [SOFTBANK[0], SOFTBANK[3]]}, "SoftBank Group Corp.")
    assert ig_shim.get_epic("9984.T") == "AJ.D.9984JT.DEC.IP"


def test_a_name_search_with_no_namesake_is_refused_quietly(monkeypatch):
    """Ticker search empty, name search answers only another company: refused, not cached, the alert is logged not
    emailed, and it lists what the NAME search found (the ticker search found nothing to list)."""
    db = _Db()
    _, alerts = _wire_by_term(monkeypatch, db, {"taiheiyo cement": [PRU_PLC]}, "Taiheiyo Cement Corporation")
    assert ig_shim.get_epic("5233.T") is None
    assert not _cached(db)
    assert len(alerts) == 1 and alerts[0][1].get("email") is False, "the name-only refusal must not email"
    assert "KA.D.PRU.DAILY.IP" in alerts[0][1].get("detail", "")


def test_a_london_ticker_found_only_by_name_off_london_is_refused_quietly(monkeypatch):
    """Before 2026-11-08 an empty ticker search returned None with no alert. The name search must not turn that into a
    new email: a .L ticker whose name search finds only a non-London listing is refused, not cached, not emailed."""
    db = _Db()
    _, alerts = _wire_by_term(monkeypatch, db, {"apache": [APACHE]}, "Apache Holdings plc")
    assert ig_shim.get_epic("APAX.L") is None
    assert not _cached(db)
    assert all(k.get("email", True) is False for _, k in alerts), "the name-only refusal must not email"
