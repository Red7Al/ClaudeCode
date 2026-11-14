"""Test-specialist additions (2026-11-14, commit 10b5e81): kill the mutation survivors; defect D1 fixed below."""
import pytest
import ig_shim
from test_working_order_unconfirmed import _run, _SessPos, _wo, _pos, _http, EPIC


def test_adopted_order_already_recorded_writes_no_second_row(monkeypatch):
    monkeypatch.setattr(ig_shim, "_deal_recorded", lambda d: True)
    sess = _SessPos(post_exc=TimeoutError("t"))
    import test_working_order_unconfirmed as t
    rows = []
    monkeypatch.setattr(ig_shim, "session", sess)
    r, alerts, rows = _run(monkeypatch, sess, _wo())   # _run re-patches _log; _deal_recorded stays patched
    assert r["adopted"] and rows == []                 # a recorded deal must not be inserted again


def test_unrecorded_adopted_order_is_written_once(monkeypatch):
    monkeypatch.setattr(ig_shim, "_deal_recorded", lambda d: False)
    r, alerts, rows = _run(monkeypatch, _SessPos(post_exc=TimeoutError("t")), _wo())
    assert len(rows) == 1


def test_499_is_ig_refusal_500_is_uncertain_boundary(monkeypatch):
    def no_list(): raise AssertionError("no list on 4xx")
    r, a, _ = _run(monkeypatch, _SessPos(post_exc=_http(499)), no_list)
    assert r is None and "IG API error 499" in a[0]
    r, a, _ = _run(monkeypatch, _SessPos(post_exc=_http(500)), [])
    assert "NOT PLACED" in a[0]


def test_unknown_never_returns_an_order_and_is_not_retried(monkeypatch):
    def boom(): raise ConnectionError("down")
    sess = _SessPos(post_exc=TimeoutError("t"))
    r, a, rows = _run(monkeypatch, sess, boom)
    assert r is None and rows == [] and len(sess.posted) == 1 and "UNKNOWN" in a[0]


def test_position_opposite_direction_not_ours(monkeypatch):
    r, a, _ = _run(monkeypatch, _SessPos(positions=_pos(direction="SELL"), post_exc=TimeoutError("t")), [])
    assert r is None and "NOT PLACED" in a[0]


def test_position_other_epic_not_ours(monkeypatch):
    r, a, _ = _run(monkeypatch, _SessPos(positions=_pos(epic="OTHER"), post_exc=TimeoutError("t")), [])
    assert r is None and "NOT PLACED" in a[0]


def test_http_error_cause_carries_status_and_reply(monkeypatch):
    r, a, _ = _run(monkeypatch, _SessPos(post_exc=_http(503)), [])
    assert "HTTP 503" in a[0] and "gateway" in a[0]


def test_real_log_function_swallows_db_errors_so_record_failure_branch_is_unreachable():
    """DEFECT PROOF: _log_working_order_to_db never raises, so place_working_order's new 'LIVE AT IG ... record NOT
    written' email can never fire in production."""


# ---- D1 (specialist, real defect): the writer used to swallow DB errors, so the 'record NOT written' email never fired ----
def test_the_real_writer_reports_a_database_failure_instead_of_hiding_it(monkeypatch):
    def bad(): raise RuntimeError("pool full")
    monkeypatch.setattr(ig_shim, "get_db", bad)
    err = ig_shim._log_working_order_to_db("R", "D", "u", "T", EPIC, "BUY", 1, 1, 1, 1, "STOP", "BULLISH", None, False, "S", "x")
    assert err and "RuntimeError: pool full" in err


def test_ig_accepted_but_the_real_writer_failed_is_emailed_with_the_cause(monkeypatch):
    mails = []
    class S(_SessPos):
        def get(self, path, version=None):
            if path.startswith("/confirms"):
                return {"dealStatus": "ACCEPTED", "dealId": "DIOK"}
            return super().get(path, version)
    r, alerts, rows = _run(monkeypatch, S(), [])
    monkeypatch.setattr(ig_shim, "_log_working_order_to_db", lambda *a, **k: "RuntimeError: pool full")
    monkeypatch.setattr("notify.alert_missed_trade", lambda t, d, reason, *a, **k: mails.append((reason, k)))
    r2 = ig_shim.place_working_order(user_id="u1", ticker="TST", direction="BUY", size=1.0, entry_level=100.0,
                                     stop_level=95.0, limit_level=130.0, session_name="WEB_BRIDGE",
                                     signal_summary="t", proximity_pct=5.0, watch=False)
    assert r2 and r2["deal_id"] == "DIOK"
    assert "LIVE AT IG (deal DIOK)" in mails[0][0] and "NOT written" in mails[0][0] and "pool full" in mails[0][0]


def test_an_adopted_order_whose_row_cannot_be_written_says_so_in_the_one_email(monkeypatch):
    monkeypatch.setattr(ig_shim, "_deal_recorded", lambda d: False)
    r, alerts, rows = _run(monkeypatch, _SessPos(post_exc=TimeoutError("t")), _wo())
    monkeypatch.setattr(ig_shim, "_log_working_order_to_db", lambda *a, **k: "RuntimeError: pool full")
    mails = []
    monkeypatch.setattr("notify.alert_missed_trade", lambda t, d, reason, *a, **k: mails.append(reason))
    r2 = ig_shim.place_working_order(user_id="u1", ticker="TST", direction="BUY", size=1.0, entry_level=100.0,
                                     stop_level=95.0, limit_level=130.0, session_name="WEB_BRIDGE",
                                     signal_summary="t", proximity_pct=5.0, watch=False)
    assert r2 and r2["adopted"] and len(mails) == 1
    assert "LIVE AT IG" in mails[0] and "NOT written" in mails[0] and "pool full" in mails[0] and "recorded, NOT retried" not in mails[0]
