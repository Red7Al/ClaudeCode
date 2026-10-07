"""The freshness mechanism (owner 2026-09-28: "there needs to be a mechanism to eradicate stale data",
and "make sure the freshness mechanism is not over complicated but well engineered - I don't want to go
through different stages of if failing and it needs fixing").

So these tests are aimed squarely at the ways a checker like this FAILS IN SERVICE rather than at its
happy path:

  * it reports "fine" when it could not actually measure anything          -> fail-closed tests
  * it breaks the page it is supposed to protect                           -> never-raises tests
  * it cries wolf over a weekend, gets muted, and then means nothing       -> limit tests
  * nothing ever calls it                                                  -> invocation test
"""
import datetime as dt

import pytest

import data_freshness as fresh


# ------------------------------------------------------------------------------------------------------
# Age arithmetic
# ------------------------------------------------------------------------------------------------------

def test_a_timestamp_without_a_timezone_is_read_as_utc_not_local():
    """The database returns naive timestamps; reading them as local time would shift every age by hours."""
    naive = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(hours=5)

    assert 4.9 < fresh._age_hours(naive) < 5.1


def test_an_iso_string_with_a_z_suffix_is_accepted():
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3)).isoformat().replace("+00:00", "Z")

    assert 2.9 < fresh._age_hours(when) < 3.1


def test_todays_bare_date_reads_as_current_not_as_a_day_old():
    """A date-only surface must not look half a day stale by lunchtime."""
    assert fresh._age_hours(dt.date.today()) == 0.0


def test_age_can_never_be_negative():
    """THE FIRST RUN OF THIS MODULE reported instrument_metrics at -9.8 hours, because a bare date is read
    as end-of-day and today's end-of-day is in the future. Nothing can be fresher than the moment we ask."""
    tomorrow = dt.date.today() + dt.timedelta(days=1)

    assert fresh._age_hours(tomorrow) == 0.0


def test_a_missing_timestamp_raises_rather_than_returning_zero():
    """Returning 0 for "never recorded" would report the stalest possible surface as the freshest."""
    with pytest.raises(Exception):
        fresh._age_hours(None)


# ------------------------------------------------------------------------------------------------------
# Fail closed -- the rule that decides whether this is a safeguard or decoration
# ------------------------------------------------------------------------------------------------------

def _one_surface(monkeypatch, measure, max_age_hours=24, customer_facing=True):
    monkeypatch.setattr(fresh, "SURFACES", (
        {"name": "probe", "label": "Probe", "measure": measure,
         "max_age_hours": max_age_hours, "customer_facing": customer_facing, "owner": "test"},))


def test_a_surface_that_cannot_be_measured_is_reported_stale_not_skipped(monkeypatch):
    """"I could not tell" and "it is fine" must not look the same -- that confusion IS the bug."""
    def _boom():
        raise RuntimeError("database unreachable")

    _one_surface(monkeypatch, _boom)
    out = fresh.check()

    assert out["surfaces"][0]["stale"] is True
    assert "database unreachable" in out["surfaces"][0]["error"]
    assert out["stale"] == ["probe"] and out["customer_facing_stale"] == ["probe"]


def test_a_surface_that_has_never_been_written_is_reported_stale(monkeypatch):
    _one_surface(monkeypatch, lambda: None)

    assert fresh.check()["surfaces"][0]["stale"] is True


def test_check_never_raises_even_when_every_surface_explodes(monkeypatch):
    """A safeguard that can break the page it protects is not a safeguard."""
    def _boom():
        raise RuntimeError("everything is on fire")

    _one_surface(monkeypatch, _boom)

    out = fresh.check()          # must not raise

    assert out["customer_facing_stale"] == ["probe"]


def test_an_unmeasurable_surface_never_has_a_number_invented_for_it(monkeypatch):
    """Wording changed 2026-09-28 from "(age unknown)" to a separate "Could not check" sentence; the
    rule it protects is unchanged -- no age may be stated for a surface that was never measured."""
    def _boom():
        raise RuntimeError("nope")

    _one_surface(monkeypatch, _boom)

    msg = fresh.banner()

    assert "Could not check" in msg
    assert "hours old" not in msg and "days old" not in msg


# ------------------------------------------------------------------------------------------------------
# The banner -- silent when healthy, specific when not
# ------------------------------------------------------------------------------------------------------

def test_the_banner_is_empty_when_everything_is_current(monkeypatch):
    """A permanent green badge is ignored within a week and then means nothing."""
    _one_surface(monkeypatch, lambda: dt.datetime.now(dt.timezone.utc))

    assert fresh.banner() == ""


def test_the_banner_names_the_surface_and_its_age(monkeypatch):
    _one_surface(monkeypatch, lambda: dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=40))

    msg = fresh.banner()

    assert "Probe" in msg and "40 hours" in msg


