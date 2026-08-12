# IONOS deployment

Build the production-only archive with:

```powershell
python build_ionos_package.py
```

Before switching traffic:

1. Run `python migrate_runtime_state_to_supabase.py --apply` from a trusted machine and confirm every store
   reports `migrated` or `verified`. The command retains every local original.
2. Configure `SUPABASE_USER`, `SUPABASE_DB_PASSWORD`, `APP_SECRET_KEY` and `WEB_USERS_FERNET_KEY` as IONOS
   environment secrets. `WEB_USERS_FERNET_KEY` must contain the existing key so current encrypted user/IG
   fields remain decryptable; never upload `data/.web_users.key` into the web root.
3. For IONOS Linux Web Hosting, extract the archive into the domain directory. The package's `.htaccess`
   serves static HTML directly and rewrites `/api/*` to `cgi-bin/app.py`; keep that adapter executable
   (`chmod 755 cgi-bin/app.py`). It uses `.venv_linux`, which can be created/refreshed with
   `python3 -m venv .venv_linux` followed by `.venv_linux/bin/python -m pip install -r requirements.txt`.
   `wsgi:application` remains available for VPS, Cloud Server or another persistent WSGI host.
4. Keep scheduled scans, order bridging and other background jobs in their existing scheduler. WSGI may run
   several worker processes, so starting an order loop in every web worker would risk duplicate execution.
5. The archive contains the current `hvf_web/snapshot.json` only as a rebuildable boot cache. Use the existing
   refresh endpoint/job to replace it after deployment.

The package deliberately excludes local credentials and user data, development/test files, caches, logs,
working notes, source-control metadata, desktop launchers and historical zip archives.

On an existing deployment, retain `.env`, `data/` and `.venv_linux/` when installing a new release. The
packaged `.htaccess` explicitly denies public access to those paths and to server-side source/configuration.
