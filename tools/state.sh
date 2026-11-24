#!/usr/bin/env bash
# Persist data_v2.json (plus the optional EPS cache files) on an orphan branch (plan sections 2-3).
# Optional files live in .cache/ locally and at the branch root on the state branch:
# the forward-EPS month-end history (the runner discards config/forward_eps_seed.yaml
# write-back) and the FactSet once-per-day cache.
# Usage: tools/state.sh restore <ref> | save <ref>     (ref: state | state-test)
set -euo pipefail

cmd="${1:-}"
ref="${2:-}"
case "$ref" in
  state|state-test) ;;
  *) echo "state.sh: ref must be 'state' or 'state-test' (got '${ref}')" >&2; exit 2 ;;
esac

root="$(git rev-parse --show-toplevel)"
cd "$root"
FILE="data_v2.json"
BASELINE="tests/fixtures/baseline_data_v2.json"
OPTIONAL_FILES=(forward_eps_ntm_history.json factset_eps_latest.json)

restore() {
  local out
  if ! out="$(git ls-remote --heads origin "refs/heads/${ref}")"; then
    echo "state.sh: git ls-remote failed" >&2
    exit 1
  fi
  if [ -z "$out" ]; then
    echo "::warning::State branch '${ref}' does not exist; seeding ${FILE} from ${BASELINE}"
    cp "$BASELINE" "$FILE"
    return 0
  fi
  if ! git fetch --depth=1 origin "refs/heads/${ref}"; then
    echo "state.sh: fetch of '${ref}' failed although it exists" >&2
    exit 1
  fi
  if ! git show "FETCH_HEAD:${FILE}" > "${FILE}.tmp"; then
    rm -f "${FILE}.tmp"
    echo "state.sh: ${FILE} not found on '${ref}'" >&2
    exit 1
  fi
  mv "${FILE}.tmp" "$FILE"
  echo "state.sh: restored ${FILE} from '${ref}'"
  # Optional files: absence on the branch is normal (first run after this feature).
  local f
  mkdir -p .cache
  for f in "${OPTIONAL_FILES[@]}"; do
    if git show "FETCH_HEAD:${f}" > ".cache/${f}.tmp" 2>/dev/null; then
      mv ".cache/${f}.tmp" ".cache/${f}"
      echo "state.sh: restored .cache/${f} from '${ref}'"
    else
      rm -f ".cache/${f}.tmp"
      echo "state.sh: ${f} not on '${ref}' (ok)"
    fi
  done
}

save() {
  [ -f "$FILE" ] || { echo "state.sh: ${FILE} missing" >&2; exit 1; }
  tmp="$(mktemp -d)"
  wt="${tmp}/wt"
  tmpbranch="${ref}-save-$$-${RANDOM}"
  trap 'git worktree remove --force "$wt" >/dev/null 2>&1 || true; git branch -D "$tmpbranch" >/dev/null 2>&1 || true; rm -rf "$tmp"' EXIT
  git worktree add --detach "$wt" HEAD >/dev/null
  (
    cd "$wt"
    git checkout --orphan "$tmpbranch" >/dev/null 2>&1
    git rm -rf . >/dev/null 2>&1 || true
    find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
    cp "${root}/${FILE}" "$FILE"
    git add "$FILE"
    for f in "${OPTIONAL_FILES[@]}"; do
      if [ -f "${root}/.cache/${f}" ]; then
        cp "${root}/.cache/${f}" "$f"
        git add "$f"
      fi
    done
    git -c user.name="github-actions[bot]" \
        -c user.email="41898579+github-actions[bot]@users.noreply.github.com" \
        commit -q -m "state: ${FILE} $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local n
    for n in 1 2 3; do
      if git push --force origin "HEAD:refs/heads/${ref}"; then
        echo "state.sh: saved ${FILE} to '${ref}'"
        exit 0
      fi
      echo "state.sh: push attempt ${n} failed" >&2
      sleep $((n * 3))
    done
    echo "state.sh: push failed after 3 attempts" >&2
    exit 1
  )
}

case "$cmd" in
  restore) restore ;;
  save) save ;;
  *) echo "usage: $0 restore|save <state|state-test>" >&2; exit 2 ;;
esac
