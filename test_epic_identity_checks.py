"""2026-11-21 IG name/price check fix (Architect design + the ruling on no-name paths).

Yahoo names are the real longName values of the 2026-11-15 snapshot; epic_lookup names/epics are live rows. Where IG's exact
string for a ticker with no epic_lookup row is not known (MUV2.DE, SAX.DE, OR.PA, NN.AS, RACE.MI, LDO.MI) the IG name is IG's
own convention measured on live rows (digraphs: 'Deutsche Boerse AG'; legal forms NV, SA, SpA without dots) -- UNVERIFIED
against IG's real string, which the Developer may not read."""
import datetime as dt
import logging
import sys
import types

import pytest

import ig_shim


# ---- names ------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("ticker,ig_name,yahoo_name", [
    ("MUV2.DE", "Muenchener Rueckversicherungs-Gesellschaft AG",
     "Münchener Rückversicherungs-Gesellschaft Aktiengesellschaft in München"),
    ("SAX.DE", "Stroeer SE", "Ströer SE & Co. KGaA"),
    ("OR.PA", "L'Oreal SA", "L'Oréal S.A."),
    ("NN.AS", "NN Group NV", "NN Group N.V."),
    ("RACE.MI", "Ferrari NV", "Ferrari N.V."),
    ("LDO.MI", "Leonardo SpA", "Leonardo S.p.a."),
    ("ADS.DE", "adidas AG", "adidas AG"),
    ("DB1.DE", "Deutsche Boerse AG", "Deutsche Börse AG"),
    ("0836.HK", "China Resources Power Holdings Co Ltd", "China Resources Power Holdings Company Limited"),
])
def test_accents_transliterations_and_legal_forms_do_not_break_a_right_match(ticker, ig_name, yahoo_name):
    assert ig_shim.instrument_names_match(ticker, ig_name, yahoo_name)


def test_a_different_company_still_does_not_match():
    assert not ig_shim.instrument_names_match("ASX", "ASX Ltd", "ASE Technology Holding Co., Ltd.")
    assert not ig_shim.instrument_names_match("NN.AS", "NN Group NV", "NNN Group Ltd")
    assert not ig_shim.instrument_names_match("X", "Domino's Pizza Enterprises Ltd", "DOMINOS FPO [DMP]")


# ---- one market family ---------------------------------------------------------------------------------------------------
def test_each_suffix_takes_only_its_own_family():
    w = ig_shim._wrong_market
    assert not w("0836.HK", "AC.D.836HK.DAILY.IP") and w("0836.HK", "KA.D.836.DAILY.IP")
    assert not w("DB1.DE", "ED.D.DB1.DAILY.IP") and w("DB1.DE", "UA.D.DB1.DAILY.IP")
    assert not w("4503.T", "AJ.D.4503JT.DAILY.IP") and w("4503.T", "UB.D.4503US.DAILY.IP")
    assert not w("HSBA.L", "KA.D.HSBA.DAILY.IP") and w("HSBA.L", "KC.D.SHAWLN.DAILY.IP")
    assert w("PRU", "KA.D.PRU.DAILY.IP")                                  # the 2026-10-26 rule, unchanged
    assert not w("360.AX", "AB.D.360AU.DAILY.IP")                          # .AX has four families: no entry


def test_a_suffixed_ticker_never_takes_a_us_line_but_a_us_ticker_and_a_class_share_may():
    w = ig_shim._wrong_market
    assert w("ABB.NS", "SA.D.ABBUS.DAILY.IP")
    assert not w("ABB", "SA.D.ABBUS.DAILY.IP")
    assert not w("BRK.B", "UA.D.BRKBUS.DAILY.IP")


def test_a_depositary_receipt_is_refused_only_when_yahoo_names_none():
    w = ig_shim._wrong_market
    assert w("M&M.NS", "PK.D.MHID.DAILY.IP", "Mahindra & Mahindra Ltd - GDR", "Mahindra & Mahindra Limited")
    assert not w("M&M.NS", "PK.D.MHID.DAILY.IP", "Mahindra & Mahindra Ltd - GDR", "Mahindra GDR")
    assert not w("NOK", "SE.D.NOK.DAILY.IP", "Nokia OYJ ADR (24 Hours)", "Nokia ADR")
    assert not w("M&M.NS", "PK.D.MHID.DAILY.IP")                         # no name given: the old two-argument call


