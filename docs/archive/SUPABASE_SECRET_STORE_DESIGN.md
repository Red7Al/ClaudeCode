# Supabase-backed encrypted secret store — design

**Status:** proposed (2026-08-01). **Owner:** Alex / Claude. **Task:** #53.

## 1. Why

Today secrets live in two places: the local `.env` (IG, Supabase, Slack, FRED, EIA, plus the
`cronjob_api_key`) and a Fernet-encrypted **local file** (`data/web_users.json` `__app__` record, key at
`data/.web_users.key`). GitHub Actions get the same values injected from **GitHub Secrets**.

Problems: three copies drift; `.env` can't be pruned (the app reads `os.environ` directly at runtime, so
removing a key breaks DB/IG/Slack); GitHub Secrets are unreadable so they can't be a source of truth; and
`app_config` is **plaintext** so it must never hold secrets.

**Goal:** ONE encrypted source of truth in Supabase, decrypted at runtime, so `.env` shrinks to an
irreducible bootstrap and Actions need only 3 secrets. Encrypted at rest; useless without the key.

## 2. The irreducible bootstrap (what MUST stay in the environment)

You cannot store the DB credentials inside the DB you need them to reach, nor the decryption key in the
store it decrypts. So exactly **three** values remain environment-provided (env var locally; GitHub Secret
in Actions):

| Var | Why it can't move |
|-----|-------------------|
| `SUPABASE_USER` | needed to connect to Supabase to read the store (chicken-and-egg) |
| `SUPABASE_DB_PASSWORD` | same |
| `APP_SECRET_KEY` | the Fernet master key that decrypts the store (never in the DB or git) |

Everything else — `IG_*`, `SLACK_*`, `FRED_API_KEY`, `EIA_API_KEY`, `X_*`, `cronjob_api_key`, and (optionally)
a `GH_PAT` for `setup_cronjobs.py` — moves into the encrypted table.

## 3. Schema

```sql
create table if not exists app_secrets (
    key         text primary key,          -- e.g. 'IG_PASSWORD', 'SLACK_ALERTS'
    ciphertext  text not null,             -- base64(Fernet(APP_SECRET_KEY).encrypt(value))
    updated_by  text,
    updated_at  timestamptz default now()
);
-- RLS: deny anon/auth roles entirely; only the pooler/service role (used by db_pool) may read.
alter table app_secrets enable row level security;
-- (no policies for anon/authenticated → they get nothing; the direct pg connection bypasses RLS as owner)
```

Values are **Fernet-encrypted** (reusing the mechanism already in `web_users.py`), so the ciphertext in the
DB is inert without `APP_SECRET_KEY`.

## 4. Runtime access — the key design decision

Many modules read `os.environ["…"]` at import time (`db_pool`, `ig_shim`, `notify`). Rather than rewrite all
of them, **decrypt the store into `os.environ` once at process start**, before those modules are used. So the
change is *additive* and low-risk.

New module `app_secrets.py`:

```python
def load_secrets_into_env(override=False) -> int:
    """Decrypt every app_secrets row and set it in os.environ (unless already set, or override=True).
    Called ONCE at startup, AFTER SUPABASE_USER/DB_PASSWORD + APP_SECRET_KEY are present, and BEFORE
    db_pool/ig_shim/notify read their values. Fail-open on individual rows; never raises."""

def get_secret(name: str, default="") -> str:
    """Single-value read: os.environ first (bootstrap/back-compat), else decrypt from the store (cached)."""

def set_secret(name: str, value: str, updated_by=""):
    """Fernet-encrypt + upsert into app_secrets; refresh cache. Used by the admin Credentials UI."""
```

Call site (single chokepoint — IMPLEMENTED): `db_pool.py` runs `_bootstrap_secrets()` at import (after
`get_db` is defined, once per process, fully fail-open). Every DB-using entrypoint — the web server and all
41 GitHub-Actions scripts — imports `db_pool`, so this one hook wires them all with no per-file edits.
`hvf_web/server.py __main__` also calls it explicitly at boot (belt-and-suspenders). Verified: importing
`db_pool` with only the bootstrap creds in env pulls `CRONJOB_API_KEY`/`FRED_API_KEY` from the store.

Key rotation is supported by making `APP_SECRET_KEY` a **MultiFernet** (comma-separated keys: new first for
encrypt, all tried for decrypt) — rotate by prepending a new key, re-encrypting, then retiring the old.

## 5. Migration (phased, reversible)

1. **Provision.** Create `app_secrets`; generate `APP_SECRET_KEY` (`Fernet.generate_key()`), set it as an env
   var locally and a GitHub Secret. Add `SUPABASE_USER`, `SUPABASE_DB_PASSWORD`, `APP_SECRET_KEY` to Actions.
2. **Seed.** `migrate_secrets_to_supabase.py` reads the current `.env` + the local Fernet store and
   `set_secret()`s each into `app_secrets`. Idempotent; prints a checklist of what landed.
3. **Dual-read.** Ship `load_secrets_into_env()` at every entrypoint. Because it only fills keys **not already
   in env**, `.env` still wins during the transition — nothing changes behaviourally. Verify every secret
   resolves from the store with `.env` temporarily emptied on a scratch run.
4. **Prune.** Once verified, delete the non-bootstrap keys from `.env` and from the per-workflow GitHub Secret
   injections (keep only the 3 bootstrap secrets). The local Fernet file store can be retired or kept as a
   cold backup.
5. **UI.** Point the admin Credentials section (`get_app_secret`/`set_app_secret`) at `app_secrets` so edits
   write the encrypted DB rows (one source of truth), replacing the local-file store.

## 6. Security notes

- Ciphertext in `app_secrets` is useless without `APP_SECRET_KEY`; the key is never in the DB or git.
- RLS denies anon/authenticated; the owner pg connection is the only reader.
- `updated_by` / `updated_at` give an audit trail; every write is attributable.
- Rotation via MultiFernet; documented runbook.
- This directly closes the security-review note about `.env`/local-key co-location under OneDrive: the
  authoritative secrets move to Supabase, and only the master key + DB creds remain local.
- **Do NOT** ever write raw secrets to `app_config` (plaintext) — that table stays for non-secret config only.

## 7. Effort / risk

- Net-new: `app_secrets.py`, `migrate_secrets_to_supabase.py`, the table + RLS, ~1 line per entrypoint.
- No change to `db_pool`/`ig_shim`/`notify` (they keep reading `os.environ`, now populated from the store).
- Risk is contained by the dual-read phase (env still wins until pruned) and per-row fail-open.
- Rollback = restore the pruned `.env` keys; the store + code are inert if `APP_SECRET_KEY` is absent
  (fail-open leaves existing env untouched).
