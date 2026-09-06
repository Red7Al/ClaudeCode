"""Package every skills_src/<name>/ directory into its distributable <PREFIX>-<name>.skill archive.

WHY THIS EXISTS. The packaged .skill files are what actually get loaded; skills_src/ is the source. They
drifted badly because packaging was a manual step: on 2026-08-22 all five shipped archives were roughly
two months behind their sources (packaged 12-20 June, sources changed 4 August onward, ah-web-formatting
changed that very day), ah-x-publications had grown 7.2 KB -> 15.9 KB in source, and six skills had no
package at all. A stale skill is worse than a missing one, because it silently teaches the old rules.

Run `python build_skills.py` after editing anything under skills_src/. test_skill_packages.py fails if a
package is missing or out of date, so the drift cannot go unnoticed again.
"""

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "skills_src"

# skills_src/ah-hvf-analysis -> AH-hvf-analysis.skill. The archive stores paths under the SOURCE
# directory name ("ah-hvf-analysis/SKILL.md"), which is the layout the existing archives use.
PREFIX = "AH"


def archive_name(skill_dir: Path) -> str:
    stem = skill_dir.name
    if stem.startswith("ah-"):
        stem = stem[len("ah-"):]
    return f"{PREFIX}-{stem}.skill"


def skill_dirs() -> list:
    return sorted(d for d in SOURCE.iterdir() if d.is_dir() and (d / "SKILL.md").is_file())


def members(skill_dir: Path) -> list:
    """SKILL.md first (it is the entry point), then every other file in stable path order."""
    files = sorted(p for p in skill_dir.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    head = [p for p in files if p.name == "SKILL.md" and p.parent == skill_dir]
    return head + [p for p in files if p not in head]


# Text members are stored with LF, whatever the building platform used on disk.
#
# WHY (CI red, diagnosed 2026-09-06). .gitattributes carries `* text=auto`, so skills_src/**/*.md is
# CRLF in a Windows working tree and LF in a Linux one. The .skill archive is a ZIP -- binary to git --
# so it preserves whatever bytes the machine that BUILT it happened to have. An archive built on Windows
# therefore holds CRLF, and test_skill_packages, which compares packaged bytes against the source file's
# bytes, can never pass on Linux. It had failed on every CI run for weeks while passing locally, and the
# instruction it prints ("Run: python build_skills.py") could not fix it: rebuilding on Windows
# reproduces the same CRLF.
#
# Normalising here makes the archive reproducible from either platform, which is what a build artifact
# committed to the repository has to be. Only text extensions are touched -- normalising an image would
# corrupt it.
_TEXT_SUFFIXES = {".md", ".txt", ".json", ".yml", ".yaml", ".py", ".csv", ".cfg", ".ini"}


def member_bytes(path) -> bytes:
    """The bytes a member is stored as: LF-normalised for text, untouched for anything else."""
    raw = path.read_bytes()
    if path.suffix.lower() not in _TEXT_SUFFIXES:
        return raw
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def build_one(skill_dir: Path, output_dir: Path) -> tuple:
    out = output_dir / archive_name(skill_dir)
    paths = members(skill_dir)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in paths:
            rel = f"{skill_dir.name}/{path.relative_to(skill_dir).as_posix()}"
            # Mirror the file's own mtime, as the hand-built archives did: an unchanged source then
            # rebuilds to identical bytes, so a no-op run does not churn the repository.
            info = zipfile.ZipInfo(rel, date_time=_mtime(path))
            info.external_attr = 0o644 << 16
            info.create_system = 3
            archive.writestr(info, member_bytes(path), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return out, paths


def _mtime(path: Path) -> tuple:
    import time
    t = time.localtime(path.stat().st_mtime)
    return (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec // 2 * 2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT, help="where to write the .skill archives")
    parser.add_argument("--check", action="store_true",
                        help="report which archives are missing or stale; write nothing")
    args = parser.parse_args()

    if args.check:
        stale = [d.name for d in skill_dirs() if _is_stale(d, args.output)]
        for name in stale:
            print(f"STALE OR MISSING: {name}")
        print(f"{len(skill_dirs()) - len(stale)} of {len(skill_dirs())} skill packages are current.")
        return 1 if stale else 0

    for skill_dir in skill_dirs():
        out, paths = build_one(skill_dir, args.output)
        print(f"{out.name:42} {len(paths)} file(s), {out.stat().st_size} bytes")
    print(f"Packaged {len(skill_dirs())} skills from {SOURCE.relative_to(ROOT).as_posix()}/.")
    return 0


def _is_stale(skill_dir: Path, output_dir: Path) -> bool:
    out = output_dir / archive_name(skill_dir)
    if not out.is_file():
        return True
    try:
        with zipfile.ZipFile(out) as archive:
            have = {i.filename: archive.read(i.filename) for i in archive.infolist()}
    except (OSError, zipfile.BadZipFile):
        return True
    # member_bytes, matching build_one. Comparing against the raw on-disk bytes made a freshly built
    # archive read as STALE on any platform whose working tree differs from the stored form.
    want = {f"{skill_dir.name}/{p.relative_to(skill_dir).as_posix()}": member_bytes(p)
            for p in members(skill_dir)}
    return have != want


if __name__ == "__main__":
    raise SystemExit(main())
