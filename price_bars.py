"""Price bars from the price bars file instead of price_history (owner 2026-10-21: "read files, not the database";
rule in CLAUDE.md). bars_file.py builds the file once per refresh; this module is the ONE reader of it.

    PRICE_BARS_DIR   the directory holding price_bars.csv.gz + price_bars.meta.json (a job fetches it from IONOS)
    PRICE_BARS       off (default) | shadow | on

off     the database, exactly as before.
shadow  the database answers; the file's answer is computed beside it and every difference is logged. A step moves
        to "on" only after its shadow runs show none (owner: "a step moves only when its output from the file is
        identical, row for row, to its output from the database").
on      the file answers -- when it is CURRENT. Otherwise the database answers, as before.

CURRENT means the file holds exactly what price_history holds: the same row count and the same newest updated_at
at or above the file's retention cutoff, asked of the database once per process (one row back). Every write to
price_history stamps updated_at (price_store's writes; the trigger since e93dca0 for any other UPDATE), so a write
after the build moves the newest stamp. Deletes happen only below the retention cutoff, which both sides exclude.
LIMIT, recorded rather than left to be found: a transaction that began before the build and committed after it
carries a stamp older than the build's newest, so the probe cannot see it. bars_file runs after the price refresh
has finished, and its own verify step compares every ticker's sums with the database after the build.

A request reaching below the file's retention cutoff goes to the database: the database may still hold a day the
prune has not reached yet, and the file never does.
"""
import csv
import datetime as dt
import gzip
import json
import logging
import os

log = logging.getLogger(__name__)

_STATE = {"dir": None, "by": None, "meta": None, "count": 0, "newest": "", "current": None}


def mode() -> str:
    m = (os.environ.get("PRICE_BARS") or "off").strip().lower()
    return m if m in ("off", "shadow", "on") and os.environ.get("PRICE_BARS_DIR") else "off"


def _load(dir_path):
    """{ticker: [(bar_date, high, low, close, volume), ...] in date order}, the metadata, the row count and the
    newest updated_at (text, microseconds -- sorts as time)."""
    with open(os.path.join(dir_path, "price_bars.meta.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    by, count, newest = {}, 0, ""
    num = lambda s: None if s == "" else float(s)
    with gzip.open(os.path.join(dir_path, "price_bars.csv.gz"), "rt", encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        head = next(rd)
        if head[:7] != ["ticker", "bar_date", "open", "high", "low", "close", "volume"] or head[9] != "updated_at":
            raise ValueError("price bars file has unexpected columns")
        for tk, bd, _o, hi, lo, cl, vol, _src, _chk, upd in rd:
            by.setdefault(tk, []).append((dt.date.fromisoformat(bd), num(hi), num(lo), num(cl),
                                          None if vol == "" else int(vol)))
            count += 1
            if upd > newest:
                newest = upd
    if count != meta.get("rows"):
        raise ValueError(f"price bars file holds {count} rows, its metadata says {meta.get('rows')}")
    for rows in by.values():
        rows.sort(key=lambda r: r[0])
    return by, meta, count, newest


def _ready(db) -> bool:
    """Loaded and CURRENT. Loads once per process and asks the database once per process."""
    dir_path = os.environ.get("PRICE_BARS_DIR")
    if _STATE["dir"] != dir_path:
        _STATE.update(dir=dir_path, by=None, meta=None, current=None)
        try:
            by, meta, count, newest = _load(dir_path)
            _STATE.update(by=by, meta=meta, count=count, newest=newest)
            log.info("price bars file loaded: %s rows, newest stamp %s", f"{count:,}", newest)
        except Exception as exc:
            log.warning("price bars file unusable, reading the database: %s", exc)
            _STATE["current"] = False
            return False
    if _STATE["current"] is None:
        try:
            n, newest = (db.run("select count(*), max(updated_at) from price_history where bar_date >= :c",
                                c=_STATE["meta"]["retention_cutoff"]) or [(None, None)])[0]
            db_newest = newest.isoformat(timespec="microseconds") if newest is not None else ""
            # The file writes its stamps the same way (bars_file._row_out); compare as instants, not as text.
            same = (n == _STATE["count"] and newest is not None and _STATE["newest"]
                    and dt.datetime.fromisoformat(_STATE["newest"]) == newest)
            _STATE["current"] = bool(same)
            log.info("price bars file %s: database %s rows newest %s, file %s rows newest %s",
                     "CURRENT" if same else "STALE -- reading the database", n, db_newest,
                     _STATE["count"], _STATE["newest"])
        except Exception as exc:
            log.warning("price bars version probe failed, reading the database: %s", exc)
            _STATE["current"] = False
    return bool(_STATE["current"])


def _from_file(items, back):
    """The rows the database query would return for `items` [(ticker, from_date)], or None when a request
    reaches below the file's retention cutoff."""
    cutoff = dt.date.fromisoformat(_STATE["meta"]["retention_cutoff"])
    out = {}
    for tk, d0 in items:
        start = d0 - back
        if isinstance(start, dt.datetime):
            start = start.date()
        if start < cutoff:
            return None
        rows = [r for r in _STATE["by"].get(tk, ()) if r[0] >= start]
        if rows:
            out[tk] = rows
    return out


def _differences(db_out, file_out, limit=5):
    diffs = []
    for tk in sorted(set(db_out) | set(file_out)):
        a, b = db_out.get(tk, []), file_out.get(tk, [])
        if [tuple(r) for r in a] != [tuple(r) for r in b]:
            diffs.append(f"{tk}: database {len(a)} rows, file {len(b)} rows"
                         + (f", first difference {next((x, y) for x, y in zip(a, b) if tuple(x) != tuple(y))}"
                            if any(tuple(x) != tuple(y) for x, y in zip(a, b)) else ""))
            if len(diffs) >= limit:
                break
    return diffs


def serve(db, items, back, from_database):
    """The bars for `items` [(ticker, from_date)] widened back by `back` (a timedelta), as
    {ticker: [(bar_date, high, low, close, volume), ...]}. `from_database` is the caller's own query."""
    m = mode()
    if m == "off" or not _ready(db):
        return from_database()
    file_out = _from_file(items, back)
    if file_out is None:
        log.info("price bars: request reaches below the file's retention cutoff -- reading the database")
        return from_database()
    if m == "on":
        return file_out
    db_out = from_database()
    diffs = _differences(db_out, file_out)
    rows = sum(len(v) for v in db_out.values())
    if diffs:
        log.warning("PRICE-BARS SHADOW DIFFERENT: %d tickers, %s rows from the database; %s",
                    len(items), f"{rows:,}", "; ".join(diffs))
    else:
        log.info("PRICE-BARS SHADOW IDENTICAL: %d tickers, %s rows", len(items), f"{rows:,}")
    return db_out
