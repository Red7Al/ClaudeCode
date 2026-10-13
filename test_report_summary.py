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
    assert built == read and len(built) == 10, (built, read)


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
    out = pub.build({"generated_utc": "G", "records": []})
    assert out["sections"]["report_extras"] == {} and "open_setups" in out["sections"]


def test_only_a_recent_close_counts_as_now():
    import inspect
    import publish_report_summary as pub
    assert "bar_date >= current_date - 7" in inspect.getsource(pub.report_extras)


def test_the_order_bridge_gets_the_summary_the_summary_job_saves():
    """2026-10-13, owner: "385 calls is too many and this must be reduced with use of summary flat files".
    The Order Bridge (every 2 h on weekdays) ran _live_vwap_atr on a runner with no summary file, so every run
    read price_history. The summary job now saves the file as an artifact; the bridge fetches it BEFORE the
    bridge step, by the same name, with the automatic token (no IONOS key handed to it)."""
    import yaml
    summ = yaml.safe_load(open(".github/workflows/trading-report-summary.yml", encoding="utf-8"))
    up = [s for s in summ["jobs"]["summary"]["steps"] if "upload-artifact" in str(s.get("uses", ""))]
    assert up and up[0]["with"]["name"] == "report-summary" and up[0]["with"]["path"] == "hvf_web/report_summary.json"
    bridge = yaml.safe_load(open(".github/workflows/trading-order-bridge.yml", encoding="utf-8"))
    steps = list(bridge["jobs"].values())[0]["steps"]
    names = [s.get("name") or "" for s in steps]
    fetch = names.index("Fetch the report summary")
    run_at = next(i for i, s in enumerate(steps) if "order_bridge" in str(s.get("run", "")))
    assert names.index("Fetch the latest Scanner snapshot") < fetch < run_at
    f = steps[fetch]
    assert "fetch_trusted_artifact.sh report-summary report_summary.json hvf_web" in f["run"]
    # Only Scanner Snapshot Publish's summary: the bridge's snapshot is that run's (the evening scan); the
    # Morning Chain's newer summary is for the morning snapshot and would never match (2026-10-13).
    producers = [w.rstrip(")") for w in f["run"].split() if w.startswith(".github/workflows/")]
    assert producers == [".github/workflows/trading-scanner-snapshot.yml"], producers
    assert f.get("continue-on-error") is True
    assert "IONOS" not in str(f), "the bridge must not be given the IONOS key"


def test_the_order_bridge_installs_only_trusted_artifacts_and_only_their_json():
    """Security review 2026-10-13: the repository is public and trading-secret-scan.yml runs on pull_request, so
    a fork's PR can upload an artifact under any name. The bridge took the newest by name and unzipped the
    WHOLE archive into hvf_web/ -- a planted order_bridge.py would have run with the IG secrets. Every fetch
    now goes through one script that checks provenance and extracts only the named file."""
    import yaml
    wf = open(".github/workflows/trading-order-bridge.yml", encoding="utf-8").read()
    assert "unzip" not in wf and "actions/artifacts" not in wf, "no direct artifact download left in the bridge"
    steps = list(yaml.safe_load(wf)["jobs"].values())[0]["steps"]
    snap = next(s for s in steps if s.get("name") == "Fetch the latest Scanner snapshot")["run"]
    assert ("fetch_trusted_artifact.sh scanner-snapshot snapshot.json hvf_web" in snap
            and ".github/workflows/trading-scanner-snapshot.yml" in snap)
    sh = open(".github/scripts/fetch_trusted_artifact.sh", encoding="utf-8").read()
    for check in ('.workflow_run.head_branch == "main"',
                  ".workflow_run.head_repository_id == .workflow_run.repository_id",
                  '"$event" != "workflow_dispatch"', '"$allowed" != *" $path "*',
                  'unzip -q -o "$scratch/a.zip" "$file" -d "$scratch/x"'):
        assert check in sh, check
