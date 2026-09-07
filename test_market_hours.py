# ======================================================================================================================
# Tests for market_hours.
#
# The previous version of this module was deleted because its tests asserted the same values its table asserted, so
# they proved internal consistency and not correctness. These are written the other way round:
#
#   * BEHAVIOUR is tested against a synthetic table injected into the module, so a test failing means the logic is
#     wrong rather than that an exchange moved its clock.
#   * The COMMITTED TABLE is tested only for the properties that must hold however it was generated -- full coverage of
#     the live universe, internally coherent entries, and provenance on every row.
#   * CORRECTNESS of the values is checked against the exchanges themselves in a live_state test, which is the only
#     place that can honestly make that claim.
#
# Every guard here has been mutation-tested: `test_..._catches_...` names a test that deliberately breaks the thing
# above it and asserts the guard notices.
# ======================================================================================================================

import datetime as dt

import pytest

import market_hours as mh

UTC = dt.timezone.utc

# A table with one of each shape, so the behaviour tests never depend on a real exchange's timetable.
FAKE = {
    "SUFFIX:.TEST":  {"tz": "Europe/London", "open": "08:00", "close": "16:30",
                      "samples": ["A.TEST"], "agreement": "1/1"},
    "SUFFIX:.SYD":   {"tz": "Australia/Sydney", "open": "10:00", "close": "16:12",
                      "samples": ["B.SYD"], "agreement": "1/1"},
    "SUFFIX:.ALLDAY": {"tz": "UTC", "open": "00:00", "close": "23:59",
                       "samples": ["C.ALLDAY"], "agreement": "1/1"},
    # UTC+14: the local date runs AHEAD of the UTC date, which is what separates "the exchange's day" from "today".
    "SUFFIX:.AHEAD": {"tz": "Pacific/Kiritimati", "open": "09:00", "close": "10:00",
                      "samples": ["D.AHEAD"], "agreement": "1/1"},
}


@pytest.fixture
def fake_table(monkeypatch):
    import market_hours_data
    monkeypatch.setattr(market_hours_data, "EXCHANGES", FAKE)
    return FAKE


# ----------------------------------------------------------------------------------------------------------------------
# The guard the previous version did not have
# ----------------------------------------------------------------------------------------------------------------------
def test_every_universe_instrument_resolves_to_an_exchange():
    """The check whose absence let 205 instruments go missing unnoticed.

    An unmapped instrument and a continuously-traded one look identical at the call site -- both are "never in a
    closing window" -- so nothing distinguishes a deliberate exemption from an oversight except this.
    """
    import run_hvf_report
    tickers = [t for ts in run_hvf_report.UNIVERSE.values() for t in ts]
    missing = mh.unmapped(tickers)
    assert not missing, (f"{len(missing)} instrument(s) map to no exchange, e.g. {missing[:10]}. "
                         "Run: python market_hours.py --refresh")


def test_the_coverage_guard_catches_a_missing_exchange(monkeypatch):
    """Mutation test: remove Tokyo and the guard above must notice. A test that has never failed proves nothing."""
    import market_hours_data
    pruned = {k: v for k, v in market_hours_data.EXCHANGES.items() if k != "SUFFIX:.T"}
    monkeypatch.setattr(market_hours_data, "EXCHANGES", pruned)
    import run_hvf_report
    tickers = [t for ts in run_hvf_report.UNIVERSE.values() for t in ts]
    missing = mh.unmapped(tickers)
    assert missing, "removing an exchange did not make any instrument unmapped -- the guard cannot fail"
    assert all(t.endswith(".T") for t in missing)


# ----------------------------------------------------------------------------------------------------------------------
# Resolving an instrument to its exchange
# ----------------------------------------------------------------------------------------------------------------------
def test_synthetic_tickers_resolve_through_the_project_map_not_by_name():
    """OIL is a US-listed ETN and NASDAQ is a bank holding company.

    Looking either up at Yahoo by its universe name returns a real, wrong instrument with New York hours, for what are
    meant to be a futures contract and an index. So the mapping must go through config.YAHOO_MAP.
    """
    assert mh.yahoo_symbol("OIL") == "CL=F"
    assert mh.yahoo_symbol("NASDAQ") == "^IXIC"
    assert mh.exchange_key("OIL") == "SUFFIX:=F"
    assert mh.exchange_key("NASDAQ") == "INDEX:^IXIC"
    assert mh.yahoo_symbol("BP.L") == "BP.L"          # a real symbol passes straight through