def test_a_long_outage_is_expressed_in_days_not_hundreds_of_hours(monkeypatch):
    """The Supabase copy was 42 days stale; "1027.1 hours old" is not a sentence anyone reads."""
    _one_surface(monkeypatch, lambda: dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=42))

    assert "42 days old" in fresh.banner()


def test_an_internal_surface_staleness_never_reaches_the_customer_banner(monkeypatch):
    """The Supabase backup being stale is an operations problem, not something to alarm a visitor with."""
    _one_surface(monkeypatch, lambda: dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=42),
                 customer_facing=False)

    out = fresh.check()

    assert out["stale"] == ["probe"], "it must still be reported"
    assert out["customer_facing_stale"] == [], "but not shown on the site"
    assert fresh.banner(out) == ""


# ------------------------------------------------------------------------------------------------------
# The limits -- the thing that decides whether this gets muted
# ------------------------------------------------------------------------------------------------------

def test_every_registered_surface_clears_a_normal_weekend():
    """Design rule 3. A limit that fires every Monday morning gets muted, and a muted alarm is worse than
    none. Anything that only updates on trading days needs to survive Friday close -> Monday open."""
    weekend_gap = 72
    for s in fresh.SURFACES:
        if s["name"] == "scanner_snapshot":
            # Exempt because it is OBSERVED to rebuild at weekends, not assumed to: the Scanner Snapshot
            # Publish run list shows runs on Sat 2026-09-20, Sun 2026-09-21 and Sun 2026-09-27. If that
            # ever stops being true, this surface needs the weekend allowance too.
            continue
        assert s["max_age_hours"] >= weekend_gap, (
            f"{s['name']} at {s['max_age_hours']}h would fire over a normal weekend")


def test_every_surface_declares_the_whole_contract():
    """A half-declared surface is how one silently stops being checked."""
    for s in fresh.SURFACES:
        for field in ("name", "label", "measure", "max_age_hours", "customer_facing", "owner"):
            assert field in s, f"{s.get('name')} is missing {field}"
        assert callable(s["measure"])
        assert s["owner"], f"{s['name']} must name the job that feeds it, or nobody knows what to fix"


def test_the_surfaces_that_burned_us_are_all_registered():
    """Every staleness failure measured on 2026-09-28 must be covered, or this fixes nothing."""
    names = {s["name"] for s in fresh.SURFACES}

    assert {"scanner_snapshot", "squeeze_history", "instrument_metrics",
            "supabase_snapshot_copy", "market_data"} <= names


# ------------------------------------------------------------------------------------------------------
# Invocation -- this repository's recurring defect is correct code that nothing ever calls
# ------------------------------------------------------------------------------------------------------

def test_the_website_actually_calls_the_freshness_check():
    """Deliberately NOT a cron job: a scheduled checker would be one more thing to notice had stopped.
    The page load is the trigger, so if this breaks it is visible immediately."""
    from pathlib import Path
    server = (Path(__file__).parent / "hvf_web" / "server.py").read_text(encoding="utf-8")
    client = (Path(__file__).parent / "hvf_web" / "app.js").read_text(encoding="utf-8")

    assert '"/api/freshness"' in server, "the endpoint is not registered"
    assert "data_freshness.check()" in server, "the endpoint does not call the checker"
    assert "/api/freshness" in client, "nothing on the site ever asks"
    assert "paintFreshness()" in client, "the banner painter is never called"


# ------------------------------------------------------------------------------------------------------
# The market-data measure (owner 2026-09-28: "not all markets are open monday to friday")
# ------------------------------------------------------------------------------------------------------

