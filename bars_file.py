#!/usr/bin/env python3
"""The price bars file: every row of price_history, read from the database ONCE and then only what changed
(owner 2026-10-21: "read files, not the database"; rule in CLAUDE.md).

    python bars_file.py build  --out DIR [--prev DIR]     # seed (no --prev) or update from the previous file
    python bars_file.py verify --dir DIR                  # prove the file equals price_history

FILES in DIR: price_bars.csv.gz (one row per (ticker, bar_date), floats written with repr so they read back
bit-identical) and price_bars.meta.json (watermark, row count, retention cutoff, the counts of the read).

UPDATE. Every write to price_history stamps updated_at -- price_store.py's own writes, and since e93dca0 a
trigger for any other UPDATE. A build re-reads only rows stamped after the previous build's START minus
OVERLAP: a transaction stamps now() when it BEGINS but becomes visible when it COMMITS, so a long write that
began before the last build and committed after it would otherwise fall between the two. Re-reading the
overlap is idempotent. MEASURED 2026-10-21: the 03:30 refresh stamped 40,562 rows; a full read is 1,925,858.

DELETES leave no stamp. Bars are deleted only below price_store.retention_cutoff (the nightly prune in
price_audit.py), so every build drops rows below that same cutoff, computed by the same function.

PROOF. verify compares per ticker: row count, sum of open/high/low/close/volume, and the newest updated_at,
computed INSIDE Postgres -- about 2,000 rows back, not 1.9 million. Rows below the file's cutoff are left out
on both sides: the database may still hold a day the prune has not reached yet.
"""
import argparse
import csv
import datetime as dt
import gzip
import io
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

DATA_NAME = "price_bars.csv.gz"
META_NAME = "price_bars.meta.json"
COLUMNS = ("ticker", "bar_date", "open", "high", "low", "close", "volume", "source", "double_checked",
           "updated_at")
OVERLAP = dt.timedelta(hours=6)
# Relative tolerance for comparing sums: Postgres and Python add the same floats in a different order.
SUM_TOLERANCE = 1e-9


def _f(v):
    return "" if v is None else repr(float(v))


def _row_out(r):
    tk, bd, o, h, lo, c, vol, src, chk, upd = r
    return [tk, str(bd), _f(o), _f(h), _f(lo), _f(c), "" if vol is None else str(int(vol)), src,
            # Microseconds always written, so the stamps sort correctly as text.
            "1" if chk else "0", upd.isoformat(timespec="microseconds") if hasattr(upd, "isoformat") else str(upd)]


def _row_in(cells):
    tk, bd, o, h, lo, c, vol, src, chk, upd = cells
    num = lambda s: None if s == "" else float(s)
    return (tk, bd, num(o), num(h), num(lo), num(c), None if vol == "" else int(vol), src, chk == "1", upd)


