# Phase 6 — Independent QA review (PEC cloud migration, Amendment B)

Reviewer: independent QA agent (wrote none of the code). Date: 2026-11-23, 19:10–19:35 PT.
Refs reviewed: `main` @ 28f4ca8 (production) and `amendment-b` @ e4116f5 (pending merge).
Method: static review of both refs, unit suites run locally on both refs, ad hoc simulations of
`_needs_refresh` / `healthcheck` against real state files, and six live `refresh.yml` runs on
`amendment-b` against `state-test` with `deploy=false`. Production (`state`, `main`, Pages) was not
touched: `state` was 3aa2900 before and after.

Limits: artifacts and the live page cannot be fetched from the sandbox; macOS scripts were reviewed
statically only; Telegram receipt can only be confirmed by the owner; FactSet/Yahoo/FRED/multpl were
exercised only from GitHub runners.

## 1. Verdict

**GO-with-fixes.** No blocker was found. The Amendment B model, the NYSE calendar, the FactSet path
and the gate all behaved as designed in live runs, and the first run after merge will behave sanely
on the old-model production state. Five majors should be fixed; M1 and M3 are small and should go in
before (or with) the merge, M2/M4/M5 within the parallel-run week. Acceptance is not complete until
the 5-day unattended run and UAT are done.

## 2. Blockers

None.

## 3. Majors

### M1 — A slow series that errors while its period is present is never retried, and can hard-fail every later run
- `refresh_v2.py:640-647`: for `monthly`/`quarterly`, the "expected period already present" test
  returns `False` before the error status is looked at. The skip path (`refresh_v2.py:913-918`) then
  copies the `error` status forward on every run. This contradicts the spec line "A failed fetch
  never counts as satisfied" (plan Amendment B, runbook §1). The behaviour is pinned by
  `tests/unit/test_schedule.py:312-315`.
- `healthcheck.py:225-229`: `attempted()` treats any series with status `error` as attempted this
  run. So three stuck FRED slow series make rule 4 report "all 3 attempted FRED series failed" on
  every run in which no other FRED series is fetched.
- Reproduced (script against the `state-test` data): with `cpi_yoy`, `gdp_growth`, `nber_recession`
  set to `error`, `_needs_refresh` is `False` the next day (and still `False` on 2026-12-15 for
  `gdp_growth`); a market-hours run that fetched only Yahoo then returns
  `ok False ['rule 4: all 3 attempted FRED series failed']`.
- How it arises: a `force` run (the runbook recommends `force` "after a bad day") in which 3 or more
  of the six slow FRED series fail while the gate still passes. They are fetched consecutively, so a
  FRED rate-limit burst hits them together.
- Effect: no deploy and one Telegram alert per run (up to 11 a day) until the month/quarter rolls or
  someone forces a clean run.

### M2 — After the close, `intraday` and `daily_after_close` are satisfied by the fetch time, not by the data
- `refresh_v2.py:624-631`: once any successful fetch is stamped after the last completed close, the
  series is skipped until the next market open. Whether that fetch returned the day's bar is not checked.
- Evidence in production now: `state` (3aa2900) has `developed_ex_us`, `emerging_markets`,
  `developed_ex_us_efa`, `emerging_markets_eem` with last observation 2026-11-20, status `ok`,
  `last_fetched 2026-11-23T17:42:15`. Run 45306912366 logged 4248 / 1984 observations for them at
  17:42 PT; run 45312223712 at 18:25 PT got 4249 / 1985. Yahoo delivered the ETF bar more than
  4 h 40 min after the close. The cause on Yahoo's side is not known.
- Evidence of the mechanism: run 45319005518 (`stale_state`) logged
  `[dow_jones] SKIPPED (frequency=intraday, data current)` for data 30 days old.
- Consequence under Amendment B: the 13:20 PT "post-close final" fetch runs 20 minutes after the
  close; the 15:50 PT "retry" and both backup crons then skip these series, so a missing close stays
  missing until 06:45 PT the next trading day (Monday, after a Friday). The healthcheck does not see
  it (rule 5 limit is 7 days). The same weakness exists on `main`; Amendment B does not fix it.

### M3 — The Mac watchdog no longer proves that a post-close refresh happened
- `mac/pec-watchdog.sh:49-60,73`: the check is "at least one successful `[state]` run on `main`
  created since 00:00 PT". This was adequate for Amendment A (two post-close runs). With hourly runs
  from 06:45, one morning success satisfies it even if every run from 07:45 on never fired (Mac
  asleep) and the two GitHub crons were dropped or delayed.
