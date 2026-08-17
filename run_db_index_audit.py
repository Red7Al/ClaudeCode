# ======================================================================================================================
# File:         run_db_index_audit.py
# Author:       Claude
# Created:      2026-08-17
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Weekly index-health review of the Supabase Postgres database (user 2026-08-17: "also review this weekly to see what
# is required"). Reports three things and posts them to #alerts:
#
#   1. DUPLICATE indexes  — two or more indexes with identical definitions on the same table. Only one can ever be
#                           chosen by the planner; the rest are pure write cost, maintained on every insert and update.
#   2. UNUSED indexes     — zero scans since the statistics were last reset, reported WITH that reset date so the
#                           number can be judged. A week of stats means nothing; three months is evidence.
#   3. MISSING FK indexes — a foreign key whose child column has no covering index. Deletes and updates on the parent
#                           then scan the whole child table, holding a lock while they do it.
#
# READ-ONLY BY DESIGN. It never drops or creates anything. The 2026-08-17 clean-up was applied deliberately, as
# reviewed migrations in run_schema.py; this job exists so the NEXT drift is noticed rather than discovered months
# later, which is exactly how the expired GH_PAT went unseen for eight weeks. Recommending is cheap and safe;
# auto-dropping an index on a live trading database because a counter says zero is neither.
#
# Scope is the `public` schema only. The auth/storage/realtime schemas belong to Supabase, several of their indexes
# are also unused, and they are not ours to manage — reporting them would be noise that trains everyone to ignore
# the message.
#
# Silent when there is nothing to say, so a post means something changed.
#
# Usage:
#   python run_db_index_audit.py            # audit + Slack post if anything is found
#   python run_db_index_audit.py --print    # print only, never post (local inspection)
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-08-17  Claude      Initial build.
# ======================================================================================================================

import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("db_index_audit")

# An index this small is not worth a line in an alert: dropping it frees nothing measurable, and the write cost of a
# tiny index on a rarely-written table is noise. The point of the report is to surface what is worth acting on.
MIN_UNUSED_BYTES = 64 * 1024


# indoption MUST be in the grouping key. Without it this reported (ticker, bar_date) and (ticker, bar_date DESC) as
# identical, which they are not — the first draft of this script flagged price_history's 168 MB primary key as a
# duplicate of idx_price_history_ticker_date on exactly that mistake. A btree can scan backwards, so a DESC twin is
# often redundant in PRACTICE, but "often redundant" is a judgement for a human, not a line in an automated report
# that also names a primary key as the thing to drop.
#
# is_constraint flags whether any member of the pair backs a PRIMARY KEY or UNIQUE constraint. Such an index can
# never be the one dropped — it enforces correctness, not just speed — so the report says which member is safe.
_DUPLICATES = """
select  indrelid::regclass::text                       as table_name,
        array_agg(indexrelid::regclass::text order by indisunique, indexrelid::regclass::text) as names,
        bool_or(indisunique or indisprimary)           as has_constraint,
        pg_size_pretty(sum(pg_relation_size(indexrelid))) as total_size
  from  pg_index i
  join  pg_class c on c.oid = i.indexrelid
  join  pg_namespace n on n.oid = c.relnamespace
 where  n.nspname = 'public'
 group  by indrelid, (indclass::text || ' ' || indkey::text || ' ' || indoption::text ||
                      ' ' || coalesce(indexprs::text,'') || ' ' || coalesce(indpred::text,''))
having  count(*) > 1
 order  by sum(pg_relation_size(indexrelid)) desc
"""

_UNUSED = """
select  s.relname                                  as table_name,
        s.indexrelname                             as index_name,
        pg_relation_size(s.indexrelid)             as bytes,
        pg_size_pretty(pg_relation_size(s.indexrelid)) as size
  from  pg_stat_user_indexes s
  join  pg_index i on i.indexrelid = s.indexrelid
  join  pg_class c on c.oid = s.indexrelid
  join  pg_namespace n on n.oid = c.relnamespace
 where  s.idx_scan = 0
   and  not i.indisprimary and not i.indisunique
   and  n.nspname = 'public'
 order  by pg_relation_size(s.indexrelid) desc
"""

# A foreign key is covered when some index's leading columns match the FK's columns in order. conkey is the child-side
# column list; indkey is the index's column list, so the test is "conkey is a prefix of indkey".
_MISSING_FK = """
select  con.conrelid::regclass::text as table_name,
        con.conname                  as fk_name,
        pg_size_pretty(pg_relation_size(con.conrelid)) as table_size
  from  pg_constraint con
  join  pg_class c on c.oid = con.conrelid
  join  pg_namespace n on n.oid = c.relnamespace
 where  con.contype = 'f'
   and  n.nspname = 'public'
   and  not exists (
            select 1 from pg_index i
             where i.indrelid = con.conrelid
               and (i.indkey::smallint[])[0:array_length(con.conkey,1)-1] = con.conkey
        )
 order  by pg_relation_size(con.conrelid) desc
"""

_STATS_AGE = """
select  stats_reset::date::text,
        (now()::date - stats_reset::date)
  from  pg_stat_database where datname = current_database()
"""


def audit() -> dict:
    """Run the three checks. Read-only; returns plain data so the caller decides what to do with it."""
    from db_pool import get_db
    db = get_db()
    try:
        row = (db.run(_STATS_AGE) or [[None, 0]])[0]
        result = {
            "stats_since": row[0],
            "stats_days": int(row[1] or 0),
            "duplicates": [(r[0], list(r[1]), bool(r[2]), r[3]) for r in (db.run(_DUPLICATES) or [])],
            "unused": [(r[0], r[1], int(r[2]), r[3]) for r in (db.run(_UNUSED) or [])
                       if int(r[2]) >= MIN_UNUSED_BYTES],
            "missing_fk": [(r[0], r[1], r[2]) for r in (db.run(_MISSING_FK) or [])],
        }
    finally:
        db.close()
    return result


def _lines(a: dict) -> list:
    out = []
    if a["duplicates"]:
        out.append(f"*Duplicate indexes ({len(a['duplicates'])})* — identical definitions; only one can be used, "
                   f"all are maintained on every write:")
        for table, names, has_constraint, size in a["duplicates"]:
            # Plain ASCII marker: this also prints to a Windows console, whose default cp1252 codec cannot
            # encode most symbols and would crash the whole audit on a decoration.
            note = "  [!] one of these backs a PK/UNIQUE constraint - keep that one" if has_constraint else ""
            out.append(f"  • `{table}` — {', '.join(f'`{n}`' for n in names)}  ({size}){note}")
    if a["unused"]:
        out.append(f"*Unused indexes ({len(a['unused'])})* — zero scans in {a['stats_days']} days "
                   f"(stats since {a['stats_since']}); over ~64 kB only:")
        for table, name, _b, size in a["unused"]:
            out.append(f"  • `{table}.{name}`  ({size})")
    if a["missing_fk"]:
        out.append(f"*Foreign keys with no covering index ({len(a['missing_fk'])})* — parent deletes scan the "
                   f"whole child table:")
        for table, fk, size in a["missing_fk"]:
            out.append(f"  • `{table}` — `{fk}`  (table {size})")
    return out


def main() -> int:
    a = audit()
    lines = _lines(a)
    findings = len(a["duplicates"]) + len(a["unused"]) + len(a["missing_fk"])

    print(f"Index audit — stats since {a['stats_since']} ({a['stats_days']} days)")
    print("\n".join(lines) if lines else "  nothing to report — no duplicates, no sizeable unused indexes, "
                                        "every foreign key covered.")

    if "--print" in sys.argv or not findings:
        return 0

    try:
        import notify
        notify.alert_system_error(
            session="Weekly",
            component="Database indexes (public schema)",
            summary=f"{findings} index finding(s) to review. These are RECOMMENDATIONS — nothing has been changed. "
                    f"Apply any you agree with as a migration in run_schema.py.",
            detail="\n".join(lines))
    except Exception as exc:
        log.warning(f"could not post the index audit: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
