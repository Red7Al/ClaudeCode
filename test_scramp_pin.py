"""scramp 1.5.0 (PyPI 2026-10-25 17:43Z) dropped make_channel_binding, which pg8000 1.31.5 calls on login: every GitHub
job failed to connect to the database from Sunday 18:30 (100+ failures). The cause underneath: requirements.txt held
only minimums, so every job installed whatever was newest that minute. Now every package -- direct and indirect --
is pinned in constraints.txt, which requirements.txt applies, and every direct pip install applies it too."""
import glob
import re


def _pins():
    return {l.split("==")[0].lower().replace("_", "-"): l.split("==")[1].strip()
            for l in open("constraints.txt", encoding="utf-8") if "==" in l and not l.startswith("#")}


def test_requirements_apply_the_constraints():
    req = open("requirements.txt", encoding="utf-8").read()
    assert re.search(r"^-c constraints\.txt", req, re.M)
    assert re.search(r"^scramp[^\n#]*<1\.5", req, re.M)


def test_every_requirement_and_the_login_library_are_pinned_exactly():
    pins = _pins()
    names = [re.split(r"[<>=~ ;#\[]", l.strip())[0].lower().replace("_", "-")
             for l in open("requirements.txt", encoding="utf-8") if l.strip() and not l.lstrip().startswith(("#", "-"))]
    assert names and not [n for n in names if n not in pins]
    assert pins["scramp"] == "1.4.18" and pins["pg8000"] == "1.31.5"


def test_every_direct_pip_install_applies_the_constraints():
    bad = []
    for path in glob.glob(".github/workflows/*.yml"):
        for line in open(path, encoding="utf-8"):
            m = re.search(r"pip install (.*)", line)
            if m and "-r requirements" not in m.group(1) and "--upgrade pip" not in m.group(1) \
                    and "-c constraints.txt" not in m.group(1):
                bad.append(f"{path}: {line.strip()}")
    assert not bad, bad