def test_the_market_measure_takes_the_worst_market_not_the_best(monkeypatch):
    """THE MASKING BUG. A global max(bar_date) reports whichever market is furthest AHEAD, so every other
    market could stop updating and the check would still read healthy. Measured on the live database
    2026-09-28 the spread was three days: Commodities 09-27 against China 09-24."""
    # RELATIVE to today, never fixed dates. The measure now compares each market against its OWN
    # allowance, which depends on how long ago the bar was, so fixed dates would make this pass or fail
    # by the calendar -- the same trap that put test_audit_trading_state's FUTURE constant in the past.
    today = dt.date.today()
    snapshot = {"records": [{"ticker": "BTC", "market": "Crypto"},
                            {"ticker": "600519.SS", "market": "SSE (Shanghai)"},
                            {"ticker": "VOD.L", "market": "FTSE 100"}]}

    class _Db:
        def run(self, sql, **p):
            return [("BTC", today - dt.timedelta(days=2)),              # current
                    ("600519.SS", today - dt.timedelta(days=11)),       # OLDEST, but inside its 288h
                    ("VOD.L", today - dt.timedelta(days=8))]            # over the 168h default

        def close(self):
            pass

    # Patch the FUNCTION on the real module, not sys.modules. A sys.modules entry for "hvf_web.server"
    # is ignored once the package already carries a real `server` attribute, which any earlier test that
    # imports it leaves behind -- so this test was silently running against the REAL _load_snapshot and
    # passing only because a local hvf_web/snapshot.json happened to exist. On a clean CI checkout there
    # is none, records came back empty, and _market_data_worst raised "no market could be matched to a
    # bar date". That is why CI was red from c1251dd (2026-09-28) onward while the suite passed locally.
    monkeypatch.setattr("hvf_web.server._load_snapshot", lambda: snapshot)
    monkeypatch.setattr("db_pool.get_db", lambda: _Db())

    moment, note, limit = fresh._market_data_worst()

    # THE ORIGINAL GUARANTEE, unchanged: a market in breach is never masked by a fresher one. Crypto is
    # two days old and must not win.
    assert "Crypto" not in note, "masked by the market that is furthest ahead"
    # THE NEW GUARANTEE: Shanghai is the OLDEST at 11 days, and must still lose to London at 8, because
    # 11 days is inside Shanghai's measured annual closure allowance and 8 days is outside London's.
    # Picking purely by oldest date meant the market with the widest legitimate pause always won, so a
    # genuinely late market behind it could never surface.
    assert moment == today - dt.timedelta(days=8), f"took {moment}, expected the market past its own limit"
    assert "FTSE 100" in note, "the market in breach must be named, or nobody knows what to chase"
    assert limit == 168, "London carries the default allowance"


def test_a_measure_may_name_what_it_found_without_a_second_mechanism(monkeypatch):
    """The (moment, note) return shape must survive into the reported row."""
    _one_surface(monkeypatch, lambda: (dt.datetime.now(dt.timezone.utc), "oldest market: Nowhere"))

    assert fresh.check()["surfaces"][0]["note"] == "oldest market: Nowhere"


def test_an_unmeasurable_surface_is_not_announced_as_stale_data(monkeypatch):
    """FOUND IN SERVICE 2026-09-28: a saturated connection pool made the banner tell visitors that
    perfectly current data was "not current". "The data is old" and "I could not check" are two
    different facts, and claiming the first when only the second is known asserts what was never
    measured."""
    def _boom():
        raise RuntimeError("pool exhausted")

    _one_surface(monkeypatch, _boom)

    msg = fresh.banner()

    assert "Could not check" in msg
    assert "not current" not in msg, f"claimed staleness it never measured: {msg!r}"


def test_measured_staleness_and_an_unmeasurable_surface_are_reported_separately(monkeypatch):
    monkeypatch.setattr(fresh, "SURFACES", (
        {"name": "old", "label": "Old thing", "customer_facing": True, "owner": "t", "max_age_hours": 1,
         "measure": lambda: dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=50)},
        {"name": "broken", "label": "Broken thing", "customer_facing": True, "owner": "t",
         "max_age_hours": 1, "measure": lambda: (_ for _ in ()).throw(RuntimeError("nope"))},
    ))

    msg = fresh.banner()

    assert "Old thing is 2 days old" in msg
    assert "Could not check whether this is current: Broken thing." in msg


def test_a_market_inside_its_own_allowance_is_not_reported_stale(monkeypatch):
    """Shenzhen shuts for ~10 days every October and our bars then match the source exactly.

    MEASURED 2026-10-07 from price_history -- SZSE publishes nothing from 1 October until the 8th-10th in
    every year held (2022 the 10th, 2023 the 9th, 2024 the 8th, 2025 the 9th) -- and confirmed against the
    source the same day: our latest SZSE bar was 2026-09-30 and so was the source's. At the old flat 168h
    this surface would have told visitors the data was stale while it was as current as the market allowed.
    """
    today = dt.date.today()
    snapshot = {"records": [{"ticker": "000001.SZ", "market": "SZSE (Shenzhen)"}]}

    class _Db:
        def run(self, sql, **p):
            return [("000001.SZ", today - dt.timedelta(days=9))]       # 9 days: over 168h, inside 288h

        def close(self):
            pass

    monkeypatch.setattr("hvf_web.server._load_snapshot", lambda: snapshot)
    monkeypatch.setattr("db_pool.get_db", lambda: _Db())

    moment, note, limit = fresh._market_data_worst()
    assert limit == 288, "Shenzhen must carry its measured allowance, not the default"
    assert "allowed 288h" in note, "a non-default allowance must be visible in the note"

    row = [r for r in fresh.check()["surfaces"] if r["name"] == "market_data"][0]
    assert row["max_age_hours"] == 288, "the reported limit must be the one actually applied"
    assert row["stale"] is False, "9 days inside a measured 12-day allowance is not stale"
    assert "market_data" not in fresh.check()["customer_facing_stale"]
