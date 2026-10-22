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
over the WHOLE table, asked of the database once per process (one row back). Every write to
price_history stamps updated_at (price_store's writes; the trigger since e93dca0 for any other UPDATE), so a write
after the build moves the newest stamp; an insert or a delete moves the count.
LIMIT, recorded rather than left to be found: a transaction that began before the build and committed after it
carries a stamp older than the build's newest, so the probe cannot see it. bars_file runs after the price refresh
has finished, and its own verify step compares every ticker's sums with the database after the build.

The count is over the whole table, not only the rows from the file's retention cutoff on: equal counts prove the
database holds nothing older than the file, so a request reaching back past the cutoff is answered from the file too.
MEASURED 2026-10-22: 0 rows below the cutoff (2022-09-21); Best Settings asks back to 2021 and, with a cutoff-only
probe, never reached the file.
"""
import atexit
import bisect
import csv
import datetime as dt
import gzip
import json
import logging
import os
import sys

log = logging.getLogger(__name__)

_STATE = {"dir": None, "by": None, "count": 0, "newest": "", "current": None}


def mode() -> str:
    m = (os.environ.get("PRICE_BARS") or "off").strip().lower()
    return m if m in ("off", "shadow", "on") and os.environ.get("PRICE_BARS_DIR") else "off"


def _load(dir_path):
    """{ticker: [(bar_date, high, low, close, volume), ...] in date order}, the row count and the newest
    updated_at (text, microseconds -- sorts as time). The file's format is bars_file's (its names, columns and
    cell parser); grouped by ticker here rather than through bars_file.read, which keys every row by
    (ticker, date) -- 1.9 million keys this reader does not need."""
    import bars_file
    with open(os.path.join(dir_path, bars_file.META_NAME), encoding="utf-8") as fh:
        meta = json.load(fh)
    by, count, newest, dates = {}, 0, "", {}
    with gzip.open(os.path.join(dir_path, bars_file.DATA_NAME), "rt", encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        if next(rd) != list(bars_file.COLUMNS):
            raise ValueError("price bars file has unexpected columns")
        for cells in rd:
            tk, bd, _o, hi, lo, cl, vol, _src, _chk, upd = bars_file._row_in(cells)
            day = dates.get(bd)
            if day is None:   # ~1,000 distinct dates across 1.9 million rows: parse each once
                day = dates[bd] = dt.date.fromisoformat(bd)
            by.setdefault(tk, []).append((day, hi, lo, cl, vol))
            count += 1
            if upd > newest:
                newest = upd
    if count != meta.get("rows"):
        raise ValueError(f"price bars file holds {count} rows, its metadata says {meta.get('rows')}")
    for rows in by.values():
        rows.sort(key=lambda r: r[0])
    return by, count, newest


def _ready(db) -> bool:
    """Loaded and CURRENT. Loads once per process and asks the database once per process."""
    dir_path = os.environ.get("PRICE_BARS_DIR")
    if _STATE["dir"] != dir_path:
        _STATE.update(dir=dir_path, by=None, current=None)
        try:
            by, count, newest = _load(dir_path)
            _STATE.update(by=by, count=count, newest=newest)
            log.info("price bars file loaded: %s rows, newest stamp %s", f"{count:,}", newest)
        except Exception as exc:
            log.warning("price bars file unusable, reading the database: %s", exc)
            _STATE["current"] = False
            return False
    if _STATE["current"] is None:
        try:
            n, newest = (db.run("select count(*), max(updated_at) from price_history") or [(None, None)])[0]
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
    """The rows the database query would return for `items` [(ticker, from_date)]."""
    out = {}
    for tk, d0 in items:
        start = d0 - back
        if isinstance(start, dt.datetime):
            start = start.date()
        bars = _STATE["by"].get(tk, ())
        rows = bars[bisect.bisect_left(bars, start, key=lambda r: r[0]):]   # date order (_load)
        if rows:
            out[tk] = rows
    return out


def _differences(db_out, file_out, limit=5):
    diffs = []
    for tk in sorted(set(db_out) | set(file_out)):
        a, b = db_out.get(tk, []), file_out.get(tk, [])
        first = next(((x, y) for x, y in zip(a, b) if tuple(x) != tuple(y)), None)
        if first or len(a) != len(b):
            diffs.append(f"{tk}: database {len(a)} rows, file {len(b)} rows"
                         + (f", first difference {first}" if first else ""))
            if len(diffs) >= limit:
                break
    return diffs


_SHADOW = {"calls": 0, "rows": 0, "different": 0}


def _shadow_summary():
    s = _SHADOW
    print(f"PRICE-BARS SHADOW SUMMARY: {s['calls']} calls, {s['rows']:,} rows from the database, "
          f"{s['different']} different -- {'IDENTICAL' if not s['different'] else 'NOT IDENTICAL'}", file=sys.stderr)


def _record_shadow(rows, different):
    """Count every shadow comparison and print one summary line when the process ends -- the jobs do not all
    log INFO, and the proof must be in the job log whatever they configure."""
    if not _SHADOW["calls"]:
        atexit.register(_shadow_summary)
    _SHADOW["calls"] += 1
    _SHADOW["rows"] += rows
    _SHADOW["different"] += int(different)


def fetch_when_needed(want: str = "shadow") -> bool:
    """For a job that only sometimes needs bars (Closing Window: every 10 minutes, almost always with nothing
    to judge -- 51 of 51 runs on 2026-10-21): fetch the file from IONOS now, inside the run, instead of in a
    workflow step on every run. Uses .github/scripts/fetch_price_bars.sh; on success points this process at
    it. Does nothing outside GitHub Actions, or when a file is already configured. True when bars come from
    a file (shadow or on) from here on."""
    if os.environ.get("PRICE_BARS_DIR"):
        return mode() != "off"
    if not (os.environ.get("RUNNER_TEMP") and os.environ.get("GITHUB_ENV") and os.environ.get("IONOS_SSH_KEY")):
        return False
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts", "fetch_price_bars.sh")
    try:
        r = subprocess.run(["bash", script, want], capture_output=True, text=True, timeout=300)
    except Exception as exc:
        log.warning("price bars fetch did not run (%s); reading the database", exc)
        return False
    if r.returncode != 0:
        log.warning("price bars fetch failed (%s); reading the database", (r.stderr or r.stdout).strip()[-300:])
        return False
    os.environ["PRICE_BARS_DIR"] = os.path.join(os.environ["RUNNER_TEMP"], "price_bars")
    os.environ["PRICE_BARS"] = want
    log.info("%s", r.stdout.strip()[-300:])
    return True


def serve(db, items, back, from_database):
    """The bars for `items` [(ticker, from_date)] widened back by `back` (a timedelta), as
    {ticker: [(bar_date, high, low, close, volume), ...]}. `from_database` is the caller's own query."""
    m = mode()
    if m == "off" or not _ready(db):
        return from_database()
    file_out = _from_file(items, back)
    if m == "on":
        return file_out
    db_out = from_database()
    diffs = _differences(db_out, file_out)
    rows = sum(len(v) for v in db_out.values())
    _record_shadow(rows, bool(diffs))
    if diffs:
        log.warning("PRICE-BARS SHADOW DIFFERENT: %d tickers, %s rows from the database; %s",
                    len(items), f"{rows:,}", "; ".join(diffs))
    else:
        log.info("PRICE-BARS SHADOW IDENTICAL: %d tickers, %s rows", len(items), f"{rows:,}")
    return db_out
