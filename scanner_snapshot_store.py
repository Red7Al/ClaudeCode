"""Durable Supabase publication and read-through caching for the Scanner snapshot.

The scan is built by a worker and published as an immutable object in a private
Supabase Storage bucket. Postgres contains only version metadata and the atomic
current-version pointer. The web host retains one verified local copy so a
Supabase outage does not blank the Scanner.
"""

from __future__ import annotations

import gzip
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
    """create table if not exists scanner_refresh_progress (
           refresh_id text primary key,
           status text not null default 'queued',
           stage text not null default 'queued',
           done integer not null default 0 check (done >= 0),
           total integer not null default 0 check (total >= 0),
           markets text,
           worker text,
           queued_for timestamptz,
           requested_at timestamptz not null default now(),
           started_at timestamptz,
           updated_at timestamptz not null default now(),
           completed_at timestamptz,
           generated_utc text,
           error text)""",
    "create index if not exists idx_scanner_refresh_updated on scanner_refresh_progress(updated_at desc)",
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


def _object_path(generated: str, digest: str, compressed: bool = True) -> str:
    stamp = datetime.fromisoformat(generated.replace("Z", "+00:00")).astimezone(timezone.utc)
    ext = ".json.gz" if compressed else ".json"
    return f"snapshots/{stamp:%Y/%m/%d}/{stamp:%Y%m%dT%H%M%SZ}-{digest[:16]}{ext}"


# Gzip magic number. Objects published before 2026-08-17 are plain JSON and must keep loading, so the
# reader sniffs these two bytes rather than trusting the file extension or a stored flag.
_GZIP_MAGIC = b"\x1f\x8b"


def _compress(data: bytes) -> bytes:
    """Gzip the encoded snapshot for storage.

    Why (2026-08-17): the encoded snapshot is ~816 KB of highly repetitive JSON and compresses to
    ~77 KB, a 10.6x cut (measured on the 2026-08-12 snapshot, 1,421 records). Supabase's free tier
    allows 5 GB of egress a month and Storage began returning HTTP 402 once that ran out, which froze
    the live Scanner on 12 August data. Uncompressed that budget is ~6,580 downloads; compressed it is
    ~69,900. Note the on-DISK hvf_web/snapshot.json is larger again (~1.29 MB) because it is written
    with default separators -- _encoded re-serialises compactly, so the published payload is smaller
    than the file before compression even starts.

    mtime=0 makes the output deterministic. Publication relies on immutable, content-addressed object
    names so that retrying the exact same publish is safe (it tolerates the 409 "already exists"), and
    that promise only holds if identical input produces identical bytes -- the gzip default stamps the
    current time into the header and would break it.
    """
    return gzip.compress(data, compresslevel=9, mtime=0)


def _decompress(data: bytes) -> bytes:
    """Inverse of _compress, transparently passing through pre-2026-08-17 uncompressed objects."""
    if data[:2] != _GZIP_MAGIC:
        return data
    try:
        return gzip.decompress(data)
    except (OSError, EOFError) as exc:
        raise SnapshotStoreError("stored snapshot is not readable gzip") from exc


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


def _refresh_id(value: str) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", value):
        raise SnapshotStoreError("Scanner refresh id is invalid")
    return value


def queue_refresh_progress(refresh_id: str, markets=None, worker: str = "", queued_for=None) -> bool:
    """Persist the browser request before the external worker starts; failure never blocks dispatch."""
    try:
        refresh_id = _refresh_id(refresh_id)
        ensure_schema()
        db = _db()
        try:
            db.run(
                """insert into scanner_refresh_progress
                       (refresh_id,status,stage,done,total,markets,worker,queued_for,requested_at,updated_at)
                     values (:r,'queued','queued',0,0,:m,:w,cast(:q as timestamptz),now(),now())
                     on conflict (refresh_id) do update set
                       status='queued',stage='queued',done=0,total=0,markets=excluded.markets,
                       worker=excluded.worker,queued_for=excluded.queued_for,requested_at=now(),
                       started_at=null,updated_at=now(),completed_at=null,generated_utc=null,error=null
                     where scanner_refresh_progress.status='queued'""",
                r=refresh_id, m=",".join(markets or []), w=(worker or "")[:100], q=queued_for,
            )
            return True
        finally:
            db.close()
    except Exception as exc:
        log.warning("could not record queued Scanner refresh %s: %s", refresh_id, exc)
        return False


