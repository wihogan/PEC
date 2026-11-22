#!/bin/bash
# Remove the PEC Mac jobs. Keeps Keychain secrets unless --purge-secrets.
set +x
set -u
PURGE=0
for a in "$@"; do
  case "$a" in
    --purge-secrets) PURGE=1 ;;
    *) echo "usage: $0 [--purge-secrets]" >&2; exit 2 ;;
  esac
done
if [ "$(uname -s)" != "Darwin" ]; then echo "macOS only." >&2; exit 1; fi

UID_N="$(id -u)"
for label in com.pec.trigger com.pec.watchdog; do
  launchctl bootout "gui/${UID_N}/${label}" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/${label}.plist"
done
rm -f "$HOME/.local/share/pec/pec-trigger.sh" "$HOME/.local/share/pec/pec-watchdog.sh"
rmdir "$HOME/.local/share/pec" 2>/dev/null || true
rm -f "$HOME/.config/pec/env"
rmdir "$HOME/.config/pec" 2>/dev/null || true
echo "Removed launchd jobs, plists, scripts and config. Logs kept in ~/Library/Logs/pec."
if [ "$PURGE" -eq 1 ]; then
  security delete-generic-password -s pec-telegram-token >/dev/null 2>&1 || true
  security delete-generic-password -s pec-telegram-chat >/dev/null 2>&1 || true
  echo "Keychain items removed."
else
  echo "Keychain items kept (use --purge-secrets to delete)."
fi
