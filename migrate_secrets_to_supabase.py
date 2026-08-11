# ======================================================================================================================
# File:         migrate_secrets_to_supabase.py
# Author:       Alex Hind (via Claude)
# Created:      2026-08-01
#
# One-time (idempotent) seed of the encrypted app_secrets store (task #53) from the current .env + the local
# web_users __app__ credential store. Keys are stored under their ENV-VAR name (UPPERCASE) so
# app_secrets.load_secrets_into_env() can populate os.environ directly.
#
# The 3 bootstrap secrets (SUPABASE_USER, SUPABASE_DB_PASSWORD, APP_SECRET_KEY) are intentionally NOT migrated
# — they must stay environment-provided.
#
#   python migrate_secrets_to_supabase.py            # seed
#   python migrate_secrets_to_supabase.py --verify   # decrypt-read every key back and compare to source
#
# 2026-08-08 (user, P-11)  _ENV_SECRETS gained CRONJOB_API_KEY + 6 Slack secrets (SLACK_BOT_TOKEN,
#                          SLACK_ORDERS, SLACK_RW_HVF, SLACK_SIGNALS_CHANNEL_ID, SLACK_TWITTER,
#                          SLACK_TWITTER_CHANNEL_ID) — these were live GitHub Secrets read by app code
#                          (notify.py, run_hvf_report.py, social_monitor.py, instrument_dossier.py,
#                          intraday_signals.py, setup_cronjobs.py) with no path into the encrypted store.
#                          GITLEAKS_LICENSE deliberately excluded — consumed directly by the gitleaks
#                          Action in trading-secret-scan.yml, never read by app code via os.environ.
# ======================================================================================================================

import argparse
import os
import sys

from dotenv import load_dotenv; load_dotenv(override=True)

import app_secrets

_BOOTSTRAP = {"SUPABASE_USER", "SUPABASE_DB_PASSWORD", "APP_SECRET_KEY"}

# Secrets currently sourced from .env / GitHub Secrets that should move into the store.
# Kept in sync with every `secrets.X` referenced across .github/workflows/*.yml — see
# test_migrate_secrets.py::test_every_workflow_secret_is_covered_or_explicitly_excluded, which fails the
# build if a new GitHub Secret is added to a workflow but never wired into this list (P-11, 2026-08-08:
# CRONJOB_API_KEY and 6 Slack secrets were being read by app code at runtime but had no way to reach
# Supabase — added below).
_ENV_SECRETS = [
    "IG_API_KEY", "IG_USERNAME", "IG_PASSWORD", "IG_ACCOUNT_ID",
    "SLACK_ALERTS", "SLACK_DAILY", "SLACK_SIGNALS", "SLACK_TRADES", "SLACK_WEEKLY",
    "SLACK_BOT_TOKEN", "SLACK_ORDERS", "SLACK_RW_HVF", "SLACK_SIGNALS_CHANNEL_ID",
    "SLACK_TWITTER", "SLACK_TWITTER_CHANNEL_ID",
    "FRED_API_KEY", "EIA_API_KEY", "QUIVER_QUANT_API_KEY",
    "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET",
    "RESEND_API_KEY", "YAHOO_USER", "YAHOO_APP_PASSWORD", "GITHUB_TOKEN", "GH_PAT",
    "CRONJOB_API_KEY",
]


def _sources() -> dict:
    """{ENV_NAME: value} to seed — from .env first, then the local web_users __app__ store (lowercase keys
    are upper-cased). Bootstrap keys and blanks are skipped."""
    out = {}
    for k in _ENV_SECRETS:
        v = os.environ.get(k, "")
        if v and k not in _BOOTSTRAP:
            out[k] = v
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "hvf_web"))
        import web_users
        rec = (web_users._load().get("__app__") or {}).get("secrets") or {}
        for lk in rec:
            up = lk.upper()
            if up in _BOOTSTRAP or up in out:
                continue
            v = web_users.get_app_secret(lk)
            if v:
                out[up] = v
    except Exception as e:
        print(f"  (warning: could not read local __app__ store: {e})")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="read back + compare instead of seeding")
    a = ap.parse_args()

    app_secrets.ensure_schema()
    src = _sources()
    if not src:
        print("Nothing to migrate (no source secrets found).")
        return

    if a.verify:
        ok = bad = 0
        for k, v in src.items():
            got = app_secrets._load_all().get(k)
            match = (got == v)
            print(f"  {'OK ' if match else 'MISMATCH'}  {k}")
            ok += 1 if match else 0
            bad += 0 if match else 1
        print(f"verify: {ok} match, {bad} mismatch, of {len(src)} source secrets.")
        return

    seeded = 0
    for k, v in src.items():
        if app_secrets.set_secret(k, v, updated_by="migrate"):
            seeded += 1
            print(f"  seeded  {k}  (len {len(v)})")
        else:
            print(f"  FAILED  {k}")
    print(f"done: {seeded}/{len(src)} secrets encrypted into app_secrets. "
          f"(bootstrap kept in env: {sorted(_BOOTSTRAP)})")


if __name__ == "__main__":
    main()
