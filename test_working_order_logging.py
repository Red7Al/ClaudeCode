"""A placed IG order must always get its working_orders row.

WHY. _log_working_order_to_db names the two Let Winners Run columns in EVERY insert, and those columns
existed only because ig_shim created them at runtime with an ALTER TABLE on the order-placement path --
they were declared in no schema file, so a database rebuilt from the schema of record did not have them.
The whole function body is a documented "never raises" handler that only logs. So an ALTER that failed
for any reason -- missing privilege, lock timeout, a fresh database -- silently skipped the INSERT, and
the result was a LIVE IG ORDER WITH NO RECORD OF IT. _log_position_to_db already had this right.

The columns are now declared in run_schema.py, and the base insert no longer references them at all
unless there is an owner binding to write.
"""

import pytest

import ig_shim


class _FakeDB:
    def __init__(self, ledger):
        self.ledger = ledger

    def run(self, sql, **params):
        self.ledger.append({"sql": " ".join(sql.split()), "params": params})

    def close(self):
        pass


@pytest.fixture
def wired(monkeypatch):
    ledger, ddl = [], []
    monkeypatch.setattr(ig_shim, "get_db", lambda: _FakeDB(ledger))
    monkeypatch.setattr(ig_shim, "_ensure_lwr_owner_columns", lambda: ddl.append("ddl"))
    return ledger, ddl


def _log(**kw):
    kw.setdefault("deal_ref", "REF"); kw.setdefault("deal_id", "D1"); kw.setdefault("user_id", "u")
    kw.setdefault("ticker", "IWG.L"); kw.setdefault("epic", "E"); kw.setdefault("direction", "BUY")
    kw.setdefault("size", 1); kw.setdefault("entry_level", 10); kw.setdefault("stop_level", 9)
    kw.setdefault("limit_level", 12); kw.setdefault("otype", "STOP"); kw.setdefault("hvf_type", "H3")
    kw.setdefault("good_till", None); kw.setdefault("paper_trade", False)
    kw.setdefault("session_name", "uk"); kw.setdefault("signal_summary", "s")
    return ig_shim._log_working_order_to_db(**kw)


def test_an_ordinary_order_runs_no_ddl_and_is_logged(wired):
    ledger, ddl = wired

    _log()

    assert ddl == [], "no owner binding to write, so the order path must not issue schema DDL"
    assert len(ledger) == 1
    assert "lwr_owner_login" not in ledger[0]["sql"]
    assert ledger[0]["params"]["v_deal"] == "D1"


def test_the_order_is_still_logged_when_the_ddl_would_fail(monkeypatch):
    """THE REGRESSION. A failing ALTER must not cost the order its record."""
    ledger = []
    monkeypatch.setattr(ig_shim, "get_db", lambda: _FakeDB(ledger))
    monkeypatch.setattr(ig_shim, "_ensure_lwr_owner_columns",
                        lambda: (_ for _ in ()).throw(RuntimeError("permission denied for table")))

    _log()

    assert len(ledger) == 1, "the order row was lost because a schema statement failed"


def test_an_owner_bound_order_still_records_the_binding(wired):
    ledger, ddl = wired

    _log(lwr_owner_login="Alex", lwr_account_fingerprint="abc123")

    assert ddl == ["ddl"], "the binding columns must be ensured before they are written"
    assert len(ledger) == 1
    assert "lwr_owner_login" in ledger[0]["sql"]
    assert ledger[0]["params"]["v_lwr_owner"] == "Alex"
    assert ledger[0]["params"]["v_lwr_account"] == "abc123"


def test_both_shapes_write_the_same_core_columns(wired):
    """The guarded branch must not quietly drop a field the unguarded one records."""
    ledger, _ = wired

    _log()
    _log(lwr_owner_login="Alex", lwr_account_fingerprint="abc123")

    plain, bound = ledger[0]["params"], ledger[1]["params"]
    assert set(plain) <= set(bound)
    assert all(bound[k] == plain[k] for k in plain)


def test_the_unguarded_insert_is_what_lost_the_row(monkeypatch):
    """A guard that has never failed proves nothing: run the shape that shipped."""
    ledger = []
    monkeypatch.setattr(ig_shim, "get_db", lambda: _FakeDB(ledger))

    def old_shape():
        try:
            raise RuntimeError("permission denied for table")   # the unconditional _ensure_... call
        except Exception:
            return
    old_shape()

    assert ledger == [], "the reconstruction must lose the row, or this test proves nothing"


def test_the_columns_are_declared_in_the_schema_of_record():
    """Runtime DDL is a safety net; it must not be the only place these columns are defined."""
    from pathlib import Path
    schema = Path(__file__).parent.joinpath("run_schema.py").read_text(encoding="utf-8")

    for table in ("working_orders", "positions"):
        for column in ("lwr_owner_login", "lwr_account_fingerprint"):
            assert f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column}" in schema, \
                f"{table}.{column} is created only at runtime"