def read(dir_path):
    """{(ticker, bar_date): row tuple} and the metadata, or ({}, None) when there is no file."""
    meta_path = os.path.join(dir_path, META_NAME)
    if not os.path.isfile(meta_path):
        return {}, None
    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    rows = {}
    with gzip.open(os.path.join(dir_path, DATA_NAME), "rt", encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        if next(rd) != list(COLUMNS):
            raise ValueError("price bars file has unexpected columns")
        for cells in rd:
            r = _row_in(cells)
            rows[(r[0], r[1])] = r
    if len(rows) != meta.get("rows"):
        raise ValueError(f"price bars file holds {len(rows)} rows, its metadata says {meta.get('rows')}")
    return rows, meta


def _write(dir_path, rows, meta):
    os.makedirs(dir_path, exist_ok=True)
    data_tmp = os.path.join(dir_path, DATA_NAME + ".tmp")
    with gzip.open(data_tmp, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        for key in sorted(rows):
            w.writerow(_row_out_str(rows[key]))
    os.replace(data_tmp, os.path.join(dir_path, DATA_NAME))
    meta_tmp = os.path.join(dir_path, META_NAME + ".tmp")
    with open(meta_tmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    os.replace(meta_tmp, os.path.join(dir_path, META_NAME))


def _row_out_str(r):
    # Every row held in memory is in the file's own types (_normalise): dates and stamps are already text.
    tk, bd, o, h, lo, c, vol, src, chk, upd = r
    return [tk, bd, _f(o), _f(h), _f(lo), _f(c), "" if vol is None else str(vol), src, "1" if chk else "0", upd]


def _normalise(r):
    """A database row in the file's own types, so old and new rows compare and sort alike."""
    return _row_in(_row_out(r))


def build(out_dir, prev_dir=None, db=None):
    """Write the file to out_dir from the previous file in prev_dir (or from nothing) plus the rows stamped
    since. Returns the metadata written."""
    import price_store
    from db_pool import get_db
    own = db is None
    if own:
        db = get_db()
    try:
        started = db.run("select now()")[0][0]
        rows, prev = read(prev_dir) if prev_dir else ({}, None)
        cutoff = price_store.retention_cutoff(db)
        sel = "select " + ", ".join(COLUMNS) + " from price_history where bar_date >= :c"
        t0 = time.time()
        if prev:
            since = dt.datetime.fromisoformat(prev["build_started"]) - OVERLAP
            fresh = db.run(sel + " and updated_at > :s", c=cutoff, s=since) or []
        else:
            since = None
            fresh = db.run(sel, c=cutoff) or []
        read_secs = time.time() - t0
    finally:
        if own:
            db.close()
    for r in fresh:
        n = _normalise(r)
        rows[(n[0], n[1])] = n
    cutoff_s = str(cutoff)
    dropped = [k for k in rows if k[1] < cutoff_s]
    for k in dropped:
        del rows[k]
    meta = {"build_started": started.isoformat(), "built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "rows": len(rows), "retention_cutoff": cutoff_s,
            "read": {"mode": "update" if prev else "seed", "since": since.isoformat() if since else None,
                     "rows_read": len(fresh), "seconds": round(read_secs, 1), "dropped_below_cutoff": len(dropped)},
            "previous_build_started": prev["build_started"] if prev else None}
    _write(out_dir, rows, meta)
    return meta


def _file_aggregates(rows):
    agg = {}
    for (tk, _bd), r in rows.items():
        a = agg.setdefault(tk, [0, 0.0, 0.0, 0.0, 0.0, 0, ""])
        a[0] += 1
        for i, v in enumerate(r[2:6]):
            a[1 + i] += v or 0.0
        a[5] += r[6] or 0
        a[6] = max(a[6], r[9])
    return agg


def _close(a, b):
    return math.isclose(a, b, rel_tol=SUM_TOLERANCE, abs_tol=1e-6)


def verify(dir_path, db=None):
    """List of differences between the file and price_history (empty = proven equal)."""
    from db_pool import get_db
    rows, meta = read(dir_path)
    if meta is None:
        return ["no price bars file"]
    own = db is None
    if own:
        db = get_db()
    try:
        live = db.run("select ticker, count(*), sum(open), sum(high), sum(low), sum(close), sum(volume), "
                      "max(updated_at) from price_history where bar_date >= :c group by ticker",
                      c=meta["retention_cutoff"]) or []
    finally:
        if own:
            db.close()
    mine = _file_aggregates(rows)
    out = []
    seen = set()
    for tk, n, so, sh, sl, sc, sv, upd in live:
        seen.add(tk)
        a = mine.get(tk)
        if a is None:
            out.append(f"{tk}: {n} rows in the database, none in the file")
            continue
        if a[0] != n:
            out.append(f"{tk}: {n} rows in the database, {a[0]} in the file")
        for label, fv, dv in (("open", a[1], so), ("high", a[2], sh), ("low", a[3], sl), ("close", a[4], sc)):
            if not _close(fv, float(dv or 0.0)):
                out.append(f"{tk}: sum({label}) database {dv} file {fv}")
        if a[5] != int(sv or 0):
            out.append(f"{tk}: sum(volume) database {sv} file {a[5]}")
        if upd is not None and dt.datetime.fromisoformat(a[6]) != upd:
            out.append(f"{tk}: newest updated_at database {upd.isoformat()} file {a[6]}")
    for tk in sorted(set(mine) - seen):
        out.append(f"{tk}: {mine[tk][0]} rows in the file, none in the database")
    return out


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", required=True)
    b.add_argument("--prev")
    v = sub.add_parser("verify")
    v.add_argument("--dir", required=True)
    a = ap.parse_args()
    if a.cmd == "build":
        meta = build(a.out, a.prev)
        size = os.path.getsize(os.path.join(a.out, DATA_NAME))
        print(f"price bars file: {meta['rows']:,} rows, {size:,} bytes; read {meta['read']}")
        return 0
    diffs = verify(a.dir)
    for d in diffs[:50]:
        print("DIFF", d)
    print("RESULT", "PASS" if not diffs else f"FAIL ({len(diffs)} differences)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
