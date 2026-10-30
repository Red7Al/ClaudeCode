"""The Scanner Report's summary file on IONOS (owner 2026-10-09, requested many times).

MEASURED before: one cold web process read 75,868 rows (61 + 4 queries) for the Scanner page and the cache
warmer. With the file, the same process read 4,134 rows, and /api/records was field-for-field identical on
the live snapshot (1,773 rows, 0 differences)."""
import datetime as dt
import json
import os

import pytest

from hvf_web import server


@pytest.fixture
def summary(tmp_path, monkeypatch):
    path = tmp_path / "report_summary.json"
    monkeypatch.setattr(server, "SUMMARY_FILE", str(path))
    monkeypatch.setattr(server, "_SUMMARY_DISABLED", False)
    server._SUMMARY_CACHE.update(mtime=None, data=None)

    def write(gen, built_at=None, **sections):
        built_at = built_at or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        path.write_text(json.dumps({"generated_utc": gen, "built_at": built_at, "sections": sections}))
        os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 1))
    yield write
    server._SUMMARY_CACHE.update(mtime=None, data=None)


SNAP = {"generated_utc": "2026-10-09T05:59:13", "records": [{"ticker": "ZS"}]}


def test_a_matching_file_answers_without_the_database(summary, monkeypatch):
    summary(SNAP["generated_utc"], wk52={"ZS": [150.0, 260.0]}, open_setups={"ZS": []})
    monkeypatch.setattr("db_pool.get_db", lambda: pytest.fail("the database was read"))
    assert server._snapshot_52wk(SNAP) == {"ZS": [150.0, 260.0]}
    assert server._open_trigger_setups(SNAP) == {"ZS": []}


def test_a_file_for_another_snapshot_is_never_used(summary):
    summary("2026-10-08T19:20:15", wk52={"ZS": [1, 2]})
    assert server._summary_section("wk52", SNAP) is None


def test_no_file_means_the_database_path(summary):
    assert server._summary_section("wk52", SNAP) is None


def test_market_cap_is_used_from_any_recent_file_but_not_an_old_one(summary):
    summary("x", mcap={"ZS": 2.4e10})
    assert server._summary_section("mcap") == {"ZS": 2.4e10}
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=37)).isoformat(timespec="seconds")
    summary("x", built_at=old, mcap={"ZS": 2.4e10})
    assert server._summary_section("mcap") is None


def test_the_builder_never_reads_an_older_file(summary, monkeypatch):
    summary(SNAP["generated_utc"], wk52={"ZS": [1, 2]})
    monkeypatch.setattr(server, "_SUMMARY_DISABLED", True)
    assert server._summary_section("wk52", SNAP) is None


def test_an_unreadable_file_falls_back(summary, tmp_path):
    (tmp_path / "report_summary.json").write_text("{not json")
    server._SUMMARY_CACHE.update(mtime=None, data=None)
    assert server._summary_section("wk52", SNAP) is None


def test_every_section_the_builder_writes_is_read_by_a_helper():
    """A section written but never read is the repo's signature defect; one read but never written is a
    silent database fallback for ever."""
    import inspect
    import publish_report_summary as pub
    src = inspect.getsource(server)
    built = set(__import__("re").findall(r'"(\w+)": server\._', inspect.getsource(pub.build)))
    read = set(__import__("re").findall(r'_summary_section\("(\w+)"', src))
    built |= set(__import__("re").findall(r'sections\["(\w+)"\] = ', inspect.getsource(pub.build)))
    assert built == read and len(built) == 11, (built, read)


def test_a_deploy_never_ships_a_local_summary_over_the_hosts():
    """The host's file is installed by the workflow; a stale local copy shipped by deploy_ionos.sh (unzip -o)
    would replace it."""
    from pathlib import Path
    import build_ionos_package
    assert build_ionos_package.include_path(Path("hvf_web/report_summary.json")) is False
    assert build_ionos_package.include_path(Path("publish_report_summary.py")) is True


