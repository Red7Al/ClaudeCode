"""Closing Window history comes from the price bars file, fetched only when something is being judged (owner
2026-10-22: "400 days history every 10 mins should not come from supabase - unless performance is terrible
from flat files"). MEASURED 2026-10-21: 51 of 51 runs had nothing in a closing window."""
import datetime as dt

import closing_bar_capture as cbc
import instrument_metrics
import price_bars

HIST = [((dt.date(2026, 9, 1) + dt.timedelta(days=i)).isoformat(), 2.0, 1.0, 1.5, 100.0) for i in range(40)]


class _Db:
    def run(self, sql, **kw):
        return []

    def close(self):
        pass


def _wire(monkeypatch):
    seen = {"fetch": 0, "bulk": []}
    monkeypatch.setattr(price_bars, "fetch_when_needed", lambda want="shadow": seen.__setitem__("fetch", seen["fetch"] + 1) or True)
    monkeypatch.setattr(instrument_metrics, "ensure_schema", lambda db: None)
    monkeypatch.setattr(instrument_metrics, "_bars_bulk", lambda ts, end, db, **k: seen["bulk"].append(list(ts)) or {t: HIST for t in ts})
    monkeypatch.setattr(instrument_metrics, "_bars", lambda *a, **k: (_ for _ in ()).throw(AssertionError("one read per ticker")))
    monkeypatch.setattr(cbc, "todays_bar", lambda t: ("2026-10-22", 2.1, 1.1, 2.0, 500.0))
    return seen


def test_one_read_for_every_ticker_through_the_file_reader(monkeypatch):
    seen = _wire(monkeypatch)
    out = cbc.capture(["AAA", "BBB"], as_of=dt.date(2026, 10, 22), dry_run=True, db=_Db())
    assert seen == {"fetch": 1, "bulk": [["AAA", "BBB"]]}
    assert out["attempted"] == 2 and [r["ticker"] for r in out["rows"]] == ["AAA", "BBB"]


def test_a_run_with_nothing_to_judge_fetches_nothing(monkeypatch):
    seen = _wire(monkeypatch)
    cbc.capture([], dry_run=True, db=_Db())
    assert seen == {"fetch": 0, "bulk": []}


def test_the_fetch_does_nothing_outside_github_actions(monkeypatch):
    for k in ("PRICE_BARS_DIR", "RUNNER_TEMP", "GITHUB_ENV", "IONOS_SSH_KEY"):
        monkeypatch.delenv(k, raising=False)
    ran = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: ran.append(a))
    assert price_bars.fetch_when_needed() is False and ran == []


def test_a_successful_fetch_points_this_process_at_the_file(monkeypatch, tmp_path):
    import subprocess
    monkeypatch.delenv("PRICE_BARS_DIR", raising=False)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "env"))
    monkeypatch.setenv("IONOS_SSH_KEY", "k")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **k: calls.append(args) or subprocess.CompletedProcess(args, 0, "fetched", ""))
    assert price_bars.fetch_when_needed("shadow") is True
    assert calls[0][-1] == "shadow" and calls[0][1].endswith("fetch_price_bars.sh")
    import os
    assert os.environ["PRICE_BARS_DIR"] == str(tmp_path / "price_bars") and os.environ["PRICE_BARS"] == "shadow"


def test_a_failed_fetch_leaves_the_database_answering(monkeypatch, tmp_path):
    import subprocess
    monkeypatch.delenv("PRICE_BARS_DIR", raising=False)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "env"))
    monkeypatch.setenv("IONOS_SSH_KEY", "k")
    monkeypatch.setattr(subprocess, "run", lambda args, **k: subprocess.CompletedProcess(args, 1, "", "ssh refused"))
    assert price_bars.fetch_when_needed() is False
    import os
    assert "PRICE_BARS_DIR" not in os.environ


def test_nothing_is_fetched_when_no_instrument_has_todays_bar(monkeypatch):
    """Efficiency review 2026-10-22: the file was fetched and read before asking whether anything had a bar."""
    seen = _wire(monkeypatch)
    monkeypatch.setattr(cbc, "todays_bar", lambda t: None)
    out = cbc.capture(["AAA", "BBB"], as_of=dt.date(2026, 10, 22), dry_run=True, db=_Db())
    assert seen == {"fetch": 0, "bulk": []} and out["attempted"] == 2 and out["skipped"] == 2


def test_only_instruments_with_todays_bar_are_read(monkeypatch):
    seen = _wire(monkeypatch)
    monkeypatch.setattr(cbc, "todays_bar", lambda t: ("2026-10-22", 2.1, 1.1, 2.0, 500.0) if t == "BBB" else None)
    cbc.capture(["AAA", "BBB"], as_of=dt.date(2026, 10, 22), dry_run=True, db=_Db())
    assert seen["bulk"] == [["BBB"]]
