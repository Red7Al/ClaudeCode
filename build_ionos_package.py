"""Build a production-only IONOS zip without modifying or deleting workspace files."""

import argparse
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
    return suffix in {".py", ".html", ".sql"}


def package_files():
    return sorted(
        (path for path in ROOT.rglob("*") if path.is_file() and include_path(path.relative_to(ROOT))),
        key=lambda path: path.relative_to(ROOT).as_posix(),
    )


def build(output: Path = DEFAULT_OUTPUT) -> tuple:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = package_files()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo.from_file(path, relative)
            info.create_system = 3  # Unix permissions must survive extraction on IONOS Linux hosting.
            mode = 0o755 if relative == "cgi-bin/app.py" else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            with path.open("rb") as source:
                contents = source.read()
            archive.writestr(info, contents, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            if relative == "hvf_web/index.html":
                # Apache serves the domain root directly; retain the package copy used by Flask as well.
                root_info = zipfile.ZipInfo.from_file(path, "index.html")
                root_info.create_system = 3
                root_info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(root_info, contents, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output, files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--list", action="store_true", help="list included files after building")
    args = parser.parse_args()
    output, files = build(args.output)
    archive_entries = len(files) + int(any(path.relative_to(ROOT).as_posix() == "hvf_web/index.html" for path in files))
    print(f"Built {output} with {archive_entries} production files ({output.stat().st_size} bytes).")
    if args.list:
        for path in files:
            print(path.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
