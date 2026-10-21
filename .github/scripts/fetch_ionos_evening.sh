#!/usr/bin/env bash
# fetch_ionos_evening.sh FILE DEST_DIR
#
# Copy /home/www/data/evening/FILE from IONOS into DEST_DIR over SSH. Scanner Snapshot Publish keeps the
# evening snapshot and its summary there, outside the web root (owner 2026-10-21: the repository is PUBLIC
# and is for code, not reports -- no GitHub artifact). Only a .json name is accepted, it lands under a
# temporary name and must parse before it replaces DEST_DIR/FILE. Exits 3 when the host has no such file.
# Needs IONOS_HOST, IONOS_USER, IONOS_SSH_KEY; IONOS_PORT defaults to 22.
set -euo pipefail
file="$1"; dest="$2"
[[ "$file" =~ ^[a-z_]+\.json$ ]] || { echo "refusing file name '$file'" >&2; exit 2; }
key="${RUNNER_TEMP:-/tmp}/ionos_evening_key"
printf '%s\n' "$IONOS_SSH_KEY" > "$key"; chmod 600 "$key"
src="/home/www/data/evening/$file"
if ! ssh -i "$key" -p "${IONOS_PORT:-22}" -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
       "${IONOS_USER}@${IONOS_HOST}" "test -f $src && cat $src" > "$dest/$file.new"; then
  rm -f "$dest/$file.new" "$key"; echo "no $src on IONOS" >&2; exit 3
fi
rm -f "$key"
python -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" "$dest/$file.new"
mv -f "$dest/$file.new" "$dest/$file"
echo "$file from IONOS: $(stat -c %s "$dest/$file") bytes"
