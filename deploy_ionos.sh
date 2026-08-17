#!/usr/bin/env bash
# =====================================================================================================
# File:         deploy_ionos.sh
# Created:      2026-08-15  (Claude, user: "I'm waiting on you for a sh file to run")
#
# Build and deploy the production package to IONOS (https://www.squeezescanner.cloud/).
#
# Pushing to GitHub updates the Actions automation immediately, but the WEBSITE only changes when this
# runs — see IONOS_DEPLOYMENT.md.
#
# Usage:
#   IONOS_HOST=… IONOS_USER=… IONOS_DIR=… ./deploy_ionos.sh            # build, upload, extract, verify
#   ./deploy_ionos.sh --dry-run                                        # build + checks only, no upload
#   VERIFY_STRING='">125 trades"' ./deploy_ionos.sh                    # also assert the live page contains it
#
# Settings (environment variables):
#   IONOS_HOST     required  SSH/SFTP host for the hosting account
#   IONOS_USER     required  SSH username
#   IONOS_DIR      required  ABSOLUTE path of the domain directory on the server
#                            (don't know it? run: ./deploy_ionos.sh --find-dir)
#   IONOS_PORT     optional  SSH port (default 22)
#   IONOS_KEY      optional  path to a private key; omit to use your default agent/key
#   VERIFY_STRING  optional  literal string that must appear in the deployed page
#   SKIP_BACKUP    optional  set to 1 to skip the server-side backup tarball
#
# Put the three required values in a local, gitignored file and source it, e.g.
#   echo 'export IONOS_HOST=… IONOS_USER=… IONOS_DIR=…' > .ionos.env   # .env* is gitignored
#   source .ionos.env && ./deploy_ionos.sh
#
# SFTP-only accounts (no shell): this script needs SSH. Without it, extract the zip locally and upload
# the contents, then chmod cgi-bin/app.py to 755 by hand — uploading extracted files does not preserve
# the Unix mode the packager sets, and missing it makes every /api/* request return 500.
# =====================================================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Local, gitignored settings (matched by the existing *.env rule) so the host and username never enter
# the repo. Anything already exported wins, so CI or a one-off override still works.
if [ -f "$ROOT/.ionos.env" ]; then
  # shellcheck disable=SC1091
  set -a; . "$ROOT/.ionos.env"; set +a
fi

ZIP="dist/ionos/squeeze-scanner-ionos.zip"
SITE="https://www.squeezescanner.cloud"
DRY_RUN=0
FIND_DIR=0
[ "${1:-}" = "--dry-run" ]  && DRY_RUN=1
[ "${1:-}" = "--find-dir" ] && FIND_DIR=1

PY=python
[ -x ".venv/Scripts/python.exe" ] && PY=".venv/Scripts/python.exe"
[ -x ".venv/bin/python" ]         && PY=".venv/bin/python"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# --- 1. build ----------------------------------------------------------------------------------------
say "Building the production package"
"$PY" build_ionos_package.py
[ -f "$ZIP" ] || die "expected $ZIP to exist after the build"

# --- 2. safety check ----------------------------------------------------------------------------------
# Extracting over a live release must never touch the host's credentials, runtime state or virtualenv.
# The packager already excludes them; this asserts it rather than trusting it, because `unzip -o`
# overwrites whatever the archive happens to contain.
say "Checking the archive cannot clobber .env / data/ / .venv_linux/"
"$PY" - "$ZIP" <<'PYEOF'
import sys, zipfile
names = zipfile.ZipFile(sys.argv[1]).namelist()
bad = [n for n in names if n == ".env" or n.startswith(("data/", ".venv_linux/"))]
if bad:
    sys.exit(f"ERROR: archive would overwrite protected paths: {bad}")
print(f"  OK - {len(names)} entries, none targeting protected paths")
PYEOF

if [ "$DRY_RUN" = "1" ]; then
  say "Dry run - built and checked, nothing uploaded"
  ls -lh "$ZIP"
  exit 0
fi

# --- 3. config ------------------------------------------------------------------------------------------
: "${IONOS_HOST:?set IONOS_HOST (see the header of this script)}"
: "${IONOS_USER:?set IONOS_USER}"
PORT="${IONOS_PORT:-22}"

# -4 forces IPv4. The host resolves to BOTH an AAAA (2001:8d8:1001:9000::2) and an A record
# (217.160.137.2), ssh prefers the IPv6 address, and this machine has no IPv6 route — so it fails with
# "Network is unreachable" while the site itself is perfectly reachable over HTTPS. That is not transient
# and survives a reboot, because it is just how DNS and the local routing table interact (2026-08-16).
SSH_OPTS=(-4 -p "$PORT")
SCP_OPTS=(-4 -P "$PORT")
CTL=""
if [ -n "${IONOS_KEY:-}" ]; then
  # Key auth: every connection is unattended, so multiplexing buys nothing. Skip it — ControlMaster
  # sockets are unreliable under Git Bash on Windows and failed here with "Failed to connect to new
  # control master" AFTER the upload had succeeded, leaving a zip on the server that was never extracted.
  SSH_OPTS+=(-o BatchMode=yes -i "$IONOS_KEY")
  SCP_OPTS+=(-o BatchMode=yes -i "$IONOS_KEY")
