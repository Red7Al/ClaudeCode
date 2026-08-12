# IONOS deployment

Build the production-only archive with:

```powershell
python build_ionos_package.py
```

Before switching traffic:

1. Run `python migrate_runtime_state_to_supabase.py --apply` from a trusted machine and confirm every store
   reports `migrated` or `verified`. The command retains every local original.
2. Configure `SUPABASE_USER`, `SUPABASE_DB_PASSWORD`, `APP_SECRET_KEY`, `WEB_USERS_FERNET_KEY` and the
   dedicated server-only `SUPABASE_SCANNER_WEB_KEY` as IONOS
   environment secrets. `WEB_USERS_FERNET_KEY` must contain the existing key so current encrypted user/IG
   fields remain decryptable; never upload `data/.web_users.key` into the web root. The Scanner key must be
   a modern `sb_secret_...` Supabase key and must never appear in browser code, source control or a URL.
3. For IONOS Linux Web Hosting, extract the archive into the domain directory. The package's `.htaccess`
   serves static HTML directly and rewrites `/api/*` to `cgi-bin/app.py`; keep that adapter executable
   (`chmod 755 cgi-bin/app.py`). It uses `.venv_linux`, which can be created/refreshed with
   `python3 -m venv .venv_linux` followed by `.venv_linux/bin/python -m pip install -r requirements.txt`.
   `wsgi:application` remains available for VPS, Cloud Server or another persistent WSGI host.
4. Keep scheduled scans, order bridging and other background jobs in their existing scheduler. The heavy
   Scanner build is performed by `trading-scanner-snapshot.yml` and publishes to the private
   `scanner-artifacts` Supabase Storage bucket; configure its dedicated `SUPABASE_SCANNER_PUBLISH_KEY` as a
   GitHub Actions secret. The morning HVF report reuses its completed scan for the first publication and the
   evening Scanner job performs the second full refresh. WSGI may run
   several worker processes, so starting an order loop in every web worker would risk duplicate execution.
5. The archive contains the current `hvf_web/snapshot.json` only as a last-known-good boot cache. On startup
   and at a bounded cadence, the web tier verifies the current Supabase object and atomically refreshes that
   one local fallback. The admin Refresh button dispatches the external worker; it does not scan on IONOS.

The package deliberately excludes local credentials and user data, development/test files, caches, logs,
working notes, source-control metadata, desktop launchers and historical zip archives.

On an existing deployment, retain `.env`, `data/` and `.venv_linux/` when installing a new release. The
packaged `.htaccess` explicitly denies public access to those paths and to server-side source/configuration.
It also denies direct access to internal JSON, logs, archives, documents and generated guides; those remain
available only through the authenticated Flask API where applicable.
