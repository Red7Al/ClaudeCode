"""Build a production-only IONOS zip without modifying or deleting workspace files."""

import argparse
import json
import stat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "dist" / "ionos" / "squeeze-scanner-ionos.zip"


def include_path(relative: Path) -> bool:
    parts = relative.parts
    if not parts:
        return False
    # The admin Change Requests tab reads these source-of-record registers at runtime. They are protected
    # by the API's admin gate and the root .htaccess denial for direct .txt requests.
    if parts[0] == "ChangeRequests":
        return len(parts) == 2 and relative.suffix.lower() == ".txt"
    if parts[0] in {
        ".git", ".github", ".claude", ".obsidian", ".pytest_cache", ".venv", "node_modules",
        "aa_images", "dossier", "x_drafts", "tests_fixtures", "skills_src", "dist",
        "data",
    }:
        return False
    if parts[0].startswith(".pytest_"):
        return False
    if "__pycache__" in parts or "price_cache" in parts:
        return False
    name, suffix = relative.name, relative.suffix.lower()
    if relative.as_posix() == ".htaccess":
        return True
    if name.startswith("test_") or suffix in {".zip", ".docx", ".skill", ".pkl", ".pyc", ".log", ".cmd", ".bat"}:
        return False
    if name in {".env", ".gitignore", ".gitattributes", ".gitleaks.toml", ".pre-commit-config.yaml",
                "AGENTS.md", "BACKLOG.md", "pytest.ini", "requirements-dev.txt", "build_ionos_package.py"}:
        return False
    if name == "IONOS_DEPLOYMENT.md":
        return True
    if relative.parts[:2] == ("hvf_web", "data"):
        return False
    if relative.as_posix() in {"hvf_web/name_cache.json", "sector_cache.json"}:
        return False
    if parts[0] == "docs":
        return len(parts) == 3 and parts[1] == "guides" and (suffix == ".html" or name == "_manifest.json")
    if suffix == ".json":
        if relative.as_posix() == "hvf_web/snapshot.json":
            return True   # rebuildable, but retained as a boot cache until the first hosted refresh
        return False
    if suffix == ".txt":
        return name == "requirements.txt"
    # The page's JavaScript, extracted from index.html on 2026-08-23. best_settings.js joined it on
    # 2026-09-03 (the Best Settings search, shared with the Node precompute) and index.html loads it
    # FIRST, so a package without it is a page that cannot start. Only these two .js files ship: docs/
    # build scripts and node_modules are excluded above and must stay excluded.
    if relative.as_posix() in ("hvf_web/app.js", "hvf_web/best_settings.js"):
        return True
    # The Introduction's worked example, pre-rendered (user 2026-08-30). It is the ONLY image that ships:
    # /api/card/<ticker> built it live and answered HTTP 500 after ~120 s on the host, because it runs a
    # yfinance download and a matplotlib render on the request thread. A blanket .png rule would sweep in
    # every stray chart in the tree, so this is named explicitly.
    if relative.as_posix() == "hvf_web/intro_card.png":
        return True
    return suffix in {".py", ".html", ".sql"}


def package_files():
    return sorted(
        (path for path in ROOT.rglob("*") if path.is_file() and include_path(path.relative_to(ROOT))),
        key=lambda path: path.relative_to(ROOT).as_posix(),
    )


def version_history_entries() -> list:
    """Version history, read from git HERE because the IONOS host has none.

    server._version_entries() builds this live from `git log`, falling back to a Supabase store and to
    data/version_history.json -- the REPO-ROOT data/ directory (server._VERSION_FILE), not hvf_web/data/.
    On IONOS the git call always fails, because the package deliberately excludes .git, and nothing in the
    deploy ever refreshed either fallback: the Supabase copy was seeded once by
    migrate_runtime_state_to_supabase.py and sat at 958 entries ending 2026-08-18, and the file was never
    shipped at all because package_files() excludes data/. So the tab froze on 18/8 (user 2026-08-22:
    "version history is not being maintained", and again 2026-08-23: "latest entry 18/8").

    Generating it at package time keeps the file fallback current with the very commits being deployed.
    """
    import subprocess
    out = subprocess.check_output(
        ["git", "-C", str(ROOT), "log", "--date=short", "--pretty=format:%ad|%h|%s"],
        text=True, encoding="utf-8", errors="replace", timeout=60)
    entries = []
    for line in out.splitlines():
        if not line.strip():
            continue
        date, version, summary = line.split("|", 2)
        entries.append({"date": date, "version": version, "summary": summary.strip()})
    return entries