# ---- get_epic -------------------------------------------------------------------------------------------------------
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


def _bar(close):
    return [(dt.date.today(), close, close, close, close, 0)]


@pytest.fixture
def wired(monkeypatch):
    def wire(db, markets, name, close_error=None):
        monkeypatch.setattr(ig_shim, "get_db", lambda: db)
        monkeypatch.setattr(ig_shim, "_heuristic_epic", lambda t: None)
        monkeypatch.setattr(ig_shim, "_epic_alert", lambda *a, **k: None)
        monkeypatch.setattr(ig_shim, "_yahoo_name", lambda t: name)
        monkeypatch.setattr(ig_shim.session, "get", lambda path, params=None, version=None: {"markets": markets})
        monkeypatch.delenv("HVF_WEB_TIER", raising=False)
        if close_error:
            monkeypatch.setattr(ig_shim, "_last_close", lambda t: (_ for _ in ()).throw(close_error))
    return wire


CRP = {"epic": "AC.D.836HK.DAILY.IP", "instrumentName": "China Resources Power Holdings Co Ltd",
       "expiry": "DFB", "instrumentType": "SHARES", "bid": 20.1, "offer": 20.2}


def test_a_hong_kong_code_with_a_leading_zero_finds_ig_code_without_it(monkeypatch, wired):
    other = {**CRP, "epic": "AC.D.83HK.DAILY.IP", "instrumentName": "China Resources Power Holdings Co Ltd"}
    db = _Db(bars=_bar(20.0))
    wired(db, [other, CRP], "China Resources Power Holdings Company Limited")
    monkeypatch.setattr(ig_shim, "_EPIC_VERIFIED_OVERRIDES", {})
    assert ig_shim.get_epic("0836.HK") == "AC.D.836HK.DAILY.IP"


def test_a_result_with_no_ig_price_is_refused_with_its_cause(monkeypatch, wired):
    """China A50 (IX.D.XINHUA.DFB.IP) has no IG price: nothing confirms it."""
    a50 = {"epic": "IX.D.XINHUA.DFB.IP", "instrumentName": "China A50", "expiry": "DFB", "instrumentType": "INDICES",
           "bid": None, "offer": None}
    db = _Db(bars=_bar(14000.0))
    wired(db, [a50], "China A50")
    monkeypatch.setattr(ig_shim, "_EPIC_VERIFIED_OVERRIDES", {})
    assert ig_shim.get_epic("CN50X") is None
    assert "no IG price" in ig_shim.LAST_EPIC_WHY["CN50X"]
    assert not [s for s in db.sql if s.lstrip().lower().startswith("insert into epic_lookup")]


def test_a_database_error_reading_closes_is_named_not_swallowed(monkeypatch, wired):
    wired(_Db(), [CRP], "China Resources Power Holdings Company Limited", close_error=RuntimeError("pool full"))
    monkeypatch.setattr(ig_shim, "_EPIC_VERIFIED_OVERRIDES", {})
    assert ig_shim.get_epic("0836.HK") is None
    assert "RuntimeError" in ig_shim.LAST_EPIC_WHY["0836.HK"] and "pool full" not in ig_shim.LAST_EPIC_WHY["0836.HK"]


# ---- no company name (the Architect's ruling) ---------------------------------------------------------------------------
def test_a_cache_hit_with_no_name_still_returns_the_epic_and_says_it_was_unchecked(monkeypatch, wired, caplog):
    wired(_Db(cached=("AC.D.836HK.DAILY.IP", "China Resources Power Holdings Co Ltd")), [], "")
    monkeypatch.setattr(ig_shim, "_YNAME_WHY", {"0836.HK": ig_shim._WHY_UNREACHABLE})
    monkeypatch.setattr(ig_shim, "_EPIC_VERIFIED_OVERRIDES", {})
    with caplog.at_level(logging.WARNING, logger=ig_shim.log.name):
        assert ig_shim.get_epic("0836.HK") == "AC.D.836HK.DAILY.IP"
    assert "UNCHECKED" in caplog.text and "unreachable" in caplog.text


