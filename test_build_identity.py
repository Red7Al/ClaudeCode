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


# ------------------------------------------------------------------------------------------------------
# The extracted client script must ship, to BOTH serving paths (2026-08-23).
#
# index.html loads app.js RELATIVELY. Apache serves the domain root copy and never reaches Flask, while
# Flask serves hvf_web/. If only one copy shipped, one of those paths would deliver a page whose script
# 404s — the markup renders and then nothing works, with no error a user could act on. That is the single
# way this extraction could break the live site, so it is pinned here.
# ------------------------------------------------------------------------------------------------------

def test_the_extracted_script_ships_to_both_serving_paths(tmp_path):
    import hashlib
    import zipfile

    out = build_ionos_package.build(tmp_path / "pkg.zip")[0]
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        for required in ("index.html", "app.js", "best_settings.js",
                         "hvf_web/index.html", "hvf_web/app.js", "hvf_web/best_settings.js"):
            assert required in names, f"{required} is missing from the package"
        for root, nested in (("index.html", "hvf_web/index.html"), ("app.js", "hvf_web/app.js"),
                             ("best_settings.js", "hvf_web/best_settings.js")):
            assert hashlib.sha256(z.read(root)).hexdigest() == hashlib.sha256(z.read(nested)).hexdigest(), (
                f"{root} and {nested} differ; the two serving paths would run different code")
        html = z.read("index.html").decode("utf-8")
        # VERSIONED, not bare (2026-09-05). A bare src let the browser keep a stale copy: a cached
        # best_settings.js that defined none of its functions was found live, which killed app.js with
        # "ReferenceError: makeCombReplay is not defined" and hung the Scanner on "Data loading...".
        # The build fingerprint in the URL makes a deploy a new URL, so the browser must refetch --
        # which matters more here than usual, because shared hosting gives us no cache-header control.
        import re as _re
        m = _re.search(r'<script src="app\.js\?v=([0-9a-f]{6,})"></script>', html)
        assert m, f"app.js must ship with a build-stamped URL: {html[html.find('<script'):][:120]}"
        assert f'<script src="best_settings.js?v={m.group(1)}"></script>' in html, (
            "both scripts must carry the SAME stamp, or one can go stale against the other")
        # Loaded FIRST: app.js builds its wallet replay from makeCombReplay at load time.
        # No closing quote in these needles: the URLs now carry ?v=<stamp> after the filename.
        assert html.index('src="best_settings.js') < html.index('src="app.js')
        assert "<script>" not in html, "an inline script block came back"


def test_only_the_client_script_ships_as_javascript(tmp_path):
    """docs/ build scripts and node_modules must stay out; exactly one .js file is production code."""
    import zipfile

    out = build_ionos_package.build(tmp_path / "pkg.zip")[0]
    with zipfile.ZipFile(out) as z:
        js = sorted(n for n in z.namelist() if n.endswith(".js"))

    assert js == ["app.js", "best_settings.js", "hvf_web/app.js", "hvf_web/best_settings.js"], \
        f"unexpected JavaScript in the package: {js}"


def test_flask_can_serve_the_script_itself():
    """Apache serves it in production, but the Flask route is what local runs and any other host use."""
    from hvf_web import server

    response = server.app.test_client().get("/app.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["Content-Type"]
    assert b"const _combReplay=makeCombReplay(" in response.data
    assert "no-store" in response.headers.get("Cache-Control", ""), (
        "a browser holding yesterday's app.js against today's index.html fails silently")


def test_flask_can_serve_the_best_settings_module():
    """app.js builds its replay from this file at load time, so a 404 here is a page that never starts."""
    from hvf_web import server

    response = server.app.test_client().get("/best_settings.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["Content-Type"]
    assert b"function makeCombReplay(env)" in response.data
    assert b"function computeBestSettings(env)" in response.data
    assert "no-store" in response.headers.get("Cache-Control", "")


def test_the_page_and_script_are_revalidated_by_apache():
    from pathlib import Path
    htaccess = (Path(__file__).parent / ".htaccess").read_text(encoding="utf-8")

    assert 'Files "app.js"' in htaccess
    assert 'Files "best_settings.js"' in htaccess
    assert "must-revalidate" in htaccess


# ======================================================================================================
# The Introduction's example image must actually reach the host (user 2026-08-30).
#
# The markup was repointed at a static /intro_card.png while include_path() still ended with
# `return suffix in {".py", ".html", ".sql"}` -- so the PNG would have been silently left out of the
# package and the page would have shown the "could not be loaded" fallback instead of the picture.
# Caught before shipping by listing the archive; pinned here so a later packager change cannot undo it.
# ======================================================================================================

def test_the_intro_card_image_is_packaged():
    from pathlib import Path
    import build_ionos_package as pkg

    assert pkg.include_path(Path("hvf_web/intro_card.png")), \
        "the Introduction example image is excluded from the production package"


def test_the_intro_card_image_is_mirrored_to_the_domain_root():
    """index.html asks for /intro_card.png. Apache serves the domain root, and only files mirrored
    there answer that path -- the hvf_web/ copy alone would 404."""
    import zipfile
    from pathlib import Path
    import build_ionos_package as pkg

    out = pkg.build(Path(pkg.DEFAULT_OUTPUT))[0]
    names = set(zipfile.ZipFile(out).namelist())

    assert "intro_card.png" in names, "not mirrored to the root, so /intro_card.png would 404"
    assert "hvf_web/intro_card.png" in names


def test_other_images_are_still_excluded():
    """The rule names one file rather than allowing .png, so a stray chart cannot ride along."""
    from pathlib import Path
    import build_ionos_package as pkg

    assert not pkg.include_path(Path("hvf_web/some_other_chart.png"))
    assert not pkg.include_path(Path("aa_images/whatever.png"))
