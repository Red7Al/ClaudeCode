"""price_bars: the one reader of the price bars file (owner 2026-10-21: "read files, not the database").

The fixture rows are real price_history rows (BP.L 2026-10-16 and 10-19, read 2026-10-21) written through
bars_file's own writer, so the file under test is in the exact format the build produces."""
import datetime as dt

import pytest

import bars_file
import price_bars
from hvf_web import server

D = dt.date
UPD1 = dt.datetime(2026, 10, 20, 4, 1, 2, 345678, tzinfo=dt.timezone.utc)
UPD2 = dt.datetime(2026, 10, 21, 3, 41, 9, 123456, tzinfo=dt.timezone.utc)
ROWS = [  # ticker, bar_date, open, high, low, close, volume, source, double_checked, updated_at
    ("BP.L", D(2026, 10, 16), 610.0, 616.572021484375, 602.575988769531, 614.099975585938, 99077085, "yahoo", True, UPD1),
    ("BP.L", D(2026, 10, 19), 615.0, 622.700012207031, 612.5, 616.400024414062, 25603773, "yahoo", False, UPD2),
    ("NVDA", D(2026, 10, 19), 180.0, 182.5, 178.25, 181.0, None, "yahoo", False, UPD1),
]


class FakeDb:
    """Answers the version probe from ROWS, and the bars query by running the filter itself."""
    def __init__(self, rows, probe=None):
        self.rows, self.probe, self.bar_queries = rows, probe, 0

    def run(self, sql, **kw):
        if sql.startswith("select count(*), max(updated_at) from price_history"):
            return [self.probe or (len(self.rows), max(r[9] for r in self.rows))]
        if "join (values" in sql:
            self.bar_queries += 1
            want = {kw[f"t{i}"]: D.fromisoformat(kw[f"d{i}"]) for i in range(len(kw) // 2)}
            return [(r[0], r[1], r[3], r[4], r[5], r[6]) for r in sorted(self.rows, key=lambda r: (r[0], r[1]))
                    if r[0] in want and r[1] >= want[r[0]]]
        if "max(bar_date)" in sql:   # server._price_data_version
            return [(max(r[1] for r in self.rows), len(self.rows))]
        raise AssertionError(sql)


@pytest.fixture
def bars_dir(tmp_path, monkeypatch):
    rows = {(r[0], str(r[1])): bars_file._normalise(r) for r in ROWS}
    bars_file._write(str(tmp_path), rows, {"build_started": UPD2.isoformat(), "rows": len(rows),
                                            "retention_cutoff": "2022-09-21"})
    monkeypatch.setenv("PRICE_BARS_DIR", str(tmp_path))
    price_bars._STATE.update(dir=None, by=None, meta=None, current=None)
    server._PB_CACHE.clear(); server._PB_ORDER.clear(); server._PB_VERSION.update(at=0, value="")
    yield tmp_path
    price_bars._STATE.update(dir=None, by=None, meta=None, current=None)
    server._PB_CACHE.clear(); server._PB_ORDER.clear(); server._PB_VERSION.update(at=0, value="")


CUT = {"BP.L": D(2026, 10, 19), "NVDA": D(2026, 10, 19), "ZZZ": D(2026, 10, 19)}


def test_on_serves_exactly_what_the_database_serves_without_asking_it(bars_dir, monkeypatch):
    monkeypatch.setenv("PRICE_BARS", "on")
    want = server._perf_bars(FakeDb(ROWS), CUT, lookback_days=3)   # computed by the database path below
    db = FakeDb(ROWS)
    server._PB_CACHE.clear(); server._PB_ORDER.clear()
    got = server._perf_bars(db, CUT, lookback_days=3)
    monkeypatch.setenv("PRICE_BARS", "off")
    server._PB_CACHE.clear(); server._PB_ORDER.clear()
    ref = server._perf_bars(FakeDb(ROWS), CUT, lookback_days=3)
    assert got == ref == want and db.bar_queries == 0
    assert got["BP.L"][0] == (D(2026, 10, 16), 616.572021484375, 602.575988769531, 614.099975585938, 99077085)
    assert got["NVDA"] == [(D(2026, 10, 19), 182.5, 178.25, 181.0, None)] and "ZZZ" not in got
    assert [type(v).__name__ for v in got["BP.L"][1]] == ["date", "float", "float", "float", "int"]


def test_a_write_after_the_build_makes_the_file_stale_and_the_database_answers(bars_dir, monkeypatch):
    monkeypatch.setenv("PRICE_BARS", "on")
    later = UPD2 + dt.timedelta(seconds=1)
    db = FakeDb(ROWS, probe=(len(ROWS), later))
    server._perf_bars(db, CUT, lookback_days=3)
    assert db.bar_queries == 1 and price_bars._STATE["current"] is False


def test_an_added_row_with_an_old_stamp_still_makes_it_stale(bars_dir, monkeypatch):
    monkeypatch.setenv("PRICE_BARS", "on")
    db = FakeDb(ROWS, probe=(len(ROWS) + 1, UPD2))
    server._perf_bars(db, CUT, lookback_days=3)
    assert db.bar_queries == 1


def test_a_request_older_than_the_cutoff_is_answered_by_the_file_when_the_counts_match(bars_dir, monkeypatch):
    """MEASURED 2026-10-22: the database holds 0 rows below the cutoff, and Best Settings asks back to 2021 --
    a cutoff-only probe sent every such request to the database, so the shadow never compared anything."""
    monkeypatch.setenv("PRICE_BARS", "on")
    db = FakeDb(ROWS)
    got = server._perf_bars(db, {"BP.L": D(2022, 10, 1)}, lookback_days=30)
    monkeypatch.setenv("PRICE_BARS", "off")
    server._PB_CACHE.clear(); server._PB_ORDER.clear()
    assert db.bar_queries == 0 and got == server._perf_bars(FakeDb(ROWS), {"BP.L": D(2022, 10, 1)}, lookback_days=30)


def test_the_probe_counts_the_whole_table(bars_dir, monkeypatch):
    """A row older than the file (not yet pruned) is outside the file; the whole-table count must see it."""
    monkeypatch.setenv("PRICE_BARS", "on")
    seen = []
    db = FakeDb(ROWS)
    real = db.run
    db.run = lambda sql, **kw: seen.append(sql) or real(sql, **kw)
    server._perf_bars(db, CUT, lookback_days=3)
    probe = [q for q in seen if "max(updated_at)" in q]
    assert probe == ["select count(*), max(updated_at) from price_history"]


def test_shadow_answers_from_the_database_and_logs_identical(bars_dir, monkeypatch, caplog):
    monkeypatch.setenv("PRICE_BARS", "shadow")
    db = FakeDb(ROWS)
    with caplog.at_level("INFO", logger="price_bars"):
        server._perf_bars(db, CUT, lookback_days=3)
    assert db.bar_queries == 1 and "PRICE-BARS SHADOW IDENTICAL: 3 tickers, 3 rows" in caplog.text


def test_shadow_names_a_difference(bars_dir, monkeypatch, caplog):
    monkeypatch.setenv("PRICE_BARS", "shadow")
    changed = list(ROWS)
    changed[1] = changed[1][:5] + (999.0,) + changed[1][6:]     # the database's close differs from the file's
    db = FakeDb(changed, probe=(len(ROWS), UPD2))
    with caplog.at_level("INFO", logger="price_bars"):
        server._perf_bars(db, CUT, lookback_days=3)
    assert "PRICE-BARS SHADOW DIFFERENT" in caplog.text and "BP.L" in caplog.text


def test_off_or_no_directory_never_touches_the_file(tmp_path, monkeypatch):
    monkeypatch.delenv("PRICE_BARS_DIR", raising=False)
    monkeypatch.setenv("PRICE_BARS", "on")
    assert price_bars.mode() == "off"
    monkeypatch.setenv("PRICE_BARS_DIR", str(tmp_path))
    monkeypatch.setenv("PRICE_BARS", "off")
    assert price_bars.mode() == "off"


def test_a_damaged_file_falls_back_to_the_database(bars_dir, monkeypatch):
    monkeypatch.setenv("PRICE_BARS", "on")
    (bars_dir / "price_bars.meta.json").write_text('{"rows": 99, "retention_cutoff": "2022-09-21"}')
    db = FakeDb(ROWS)
    server._perf_bars(db, CUT, lookback_days=3)
    assert db.bar_queries == 1


def test_the_evening_publish_and_the_morning_best_settings_fetch_the_file_in_shadow():
    """MEASURED 2026-10-21 19:32-19:36: the publish run's winners + Best Settings steps read ~1.57M bar rows."""
    import yaml
    pub = yaml.safe_load(open(".github/workflows/trading-scanner-snapshot.yml", encoding="utf-8"))
    steps = pub["jobs"]["publish"]["steps"]
    names = [s.get("name") or "" for s in steps]
    fetch = names.index("Fetch the price bars file (shadow)")
    assert fetch < names.index("Build, publish and read-verify immutable Scanner snapshot"), "refresh_daily runs in the build"
    assert fetch < names.index("Warm the precomputed winners payloads") < names.index("Precompute the public Best Settings cards")
    assert "if" not in steps[fetch]
    assert steps[fetch]["run"] == "bash .github/scripts/fetch_price_bars.sh shadow"
    assert steps[fetch]["continue-on-error"] is True
    bs = yaml.safe_load(open(".github/workflows/trading-best-settings-audit.yml", encoding="utf-8"))
    steps = bs["jobs"]["audit"]["steps"]
    runs = [str(s.get("run", "")) for s in steps]
    assert runs.index("bash .github/scripts/fetch_price_bars.sh shadow") < runs.index("python run_best_settings_audit.py")


def test_the_fetch_script_refuses_any_mode_but_shadow_or_on(tmp_path):
    import os, shutil, subprocess
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash")
    r = subprocess.run([bash, ".github/scripts/fetch_price_bars.sh", "off"], capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], "RUNNER_TEMP": str(tmp_path)})
    assert r.returncode == 2 and "shadow or on" in r.stderr


