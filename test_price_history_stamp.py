"""Every update of price_history stamps updated_at, whoever makes it (owner 2026-10-21: read files, not the
database). The bars file reads only rows stamped since it was built, so an unstamped change is never seen.

MEASURED 2026-10-21 before this migration, in a rolled-back transaction: a hand-written
`update price_history set high = high` left updated_at unchanged. The live effect is checked against the
database after run_schema.py runs; these tests pin what the migration declares.
"""
import run_schema


def _sql(name_part):
    hits = [sql for name, sql in run_schema.MIGRATIONS if name_part in name]
    assert len(hits) == 1, f"expected one migration named like {name_part!r}, found {len(hits)}"
    return " ".join(hits[0].split()).lower()


def test_trigger_stamps_every_update_before_it_is_written():
    sql = _sql("trigger that stamps updated_at")
    assert "before update on price_history" in sql
    assert "for each row execute function price_history_stamp_updated_at()" in sql


def test_function_sets_updated_at_to_now():
    sql = _sql("function that stamps updated_at")
    assert "new.updated_at = now();" in sql
    assert "return new;" in sql
    assert ":=" not in sql          # pg8000 reads :name as a parameter


def test_trigger_is_created_only_when_absent():
    # CREATE OR REPLACE TRIGGER on every run would lock price_history while the refresh writes.
    sql = _sql("trigger that stamps updated_at")
    assert "if not exists (select 1 from pg_trigger" in sql
    assert "create or replace trigger" not in sql


def test_function_runs_before_the_trigger_that_uses_it():
    names = [name for name, _sql in run_schema.MIGRATIONS]
    f = next(i for i, n in enumerate(names) if "function that stamps updated_at" in n)
    t = next(i for i, n in enumerate(names) if "trigger that stamps updated_at" in n)
    assert f < t
