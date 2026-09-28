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
    # Batched now (2026-09-28): the same verdict, carried by positional placeholders rather than one
    # statement per row. The VERDICT is what this test is about, so it is asserted field by field.
    assert updates == [{"i0": 7, "td0": dt.date(2026, 8, 10), "o0": "TARGET",
                        "od0": dt.date(2026, 8, 11), "r0": 10.0, "v0": None}]


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


# ======================================================================================================
# refresh_daily was 51 MINUTES of silence and 31% of the Supabase free-tier egress allowance.
#
# MEASURED on the live database 2026-09-28, after the morning chain was killed at its 120-minute cap and
# took Scanner report email, Winners precompute, Best settings audit and HVF orders down with it:
#
#   * 4,287 active lifecycle rows, each written by its OWN single-row UPDATE -- one network round trip
#     apiece, ~714 ms each, ~51 minutes for the phase.
#   * a FLAT 18*31 = 558-day bar window for all 1,342 active tickers: 512,804 rows, 52.2 MB per run at
#     the measured 101.72 B/row wire width, 1.56 GB per 30 days on a daily job.
#   * not one log line for the whole phase, so working and stuck looked identical. It was misdiagnosed
#     as a hang during the very investigation that found this.
#
# These tests pin the shape, not the timing: one statement per batch, per-row cutoffs, and progress logs.
# ======================================================================================================


def _active(*rows):
    """A FakeDb whose active-row query returns `rows`, recording every statement it is asked to run."""
    seen = {"updates": [], "bar_queries": []}

    class FakeDb:
        def run(self, sql, **params):
            normal = " ".join(sql.split()).lower()
            if normal.startswith("create ") or normal.startswith("alter table"):
                return []
            if normal.startswith("insert into squeeze_history"):
                return [(1,)]
            if "from squeeze_history where outcome" in normal:
                return list(rows)
            if "from price_history" in normal:
                seen["bar_queries"].append((normal, dict(params)))
                out = []
                for ticker in {r[1] for r in rows}:
                    out += [(ticker, dt.date(2026, 8, 10), 105.0, 99.0, 102.0, 1000.0),
                            (ticker, dt.date(2026, 8, 11), 111.0, 101.0, 110.0, 1200.0)]
                return out
            if normal.startswith("update squeeze_history"):
                seen["updates"].append((normal, dict(params)))
                return []
            raise AssertionError(sql)

        def close(self):
            return None

    return FakeDb(), seen


def _row(row_id, ticker="ABC.L", ready=dt.date(2026, 8, 6), triggered=dt.date(2026, 8, 10)):
    return (row_id, ticker, "BULLISH", 100.0, 90.0, 110.0,
            dt.date(2026, 8, 5), dt.date(2026, 8, 6), ready, triggered, "OPEN")


def test_every_lifecycle_row_is_written_by_one_batched_statement(monkeypatch):
    """4,287 rows must not mean 4,287 round trips. Three rows, ONE update statement."""
    db, seen = _active(_row(1), _row(2, "DEF.L"), _row(3, "GHI.L"))
    monkeypatch.setattr("db_pool.get_db", lambda: db)

    result = squeeze_history.refresh_daily(_snapshot())

    assert result["active_refreshed"] == 3
    assert len(seen["updates"]) == 1, f"expected 1 batched UPDATE, got {len(seen['updates'])}"
    sql, params = seen["updates"][0]
    assert "from (values" in sql and "where sh.id = v.id" in sql
    assert [params["i0"], params["i1"], params["i2"]] == [1, 2, 3]


def test_a_batch_larger_than_the_limit_is_split_rather_than_sent_whole(monkeypatch):
    """The batch is bounded so the statement cannot outgrow a parameter limit."""
    monkeypatch.setattr(squeeze_history, "_UPDATE_BATCH", 2)
    db, seen = _active(_row(1), _row(2, "DEF.L"), _row(3, "GHI.L"))
    monkeypatch.setattr("db_pool.get_db", lambda: db)

    squeeze_history.refresh_daily(_snapshot())

    assert len(seen["updates"]) == 2          # 2 + 1, not 3 single-row statements


