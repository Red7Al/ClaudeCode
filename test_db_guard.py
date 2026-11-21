"""Canary for conftest._no_test_reaches_the_live_database (independent test specialist, 2026-11-21).
If the guard stops working, these fail: an unguarded test that reaches get_db must be recorded and refused, the
teardown must fail it even when the test swallows the error, and the table parsing must cover update/delete."""
import os
import subprocess
import sys
import textwrap

import pytest

import conftest
import db_pool

HERE = os.path.dirname(os.path.abspath(__file__))


def _run_inner(tmp_path, body):
    """Run a throwaway test file under a copy of the real conftest, in a subprocess, with CI placeholder credentials."""
    (tmp_path / "conftest.py").write_text(open(os.path.join(HERE, "conftest.py"), encoding="utf-8").read(), encoding="utf-8")
    (tmp_path / "test_inner.py").write_text(textwrap.dedent(body), encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    live_state: x\n    real_db_pool: x\n", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=HERE, SUPABASE_USER="ci-placeholder", SUPABASE_DB_PASSWORD="ci-placeholder")
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_inner.py"],
                          cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=300)


def test_unguarded_get_db_is_recorded_and_refused():
    db = db_pool.get_db()
    assert isinstance(db, conftest._RecordingDb)
    with pytest.raises(ConnectionError):
        db.run("select 1 from app_config")
    assert conftest._DB_GUARD["attempts"] == ["get_db()", "select app_config"]
    conftest._DB_GUARD["attempts"].clear()       # this test is the deliberate offender; do not fail its own teardown


@pytest.mark.parametrize("sql,expect", [
    ("insert into missed_trade_log (a) values (1)", "insert missed_trade_log"),
    ("UPDATE working_orders SET x=1 WHERE id=2", "update working_orders"),
    ("delete from   web_json_store where k=1", "delete web_json_store"),
    ("select a,\n b from price_history where x=1", "select price_history"),
    ("with t as (select 1) select * from t", "with t"),
])
def test_table_parsing_names_the_verb_and_table(sql, expect):
    attempts = []
    rec = conftest._RecordingDb(attempts)
    with pytest.raises(ConnectionError):
        rec.run(sql)
    assert attempts == [expect]


def test_teardown_fails_a_test_that_swallows_the_refusal(tmp_path):
    r = _run_inner(tmp_path, """
        import db_pool
        def test_swallows():
            try:
                db_pool.get_db().run("insert into missed_trade_log values (1)")
            except Exception:
                pass
    """)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "reached the live database" in r.stdout and "insert missed_trade_log" in r.stdout, r.stdout


def test_a_from_import_alias_is_also_guarded(tmp_path):
    r = _run_inner(tmp_path, """
        from db_pool import get_db
        def test_alias():
            try:
                get_db().run("update working_orders set a=1")
            except Exception:
                pass
    """)
    assert r.returncode != 0 and "update working_orders" in r.stdout, r.stdout


def test_direct_pg8000_connection_is_refused_and_fails_the_test(tmp_path):
    r = _run_inner(tmp_path, """
        import pg8000.native
        def test_direct():
            try:
                pg8000.native.Connection("u", host="127.0.0.1", port=1, password="x")
            except Exception:
                pass
    """)
    assert r.returncode != 0 and "pg8000 Connection()" in r.stdout, r.stdout


def test_opt_out_markers_and_empty_database_pass(tmp_path):
    r = _run_inner(tmp_path, """
        import pytest, db_pool
        @pytest.mark.real_db_pool
        def test_optout():
            assert db_pool.get_db.__name__ != "_guarded_get_db"
        def test_empty(empty_database):
            with pytest.raises(db_pool.DatabaseUnavailable):
                db_pool.get_db().run("select 1 from t")
            assert empty_database[0][0] == "select 1 from t"
    """)
    assert r.returncode == 0, r.stdout + r.stderr