def test_positions_look_up_only_the_held_instruments(monkeypatch):
    """/api/positions read all of epic_lookup (~2,000 rows, MEASURED 2,000 of the 2,351 rows one Scanner
    page load read) to name a handful of positions. It now asks for the held epics only."""
    import contextlib
    import ig_shim
    monkeypatch.setattr(server._wu, "name_for_token", lambda t: "Alex")
    monkeypatch.setattr(ig_shim, "session_for", lambda login=None: object())
    monkeypatch.setattr(ig_shim, "acting_session", lambda login=None: contextlib.nullcontext())
    monkeypatch.setattr(ig_shim, "get_open_positions", lambda: [
        {"market": {"epic": "KA.D.ZS.DAILY.IP", "instrumentName": "Zscaler"}},
        {"market": {"epic": "KA.D.ZS.DAILY.IP", "instrumentName": "Zscaler"}},
        {"market": {"epic": "KA.D.UNKNOWN.IP", "instrumentName": "Unmapped Co"}}])
    asked = []

    class _Db:
        def run(self, sql, **k):
            asked.append((" ".join(sql.split()), k))
            return [("ZS", "KA.D.ZS.DAILY.IP")]

        def close(self):
            pass
    monkeypatch.setattr("db_pool.get_db", lambda: _Db())
    j = server.app.test_client().get("/api/positions", headers={"X-Auth": "t"}).get_json()
    assert j == {"positions": {"ZS": 2, "Unmapped Co": 1}}
    assert asked == [("select ticker, epic from epic_lookup where epic = any(:e)",
                      {"e": ["KA.D.UNKNOWN.IP", "KA.D.ZS.DAILY.IP"]})]


def test_no_positions_means_no_lookup(monkeypatch):
    import contextlib
    import ig_shim
    monkeypatch.setattr(server._wu, "name_for_token", lambda t: "Alex")
    monkeypatch.setattr(ig_shim, "session_for", lambda login=None: object())
    monkeypatch.setattr(ig_shim, "acting_session", lambda login=None: contextlib.nullcontext())
    monkeypatch.setattr(ig_shim, "get_open_positions", lambda: [])
    monkeypatch.setattr("db_pool.get_db", lambda: pytest.fail("the database was read with nothing held"))
    assert server.app.test_client().get("/api/positions", headers={"X-Auth": "t"}).get_json() == {"positions": {}}


def test_report_extras_come_from_stored_data_through_the_scans_rules(monkeypatch):
    """Only setups the scan has no price for; Now = latest stored close; P/E and Insider % through
    pe_of / insider_pct_of on the stored fundamentals. No live yfinance (it rate-limits, MEASURED)."""
    import publish_report_summary as pub
    import web_store

    class _Db:
        def run(self, sql, **k):
            assert "distinct on (ticker)" in sql and k["t"] == ["AAF.L"]
            return [("AAF.L", 309.6)]

        def close(self):
            pass
    monkeypatch.setattr("db_pool.get_db", lambda: _Db())
    monkeypatch.setattr(web_store, "load_json_store", lambda k: {"records": {"AAF.L": {"kpis": {
        "trailingPE": 20.728573, "forwardPE": 11.1, "heldPercentInsiders": 0.79668}}}})
    snap = {"records": [{"ticker": "ZS", "current_price": 216.8}, {"ticker": "AAF.L", "current_price": None}]}
    out = pub.report_extras(snap, {"ZS": [{}], "AAF.L": [{}], "NONE": []})
    assert out == {"AAF.L": {"current_price": 309.6, "pe": 20.7, "insider_pct": 79.668}}


def test_fundamentals_still_computes_the_insider_value(monkeypatch):
    """Regression (code review 2026-10-10): moving the insider rule into insider_pct_of left
    `insider_value = ins * mcap` reading a variable that no longer existed -- NameError for every equity
    with both values, which build_snapshot swallowed (losing insider/broker/sector)."""
    import quality_report as qr

    class _T:
        info = {"heldPercentInsiders": 0.05, "marketCap": 2_000_000_000, "currency": "USD"}
        recommendations = None

        def __getattr__(self, name):
            return None
    monkeypatch.setattr("yfinance.Ticker", lambda tk: _T())
    f = qr.fundamentals("AAPL")
    assert f["insider_pct"] == 5.0 and f["insider_value"] == 100_000_000


def test_a_failing_extras_step_does_not_cost_the_whole_summary(monkeypatch):
    import publish_report_summary as pub
    from hvf_web import server as srv
    for name in ("_open_trigger_setups", "_stored_metrics", "_live_instrument_metrics", "_snapshot_52wk",
                 "_snapshot_trigger_dates", "_snapshot_rvol", "_snapshot_volscore", "_live_vwap_atr"):
        monkeypatch.setattr(srv, name, lambda snap: {})
    monkeypatch.setattr(srv, "_mcap_map", lambda: {})
    monkeypatch.setattr(pub, "report_extras", lambda snap, s: 1 / 0)
    monkeypatch.setattr(pub, "squeeze_history_section", lambda: None)
    out = pub.build({"generated_utc": "G", "records": []})
    assert out["sections"]["report_extras"] == {} and "open_setups" in out["sections"]


