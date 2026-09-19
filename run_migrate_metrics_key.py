# ======================================================================================================================
# File:         run_migrate_metrics_key.py
# Author:       Alex Hind
# Created:      2026-09-19
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Re-keys instrument_metrics_daily from (ticker, as_of) to (ticker, bar_date).
#
# WHY. The table records what four break-bar measures were ON A BAR, but it was keyed on as_of -- the day
# we happened to look -- and every consumer asks for a bar. That one mismatch caused all of the following,
# measured on 2026-09-19:
#
#   * ONE capture writes rows describing MANY bars. record_daily stores each instrument's LATEST completed
#     bar at ~03:30 UTC, so as_of 2026-09-18 held rows describing TEN different bar_dates -- 1,138 for the
#     17th, 601 for the 16th, and only TWO for the 18th itself.
#   * order_filter_audit.break_state looks up the position's opening bar exactly, so a position opened on
#     the 18th was judgeable for 2 of 1,772 instruments. That is the "RVOL not recorded" the owner has
#     reported repeatedly.
#   * 22,700 rows described only 17,317 distinct (ticker, bar_date) pairs -- 5,383 rows, 24%, were repeat
#     captures of a bar already described, on a 500 MB tier that is 83% full.
#   * the on-demand backfill needed a CONDITIONAL upsert purely to avoid clobbering a row that described a
#     different bar under the same key.
#
# After this, (ticker, bar_date) is unique, the lookup every reader performs is the key, re-capturing a bar
# updates it in place, and the conditional upsert is gone.
#
# as_of is KEPT as an informational column -- when the row was computed -- because the distinction between
# "which bar" and "when we looked" is real and worth recording. It simply is not the identity.
#
# SAFE TO RE-RUN. Dry run by default; --apply performs the change inside one transaction.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-09-19  Alex Hind   Initial build — de-duplicate, then re-key to (ticker, bar_date).
# ======================================================================================================================

import argparse
import logging
import sys

log = logging.getLogger("migrate_metrics_key")

TABLE = "instrument_metrics_daily"
OLD_PK = "instrument_metrics_daily_pkey"
NEW_PK = "instrument_metrics_daily_bar_pk"


def survey(db) -> dict:
    rows = db.run(f"select count(*) from {TABLE}")[0][0]
    pairs = db.run(f"select count(*) from (select distinct ticker, bar_date from {TABLE}) x")[0][0]
    dupes = db.run(f"select count(*) from (select ticker, bar_date from {TABLE} "
                   f"group by ticker, bar_date having count(*) > 1) x")[0][0]
    nulls = db.run(f"select count(*) from {TABLE} where bar_date is null")[0][0]
    keyed = db.run("select indexdef from pg_indexes where tablename = :t and indexname = :i",
                   t=TABLE, i=NEW_PK) or []
    return {"rows": rows, "pairs": pairs, "duplicate_pairs": dupes, "null_bar_date": nulls,
            "already_migrated": bool(keyed)}


def migrate(db, apply: bool) -> int:
    s = survey(db)
    log.info("before: %d rows, %d distinct (ticker,bar_date), %d duplicated pairs, %d null bar_date",
             s["rows"], s["pairs"], s["duplicate_pairs"], s["null_bar_date"])
    if s["already_migrated"]:
        log.info("already keyed on (ticker, bar_date) — nothing to do")
        return 0
    if s["null_bar_date"]:
        # A row with no bar_date describes no bar and cannot be keyed. Refuse rather than guess: the
        # right answer depends on why it is null, and silently deleting measurements is not a migration.
        log.error("%d rows have a NULL bar_date; they cannot be keyed. Resolve them first.",
                  s["null_bar_date"])
        return 2

    will_delete = s["rows"] - s["pairs"]
    log.info("%d redundant rows would be removed (keeping the LATEST as_of for each bar)", will_delete)
    if not apply:
        log.info("DRY RUN — nothing changed. Re-run with --apply to perform the migration.")
        return 0

    # One transaction: either the table is re-keyed or it is untouched.
    db.run("begin")
    try:
        # Keep the most recently COMPUTED row for each bar; ties broken by recorded_at, then ctid so the
        # statement is deterministic rather than merely usually-right.
        db.run(f"""delete from {TABLE} a using {TABLE} b
                   where a.ticker = b.ticker and a.bar_date = b.bar_date
                     and (a.as_of, a.recorded_at, a.ctid) < (b.as_of, b.recorded_at, b.ctid)""")
        db.run(f"alter table {TABLE} alter column bar_date set not null")
        db.run(f"alter table {TABLE} drop constraint if exists {OLD_PK}")
        db.run(f"alter table {TABLE} add constraint {NEW_PK} primary key (ticker, bar_date)")
        db.run("commit")
    except Exception:
        db.run("rollback")
        raise

    after = survey(db)
    log.info("after: %d rows, %d distinct pairs, keyed on (ticker, bar_date): %s",
             after["rows"], after["pairs"], after["already_migrated"])
    if after["rows"] != after["pairs"]:
        log.error("rows and distinct pairs disagree after migration — investigate")
        return 3
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Re-key instrument_metrics_daily to (ticker, bar_date).")
    ap.add_argument("--apply", action="store_true", help="perform the change (default is a dry run)")
    a = ap.parse_args()

    from db_pool import get_db
    db = get_db()
    try:
        return migrate(db, apply=a.apply)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