def test_shadow_prints_one_summary_line(bars_dir, monkeypatch, capsys):
    monkeypatch.setenv("PRICE_BARS", "shadow")
    price_bars._SHADOW.update(calls=0, rows=0, different=0)
    monkeypatch.setattr(price_bars.atexit, "register", lambda f: None)
    server._perf_bars(FakeDb(ROWS), CUT, lookback_days=3)
    price_bars._shadow_summary()
    assert "PRICE-BARS SHADOW SUMMARY: 1 calls, 3 rows from the database, 0 different -- IDENTICAL" in capsys.readouterr().err


def test_squeeze_history_asks_the_one_reader_with_each_tickers_own_start(monkeypatch):
    import squeeze_history
    asked = []
    monkeypatch.setattr(price_bars, "serve", lambda db, items, back, from_db: asked.append((items, back)) or {})
    squeeze_history._price_bars_by_cutoff(object(), {"BP.L": "2026-10-15", "NVDA": dt.date(2026, 10, 1), "": "x"})
    assert asked == [([("BP.L", D(2026, 10, 15)), ("NVDA", D(2026, 10, 1))], dt.timedelta(0))]


def test_squeeze_history_database_answer_is_the_files_answer(bars_dir, monkeypatch):
    import squeeze_history
    monkeypatch.setenv("PRICE_BARS", "off")
    want = squeeze_history._price_bars_by_cutoff(FakeDb(ROWS), {"BP.L": "2026-10-16", "NVDA": "2026-10-16"})
    monkeypatch.setenv("PRICE_BARS", "on")
    db = FakeDb(ROWS)
    got = squeeze_history._price_bars_by_cutoff(db, {"BP.L": "2026-10-16", "NVDA": "2026-10-16"})
    assert db.bar_queries == 0 and dict(got) == dict(want) and got


def test_the_morning_report_fetches_todays_file_without_needing_the_bars_job():
    """refresh_daily runs inside the HVF report. The chain builds the file in a PARALLEL job; making the report
    need it would skip every later job if the bars file failed -- so the fetch waits for today's file instead."""
    import yaml
    rep = yaml.safe_load(open(".github/workflows/trading-hvf-report.yml", encoding="utf-8"))
    steps = rep["jobs"]["run"]["steps"]
    names = [s.get("name") or "" for s in steps]
    fetch = names.index("Fetch the price bars file (shadow)")
    assert fetch < names.index("Run HVF daily report")
    assert steps[fetch]["run"] == "bash .github/scripts/fetch_price_bars.sh shadow 600" and steps[fetch]["continue-on-error"]
    chain = yaml.safe_load(open(".github/workflows/trading-morning-chain.yml", encoding="utf-8"))
    assert chain["jobs"]["daily_report"]["needs"] == "price_refresh"
