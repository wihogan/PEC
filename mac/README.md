# PEC Mac jobs (launchd)

The Mac triggers the GitHub refresh workflow (primary trigger) and runs a watchdog. Mac timezone must be Pacific.

1. Install GitHub CLI and log in: `brew install gh && gh auth login` (the login needs push access to wihogan/PEC).
2. Have your Telegram bot token and chat id ready (the same ones stored as GitHub secrets).
3. Install: `bash mac/install.sh` (add `--dry-run` first to preview; `--reset-secrets` to re-enter the Telegram values).
4. Confirm Telegram works: `bash mac/install.sh --test`.
5. Schedule installed: trigger Mon-Fri 1:20 PM and 3:50 PM PT; watchdog Mon-Fri 4:30 PM PT.
6. Logs: `~/Library/Logs/pec/` (trigger.log, watchdog.log). The Mac must be awake and online at those times.
7. Secrets live in the macOS Keychain (`pec-telegram-token`, `pec-telegram-chat`), never in files.
8. Uninstall: `bash mac/uninstall.sh` (add `--purge-secrets` to also delete the Keychain items).
9. Reinstalling is safe; the installer is idempotent.
10. Manual refresh any time: GitHub app, wihogan/PEC, Actions, refresh, Run workflow.
