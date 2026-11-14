"""Architect item 7 (2026-11-14): correct code that nothing calls. Every workflow must be fired by something --
cron-job.org (named in setup_cronjobs.py), a git event, another workflow (workflow_call) -- or be listed in MANUAL
with the reason it is run by hand. A new workflow with none of these fails here, so it cannot sit unscheduled."""
import glob
import os
import re

ROOT = os.path.dirname(__file__)

MANUAL = {
    "seed-secrets.yml": "one-off secret seeding (app_secrets.py)",
    "trading-email-test.yml": "email smoke test (docs/OPS_RUNBOOK.md)",
    "trading-instrument-dossier.yml": "on-demand dossier (skills_src/ah-instrument-dossier)",
    "trading-query.yml": "ad-hoc read-only query (docs/STATUS.md; output never tracked)",
    "trading-self-audit.yml": "manual audit (skills_src/ah-deploy)",
    "trading-x-publish.yml": "manual X publish (skills_src/ah-x-publications)",
    "trading-x-verify.yml": "manual X check (skills_src/ah-x-publications)",
    "trading-bounce-alert.yml": "REVIEW: referenced only by BACKLOG.md and run_bounce_alert.py (2026-11-14)",
    "trading-hot-take.yml": "REVIEW: no reference anywhere, dispatch-only (2026-11-14); owner to keep or delete",
    "trading-squeeze-history-replay.yml": "REVIEW: no reference anywhere, dispatch-only (2026-11-14); owner to keep or delete",
}
EVENT = ("push", "pull_request", "workflow_call", "workflow_run")


def test_every_workflow_has_a_trigger_or_a_stated_manual_reason():
    jobs = open(os.path.join(ROOT, "setup_cronjobs.py"), encoding="utf-8").read()
    orphans = []
    for f in sorted(glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml"))):
        name, text = os.path.basename(f), open(f, encoding="utf-8").read()
        has_event = any(re.search(rf"^\s*{k}\s*:", text, re.M) for k in EVENT)
        if not (has_event or name in jobs or name[:-4] in jobs or name in MANUAL):
            orphans.append(name)
    assert not orphans, f"workflow(s) nothing fires and not in MANUAL with a reason: {orphans}"


def test_manual_entries_still_exist():
    gone = [n for n in MANUAL if not os.path.exists(os.path.join(ROOT, ".github", "workflows", n))]
    assert not gone, f"MANUAL lists workflows that no longer exist: {gone}"
