# PEC Runbook (for the owner)

Dashboard: https://wihogan.github.io/PEC/ (confirm the capitalisation of this URL after the first deploy)
Repo: https://github.com/wihogan/PEC

## 1. How the daily refresh works

| What | When (Pacific, Mon-Fri) | Notes |
|---|---|---|
| Mac trigger (primary) | 6:45, 7:45, 8:45, 9:45, 10:45, 11:45 AM, 12:45 PM (market hours), 1:20 PM (post-close final), 3:50 PM (retry) | Mac runs `gh workflow run`; the Mac must be awake and online. After changing times, run `git pull && mac/install.sh` once |
| GitHub schedule (backup) | about 2:17 PM and 3:47 PM (3:17 / 4:47 PM in summer time) | Only active after Phase 3 go; if a run is already up to date it does little |
| Watchdog (Mac) | 4:30 PM | Alerts if no successful refresh happened today |

How fresh is each data type (a run only fetches what is due; the rest is carried forward):

| Data | Series | When it is fetched |
|---|---|---|
| Market prices (`intraday`) | All Yahoo series: indexes, metals, crypto | Every run while the market is open (9:30 AM-4:00 PM ET; 1:00 PM ET on early-close days); once more after the close. No market-hours fetches on weekends or NYSE holidays. |
| Daily releases (`daily_release`) | FRED rates, TIPS, credit, Fed funds, FX, oil; Treasury yield curve | Every run until the newest expected day arrives (FRED: previous trading day; yield curve: the last closed day), then not again until the next day is due |
| Valuation (`daily_after_close`) | The 6 Shiller-derived valuation series (`sp500_dividend_yield`, `sp500_pe`, `sp500_earnings_yield`, `sp500_earnings`, `sp500_earnings_nominal`, `shiller_cape`); latest month provisional (`provisional_from`); source order shillerdata.com, then multpl.com, then Yale (`source_used` in `data_v2.json`, `metadata.statuses` and `health.json`; `sp500_earnings_nominal` has no multpl source) | Once per trading day, at the first run after the close; retried on later runs that day if it failed |
| Monthly (`monthly`) | CPI, NBER recession | Once a day until the last finished month appears, then re-checked every 7 days (Pacific calendar days) to pick up revisions |
| Quarterly (`quarterly`) | GDP, 3 delinquency series | Same, per finished calendar quarter (daily until it appears, then weekly re-check) |

A failed fetch never counts as done; the next run tries again.

Each run: fetch data, build the page, run the healthcheck, then publish to the page. If anything fails the old page stays up and you get a Telegram message. The data's memory (state) is saved only after a good run.

## 2. Telegram alerts

| Message | Meaning | What to do |
|---|---|---|
| `⚠️ PEC refresh FAILED` (with run link and failure lines) | The refresh ran and failed or was cancelled. Old page is still live. | Open the link to read why. Most failures are a data source down for a day: re-run later (section 3). If it repeats for 2+ days, ask for help. |
| `[TEST] ⚠️ PEC refresh FAILED` | A test run on the test state failed | Normal during testing; ignore otherwise |
| `⚠️ PEC: Mac could not trigger refresh (gh exit N)` | The Mac could not start the run (login expired, offline) | Run `gh auth status` on the Mac; `gh auth login` if needed. Meanwhile do a manual refresh. The GitHub backup schedule may still run. |
| `⚠️ PEC: no successful refresh today as of HH:MM PT` | Watchdog found no successful run today | Trigger a manual refresh (section 3), then check the Mac jobs |
| `ℹ️ PEC valuation series served by multpl.com fallback — shillerdata.com unavailable since DATE` (or `Yale ie_data.xls`) | shillerdata.com could not be used and a fallback is serving the valuation series (sent once; again only if the fallback source changes) | Nothing required. `sp500_earnings_nominal` stays carried forward while on the multpl fallback. If it lasts more than a few days, check shillerdata.com in a browser. |
| `✅ PEC valuation series back on shillerdata.com` | The primary source works again after a fallback message | Nothing |
| `ℹ️ PEC: Valuation data fetch (shillerdata.com, multpl.com and Yale) has failed on N different days...` | Every source failed on N different days; valuation series are carried forward | Ask for help if it persists; see `docs/plan.md` Amendment D |
| `⏳ PEC: refresh still in progress` | A run is going but not finished at 4:30 PM | Wait a few minutes; you will be alerted again only if it fails |
| `⚠️ PEC: watchdog could not query GitHub` | Mac/GitHub login problem | Check `gh auth status` on the Mac |

