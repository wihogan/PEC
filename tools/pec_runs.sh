#!/usr/bin/env bash
# Look up recent PRODUCTION refresh runs (run title ends with "[state]", branch main).
# Usage: tools/pec_runs.sh previous <current_run_id>
#            -> prints success|failure|none: conclusion of the most recent earlier completed
#               production run that ended in success or failure (cancelled/skipped ignored)
#        tools/pec_runs.sh failures-today <current_run_id>
#            -> prints the number of completed FAILED production runs created today (PT),
#               excluding the current run
# Env: GH_TOKEN (needs actions: read), GITHUB_REPOSITORY (or REPO). Never prints secrets.
set +x
set -uo pipefail

cmd="${1:-}"
cur="${2:-0}"
repo="${REPO:-${GITHUB_REPOSITORY:-}}"
if [ -z "$repo" ] || ! [[ "$cur" =~ ^[0-9]+$ ]]; then
  echo "usage: $0 previous|failures-today <run_id>  (GITHUB_REPOSITORY required)" >&2
  exit 2
fi

runs="$(gh run list -R "$repo" -w refresh.yml --branch main --limit 50 \
  --json databaseId,conclusion,status,createdAt,displayTitle 2>/dev/null)" || runs=""
if [ -z "$runs" ]; then
  echo "pec_runs: gh run list failed" >&2
  case "$cmd" in previous) echo none ;; *) echo 0 ;; esac
  exit 0
fi

# shellcheck disable=SC2016  # jq expression, not shell
prod='[.[] | select((.displayTitle|endswith("[state]")) and .status=="completed" and .databaseId != $cur)]'

case "$cmd" in
  previous)
    printf '%s' "$runs" | jq -r --argjson cur "$cur" \
      "${prod} | map(select(.conclusion==\"success\" or .conclusion==\"failure\")) | (.[0].conclusion // \"none\")"
    ;;
  failures-today)
    export TZ=America/Los_Angeles
    today="$(date +%Y-%m-%d)"
    if date --version >/dev/null 2>&1; then
      since="$(date -u -d "@$(date -d "${today} 00:00:00" +%s)" +%Y-%m-%dT%H:%M:%SZ)"  # midnight PT, not UTC (D5)
    else
      since="$(date -u -r "$(date -j -f '%Y-%m-%d %H:%M:%S' "${today} 00:00:00" +%s)" +%Y-%m-%dT%H:%M:%SZ)"
    fi
    printf '%s' "$runs" | jq -r --argjson cur "$cur" --arg since "$since" \
      "${prod} | map(select(.conclusion==\"failure\" and .createdAt >= \$since)) | length"
    ;;
  *)
    echo "usage: $0 previous|failures-today <run_id>" >&2
    exit 2
    ;;
esac
