# Session Watchdog judges only sessions cron-job.org will fire (2026-10-08).
#
# AUS/UK/US Open are DISABLED on cron-job.org by the owner's decision (session monitors off; last runs
# 2026-08-06). The watchdog nevertheless alerted "did not start" every 10 minutes and tried to re-dispatch
# them; only a 403 on the token stopped it actually restarting sessions that are off on purpose.

import run_cron_watch
import run_session_watchdog as W


class _Conn:
    def close(self):
        pass


def _run(monkeypatch, enabled):
    judged = []
    monkeypatch.setattr(run_cron_watch, "_enabled_titles", lambda: enabled)
    monkeypatch.setattr(W, "get_db", lambda: _Conn())
    monkeypatch.setattr(W, "check_session", lambda conn, name, *a: judged.append(name) or True)
    W.main()
    return judged


def test_disabled_sessions_are_neither_judged_nor_re_dispatched(monkeypatch):
    assert _run(monkeypatch, {"Session Watchdog", "Closing Window"}) == []


def test_an_enabled_session_is_still_judged(monkeypatch):
    assert _run(monkeypatch, {"UK Open"}) == ["UK_OPEN"]


def test_unknown_enabled_state_judges_everything_rather_than_nothing(monkeypatch):
    assert _run(monkeypatch, None) == ["AUS_OPEN", "UK_OPEN", "US_OPEN"]