def test_only_a_recent_close_counts_as_now():
    import inspect
    import publish_report_summary as pub
    assert "bar_date >= current_date - 7" in inspect.getsource(pub.report_extras)


def test_the_order_bridge_gets_the_evening_snapshot_and_summary_from_ionos():
    """2026-10-13, owner: "385 calls is too many and this must be reduced with use of summary flat files" -- the
    bridge reads the summary built with ITS snapshot, the evening one (the Morning Chain's would never match).
    2026-10-21, owner: the repository is PUBLIC and is for code, not reports -- both files come from IONOS
    over SSH, from the folder Scanner Snapshot Publish and its summary job write, never from an artifact."""
    import yaml
    wf = open(".github/workflows/trading-order-bridge.yml", encoding="utf-8").read()
    steps = list(yaml.safe_load(wf)["jobs"].values())[0]["steps"]
    code = str([(s.get("uses"), s.get("run")) for s in steps])
    assert "artifact" not in code and "gh run download" not in code
    names = [s.get("name") or "" for s in steps]
    snap, summ = names.index("Fetch the latest Scanner snapshot"), names.index("Fetch the report summary")
    run_at = next(i for i, s in enumerate(steps) if "order_bridge" in str(s.get("run", "")))
    assert snap < summ < run_at
    assert "fetch_ionos_evening.sh snapshot.json hvf_web" in steps[snap]["run"]
    assert "age > 36" in steps[snap]["run"], "the stale-snapshot refusal stays"
    assert "fetch_ionos_evening.sh report_summary.json hvf_web" in steps[summ]["run"]
    assert steps[summ].get("continue-on-error") is True and steps[snap].get("continue-on-error") is not True
    # The files the bridge reads are the ones the evening jobs write.
    pub = open(".github/workflows/trading-scanner-snapshot.yml", encoding="utf-8").read()
    rs = yaml.safe_load(open(".github/workflows/trading-report-summary.yml", encoding="utf-8"))
    assert "dir=/home/www/data/evening" in pub and "$dir/snapshot.json" in pub
    keep = next(s for s in rs["jobs"]["summary"]["steps"] if s.get("name") == "Keep the evening summary on IONOS")
    assert "dir=/home/www/data/evening" in keep["run"] and "$dir/report_summary.json" in keep["run"]
    sh = open(".github/scripts/fetch_ionos_evening.sh", encoding="utf-8").read()
    assert 'src="/home/www/data/evening/$file"' in sh


def test_the_thread_precompute_gets_the_evening_snapshot_from_ionos():
    import yaml
    wf = open(".github/workflows/trading-thread-precompute.yml", encoding="utf-8").read()
    steps = list(yaml.safe_load(wf)["jobs"].values())[0]["steps"]
    code = str([(s.get("uses"), s.get("run")) for s in steps])
    assert "gh run download" not in code and "scanner-snapshot" not in code
    fetch = next(s for s in steps if "fetch_ionos_evening.sh" in str(s.get("run", "")))
    assert "fetch_ionos_evening.sh snapshot.json hvf_web" in fetch["run"]


def test_no_workflow_saves_a_github_artifact():
    """Owner 2026-10-21: "the github repository is for code not reports". The repository is PUBLIC; an
    artifact's listing is public (measured) and any signed-in GitHub user can download it (GitHub docs)."""
    import glob
    hits = [p for p in glob.glob(".github/workflows/*.yml")
            if "actions/upload-artifact" in open(p, encoding="utf-8").read()]
    assert hits == [], hits


def test_fetch_ionos_evening_takes_only_a_plain_json_name(tmp_path):
    import shutil, subprocess
    bash = shutil.which("bash")
    if not bash:
        import pytest; pytest.skip("no bash")
    for bad in ("../x.json", "a/b.json", "x.py", "snapshot.json;rm"):
        r = subprocess.run([bash, ".github/scripts/fetch_ionos_evening.sh", bad, str(tmp_path)],
                           capture_output=True, text=True, env={"PATH": __import__("os").environ["PATH"]})
        assert r.returncode == 2 and "refusing" in r.stderr, (bad, r.returncode, r.stderr)


