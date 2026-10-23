"""2026-10-22: 268 rows written at 14:32 (a chart's Yahoo write-through) made the 03:52 price bars file STALE, so every
evening step read the database and the evening file-vs-database comparison never ran. The evening publish now brings
the file up to date before it builds -- and a failed file build never holds the snapshot back."""
import yaml


def _jobs():
    with open(".github/workflows/trading-scanner-snapshot.yml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)["jobs"]


def test_the_evening_publish_builds_the_bars_file_first():
    jobs = _jobs()
    builders = [n for n, j in jobs.items() if j.get("uses") == "./.github/workflows/trading-bars-file.yml"]
    assert len(builders) == 1 and jobs[builders[0]].get("secrets") == "inherit"
    assert jobs["publish"].get("needs") == builders[0]


def test_a_failed_bars_file_never_holds_the_snapshot_back():
    cond = str(_jobs()["publish"].get("if", ""))
    assert "!cancelled()" in cond or "always()" in cond
