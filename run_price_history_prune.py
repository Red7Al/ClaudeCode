# ======================================================================================================================
# File:         run_price_history_prune.py
# Author:       Claude
# Created:      2026-08-17
#
# Description:
# ----------------------------------------------------------------------------------------------------------------------
# Enforces config.PRICE_HISTORY_RETENTION_YEARS on the price_history table (user 2026-08-17: "5 years can go to 4.5
# years of data").
#
# WHY: price_history is 399 MB of a 521 MB database, against a 500 MB Supabase free-tier allowance. Five years of bars
# (2021-08-17 onwards) is more than anything reads. Trimming to 4.5 years removes the oldest ~10%, about 175,000 rows
# and ~40 MB, taking the database back under the limit.
#
# TWO THINGS THAT ARE EASY TO GET WRONG, both handled here:
#
#  1. A DELETE alone frees NOTHING measurable. Postgres marks the rows dead and reuses the space for future inserts,
#     but the file keeps its size and pg_database_size does not move. Reclaiming to the operating system needs
#     VACUUM FULL, which rewrites the table and its indexes. That is a separate, deliberate step here (--vacuum),
#     because it takes an ACCESS EXCLUSIVE lock: for the duration NOTHING can read or write price_history, and the
#     scanner, HVF report and order bridge all do. Run it when nothing is scanning, never on a schedule.
#
#  2. Deleting the oldest bars is not reversible from inside this system -- the data would have to be re-fetched from
#     Yahoo. So the prune is a dry run by default and only writes when told to (--apply).
#
# Usage:
#   python run_price_history_prune.py                 # dry run: what WOULD go, and how much space
#   python run_price_history_prune.py --apply         # delete, in batches
#   python run_price_history_prune.py --apply --vacuum  # ...then reclaim to the OS (locks the table)
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-08-17  Claude      Initial build.
# ======================================================================================================================

import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("price_history_prune")

# Delete in batches rather than one statement. A single DELETE of ~175,000 rows holds one long transaction, bloats WAL
# and blocks autovacuum from reclaiming anything until it commits; batches keep each transaction short so the table
# stays usable to the scanner throughout.
BATCH = 25_000


def _cutoff(db):
    import config
    years = float(getattr(config, "PRICE_HISTORY_RETENTION_YEARS", 5))
    row = db.run("select (current_date - (:y || ' years')::interval)::date", y=str(years))
    return row[0][0], years


def main() -> int:
    apply = "--apply" in sys.argv
    vacuum = "--vacuum" in sys.argv

    from db_pool import get_db
    db = get_db()
    try:
        cutoff, years = _cutoff(db)
        before = db.run("""select count(*), min(bar_date), max(bar_date),
                                  pg_size_pretty(pg_total_relation_size('price_history'))
                             from price_history""")[0]
        doomed = db.run("select count(*) from price_history where bar_date < :c", c=cutoff)[0][0]

        log.info(f"retention        : {years} years  (cutoff {cutoff})")
        log.info(f"price_history    : {before[0]:,} rows, {before[1]} .. {before[2]}, {before[3]}")
        log.info(f"older than cutoff: {doomed:,} rows "
                 f"({(100.0 * doomed / before[0]) if before[0] else 0:.1f}%)")

        # --vacuum must work on its own. Reclaiming is a SEPARATE operation from deleting: the normal
        # sequence is --apply now and --vacuum later, in a window where nothing is reading the table.
        # The first version returned here when there was nothing left to delete, so the second run
        # silently skipped the vacuum it was invoked for.
        if not doomed:
            log.info("nothing to prune.")
            if not vacuum:
                return 0
        elif not apply:
            log.info("DRY RUN - nothing deleted. Re-run with --apply to delete, "
                     "and --vacuum as well to return the space to the operating system.")
            return 0

        removed = 0
        while doomed and apply:
            n = db.run("""delete from price_history
                           where ctid in (select ctid from price_history
                                           where bar_date < :c limit :n)""",
                       c=cutoff, n=BATCH)
            # pg8000 returns None for DELETE; count what is left instead of trusting a rowcount.
            left = db.run("select count(*) from price_history where bar_date < :c", c=cutoff)[0][0]
            done = doomed - left - removed
            removed += done
            log.info(f"  deleted {removed:,} / {doomed:,}")
            if left == 0 or done == 0:
                break

        after = db.run("""select count(*), min(bar_date),
                                 pg_size_pretty(pg_total_relation_size('price_history'))
                            from price_history""")[0]
        log.info(f"deleted {removed:,} rows; {after[0]:,} remain from {after[1]}")
        log.info(f"table size still {after[2]} - dead rows are not returned to the OS without VACUUM FULL")

        if vacuum:
            log.info("VACUUM FULL price_history - this takes an exclusive lock; nothing can read the "
                     "table until it finishes...")
            db.run("vacuum full price_history")
            size = db.run("select pg_size_pretty(pg_total_relation_size('price_history'))")[0][0]
            dbsz = db.run("select pg_size_pretty(pg_database_size(current_database()))")[0][0]
            log.info(f"reclaimed - price_history now {size}, database now {dbsz}")
        else:
            log.info("space NOT reclaimed. Re-run with --vacuum when nothing is scanning.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
