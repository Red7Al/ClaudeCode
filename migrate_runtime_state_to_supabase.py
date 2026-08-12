"""Non-destructively seed small durable runtime stores into Supabase.

Local files are retained as compatibility/cold-backup copies. The command never prints payload contents,
password hashes, ciphertext or personal details. A differing remote document is treated as a conflict unless
--overwrite is explicitly supplied.
"""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import web_store

ROOT = Path(__file__).resolve().parent


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _version_history():
    try:
        output = subprocess.check_output(
            ["git", "-C", str(ROOT), "log", "--date=short", "--pretty=format:%ad|%h|%s"],
            text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        entries = []
        for line in output.splitlines():
            if not line.strip():
                continue
            date, version, summary = line.split("|", 2)
            if date > "2026-06-04":
                entries.append({"date": date, "version": version, "summary": summary.strip()})
        return {"entries": entries}
    except Exception:
        return _read_json(ROOT / "data" / "version_history.json")


def sources() -> dict:
    return {
        "web_users": lambda: _read_json(ROOT / "data" / "web_users.json"),
        "name_cache": lambda: _read_json(ROOT / "hvf_web" / "name_cache.json"),
        "sector_cache": lambda: _read_json(ROOT / "sector_cache.json"),
        "fundamentals_overrides": lambda: _read_json(ROOT / "data" / "fundamentals_overrides.json"),
        "version_history": _version_history,
    }


def _fingerprint(payload) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _count(payload) -> int:
    if isinstance(payload, dict) and set(payload) == {"entries"} and isinstance(payload["entries"], list):
        return len(payload["entries"])
    return len(payload) if isinstance(payload, (dict, list)) else 0


def migrate(apply: bool = False, overwrite: bool = False) -> tuple:
    results, conflicts = [], 0
    for key, loader in sources().items():
        try:
            local = loader()
        except (OSError, json.JSONDecodeError) as exc:
            results.append((key, "missing", 0, str(exc)))
            continue
        if not isinstance(local, (dict, list)):
            results.append((key, "invalid", 0, "not a JSON object/list"))
            continue
        remote = web_store.load_json_store(key)
        same = remote is not None and _fingerprint(remote) == _fingerprint(local)
        if same:
            results.append((key, "verified", _count(local), ""))
            continue
        if remote is not None and not overwrite:
            conflicts += 1
            results.append((key, "conflict", _count(local), "remote differs; preserved"))
            continue
        if not apply:
            results.append((key, "ready", _count(local), ""))
            continue
        if not web_store.save_json_store(key, local):
            results.append((key, "failed", _count(local), "Supabase write failed"))
            continue
        verified = web_store.load_json_store(key)
        ok = verified is not None and _fingerprint(verified) == _fingerprint(local)
        results.append((key, "migrated" if ok else "failed-verification", _count(local), ""))
    return results, conflicts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write missing stores, then verify")
    parser.add_argument("--overwrite", action="store_true", help="replace a differing remote store")
    args = parser.parse_args()
    results, conflicts = migrate(args.apply, args.overwrite)
    for key, status, count, note in results:
        print(f"{key}: {status} ({count} top-level records){' - ' + note if note else ''}")
    if not args.apply:
        print("Audit only; rerun with --apply to migrate. Local originals are always retained.")
    print("IONOS must receive WEB_USERS_FERNET_KEY as an environment secret; its value is never stored here.")
    return 2 if conflicts else (1 if any(r[1].startswith("failed") for r in results) else 0)


if __name__ == "__main__":
    raise SystemExit(main())
