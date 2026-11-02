"""Server-side backup rotation in deploy_ionos.sh, executed rather than read (2026-11-02), with a local bash only.

Until 2026-11-02 every deploy wrote ~/backup-<date>.tar.gz into the host's home folder and none was ever removed.
The extraction step's remote heredoc now writes into ~/backups/squeeze-scanner/ (dir 700, file 600), keeps the
newest 10 and removes only files named exactly backup-YYYYMMDD-HHMMSS.tar.gz at the top level of that folder.

The heredoc is read from the CURRENT deploy_ionos.sh at test time, so these tests cannot drift from what deploys.
Only its head runs -- from `set -euo pipefail` through the `fi` that closes the backup/rotation block; the rest
unzips a release on the host and is cut off. It runs under a real bash (`bash -s -- <release> <skip>`, as on the
host) with HOME pointed at a temporary folder. On Windows that is Git Bash, never WSL's System32 launcher; there
POSIX modes do not apply, so the mode assertions run only on POSIX (the CI ubuntu runner).
"""
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "deploy_ionos.sh"
BACKUP_RE = re.compile(r"^backup-\d{8}-\d{6}\.tar\.gz$")
SUB = "old-home-folder-backups-2026-08-15-to-2026-11-02"
HAND_NAMED = ("backup-1x-2y.tar.gz", "backup-2019-0-pre-migration-KEEP.tar.gz",
              "backup-20260101-0000-keep.tar.gz", "backup-9-9-manual.tar.gz")
POSIX = os.name == "posix"


def _find_bash():
    """A real GNU bash: /bin/bash on POSIX; Git Bash on Windows (never the WSL launcher in System32/WindowsApps)."""
    if POSIX:
        return shutil.which("bash") or ("/bin/bash" if os.path.exists("/bin/bash") else None)
    found = shutil.which("bash")
    if found:
        low = found.lower()
        if "\\system32\\" in low or "\\windowsapps\\" in low:
            found = None
    for cand in (found, r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
        if cand and os.path.exists(cand):
            return cand
    return None


BASH = _find_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="no suitable GNU bash (Git Bash on Windows, /bin/bash on POSIX)")


