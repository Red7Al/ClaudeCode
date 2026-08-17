#!/bin/bash
# ======================================================================================================================
# File:         hvf_web/snapshot-channel.sh   (installed on the IONOS web host as ~/bin/snapshot-channel.sh)
# Author:       Claude
# Created:      2026-08-17
#
# The ONLY thing the GitHub Actions snapshot key is allowed to do on this host.
#
# WHY IT EXISTS. Supabase Storage is the single point of failure that froze the live Scanner on 12 August data and
# left the order bridge blind: both fetched the snapshot from it, and its free-tier egress ran out (HTTP 402 on every
# request, including a bare bucket listing). This gives both a path through the web host we already pay for, so a
# Storage outage or an exhausted quota can no longer stop either.
#
# WHY A FORCED COMMAND. Automating the push means putting an SSH key into GitHub secrets, which is a real widening of
# the blast radius -- anything that can read those secrets could otherwise get a shell on the production web host.
# The key is therefore pinned in authorized_keys to this script:
#
#   command="$HOME/bin/snapshot-channel.sh",no-port-forwarding,no-agent-forwarding,no-pty,no-X11-forwarding ssh-ed25519 AAAA...
#
# SSH then ignores whatever the client asks to run and executes only this, passing the request in
# SSH_ORIGINAL_COMMAND. A stolen key can replace or read a snapshot. It cannot read .env, touch cgi-bin, or open a
# shell.
#
# PROTOCOL (gzip on the wire both ways -- the snapshot is ~1.3 MB of JSON that compresses about 10x):
#   get  -> writes the current snapshot, gzipped, to stdout
#   put  -> reads a gzipped snapshot from stdin, validates it, installs it atomically
#
# A put NEVER overwrites a good snapshot with a bad one: the payload has to gunzip, parse as JSON, and satisfy the
# same count == len(records) and every-record-has-a-ticker checks the Python store applies, before anything is moved
# into place. The previous file is kept as .bak-<timestamp>.
#
# Version History:
# ----------------------------------------------------------------------------------------------------------------------
# 1.0.0   2026-08-17  Claude      Initial build.
# ======================================================================================================================
set -euo pipefail

SNAPSHOT="$HOME/squeezescanner/hvf_web/snapshot.json"
ACTION="${SSH_ORIGINAL_COMMAND:-}"

log() { echo "snapshot-channel: $*" >&2; }

case "$ACTION" in
  get)
    if [ ! -s "$SNAPSHOT" ]; then
      log "no snapshot on this host"
      exit 3
    fi
    gzip -c "$SNAPSHOT"
    ;;

  put)
    TMP="$(mktemp "${SNAPSHOT}.incoming.XXXXXX")"
    # shellcheck disable=SC2064
    trap "rm -f '$TMP'" EXIT

    # Decompress explicitly rather than trusting the sender: a truncated upload fails here, not halfway
    # through overwriting the live file.
    if ! gunzip -c > "$TMP"; then
      log "payload is not valid gzip"
      exit 4
    fi

    # Same invariants the Python store enforces (scanner_snapshot_store.validate_snapshot). A snapshot whose
    # count disagrees with its records, or that carries a record without a ticker, is corrupt -- and serving
    # corrupt data would be worse than serving yesterday's.
    if ! python3 - "$TMP" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    d = json.load(fh)
records = d.get("records")
if not isinstance(records, list):
    raise SystemExit("records is not a list")
if not d.get("generated_utc"):
    raise SystemExit("missing generated_utc")
if d.get("count") != len(records):
    raise SystemExit(f"count {d.get('count')} != {len(records)} records")
if not all(isinstance(r.get("ticker"), str) and r.get("ticker") for r in records):
    raise SystemExit("a record has no ticker")
print(f"validated {d['generated_utc']} {d['count']} records")
PY
    then
      log "payload failed validation - the live snapshot is untouched"
      exit 5
    fi

    # Refuse to go backwards. A late-finishing rebuild must not overwrite a newer one, which is a real risk
    # once several scans can run and push independently.
    if [ -s "$SNAPSHOT" ] && ! python3 - "$SNAPSHOT" "$TMP" <<'PY'
import json, sys
def gen(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh).get("generated_utc") or ""
cur, new = gen(sys.argv[1]), gen(sys.argv[2])
raise SystemExit(0 if new >= cur else f"incoming {new} is older than installed {cur}")
PY
    then
      log "incoming snapshot is older than the one installed - keeping the newer one"
      exit 6
    fi

    cp -p "$SNAPSHOT" "${SNAPSHOT}.bak-$(date -u +%Y%m%dT%H%M%SZ)" 2>/dev/null || true
    chmod 644 "$TMP"
    mv "$TMP" "$SNAPSHOT"          # atomic within the same filesystem
    trap - EXIT
    log "installed"

    # Keep only the three most recent backups; this runs on shared webspace, not a NAS.
    ls -1t "${SNAPSHOT}".bak-* 2>/dev/null | tail -n +4 | xargs -r rm -f
    ;;

  *)
    log "refused: this key may only run 'get' or 'put' (asked for '${ACTION}')"
    exit 2
    ;;
esac
