"""Identifying live orders that no longer match the user's trading filters (user 2026-08-29).

The rule this file exists to enforce: a MISSING metric is UNKNOWN, never a breach. A first attempt at
this check reported 52 of 55 orders breaching require_above_vwap, which was purely an artefact of
above_vwap not being recorded daily. Reporting that would have been a false alarm about real money.
"""
import order_filter_audit as ofa

LIMITS = {"min_risk_reward": 3.0, "min_quality": 50, "min_rvol": 1.5,
          "require_above_vwap": 1, "require_atr_expanding": 0,
          "min_instrument_value": 0, "max_instrument_value": 0}


def _check(rec=None, metrics=None, limits=None, gate_ok=True):
    return ofa.check_one(rec or {}, metrics or {}, LIMITS if limits is None else limits, gate_ok)


# ------------------------------------------------------------------------------------------------------
# The rule that matters most
# ------------------------------------------------------------------------------------------------------

def test_a_missing_metric_is_unknown_not_a_breach():
    """THE BUG THIS PREVENTS. above_vwap was unrecorded, and a naive check called every order a breach."""
    out = _check(rec={"rr": 4.0, "quality": 70, "rvol": 2.0})     # no above_vwap anywhere

    assert out["verdict"] == "UNKNOWN"
    assert out["breaches"] == [], "an absence of evidence must never be reported as a breach"
    assert any("above VWAP" in u for u in out["unknown"])


def test_a_recorded_point_in_time_failure_is_stale_not_a_breach():
    """RVOL and VWAP decay after the spike that produced the setup, so a pending order missing them days
    later is normal. Counting those as breaches produced "51 of 55" on 2026-08-29 when only 9 orders
    were breaching anything durable -- an alarming number that would have prompted the wrong action."""
    out = _check(rec={"rr": 4.0, "quality": 70, "rvol": 2.0, "above_vwap_setup": False})

    assert out["verdict"] == "STALE"
    assert out["persistent"] == [] and out["point_in_time"]


def test_a_setup_property_failure_is_a_durable_breach():
    """R:R comes from the setup's own levels and does not change, so this one IS a fault."""
    out = _check(rec={"rr": 2.0, "quality": 70, "rvol": 2.0, "above_vwap_setup": True})

    assert out["verdict"] == "BREACH"
    assert out["persistent"] == ["R:R 2.0 < 3.0"]


def test_everything_satisfied_is_ok():
    out = _check(rec={"rr": 4.0, "quality": 70, "rvol": 2.0, "above_vwap_setup": True})

    assert out["verdict"] == "OK" and not out["breaches"] and not out["unknown"]


# ------------------------------------------------------------------------------------------------------
# Which VWAP is used — getting this wrong inverts every BEAR verdict
# ------------------------------------------------------------------------------------------------------

def test_the_direction_aware_setup_metric_is_used_not_the_literal_one():
    """A BEAR setup confirms BELOW its VWAP. instrument_metrics stores that as above_vwap_setup; the
    literal instrument metric (above_vwap) would give the opposite answer for every BEAR row."""
    base = {"rr": 4.0, "quality": 70, "rvol": 2.0}

    confirmed = _check(rec=dict(base, above_vwap=False, above_vwap_setup=True))
    not_confirmed = _check(rec=dict(base, above_vwap=True, above_vwap_setup=False))

    assert confirmed["verdict"] == "OK", "a BEAR setup confirmed below VWAP must pass"
    assert not_confirmed["verdict"] == "STALE", "the literal metric must not be used for the filter"
    assert any("above VWAP" in b for b in not_confirmed["point_in_time"])


# ------------------------------------------------------------------------------------------------------
# Numeric floors and the gate
# ------------------------------------------------------------------------------------------------------

def test_each_numeric_floor_is_reported_with_both_numbers():
    out = _check(rec={"rr": 2.5, "quality": 70, "rvol": 2.0, "above_vwap_setup": True})

    assert out["verdict"] == "BREACH"
    assert out["breaches"] == ["R:R 2.5 < 3.0"], "the report must show what it had and what it needed"
    assert out["persistent"] == ["R:R 2.5 < 3.0"], "R:R is a setup property, so it is a durable breach"


