#!/usr/bin/env python3
"""Prove a report summary file says what the database says (owner 2026-10-21: "do everything you can to be
sure the summaries are correct or they will be useless").

    python verify_report_summary.py --snapshot S.json --summary R.json            # every section
    python verify_report_summary.py --snapshot S.json --summary R.json --records-out OUT.json [--no-file]

SECTIONS. Rebuilds every section from the database with publish_report_summary.build -- the builder's own
definition, never a second copy -- passes it through the builder's own serialisation, and compares it with
the file key by key, values exact. Each difference is then classed:
  STALE   the instrument has a price_history row stamped after the file was built (updated_at > built_at),
          so the database moved on after the build -- the file was right when built;
  DEFECT  no such row: the file disagrees with the database for a reason the price data does not explain.
Exit 0 only when there is no DEFECT and no section is missing.

RECORDS. --records-out writes the owner's /api/records payload as the page receives it, from the file, or
with --no-file from the database. Run it once each way in SEPARATE processes (the web tier caches each
section per process) and compare the two files: that is the report as he sees it, not an intermediate.
Reads his own login's data on this machine only; writes nothing anywhere else.

Read-only against the database. Costs about one summary build of egress per run.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)


def _roundtrip(value):
    """What the file holds for `value`: the builder's own serialisation."""
    return json.loads(json.dumps(value, separators=(",", ":"), default=str))


def _stamped_since(built_at: str) -> set:
    from db_pool import get_db
    with get_db() as db:
        return {r[0] for r in db.run("select distinct ticker from price_history where updated_at > :t",
                                     t=built_at) or []}


def _ticker_of(key: str) -> str:
    # Sections are keyed by ticker, or "ticker|trigger date" for per-trigger maps.
    return str(key).split("|", 1)[0]


def compare_sections(snap: dict, summary: dict) -> int:
    import publish_report_summary
    if summary.get("generated_utc") != snap.get("generated_utc"):
        print(f"FAIL summary is for snapshot {summary.get('generated_utc')}, not {snap.get('generated_utc')}")
        return 1
    fresh = _roundtrip(publish_report_summary.build(snap)["sections"])
    filed = summary.get("sections") or {}
    moved = _stamped_since(summary["built_at"])
    print(f"snapshot {snap['generated_utc']}  built {summary['built_at']}  "
          f"instruments with prices changed since: {len(moved)}")
    defects = 0
    for name in sorted(set(fresh) | set(filed)):
        a, b = filed.get(name), fresh.get(name)
        if a is None or b is None:
            print(f"  {name:24} MISSING from the {'file' if a is None else 'rebuild'}")
            defects += 1
            continue
        if not isinstance(a, dict) or not isinstance(b, dict):
            ok = a == b
            print(f"  {name:24} {'equal' if ok else 'DEFECT (not a map, differs)'}")
            defects += 0 if ok else 1
            continue
        keys = set(a) | set(b)
        diff = sorted(k for k in keys if a.get(k, "<absent>") != b.get(k, "<absent>"))
        stale = [k for k in diff if _ticker_of(k) in moved]
        bad = [k for k in diff if _ticker_of(k) not in moved]
        print(f"  {name:24} {len(keys):6,} keys  equal {len(keys) - len(diff):6,}  "
              f"STALE {len(stale):4}  DEFECT {len(bad):4}")
        for k in bad[:5]:
            print(f"      DEFECT {k}: file={json.dumps(a.get(k, '<absent>'))[:160]}  "
                  f"database={json.dumps(b.get(k, '<absent>'))[:160]}")
        defects += len(bad)
    print("RESULT", "PASS" if not defects else f"FAIL ({defects} defects)")
    return 0 if not defects else 1


def write_records(snapshot_path: str, summary_path: str, out: str, use_file: bool, login: str) -> None:
    from hvf_web import server
    from hvf_web import web_users as wu
    server.SNAPSHOT = snapshot_path
    server.SUMMARY_FILE = summary_path
    server._SUMMARY_DISABLED = not use_file
    token = wu.token_for(login)
    if not token:
        raise SystemExit(f"unknown login {login}")
    with server.app.test_request_context("/api/records", headers={"X-Auth": token}):
        payload = server.app.make_response(server.api_records()).get_json()
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, indent=0)
    print(f"records {'FROM FILE' if use_file else 'FROM DATABASE'}: "
          f"{len(payload.get('records') or [])} records, generated_utc {payload.get('generated_utc')} -> {out}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--records-out")
    ap.add_argument("--no-file", action="store_true")
    ap.add_argument("--login", default="Alex")
    a = ap.parse_args()
    with open(a.snapshot, encoding="utf-8") as fh:
        snap = json.load(fh)
    if a.records_out:
        write_records(a.snapshot, a.summary, a.records_out, not a.no_file, a.login)
        return 0
    with open(a.summary, encoding="utf-8") as fh:
        summary = json.load(fh)
    return compare_sections(snap, summary)


if __name__ == "__main__":
    sys.exit(main())
