"""Durable Supabase publication and read-through caching for the Scanner snapshot.

The scan is built by a worker and published as an immutable object in a private
Supabase Storage bucket. Postgres contains only version metadata and the atomic
current-version pointer. The web host retains one verified local copy so a
Supabase outage does not blank the Scanner.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests


log = logging.getLogger("scanner_snapshot_store")

ROOT = Path(__file__).resolve().parent
DEFAULT_SNAPSHOT = ROOT / "hvf_web" / "snapshot.json"
DEFAULT_BUCKET = "scanner-artifacts"
CHECK_SECONDS = 60
_schema_ready = False

_SCHEMA = (
    """create table if not exists scanner_snapshot_versions (
           id bigserial primary key,
           generated_utc timestamptz not null,
           object_path text not null unique,
           sha256 text not null,
           record_count integer not null check (record_count >= 0),
           byte_count bigint not null check (byte_count > 0),
           schema_version integer not null default 1,
           source text not null default 'unknown',
           published_at timestamptz not null default now())""",
    "create index if not exists idx_scanner_snapshot_generated on scanner_snapshot_versions(generated_utc desc)",
    """create table if not exists scanner_snapshot_current (
           singleton boolean primary key default true check (singleton),
           version_id bigint not null references scanner_snapshot_versions(id),
           updated_at timestamptz not null default now())""",
)


class SnapshotStoreError(RuntimeError):
    """The remote snapshot could not be safely published or loaded."""


def _db():
    from db_pool import get_db
    return get_db()


def _bucket() -> str:
    value = os.environ.get("SUPABASE_SNAPSHOT_BUCKET", DEFAULT_BUCKET).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value):
        raise SnapshotStoreError("SUPABASE_SNAPSHOT_BUCKET is invalid")
    return value


def _project_url() -> str:
    explicit = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    if explicit:
        if not explicit.startswith("https://"):
            raise SnapshotStoreError("SUPABASE_URL must use https")
        return explicit
    user = os.environ.get("SUPABASE_USER", "").strip()
    match = re.fullmatch(r"postgres\.([a-z0-9-]+)", user)
    if match:
        return f"https://{match.group(1)}.supabase.co"
    raise SnapshotStoreError("SUPABASE_URL is missing and cannot be derived from SUPABASE_USER")


def _secret_key(purpose: str) -> str:
    names = (
        ("SUPABASE_SCANNER_PUBLISH_KEY", "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY")
        if purpose == "publish"
        else ("SUPABASE_SCANNER_WEB_KEY", "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY")
    )
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise SnapshotStoreError(f"no server-side Supabase Storage key configured for {purpose}")


def _headers(purpose: str, content_type: str | None = None) -> dict:
    key = _secret_key(purpose)
    headers = {"apikey": key}
    # New sb_secret_* keys are sent only as apikey. Legacy service_role JWTs also
    # need the bearer header for the Storage service's authenticated-object path.
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def storage_configured(purpose: str = "read") -> bool:
    try:
        _project_url()
        _secret_key(purpose)
        return True
    except SnapshotStoreError:
        return False


def validate_snapshot(snapshot: dict) -> tuple[str, int]:
    if not isinstance(snapshot, dict):
        raise SnapshotStoreError("snapshot must be a JSON object")
    generated = snapshot.get("generated_utc")
    records = snapshot.get("records")
    if not isinstance(generated, str) or not generated.strip():
        raise SnapshotStoreError("snapshot generated_utc is missing")
    try:
        datetime.fromisoformat(generated.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotStoreError("snapshot generated_utc is invalid") from exc
    if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
        raise SnapshotStoreError("snapshot records must be a list of objects")
    count = snapshot.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count != len(records):
        raise SnapshotStoreError("snapshot count does not match records")
    if records and not all(isinstance(row.get("ticker"), str) and row.get("ticker") for row in records):
        raise SnapshotStoreError("every snapshot record must have a ticker")
    return generated, count


def _encoded(snapshot: dict) -> bytes:
    validate_snapshot(snapshot)
    return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object_path(generated: str, digest: str) -> str:
    stamp = datetime.fromisoformat(generated.replace("Z", "+00:00")).astimezone(timezone.utc)
    return f"snapshots/{stamp:%Y/%m/%d}/{stamp:%Y%m%dT%H%M%SZ}-{digest[:16]}.json"


def _request(method: str, url: str, **kwargs):
    response = requests.request(method, url, timeout=kwargs.pop("timeout", 60), **kwargs)
    return response


def _bucket_is_missing(response) -> bool:
    """Supabase Storage wraps NoSuchBucket as HTTP 400 with an inner 404 code."""
    if response.status_code == 404:
        return True
    if response.status_code != 400:
        return False
    try:
        body = response.json() if response.content else {}
    except (TypeError, ValueError):
        body = {}
    code = str(body.get("statusCode") or body.get("status") or "")
    detail = " ".join(str(body.get(key) or "") for key in ("error", "message")).lower()
    return code == "404" and "bucket" in detail and "not found" in detail


def ensure_private_bucket() -> str:
    """Create the private bucket when absent; never changes an existing bucket."""
    bucket = _bucket()
    base = f"{_project_url()}/storage/v1"
    response = _request("GET", f"{base}/bucket/{quote(bucket, safe='')}", headers=_headers("publish"), timeout=20)
    if response.status_code == 200:
        body = response.json() if response.content else {}
        if body.get("public") is True:
            raise SnapshotStoreError(f"Storage bucket {bucket!r} exists but is public")
        return bucket
    if not _bucket_is_missing(response):
        raise SnapshotStoreError(f"could not inspect Storage bucket ({response.status_code})")
    response = _request(
        "POST", f"{base}/bucket", headers=_headers("publish", "application/json"),
        json={"id": bucket, "name": bucket, "public": False,
              "file_size_limit": 6 * 1024 * 1024, "allowed_mime_types": ["application/json"]}, timeout=20,
    )
    if response.status_code not in (200, 201):
        raise SnapshotStoreError(f"could not create private Storage bucket ({response.status_code})")
    return bucket


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    db = _db()
    try:
        for statement in _SCHEMA:
            db.run(statement)
        _schema_ready = True
    finally:
        db.close()


def _record_publication(meta: dict) -> int:
    ensure_schema()
    db = _db()
    in_transaction = False
    try:
        db.run("begin")
        in_transaction = True
        rows = db.run(
            """insert into scanner_snapshot_versions
                 (generated_utc, object_path, sha256, record_count, byte_count, schema_version, source)
               values (cast(:g as timestamptz), :p, :h, :n, :b, :v, :s)
               on conflict (object_path) do update set
                 sha256=excluded.sha256, record_count=excluded.record_count, byte_count=excluded.byte_count,
                 schema_version=excluded.schema_version, source=excluded.source
               returning id""",
            g=meta["generated_utc"], p=meta["object_path"], h=meta["sha256"],
            n=meta["record_count"], b=meta["byte_count"], v=meta["schema_version"], s=meta["source"],
        ) or []
        if not rows:
            raise SnapshotStoreError("snapshot metadata insert returned no id")
        version_id = int(rows[0][0])
        db.run(
            """insert into scanner_snapshot_current (singleton, version_id, updated_at)
               values (true, :i, now())
               on conflict (singleton) do update set version_id=excluded.version_id, updated_at=excluded.updated_at""",
            i=version_id,
        )
        db.run("commit")
        in_transaction = False
        return version_id
    except Exception:
        if in_transaction:
            try:
                db.run("rollback")
            except Exception:
                pass
        raise
    finally:
        db.close()


def publish_snapshot(snapshot: dict, source: str = "manual") -> dict:
    generated, count = validate_snapshot(snapshot)
    data = _encoded(snapshot)
    digest = _digest(data)
    object_path = _object_path(generated, digest)
    bucket = ensure_private_bucket()
    url = f"{_project_url()}/storage/v1/object/{quote(bucket, safe='')}/{quote(object_path, safe='/')}"
    headers = _headers("publish", "application/json")
    headers.update({"x-upsert": "false", "cache-control": "3600"})
    response = _request("POST", url, headers=headers, data=data, timeout=90)
    if response.status_code not in (200, 201):
        # Immutable names make retrying the exact same publication safe.
        detail = (response.text or "").lower()
        if response.status_code not in (400, 409) or "exist" not in detail:
            raise SnapshotStoreError(f"snapshot upload failed ({response.status_code})")
    meta = {
        "generated_utc": generated,
        "object_path": object_path,
        "sha256": digest,
        "record_count": count,
        "byte_count": len(data),
        "schema_version": 1,
        "source": (source or "unknown")[:100],
    }
    meta["version_id"] = _record_publication(meta)
    return meta


def publish_snapshot_file(path: str | os.PathLike = DEFAULT_SNAPSHOT, source: str = "manual") -> dict:
    with Path(path).open(encoding="utf-8") as handle:
        return publish_snapshot(json.load(handle), source=source)


def current_metadata() -> dict | None:
    db = _db()
    try:
        rows = db.run(
            """select v.id, v.generated_utc, v.object_path, v.sha256, v.record_count,
                      v.byte_count, v.schema_version, v.source, v.published_at
               from scanner_snapshot_current c
               join scanner_snapshot_versions v on v.id=c.version_id
               where c.singleton=true"""
        ) or []
    finally:
        db.close()
    if not rows:
        return None
    row = rows[0]
    return {
        "version_id": int(row[0]), "generated_utc": row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1]),
        "object_path": row[2], "sha256": row[3], "record_count": int(row[4]),
        "byte_count": int(row[5]), "schema_version": int(row[6]), "source": row[7],
        "published_at": row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8]),
    }


def download_snapshot(meta: dict | None = None) -> tuple[dict, dict, bytes]:
    meta = meta or current_metadata()
    if not meta:
        raise SnapshotStoreError("no Scanner snapshot has been published")
    bucket = _bucket()
    path = str(meta["object_path"])
    url = f"{_project_url()}/storage/v1/object/authenticated/{quote(bucket, safe='')}/{quote(path, safe='/')}"
    response = _request("GET", url, headers=_headers("read"), timeout=90)
    if response.status_code != 200:
        raise SnapshotStoreError(f"snapshot download failed ({response.status_code})")
    data = response.content
    if _digest(data) != meta["sha256"]:
        raise SnapshotStoreError("downloaded snapshot checksum does not match metadata")
    try:
        snapshot = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotStoreError("downloaded snapshot is not valid UTF-8 JSON") from exc
    generated, count = validate_snapshot(snapshot)
    if count != meta["record_count"] or generated != meta["generated_utc"]:
        raise SnapshotStoreError("downloaded snapshot does not match published metadata")
    return snapshot, meta, data


def _sidecar_path(local_path: Path) -> Path:
    return local_path.with_name(local_path.name + ".supabase.json")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def pull_current(path: str | os.PathLike = DEFAULT_SNAPSHOT, force: bool = False) -> tuple[dict, dict, bool]:
    """Synchronise the verified current object to ``path``; return snapshot, metadata, changed."""
    local_path = Path(path)
    sidecar_path = _sidecar_path(local_path)
    sidecar = _read_json(sidecar_path) or {}
    now = time.time()
    if not force and local_path.is_file() and now - float(sidecar.get("checked_epoch", 0)) < CHECK_SECONDS:
        local = _read_json(local_path)
        if isinstance(local, dict):
            validate_snapshot(local)
            return local, sidecar, False

    meta = current_metadata()
    if not meta:
        raise SnapshotStoreError("no Scanner snapshot has been published")
    local = _read_json(local_path)
    if (isinstance(local, dict) and sidecar.get("object_path") == meta["object_path"]
            and sidecar.get("sha256") == meta["sha256"]):
        validate_snapshot(local)
        sidecar = {**meta, "checked_epoch": now}
        _write_atomic(sidecar_path, json.dumps(sidecar, separators=(",", ":")).encode("utf-8"))
        return local, meta, False

    snapshot, meta, data = download_snapshot(meta)
    _write_atomic(local_path, data)
    sidecar = {**meta, "checked_epoch": now}
    _write_atomic(sidecar_path, json.dumps(sidecar, separators=(",", ":")).encode("utf-8"))
    return snapshot, meta, True


def load_snapshot(path: str | os.PathLike = DEFAULT_SNAPSHOT) -> dict:
    """Remote-first when configured, always falling back to the last verified local file."""
    local_path = Path(path)
    if storage_configured("read"):
        try:
            snapshot, _meta, changed = pull_current(local_path)
            if changed:
                log.info("Scanner snapshot cache advanced from Supabase")
            return snapshot
        except Exception as exc:
            log.warning("Supabase Scanner snapshot unavailable; using local last-known-good copy: %s", exc)
    local = _read_json(local_path)
    if isinstance(local, dict):
        try:
            validate_snapshot(local)
            return local
        except SnapshotStoreError as exc:
            log.error("local Scanner snapshot is invalid: %s", exc)
    return {"generated_utc": None, "count": 0, "records": []}


def verify_current() -> dict:
    snapshot, meta, _data = download_snapshot()
    validate_snapshot(snapshot)
    return meta