def test_a_floor_of_zero_or_missing_never_blocks():
    out = _check(rec={"rr": 0.1, "quality": 0, "rvol": 0.1, "above_vwap_setup": True},
                 limits={"min_risk_reward": 0, "min_quality": None, "require_above_vwap": 1})

    assert out["verdict"] == "OK"


def test_the_server_gate_is_reported_as_a_breach():
    out = _check(rec={"rr": 4.0, "quality": 70, "rvol": 2.0, "above_vwap_setup": True}, gate_ok=False)

    assert out["verdict"] == "BREACH"
    assert any("direction/location/market" in b for b in out["breaches"])


def test_daily_metrics_fill_in_what_the_snapshot_lacks():
    """The point of recording daily: an order placed days ago can still be judged."""
    out = _check(rec={"rr": 4.0, "quality": 70}, metrics={"rvol": 2.0, "above_vwap_setup": True})

    assert out["verdict"] == "OK"


def test_the_snapshot_wins_where_both_have_a_value():
    """A setup is judged on the figures it was recorded with, not on today's."""
    out = _check(rec={"rr": 4.0, "quality": 70, "rvol": 2.0, "above_vwap_setup": True},
                 metrics={"rvol": 0.1, "above_vwap_setup": False})

    assert out["verdict"] == "OK"


def test_instrument_value_bounds_are_checked_and_unknown_when_absent():
    lim = dict(LIMITS, min_instrument_value=1e9, max_instrument_value=0, require_above_vwap=0)
    good = _check(rec={"rr": 4.0, "quality": 70, "rvol": 2.0, "mcap": 5e9}, limits=lim)
    small = _check(rec={"rr": 4.0, "quality": 70, "rvol": 2.0, "mcap": 1e8}, limits=lim)
    absent = _check(rec={"rr": 4.0, "quality": 70, "rvol": 2.0}, limits=lim)

    assert good["verdict"] == "OK"
    assert small["verdict"] == "BREACH"
    assert absent["verdict"] == "UNKNOWN"


# ------------------------------------------------------------------------------------------------------
# The audit as a whole
# ------------------------------------------------------------------------------------------------------

def test_audit_summarises_every_order(monkeypatch):
    recs = {"AAA": {"rr": 4.0, "quality": 70, "rvol": 2.0, "above_vwap_setup": True},
            "BBB": {"rr": 1.0, "quality": 70, "rvol": 2.0, "above_vwap_setup": True},
            "CCC": {"rr": 4.0, "quality": 70, "rvol": 2.0}}
    import instrument_metrics
    monkeypatch.setattr(instrument_metrics, "latest", lambda t: {})

    out = ofa.audit("Alex", tickers=list(recs), record_for=recs.get,
                    allows=lambda rec: True, limits=LIMITS)

    assert out["orders"] == 3
    # CCC has no above_vwap. Since 2026-09-03 that is NOT a finding for a pending order: above-VWAP is a
    # break-bar measure and an order reaches IG a median 8 days before its break, so there is nothing to
    # judge. It passes on the criteria that ARE applicable. See docs/ORDER_TIMING_AND_RVOL.md.
    assert out["counts"] == {"OK": 2, "BREACH": 1, "STALE": 0, "UNKNOWN": 0}


def test_break_bar_metrics_are_not_applied_to_a_pending_order(monkeypatch):
    """RVOL, VolumeScore, VWAP and ATR describe the breakout bar, which has not happened yet.

    Judging a pending order on them produced 40 "failures" on the live book that were not failures.
    """
    import instrument_metrics
    monkeypatch.setattr(instrument_metrics, "latest", lambda t: {})
    weak = {"WEAK": {"rr": 9.0, "quality": 90, "rvol": 0.2, "above_vwap_setup": False,
                     "atr_expanding": False}}

    out = ofa.audit("Alex", tickers=["WEAK"], record_for=weak.get,
                    allows=lambda rec: True, limits=LIMITS)
    row = out["rows"][0]

    assert row["verdict"] == "OK", f"break-bar metrics must not condemn a pending order: {row}"
    assert not any("RVOL" in b or "VWAP" in b or "ATR" in b for b in row["breaches"])
    assert out["counts"]["STALE"] == 0, "the STALE verdict is gone; there is nothing to be stale"


