"""The price bars file must equal price_history: seeded once, then only what changed, and a verify that FAILS
on any difference. Fixture rows are price_history rows read 2026-10-21 (7203.T, TSCO.L 10-14..10-16); the table
also holds 36 rows with a null open or volume, so one is included.
"""
import datetime as dt

import pytest

import bars_file
import price_store

UTC = dt.timezone.utc
S1 = dt.datetime(2026, 10, 21, 3, 51, 50, 327972, tzinfo=UTC)
S2 = dt.datetime(2026, 10, 21, 3, 33, 8, 61851, tzinfo=UTC)
ROWS = [
    ("7203.T", dt.date(2026, 10, 14), 2988.0, 3022.0, 2972.5, 3011.0, 19149000, "YF", False, S1),
    ("7203.T", dt.date(2026, 10, 15), 2987.0, 3046.0, 2975.0, 3029.0, 32262800, "YF", False, S1),
    ("7203.T", dt.date(2026, 10, 16), 3051.0, 3096.0, 3047.0, 3094.0, 25007500, "YF", False, S1),
    ("TSCO.L", dt.date(2026, 10, 14), 518.599975585938, 527.799987792969, 516.200012207031, 526.400024414062,
     16350926, "YF", True, S2),
    ("TSCO.L", dt.date(2026, 10, 15), 526.400024414062, 535.599975585938, 525.799987792969, 534.599975585938,
     23141120, "YF", True, S2),
    ("TSCO.L", dt.date(2026, 10, 16), 530.0, 536.599975585938, 526.400024414062, 535.0, 13354161, "YF", True, S2),
    ("TSCO.L", dt.date(2026, 10, 17), None, 540.0, 530.0, 538.0, None, "YF", False, S2),
]


class FakeDB:
    """price_history in memory, answering the four statements bars_file issues."""

    def __init__(self, rows, now, cutoff=dt.date(2022, 9, 21)):
        self.rows, self.now, self.cutoff, self.reads = list(rows), now, cutoff, []

    def run(self, sql, **p):
        s = " ".join(sql.split()).lower()
        if s == "select now()":
            return [[self.now]]
        if "years')::interval" in s:
            return [[self.cutoff]]
        live = [r for r in self.rows if r[1] >= dt.date.fromisoformat(str(p["c"]))]
        if "group by ticker" in s:
            out = {}
            for r in live:
                a = out.setdefault(r[0], [r[0], 0, 0.0, 0.0, 0.0, 0.0, 0, None])
                a[1] += 1
                for i in range(4):
                    a[2 + i] += r[2 + i] or 0.0
                a[6] += r[6] or 0
                a[7] = r[9] if a[7] is None else max(a[7], r[9])
            return list(out.values())
        if "updated_at >" in s:
            got = [r for r in live if r[9] > p["s"]]
        else:
            got = live
        self.reads.append(len(got))
        return got

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _no_schema(monkeypatch):
    monkeypatch.setattr(price_store, "ensure_schema", lambda db=None: None)


def test_seed_reads_every_row_and_verifies(tmp_path):
    db = FakeDB(ROWS, now=dt.datetime(2026, 10, 21, 4, 0, tzinfo=UTC))
    meta = bars_file.build(str(tmp_path), db=db)
    assert meta["rows"] == 7 and meta["read"]["mode"] == "seed" and db.reads == [7]
    assert bars_file.verify(str(tmp_path), db=db) == []


def test_values_read_back_bit_identical(tmp_path):
    db = FakeDB(ROWS, now=dt.datetime(2026, 10, 21, 4, 0, tzinfo=UTC))
    bars_file.build(str(tmp_path), db=db)
    rows, _ = bars_file.read(str(tmp_path))
    got = rows[("TSCO.L", "2026-10-15")]
    assert got[2:7] == (526.400024414062, 535.599975585938, 525.799987792969, 534.599975585938, 23141120)
    assert rows[("TSCO.L", "2026-10-17")][2] is None and rows[("TSCO.L", "2026-10-17")][6] is None


