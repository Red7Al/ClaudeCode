"""Build, publish, pull or verify the current Scanner snapshot.

This is the external-worker entry point. It deliberately exits non-zero when
publication is not configured: a successful scan must never be mistaken for a
successful production publication.
"""

import argparse
import json
import os
from pathlib import Path

import scanner_snapshot_store as store


def _markets(value: str) -> list[str] | None:
    values = [part.strip() for part in (value or "").split(",") if part.strip()]
    return values or None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--build", action="store_true", help="build locally, publish and verify")
    action.add_argument("--publish", action="store_true", help="publish the existing local snapshot and verify")
    action.add_argument("--pull", action="store_true", help="replace the local cache with the verified current snapshot")
    action.add_argument("--verify", action="store_true", help="download and verify the current published snapshot")
    parser.add_argument("--markets", default="", help="comma-separated market names for a partial build")
    parser.add_argument("--snapshot", type=Path, default=store.DEFAULT_SNAPSHOT)
    parser.add_argument("--source", default=os.environ.get("GITHUB_WORKFLOW", "manual"))
    args = parser.parse_args()

    if args.build:
        markets = _markets(args.markets)
        if markets:
            # A fresh Actions checkout has no ignored boot file. Pull the published base before merging a subset.
            store.pull_current(args.snapshot, force=True)
        from hvf_web.build_snapshot import build
        snapshot = build(markets=markets)
        if not isinstance(snapshot, dict):
            raise store.SnapshotStoreError("snapshot build produced no candidate")
        meta = store.publish_snapshot(snapshot, source=args.source)
        verified = store.verify_current()
        if verified["sha256"] != meta["sha256"]:
            raise store.SnapshotStoreError("published snapshot verification selected a different version")
        # Reuse the completed scan to keep the Supabase-backed lifecycle history current without another
        # full 15-month universe replay. This advances OPEN/NEVER_TRIGGERED rows from price_history too.
        from squeeze_history import refresh_daily
        refresh_daily(snapshot)
        result = meta
    elif args.publish:
        result = store.publish_snapshot_file(args.snapshot, source=args.source)
        verified = store.verify_current()
        if verified["sha256"] != result["sha256"]:
            raise store.SnapshotStoreError("published snapshot verification selected a different version")
    elif args.pull:
        _snapshot, result, changed = store.pull_current(args.snapshot, force=True)
        result = {**result, "local_cache_changed": changed}
    else:
        result = store.verify_current()

    # Metadata only. Never print a credential or the Scanner records.
    print(json.dumps({key: result[key] for key in (
        "version_id", "generated_utc", "object_path", "sha256", "record_count", "byte_count"
    ) if key in result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