def test_a_pending_audit_returns_only_three_verdicts(monkeypatch):
    """The IG Account breach panel pre-ticks EVERY order this returns, which is only safe while every
    verdict it can return is one the requester has ruled on. A fourth verdict appearing here would be
    silently cancelled by that screen, so it is pinned on this side too."""
    import instrument_metrics
    monkeypatch.setattr(instrument_metrics, "latest", lambda t: {})
    recs = {"GOOD": {"rr": 9.0, "quality": 90},                  # passes every durable floor
            "LOWRR": {"rr": 1.0, "quality": 90},                 # durable breach
            "THIN": {"rr": 9.0, "rvol": 0.1, "above_vwap_setup": False},   # break-bar only -> not judged
            "NOQ": {"rr": 9.0}}                                  # quality absent -> unjudgeable

    out = ofa.audit("Alex", tickers=list(recs), record_for=recs.get,
                    allows=lambda rec: True, limits=LIMITS)

    assert {r["verdict"] for r in out["rows"]} <= {"OK", "BREACH", "UNKNOWN"}


def test_an_order_whose_instrument_left_the_snapshot_is_unknown_not_ok(monkeypatch):
    """Silence must not read as approval: a delisted or dropped instrument is unresolvable, not passing."""
    import instrument_metrics
    monkeypatch.setattr(instrument_metrics, "latest", lambda t: {})

    out = ofa.audit("Alex", tickers=["GONE"], record_for=lambda t: None,
                    allows=lambda rec: True, limits=LIMITS)

    assert out["counts"]["OK"] == 0
    assert out["rows"][0]["verdict"] == "UNKNOWN"
    assert any("not in the current snapshot" in u for u in out["rows"][0]["unknown"])


# ── Break-bar metrics must come from the break bar (2026-09-06) ───────────────────────────────────────
#
# break_state selected on as_of alone, and as_of is NOT the bar the row describes.
# instrument_metrics.record_daily runs inside the daily scan, which fires in the Morning Chain at ~03:30
# UTC -- before any market opens -- so the row written under as_of = today is computed from YESTERDAY'S
# completed bar. Measured on the live table: of the rows for as_of 2026-09-05, 1,761 carry bar_date
# 2026-09-04, with a tail older still (one 33 days behind).
#
# These four measures describe the BREAK bar, and for a position opened today the break IS today. Judging
# it on yesterday's reading would close a real position on the wrong day's evidence -- worse than closing
# on none, because it looks like evidence.

class _BarDb:
    def __init__(self, bar_date):
        self._bar = bar_date
    def run(self, sql, **kw):
        assert "bar_date" in sql, "the query must read bar_date, or the staleness cannot be detected"
        return [[2.5, True, True, 9, kw["d"], "complete", self._bar]]
    def close(self):
        pass


def test_metrics_from_the_wrong_bar_are_treated_as_unjudgeable():
    state = ofa.break_state([("BP.L", "2026-09-05")], db=_BarDb("2026-09-04"))["BP.L"]

    assert state["rvol"] is None and state["volume_score"] is None, \
        "yesterday's reading must not be presented as the break bar's"
    assert state["above_vwap_setup"] is None and state["atr_expanding"] is None
    assert state["status"].startswith("stale_bar:"), \
        "the reason must be visible, not silently blank"
    assert "2026-09-04" in state["status"], "say WHICH bar was found, so the gap can be diagnosed"


def test_metrics_from_the_right_bar_are_used():
    state = ofa.break_state([("BP.L", "2026-09-05")], db=_BarDb("2026-09-05"))["BP.L"]

    assert state["rvol"] == 2.5 and state["volume_score"] == 9
    assert state["above_vwap_setup"] is True and state["atr_expanding"] is True
    assert state["status"] == "complete"


def test_an_unjudgeable_position_is_never_closed():
    """The caller must read a stale bar as 'leave it alone', not as a failed test. This is the property
    that stops a data-pipeline gap from selling a position."""
    import auto_close_failed_opens as ac
    row = {"ticker": "BP.L", "breaches": [], "unknown": ["RVOL is unavailable"], "verdict": "UNKNOWN"}
    assert ac._volume_breaches(row) == [], "an unknown is not a breach and must not justify a close"