- Also `--limit 20` (`mac/pec-watchdog.sh:58`) counts runs of every branch and state; a day with
  11 production runs plus test runs can push the successful ones out of the window and cause a false alarm.

### M4 — No alert de-duplication at hourly cadence
- `.github/workflows/refresh.yml:296-301`: the `notify` job fires on every failed or cancelled run.
  Any hard failure that persists (a discontinued or renamed ticker tripping rule 5 after 7 days,
  FRED or Yahoo down for a day, M1) produces up to 11 alerts a day (9 Mac + 2 cron) and freezes the
  whole page, because state is not saved and nothing deploys while the gate fails.
- The multpl outage itself does **not** spam: multpl is outside rules 4 and 5. Confirmed live (runs
  45317883934 and 45318738088: 5 multpl failures, `FAILURES (0)`, `RESULT: OK`, notify skipped).
- A queued run that GitHub cancels because a newer one entered the same concurrency group
  (`refresh.yml:50-52`) also sends "PEC refresh FAILED".

### M5 — FactSet updates arrive late and FactSet failures are silent
- `fetchers/factset_eps.py:239-245`: a successful result is reused for the rest of the Pacific day.
  The first run of the day is 06:45 PT and there are no weekend runs. `docs/eps-sources.md:70,198`
  says the report is published on Fridays and recommended a Saturday-morning run. If the PDF is
  posted after 06:45 PT on Friday, the page shows it on Monday 06:45 PT. I could not verify FactSet's
  posting time, so the size of the delay is an inference.
- `refresh_v2.py:402-404,505-511`: a FactSet failure is logged and the status is still `ok`; the
  previous value is carried. The only signal is a `health.json` warning after 45 days
  (`healthcheck.py:62,318-325`), which is six missed weekly reports and sends no Telegram message.
  The page does not show the EPS source or date (`PECtemplate.html` reads only the value). A wording
  change in the PDF therefore gives a stale EPS with no notice.

## 4. Minors

| # | Where | Finding |
|---|---|---|
| m1 | `refresh.yml:98-107` | `state_ref=state` is accepted from any ref; only `eps_value` requires `main`. A dispatch on a feature branch with defaults runs branch code against production state and saves it (the deploy job is blocked by `refresh.yml:283`, the state push is not). |
| m2 | `refresh.yml:108-113` | From `main`, `state_ref=state-test` with `deploy=true` (the default) and no inject publishes test state to the live page. |
| m3 | `refresh_v2.py:642-645` | Once the expected period is present the series is not fetched again until the next period ends, so revisions (GDP second and third estimates, CPI revisions) are missed for up to a quarter. This follows the spec; raise it with the owner. `main` re-fetched once per calendar month. |
| m4 | `refresh.yml:124,126,266,272,294,307,311`; `ci.yml:16-17`; `alert-test.yml:15` | Node 20 actions. Runner annotation in every run: "Node.js 20 is deprecated … forced to run on Node.js 24: actions/checkout@v4, actions/setup-python@v5, actions/upload-artifact@v4" (also `download-artifact@v4`). Works today; move to the Node 24 majors. |
| m5 | `market_calendar.py:31,50-57` | Calendar ends 2027. After that weekdays are treated as full sessions and the only signal is a Python warning in the log. |
| m6 | `refresh.yml:229-232,270-279` | The leak scan covers `PEC.html`, `data_v2.json`, `health.json` but not `refresh.log`, which is uploaded with `if: always()` as an artifact of a public repo (artifacts are not masked). No leak was seen; fredapi errors do not include the key. |
| m7 | `mac/install.sh:137` | Minute is normalised with `10#`, hour is not: the plist gets `<integer>06</integer>` … `<integer>09</integer>`. I expect these parse as decimal but could not run `plutil` here. |
| m8 | `mac/install.sh:153-154` | `bootstrap` directly after `bootout` can fail with error 5 while the old job is still unloading; the result is not checked (only `set -u`). |
| m9 | `mac/README.md`, `mac/install.sh:4` | LaunchAgents in `gui/<uid>` run only while the user is logged in; the docs say only "awake and online". The Pacific-timezone requirement is stated but not checked. |
| m10 | `refresh_v2.py:304-313`; `refresh.yml:211-222` | A source switch with an unchanged value sends "419.64 → 419.64" (seen in run 45319693938 log: `EPS change flag written: 419.64 -> 419.64 (manual override)`). The message is sent before the healthcheck, so if the gate then fails (state not saved) it repeats on every run. |
| m11 | `refresh_v2.py:405-424`; `fetchers/spglobal.py:268-309` | Month-end history is written only when FactSet wins. A month in which the manual override wins throughout gets no entry. Entries are dated the calendar month-end, so the current month's point is in the future (2026-11-30 written on 11-23 in run 45319479573). |
| m12 | `refresh_v2.py:568-571,621` | An unparseable `last_fetched` raises `ValueError` and crashes the run on every attempt until state is repaired. |
| m13 | Treasury fetch | `yield_curve` took 5 min 7 s in run 45317883934 (19:13:52 → 19:18:59). With lag 0 it is polled on every run after the close until Treasury publishes, and on every run the day after a bond-only holiday. Fits the 25-minute timeout. |
| m14 | `ci.yml:3-6` | CI runs only for `main`. `amendment-b` pushes were tested only by the pytest step inside `refresh.yml`. |

