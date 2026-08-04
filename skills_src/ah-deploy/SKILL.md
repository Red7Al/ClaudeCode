---
name: ah-deploy
description: >
  Ship a code change to production safely, then verify it (or roll it back). Use whenever
  the user says "deploy", "ship it", "put it live", "make it live", "release", "roll out",
  asks to run/trigger a workflow, or to roll back a change. Covers branch + commit, push,
  merge to main, triggering the matching GitHub Actions workflow, verifying the real result
  (Slack/DB), and the one-line rollback. Encodes the hard rules: secrets live in GitHub (not
  local .env), never post/preview from the local machine, never swap a working mechanism.
---

# AH Deploy — ship a change live, then verify (or roll back)

Keep it simple (memory: feedback_keep_it_simple). Take the direct path; do not invent
workarounds. If something can only run with GitHub secrets, say so in ONE line — don't try
to reproduce it locally.

## Hard rules (read first)
- **Secrets live in GitHub Secrets, NOT local `.env`** (memory: secrets_and_x_delivery).
  The local machine is switchable off (memory: feedback_scheduler). So anything needing a
  secret (Slack posting, IG, etc.) runs in GitHub Actions — never post/preview it locally,
  and never ask the user to add secrets to `.env`.
- **Never swap a working mechanism.** When a flow already works, change ONLY what's asked
  (order, content). Don't bring in a new tool/method to "get back to where we were".
- **Scheduling is remote** (cron-job.org → workflow_dispatch); GitHub-native `schedule:`
  is banned (memory: feedback_scheduler). To run now, dispatch manually (step 5).

## Steps
1. **Branch + commit.** If on `main`, branch first (`git checkout -b <name>-YYYY-MM-DD`).
   Commit only what's correct and tested; end the message with the Co-Authored-By trailer.
2. **Verify what you can locally** — compile/import touched modules; run `python
   test_hvf_method.py` and READ the log; render cards/tweets offline if relevant. State
   plainly what CANNOT be verified locally (anything needing a GitHub secret).
3. **Push the branch:** `git push -u origin <branch>`.
4. **Merge to main** (merge commit → clean rollback) + push:
   `git checkout main && git merge --ff-only origin/main && git merge --no-ff <branch> -m "Merge: ..." && git push origin main`
5. **Trigger the matching workflow** (it holds the secrets):
   `gh workflow run <file>.yml --ref main`, then `gh run list --workflow=<file>.yml -L 1`.
6. **Verify where it actually lands** — the Slack channel / the DB / the run log
   (`gh run watch <id>`, or `gh run view <id> --log-failed`). A green run alone is not proof
   the user-visible output is correct.
7. **Roll back if needed:** `git revert -m 1 <merge-sha> && git push origin main`, then
   re-run the workflow. To amend instead, push a fix to `main` and re-run.

## Workflow → output map (confirm with `gh workflow list` + read the `.yml`)
- `trading-hvf-report.yml` — full Squeeze scan → report to #claude-trading-signals AND X drafts
  (text + card PNG, weight-ordered) to #arw-claude-twitter via `_generate_x_drafts`.
- `trading-uk-hvf-watch.yml` / `trading-us-hvf-watch.yml` — intraday Squeeze watches (also post X drafts).
- `trading-run-schema.yml` — apply idempotent schema migrations (run BEFORE code needing a new column).
- `trading-diagnostics.yml` / `trading-watchdog.yml` / `trading-self-audit.yml` — health checks.

## When a workflow file changes
Update its `name:` field to include today's date (YYYY-MM-DD) so the user can confirm the
new version is running (memory: feedback_naming).
