# Security review & recommendations — Squeeze Scanner web app

_Reviewer: Claude · 2026-07-28 · scope: `hvf_web/` (server, auth, credential store, DB access) + deployment (Flask dev server behind an ngrok public tunnel). Covers ticket P-10 "review the full delivery and make recommendations to further secure platform, data etc."_

This is a **recommendations** document — nothing here changes behaviour. Items are ranked by risk so they can be actioned in order. The app is currently reachable publicly at the ngrok URL, which raises the priority of the network-facing items.

---

## What is already done well (keep it)

- **Passwords**: PBKDF2-HMAC-SHA256, **200,000 iterations**, per-user 16-byte salt (`web_users._hash_pwd`); verification uses `secrets.compare_digest` (constant-time). Accounts seed **LOCKED** with an unusable random password — no credential is ever committed.
- **Per-user / app secrets** (IG API key, etc.): encrypted at rest with **Fernet** (`data/.web_users.key`); the key and the store are **gitignored** and never enter git. Plaintext secrets are never returned to the browser (masked last-4 only).
- **SQL**: all queries go through `db_pool.run` with **named prepared-statement parameters** — no string-formatted SQL / injection surface was found.
- **Session tokens rotate on password change** (derived from the password hash), so a reset invalidates every old session.
- **Password reset**: email-gated, one-time **hashed** code, 10-minute expiry, **5-attempt limit**, single-use; responses are **generic** so accounts/emails can't be enumerated. Password-change notification email with a "not you?" warning.
- **Secret scanning**: a `gitleaks` pre-commit hook blocks committing secrets.
- **Authorization**: admin-only endpoints gated by `web_users.is_admin`; logged-out responses are obfuscated (`LIMITED`); per-user IG credentials are isolated (`ig_shim.session_for` refuses to trade on another user's account).
- Debug mode is **off** (`app.run(debug=False)`) — no Werkzeug debugger/PIN exposure.

---

## HIGH — action first (network-facing, the app is public)

1. **No brute-force protection on `/api/login`.** `api_login` verifies unlimited attempts with no per-IP or per-account throttling/lockout (the *reset-code* flow is limited, but password login is not). PBKDF2's 200k iterations slow this but do not stop sustained online guessing, especially now the login is public via ngrok.
   - **Do**: add per-IP + per-account rate limiting with exponential backoff and a temporary lockout after N failures; log and alert on bursts. A small in-memory counter (keyed by IP+name, with a cooldown) is enough for a single-process app.

2. **Werkzeug development server exposed to the public.** `app.run(host="0.0.0.0", …)` is the Flask/Werkzeug dev server (it prints "do not use in a production deployment") and it binds **all interfaces** (reachable on the LAN at `192.168.1.171:5057`, not just localhost) as well as through ngrok.
   - **Do**: run behind a production WSGI server — **`waitress`** is the easiest on Windows (`from waitress import serve; serve(app, host="127.0.0.1", port=5057)`); and **bind to `127.0.0.1`** only (ngrok connects locally), removing the LAN exposure.

3. **No HTTP security headers.** No `X-Content-Type-Options`, `X-Frame-Options`, `Content-Security-Policy`, `Referrer-Policy` or HSTS are set.
   - **Do**: add a Flask `after_request` that sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` (anti-clickjacking — the app is standalone, never framed), `Referrer-Policy: no-referrer`, a **Content-Security-Policy** (start report-only, then enforce), and — since ngrok terminates TLS — `Strict-Transport-Security`. Test CSP against the inline scripts/styles in `index.html` before enforcing.

---

## MEDIUM

4. **Session tokens don't expire and can't be revoked individually.** The token is a deterministic `sha256(name:pwd_hash:salt)`; a leaked token stays valid until the user *changes their password*, and there is no idle/absolute timeout.
   - **Do**: move to random per-session tokens stored server-side with an absolute + idle expiry and a "sign out everywhere" control. This also lets you revoke one device without a password change.

5. **Encryption key and ciphertext share the same OneDrive folder.** `data/.web_users.key` (the Fernet key) and `data/web_users.json` (the encrypted secrets) both live under `…\OneDrive\…`. If the OneDrive account is compromised, the attacker has **both** the key and the ciphertext → all stored IG credentials are decryptable. Gitignore protects git, not OneDrive sync.
   - **Do**: move the key **off OneDrive** (e.g. `%LOCALAPPDATA%` or Windows DPAPI / a secrets manager), or wrap it with an OS-level key. At minimum, keep an offline backup of the key (losing it makes secrets unrecoverable) somewhere separate from the ciphertext.

6. **No rate-limiting on expensive/public endpoints.** Public routes like `/api/fundamentals/<ticker>` (Yahoo fetch), `/api/refresh`, and the pricewin image renderer can be hit repeatedly through the public URL → resource/DoS and third-party-quota abuse.
   - **Do**: apply light per-IP rate limits to public + expensive endpoints; keep the existing server-side caches.

7. **Tunnel is world-reachable by URL alone.** The ngrok subdomain is a reserved name and the only thing gating access before login. Consider defence-in-depth on the tunnel: ngrok OAuth/basic-auth or an IP allowlist in `ngrok-eah.yml`, so unauthenticated visitors can't even reach the login/API surface.

---

## LOW / housekeeping

8. **KDF**: 200k PBKDF2-SHA256 is fine; OWASP-2023 suggests ≥210k, and a memory-hard KDF (**Argon2id** or scrypt) is stronger. Migrate opportunistically on next password set.
9. **CSRF**: auth is via the `X-Auth` header (not cookies), so CSRF risk is low — **keep header-based auth**; if you ever move to cookies, add CSRF tokens + `SameSite`/`Secure`/`HttpOnly`.
10. **Dependency hygiene**: pin and periodically CVE-audit `flask`/`werkzeug`, `cryptography`, `yfinance`, `pg8000` (e.g. `pip-audit`).
11. **Log hygiene**: audit logging is good; confirm tokens, passwords and decrypted secrets never reach logs (login logs the IP, which is fine).
12. **Backups**: the Supabase backup task (a separate P-25 ticket) matters for security/availability too — an encrypted, off-site backup of `data/` (store + key handled separately per #5) and the DB.

---

## Suggested order of work
HIGH #1 (login throttling) → #3 (headers, quick + safe) → #2 (waitress + bind localhost) → MEDIUM #5 (move the key off OneDrive) → #4 (session model) → #6/#7 (rate-limit + tunnel gating). Items #1, #3 and #5 are the highest value for the least effort.
