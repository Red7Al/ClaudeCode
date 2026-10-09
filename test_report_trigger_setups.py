"""The Scanner Report shows open triggered setups judged on their TRIGGER-DATE values (owner 2026-10-08).

His rule: "a setup that triggered and passed my filters on its trigger date stays on the report, days
counting up, until it hits its target or stop". Fixtures are the real case that prompted it, read from
squeeze_history that day: ARM's 18 Sep trigger (RVOL 1.8, still OPEN) vanished on 5 Oct when the scan moved
to a 29 Sep funnel whose trigger-bar RVOL was 1.08 -- below his 1.7 floor.
"""

import trading_limits
from hvf_web import server

LIMITS = {**trading_limits.limit_defaults(), "min_risk_reward": 3.1, "max_risk_reward": 100.0,
          "min_quality": 45, "min_rvol": 1.7, "min_volume_score": 4, "min_instrument_value": 485e6,
          "max_instrument_value": 0, "require_above_vwap": 1, "require_atr_expanding": 1}

ARM_29_SEP = {"trig_date": "2026-09-29", "direction": "BULL", "timeframe": "daily-240", "entry": 290.21,
              "stop": 278.64, "target": 518.02, "rr": 19.69, "quality": 45, "rvol": 1.08, "volume_score": 5,
              "above_vwap": True, "atr_expanding": True, "h1_date": None, "h3_date": None, "l3_date": None}
ARM_18_SEP = {**ARM_29_SEP, "trig_date": "2026-09-18", "rr": 5.77, "quality": 52, "rvol": 1.8, "volume_score": 6}
ARM_MCAP = 248e9


def test_the_newest_setup_that_passes_on_its_trigger_date_is_chosen():
    pick = server._report_setup("Alex", "ARM", [ARM_29_SEP, ARM_18_SEP], ARM_MCAP, LIMITS)
    assert pick is ARM_18_SEP, "29 Sep fails RVOL 1.08 < 1.7; the still-open 18 Sep setup must be shown"


def test_the_newest_passing_setup_wins_over_an_older_one():
    newer = {**ARM_29_SEP, "rvol": 2.0}
    assert server._report_setup("Alex", "ARM", [newer, ARM_18_SEP], ARM_MCAP, LIMITS) is newer


def test_a_never_measured_value_cannot_pass_a_floor_that_is_on():
    """The eight rows of 2026-10-08 (CNOOC et al.) passed the RVOL floor only because a blank passes."""
    blank = {**ARM_18_SEP, "rvol": None}
    assert server._report_setup("Alex", "ARM", [blank], ARM_MCAP, LIMITS) is None


def test_trigger_date_vwap_and_atr_are_judged():
    below = {**ARM_18_SEP, "above_vwap": False}
    flat = {**ARM_18_SEP, "atr_expanding": False}
    assert server._report_setup("Alex", "ARM", [below], ARM_MCAP, LIMITS) is None
    assert server._report_setup("Alex", "ARM", [flat], ARM_MCAP, LIMITS) is None


# ── /api/records, end to end through the route ────────────────────────────────────────────────────────

def _records(monkeypatch, setups_map, status="TRIGGERED"):
    snap = {"generated_utc": "2026-10-08T05:11:49", "records": [
        {"ticker": "ARM", "name": "Arm Holdings PLC", "has_signal": True, "status": status, "direction": "BULL",
         "entry": 290.21, "stop": 278.64, "target": 518.02, "rr": 19.69, "quality": 45, "timeframe": "daily-240"}]}
    monkeypatch.setattr(server, "_load_snapshot", lambda: snap)
    monkeypatch.setattr(server._wu, "valid_tokens", lambda: {"tok"})
    monkeypatch.setattr(server._wu, "name_for_token", lambda t: "Alex")
    monkeypatch.setattr(trading_limits, "user_limits", lambda name: LIMITS)
    monkeypatch.setattr(server, "_snapshot_52wk", lambda s: {})
    monkeypatch.setattr(server, "_live_instrument_metrics", lambda s: {"ARM": {"rvol": 0.59, "above_vwap": True,
                                                                               "atr_expanding": True}})
    monkeypatch.setattr(server, "_mcap_map", lambda: {"ARM": ARM_MCAP})
    monkeypatch.setattr(server, "_snapshot_trigger_dates", lambda s: {"ARM": "2026-09-29"})
    monkeypatch.setattr(server, "_open_trigger_setups", lambda s: setups_map)
    body = server.app.test_client().get("/api/records", headers={"X-Auth": "tok"}).get_json()
    return body["records"][0]


