# One-off: recompute squeeze_history outcomes after the trigger-bar exit fix (user 2026-07-17).
# The trigger date is unchanged — only the outcome walk now starts from the bar AFTER the trigger (entry
# is the trigger CLOSE, so that bar's own high/low is pre-entry). Updates outcome / outcome_date /
# return_pct in place; one price fetch per ticker, so far cheaper than a full replay.

import datetime as dt
import logging
import price_store
from db_pool import get_db
from squeeze_history import _exit_outcome

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("recompute")


def main():
    db = get_db()
    try:
        rows = db.run("select id, ticker, hvf_type, entry_level, stop_level, target_level, "
                      "triggered_date, outcome from squeeze_history where triggered_date is not null") or []
    finally:
        db.close()
    by_tk = {}
    for r in rows:
        by_tk.setdefault(r[1], []).append(r)
    log.info(f"{len(rows)} funnels across {len(by_tk)} tickers")

    changed = updated = 0
    for n, (tk, rs) in enumerate(by_tk.items(), 1):
        end = dt.date.today()
        b = price_store.get_bars(tk, end - dt.timedelta(days=900), end)
        if b is None or b.empty:
            continue
        tuples = [(i.date(), row.High, row.Low, row.Close, row.Volume) for i, row in b.iterrows()]
        by_date = {t[0]: k for k, t in enumerate(tuples)}
        db = get_db()
        try:
            for (rid, _tk, ht, e, s, t, td, old_oc) in rs:
                if td not in by_date:
                    continue
                i = by_date[td]
                bull = ht == "BULLISH"
                oc, od, ret = _exit_outcome(bull, float(e), float(s), float(t), tuples[i + 1:])
                if oc != old_oc:
                    changed += 1
                db.run("update squeeze_history set outcome=:o, outcome_date=:d, return_pct=:r where id=:id",
                       o=oc, d=od, r=(round(ret, 2) if ret is not None else None), id=rid)
                updated += 1
        finally:
            db.close()
        if n % 100 == 0 or n == len(by_tk):
            log.info(f"  {n}/{len(by_tk)} tickers — {updated} rows updated, {changed} outcomes changed")
    log.info(f"Done: {updated} rows updated, {changed} outcomes changed (mostly STOPPED->TARGET/OPEN).")


if __name__ == "__main__":
    main()