else
  # Password auth: reuse ONE authenticated connection so the password is typed once rather than three
  # times. Set SSH_MULTIPLEX=0 to disable if your ssh build cannot hold a control socket.
  if [ "${SSH_MULTIPLEX:-1}" = "1" ]; then
    CTL="${TMPDIR:-/tmp}/ionos-deploy-$$.sock"
    SSH_OPTS+=(-o ControlMaster=auto -o ControlPath="$CTL" -o ControlPersist=120)
    SCP_OPTS+=(-o ControlMaster=auto -o ControlPath="$CTL" -o ControlPersist=120)
  fi
fi
cleanup() {
  [ -n "$CTL" ] && ssh -O exit -o ControlPath="$CTL" "${IONOS_USER}@${IONOS_HOST}" 2>/dev/null || true
}
trap cleanup EXIT

# --find-dir: locate the domain directory instead of deploying. The live release is wherever the existing
# .htaccess and cgi-bin/app.py already sit, so look for that pair rather than guessing at IONOS's layout.
if [ "$FIND_DIR" = "1" ]; then
  say "Looking for the domain directory on ${IONOS_HOST}"
  ssh "${SSH_OPTS[@]}" "${IONOS_USER}@${IONOS_HOST}" bash -s <<'REMOTE'
set -u
echo "  home: $(pwd)"
echo "  --- directories containing BOTH .htaccess and cgi-bin/app.py (this is what IONOS_DIR should be):"
found=0
while IFS= read -r f; do
  d="$(dirname "$f")"
  if [ -f "$d/cgi-bin/app.py" ]; then echo "      $d"; found=1; fi
done < <(find ~ / -maxdepth 4 -name .htaccess -not -path '*/.venv_linux/*' 2>/dev/null)
[ "$found" = "1" ] || echo "      (none found - the site may live outside the searched depth; try: find / -name 'app.py' -path '*cgi-bin*' 2>/dev/null)"
echo "  --- top level of home:"
ls -la ~ 2>/dev/null | head -25
REMOTE
  exit 0
fi

: "${IONOS_DIR:?set IONOS_DIR - the absolute domain directory on the server (run: ./deploy_ionos.sh --find-dir)}"

# Fail fast if SSH is unreachable. Without this the upload sits there until something times out minutes
# later, which on 2026-08-16 produced a "deploy" that was reported as running and had in fact gone nowhere.
# Checked separately from the transfer so the message says WHICH thing is broken: HTTPS to the site can be
# perfectly healthy while port 22 is blocked.
say "Checking ${IONOS_HOST}:${PORT} is reachable"
if ! ssh "${SSH_OPTS[@]}" -o ConnectTimeout=15 "${IONOS_USER}@${IONOS_HOST}" true 2>/dev/null; then
  echo "  cannot open an SSH session on port ${PORT}."
  printf '  the site itself is %s over HTTPS\n' \
    "$(curl -s -o /dev/null --max-time 20 -w '%{http_code}' "$SITE/" || echo unreachable)"
  die "SSH unreachable — nothing uploaded, the live release is untouched. Check the network/VPN and retry."
fi
echo "  reachable"

say "Deploying to ${IONOS_USER}@${IONOS_HOST}:${IONOS_DIR} (port ${PORT})"
if [ "${ASSUME_YES:-0}" = "1" ]; then
  echo "  ASSUME_YES=1 - proceeding without the confirmation prompt"
else
  read -r -p "Overwrite the live release? [y/N] " reply
  case "$reply" in [yY]*) ;; *) die "aborted by user" ;; esac
fi

# --- 4. upload + extract ----------------------------------------------------------------------------------
say "Uploading"
scp "${SCP_OPTS[@]}" "$ZIP" "${IONOS_USER}@${IONOS_HOST}:~/squeeze-scanner-ionos.zip"

