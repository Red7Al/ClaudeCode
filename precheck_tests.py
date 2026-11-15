"""Belt-and-braces check BEFORE the 18-minute suite (owner 2026-11-15: "we should have belt and braces checks prior to
testing"). Takes the names (functions, classes, constants) defined or changed in the diff since BASE and runs every test
file that mentions any of them, plus the files changed themselves. Seconds, not minutes; it would have caught the two stale
`_send(channel, blocks)` stubs that the full suite found on 2026-11-14.   usage: precheck_tests.py [BASE=origin/main]"""
import re
import subprocess
import sys

DEFN = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+(\w+)|^([A-Z_][A-Z0-9_]{3,})\s*=")
BASE = sys.argv[1] if len(sys.argv) > 1 else "origin/main"


def git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True, check=True).stdout


def main():
    changed = [f for f in git("diff", "--name-only", BASE, "HEAD").split() if f.endswith(".py")]
    names = set()
    for f in changed:
        for line in git("diff", "-U0", BASE, "HEAD", "--", f).splitlines():
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                for fn, const in DEFN.findall(line[1:]):
                    names.add(fn or const)
    tests = set(f for f in changed if f.startswith("test_"))
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in sorted(names)) + r")\b") if names else None
    for t in git("ls-files", "test_*.py").split():
        if pattern and pattern.search(open(t, encoding="utf8", errors="ignore").read()):
            tests.add(t)
    print(f"{len(changed)} changed modules, {len(names)} changed names, {len(tests)} test files to run first")
    if not tests:
        return 0
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-m", "not live_state", "-p", "no:cacheprovider",
                           *sorted(tests)]).returncode


if __name__ == "__main__":
    sys.exit(main())
