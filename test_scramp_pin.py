"""scramp 1.5.0 (2026-10-25) dropped make_channel_binding, which pg8000 1.31.5 calls on login: every GitHub job failed
to connect to the database from Sunday 18:30 (100+ failures). Every place that installs pg8000 must keep scramp < 1.5."""
import glob
import re


def test_requirements_pin_scramp_below_1_5():
    req = open("requirements.txt", encoding="utf-8").read()
    assert re.search(r"^scramp[^\n#]*<1\.5", req, re.M)


def test_every_direct_pg8000_install_pins_scramp():
    bad = []
    for path in glob.glob(".github/workflows/*.yml"):
        for line in open(path, encoding="utf-8"):
            if re.search(r"pip install .*\bpg8000\b", line) and "scramp<1.5" not in line:
                bad.append(f"{path}: {line.strip()}")
    assert not bad, bad