# A live squeeze_history row (read 2026-10-26) as the table returns it and as the summary file carries it.
HICL = ["HICL.L", "FTSE 250", "daily-240", "BEARISH", 60.0, 4.68, 0.88, "STOPPED", -3.27, dt.date(2024, 4, 16),
        dt.date(2024, 7, 5), 123.408156, 127.440673, 104.532955]


class _CounterDb:
    """squeeze_history's write counter is `counter`; reading the rows themselves is recorded."""
    def __init__(self, counter):
        self.counter, self.row_reads = counter, 0

    def run(self, sql, **k):
        if "pg_stat_user_tables" in sql:
            return [[self.counter]]
        self.row_reads += 1
        return [list(HICL)]


def _file_rows():
    return [[str(v) if isinstance(v, dt.date) else v for v in HICL]]


def test_squeeze_history_comes_from_the_file_while_the_table_is_unwritten(summary, monkeypatch):
    """MEASURED 2026-10-23..26: 55 reads, 1,792,968 rows -- each fresh web process read ~32,000 rows. Shadow
    2026-10-26: 32,633 rows through the file, 0 different."""
    summary("any", squeeze_history={"version": 667160, "maxrr": 100.0, "rows": _file_rows()})
    monkeypatch.setattr(server, "_SQA_RAW", {"version": None, "raw": None})
    db = _CounterDb(667160)
    assert server._squeeze_history_raw(db, 100.0) == [HICL]
    assert db.row_reads == 0, "the table was read although the file holds exactly its rows"


def test_a_write_since_the_build_reads_the_table(summary, monkeypatch):
    summary("any", squeeze_history={"version": 667160, "maxrr": 100.0, "rows": _file_rows()})
    monkeypatch.setattr(server, "_SQA_RAW", {"version": None, "raw": None})
    db = _CounterDb(667161)
    assert server._squeeze_history_raw(db, 100.0) == [HICL] and db.row_reads == 1


# ------------------------------------------------------------------------------------------------------
# The squeeze history file (2026-10-30). MEASURED 10-26 22:17Z -> 10-30 11:32Z: 34 reads / 1,111,532 rows of
# squeeze_history, the biggest query by rows -- the Morning Chain and the evening scan ran before the summary held
# the table's new write counter, so every job between the write and the summary read the table itself.
# ------------------------------------------------------------------------------------------------------
class _ClosingCounterDb(_CounterDb):
    def close(self):
        pass


def _no_summary(monkeypatch):
    monkeypatch.setattr(server, "_summary_section", lambda *a, **k: None)
    monkeypatch.setattr(server, "_SQA_RAW", {"version": None, "raw": None})


def _write_file(tmp_path, monkeypatch, counter=667160):
    """The file exactly as the writer job builds it: publish_report_summary.write_squeeze_history_file."""
    import publish_report_summary
    _no_summary(monkeypatch)
    db = _ClosingCounterDb(counter)
    monkeypatch.setattr("db_pool.get_db", lambda: db)
    path = str(tmp_path / "squeeze_history.json")
    assert publish_report_summary.write_squeeze_history_file(path) == 1
    assert db.row_reads == 1, "the writer reads the table once"
    return path


def test_squeeze_history_file_round_trips_the_tables_rows(tmp_path, monkeypatch):
    path = _write_file(tmp_path, monkeypatch)
    monkeypatch.setenv("SQUEEZE_HISTORY_FILE", path)
    _no_summary(monkeypatch)
    db = _CounterDb(667160)

    assert server._squeeze_history_raw(db, 100.0) == [HICL], "the file must give the table's rows, value for value"
    assert db.row_reads == 0, "a later job read the table although the file holds exactly its rows"


def test_squeeze_history_file_from_another_write_counter_is_not_used(tmp_path, monkeypatch):
    path = _write_file(tmp_path, monkeypatch, counter=667160)
    monkeypatch.setenv("SQUEEZE_HISTORY_FILE", path)
    _no_summary(monkeypatch)
    db = _CounterDb(667161)                    # written after the file was built

    assert server._squeeze_history_raw(db, 100.0) == [HICL] and db.row_reads == 1


def test_squeeze_history_file_for_another_rr_cap_is_not_used(tmp_path, monkeypatch):
    path = _write_file(tmp_path, monkeypatch)
    monkeypatch.setenv("SQUEEZE_HISTORY_FILE", path)
    _no_summary(monkeypatch)
    db = _CounterDb(667160)

    server._squeeze_history_raw(db, 10.0)
    assert db.row_reads == 1


