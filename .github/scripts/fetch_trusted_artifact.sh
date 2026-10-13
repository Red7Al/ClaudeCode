#!/usr/bin/env bash
# fetch_trusted_artifact.sh NAME FILE DEST_DIR ALLOWED_WORKFLOW_PATH...
#
# Download the newest artifact NAME that THIS repository's own trusted run produced, and install ONLY
# the file FILE from it into DEST_DIR. Prints the artifact id; exits 3 when no trusted artifact exists.
#
# WHY (security review 2026-10-13). The repository is public and trading-secret-scan.yml runs on
# pull_request, so a fork's pull request can run its own workflow files and upload an artifact under any
# name -- and the repository's artifact listing includes it. The Order Bridge used to take the newest
# artifact by name and unzip the WHOLE archive into hvf_web/, so a planted zip carrying order_bridge.py
# would have run with the IG and Supabase secrets. Two checks close that:
#   1. provenance -- built on main, in this repository (not a fork), by workflow_dispatch (how
#      cron-job.org and a manual run start every producer), from one of the named workflow files;
#   2. extraction -- only FILE is extracted, into a scratch directory, then copied: no archive can write
#      anything else, and never a .py file.
set -euo pipefail
name="$1"; file="$2"; dest="$3"; shift 3
allowed=" $* "

candidates=$(gh api "repos/$GITHUB_REPOSITORY/actions/artifacts?name=${name}&per_page=30" --jq '
  [.artifacts[] | select(.expired == false
                         and .workflow_run.head_branch == "main"
                         and .workflow_run.head_repository_id == .workflow_run.repository_id)]
  | sort_by(.created_at) | reverse | .[] | "\(.id) \(.workflow_run.id)"')

while read -r art run; do
  [ -n "${art:-}" ] || continue
  meta=$(gh api "repos/$GITHUB_REPOSITORY/actions/runs/${run}" --jq '"\(.event) \(.path)"')
  event="${meta%% *}"; path="${meta#* }"
  if [ "$event" != "workflow_dispatch" ] || [[ "$allowed" != *" $path "* ]]; then
    echo "skipping artifact $art: run $run is $event from $path" >&2
    continue
  fi
  scratch=$(mktemp -d)
  gh api "repos/$GITHUB_REPOSITORY/actions/artifacts/${art}/zip" > "$scratch/a.zip"
  unzip -q -o "$scratch/a.zip" "$file" -d "$scratch/x"
  [ -s "$scratch/x/$file" ] || { echo "artifact $art has no $file" >&2; exit 4; }
  mkdir -p "$dest"
  cp "$scratch/x/$file" "$dest/$file"
  rm -rf "$scratch"
  echo "$art"
  exit 0
done <<< "$candidates"

echo "no trusted '$name' artifact (built on main by workflow_dispatch from:$allowed)" >&2
exit 3
