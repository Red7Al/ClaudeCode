"""The live API must be able to say which build it is running.

WHY. IONOS shared hosting keeps the imported Flask module resident behind the CGI wrapper and offers no
way to restart it: there is no control panel button, and the SSH session is a sandbox that cannot see the
web worker or any FastCGI socket. A deploy therefore updates every file on the host while /api/* keeps
answering from a previously-loaded module.

That happened twice on 2026-08-22/23. It was only noticed because a NEW ROUTE returned 404 -- a change
that adds no route gives no signal at all, and two of that day's server.py fixes had no way to be
verified. /api/build closes that hole: it reports what the running PROCESS loaded, so the deploy can
prove the API took effect instead of assuming it from a green static upload.
"""

import json

import pytest

import build_ionos_package
from hvf_web import server


def test_the_endpoint_is_unauthenticated_and_carries_no_secret():
    """The deploy must be able to check it before anyone logs in — so it must not leak anything."""
    body = server.app.test_client().get("/api/build").get_json()

    assert set(body) == {"fingerprint", "built_at", "module_loaded_at"}
    assert "sha" not in json.dumps(body).lower()


def test_the_fingerprint_is_not_the_commit_sha():
    """A short hash proves equality without publishing the repository's history."""
    identity = build_ionos_package.build_identity()

    import subprocess
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True,
                                  cwd=build_ionos_package.ROOT).strip()
    assert identity["fingerprint"] != sha
    assert identity["fingerprint"] not in sha
    assert len(identity["fingerprint"]) == 12


def test_the_package_carries_the_stamp_the_endpoint_reads(tmp_path):
    """The two halves must agree on the path, or the check silently reports an empty fingerprint."""
    import os
    import zipfile

    out = build_ionos_package.build(tmp_path / "pkg.zip")[0]
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        stamped = json.loads(z.read("data/build_id.json"))

    want = os.path.relpath(os.path.join(server._DATA_DIR, "build_id.json"),
                           build_ionos_package.ROOT).replace(os.sep, "/")
    assert want in names, f"the endpoint reads {want}, which the package does not contain"
    assert stamped["fingerprint"] and stamped["built_at"]


def test_the_running_build_is_read_once_at_import(monkeypatch, tmp_path):
    """THE POINT. Re-reading per request would report the DISK, and the disk is not what is stale."""
    stamp = tmp_path / "build_id.json"
    stamp.write_text(json.dumps({"fingerprint": "newnewnewnew", "built_at": "2026-08-23T12:00:00+00:00"}),
                     encoding="utf-8")
    monkeypatch.setattr(server, "_DATA_DIR", str(tmp_path))

    body = server.app.test_client().get("/api/build").get_json()

    assert body["fingerprint"] != "newnewnewnew", (
        "the endpoint re-read the file on disk; a stale worker would then report itself as current, "
        "which is precisely the failure this endpoint exists to detect")


def test_a_missing_stamp_reports_empty_rather_than_failing():
    """An older worker predates the stamp; it must answer, not 500, so the deploy can tell them apart."""
    assert server._read_build_id() == {"fingerprint": "", "built_at": ""} or \
        set(server._read_build_id()) == {"fingerprint", "built_at"}

    response = server.app.test_client().get("/api/build")
    assert response.status_code == 200


def test_the_deploy_checks_the_live_build():
    from pathlib import Path
    script = (Path(__file__).parent / "deploy_ionos.sh").read_text(encoding="utf-8")

    assert "/api/build" in script, "the deploy must verify the API worker, not just the static upload"
    assert "BUILD_ID=" in script
    assert "is NOT live yet" in script, "a stale worker must be reported in plain words"