def test_the_route_shows_the_open_18_sep_setup_with_its_trigger_values(monkeypatch):
    r = _records(monkeypatch, {"ARM": [ARM_29_SEP, ARM_18_SEP]})
    assert r["report_ok"] is True and r["trig_date"] == "2026-09-18"
    assert r["rvol"] == 1.8 and r["current_rvol"] == 0.59, "trigger and now are separate fields"
    assert r["rr"] == 5.77 and r["quality"] == 52


def test_a_triggered_row_with_no_passing_setup_is_marked_for_hiding(monkeypatch):
    r = _records(monkeypatch, {"ARM": [ARM_29_SEP]})
    assert r["report_ok"] is False


def test_the_client_hides_only_what_the_server_marked():
    html = __import__("client_source").client_source()
    i = html.index("function pass(r,except)")
    assert "if(r.report_ok===false)return false;" in html[i:i + 4000]


def test_only_setups_the_daily_refresh_has_confirmed_open_are_read():
    """squeeze_history.refresh_daily replays an 18-month window, so older rows keep a stale "OPEN" for ever:
    3968.HK's 26 Sep 2024 setup had hit its target and still said OPEN on 2026-10-08."""
    seen = []

    class _Db:
        def run(self, sql, **k):
            seen.append(sql)
            return []

        def close(self):
            pass

    import db_pool
    server._OPEN_SETUPS_CACHE.update(gen=None, data=None, at=0.0)
    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(db_pool, "get_db", lambda: _Db())
    try:
        server._open_trigger_setups({"generated_utc": "t"})
    finally:
        mp.undo()
        server._OPEN_SETUPS_CACHE.update(gen=None, data=None, at=0.0)
    assert "refreshed_at >= now() - interval" in seen[0]


def test_only_triggers_from_the_last_90_days_are_read(monkeypatch):
    """Owner 2026-10-08: "90 days" -- older triggers have "no value" (his words of 2026-09-18)."""
    seen = []

    class _Db:
        def run(self, sql, **k):
            seen.append((sql, k))
            return []

        def close(self):
            pass

    import db_pool
    server._OPEN_SETUPS_CACHE.update(gen=None, data=None, at=0.0)
    monkeypatch.setattr(db_pool, "get_db", lambda: _Db())
    server._open_trigger_setups({"generated_utc": "t2"})
    server._OPEN_SETUPS_CACHE.update(gen=None, data=None, at=0.0)
    sql, params = seen[0]
    assert "triggered_date >= current_date - cast(:max_age as integer)" in sql
    assert params.get("max_age") == 90 == server.REPORT_MAX_TRIGGER_AGE_DAYS


@__import__("pytest").mark.live_state
def test_the_real_query_runs_against_the_database():
    """2026-10-08: the 90-day clause passed every offline test and failed on Postgres ("operator does not
    exist: date >= integer"), and the failure path silently left rows as scanned. Only the database can say
    the SQL is valid, so this runs it."""
    server._OPEN_SETUPS_CACHE.update(gen=None, data=None, at=0.0)
    m = server._open_trigger_setups({"generated_utc": "live-test"})
    server._OPEN_SETUPS_CACHE.update(gen=None, data=None, at=0.0)
    assert m is not None, "the open-setups query failed against the live database"
    import datetime as dt
    oldest = min(s["trig_date"] for v in m.values() for s in v)
    assert oldest >= (dt.date.today() - dt.timedelta(days=server.REPORT_MAX_TRIGGER_AGE_DAYS)).isoformat()


def test_the_cache_is_re_read_after_its_ttl_even_on_the_same_snapshot(monkeypatch):
    """volscore_features is filled AFTER a snapshot is published; a cache keyed on the snapshot alone would
    hold the empty store for ~12 h."""
    calls = []

    class _Db:
        def run(self, sql, **k):
            calls.append(1)
            return []

        def close(self):
            pass

    import db_pool
    server._OPEN_SETUPS_CACHE.update(gen=None, data=None, at=0.0)
    monkeypatch.setattr(db_pool, "get_db", lambda: _Db())
    snap = {"generated_utc": "same"}
    server._open_trigger_setups(snap); server._open_trigger_setups(snap)
    assert len(calls) == 1, "within the TTL the cache must serve"
    server._OPEN_SETUPS_CACHE["at"] -= server._OPEN_SETUPS_TTL + 1
    server._open_trigger_setups(snap)
    assert len(calls) == 2, "after the TTL it must re-read, even for the same snapshot"
    server._OPEN_SETUPS_CACHE.update(gen=None, data=None, at=0.0)


