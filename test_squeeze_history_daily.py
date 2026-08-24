import datetime as dt

import squeeze_history


def _snapshot():
    return {
        "generated_utc": "2026-08-12T19:45:00+00:00",
        "records": [{
            "ticker": "ABC.L", "market": "FTSE 100", "has_signal": True,
            "status": "TRIGGERED", "direction": "BULL", "timeframe": "daily-90",
            "entry": 100.0, "stop": 90.0, "target": 110.0, "quality": 80, "rr": 1.0,
            "h3_date": "2026-08-05", "l3_date": "2026-08-06",
            "_card": {
                "ticker": "ABC.L", "hvf_type": "BULLISH", "hvf_signal": "TRIGGERED",
                "hvf_timeframe": "daily-90", "h1_level": 120.0, "h2_level": 115.0,
                "h3_level": 100.0, "l1_level": 80.0, "l2_level": 85.0, "l3_level": 90.0,
                "h1_date": "2026-07-01", "h2_date": "2026-07-20", "h3_date": "2026-08-05",
                "l1_date": "2026-07-05", "l2_date": "2026-07-25", "l3_date": "2026-08-06",
                "stop_level": 90.0, "target": 110.0, "risk_reward": 1.0,
            },
        }],
    }


def test_snapshot_rows_preserve_funnel_identity_and_current_lifecycle_date():
    rows = squeeze_history._snapshot_rows(_snapshot())

    assert len(rows) == 1
    assert rows[0]["ticker"] == "ABC.L"
    assert rows[0]["h3_date"] == "2026-08-05"
    assert rows[0]["l3_date"] == "2026-08-06"
    assert rows[0]["last_seen"] == "2026-08-12"
    assert rows[0]["first_signal"] == "TRIGGERED"
    assert rows[0]["ready_date"] == "2026-08-06"


def test_daily_refresh_advances_open_history_from_price_history(monkeypatch):
    updates = []

    class FakeDb:
        def run(self, sql, **params):
            normal = " ".join(sql.split()).lower()
            if normal.startswith("create ") or normal.startswith("alter table"):
                return []
            if normal.startswith("insert into squeeze_history"):
                return [(1,)]
            if "from squeeze_history where outcome" in normal:
                return [(7, "ABC.L", "BULLISH", 100.0, 90.0, 110.0,
                         dt.date(2026, 8, 5), dt.date(2026, 8, 6), dt.date(2026, 8, 6),
                         dt.date(2026, 8, 10), "OPEN")]
            if "from price_history" in normal:
                return [
                    ("ABC.L", dt.date(2026, 8, 10), 105.0, 99.0, 102.0, 1000.0),
                    ("ABC.L", dt.date(2026, 8, 11), 111.0, 101.0, 110.0, 1200.0),
                ]
            if normal.startswith("update squeeze_history"):
                updates.append(params)
                return []
            raise AssertionError(sql)

        def close(self):
            return None

    monkeypatch.setattr("db_pool.get_db", lambda: FakeDb())

    result = squeeze_history.refresh_daily(_snapshot())

    assert result == {"current_funnels": 1, "current_upserts": 1,
                      "active_refreshed": 1, "data_through": "2026-08-11"}
    assert updates == [{"td": dt.date(2026, 8, 10), "outcome": "TARGET",
                        "od": dt.date(2026, 8, 11), "ret": 10.0, "rvol": None, "id": 7}]


def test_daily_store_updates_mutable_fields_without_deleting_history():
    statements = []

    class FakeDb:
        def run(self, sql, **params):
            statements.append(sql)
            return [(1,)]

    changed = squeeze_history.store(FakeDb(), squeeze_history._snapshot_rows(_snapshot()), update_existing=True)

    assert changed == 1
    sql = " ".join(statements[0].split()).lower()
    assert "on conflict" in sql and "do update set" in sql
    assert "least(squeeze_history.first_seen,excluded.first_seen)" in sql
    assert "greatest(squeeze_history.last_seen,excluded.last_seen)" in sql
    assert "refreshed_at=now()" in sql


# ======================================================================================================
# The lifecycle history must survive a failed publication (user 2026-08-24: "data in squeeze history is
# still NOT being maintained - it has not added data added in ten days").
#
# refresh_daily() used to run only AFTER a successful Supabase publication. When Storage began returning
# 402 on 2026-08-16, every run took the fallback path -- which returns early in run_hvf_report and raises
# in publish_scanner_snapshot -- and skipped the history refresh entirely. The IONOS fallback kept
# publishing the snapshot, so the site stayed current and nothing looked wrong, while squeeze_history
# silently stopped advancing for eight days. Verified in the database: every timestamp column stopped at
# 2026-08-16 against a current date of 2026-08-24.
#
# The history depends on the COMPLETED SCAN, not on where the snapshot ends up.
# ======================================================================================================

import re
from pathlib import Path

ROOT = Path(__file__).parent


def _source(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_history_runs_before_publication_in_the_daily_report():
    src = _source("run_hvf_report.py")
    history = src.index("refresh_daily(snapshot)")
    publish = src.index("meta = publish_snapshot(snapshot")

    assert history < publish, (
        "refresh_daily runs after publish_snapshot; a Storage failure returns early and skips the history")


def test_history_runs_before_publication_in_the_snapshot_publisher():
    src = _source("publish_scanner_snapshot.py")
    history = src.index("refresh_daily(snapshot)")
    publish = src.index("store.publish_snapshot(snapshot")

    assert history < publish, (
        "refresh_daily runs after publish_snapshot; a raised Storage error skips the history")


def test_a_failed_history_refresh_does_not_cost_a_good_publication():
    """Independent in BOTH directions: history must not be able to abort publishing either."""
    for name, call in (("run_hvf_report.py", "refresh_daily(snapshot)"),
                       ("publish_scanner_snapshot.py", "refresh_daily(snapshot)")):
        src = _source(name)
        i = src.index(call)
        window = src[max(0, i - 260):i + 260]
        assert "try:" in window and "except" in window, (
            f"{name}: the history refresh is not guarded, so a failure there would abort the publication")


def test_the_storage_402_path_still_reaches_the_history():
    """THE REGRESSION. The 402 branch returns early -- the history must already have run by then."""
    src = _source("run_hvf_report.py")
    history = src.index("refresh_daily(snapshot)")
    fallback = src.index('marker = os.path.join(os.path.dirname(__file__), "hvf_web", ".ionos-fallback-required")')

    assert history < fallback, (
        "the IONOS fallback return happens before the history refresh, which is the bug that stopped "
        "squeeze_history for eight days while the site continued to look healthy")