def test_indices_are_keyed_individually_because_they_do_not_share_a_session():
    """The scanner's "Indices" market spans exchanges hours apart; one key for it would be wrong for most of them."""
    assert mh.exchange_key("SPX500") != mh.exchange_key("JPN225")
    assert mh.session("SPX500")["tz"] == "America/New_York"
    assert mh.session("JPN225")["tz"] == "Asia/Tokyo"


def test_euronext_members_are_not_forced_onto_one_timetable():
    """Measured 2026-09-07: Oslo closes 16:20 and Brussels 17:40, against Paris's 17:30.

    A single "Euronext" row -- which is what the deleted version had -- is 70 minutes wrong for Oslo.
    """
    closes = {mh.session(t)["close"] for t in ("EQNR.OL", "ABI.BR", "MC.PA")}
    assert len(closes) == 3, f"expected three different closes across Oslo/Brussels/Paris, got {closes}"


# ----------------------------------------------------------------------------------------------------------------------
# Behaviour, against the synthetic table
# ----------------------------------------------------------------------------------------------------------------------
def test_the_utc_close_follows_daylight_saving_in_both_directions(fake_table):
    """The local close is fixed; the UTC one is not. A stored UTC time is right for part of the year only."""
    winter = mh.close_utc("A.TEST", dt.date(2026, 1, 15))
    summer = mh.close_utc("A.TEST", dt.date(2026, 7, 15))
    assert winter.strftime("%H:%M") == "16:30"        # GMT
    assert summer.strftime("%H:%M") == "15:30"        # BST


def test_southern_hemisphere_daylight_saving_moves_the_opposite_way(fake_table):
    """Sydney is the case a northern-hemisphere assumption gets backwards."""
    jan = mh.close_utc("B.SYD", dt.date(2026, 1, 15))
    jul = mh.close_utc("B.SYD", dt.date(2026, 7, 15))
    assert jan.strftime("%H:%M") == "05:12"           # AEDT, UTC+11
    assert jul.strftime("%H:%M") == "06:12"           # AEST, UTC+10


@pytest.mark.parametrize("minutes_before_close, expected", [
    (31, False),      # not yet
    (30, True),       # the window opens exactly here
    (1, True),
    (0, True),        # the close itself is still inside
    (-1, False),      # gone: nothing can be traded now
])
def test_the_closing_window_boundaries(fake_table, minutes_before_close, expected):
    close = mh.close_utc("A.TEST", dt.date(2026, 7, 15))
    now = close - dt.timedelta(minutes=minutes_before_close)
    assert mh.in_closing_window("A.TEST", now=now) is expected


def test_a_continuous_instrument_is_never_in_a_closing_window(fake_table):
    """No closing bar to wait for, so no window to be in. The caller must read this as "do not act".

    Mutation-tested 2026-09-07, and the result is worth recording: removing the is_continuous guard from EITHER
    close_utc or in_closing_window alone leaves this green, because each masks the other -- in_closing_window bails on
    the None that close_utc returns, and close_utc is never reached once in_closing_window has bailed. It fails only
    when BOTH are removed. The pair is redundant defence, not a single guard, so do not read one passing mutation as
    proof this test is vacuous.
    """
    assert mh.is_continuous("C.ALLDAY") is True
    assert mh.close_utc("C.ALLDAY", dt.date(2026, 7, 15)) is None
    for hour in range(24):
        now = dt.datetime(2026, 7, 15, hour, 30, tzinfo=UTC)
        assert mh.in_closing_window("C.ALLDAY", now=now) is False


def test_an_unmapped_instrument_never_opens_a_window(fake_table):
    """The failure direction that matters: not knowing must never authorise closing a real position."""
    assert mh.session("Z.UNKNOWN") is None
    assert mh.unmapped(["Z.UNKNOWN", "A.TEST"]) == ["Z.UNKNOWN"]
    for hour in range(24):
        assert mh.in_closing_window("Z.UNKNOWN", now=dt.datetime(2026, 7, 15, hour, tzinfo=UTC)) is False


def test_the_window_uses_the_exchange_day_not_the_utc_day(fake_table):
    """At UTC+14 the exchange is already on tomorrow's date, so "today" in UTC names the wrong session.

    Kiritimati closes 10:00 local, which is 20:00 UTC on the PREVIOUS calendar day. Taking the UTC date would look up
    a session 24 hours out and report no window at the exact moment there is one.
    """
    now = dt.datetime(2026, 1, 5, 19, 45, tzinfo=UTC)      # = 2026-01-06 09:45 local
    assert mh.in_closing_window("D.AHEAD", now=now) is True


def test_a_naive_datetime_is_read_as_utc_not_as_local_time(fake_table):
    close = mh.close_utc("A.TEST", dt.date(2026, 7, 15))
    naive = close.astimezone(UTC).replace(tzinfo=None) - dt.timedelta(minutes=5)
    assert mh.in_closing_window("A.TEST", now=naive) is True


