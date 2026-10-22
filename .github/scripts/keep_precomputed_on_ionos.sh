#!/usr/bin/env bash
# keep_precomputed_on_ionos.sh
#
# Copy every document this job stored in web_json_store and also wrote to $PRECOMPUTED_OUT (web_store,
# owner 2026-10-22: "the best settings can be in summary files") to $IONOS_DIR/data/precomputed/<key>.json,
# where the website reads it instead of fetching it from Supabase on every request. data/ is never
# overwritten by deploy and the site answers 403 for it; files are 0640, group www-data, so the web
# process can read them and nobody else. Each lands under a temporary name and replaces the live copy only
# when its size matches and it parses as JSON. Never a GitHub artifact (CLAUDE.md).
# Needs IONOS_HOST, IONOS_USER, IONOS_DIR, IONOS_SSH_KEY; IONOS_PORT defaults to 22.
set -euo pipefail
shopt -s nullglob
files=("${PRECOMPUTED_OUT:?}"/*.json)
[ ${#files[@]} -gt 0 ] || { echo "no precomputed files to keep"; exit 0; }
key="$RUNNER_TEMP/ionos_precomputed_key"
printf '%s\n' "$IONOS_SSH_KEY" > "$key"; chmod 600 "$key"
trap 'rm -f "$key"' EXIT
ssh="ssh -i $key -p ${IONOS_PORT:-22} -o BatchMode=yes -o StrictHostKeyChecking=accept-new ${IONOS_USER}@${IONOS_HOST}"
dir="$IONOS_DIR/data/precomputed"
$ssh "mkdir -p '$dir'; chgrp www-data '$dir' 2>/dev/null || true; chmod 750 '$dir'"
for f in "${files[@]}"; do
  name=$(basename "$f")
  $ssh "umask 027; cat > '$dir/$name.new'" < "$f"
  want=$(stat -c %s "$f")
  $ssh "set -eu; f='$dir/$name.new'; [ \"\$(stat -c %s \"\$f\")\" = '$want' ]; python3 -c 'import json,sys; json.load(open(sys.argv[1], encoding=\"utf-8\"))' \"\$f\"; chgrp www-data \"\$f\" 2>/dev/null || true; chmod 640 \"\$f\"; mv -f \"\$f\" '$dir/$name'"
  echo "kept $name ($want bytes) on IONOS"
done
