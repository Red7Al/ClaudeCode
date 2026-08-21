"""Backend tests for migrate_secrets_to_supabase.py (ChangeRequest P-11, 2026-08-08).

The bug this guards against: a secret gets added as a GitHub Secret and read by some app script via
os.environ, but nobody remembers to also add it to migrate_secrets_to_supabase.py's _ENV_SECRETS list
(and the seed-secrets.yml workflow's env: block) — so it can never reach the encrypted Supabase store.
That happened for real: CRONJOB_API_KEY and 6 Slack secrets (SLACK_BOT_TOKEN, SLACK_ORDERS, SLACK_RW_HVF,
SLACK_SIGNALS_CHANNEL_ID, SLACK_TWITTER, SLACK_TWITTER_CHANNEL_ID) were live secrets.X references in
.github/workflows/*.yml with no path into the store. This test scans every workflow file for
`secrets.NAME` references and fails if a name shows up that isn't accounted for by _ENV_SECRETS, the
bootstrap set, or an explicit CI-only exclusion list.
"""

import os
import re

import pytest

import migrate_secrets_to_supabase as m

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_WORKFLOWS_DIR = os.path.join(_REPO_ROOT, ".github", "workflows")

# Secrets that are consumed directly inside a workflow (by a GitHub Action's `with:`/`env:` at the
# workflow-runner level) and are never read by app code via os.environ — so they have no reason to live
# in the encrypted app_secrets store. Keep this list short and each entry justified; anything else found
# in a workflow must be added to _ENV_SECRETS instead.
_CI_ONLY_EXCLUSIONS = {
    "GITLEAKS_LICENSE",  # license key for the gitleaks Action itself, trading-secret-scan.yml
    # IONOS values are consumed only by the deployment/fallback workflow. They are infrastructure
    # connection details, not application credentials and must never be copied into app_secrets.
    "IONOS_DIR", "IONOS_HOST", "IONOS_SSH_KEY", "IONOS_USER",
}


def _secrets_referenced_in_workflows() -> set:
    names = set()
    for fn in os.listdir(_WORKFLOWS_DIR):
        if not fn.endswith((".yml", ".yaml")):
            continue
        with open(os.path.join(_WORKFLOWS_DIR, fn), encoding="utf-8") as f:
            text = f.read()
        names.update(re.findall(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)\s*\}\}", text))
    return names


def test_every_workflow_secret_is_covered_or_explicitly_excluded():
    referenced = _secrets_referenced_in_workflows()
    assert referenced, "sanity check: expected to find at least one secrets.X reference in workflows"

    covered = set(m._ENV_SECRETS) | m._BOOTSTRAP | _CI_ONLY_EXCLUSIONS
    uncovered = referenced - covered
    assert not uncovered, (
        f"GitHub Secret(s) {sorted(uncovered)} are referenced in a workflow but have no path into the "
        "Supabase app_secrets store — add to migrate_secrets_to_supabase.py's _ENV_SECRETS (and "
        "seed-secrets.yml's env: block) if app code reads them, or to _CI_ONLY_EXCLUSIONS in this test "
        "if they're genuinely CI-tool-only."
    )


def test_seed_secrets_workflow_env_block_matches_ENV_SECRETS():
    """Every non-bootstrap _ENV_SECRETS entry should be injected into seed-secrets.yml's env: block, or
    the GitHub-Actions-only seed path can never see it (values that live only in GitHub Secrets, like
    QUIVER_QUANT_API_KEY or the Slack secrets added in P-11, have no other way to reach Supabase).

    GITHUB_TOKEN is deliberately exempt: in Actions it's the ephemeral, run-scoped token GitHub injects
    automatically (not a stable credential), so seeding it into Supabase would just persist a token that's
    already expired by the time anything reads it back. _ENV_SECRETS still lists it for the *local*
    .env-sourced path, where a developer might set it to a real long-lived PAT."""
    path = os.path.join(_WORKFLOWS_DIR, "seed-secrets.yml")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    injected = set(re.findall(r"^\s*([A-Za-z0-9_]+):\s*\$\{\{\s*secrets\.\1\s*\}\}", text, re.MULTILINE))

    missing = set(m._ENV_SECRETS) - injected - {"GITHUB_TOKEN"}
    assert not missing, f"_ENV_SECRETS entries missing from seed-secrets.yml's env: block: {sorted(missing)}"


def test_sources_never_includes_bootstrap_keys(monkeypatch):
    """_sources() must never pick up a bootstrap secret even if it happens to be in os.environ or the
    local __app__ store — bootstrap credentials stay environment-provided by design (see
    docs/SUPABASE_SECRET_STORE_DESIGN.md).

    _sources() imports `web_users` (bare name) via its own sys.path.insert(hvf_web) — a different
    sys.modules entry than the package-qualified `hvf_web.web_users`. Mimic the same sys.path insert here
    first so `import web_users` resolves to (and caches) the identical module object _sources() will reuse,
    then monkeypatch that."""
    for k in m._BOOTSTRAP:
        monkeypatch.setenv(k, "should-never-be-migrated")
    import sys as _sys
    _sys.path.insert(0, os.path.join(_REPO_ROOT, "hvf_web"))
    import web_users as _wu_module
    monkeypatch.setattr(_wu_module, "_load", lambda: {"__app__": {"secrets": {}}})

    src = m._sources()

    assert not (set(src) & m._BOOTSTRAP)


def test_cronjob_api_key_and_slack_secrets_are_in_env_secrets():
    """Regression guard for the specific P-11 gap (belt-and-suspenders alongside the workflow-scan test
    above, which would also catch this — but this pins the exact names so intent is obvious in a diff)."""
    added_2026_08_08 = {
        "CRONJOB_API_KEY", "SLACK_BOT_TOKEN", "SLACK_ORDERS", "SLACK_RW_HVF",
        "SLACK_SIGNALS_CHANNEL_ID", "SLACK_TWITTER", "SLACK_TWITTER_CHANNEL_ID",
    }
    assert added_2026_08_08.issubset(set(m._ENV_SECRETS))
