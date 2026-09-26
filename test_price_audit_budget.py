"""A slow price audit must stop itself, not be killed and take the Morning Chain down with it.

WHY (all measured 2026-09-26). trading-price-refresh.yml caps the job at 90 minutes. On BOTH Saturdays in
the fetched history -- 2026-09-19 and 2026-09-26 -- the Morning Chain's price refresh hit that cap and
GitHub killed it (which reports as "cancelled", not a timeout), and all five downstream jobs were skipped:
the HVF report and with it instrument_metrics.record_daily -- so the Scanner's RVOL/VWAP/ATR columns went
blank -- the scanner email, the winners precompute, the best-settings audit, and HVF orders. One slow pass
took out six jobs.

IT WAS NOT HUNG, which is why a timeout on the network calls is not the answer (and both already have one:
yfinance defaults to timeout=10, ig_shim uses 15). From its own progress lines it reached 1475 of 1773 in
80 minutes -- 3.25 s/ticker against 0.65-1.0 on a weekday -- and needed about 16 minutes more. The cause is
the IG cross-check: at the same point in the run, Friday had made 5 IG fixes in 17.3 minutes and Saturday
4,938 in 79.6. price_audit_log records "IG fixes 5" or 6 on every weekday pass and 1,069 on the Saturday
23:00 one.

WHAT THESE TESTS PIN, and what they deliberately do not. They pin that an overrunning pass ends cleanly and
says so. They do NOT address why Saturday disagrees -- whether those corrections are right or wrong is a
data-integrity question still open, and the cross-check exists because real phantom LSE prints once cost a
missed HVF, so it must not be disabled on a guess.

A killed run also wrote thousands of bars and left NO row in price_audit_log, because the insert only runs
after the loop. A self-imposed budget is what makes the record possible at all.
"""

import io
import time

import pytest

import price_audit


class _Db:
    """Captures the audit-log insert so the recorded notes can be asserted."""

    def __init__(self):
        self.inserts = []

    def run(self, sql, **kw):
        if "insert into price_audit_log" in sql:
            self.inserts.append(kw)
        return []

    def close(self):
        pass


def _wire(monkeypatch, n_tickers, seconds_per_ticker=0.0):
    """Run the real run() over `n_tickers` fake instruments, each costing `seconds_per_ticker`."""
    db = _Db()
    clock = {"t": 1000.0}
    monkeypatch.setattr(price_audit.time, "time", lambda: clock["t"])
    monkeypatch.setattr(price_audit, "_universe",
                        lambda: {f"T{i}.L": f"T{i}.L" for i in range(n_tickers)})
    monkeypatch.setattr(price_audit, "get_db", lambda *a, **k: db)
    monkeypatch.setattr(price_audit, "_ensure_audit_log", lambda d: None)
    monkeypatch.setattr(price_audit.price_store, "ensure_schema", lambda d: None)
    monkeypatch.setattr(price_audit.price_store, "prune_older_than", lambda: 0)

    def _tick(tk, ysym, window_days, source, d):
        clock["t"] += seconds_per_ticker           # the only thing that advances the fake clock
        return 1, 0, None

    monkeypatch.setattr(price_audit, "audit_ticker", _tick)
    return db


def _notes(db):
    assert db.inserts, "the audit-log row was never written"
    return db.inserts[-1]["no"]


# ── the defect: an overrunning pass must stop, not be killed ────────────────────────────────────────────

def test_a_pass_that_exceeds_its_budget_stops_early(monkeypatch):
    """60 tickers at 10s each is 600s against a 100s budget: it must stop, not run to the end."""
    db = _wire(monkeypatch, 60, seconds_per_ticker=10.0)
    price_audit.run("daily", 30, "YF", use_ig=False, max_seconds=100)
    assert db.inserts, "a truncated pass must still record itself"
    assert db.inserts[-1]["tc"] < 60, "it must not have checked every ticker"
    assert db.inserts[-1]["tc"] > 0, "and it must have done real work before stopping"


def test_the_truncation_is_recorded_in_the_audit_log(monkeypatch):
    """THE POINT OF THE ROW. A truncated pass that reads identically to a complete one is the
    silent-failure shape this repository keeps producing."""
    db = _wire(monkeypatch, 60, seconds_per_ticker=10.0)
    price_audit.run("daily", 30, "YF", use_ig=False, max_seconds=100)
    assert "TRUNCATED" in _notes(db)
    assert "/60" in _notes(db), "the notes must say how far it got, out of how many"


def test_a_pass_inside_its_budget_is_not_truncated(monkeypatch):
    """The common case must be untouched -- no truncation note, every ticker checked."""
    db = _wire(monkeypatch, 40, seconds_per_ticker=1.0)
    price_audit.run("daily", 30, "YF", use_ig=False, max_seconds=4500)
    assert db.inserts[-1]["tc"] == 40
    assert "TRUNCATED" not in _notes(db)


def test_a_zero_budget_means_no_limit(monkeypatch):
    """The escape hatch: a deliberate long backfill must not be cut off."""
    db = _wire(monkeypatch, 40, seconds_per_ticker=1000.0)
    price_audit.run("daily", 30, "YF", use_ig=False, max_seconds=0)
    assert db.inserts[-1]["tc"] == 40
    assert "TRUNCATED" not in _notes(db)


def test_the_budget_is_checked_before_the_work_not_after(monkeypatch):
    """Checking after the call would let the LAST ticker start arbitrarily late and overrun anyway, which
    with a 15-minute margin is the difference between finishing and being killed."""
    db = _wire(monkeypatch, 10, seconds_per_ticker=60.0)
    price_audit.run("daily", 30, "YF", use_ig=False, max_seconds=120)
    assert db.inserts[-1]["tc"] <= 3, f"started too many tickers past the budget: {db.inserts[-1]['tc']}"


# ── the default, and where it came from ────────────────────────────────────────────────────────────────

def test_the_default_budget_leaves_room_inside_the_workflow_cap():
    """The workflow kills the job at 90 minutes; the pass must stop with time left for the prune, the
    audit-log write and the Slack summary. If someone raises one without the other this fails."""
    cap_minutes = None
    for line in io.open(".github/workflows/trading-price-refresh.yml", encoding="utf-8"):
        if "timeout-minutes:" in line:
            cap_minutes = int(line.split(":")[1].strip())
            break
    assert cap_minutes, "the price-refresh workflow has no timeout-minutes to reason about"
    assert price_audit.MAX_SECONDS < cap_minutes * 60, (
        f"the self-imposed budget ({price_audit.MAX_SECONDS}s) must be inside the workflow cap "
        f"({cap_minutes * 60}s), or it can never take effect")
    assert cap_minutes * 60 - price_audit.MAX_SECONDS >= 600, (
        "leave at least 10 minutes for the prune, the audit-log write and the summary")


def test_the_budget_can_be_set_by_environment(monkeypatch):
    """So the cap and the budget can be changed together from the workflow without a code edit."""
    monkeypatch.setenv("PRICE_AUDIT_MAX_SECONDS", "1234")
    import importlib
    reloaded = importlib.reload(price_audit)
    try:
        assert reloaded.MAX_SECONDS == 1234
    finally:
        monkeypatch.delenv("PRICE_AUDIT_MAX_SECONDS", raising=False)
        importlib.reload(price_audit)