def test_rvol_recomputed_as_null_still_does_not_erase_a_stored_value(monkeypatch):
    """The coalesce rule predates the batching and must survive it."""
    db, seen = _active(_row(1))
    monkeypatch.setattr("db_pool.get_db", lambda: db)

    squeeze_history.refresh_daily(_snapshot())

    sql, _params = seen["updates"][0]
    assert "rvol=coalesce(v.rvol, sh.rvol)" in sql


def test_bars_are_fetched_from_each_rows_own_cutoff_not_a_flat_window(monkeypatch):
    """A row anchored in August must not drag 558 days of bars with it."""
    db, seen = _active(_row(1))
    monkeypatch.setattr("db_pool.get_db", lambda: db)

    squeeze_history.refresh_daily(_snapshot())

    assert len(seen["bar_queries"]) == 1
    sql, params = seen["bar_queries"][0]
    assert "join (values" in sql, "the bar fetch must carry a per-ticker cutoff"
    flat_start = (dt.date.today() - dt.timedelta(days=18 * 31)).isoformat()
    assert params["d0"] > flat_start, (
        f"cutoff {params['d0']} should be later than the flat window {flat_start}")


def test_the_cutoff_keeps_enough_history_to_average_rvol_against():
    """_rvol_at needs RVOL_BARS trading bars BEFORE the trigger; too tight silently returns None."""
    cutoffs = squeeze_history._bar_cutoffs([_row(1)], "2020-01-01")

    anchor = dt.date(2026, 8, 6)              # min(ready, triggered)
    assert cutoffs["ABC.L"] == (anchor - dt.timedelta(days=squeeze_history._BAR_BUFFER_DAYS)).isoformat()
    assert squeeze_history._BAR_BUFFER_DAYS >= 28, "20 trading bars is ~28 calendar days"


def test_a_row_with_no_pivot_and_no_trigger_keeps_the_whole_window():
    """No anchor means no way to narrow it safely, so the old flat window stands."""
    bare = (9, "XYZ.L", "BULLISH", 100.0, 90.0, 110.0, None, None, None, None, "OPEN")

    assert squeeze_history._bar_cutoffs([bare], "2025-03-19") == {"XYZ.L": "2025-03-19"}


def test_a_cutoff_is_never_earlier_than_the_flat_window():
    """The new fetch must always be a SUBSET of what the old code fetched."""
    ancient = _row(1, ready=dt.date(2021, 1, 4), triggered=dt.date(2021, 1, 5))

    assert squeeze_history._bar_cutoffs([ancient], "2025-03-19") == {"ABC.L": "2025-03-19"}


def test_one_ticker_with_several_rows_takes_the_earliest_cutoff_of_them_all():
    """Shared bars must satisfy the hungriest row, or a later row silently loses its history."""
    rows = [_row(1, ready=dt.date(2026, 8, 6), triggered=dt.date(2026, 8, 10)),
            _row(2, ready=dt.date(2026, 5, 1), triggered=dt.date(2026, 5, 4))]

    cutoffs = squeeze_history._bar_cutoffs(rows, "2020-01-01")

    # The anchor is min(max(pivots), triggered_date), and _row pins h3/l3 at 2026-08-05/06, so the
    # second row anchors on its TRIGGER (2026-05-04) rather than on the ready date passed in.
    earliest = dt.date(2026, 5, 4) - dt.timedelta(days=squeeze_history._BAR_BUFFER_DAYS)
    assert cutoffs["ABC.L"] == earliest.isoformat()


def test_the_phase_reports_progress_instead_of_going_silent(monkeypatch, caplog):
    """51 minutes of silence is why this was read as a hang. It must say what it is doing."""
    db, _seen = _active(_row(1))
    monkeypatch.setattr("db_pool.get_db", lambda: db)

    with caplog.at_level("INFO", logger=squeeze_history.log.name):
        squeeze_history.refresh_daily(_snapshot())

    messages = " | ".join(r.getMessage() for r in caplog.records)
    assert "active lifecycle rows" in messages
    assert "bars fetched" in messages
    assert "lifecycle rows written" in messages