say "Extracting on the server"
# `unzip -o` overwrites archive members only; it never deletes files that have since been removed from
# the repo, so stale server-side files persist until cleaned by hand. The chmod is belt-and-braces: the
# packager stamps cgi-bin/app.py 0755 inside the zip and server-side unzip honours it.
ssh "${SSH_OPTS[@]}" "${IONOS_USER}@${IONOS_HOST}" bash -s -- "$IONOS_DIR" "${SKIP_BACKUP:-0}" <<'REMOTE'
set -euo pipefail
DIR="$1"; SKIP_BACKUP="$2"
cd "$DIR" || { echo "ERROR: no such directory: $DIR" >&2; exit 1; }
if [ "$SKIP_BACKUP" != "1" ]; then
  BACKUP=~/"backup-$(date +%Y%m%d-%H%M%S).tar.gz"
  echo "  backing up current release -> $BACKUP"
  # The tarball contains .env and data/, so it must not be group/world readable. umask on this host
  # would otherwise leave it 644 under www-data.
  ( umask 077; tar czf "$BACKUP" --exclude=.venv_linux . 2>/dev/null ) || echo "  (backup reported warnings; continuing)"
  chmod 600 "$BACKUP" 2>/dev/null || true
fi
# NEVER overwrite a snapshot the host already has. The package carries hvf_web/snapshot.json as a
# last-known-good BOOT cache for a fresh install; on an upgrade it is actively harmful. It replaces
# current data with whatever the build machine happened to hold (here, 2026-08-12) while the sidecar still
# describes the newer object -- so every subsequent request sees a digest mismatch and re-downloads ~830KB
# from Supabase Storage. Under CGI there is no process memory to damp that, and several deploys in a day
# was enough to exhaust the Storage egress allowance and start getting HTTP 402 on the download, leaving
# the site pinned to five-day-old data (2026-08-17).
if [ -f hvf_web/snapshot.json ]; then
  echo "  keeping the host's existing snapshot cache (not shipping the boot copy over it)"
  unzip -o ~/squeeze-scanner-ionos.zip -x 'hvf_web/snapshot.json' >/dev/null
else
  echo "  no snapshot on the host — installing the packaged boot cache"
  unzip -o ~/squeeze-scanner-ionos.zip >/dev/null
fi
chmod 755 cgi-bin/app.py
rm -f ~/squeeze-scanner-ionos.zip
echo "  extracted; cgi-bin/app.py is $(stat -c '%a' cgi-bin/app.py)"
REMOTE

# --- 5. verify ------------------------------------------------------------------------------------------
say "Verifying the live site"
fail=0

code=$(curl -s -o /tmp/ionos_status.txt -w '%{http_code}' "$SITE/api/status" || true)
if [ "$code" = "200" ] && grep -q 'generated_utc' /tmp/ionos_status.txt; then
  echo "  /api/status  200 OK  $(cat /tmp/ionos_status.txt)"
else
  echo "  /api/status  FAILED (http $code) - the CGI adapter is the usual cause; check cgi-bin/app.py is 755"
  fail=1
fi

# The package ships hvf_web/snapshot.json as a BOOT CACHE, and the local copy is usually older than what
# the nightly Scanner job has published. The first request after extraction therefore serves stale data
# until the web tier verifies the Supabase object and advances its cache. That self-heals in seconds, but
# report it rather than let a deploy look like it rolled the Scanner backwards (observed 2026-08-15).
LOCAL_GEN=$("$PY" -c "import json,io;print(json.load(io.open('hvf_web/snapshot.json',encoding='utf-8')).get('generated_utc') or '')" 2>/dev/null || echo "")
for _ in 1 2 3 4 5; do
  LIVE_GEN=$(sed -n 's/.*"generated_utc":"\([^"]*\)".*/\1/p' /tmp/ionos_status.txt)
  [ -n "$LIVE_GEN" ] && [ "$LIVE_GEN" != "$LOCAL_GEN" ] && break
  echo "  snapshot     serving the shipped boot cache ($LIVE_GEN) - waiting for the Supabase pull..."
  sleep 5
  curl -s -o /tmp/ionos_status.txt "$SITE/api/status" || true
done
if [ -n "$LIVE_GEN" ] && [ "$LIVE_GEN" = "$LOCAL_GEN" ]; then
  echo "  snapshot     WARNING: still serving the shipped boot cache ($LIVE_GEN)."
  echo "               Not fatal - the next hosted refresh advances it - but check SUPABASE_SCANNER_WEB_KEY is set on the host."
else
  echo "  snapshot     live is $LIVE_GEN (shipped boot cache was $LOCAL_GEN)"
fi

size=$(curl -s "$SITE/" -o /tmp/ionos_index.html -w '%{size_download}' || echo 0)
if [ "$size" -gt 100000 ]; then
  echo "  /            served ${size} bytes"
else
  echo "  /            SUSPICIOUS - only ${size} bytes"
  fail=1
fi

if [ -n "${VERIFY_STRING:-}" ]; then
  if grep -qF "$VERIFY_STRING" /tmp/ionos_index.html; then
    echo "  marker       found: $VERIFY_STRING"
  else
    echo "  marker       NOT FOUND: $VERIFY_STRING - the old build may still be cached or served"
    fail=1
  fi
fi

[ "$fail" = "0" ] || die "deployed, but verification failed - check the output above before trusting the release"
say "Done - $SITE is serving the new build"