def get_refresh_progress(refresh_id: str | None = None) -> dict | None:
    """Read one refresh, or the most recent active refresh when no id is supplied."""
    try:
        db = _db()
        try:
            if refresh_id:
                rows = db.run(
                    """select refresh_id,status,stage,done,total,worker,queued_for,requested_at,
                              started_at,updated_at,completed_at,generated_utc,error
                         from scanner_refresh_progress where refresh_id=:r""",
                    r=_refresh_id(refresh_id),
                ) or []
            else:
                rows = db.run(
                    """select refresh_id,status,stage,done,total,worker,queued_for,requested_at,
                              started_at,updated_at,completed_at,generated_utc,error
                         from scanner_refresh_progress
                        where status in ('queued','running','publishing','history')
                          and updated_at > now() - interval '2 hours'
                        order by requested_at desc limit 1"""
                ) or []
        finally:
            db.close()
        if not rows:
            return None
        row = rows[0]
        iso = lambda value: value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)
        return {
            "refresh_id": row[0], "status": row[1], "stage": row[2],
            "done": int(row[3] or 0), "total": int(row[4] or 0), "worker": row[5] or "",
            "queued_for": iso(row[6]), "requested_at": iso(row[7]), "started_at": iso(row[8]),
            "updated_at": iso(row[9]), "completed_at": iso(row[10]),
            "generated_utc": row[11], "error": row[12],
        }
    except Exception as exc:
        log.warning("could not read Scanner refresh progress: %s", exc)
        return None


class RefreshProgressReporter:
    """Throttled progress writer used by the external Scanner worker."""

    def __init__(self, refresh_id: str | None):
        self.refresh_id = None
        self.db = None
        self.last_done = -1
        self.last_write = 0.0
        if not refresh_id:
            return
        try:
            self.refresh_id = _refresh_id(refresh_id)
            ensure_schema()
            self.db = _db()
        except Exception as exc:
            log.warning("Scanner progress reporting unavailable: %s", exc)
            self.db = None

    def _run(self, sql: str, **params) -> bool:
        if not self.db or not self.refresh_id:
            return False
        for attempt in range(2):
            try:
                self.db.run(sql, r=self.refresh_id, **params)
                return True
            except Exception as exc:
                if attempt == 0:
                    try:
                        self.db.close()
                    except Exception:
                        pass
                    try:
                        self.db = _db()
                        continue
                    except Exception:
                        self.db = None
                log.warning("Scanner progress update failed: %s", exc)
        return False

    def start(self) -> None:
        self._run(
            """insert into scanner_refresh_progress
                   (refresh_id,status,stage,done,total,requested_at,started_at,updated_at)
                 values (:r,'running','scanning',0,0,now(),now(),now())
                 on conflict (refresh_id) do update set status='running',stage='scanning',
                   started_at=coalesce(scanner_refresh_progress.started_at,now()),updated_at=now(),error=null"""
        )

    def update(self, done: int, total: int) -> None:
        done, total = max(0, int(done)), max(0, int(total))
        now = time.monotonic()
        if done < total and done - self.last_done < 5 and now - self.last_write < 10:
            return
        if self._run(
            """update scanner_refresh_progress set status='running',stage='scanning',done=:d,total=:t,
                   updated_at=now() where refresh_id=:r""",
            d=done, t=total,
        ):
            self.last_done, self.last_write = done, now

    def stage(self, stage: str, done: int | None = None, total: int | None = None) -> None:
        values = {"s": stage[:30], "d": max(0, int(done or 0)), "t": max(0, int(total or 0))}
        self._run(
            """update scanner_refresh_progress set status=:s,stage=:s,
                   done=case when :d > 0 then :d else done end,
                   total=case when :t > 0 then :t else total end,updated_at=now()
                 where refresh_id=:r""",
            **values,
        )

    def complete(self, generated_utc: str | None = None) -> None:
        self._run(
            """update scanner_refresh_progress set status='completed',stage='completed',
                   done=case when total > 0 then total else done end,generated_utc=:g,
                   updated_at=now(),completed_at=now(),error=null where refresh_id=:r""",
            g=generated_utc,
        )

    def fail(self, error: Exception | str) -> None:
        self._run(
            """update scanner_refresh_progress set status='failed',stage='failed',error=:e,
                   updated_at=now(),completed_at=now() where refresh_id=:r""",
            e=str(error)[:500],
        )

    def close(self) -> None:
        if self.db:
            try:
                self.db.close()
            except Exception:
                pass
            self.db = None


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
    # sha256 and byte_count describe the RAW JSON, not the stored bytes, and must keep doing so:
    # _matches_digest hashes the web host's UNCOMPRESSED snapshot.json against this value to decide
    # whether a download is needed at all, and validate_snapshot's byte_count is the snapshot's real
    # size. Only the transfer is compressed; the identity of the thing is unchanged.
    body = _compress(data)
    object_path = _object_path(generated, digest)
    bucket = ensure_private_bucket()
    url = f"{_project_url()}/storage/v1/object/{quote(bucket, safe='')}/{quote(object_path, safe='/')}"
    # application/gzip, not application/json with Content-Encoding: gzip -- a proxy or CDN that helpfully
    # decompresses on the way out would silently undo the saving and break the digest check. Opaque
    # bytes we decompress ourselves are predictable.
    headers = _headers("publish", "application/gzip")
    headers.update({"x-upsert": "false", "cache-control": "3600"})
    log.info("publishing snapshot: %d bytes raw -> %d gzipped (%.1fx)",
             len(data), len(body), (len(data) / len(body)) if body else 1.0)
    response = _request("POST", url, headers=headers, data=body, timeout=90)
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