## 5. Nits

- `refresh_v2.py:1057-1066`: console banner still says the EPS "needs a weekly manual update" and points to Yardeni.
- `config/series_v2.yaml:500-513`: notes say FactSet reports CY GAAP EPS and is for cross-check only; this contradicts the automation.
- `.github/workflows/diag-multpl.yml` is still on the branch and marked temporary.
- `refresh_v2.py:609-612`: docstring says slow-series errors retry "at most once per Pacific day"; an error whose last success was on an earlier day retries on every run.
- `docs/runbook.md` §8: "a manual entry dated today or later" — `set_eps.py` rejects future dates.
- `requirements-dev.txt:3`: comment says pytest is pinned in `requirements.txt`; it is not.
- `fetchers/factset_eps.py:133`: the size cap is applied after the full download.
- `refresh_v2.py:614-616`: a naive `now` is read in the process zone (tests only; the default is aware).
- `docs/test-report.md` D2: `.github/fail-next-scheduled` is still not implemented.
- The page shows `last_refreshed` without a zone label (CLAUDE.md says times are PT). UI is out of scope.

## 6. Scope items checked and found correct

- **`_needs_refresh` vs spec** (`refresh_v2.py:586-649`): `intraday`, `daily_release`,
  `daily_after_close`, `monthly`, `quarterly` match the Amendment B table, apart from M1.
- **DST**: Mon 2027-03-15 06:45 PDT = 09:45 EDT open; 13:20 PDT closed, fetch once; crons at 15:17
  and 16:47 PDT are after the close. Mon 2026-11-02 06:45 PST open; a Friday 13:20 PDT fetch compares
  correctly with Monday's close. Naive stamps in the repeated/skipped hour parse without error. All
  comparisons are between aware datetimes.
- **Early close and holiday**: 2026-11-26 closed, nothing due; 2026-11-27 open until 10:00 PT, then
  one post-close fetch. The 2026–2027 holiday and early-close tables are internally consistent
  (Juneteenth 2027 observed 06-18, Christmas 2027 observed 12-24, 2027-12-31 a trading day).
- **First run / no state**: a missing `last_fetched` is due; a missing state branch seeds from the baseline with a warning.
- **Old-model production state under the new code**: simulated on `state` 3aa2900. At 19:30 PT
  Monday: 0 of 14 intraday, 5 multpl retried, `wti_crude` polled, nothing else. At 06:45 PT Tuesday:
  14 intraday, 3 delinquency series (Q3 not yet present), `wti_crude`. EPS stays `override` 419.64
  with no change message. History comes from the seed, including the backfill.
- **FactSet**: strict regexes, range checks, jump guard, conflicting values and non-PDF responses all
  fail closed (unit-tested). Precedence, carry-forward and rule 6 are consistent with each other.
  The once-per-day cache and the history file round-trip through `state.sh` (live).
- **Backfill**: all six values recomputed — 7563.63/21.2 = 356.78, 7357.49/20.1 = 366.04,
  7437.63/19.6 = 379.47, 7730.99/19.6 = 394.44, 7704.13/19.2 = 401.26, 7720.39/18.7 = 412.86;
  check 7175.83/17.1 = 419.64. Seed and `docs/eps-backfill.md` agree.
- **Workflows**: every job has explicit minimal permissions (`contents: write` for refresh only;
  `pages` + `id-token` for deploy; `contents: read` elsewhere). All inputs reach shell through
  `env:` and are validated; EPS strings are regex-checked before use. Secrets are scoped to the steps
  that need them; the Telegram URL goes to curl on stdin. Deploy needs `deploy=true` and `main`.
  Concurrency is per state ref without cancel-in-progress. Keep-alive is guarded to `main`.