@pytest.mark.parametrize("kind,words", [("unreachable", "unreachable (retry later)"), ("no_name", "no company name")])
def test_a_miss_with_no_name_is_refused_and_the_refusal_says_which(monkeypatch, wired, kind, words):
    why = ig_shim._WHY_UNREACHABLE + ": ConnectionError" if kind == "unreachable" else ig_shim._WHY_NO_NAME
    db = _Db(bars=_bar(20.0))
    wired(db, [CRP], "")
    monkeypatch.setattr(ig_shim, "_YNAME_WHY", {"HAB.DE": why})
    monkeypatch.setattr(ig_shim, "_EPIC_VERIFIED_OVERRIDES", {})
    assert ig_shim.get_epic("HAB.DE") is None
    assert words in ig_shim.LAST_EPIC_WHY["HAB.DE"]
    assert not [s for s in db.sql if s.lstrip().lower().startswith("insert into epic_lookup")]


def test_a_pinned_oil_resolves_with_no_name_and_is_the_cached_epic():
    assert ig_shim._EPIC_VERIFIED_OVERRIDES["OIL"] == "CC.D.CL.USS.IP"
    assert ig_shim.get_epic("OIL") == "CC.D.CL.USS.IP"


def test_the_website_miss_with_no_scan_name_stays_open_and_logs_the_cause(monkeypatch, wired, caplog):
    db = _Db(bars=_bar(20.0))
    wired(db, [CRP], "")
    monkeypatch.setenv("HVF_WEB_TIER", "1")
    monkeypatch.setattr(ig_shim, "_YNAME_WHY", {"0836.HK": ig_shim._WHY_WEB_NO_NAME})
    monkeypatch.setattr(ig_shim, "_EPIC_VERIFIED_OVERRIDES", {})
    with caplog.at_level(logging.WARNING, logger=ig_shim.log.name):
        assert ig_shim.get_epic("0836.HK") == "AC.D.836HK.DAILY.IP"
    assert "web tier: no scan name; identity not checked" in caplog.text


# ---- _yahoo_name records why ----------------------------------------------------------------------------------------------
def _yahoo(monkeypatch, ticker, info=None, error=None):
    monkeypatch.delenv("HVF_WEB_TIER", raising=False)
    monkeypatch.setattr(ig_shim, "_YNAME_CACHE", {})
    monkeypatch.setattr(ig_shim, "_YNAME_WHY", {})

    def t(sym):
        if error:
            raise error
        return types.SimpleNamespace(info=info)
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=t))


def test_a_404_thin_dict_is_cached_with_a_no_name_reason(monkeypatch):
    """MEASURED 2026-11-21: yfinance returns {'trailingPegRatio': None} for HAB.DE, ANSS, XRPUSD (no exception)."""
    _yahoo(monkeypatch, "HAB.DE", info={"trailingPegRatio": None})
    assert ig_shim._yahoo_name("HAB.DE") == ""
    assert ig_shim._YNAME_WHY["HAB.DE"] == ig_shim._WHY_NO_NAME and "HAB.DE" in ig_shim._YNAME_CACHE


def test_a_transport_failure_is_not_cached_and_says_unreachable(monkeypatch):
    """MEASURED 2026-11-21: a refused connection raises requests-style ConnectionError out of .info."""
    _yahoo(monkeypatch, "AAPL", error=ConnectionError("curl: (7) Failed to connect"))
    assert ig_shim._yahoo_name("AAPL") == ""
    assert "unreachable (retry later)" in ig_shim._YNAME_WHY["AAPL"] and "AAPL" not in ig_shim._YNAME_CACHE


def test_a_found_name_clears_the_reason(monkeypatch):
    _yahoo(monkeypatch, "OR.PA", info={"longName": "L'Oréal S.A."})
    ig_shim._YNAME_WHY["OR.PA"] = "stale"
    assert ig_shim._yahoo_name("OR.PA") == "L'Oréal S.A." and "OR.PA" not in ig_shim._YNAME_WHY


