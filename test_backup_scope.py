"""Owner 2026-10-23 ("yes"): the weekly backup no longer dumps price_history's rows -- most of every dump's egress,
and the table already has a verified full copy on IONOS (the price bars file). Its definition is still dumped and
checked, so a restore recreates the table."""
import yaml


def _dump_steps():
    wf = yaml.safe_load(open(".github/workflows/supabase-backup.yml", encoding="utf-8"))
    return [str(s.get("run", "")) for job in wf["jobs"].values() for s in job["steps"]]


def test_price_history_rows_are_not_dumped():
    dump = next(r for r in _dump_steps() if "pg_dump \\" in r)
    assert "--exclude-table-data=public.price_history" in dump
    assert "--exclude-table " not in dump and "-T " not in dump      # the definition stays


def test_the_check_requires_the_price_history_definition():
    check = next(r for r in _dump_steps() if "pg_restore --list" in r)
    assert '" TABLE public price_history "' in check
