#!/usr/bin/env bash
# Fail if the FRED API key (or any api_key=<16+ alnum> pattern) appears in the given files.
# Usage: tools/leak_scan.sh PEC.html data_v2.json health.json
# Never prints the key.
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 FILE..." >&2
  exit 2
fi

rc=0
key="${FRED_API_KEY:-}"
for f in "$@"; do
  if [ ! -f "$f" ]; then
    echo "leak_scan: $f not found" >&2
    rc=1
    continue
  fi
  if [ -n "$key" ] && grep -qF -- "$key" "$f"; then
    echo "leak_scan: FAIL - FRED_API_KEY value found in $f" >&2
    rc=1
  fi
  if grep -qE 'api_key=[A-Za-z0-9]{16,}' "$f"; then
    echo "leak_scan: FAIL - api_key=<token> pattern found in $f" >&2
    rc=1
  fi
done

if [ "$rc" -eq 0 ]; then
  echo "leak_scan: OK ($# file(s) scanned)"
fi
exit "$rc"
