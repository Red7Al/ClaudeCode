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