def test_the_website_with_no_scan_name_never_imports_yfinance_and_records_why(monkeypatch):
    import instrument_name
    monkeypatch.setenv("HVF_WEB_TIER", "1")
    monkeypatch.setattr(ig_shim, "_YNAME_CACHE", {})
    monkeypatch.setattr(ig_shim, "_YNAME_WHY", {})
    monkeypatch.setattr(instrument_name, "scan_name", lambda t: "")
    monkeypatch.setitem(sys.modules, "yfinance", None)         # an import of yfinance now raises ImportError
    assert ig_shim._yahoo_name("ABB.NS") == ""
    assert ig_shim._YNAME_WHY["ABB.NS"] == ig_shim._WHY_WEB_NO_NAME


# ---- added by the independent test specialist 2026-11-22: mutants of the step 3 rules that no test killed --------------
@pytest.mark.parametrize("ig_name,yahoo_name", [
    ("Orsted A/S", "Ørsted A/S"),                       # letters with no decomposition: o-stroke
    ("Strasse AG", "Straße AG"),   # sharp s
    ("Lodz SA", "Łódź S.A."),               # l-stroke
    ("L'Oreal SA", "L’Oréal S.A."),           # the curly apostrophe Yahoo uses
])
def test_letters_without_a_decomposition_and_the_curly_apostrophe_fold(ig_name, yahoo_name):
    assert ig_shim.instrument_names_match("XX.YY", ig_name, yahoo_name)


def test_each_suffix_family_is_enforced_on_its_own_not_only_through_another_rule():
    w = ig_shim._wrong_market
    assert w("4503.T", "AB.D.4503JT.DAILY.IP")             # not a US line, not KA: only the .T family can refuse it
    assert w("0836.HK", "UA.D.836HK.DAILY.IP")             # same for .HK
    assert w("DB1.DE", "AB.D.DB1.DAILY.IP")
    assert w("HSBA.L", "UA.D.HSBA.DAILY.IP")


def test_a_london_or_tokyo_ticker_never_takes_a_us_line_even_inside_its_own_family():
    w = ig_shim._wrong_market
    assert w("ABC.L", "KA.D.ABCUS.DAILY.IP")                # only the US-line rule can refuse this one
    assert w("1234.T", "AJ.D.1234US.DAILY.IP")


def test_an_h_share_listing_is_a_depositary_name_when_yahoo_has_none():
    w = ig_shim._wrong_market
    assert w("0939.HK", "AC.D.939HK.DAILY.IP", "China Construction Bank Corp - H shares", "China Construction Bank Corporation")
    assert not w("0939.HK", "AC.D.939HK.DAILY.IP", "China Construction Bank Corp", "China Construction Bank Corporation")


def test_a_cached_depositary_row_is_refused_and_not_purged(monkeypatch, wired):
    """The cache-hit path passes the row's name and Yahoo's to _wrong_market (a two-argument call would let it through)."""
    db = _Db(cached=("PK.D.MHID.DAILY.IP", "Mahindra & Mahindra Ltd - GDR"))
    wired(db, [], "Mahindra & Mahindra Limited")
    monkeypatch.setattr(ig_shim, "_EPIC_VERIFIED_OVERRIDES", {})
    assert ig_shim.get_epic("M&M.NS") is None
    assert "another market's listing" in ig_shim.LAST_EPIC_WHY["M&M.NS"]
    assert not [s for s in db.sql if s.lstrip().lower().startswith(("delete", "insert", "update"))]


def test_the_name_search_result_in_another_markets_family_is_refused(monkeypatch, wired):
    """A non-London ticker whose ticker search finds nothing by name; the company-name search finds a KA.D. code with the
    right name at the right price. It is another market's listing: refused, never chosen on score or name."""
    first = {"epic": "UA.D.FOURUS.DAILY.IP", "instrumentName": "Four Seasons Ltd", "expiry": "DFB",
             "instrumentType": "SHARES", "bid": 100.0, "offer": 100.2}
    uk = {"epic": "KA.D.SHIFT4.DAILY.IP", "instrumentName": "Shift4 Payments Inc", "expiry": "DFB",
          "instrumentType": "SHARES", "bid": 100.0, "offer": 100.2}
    db = _Db(bars=_bar(100.0))
    wired(db, [first], "Shift4 Payments, Inc.")
    monkeypatch.setattr(ig_shim, "_EPIC_VERIFIED_OVERRIDES", {})
    calls = []

    def get(path, params=None, version=None):
        calls.append((params or {}).get("searchTerm"))
        return {"markets": [first] if len(calls) == 1 else [uk]}
    monkeypatch.setattr(ig_shim.session, "get", get)
    assert ig_shim.get_epic("FOUR") is None
    assert len(calls) == 2                                           # the name search really ran
    assert not [s for s in db.sql if s.lstrip().lower().startswith("insert into epic_lookup")]


