#!/usr/bin/env bash
# Send the text of a file to Telegram (truncated to 3500 chars).
# Usage: tools/notify_telegram.sh MESSAGE_FILE
# Env:   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (both required)
# Never prints the token or the request URL; on failure prints only the HTTP status.
set +x
set -uo pipefail

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "usage: $0 MESSAGE_FILE" >&2
  exit 2
fi
if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
  echo "notify_telegram: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is empty" >&2
  exit 1
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
# Truncate to 3500 bytes, then drop any partial trailing UTF-8 sequence.
head -c 3500 "$1" | iconv -c -f UTF-8 -t UTF-8 > "$tmp"

# The URL (which embeds the token) is passed to curl on stdin (-K -), so it
# never appears in the process list or in any log.
code="$(printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$TELEGRAM_BOT_TOKEN" \
  | curl -sS --max-time 20 --retry 2 -K - \
      -o /dev/null -w '%{http_code}' \
      --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
      --data-urlencode "disable_web_page_preview=true" \
      --data-urlencode "text@${tmp}")"
rc=$?

if [ "$rc" -ne 0 ] || [ "$code" != "200" ]; then
  echo "notify_telegram: send failed (curl exit ${rc}, HTTP ${code:-none})" >&2
  exit 1
fi
echo "notify_telegram: sent (HTTP 200)"
