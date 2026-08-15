# Security review & recommendations — Squeeze Scanner web app

_Reviewer: Claude · first issued 2026-07-28 · **re-reviewed 2026-08-15** after the move to IONOS._

_Scope: `hvf_web/` (server, auth, credential store, DB access) + deployment. Covers ticket P-10 "review the
full delivery and make recommendations to further secure platform, data etc."_

> **2026-08-15 — the deployment changed and this document was re-reviewed against it.** The app is no
> longer a Flask dev server on a laptop behind an ngrok tunnel. It is hosted on **IONOS Linux Web Hosting**
> at **https://www.squeezescanner.cloud/**: Apache serves the static HTML directly and rewrites `/api/*`
> into a **CGI/WSGI adapter** (`cgi-bin/app.py`) that imports the Flask app. Two findings below are now
> obsolete, one is re-scoped to the development machine, and **the fix for the top finding has changed**
> because CGI runs a fresh process per request. Findings were re-verified in code, not assumed.

This is a **recommendations** document — nothing here changes behaviour. Items are ranked by risk.

---

## What is already done well (keep it)

- **Passwords**: PBKDF2-HMAC-SHA256, **200,000 iterations**, per-user 16-byte salt (`web_users._hash_pwd`);
  verification uses `secrets.compare_digest` (constant-time). Accounts seed **LOCKED** with an unusable
  random password — no credential is ever committed.
- **Per-user / app secrets** (IG API key, etc.): encrypted at rest with **Fernet**. On IONOS the key comes
  from the `WEB_USERS_FERNET_KEY` environment secret (`web_users.py:771`) and
  `data/.web_users.key` is deliberately **never uploaded into the web root**. Plaintext secrets are never
  returned to the browser (masked last-4 only).
- **SQL**: all queries go through `db_pool.run` with **named prepared-statement parameters** — no
  string-formatted SQL / injection surface was found.
- **Session tokens rotate on password change** (derived from the password hash), so a reset invalidates
  every old session.
- **Password reset**: email-gated, one-time **hashed** code, 10-minute expiry, **5-attempt limit**,
  single-use; responses are **generic** so accounts/emails can't be enumerated. Password-change
  notification email with a "not you?" warning.