@pytest.fixture(scope="module", autouse=True)
def _report_bash():
    v = subprocess.run([BASH, "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    print(f"\n[test_deploy_ionos_backups] bash: {BASH} -- {v}")


def _backup_head():
    """The extraction heredoc, from its first line through the `fi` closing the backup block."""
    lines = SCRIPT.read_text(encoding="utf-8").split("\n")
    start = next(i for i, ln in enumerate(lines)
                 if 'bash -s -- "$IONOS_DIR"' in ln and ln.rstrip().endswith("<<'REMOTE'"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "REMOTE")
    body = lines[start + 1:end]
    assert body[0] == "set -euo pipefail", body[0]
    opener = next(i for i, ln in enumerate(body) if ln.startswith('if [ "$SKIP_BACKUP"'))
    close = next(i for i in range(opener + 1, len(body)) if body[i] == "fi")
    head = "\n".join(body[:close + 1]) + "\n"
    assert "unzip" not in head and "unzip" in "\n".join(body[close + 1:]), "cut point moved"
    return head


def _snapshot(root):
    """{relative posix path: bytes or 'DIR'} for everything under root."""
    out = {}
    for p in sorted(Path(root).rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = "DIR" if p.is_dir() else p.read_bytes()
    return out


class Env:
    def __init__(self, tmp_path, home_name="home"):
        self.root = tmp_path
        self.home = tmp_path / home_name
        self.folder = self.home / "backups" / "squeeze-scanner"
        self.release = tmp_path / "release"
        self.home.mkdir(parents=True)
        (self.release / "cgi-bin").mkdir(parents=True)
        (self.release / "data").mkdir()
        (self.release / ".env").write_bytes(b"SECRET=1\n")
        (self.release / "cgi-bin" / "app.py").write_text("print(1)\n")

    def seed(self, count, folder=None):
        folder = folder or self.folder
        folder.mkdir(parents=True, exist_ok=True)
        names = [f"backup-20010101-{i:06d}.tar.gz" for i in range(1, count + 1)]
        for n in names:
            (folder / n).write_text(n)
        return names

    def run(self, skip="0"):
        env = dict(os.environ)
        env.pop("BASH_ENV", None)
        env.pop("ENV", None)
        env["HOME"] = str(self.home)
        kw = {"preexec_fn": lambda: os.umask(0o022)} if POSIX else {}
        r = subprocess.run([BASH, "-s", "--", str(self.release), skip], input=_backup_head(),
                           env=env, capture_output=True, text=True, timeout=120, **kw)
        assert r.returncode == 0, f"backup step aborted with {r.returncode}:\n{r.stdout}\n{r.stderr}"
        return r.stdout

    def top(self):
        return sorted(p.name for p in self.folder.iterdir() if p.is_file())

    def dated(self):
        return [n for n in self.top() if BACKUP_RE.match(n)]

    def new(self):
        return [n for n in self.dated() if not n.startswith("backup-2001")]


def _expect_kept(e, seeded_from):
    new = e.new()
    assert len(new) == 1, e.top()
    assert e.dated() == sorted([f"backup-20010101-{i:06d}.tar.gz" for i in range(seeded_from, 13)] + new), e.top()


def test_first_run_creates_private_folder_and_a_valid_backup_holding_env(tmp_path):
    e = Env(tmp_path)
    e.run()
    assert e.folder.is_dir()
    new = e.new()
    assert len(new) == 1 and e.top() == new, e.top()
    with tarfile.open(e.folder / new[0], "r:gz") as t:
        members = {m.name[2:] if m.name.startswith("./") else m.name: m for m in t.getmembers()}
        assert ".env" in members, sorted(members)
        assert t.extractfile(members[".env"]).read() == b"SECRET=1\n"
    assert not [p for p in e.home.iterdir() if p.is_file()], "a backup landed in the home folder"
    if POSIX:   # modes are meaningless on Windows (Git Bash chmod is a no-op), checked on the CI ubuntu runner
        assert (e.folder.stat().st_mode & 0o777) == 0o700
        assert ((e.home / "backups").stat().st_mode & 0o777) == 0o700
        assert ((e.folder / new[0]).stat().st_mode & 0o777) == 0o600


def test_twelve_existing_backups_leave_the_newest_ten(tmp_path):
    e = Env(tmp_path)
    e.seed(12)
    out = e.run()
    _expect_kept(e, 4)
    for i in (1, 2, 3):
        assert f"removed old backup {e.folder.as_posix()}/backup-20010101-{i:06d}.tar.gz" in out.replace("\\", "/") \
            or f"backup-20010101-{i:06d}.tar.gz" in out, out
    assert out.count("removed old backup") == 3, out


def test_hand_named_files_are_never_removed_and_never_counted(tmp_path):
    e = Env(tmp_path)
    e.seed(9)
    for n in HAND_NAMED:
        (e.folder / n).write_text("hand")
    out = e.run()
    assert "removed old backup" not in out, out          # 9 dated + 1 new = 10 exactly
    assert len(e.dated()) == 10
    for n in HAND_NAMED:
        assert (e.folder / n).read_text() == "hand"

    e.seed(12)                                             # now 12 old dated + the new one + hand-named
    out = e.run()
    for n in HAND_NAMED:
        assert (e.folder / n).read_text() == "hand", f"{n} removed:\n{out}"
    assert len(e.dated()) == 10, e.top()
    assert out.count("removed old backup") >= 3, out


def test_home_with_a_space_removes_only_the_right_files(tmp_path):
    e = Env(tmp_path, home_name="h o")
    e.seed(12)
    # decoys at exactly the paths a word-split ".../h o/backups/..." would name: ".../h" and "o/..." under cwd
    decoys = [tmp_path / "h"]
    for i in range(1, 13):
        d = e.release / "o" / "backups" / "squeeze-scanner" / f"backup-20010101-{i:06d}.tar.gz"
        d.parent.mkdir(parents=True, exist_ok=True)
        decoys.append(d)
    for d in decoys:
        d.write_text("decoy")
    out = e.run()
    for d in decoys:
        assert d.exists() and d.read_text() == "decoy", f"{d} outside the backups folder was touched:\n{out}"
    _expect_kept(e, 4)


def test_owners_moved_backups_subfolder_and_readmes_untouched_and_not_counted(tmp_path):
    e = Env(tmp_path)
    e.seed(12)
    sub = e.folder / SUB
    sub.mkdir()
    for n in ([f"backup-20000101-{i:06d}.tar.gz" for i in range(1, 9)]       # older than the top level
              + [f"backup-20990101-{i:06d}.tar.gz" for i in range(1, 9)]):   # newer than any new backup
        (sub / n).write_text(n)
    (sub / "README.txt").write_text("read me")
    (e.folder / "README.txt").write_text("top read me")
    before = _snapshot(sub)
    assert len([k for k in before if BACKUP_RE.match(k)]) == 16 and "README.txt" in before
    out = e.run()
    assert _snapshot(sub) == before, f"subfolder content changed:\n{out}"
    assert (e.folder / "README.txt").read_text() == "top read me"
    _expect_kept(e, 4)
    assert out.count("removed old backup") == 3, out
    assert SUB not in out, out


def test_skip_backup_creates_nothing_and_removes_nothing(tmp_path):
    e = Env(tmp_path)
    e.seed(12)
    before = _snapshot(tmp_path)
    out = e.run(skip="1")
    assert _snapshot(tmp_path) == before
    assert "backing up" not in out and "removed old backup" not in out, out

    e2 = Env(tmp_path / "fresh")
    e2.run(skip="1")
    assert not (e2.home / "backups").exists()


def test_empty_existing_folder_does_not_abort(tmp_path):
    e = Env(tmp_path)
    e.folder.mkdir(parents=True)
    out = e.run()                                          # Env.run asserts exit code 0
    assert len(e.new()) == 1 and "removed old backup" not in out, out


def test_tar_failure_leaves_glob_empty_without_aborting(tmp_path):
    # a tar that writes nothing: the rotation's glob then matches no file at all, which must not abort set -eu
    e = Env(tmp_path)
    e.folder.mkdir(parents=True)
    stub = tmp_path / "stubbin"
    stub.mkdir()
    (stub / "tar").write_text("#!/bin/sh\nexit 2\n")
    os.chmod(stub / "tar", 0o755)
    env_path = os.environ.get("PATH", "")
    try:
        os.environ["PATH"] = str(stub) + os.pathsep + env_path
        out = e.run()
    finally:
        os.environ["PATH"] = env_path
    assert "(backup reported warnings; continuing)" in out, out
    assert e.dated() == [], e.top()