- **Mac**: weekdays 1–5 × 9 times = 45 calendar entries plus 5 for the watchdog; launchd uses local
  time, so DST needs no change; absolute `gh` path via `~/.config/pec/env`; Keychain for both
  secrets; `bootout` before `bootstrap`; the `[state]` title filter matches scheduled and default
  runs; the BSD `date -j -f` / `date -u -r` branch is correct.
- **Tests**: offline (`--disable-socket`), all paths redirected to `tmp_path`, clocks frozen, no
  wall-clock use. Independent of the owner-editable EPS value: after
  `set_eps.py --value 433.21 --date 2026-11-23` on a copy, 751 passed (D1 fixed).
- **Secrets**: no key-like strings on either ref (only test dummies); `leak_scan: OK` in every run that reached it.

Untested paths: explicit DST cases (they pass in my ad hoc checks), the input-guard shell in
`refresh.yml`, the Mac scripts and the watchdog jq filter, text extraction from a real FactSet PDF
(the unit test uses a synthetic PDF; the live run covers today's layout), M1's sticky-error interaction with rule 4.

## 7. Handoff §11 acceptance criteria

| Criterion | Status | What remains |
|---|---|---|
| Unattended weekday refresh for five consecutive business days, no laptop | NOT YET MET | No `schedule` refresh run and no Mac launchd run exists yet. The 5-day run must start after the merge and after the owner re-runs `git pull && mac/install.sh`. |
| Manual refresh works from a browser | MET (workflow side) | Dispatch works (runs 45312036764, 45313813176). Owner to confirm from the web/phone UI in UAT. |
| 41 series present; parity with the laptop baseline | MET | 41 config ids + derived `fwd_pe_history` in `state` and `state-test`; parity per `docs/test-report.md` 6.2-5 (not re-run by me). multpl parity is trivial while multpl.com is down. |
| All dashboard features in the UAT list work | NOT YET MET | UAT not done; no `docs/uat-signoff.md`. The page could not be viewed from the sandbox. |
| A failed run does not replace the live page and notifies Hogan | MET (workflow side), AT RISK on noise | Production gate shown in 45311178206; on the branch 45319005518 failed at rule 5, skipped save/build, notify sent HTTP 200. Owner must confirm receipt. M3 and M4 weaken this at hourly cadence. |
| No secret in the repo, the logs or the zip | MET | Scan of both refs clean; leak scan OK. The zip was not available to me. See m6. |
| Repo does not grow by megabytes per run | MET | State is a single force-pushed orphan commit; 6.2-11 measured +8.6 KiB over 10 runs, all from doc/config commits. |
| Unit, integration and QA evidence in `docs/`; docs updated | AT RISK | `docs/test-report.md` predates Amendment B and lists 6.2-3, 4, 6c, 10 for re-run; this report covers them on the branch (section 8). 6.2-4b, 8, 9 are still pending observation. Stale text per section 5. |

## 8. Evidence

### Unit suites (Python 3.12, local)
| Ref | Result |
|---|---|
| `main` @ 28f4ca8 | 413 passed, 1 xfailed, 4 deselected |
| `amendment-b` @ e4116f5 | 751 passed, 4 deselected (same count on the runner: `751 passed, 4 deselected in 38.69s`) |

### Live runs (all `ref=amendment-b`, `state_ref=state-test`, `deploy=false`)
Base URL: `https://github.com/wihogan/PEC/actions/runs/<id>`

| Run | Inputs | Result | What it showed |
|---|---|---|---|
| 45317883934 | force=true | success | 36/41 fetched, 5 multpl failed (outage), `FAILURES (0)`, `RESULT: OK`. FactSet fetched from the runner: `EarningsInsight_112026A.pdf`, `EPS 419.64 = 7175.83 / 17.1 (as of 2026-11-20)`; "not newer than override 2026-11-20 — override wins". History 70 entries to 2026-10-31. Cache saved to `state-test`. Deploy and notify skipped. |
| 45318738088 | force=false | success | `1/6 fetched successfully | 35 skipped`: all 14 intraday, 15 of 16 daily_release, 6 slow series skipped; 5 multpl retried and failed; `wti_crude` polled (last obs 11-17). `Reusing today's cached result` (cache restored from the state branch). `RESULT: OK`. |
| 45319005518 | inject=stale_state | failure (expected) | `rule 5: dow_jones last observation 2026-10-24 is 30 days old (limit 7)`; leak scan, save, build skipped; `state-test` unchanged (dfbf329); notify `sent (HTTP 200)`. Covers 6.2-6c. |
| 45319284815 | eps_value=430.00, eps_date=2026-11-23 | success | Working copy only; override wins over FactSet 11-20; forward P/E 16.83, DDM 4401; rule 6 OK. Covers 6.2-10. |
| 45319479573 | eps_value=410.00, eps_date=2026-11-13 | success | FactSet wins: `Using FactSet — value=419.64 as_of=2026-11-20 (override 410.0 dated 2026-11-13)`; state has `source: factset`, `as_of`, `url`; history 71 entries with 2026-11-30 = 419.64; `forward_eps_ntm_history.json` saved; change flag written; rule 6 OK. |
| 45319693938 | defaults (restore) | success | Back to override 419.64; history file restored from the branch; `RESULT: OK`. `state-test` now 677a82e. |

Side effects of my runs: one `[TEST] ⚠️ PEC refresh FAILED` Telegram message (run 45319005518);
`state-test` now also holds `forward_eps_ntm_history.json` with a 2026-11 entry. Nothing else changed.

### Simulations (not committed; scripts kept outside the repo)
- `_needs_refresh` over production `state` at eight instants (merge night, next open, post-close,
  Thanksgiving, early close, weekend, month start): results as in section 6.
- DST, holiday, early-close and bad-input cases under `TZ=UTC`: results as in section 6 and m5, m12.
- M1 reproduction: output quoted in M1.

## 9. Recommended fixes

1. **M1** — In `_needs_refresh`, for `monthly`/`quarterly` check the error first:
   `if errored: return last.astimezone(PT).date() < today` is not enough because `last` is the last
   success; store a `last_attempt` stamp on every attempt (success or failure) and throttle errors on
   that. In `healthcheck.attempted()`, count a series only if its `last_attempt` (or `last_fetched`)
   equals `last_refreshed`. Replace `test_slow_error_with_expected_period_present_is_skipped` and add
   a test for the rule 4 case.
2. **M2** — For `intraday` after the close, also require `last_obs >= last_completed_trading_day`
   (pass `last_obs`, which `run()` already computes) for the exchange-traded Yahoo series; bound the
   re-fetch to the day's remaining runs. Apply the same data test to `daily_after_close`.
3. **M3** — Watchdog: require a successful `[state]` run created after today's close (13:00 PT, or
   10:00 PT on early-close days; simplest is "created at or after 13:00 PT"); add `--branch main` and raise `--limit` to 50.
