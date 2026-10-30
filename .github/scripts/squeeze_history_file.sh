#!/usr/bin/env bash
# squeeze_history_file.sh put|get
#
# The squeeze history file: the bridge-eligible squeeze_history rows with the table's write counter they were read
# at (publish_report_summary.py --squeeze-history-file). The job that writes the table builds it straight after
# and PUTs it on IONOS (/home/www/data/squeeze_history); each later job GETs it, and server._squeeze_history_raw
# uses it only while the table's write counter is unchanged -- otherwise the database, exactly as before.
# Owner 2026-10-21: read files, not the database -- and never a GitHub artifact. MEASURED 10-26 22:17Z -> 10-30
# 11:32Z: 34 reads / 1,111,532 rows of squeeze_history, the biggest query by rows, from the Morning Chain and the
# evening scan reading the table themselves before the summary held its new counter.
#
#   put   upload $RUNNER_TEMP/squeeze_history.json (must already exist); it lands under a temporary name and
#         replaces the host's copy only once it parses there with the same write counter.
#   get   download it to $RUNNER_TEMP/squeeze_history.json; size must match the host's and it must parse. Only
#         then is SQUEEZE_HISTORY_FILE exported -- on any failure nothing is, and the job reads the database.
#
# Both modes export SQUEEZE_HISTORY_FILE for the later steps of THIS job. Needs IONOS_HOST, IONOS_USER,
# IONOS_SSH_KEY; IONOS_PORT defaults to 22.
set -euo pipefail
mode="${1:-}"
[[ "$mode" == "put" || "$mode" == "get" ]] || { echo "mode must be put or get" >&2; exit 2; }
file="$RUNNER_TEMP/squeeze_history.json"
key="$RUNNER_TEMP/ionos_sqh_key"
printf '%s\n' "$IONOS_SSH_KEY" > "$key"; chmod 600 "$key"
trap 'rm -f "$key"' EXIT
ssh="ssh -i $key -p ${IONOS_PORT:-22} -o BatchMode=yes -o StrictHostKeyChecking=accept-new ${IONOS_USER}@${IONOS_HOST}"
dir=/home/www/data/squeeze_history
# The counter is spliced into a command run on the host, so it must be digits only.
counter() { python -c "import json,sys; v=json.load(open(sys.argv[1], encoding='utf-8'))['version']; assert isinstance(v, int) and v >= 0, v; print(v)" "$1"; }

if [ "$mode" = "put" ]; then
  want=$(counter "$file")
  $ssh "umask 077; mkdir -p $dir; cat > $dir/squeeze_history.json.new" < "$file"
  $ssh "set -eu; python3 -c \"import json,sys; d=json.load(open('$dir/squeeze_history.json.new', encoding='utf-8')); sys.exit(0 if str(d.get('version')) == '$want' else 3)\"; mv -f $dir/squeeze_history.json.new $dir/squeeze_history.json"
  echo "squeeze history file on IONOS at write counter $want ($(stat -c %s "$file") bytes)"
else
  $ssh "cat $dir/squeeze_history.json" > "$file.new"
  want=$($ssh "stat -c %s $dir/squeeze_history.json")
  [ "$(stat -c %s "$file.new")" = "$want" ] || { echo "squeeze_history.json: size differs from the host's" >&2; exit 1; }
  counter "$file.new" > /dev/null
  mv -f "$file.new" "$file"
  echo "squeeze history file fetched: write counter $(counter "$file"), $want bytes"
fi
echo "SQUEEZE_HISTORY_FILE=$file" >> "$GITHUB_ENV"
