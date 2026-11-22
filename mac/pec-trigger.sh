#!/bin/bash
# PEC: trigger the GitHub refresh workflow from this Mac (launchd, weekdays).
# Config (absolute gh path etc.) is written by install.sh to ~/.config/pec/env
# because launchd runs with a minimal PATH.
set +x
set -u

ENV_FILE="${HOME}/.config/pec/env"
# shellcheck disable=SC1090
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
GH="${GH:-/opt/homebrew/bin/gh}"
REPO="${REPO:-wihogan/PEC}"
LOG_DIR="${LOG_DIR:-$HOME/Library/Logs/pec}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/trigger.log"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" >> "$LOG"; }

send_telegram() {
  local text="$1" token chat code
  token="$(security find-generic-password -s pec-telegram-token -w 2>/dev/null)" || token=""
  chat="$(security find-generic-password -s pec-telegram-chat -w 2>/dev/null)" || chat=""
  if [ -z "$token" ] || [ -z "$chat" ]; then
    log "telegram: secrets missing in Keychain; alert not sent"
    return 1
  fi
  code="$(printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" \
    | curl -sS --max-time 20 --retry 2 -K - -o /dev/null -w '%{http_code}' \
        --data-urlencode "chat_id=${chat}" \
        --data-urlencode "disable_web_page_preview=true" \
        --data-urlencode "text=${text}" 2>>"$LOG")" || true
  if [ "$code" != "200" ]; then
    log "telegram: send failed (HTTP ${code:-none})"
    return 1
  fi
  log "telegram: alert sent"
}

log "triggering refresh.yml on ${REPO} (ref main)"
out="$("$GH" workflow run refresh.yml -R "$REPO" --ref main 2>&1)"
rc=$?
if [ "$rc" -eq 0 ]; then
  log "ok: ${out}"
  exit 0
fi
log "gh failed (exit ${rc}): ${out}"
send_telegram "⚠️ PEC: Mac could not trigger refresh (gh exit ${rc})"
exit "$rc"