def test_session_fraction_elapsed_reports_how_complete_the_bar_is(fake_table):
    """Used to state a partial reading's limitation rather than present it as final."""
    open_utc = dt.datetime(2026, 7, 15, 7, 0, tzinfo=UTC)      # 08:00 London
    close = mh.close_utc("A.TEST", dt.date(2026, 7, 15))       # 15:30 UTC
    assert mh.session_fraction_elapsed("A.TEST", now=open_utc) == pytest.approx(0.0, abs=1e-6)
    assert mh.session_fraction_elapsed("A.TEST", now=close) == pytest.approx(1.0, abs=1e-6)
    half = open_utc + (close - open_utc) / 2
    assert mh.session_fraction_elapsed("A.TEST", now=half) == pytest.approx(0.5, abs=1e-6)
    assert mh.session_fraction_elapsed("C.ALLDAY", now=half) is None       # nothing to be part-way through


# ----------------------------------------------------------------------------------------------------------------------
# The committed table: the properties that must hold however it was generated
# ----------------------------------------------------------------------------------------------------------------------
def test_every_generated_entry_is_coherent_and_carries_its_provenance():
    from market_hours_data import EXCHANGES
    assert EXCHANGES, "the generated table is empty"
    for key, e in EXCHANGES.items():
        assert set(e) >= {"tz", "open", "close", "samples", "agreement"}, key
        assert e["samples"], f"{key} records no instrument it was derived from"
        n, of = e["agreement"].split("/")
        assert n == of and int(n) >= 1, f"{key} was written from disagreeing samples: {e['agreement']}"
        for field in ("open", "close"):
            hh, mm = e[field].split(":")
            assert 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59, f"{key} {field}={e[field]}"
        assert e["open"] < e["close"], f"{key} closes before it opens: {e['open']}-{e['close']}"


def test_every_generated_timezone_is_a_real_zone():
    from zoneinfo import ZoneInfo
    from market_hours_data import EXCHANGES
    for key, e in EXCHANGES.items():
        ZoneInfo(e["tz"])                                  # raises if the zone does not exist


def test_the_markets_that_hold_ig_epics_are_covered():
    """Only an instrument that can be HELD can ever need closing, so these are the entries that matter most."""
    import run_hvf_report
    tickers = [t for ts in run_hvf_report.UNIVERSE.values() for t in ts]
    tradeable = [t for t in tickers if mh.session(t) and not mh.is_continuous(t)]
    assert len(tradeable) > 1500, f"only {len(tradeable)} instruments have a closing bar; expected most of the universe"


# ----------------------------------------------------------------------------------------------------------------------
# Correctness of the values -- the only place that can honestly claim it
# ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.live_state
def test_the_committed_table_still_matches_the_exchanges():
    """Re-derives a sample from the exchanges and compares. Exchanges do change their hours.

    live_state because it needs the network; `python market_hours.py --refresh` is the fix when it fails.
    """
    from market_hours_data import EXCHANGES
    checked, wrong = 0, []
    for key in ("SUFFIX:.L", "SUFFIX:(bare)", "SUFFIX:.T", "SUFFIX:.AX", "SUFFIX:.HK", "SUFFIX:.OL"):
        entry = EXCHANGES[key]
        got = mh.probe(entry["samples"][0])
        checked += 1
        if got and (got[0], got[2]) != (entry["tz"], entry["close"]):
            wrong.append(f"{key}: committed {entry['tz']} {entry['close']}, exchange says {got[0]} {got[2]}")
    assert checked and not wrong, "; ".join(wrong) + " -- run: python market_hours.py --refresh"


# ----------------------------------------------------------------------------------------------------------------------
# The live tradeability check
# ----------------------------------------------------------------------------------------------------------------------
def test_is_tradeable_now_reads_igs_own_market_status(monkeypatch):
    class _S:
        def __init__(self, status):
            self.status = status
        def get(self, path, version=None):
            return {"snapshot": {"marketStatus": self.status}}
    assert mh.is_tradeable_now("X.EPIC", session_obj=_S("TRADEABLE")) is True
    assert mh.is_tradeable_now("X.EPIC", session_obj=_S("EDITS_ONLY")) is False
    assert mh.is_tradeable_now("X.EPIC", session_obj=_S("SUSPENDED")) is False


def test_is_tradeable_now_says_no_when_it_cannot_ask():
    """An unanswerable question about a real position is a reason not to act on it."""
    class _Boom:
        def get(self, path, version=None):
            raise RuntimeError("IG unreachable")
    assert mh.is_tradeable_now("X.EPIC", session_obj=_Boom()) is False
