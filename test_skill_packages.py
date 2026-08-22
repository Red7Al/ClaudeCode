"""Every skills_src/<name>/ must have a current packaged .skill archive.

WHY. The archive is what gets loaded; skills_src/ is only the source. Packaging was a manual step, so on
2026-08-22 ALL FOURTEEN packages were stale or missing: the five that existed were roughly two months
behind (packaged 12-20 June against sources changed 4 August onward, ah-web-formatting changed that day),
ah-x-publications had grown 6.2 KB -> 15.9 KB in source, and six skills had never been packaged at all.

A stale skill is worse than a missing one: it silently teaches superseded rules. `python build_skills.py`
regenerates them; this test is what makes forgetting it visible.
"""

import zipfile
from pathlib import Path

import pytest

import build_skills

ROOT = Path(__file__).parent
SKILL_DIRS = build_skills.skill_dirs()


def test_there_are_skills_to_package():
    assert SKILL_DIRS, "skills_src/ has no skill directories — the discovery rule must have changed"


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda d: d.name)
def test_each_package_matches_its_source_exactly(skill_dir):
    """Byte-for-byte, including every reference file — not just SKILL.md, and not just the file list.

    `.gitignore` treats `*.skill` as a build artifact ("source of truth is skills_src/"), so a clean
    checkout legitimately has none and this SKIPS rather than failing — the same clean-checkout trap that
    kept the CI job red for three days in August. What must never be true is a package that EXISTS and
    disagrees with its source, which is the state all five tracked archives were in on 2026-08-22.
    The packager itself is covered unconditionally by the temp-directory tests below.
    """
    archive = ROOT / build_skills.archive_name(skill_dir)
    if not archive.is_file():
        pytest.skip(f"{archive.name} is not built in this checkout (gitignored build artifact)")

    with zipfile.ZipFile(archive) as zf:
        packaged = {i.filename: zf.read(i.filename) for i in zf.infolist()}
    source = {f"{skill_dir.name}/{p.relative_to(skill_dir).as_posix()}": p.read_bytes()
              for p in build_skills.members(skill_dir)}

    assert set(packaged) == set(source), (
        f"{archive.name} file list differs from skills_src/{skill_dir.name}. "
        f"Only in package: {sorted(set(packaged) - set(source))}. "
        f"Only in source: {sorted(set(source) - set(packaged))}. Run: python build_skills.py")
    stale = [name for name in source if packaged[name] != source[name]]
    assert not stale, f"{archive.name} is out of date for {stale}. Run: python build_skills.py"


def test_the_packager_reports_a_stale_archive():
    """A detector that has never failed proves nothing: corrupt a copy and confirm --check catches it."""
    skill_dir = SKILL_DIRS[0]
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        assert build_skills._is_stale(skill_dir, out), "a MISSING archive must read as stale"

        build_skills.build_one(skill_dir, out)
        assert not build_skills._is_stale(skill_dir, out), "a freshly built archive must read as current"

        archive = out / build_skills.archive_name(skill_dir)
        archive.write_bytes(archive.read_bytes()[:-40])
        assert build_skills._is_stale(skill_dir, out), "a DAMAGED archive must read as stale"


def test_rebuilding_unchanged_sources_is_byte_identical():
    """Non-deterministic output would churn the repository on every run and hide real changes."""
    import tempfile
    skill_dir = SKILL_DIRS[0]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        first, _ = build_skills.build_one(skill_dir, out)
        data = first.read_bytes()
        build_skills.build_one(skill_dir, out)

        assert first.read_bytes() == data