No message does not guarantee success if the Mac was off. If the page timestamp looks old, refresh manually.

## 3. Manual refresh

Phone: GitHub app, wihogan/PEC, Actions, refresh, Run workflow (leave defaults), Run.
Web: https://github.com/wihogan/PEC/actions/workflows/refresh.yml, Run workflow, Run.

Options: `force` re-fetches every series (use after a bad day). Leave `deploy` on, `state_ref` = `state`, `inject` = `none`. The other values are for testing only.

## 4. Forward EPS

Automatic: every refresh run reads the latest FactSet Earnings Insight PDF (published weekly, Fridays) and computes forward EPS = S&P 500 closing price divided by the forward 12-month P/E (accurate to about 0.3 %). It fetches at most once per Pacific day. The month-end history on the page is updated automatically. When the value moves more than 3 % or the source switches, you get an informational Telegram message.

Your manual entry wins when it is newer: FactSet is used only if its report date is later than the manual override's effective date. To enter a value yourself: Run workflow on the refresh screen, fill `eps_value` (a number from 200 to 600) and `eps_date` (YYYY-MM-DD, not in the future); both are required together. The run edits `config/series_v2.yaml` on main, then refreshes. Bad values make the run fail at the start with a clear message and change nothing. The override stays in force until the next FactSet report dated after `eps_date`.

To force manual only (ignore FactSet): change `PEC_EPS_MODE` to `override` in `.github/workflows/refresh.yml` on main. If FactSet fails (page change, block), the run keeps the last FactSet value (or the override) and `health.json` warns when the date is more than 45 days old. Check the DDM panel on the page afterwards.

## 5. Reading health.json

Open https://wihogan.github.io/PEC/health.json . Key fields: `ok` (true/false), `last_refreshed`, `run_id`, `failures` (should be empty), `warnings` (slow series that are old; informational), `series` (last observation date and age of each). Warnings never block publishing; failures do.

## 6. Where state lives

Branch `state` on the repo holds `data_v2.json` plus the forward-EPS cache files (`forward_eps_ntm_history.json`, `factset_eps_latest.json`), replaced after each good run. Branch `state-test` is for testing. Run logs and health.json are also attached to each run for 7 days (run page, Artifacts). The page itself is never stored in git. The only secrets are `FRED_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (GitHub repo secrets) and the two Keychain items on the Mac.

## 7. Mac jobs: install and uninstall

Install: `bash mac/install.sh` (preview with `--dry-run`; `--reset-secrets` to change Telegram values; `--test` sends a test message). Needs `gh` installed and logged in with access to wihogan/PEC. Uninstall: `bash mac/uninstall.sh` (`--purge-secrets` also deletes Keychain items). Logs: `~/Library/Logs/pec/`. See `mac/README.md`.
To check the Telegram path from GitHub: Actions, alert-test, Run workflow.

## 8. Rollback

- Bad data published: the last good data is in `state`; re-run refresh with `force` after the source recovers. An earlier page cannot be re-deployed directly; instead revert the code commit on main (GitHub, Commits, Revert) and re-run refresh.
- Bad EPS: run the EPS update again with the correct value (a manual entry dated today beats FactSet until a FactSet report dated after it appears; future dates are rejected by `set_eps.py`), or set `PEC_EPS_MODE` to `override`.
- Stop everything: `bash mac/uninstall.sh` and disable the workflow (Actions, refresh, three dots, Disable workflow). The old laptop process remains as documented in the migration handoff.
- Broken `state`: delete the `state` branch on GitHub; the next run reseeds it from the saved baseline (it logs a loud warning).