def download_snapshot(meta: dict | None = None, purpose: str = "read") -> tuple[dict, dict, bytes]:
    meta = meta or current_metadata()
    if not meta:
        raise SnapshotStoreError("no Scanner snapshot has been published")
    bucket = _bucket()
    path = str(meta["object_path"])
    url = f"{_project_url()}/storage/v1/object/authenticated/{quote(bucket, safe='')}/{quote(path, safe='/')}"
    response = _request("GET", url, headers=_headers(purpose), timeout=90)
    if response.status_code != 200:
        raise SnapshotStoreError(f"snapshot download failed ({response.status_code})")
    # Objects published from 2026-08-17 are gzipped; older ones are plain JSON and pass straight through.
    # Everything below this line works on the raw JSON, so the digest still verifies what was published.
    data = _decompress(response.content)
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


def _matches_digest(path: Path, expected: str | None) -> bool:
    if not expected:
        return False
    try:
        return _digest(path.read_bytes()) == expected
    except OSError:
        return False


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


def pull_current(path: str | os.PathLike = DEFAULT_SNAPSHOT, force: bool = False,
                 purpose: str = "read") -> tuple[dict, dict, bool]:
    """Synchronise the verified current object to ``path``; return snapshot, metadata, changed."""
    local_path = Path(path)
    sidecar_path = _sidecar_path(local_path)
    sidecar = _read_json(sidecar_path) or {}
    now = time.time()
    if (not force and local_path.is_file()
            and now - float(sidecar.get("checked_epoch", 0)) < CHECK_SECONDS
            and _matches_digest(local_path, sidecar.get("sha256"))):
        local = _read_json(local_path)
        if isinstance(local, dict):
            validate_snapshot(local)
            return local, sidecar, False

    meta = current_metadata()
    if not meta:
        raise SnapshotStoreError("no Scanner snapshot has been published")
    local = _read_json(local_path)
    if (isinstance(local, dict) and sidecar.get("object_path") == meta["object_path"]
            and sidecar.get("sha256") == meta["sha256"]
            and _matches_digest(local_path, meta["sha256"])):
        validate_snapshot(local)
        sidecar = {**meta, "checked_epoch": now}
        _write_atomic(sidecar_path, json.dumps(sidecar, separators=(",", ":")).encode("utf-8"))
        return local, meta, False

    snapshot, meta, data = download_snapshot(meta, purpose=purpose)
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
    # Publication workers intentionally receive only the write-scoped Scanner
    # secret. Reuse that credential for the immediate read-back verification;
    # normal web/cache reads continue to require the separate web key.
    snapshot, meta, _data = download_snapshot(purpose="publish")
    validate_snapshot(snapshot)
    return meta
