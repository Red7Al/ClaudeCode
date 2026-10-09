#!/usr/bin/env python3
"""Build the Scanner Report's summary file and install it on IONOS -- the website then reads THIS FILE, not
the database (owner, repeatedly; 2026-10-09: "The read should be once or twice per day as the summary files
are created on IONOS").

    python publish_report_summary.py [--snapshot hvf_web/snapshot.json] [--no-upload]

Runs in GitHub Actions after each snapshot publish and after the winners precompute (which fills the
trigger-date store this reads). It calls the web tier's OWN helpers with the database, so the file holds
exactly what each request used to compute -- one definition, computed twice a day instead of per request.

MEASURED before (2026-10-09, one cold web process, live snapshot): 61 queries / 12,726 rows for the page's
five boot calls and 4 queries / 63,142 rows for the cache warmer.

The file is keyed on the snapshot's generated_utc. The web tier uses a section only when that matches the
snapshot it is serving, so a stale file can never be mistaken for current: it falls back to the database.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
SUMMARY_NAME = "report_summary.json"


def build(snap: dict) -> dict:
    """Every section the web tier reads from the file, built by the web tier's own helpers."""
    from hvf_web import server
    server._SUMMARY_DISABLED = True          # build from the database, never from an older file
    sections = {
        "open_setups": server._open_trigger_setups(snap),
        "stored_metrics": server._stored_metrics(snap),
        "live_instrument_metrics": server._live_instrument_metrics(snap),
        "mcap": server._mcap_map(),
        "wk52": server._snapshot_52wk(snap),
        "trigdates": server._snapshot_trigger_dates(snap),
        "rvol": server._snapshot_rvol(snap),
        "volscore": server._snapshot_volscore(snap),
        "vwap_atr": server._live_vwap_atr(snap),
    }
    if sections["open_setups"] is None:
        raise RuntimeError("open trigger setups could not be read -- refusing to publish a summary without them")
    return {"generated_utc": snap.get("generated_utc"),
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sections": sections}


def _upload(local_path: str, generated_utc: str) -> None:
    """Atomic install over the SSH route the snapshot fallback already uses (scp/SFTP can chroot differently
    on IONOS shared hosting). Validated on the host before the rename."""
    host, user, rdir = os.environ["IONOS_HOST"], os.environ["IONOS_USER"], os.environ["IONOS_DIR"]
    port = os.environ.get("IONOS_PORT", "22")
    key = os.environ.get("IONOS_KEY_FILE")
    if not key:
        fd, key = tempfile.mkstemp(prefix="ionos_summary_key_")
        with os.fdopen(fd, "w") as fh:
            fh.write(os.environ["IONOS_SSH_KEY"].rstrip("\n") + "\n")
        os.chmod(key, 0o600)
    ssh = ["ssh", "-i", key, "-p", port, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
           f"{user}@{host}"]
    target = f"{rdir}/hvf_web/{SUMMARY_NAME}"
    tmp = f"{target}.tmp-{int(time.time())}"
    with open(local_path, "rb") as fh:
        subprocess.run(ssh + [f"umask 022; cat > '{tmp}'"], stdin=fh, check=True)
    check = (f"set -eu; test -s '{tmp}'; python3 -c \"import json,sys; d=json.load(open('{tmp}', encoding='utf-8')); "
             f"sys.exit(0 if d.get('generated_utc') == '{generated_utc}' else 3)\"; mv -f '{tmp}' '{target}'; "
             f"chmod 644 '{target}'")
    subprocess.run(ssh + [check], check=True)
    print(f"installed {target} for snapshot {generated_utc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default=os.path.join(ROOT, "hvf_web", "snapshot.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "hvf_web", SUMMARY_NAME))
    ap.add_argument("--no-upload", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, ROOT)
    with open(a.snapshot, encoding="utf-8") as fh:
        snap = json.load(fh)
    started = time.time()
    summary = build(snap)
    body = json.dumps(summary, separators=(",", ":"), default=str)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(body)
    sizes = {k: (len(v) if isinstance(v, dict) else None) for k, v in summary["sections"].items()}
    print(f"summary for {summary['generated_utc']}: {len(body):,} bytes in {time.time() - started:.1f}s; "
          f"entries per section {sizes}")
    if not a.no_upload:
        _upload(a.out, summary["generated_utc"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
