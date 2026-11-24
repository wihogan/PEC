#!/bin/bash
# PEC: weekday watchdog. Alerts via Telegram if no successful POST-CLOSE production
# refresh run exists today (created at or after 13:00 PT; early-close days also pass by
# then), so a morning success cannot hide missing post-close runs. Production runs are identified by the run title suffix
# "[state]" set by `run-name` in refresh.yml, so state-test runs are ignored.
set +x
set -u

ENV_FILE="${HOME}/.config/pec/env"
# shellcheck disable=SC1090
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
GH="${GH:-/opt/homebrew/bin/gh}"
REPO="${REPO:-wihogan/PEC}"
LOG_DIR="${LOG_DIR:-$HOME/Library/Logs/pec}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/watchdog.log"
ACTIONS_URL="https://github.com/${REPO}/actions/workflows/refresh.yml"

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

export TZ=America/Los_Angeles
dow="$(date +%u)"
if [ "$dow" -gt 5 ]; then
  log "weekend; nothing to check"
  exit 0
fi
today="$(date +%Y-%m-%d)"
now_hm="$(date +%H:%M)"

# Today's 13:00 Pacific as UTC ISO (BSD date on macOS, GNU date elsewhere).
if date --version >/dev/null 2>&1; then
  epoch="$(date -d "${today} 13:00:00" +%s)"
  since="$(date -u -d "@${epoch}" +%Y-%m-%dT%H:%M:%SZ)"
else
  epoch="$(date -j -f '%Y-%m-%d %H:%M:%S' "${today} 13:00:00" +%s)"
  since="$(date -u -r "${epoch}" +%Y-%m-%dT%H:%M:%SZ)"
fi

summary="$("$GH" run list -R "$REPO" -w refresh.yml --branch main --limit 50 \
  --json conclusion,status,createdAt,event,headBranch,displayTitle \
  -q "[.[] | select(.headBranch==\"main\" and (.displayTitle|endswith(\"[state]\")) and .createdAt >= \"${since}\")] | \"\(map(select(.conclusion==\"success\")) | length) \(map(select(.status!=\"completed\")) | length) \(length)\"" 2>&1)"
rc=$?
if [ "$rc" -ne 0 ]; then
  log "gh run list failed (exit ${rc}): ${summary}"
  send_telegram "⚠️ PEC: watchdog could not query GitHub (gh exit ${rc}) as of ${now_hm} PT — check manually: ${ACTIONS_URL}"
  exit 0
fi

read -r ok active total <<EOF2
${summary}
EOF2
log "since=${since} success=${ok:-?} in_progress=${active:-?} total=${total:-?}"

if [ "${ok:-0}" -gt 0 ]; then
  exit 0
fi
if [ "${active:-0}" -gt 0 ]; then
  send_telegram "⏳ PEC: refresh still in progress as of ${now_hm} PT, no successful post-close run yet today — ${ACTIONS_URL}"
  exit 0
fi
send_telegram "⚠️ PEC: no successful post-close refresh (since 13:00 PT) as of ${now_hm} PT — trigger manually: ${ACTIONS_URL}"
exit 0