- **Secret scanning**: a `gitleaks` pre-commit hook blocks committing secrets.
- **Authorization**: admin-only endpoints gated by `web_users.is_admin`; logged-out responses are
  obfuscated (`LIMITED`); per-user IG credentials are isolated (`ig_shim.session_for` refuses to trade on
  another user's account).
- **NEW (2026-08-15) — the `.htaccess` shipped with the IONOS package is a genuine defence layer.** It
  disables directory indexes, hard-denies `data/`, `docs/` and `.venv_linux/`, blocks direct browser
  requests for `.py/.sql/.md/.txt/.key/.env/.json/.log/.zip/.docx/.pkl` (scoped via `THE_REQUEST` so
  Flask can still read them internally), and refuses `/cgi-bin/` as a second public API prefix. Keep this
  file under review whenever a new server-side file type is added.
- **NEW — the production server is no longer the Werkzeug dev server** (see obsolete item #2).

---

## HIGH — action first

1. **No brute-force protection on `/api/login`.** Verified still open: `api_login` accepts unlimited
   attempts with no per-IP or per-account throttling or lockout. The *reset-code* flow is limited;
   password login is not. PBKDF2 slows guessing but does not stop it, and the login is now on a **stable,
   discoverable public domain** rather than a random tunnel hostname — see obsolete item #7.
   - ⚠️ **The 2026-07-28 advice is no longer valid.** It said "a small in-memory counter … is enough for a
     single-process app". Under CGI **every request is a fresh Python process**, so an in-memory counter
     resets on each attempt and provides *zero* protection.
   - **Do**: persist the counter. A small `login_attempts` table (or `app_config`-style rows) in Supabase
     keyed by IP + name, with attempt count, first-attempt timestamp and a lockout-until stamp; reject
     early when locked, clear on success, and alert on bursts. Cheap: it is one indexed read/write on an
     endpoint that should be low-volume.

2. **No HTTP security headers.** Re-verified 2026-08-15 — still completely absent. There is no
   `after_request` handler in `hvf_web/server.py` and `.htaccess` sets no `Header` directives.
   - ⚠️ **The fix location has changed.** Apache serves the main document (`index.html`) **directly**;
     only `/api/*` reaches Flask. Headers added in Flask would therefore protect the JSON API but **not
     the page users actually load** — clickjacking and CSP protections would be missing exactly where
     they matter.
   - **Do**: set them in `.htaccess` (`Header always set …`) so they cover static and API responses
     alike: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` (the app is standalone, never
     framed), `Referrer-Policy: no-referrer`, `Strict-Transport-Security` (IONOS terminates TLS), and a
     **Content-Security-Policy**. Start CSP report-only: `index.html` is one large inline `<script>` plus
     inline styles, so a naive `script-src 'self'` will break the entire page — it needs a nonce or hash,
     or an explicit, deliberate `'unsafe-inline'` decision.

---

## MEDIUM

3. **Session tokens don't expire and can't be revoked individually.** The token is a deterministic
   `sha256(name:pwd_hash:salt)`; a leaked token stays valid until the user *changes their password*, and
   there is no idle or absolute timeout.
   - **Do**: move to random per-session tokens stored server-side with absolute + idle expiry and a "sign
     out everywhere" control. Server-side storage is now unavoidable anyway — see #1, CGI has no process
     memory to keep sessions in.

4. **No rate-limiting on expensive/public endpoints.** Public routes such as
   `/api/fundamentals/<ticker>` (Yahoo fetch), `/api/refresh` and the pricewin image renderer can be hit
   repeatedly through the public domain → resource and third-party-quota abuse.
   - **Do**: light per-IP limits, persisted as in #1. Note the CGI corollary: **the in-process caches
     (`_PERF_CACHE`, `_SQA_CACHE`, the PNG cache) no longer survive between requests on IONOS**, so an
     abusive caller re-does the expensive work every time rather than hitting a warm cache. That makes
     rate-limiting more important post-move, not less, and is worth measuring as a performance question
     in its own right.

5. **Encryption key and ciphertext share the same OneDrive folder — now a *development machine* issue.**
   Re-scoped 2026-08-15: on IONOS the key is supplied via `WEB_USERS_FERNET_KEY` and the key file is never
   uploaded, so production no longer co-locates key and ciphertext. On this laptop, `data/.web_users.key`
   and `data/web_users.json` still both sit under `…\OneDrive\…`; if the OneDrive account is compromised
   an attacker has both, and gitignore protects git, not OneDrive sync.
   - **Do**: move the local key off OneDrive (`%LOCALAPPDATA%`, DPAPI, or a secrets manager). Keep an
     offline backup of the key somewhere separate from the ciphertext — losing it makes secrets
     unrecoverable.

6. **Secrets are now spread across three stores.** GitHub Secrets (Actions), IONOS environment variables
   (web tier) and the encrypted Supabase `app_secrets` document all hold overlapping values, with
   `SUPABASE_SCANNER_PUBLISH_KEY` / `SUPABASE_SCANNER_WEB_KEY` deliberately excluded from the third
   because they bypass RLS (`migrate_secrets_to_supabase._BOOTSTRAP`).
   - **Do**: keep the `_BOOTSTRAP` split as designed, but document a **rotation runbook** — rotating the
     IG password or a Supabase key currently means remembering all three places. A missed one fails
     silently, and for the RLS-bypassing Storage keys a stale copy left behind is a live credential.

---

## LOW / housekeeping

7. **KDF**: 200k PBKDF2-SHA256 is fine; OWASP-2023 suggests ≥210k, and a memory-hard KDF (**Argon2id** or
   scrypt) is stronger. Migrate opportunistically on next password set.
8. **CSRF**: auth is via the `X-Auth` header (not cookies), so CSRF risk is low — **keep header-based
   auth**; if you ever move to cookies, add CSRF tokens + `SameSite`/`Secure`/`HttpOnly`.
9. **Dependency hygiene**: pin and periodically CVE-audit `flask`/`werkzeug`, `cryptography`, `yfinance`,
   `pg8000` (e.g. `pip-audit`). Note the hosted `.venv_linux` is refreshed manually, so a patched
   dependency does not reach production until someone rebuilds it.
10. **Log hygiene**: audit logging is good; confirm tokens, passwords and decrypted secrets never reach
    logs (login logs the IP, which is fine).
11. **Backups**: the daily Supabase backup job runs at 23:30 UTC with a 90-day artifact retention. Also
    keep an encrypted, off-site backup of `data/` (store and key handled separately per #5).

---

## Obsolete since the IONOS move (kept so the change is visible, not silently dropped)

- **~~Werkzeug development server exposed to the public~~** (was HIGH #2). Production is Apache +
  `cgi-bin/app.py`; `app.run(host="0.0.0.0", …)` now sits inside `if __name__ == "__main__":` and executes
  only for a local development instance. *Residual, low:* that local instance still binds all interfaces,
  so a dev run is reachable across the LAN — pass `host="127.0.0.1"` if that matters to you.
- **~~Tunnel is world-reachable by URL alone~~** (was MEDIUM #7). There is no ngrok tunnel to gate. The
  replacement exposure is a permanent, guessable, indexable public domain, which is *more* discoverable
  than a reserved tunnel hostname — this is precisely why HIGH #1 is ranked first.

---

## Suggested order of work

HIGH #1 (persisted login throttling) → #2 (headers in `.htaccess`, quick and safe — CSP report-only
first) → MEDIUM #5 (move the local key off OneDrive) → #4 (rate limits, which share #1's storage) → #3
(session model) → #6 (rotation runbook). Items #1, #2 and #5 remain the highest value for the least
effort; #1 and #4 should be built together since they need the same persistent counter.
