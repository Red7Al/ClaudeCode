"""P-04 check 1: a report trigger in the ordering window ends a bridge pass ordered, skipped with a recorded
reason, or too old. Anything else is emailed."""
import pytest
from hvf_web import order_bridge

SIG = lambda t: {"ticker": t, "direction": "BUY", "trig_date": "2026-11-06", "hvf_h3_level": 10.0}


class _Db:
    def __init__(self, recorded): self.recorded = recorded
    def run(self, sql, **kw): return [(t,) for t in self.recorded if t in kw.get("t", [])]
    def close(self): pass


def _run(monkeypatch, tickers, placer, recorded=(), cap=None, working=(), ready=(), db=None):
    import ig_shim, trading_limits
    sent = []
    cands = [dict(ticker=t, _sig=SIG(t), current_price=10) for t in tickers]
    cands += [dict(ticker=t, direction='BULL', status='READY', entry=10, stop=9, target=12) for t in ready]
    monkeypatch.setattr("run_session.get_user_profile", lambda: {"name": "Owner"})
    monkeypatch.setattr(trading_limits, "login_for_profile", lambda p: "Alex")
    monkeypatch.setattr(order_bridge, "_candidates", lambda login=None: cands)
    monkeypatch.setattr(order_bridge, "_already_working", lambda owner=None: set(working))
    monkeypatch.setattr(order_bridge, "_ordered_triggers", lambda sigs, owner=None: set())
    monkeypatch.setattr(ig_shim, "place_hvf_order_from_sig", placer)
    monkeypatch.setattr(ig_shim, "run_let_winners_run", lambda: {})
    monkeypatch.setattr("hvf_web.server._live_vwap_atr", lambda s: {}, raising=False)
    monkeypatch.setattr("hvf_web.server._load_snapshot", lambda: {}, raising=False)
    monkeypatch.setattr("db_pool.get_db", db or (lambda: _Db(recorded)))
    monkeypatch.setattr("notify.alert_system_error", lambda *a, **k: sent.append(a))
    if cap is not None:
        monkeypatch.setattr(order_bridge, "BRIDGE_MAX_PER_RUN", cap)
    order_bridge.run_bridge()
    return sent


def test_placed_trigger_is_not_alerted(monkeypatch):
    assert _run(monkeypatch, ["AAA"], lambda *a, **k: {"status": "ok"}) == []


def test_no_order_with_recorded_reason_is_not_alerted(monkeypatch):
    assert _run(monkeypatch, ["AAA"], lambda *a, **k: None, recorded=["AAA"]) == []


def test_no_order_and_no_reason_is_alerted(monkeypatch):
    sent = _run(monkeypatch, ["AAA", "BBB"], lambda *a, **k: None, recorded=["AAA"])
    assert len(sent) == 1 and "BBB" in sent[0][3] and "AAA" not in sent[0][3]


def test_exception_is_alerted_with_its_text(monkeypatch):
    def boom(*a, **k): raise RuntimeError("IG said no")
    sent = _run(monkeypatch, ["AAA"], boom)
    assert len(sent) == 1 and "IG said no" in sent[0][3]


def test_cap_deferred_trigger_is_alerted(monkeypatch):
    sent = _run(monkeypatch, ["AAA", "BBB"], lambda *a, **k: {"status": "ok"}, cap=1)
    assert len(sent) == 1 and "BBB" in sent[0][3] and "per-run cap" in sent[0][3]


def test_already_working_trigger_is_not_alerted(monkeypatch):
    assert _run(monkeypatch, ["AAA"], lambda *a, **k: None, working=["AAA"]) == []


def test_cap_does_not_alert_already_working_trigger(monkeypatch):
    sent = _run(monkeypatch, ["AAA", "BBB", "CCC"], lambda *a, **k: {"status": "ok"}, cap=1, working=["BBB"])
    assert len(sent) == 1 and "CCC" in sent[0][3] and "BBB" not in sent[0][3]


def test_ready_setup_without_order_is_not_alerted(monkeypatch):
    assert _run(monkeypatch, [], lambda *a, **k: None, ready=["RDY"]) == []


def test_database_failure_alerts_every_unordered_trigger(monkeypatch):
    def down(): raise RuntimeError("pool full")
    sent = _run(monkeypatch, ["AAA", "BBB"], lambda *a, **k: None, db=down)
    assert len(sent) == 1 and "AAA" in sent[0][3] and "BBB" in sent[0][3]


def test_alert_email_failure_does_not_break_the_pass(monkeypatch):
    def bad(*a, **k): raise RuntimeError("smtp")
    monkeypatch.setattr("db_pool.get_db", lambda: _Db([]))
    monkeypatch.setattr("notify.alert_system_error", bad)
    assert order_bridge._alert_unaccounted({"AAA": "x"}, {"AAA": SIG("AAA")}) == ["AAA"]


def test_lookup_is_todays_rows_of_missed_trade_log(monkeypatch):
    seen = []
    class D(_Db):
        def run(self, sql, **kw): seen.append(" ".join(sql.split())); return []
    monkeypatch.setattr("db_pool.get_db", lambda: D([]))
    monkeypatch.setattr("notify.alert_system_error", lambda *a, **k: None)
    order_bridge._alert_unaccounted({"AAA": "x"}, {"AAA": SIG("AAA")})
    assert "from missed_trade_log where trade_date = current_date and ticker = any(:t)" in seen[0]