# ── Round 2, adversarial cases (2026-10-08) ─────────────────────────────────────────────────────────

def test_a_short_keeps_its_direction(monkeypatch):
    short = {**ARM_18_SEP, "direction": "BEAR"}
    r = _records(monkeypatch, {"ARM": [short]})
    assert r["direction"] == "BEAR" and r["report_ok"] is True


def test_a_row_the_scan_now_calls_ready_is_shown_as_its_open_trigger(monkeypatch):
    """The scan moved on to a newer, untriggered funnel; the older open trigger is what he acts on."""
    r = _records(monkeypatch, {"ARM": [ARM_18_SEP]}, status="READY")
    assert r["status"] == "TRIGGERED" and r["trig_date"] == "2026-09-18" and r["report_ok"] is True


def test_a_ready_row_with_no_passing_trigger_is_left_alone(monkeypatch):
    r = _records(monkeypatch, {"ARM": [ARM_29_SEP]}, status="READY")
    assert r["status"] == "READY" and "report_ok" not in r


def test_no_market_cap_cannot_pass_a_minimum_instrument_value():
    """Same as the money path (ig_shim, require_data=True): an index with no market cap is unverifiable."""
    assert server._report_setup("Alex", "^BSESN", [ARM_18_SEP], None, LIMITS) is None


def test_a_user_with_no_saved_limits_is_judged_on_the_code_defaults(monkeypatch):
    seen = []

    def defaults(name):
        seen.append(name)
        return trading_limits.limit_defaults()
    monkeypatch.setattr(trading_limits, "user_limits", defaults)
    server._report_setup("", "ARM", [ARM_18_SEP], ARM_MCAP, None)
    assert seen == [""], "with no limits passed, the login's own (default) limits must be read"


def test_setups_come_back_newest_trigger_first(monkeypatch):
    """_report_setup takes the FIRST passing setup, so the order is the rule: newest trigger first."""
    rows = [("ARM", "2026-09-18", "BULLISH", "daily-240", 1, 0.9, 2, 5.77, 52, 1.8, None, None, None),
            ("ARM", "2026-09-29", "BULLISH", "daily-240", 1, 0.9, 2, 19.69, 45, 1.08, None, None, None)]

    class _Db:
        def run(self, sql, **k):
            return rows

        def close(self):
            pass

    import db_pool, volscore_store
    server._OPEN_SETUPS_CACHE.update(gen=None, data=None, at=0.0)
    monkeypatch.setattr(db_pool, "get_db", lambda: _Db())
    monkeypatch.setattr(volscore_store, "load", lambda cutoff, db=None: {})
    m = server._open_trigger_setups({"generated_utc": "order"})
    server._OPEN_SETUPS_CACHE.update(gen=None, data=None, at=0.0)
    assert [s["trig_date"] for s in m["ARM"]] == ["2026-09-29", "2026-09-18"]


# ── Round 3: an unreadable history must never show an unvetted list (2026-10-08) ───────────────────────

def test_unreadable_history_hides_triggered_rows_and_says_so(monkeypatch):
    """Measured 2026-10-08 with the database unreachable: 215 triggered rows shown with none of the
    owner's trigger-date rules applied. On a real-money report that is worse than showing nothing."""
    r = _records(monkeypatch, None)
    assert r["report_ok"] is False


def test_unreadable_history_flags_the_response(monkeypatch):
    _records(monkeypatch, None)
    body = server.app.test_client().get("/api/records", headers={"X-Auth": "tok"}).get_json()
    assert body["report_unavailable"] is True


def test_the_last_verified_copy_is_served_when_the_history_is_unreadable(monkeypatch):
    import db_pool
    good = {"ARM": [ARM_18_SEP]}
    server._OPEN_SETUPS_CACHE.update(gen="old", data=good, at=0.0)

    def boom():
        raise OSError("database unreachable")
    monkeypatch.setattr(db_pool, "get_db", boom)
    try:
        assert server._open_trigger_setups({"generated_utc": "new"}) is good
    finally:
        server._OPEN_SETUPS_CACHE.update(gen=None, data=None, at=0.0)


def test_the_page_tells_the_user_when_triggered_rows_are_hidden():
    html = __import__("client_source").client_source()
    assert "REPORT_UNAVAILABLE=!!j.report_unavailable" in html
    assert "Triggered setups hidden" in html