def test_an_unreadable_squeeze_history_file_reads_the_table(tmp_path, monkeypatch):
    bad = tmp_path / "squeeze_history.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("SQUEEZE_HISTORY_FILE", str(bad))
    _no_summary(monkeypatch)
    db = _CounterDb(667160)

    assert server._squeeze_history_raw(db, 100.0) == [HICL] and db.row_reads == 1


def _steps(path):
    import yaml
    jobs = yaml.safe_load(open(path, encoding="utf-8"))["jobs"]
    return [s for j in jobs.values() for s in j.get("steps") or ()]


def _index(steps, needle):
    hits = [i for i, s in enumerate(steps) if needle in str(s.get("run") or "")]
    assert len(hits) == 1, (needle, hits)
    return hits[0]


def test_the_jobs_that_write_squeeze_history_put_the_file_before_their_readers():
    for wf, last_write, readers in (
            ("trading-hvf-report.yml", "run_data_quality_audit.py --current-metrics-only",
             ["run_best_settings_audit.py"]),
            ("trading-scanner-snapshot.yml", "publish_scanner_snapshot.py --build",
             ["run_winners_precompute.py", "run_best_settings_cards.py"])):
        steps = _steps(".github/workflows/" + wf)
        put = _index(steps, "squeeze_history_file.sh put")
        assert "--squeeze-history-file" in steps[put]["run"]
        assert _index(steps, last_write) < put, (wf, "the file must follow the table's last write")
        for r in readers:
            assert put < _index(steps, r), (wf, r)


def test_the_jobs_after_the_write_fetch_the_file_before_reading():
    for wf, reader in (("trading-winners-precompute.yml", "run_winners_precompute.py"),
                       ("trading-best-settings-audit.yml", "run_best_settings_audit.py"),
                       ("trading-report-summary.yml", "publish_report_summary.py")):
        steps = _steps(".github/workflows/" + wf)
        get = _index(steps, "squeeze_history_file.sh get")
        assert steps[get].get("continue-on-error") is True, (wf, "a failed fetch must leave the database path")
        assert get < _index(steps, reader), wf


def test_squeeze_history_put_refuses_a_counter_that_is_not_digits(tmp_path):
    """The counter is spliced into a command run on the IONOS host (review 2026-10-30)."""
    import shutil, subprocess
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash")
    (tmp_path / "squeeze_history.json").write_text('{"version": "1; touch pwned", "rows": []}', encoding="utf-8")
    env = dict(os.environ, RUNNER_TEMP=str(tmp_path), GITHUB_ENV=str(tmp_path / "env"), IONOS_SSH_KEY="x",
               IONOS_USER="u", IONOS_HOST="invalid.invalid")
    r = subprocess.run([bash, ".github/scripts/squeeze_history_file.sh", "put"], capture_output=True, text=True, env=env)
    assert r.returncode != 0 and "AssertionError" in r.stderr, r.stderr
    assert not (tmp_path / "env").exists(), "nothing may be exported after a refusal"


def test_the_website_reads_its_copy_of_the_squeeze_history_file(tmp_path, monkeypatch):
    """The web tier sets no SQUEEZE_HISTORY_FILE; it reads data/squeeze_history.json, installed by the put step, in
    the gap between a chain's write to the table and its summary rebuild (2026-10-30)."""
    path = _write_file(tmp_path, monkeypatch)
    monkeypatch.delenv("SQUEEZE_HISTORY_FILE", raising=False)
    monkeypatch.setattr(server, "_SQH_WEB_FILE", path)
    _no_summary(monkeypatch)
    db = _CounterDb(667160)

    assert server._squeeze_history_raw(db, 100.0) == [HICL] and db.row_reads == 0


def test_without_the_websites_copy_the_table_is_read(tmp_path, monkeypatch):
    monkeypatch.delenv("SQUEEZE_HISTORY_FILE", raising=False)
    monkeypatch.setattr(server, "_SQH_WEB_FILE", str(tmp_path / "absent.json"))
    _no_summary(monkeypatch)
    db = _CounterDb(667160)

    assert server._squeeze_history_raw(db, 100.0) == [HICL] and db.row_reads == 1


def test_put_installs_the_websites_copy_where_the_web_tier_looks():
    sh = open(".github/scripts/squeeze_history_file.sh", encoding="utf-8").read()
    assert 'web="$IONOS_DIR/data/squeeze_history.json"' in sh and "chmod 640" in sh and "chgrp www-data" in sh
    assert server._SQH_WEB_FILE == os.path.join(server._DATA_DIR, "squeeze_history.json")