def cache_bust(relative: str, contents: bytes, stamp: str) -> bytes:
    """Version the client script URLs so a deploy actually reaches the browser.

    THE BUG THIS FIXES, observed live on 2026-09-05. index.html loads best_settings.js and app.js by bare
    filename, so the browser keeps serving whatever it cached. A stale best_settings.js was found on the
    live site defining NEITHER of its functions, which made app.js die on "ReferenceError:
    makeCombReplay is not defined" and left the Scanner stuck on "Data loading..." for ever. A hard
    reload fixed it instantly -- the signature of a caching problem, not a code one -- and it explains a
    run of reports that a feature had "stopped working AGAIN" while the host was serving the right file.

    Appending the build fingerprint makes a new deploy a new URL, so the browser must refetch. Nobody has
    to know to press Ctrl+Shift+R.

    Module level, and taking the stamp as an argument, so it can be tested without building a package.
    """
    if relative != "hvf_web/index.html" or not stamp:
        return contents
    text = contents.decode("utf-8")
    for name in ("best_settings.js", "app.js"):
        text = text.replace(f'src="{name}"', f'src="{name}?v={stamp}"')
    return text.encode("utf-8")


def build_identity() -> dict:
    """A stamp identifying THIS package, so the live API can be asked which build it is running.

    IONOS shared hosting keeps the imported Flask module resident behind the CGI wrapper and offers no
    way to restart it: there is no control panel button, and the SSH session is a sandbox that cannot see
    the web worker at all. So a deploy can update every file on disk while /api/* keeps answering from a
    previously-loaded module -- twice on 2026-08-22/23 that went unnoticed until a new route 404'd, and
    silently affected changes that added no route.

    Deliberately NOT the commit sha: a short fingerprint is enough to compare "is the worker running what
    I just shipped" without publishing the repository's history on an unauthenticated endpoint.
    """
    import hashlib
    import subprocess
    from datetime import datetime, timezone
    try:
        sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                      text=True, timeout=30).strip()
    except Exception:
        sha = ""
    return {"fingerprint": hashlib.sha256(sha.encode()).hexdigest()[:12] if sha else "",
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def build(output: Path = DEFAULT_OUTPUT) -> tuple:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = package_files()
    identity = build_identity()
    stamp = str(identity.get("fingerprint") or "")[:12]

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo.from_file(path, relative)
            info.create_system = 3  # Unix permissions must survive extraction on IONOS Linux hosting.
            mode = 0o755 if relative == "cgi-bin/app.py" else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            with path.open("rb") as source:
                contents = cache_bust(relative, source.read(), stamp)
            archive.writestr(info, contents, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            # Apache serves the domain root directly; retain the package copy used by Flask as well.
            # app.js is mirrored the same way, because index.html loads it relatively — if only the
            # hvf_web/ copy shipped, the root page would load and then fail to find its script.
            if relative in ("hvf_web/index.html", "hvf_web/app.js", "hvf_web/best_settings.js",
                            "hvf_web/intro_card.png"):
                root_info = zipfile.ZipInfo.from_file(path, relative.rsplit("/", 1)[1])
                root_info.create_system = 3
                root_info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(root_info, contents, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        # server._VERSION_FILE is <root>/data/version_history.json — the REPO-ROOT data/ directory, not
        # hvf_web/data/. package_files() excludes data/ entirely, so this generated fallback is added
        # explicitly, at the exact path _version_entries reads.
        try:
            history = json.dumps({"entries": version_history_entries()}, ensure_ascii=False)
        except Exception as exc:                     # a shallow/exportless checkout must not fail the build
            print(f"  version history could not be generated ({exc}); the host keeps its previous copy.")
        else:
            info = zipfile.ZipInfo("data/version_history.json")
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, history.encode("utf-8"),
                             compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        info = zipfile.ZipInfo("data/build_id.json")
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(info, json.dumps(identity).encode("utf-8"),
                         compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output, files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--list", action="store_true", help="list included files after building")
    args = parser.parse_args()
    output, files = build(args.output)
    mirrored = {"hvf_web/index.html", "hvf_web/app.js", "hvf_web/best_settings.js",
                "hvf_web/intro_card.png"}
    archive_entries = len(files) + sum(1 for p in files if p.relative_to(ROOT).as_posix() in mirrored)
    print(f"Built {output} with {archive_entries} production files ({output.stat().st_size} bytes).")
    import zipfile as _z
    with _z.ZipFile(output) as _a:
        print(f"BUILD_ID={json.loads(_a.read('data/build_id.json'))['fingerprint']}")
    if args.list:
        for path in files:
            print(path.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
