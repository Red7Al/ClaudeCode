"""Owner 2026-10-21: "stopping use of git for operational files is very important". The repository is PUBLIC and
is for code. trading-query-auto.yml committed run_query.py's output (positions, P&L, signal log) into it with
contents: write (4 commits, 2026-06-02/03)."""
import glob
import re
import subprocess


def test_no_workflow_writes_to_the_repository():
    hits = [p for p in glob.glob(".github/workflows/*.yml")
            if re.search(r"contents:\s*write|git\s+(commit|push)\b", open(p, encoding="utf-8").read())]
    assert hits == [], hits


def test_no_query_output_is_tracked():
    tracked = subprocess.run(["git", "ls-files", "query_output.txt", "query_trigger.txt"],
                             capture_output=True, text=True, check=True).stdout.split()
    assert tracked == [], tracked