# DEFECT FOUND by the specialist 2026-11-22 (RED on the current ig_shim.py, on purpose). The depositary rule is documented as
# for SUFFIXED tickers ("M&M.NS ... the one such suffixed row"), but _wrong_market applies it to every ticker, so a US-listed
# ADR whose IG name says ADR and whose Yahoo longName does not is refused. MEASURED against epic_lookup + the 2026-11-15
# snapshot names: 3 of the 1,449 scanned rows newly refused (M&M.NS intended; MMC; ARM = Arm Holdings, which trades only as a
# Nasdaq ADS and whose cached epic works today) and 29 more rows outside the snapshot (NOK, TSM, NVO, NIO, BIDU, ...).
@pytest.mark.parametrize("ticker,epic,ig_name,yahoo_name", [
    ("ARM", "UA.D.ARMUS.DAILY.IP", "ARM Holdings PLC ADR (24 Hours)", "Arm Holdings PLC"),
    ("NOK", "SE.D.NOK.DAILY.IP", "Nokia OYJ ADR (24 Hours)", "Nokia Oyj"),
    ("TSM", "SG.D.TSM.DAILY.IP", "Taiwan Semiconductor Manufacturing Co Ltd ADR (24 Hours)",
     "Taiwan Semiconductor Manufacturing Company Limited"),
])
def test_a_us_listed_adr_ticker_is_not_refused_for_being_an_adr(ticker, epic, ig_name, yahoo_name):
    assert not ig_shim._wrong_market(ticker, epic, ig_name, yahoo_name)


# ---- Security review M1/L1 (2026-11-22) --------------------------------------------------------------------------------
DSN = "postgresql://postgres.abcd:s3cret@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"


def test_a_database_error_text_never_reaches_the_refusal_the_order_page_or_the_visible_log(monkeypatch, wired, caplog):
    """The reason is shown to a signed-in user ('Create an order') and mailed; the order page also forwards every log
    line (getMessage) that names the ticker. A connect error can carry the host, port or user."""
    wired(_Db(), [CRP], "China Resources Power Holdings Company Limited",
          close_error=RuntimeError(f"connection to {DSN} refused"))
    monkeypatch.setattr(ig_shim, "_EPIC_VERIFIED_OVERRIDES", {})
    with caplog.at_level(logging.INFO):
        assert ig_shim.get_epic("0836.HK") is None
    assert "RuntimeError" in ig_shim.LAST_EPIC_WHY["0836.HK"] and "supabase" not in ig_shim.LAST_EPIC_WHY["0836.HK"]
    assert DSN not in ig_shim.describe_size_skip("0836.HK", None)
    seen = [r.getMessage() for r in caplog.records]
    assert any("0836.HK" in m for m in seen) and not any("supabase" in m or "s3cret" in m for m in seen)
    assert any(r.exc_info and "supabase" in str(r.exc_info[1]) for r in caplog.records), "the full error stays in the log"


def test_ig_names_in_the_wrong_market_alert_carry_no_html_tags(monkeypatch):
    got = []
    monkeypatch.setattr(ig_shim, "_epic_alert", lambda t, summary, detail="", always=False: got.append(detail))
    monkeypatch.setattr(ig_shim, "live_tickers", lambda: set())
    ig_shim._own_market("PRU", [{"epic": "KA.D.PRU.DAILY.IP", "instrumentName": "<script>x</script> Prudential"}], "Prudential")
    assert "<" not in got[0] and ">" not in got[0] and "Prudential" in got[0]
