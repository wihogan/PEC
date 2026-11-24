#!/bin/bash
# Install the PEC Mac jobs (launchd): refresh trigger (Mon-Fri PT: hourly at :45 06:45-12:45,
# then 13:20 post-close final and 15:50 retry)
# and watchdog (16:30 PT, Mon-Fri). The Mac's clock/timezone must be Pacific.
# Usage: mac/install.sh [--dry-run] [--reset-secrets] [--test]
#   --dry-run        print what would happen; change nothing
#   --reset-secrets  re-prompt for the Telegram token and chat id
#   --test           send a test Telegram message from this Mac and exit
set +x
set -u

REPO="wihogan/PEC"
DRY=0; RESET=0; TEST=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --reset-secrets) RESET=1 ;;
    --test) TEST=1 ;;
    -h|--help) sed -n 2,8p "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

if [ "$(uname -s)" != "Darwin" ]; then
  echo "install.sh: this installer is for macOS only (found $(uname -s)). Nothing done." >&2
  exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)"
SHARE="$HOME/.local/share/pec"
CONF_DIR="$HOME/.config/pec"
ENV_FILE="$CONF_DIR/env"
LOG_DIR="$HOME/Library/Logs/pec"
AGENTS="$HOME/Library/LaunchAgents"
UID_N="$(id -u)"

say() { echo "==> $*"; }
run() { if [ "$DRY" -eq 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }

have_secret() { security find-generic-password -s "$1" -w >/dev/null 2>&1; }

send_test() {
  local token chat code
  token="$(security find-generic-password -s pec-telegram-token -w 2>/dev/null)" || token=""
  chat="$(security find-generic-password -s pec-telegram-chat -w 2>/dev/null)" || chat=""
  if [ -z "$token" ] || [ -z "$chat" ]; then
    echo "Telegram secrets not in Keychain; run install.sh first." >&2
    return 1
  fi
  code="$(printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" \
    | curl -sS --max-time 20 --retry 2 -K - -o /dev/null -w '%{http_code}' \
        --data-urlencode "chat_id=${chat}" \
        --data-urlencode "text=✅ PEC test message from the Mac ($(hostname -s))")" || true
  if [ "$code" = "200" ]; then echo "Test message sent (HTTP 200)."; else echo "Test message FAILED (HTTP ${code:-none})." >&2; return 1; fi
}

if [ "$TEST" -eq 1 ]; then
  if [ "$DRY" -eq 1 ]; then echo "[dry-run] would send a test Telegram message"; exit 0; fi
  send_test; exit $?
fi

# --- gh ---------------------------------------------------------------
GH="$(command -v gh || true)"
[ -z "$GH" ] && for p in /opt/homebrew/bin/gh /usr/local/bin/gh; do [ -x "$p" ] && GH="$p" && break; done
if [ -z "$GH" ]; then echo "gh not found. Install with: brew install gh" >&2; exit 1; fi
say "gh: $GH"
if ! "$GH" auth status >/dev/null 2>&1; then echo "gh is not logged in. Run: gh auth login" >&2; exit 1; fi
if [ "$("$GH" api "repos/${REPO}" -q .permissions.push 2>/dev/null)" != "true" ]; then
  echo "Your gh login does not have push access to ${REPO} (needed to run workflows)." >&2
  exit 1
fi
say "gh authenticated with push access to ${REPO}"

# --- secrets ----------------------------------------------------------
store_secret() {  # name, value
  # Note: the value is briefly visible in this process's argv (macOS 'security' limitation).
  run security add-generic-password -U -a "$USER" -s "$1" -w "$2"
}
need_prompt=0
if [ "$RESET" -eq 1 ] || ! have_secret pec-telegram-token || ! have_secret pec-telegram-chat; then need_prompt=1; fi
if [ "$need_prompt" -eq 1 ]; then
  if [ "$DRY" -eq 1 ]; then
    echo "[dry-run] would prompt for Telegram bot token and chat id and store them in Keychain"
  else
    read -r -s -p "Telegram bot token: " tok; echo
    read -r -s -p "Telegram chat id: " chat; echo
    if [ -z "$tok" ] || [ -z "$chat" ]; then echo "Empty value; aborting." >&2; exit 1; fi
    store_secret pec-telegram-token "$tok"
    store_secret pec-telegram-chat "$chat"
    unset tok chat
  fi
else
  say "Telegram secrets already in Keychain (use --reset-secrets to replace)"
fi

# --- files ------------------------------------------------------------
say "copying scripts to $SHARE"
run mkdir -p "$SHARE" "$CONF_DIR" "$LOG_DIR" "$AGENTS"
run cp "$SRC/pec-trigger.sh" "$SRC/pec-watchdog.sh" "$SHARE/"
run chmod +x "$SHARE/pec-trigger.sh" "$SHARE/pec-watchdog.sh"

say "writing $ENV_FILE"
if [ "$DRY" -eq 1 ]; then
  echo "[dry-run] GH=$GH REPO=$REPO LOG_DIR=$LOG_DIR"
else
  cat > "$ENV_FILE" <<ENVEOF
GH=$GH
REPO=$REPO
LOG_DIR=$LOG_DIR
ENVEOF
fi

# plist LABEL SCRIPT LOGNAME "H:M H:M ..."
write_plist() {
  local label="$1" script="$2" logname="$3" times="$4" f="$AGENTS/$1.plist" hm h m d
  if [ "$DRY" -eq 1 ]; then echo "[dry-run] write $f (Mon-Fri at: $times PT)"; return 0; fi
  {
    cat <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$SHARE/$script</string>
  </array>
  <key>StandardOutPath</key><string>$LOG_DIR/$logname.out</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/$logname.err</string>
  <key>StartCalendarInterval</key>
  <array>
PL
    for hm in $times; do
      h="${hm%%:*}"; m="${hm##*:}"
      for d in 1 2 3 4 5; do
        echo "    <dict><key>Weekday</key><integer>$d</integer><key>Hour</key><integer>$h</integer><key>Minute</key><integer>$((10#$m))</integer></dict>"
      done
    done
    cat <<PL
  </array>
</dict>
</plist>
PL
  } > "$f"
  plutil -lint "$f" >/dev/null || { echo "plist invalid: $f" >&2; exit 1; }
}
write_plist com.pec.trigger pec-trigger.sh launchd-trigger "06:45 07:45 08:45 09:45 10:45 11:45 12:45 13:20 15:50"
write_plist com.pec.watchdog pec-watchdog.sh launchd-watchdog "16:30"

# --- launchd ----------------------------------------------------------
for label in com.pec.trigger com.pec.watchdog; do
  run launchctl bootout "gui/${UID_N}/${label}" 2>/dev/null || true
  run launchctl bootstrap "gui/${UID_N}" "$AGENTS/${label}.plist"
done

say "status"
if [ "$DRY" -eq 1 ]; then
  echo "[dry-run] nothing was changed"
else
  launchctl list | grep -E 'com\.pec\.' || echo "(jobs not listed; check launchctl print gui/${UID_N}/com.pec.trigger)"
  echo "Logs: $LOG_DIR"
  echo "Next: run  $0 --test  to confirm Telegram works."
fi
