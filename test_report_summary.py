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
    assert built == read and len(built) == 9, (built, read)


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
