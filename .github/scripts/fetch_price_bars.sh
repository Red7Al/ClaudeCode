#!/usr/bin/env bash
# fetch_price_bars.sh MODE
#
# Copy the price bars file (bars_file.py builds it on IONOS, /home/www/data/price_bars) to $RUNNER_TEMP over SSH and
# point price_bars.py at it for every later step of this job: PRICE_BARS_DIR, and PRICE_BARS=MODE (shadow | on).
# Owner 2026-10-21: read files, not the database -- and never a GitHub artifact. Both files land under temporary
# names; the gzip must test clean and its size must match the host's before either is used. On any failure nothing
# is exported, so price_bars stays off and the job reads the database exactly as before.
# Needs IONOS_HOST, IONOS_USER, IONOS_SSH_KEY; IONOS_PORT defaults to 22.
set -euo pipefail
mode="$1"
[[ "$mode" == "shadow" || "$mode" == "on" ]] || { echo "mode must be shadow or on" >&2; exit 2; }
dest="$RUNNER_TEMP/price_bars"
mkdir -p "$dest"
key="$RUNNER_TEMP/ionos_bars_key"
printf '%s\n' "$IONOS_SSH_KEY" > "$key"; chmod 600 "$key"
ssh="ssh -i $key -p ${IONOS_PORT:-22} -o BatchMode=yes -o StrictHostKeyChecking=accept-new ${IONOS_USER}@${IONOS_HOST}"
src=/home/www/data/price_bars
for f in price_bars.meta.json price_bars.csv.gz; do
  $ssh "cat $src/$f" > "$dest/$f.new"
  want=$($ssh "stat -c %s $src/$f")
  [ "$(stat -c %s "$dest/$f.new")" = "$want" ] || { echo "$f: size differs from the host's" >&2; rm -f "$key"; exit 1; }
done
rm -f "$key"
gzip -t "$dest/price_bars.csv.gz.new"
python -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" "$dest/price_bars.meta.json.new"
mv -f "$dest/price_bars.csv.gz.new" "$dest/price_bars.csv.gz"
mv -f "$dest/price_bars.meta.json.new" "$dest/price_bars.meta.json"
echo "PRICE_BARS_DIR=$dest" >> "$GITHUB_ENV"
echo "PRICE_BARS=$mode" >> "$GITHUB_ENV"
echo "price bars file fetched ($(stat -c %s "$dest/price_bars.csv.gz") bytes, built $(python -c "import json,sys; print(json.load(open(sys.argv[1]))['built_at'])" "$dest/price_bars.meta.json")); PRICE_BARS=$mode"
