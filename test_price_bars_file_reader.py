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


def test_below_the_retention_cutoff_the_database_answers(bars_dir, monkeypatch):
    monkeypatch.setenv("PRICE_BARS", "on")
    db = FakeDb(ROWS)
    server._perf_bars(db, {"BP.L": D(2022, 10, 1)}, lookback_days=30)
    assert db.bar_queries == 1


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
    assert fetch < names.index("Warm the precomputed winners payloads") < names.index("Precompute the public Best Settings cards")
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