def test_update_reads_only_rows_stamped_since_and_applies_a_correction(tmp_path):
    seed, upd = tmp_path / "seed", tmp_path / "upd"
    db = FakeDB(ROWS, now=dt.datetime(2026, 10, 21, 12, 0, tzinfo=UTC))
    bars_file.build(str(seed), db=db)
    # The 23:00 audit corrects one high (stamped by the trigger) and the next refresh adds a bar.
    later = dt.datetime(2026, 10, 21, 23, 1, tzinfo=UTC)
    db.rows[4] = db.rows[4][:3] + (530.1,) + db.rows[4][4:9] + (later,)
    db.rows.append(("7203.T", dt.date(2026, 10, 17), 3090.0, 3110.0, 3070.0, 3100.0, 20000000, "YF", False, later))
    db.now = dt.datetime(2026, 10, 22, 3, 45, tzinfo=UTC)
    meta = bars_file.build(str(upd), prev_dir=str(seed), db=db)
    assert meta["read"]["mode"] == "update" and meta["read"]["rows_read"] == 2 and meta["rows"] == 8
    rows, _ = bars_file.read(str(upd))
    assert rows[("TSCO.L", "2026-10-15")][3] == 530.1
    assert bars_file.verify(str(upd), db=db) == []


def test_a_write_that_committed_late_inside_the_overlap_is_not_missed(tmp_path):
    seed, upd = tmp_path / "seed", tmp_path / "upd"
    db = FakeDB(ROWS, now=dt.datetime(2026, 10, 21, 12, 0, tzinfo=UTC))
    bars_file.build(str(seed), db=db)
    # Stamped 11:00 (its transaction began before the 12:00 build) but committed after the build read.
    db.rows.append(("7203.T", dt.date(2026, 10, 17), 3090.0, 3110.0, 3070.0, 3100.0, 1, "YF", False,
                    dt.datetime(2026, 10, 21, 11, 0, tzinfo=UTC)))
    db.now = dt.datetime(2026, 10, 22, 3, 45, tzinfo=UTC)
    bars_file.build(str(upd), prev_dir=str(seed), db=db)
    assert ("7203.T", "2026-10-17") in bars_file.read(str(upd))[0]


def test_rows_below_the_retention_cutoff_are_dropped(tmp_path):
    seed, upd = tmp_path / "seed", tmp_path / "upd"
    db = FakeDB(ROWS, now=dt.datetime(2026, 10, 21, 12, 0, tzinfo=UTC))
    bars_file.build(str(seed), db=db)
    db.cutoff = dt.date(2026, 10, 15)                       # the nightly prune moved the boundary
    db.rows = [r for r in db.rows if r[1] >= db.cutoff]
    meta = bars_file.build(str(upd), prev_dir=str(seed), db=db)
    assert meta["read"]["dropped_below_cutoff"] == 2 and meta["rows"] == 5
    assert bars_file.verify(str(upd), db=db) == []


@pytest.mark.parametrize("change", ["value", "missing_row", "extra_row", "volume", "stamp"])
def test_verify_fails_on_any_difference(tmp_path, change):
    db = FakeDB(ROWS, now=dt.datetime(2026, 10, 21, 4, 0, tzinfo=UTC))
    bars_file.build(str(tmp_path), db=db)
    r = db.rows[3]
    if change == "value":
        db.rows[3] = r[:4] + (r[4] - 0.01,) + r[5:]
    elif change == "missing_row":
        db.rows.append(("BP.L", dt.date(2026, 10, 16), 400.0, 401.0, 399.0, 400.5, 1, "YF", False, S2))
    elif change == "extra_row":
        del db.rows[0]
    elif change == "volume":
        db.rows[3] = r[:6] + (r[6] + 1,) + r[7:]
    else:
        db.rows[3] = r[:9] + (r[9] + dt.timedelta(seconds=1),)
    assert bars_file.verify(str(tmp_path), db=db) != []


def test_a_truncated_file_is_refused(tmp_path):
    db = FakeDB(ROWS, now=dt.datetime(2026, 10, 21, 4, 0, tzinfo=UTC))
    bars_file.build(str(tmp_path), db=db)
    import json
    meta_path = tmp_path / bars_file.META_NAME
    meta = json.loads(meta_path.read_text())
    meta["rows"] += 1
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        bars_file.read(str(tmp_path))