4. **M4** — In the `notify` job, look up the previous completed production run and skip the alert
   when it also failed, sending one "recovered" message on the next success; or alert only for the
   first failure of the day and for the 13:20 / 15:50 runs. Do not alert when the run was cancelled
   by the concurrency group.
5. **M5** — Reuse the FactSet cache only if it was fetched today after 13:00 PT, or re-check when the
   cached `as_of` is 6 or more days old (a redirect-only request is enough to see a new file name).
   Send one informational Telegram message when FactSet has failed on two consecutive days or
   `as_of` is more than 10 days old; record the FactSet error in `statuses`.
6. **m1/m2** — In "Normalise inputs": refuse `state_ref=state` unless `GIT_REF` is `refs/heads/main`;
   force `deploy=false` when `state_ref` is not `state`.
7. **m4** — Move to the Node 24 majors of `checkout`, `setup-python`, `upload-artifact`,
   `download-artifact`, `upload-pages-artifact`, `deploy-pages` (confirm the current tags; I could not query them here).
8. **m5** — Add a healthcheck warning (and a Telegram info message) when the current date is within
   60 days of the end of `CALENDAR_YEARS`.
9. **m6** — Add `refresh.log` to the leak scan and make the artifact upload depend on the scan result.
10. **m7–m9** — `$((10#$h))` for the hour; check the `bootstrap` exit status and retry once after a
    1 s sleep; document the logged-in requirement; warn in `install.sh` if the system zone is not Pacific.
11. **m10** — Skip the change flag when the rounded values are equal and only the source changed
    from FactSet to an identical override; send the message after the healthcheck step.
12. **m12** — Treat an unparseable `last_fetched` as "never fetched".
13. Clean-up before merge: delete `diag-multpl.yml`; fix the stale texts in section 5; append the
    Amendment B re-run results (section 8) to `docs/test-report.md`.
14. After merge: owner runs `git pull && bash mac/install.sh`, checks `plutil -p
    ~/Library/LaunchAgents/com.pec.trigger.plist` shows hours 6–9 correctly, then the 5-day run and UAT start.
