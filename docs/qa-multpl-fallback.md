# QA review: multpl.com fallback (Amendment D)

- Branch `multpl-fallback`, HEAD 4cd601c (off main 9af13cf). Reviewed 2026-11-25 by an independent reviewer; no code changed, nothing pushed, production not touched.
- Suite: `pytest tests` -> 1155 passed, 4 deselected (integration), working tree clean afterwards.
- Method: code reading plus offline mock runs (requests / yfinance stubbed, health file in a temp dir) using the real Shiller workbook copy, the saved multpl pages in `tests/fixtures/multpl/`, and a clone of the production `state` branch.

## Verdict: MERGE-AFTER-FIXES

No blocker. Two majors (M1, M2) both sit on the multpl-serving path, which is not reachable today (multpl.com is down), so merging as-is would not change production behaviour right now; they will bite on the day the fallback is first used, which is exactly when nobody will be looking at the code. Fix before merge.

What was verified as correct:

| Area | Result |
|---|---|
| Source order shillerdata.com -> multpl -> Yale -> fail closed | Correct (`fetchers/shiller.py:775-807`). Primary success never touches multpl or Yale. |
| Negative caches | One landing attempt, one Yale attempt, multpl marked down after the first network-level failure. All-down run = 3 requests in total (landing, one multpl, Yale). A 200 page without the table = one request per ratio series (4), as specified. |
| Earnings exclusion | `real_earnings` / `nominal_earnings` never request multpl (`_MULTPL_IDS`, `shiller.py:148-153`). |
| Splice | Archive rows <= 2026-10 byte-identical on the multpl path; October rows on a live multpl page do not override the archive; one row per month; first-of-month / month-end conventions and the today clamp match the Shiller path. |
| Duplicates / overlap across source flips | None. Each fetch rebuilds the whole series (archive + appended), so Shiller -> multpl -> Shiller replaces the post-cutoff rows; dates are identical on both paths. |
| Range checks | Applied to the appended multpl rows; failure falls through to Yale, then fails closed. |
| Budget / timeouts | (connect, read) clamped to the remaining 90 s for landing, workbook, multpl and Yale; exhaustion marks multpl down. Worst realistic case (three connect timeouts) is about 30 s; the Refresh step has an 18-minute limit. |
| `source_used` plumbing | Present in series data, `metadata.statuses`, `health.json` series entries. |
| Merge-time against production state | `shiller_health.json` on `state` (`fail_days 0, last_e 2026-09, notified {}`) has no `source` key; the first run adds `source: shiller`, `primary_down_since: null` and sends no notice. `data_v2.json` series gain `source_used: shiller`; values unchanged. |
| Tests | Offline (pytest-socket), health/cache paths redirected by `conftest.py`, meaningful assertions on order, caching, splice, notices. Gaps listed under T1. |

## Findings

### M1 (MAJOR) False "all sources failed" Telegram notice, repeated every day, while multpl is serving

`fetchers/shiller.py:430-446` (`_finalize_run`), `:438-439`.

A day is counted as failing when a run served nothing and at least one series failed. While multpl serves the four ratio series, the two earnings series fail every run (Yale is stale) and keep status `error`, so `refresh_v2._needs_refresh` retries them on every later run. Those later runs attempt only the two earnings series, serve nothing, and are counted as an all-sources failure.

With the real schedule (1:20 PM post-close, then 2:17 / 3:47 / 3:50 PM, then 6:45 AM next day) the mock sequence gives:

| Run | Attempted | fail_days | Notice |
|---|---|---|---|
| D1 1:20 PM | 6 (4 from multpl) | 0 | fallback notice (correct) |
| D1 2:17 PM | 2 earnings | 1 | none |
| D2 6:45 AM | 2 earnings | 2 | "Valuation data fetch (shillerdata.com, multpl.com and Yale) has failed on 2 different days. P/E, CAPE and earnings series are carried forward" |
| D2 1:20 PM | 6 | 0 (re-armed) | none |
| D2 2:17 PM | 2 earnings | 1 | none |
| D3 6:45 AM | 2 earnings | 2 | same notice again |

The message is untrue (P/E and CAPE are current, from multpl) and repeats every morning for the length of the outage. The existing test `test_failing_day_only_when_nothing_served` only runs all six series per run, so it misses this.

Recommended fix: count a failing day only when a multpl-eligible (ratio) series failed in the run, i.e. `any(s in _MULTPL_IDS for s in run["failed"])` with nothing served. An earnings-only retry run then leaves the counter alone. Add a test for the earnings-only retry sequence above.

### M2 (MAJOR) A stale multpl page is accepted for up to 3 months and silently replaces newer data

`fetchers/shiller.py:346-350` (staleness limit), `:353-355` (today clamp).

The only freshness test is "newest multpl month is at most 3 months old". Reproduced with the saved pages as-is (newest row Oct 30, 2026) and shillerdata.com failing:

- Fetched on 2026-11-25, 2026-12-15 and 2027-01-29: accepted, `source_used: multpl`, status `ok`, `provisional_from: None`, series ends 2026-10-30. Rejected only on 2027-02-01.
- The November point that production already holds (P/E 25.03, CAPE 38.23) is dropped, because the series is rebuilt from archive + multpl rows. Carry-forward would have kept it; the fallback makes the page older than doing nothing, with no error and only the one-time "served by multpl" notice. The healthcheck warning for these ids starts at 40 days.
- Variant: multpl frozen at Nov 3 while today is Nov 25: dividend yield is published as 1.05 % dated 2026-11-25 (the month-end clamp stamps today's date on a three-week-old value), which also hides the staleness from the age check.

This is a plausible way for multpl to "come back" (cached or restored pages), and a single bad day at shillerdata.com is enough to trigger it.

Recommended fix: in `_multpl_series`, reject the source (FetchError, not cached as down) unless the newest raw multpl row date is within about 7 calendar days of today; keep the 3-month rule as a backstop. Optionally also refuse a fallback result whose last month is older than the stored series' last month. Add tests for both cases.

### m1 (MINOR) Current-month row choice is inconsistent between series and causes an avoidable step on a source flip

`fetchers/shiller.py:343-345` ("latest-dated row of the month wins"; also the spec wording in Amendment D).

multpl lists two rows for the in-progress month: a snapshot row dated today and a month row dated by the series convention (first of month for P/E, earnings yield, CAPE; month-end for dividend yield). Because the dividend-yield month row carries a later date than the snapshot, "latest wins" picks the month row for dividend yield and the snapshot row for the other three. Confirmed with the saved Oct 30 pages (cutoff moved to 2026-09 for the test): P/E 25.86 (snapshot; month row 26.29), CAPE 40.13 (month row 40.81), dividend yield 1.05 % (month row; snapshot 1.07 %).

The Shiller path uses a month-to-date average price, so the snapshot rows are the wrong match. In a mock with a falling November, a Shiller -> multpl flip moved P/E 25.05 -> 24.71 and CAPE 38.26 -> 37.73 while dividend yield did not move; it steps back on recovery. The October pages show a 1.7 % gap between the two rows. The effect is limited to the provisional month and self-corrects, hence minor.

Recommended fix: for the current month prefer the row dated by the series convention (first-of-month / month-end), falling back to the latest row. I could not confirm against live multpl that the month row is a month-to-date average (site down); the October values are consistent with it.

### m2 (MINOR) Docs contradict the earnings exclusion

- `docs/plan.md` Amendment D "Behaviour": still says multpl covers `real_earnings` and describes its basis; the appended orchestrator decision reverses it.
- `docs/runbook.md:20, :36`: says only `sp500_earnings_nominal` has no multpl source; `sp500_earnings` is also carried forward.
- `fetchers/shiller.py:26-28` (garbled header note), `:31-32`, `:155`, `:347`, `:365-367`: dead `real_earnings` branches and constants.
- `fetchers/shiller.py:29` and plan say "dagger within the last 3 months"; the constant is 2 (current month plus two) - same thing, worded differently.

### m3 (MINOR) "Back on shillerdata.com" can fire while the ratio series are still multpl data

`fetchers/shiller.py:409-419, :497-499`. An earnings-only retry run that finds the primary up records `source: shiller` and sends the back-message, but the four ratio series are not due until the next post-close run and keep `source_used: multpl` until then. Harmless, slightly early.

### Nits

- N1 `fetchers/shiller.py:491-496`: the fallback notice says "shillerdata.com unavailable" also when the workbook loaded but was rejected as stale.
- N2 `fetchers/shiller.py:508-509`: the failing/stale message still points to Amendment C.
- N3 `refresh_v2.py:1068-1075`: on a failed attempt `statuses[sid]` loses `source_used` (so `health.json` drops it) while the carried-forward series data keeps it.
- N4 `fetchers/multpl.py:44-52`: the multpl request sends a browser User-Agent; the Shiller requests send an identifying one.
- N5 Intermittent primary failures produce a fallback/back message pair per flip (one per day at most); acceptable, not spam.
- N6 Pre-existing (Amendment C m3, unchanged): a primary workbook that loads but fails the derived range or price-staleness check does not fall over to multpl and records a success in the health file.

### T1 Test gaps

- No run sequence with a partial series list (would have caught M1).
- No test for a multpl page whose newest row is old within the 3-month window (M2), nor for the stale value being dated today.
- No test that pins which row wins when the month row and the snapshot row are both present for P/E / CAPE versus dividend yield on a real page (m1); `NOV_ROWS` uses synthetic rows only.
- No cross-day Shiller -> multpl -> Shiller value test on the same month.

## Reproduction

Mock harness used (not committed): requests router + yfinance stub around `shiller.fetch` / `shiller.pending_notice`, real workbook at `/home/claude/xls_check/ie_data.xls`, saved multpl pages with injected November rows, health file in a temp directory. Scenarios: source flip on one day, the post-close / retry / next-morning sequence for three days, saved pages fetched on later dates, multpl frozen early in the month, production health file with primary up, all-down request count, 200 page without table.

## Re-check (Opus, 2026-11-25)

- Branch `multpl-fallback` at 97f2040 (fixes 4b9678e, tests 97f2040), checked out as local `qa-m`. Independent reviewer, fresh context. No code changed, nothing pushed, production not touched (the `state` branch was only cloned read-only to `/tmp/qa3state`, commit 04efa02).
- Suite: `pytest tests` -> **1175 passed, 4 deselected**, 1 warning (pandas NaT). CI on 97f2040: `ci` success (run 45698676574).
- Reproductions: a separate, uncommitted pytest file (`/tmp/qa_recheck/test_recheck.py`, `test_edges.py`) loads the repo `conftest` as a plugin and reuses the `net` / `page` / `multpl_ok` helpers from `tests/unit/test_multpl_fallback.py`. It runs `refresh_v2.run()` end to end on the six Shiller series, seeded with the production `data_v2.json` and `shiller_health.json`.

### Verdict: MERGE

Both majors are resolved and reproduce as fixed. No blocker and no major remain. One new minor (R1) is a narrower version of M1. It needs a partial multpl failure, so it is worth a follow-up but does not block the merge. The fallback path is also unreachable today because multpl.com is down.

### Status of the original findings

| # | Status | Evidence |
|---|---|---|
| M1 | **RESOLVED** (residual R1 below) | `fetchers/shiller.py:446`: a failing day is counted only if a `_MULTPL_IDS` series failed. Reproduced through `refresh_v2.run` with the real retry scheduling (`_needs_refresh`), shillerdata.com down, multpl fresh, Yale stale, over 11 runs from Mon 11-16 13:20 to Thu 11-19 06:45. Post-close runs attempt 6 series and retries attempt 2 (earnings only). `fail_days` stays 0 throughout, and exactly one notice is sent ("served by multpl.com fallback"). No "has failed" notice. Tests: `test_m1_*` (`tests/unit/test_multpl_fallback.py:628-662`). |
| M2 | **RESOLVED** | `fetchers/shiller.py:344-350`: the freshness check runs on raw rows dated on or before today, with a 7-day limit, and is not cached as down. Reproduced with the saved pages as-is (newest Oct 30/31), today 2026-11-25, primary down, Yale stale, and **production's own data_v2.json** as the prior. All six series are carried forward byte-identical, including the November points (P/E 2026-11-01 25.0317, CAPE 38.227, DY 2026-11-25 0.0111). Statuses are `error` with "multpl data stale", there are 4 multpl requests (one per page), and `fail_days` is 1. The future-dated Nov 30 month-end row does not mask the frozen page (`test_m2_future_month_end_row_does_not_mask_frozen_page`). |
| m1 | **RESOLVED** (nit N7) | `fetchers/shiller.py:353-358`: the convention-dated row wins, with the latest row as fallback, applied to all four series. Covered by `test_row_rule`, `test_row_rule_ended_months` and `test_flip_shiller_multpl_shiller_same_month` (`:757-820`). |
| m2 | **PARTIAL** (nit-level leftovers) | Plan, runbook and dead code are fixed. Two leftovers remain: the `fetch()` docstring `fetchers/shiller.py:773` still says "multpl.com (not for nominal_earnings)" (should say "not for the earnings series"), and the header at `:36` reads `# # Source discovery`, a stray `#` introduced by 4b9678e. |
| m3 | **ACCEPTED-DEFERRAL** | The reason holds: the message is harmless. The plan's "early by at most one run" understates it slightly, because the ratio series keep `source_used: multpl` until the next post-close run, which can be about a trading day later. Still cosmetic. |
| N1 | ACCEPTED-DEFERRAL | Wording only. |
| N2 | **RESOLVED** | `fetchers/shiller.py:517` now points to Amendment D. |
| N3 | **RESOLVED** | `refresh_v2.py:1075-1076`; `healthcheck.py:235-236` passes it through to `health.json`. Asserted in `test_m2_rejection_carries_november_point_forward`. |
| N4 | ACCEPTED-DEFERRAL | The reason given ("multpl blocks identifying agents") is plausible but cannot be verified from this sandbox. |
| N5 | ACCEPTED | |
| N6 | ACCEPTED-DEFERRAL | Pre-existing from Amendment C and unchanged. |
| T1 | **RESOLVED except one gap** | Added tests: an earnings-only retry sequence, the 7-day boundary on saved and injected pages, future-dated rows, carry-forward through `refresh_v2`, the row rule per series, and a same-month Shiller -> multpl -> Shiller flip. Still missing: a run sequence where only **some** ratio series fail on multpl (this would have caught R1). |

### New findings

**R1 (MINOR). The false "all sources failed" notice still repeats daily when only one ratio series fails on multpl.** Location: `fetchers/shiller.py:442-447`. In the post-close run, three ratio series are served by multpl, so the streak is cleared. The one failed ratio series stays `error` and is retried on every later run, together with the earnings series. Those retry runs serve nothing, and a ratio series failed, so each one counts as a failing day. I reproduced this with the dividend-yield page broken (200 response, no table) and, separately, with only that page frozen at Oct 31:

| Run | Attempted | fail_days | Notice |
|---|---|---|---|
| 11-16 13:20 | 6 | 0 | fallback notice (correct) |
| 11-16 14:17 | 3 | 1 | none |
| 11-17 06:45 | 3 | 2 | "Valuation data fetch (shillerdata.com, multpl.com and Yale) has failed on 2 different days. P/E, CAPE and earnings series are carried forward" |
| 11-17 13:20 | 6 | 0 (re-armed) | none |
| 11-18 06:45 | 3 | 2 | the same notice again (and again on 11-19) |

The message is untrue, because P/E and CAPE are current from multpl, and it repeats every morning for the length of the outage. This is the same symptom as M1, but the trigger is narrower: a single page failing to parse, failing the range check or going stale while the others are fine. All four pages share one template and are updated together, so this is unlikely, which is why I rate it MINOR. Suggested fix: when something is served, record `last_served_date = today` in the health file, and in `_finalize_run` skip `_record_failure` when `last_served_date` is today or the previous PT day. Equivalently, count a failing day only when no series has been served by any source since the last post-close run. Add the partial-failure sequence as a test.

**N7 (nit). Row-rule tie when the snapshot date equals the convention date.** On the last day of a month the dividend-yield snapshot and the month-end row are both dated that day. The same happens for P/E, EY and CAPE on the 1st. The stable sort then keeps the row multpl lists first, which is the snapshot. Probe on Nov 30: snapshot 1.20 % kept over the month row 1.09 %. This lasts one day and only on the multpl path. Optional fix: break ties on the original page order, so that the second-listed (month) row wins.

**N8 (nit, carried over from M2's optional suggestion, not adopted).** On the first trading day of a month, if multpl has not yet added the new month's rows (newest snapshot is the previous day, which passes the freshness check), a Shiller -> multpl flip drops production's new-month point. The series ends at the previous month. This self-corrects the next day. Acceptable.

**Other regression checks on 4b9678e, no issues:**
- 7-day rule versus weekends and holidays: valuation fetches happen after the close on trading days, and the longest market closure is well under 7 days.
- The rejection is not cached as `down`, so each ratio series re-checks the same cached page, with no extra requests (4 requests in total).
- The removed 3-month rule is implied by the 7-day rule.
- The month-end clamp can still date a value up to 7 days old as today. This is bounded and documented.
- `source_used` on a failed attempt only copies the existing series value. With production data, which has no `source_used` yet, nothing is added, which is correct.
- Earnings-only failures never touch `fail_days` or `primary_down_since`.
- No new paths to Telegram notices beyond R1.

### Merge-time check against production state

`/tmp/qa3state` at 04efa02: `shiller_health.json` is `{fail_days 0, last_fail_date null, last_e 2026-09, notified {}}`. The valuation statuses were last fetched 2026-11-25 11:57 PT, so they are due at the first post-close run. I simulated that run at 2026-11-25 13:20 PT with the primary up, using the real workbook copy `/home/claude/xls_check/ie_data.xls` (^GSPC stubbed off, so Shiller's P is used and values differ slightly from a real run). Results:
- Exactly 2 requests (landing, workbook). No multpl or Yale request.
- All six series are `ok`, with `source_used: shiller` in both the series and the statuses. `provisional_from` is unchanged in kind.
- `shiller_health.json` gains `source: shiller` and `primary_down_since: null`. `fail_days` stays 0 and `notified` stays `{}`.
- No notice text is written to `eps_change.txt`.
- I merged the six refreshed series into the full production `data_v2.json` and ran `healthcheck.run_checks` with the real config. There are no failures apart from rule 3, which is expected because no HTML was rendered here. Rule 2 passes with no missing or unknown ids, and rules 4 and 5 pass. There are no valuation warnings, and `health.json` series carry `source_used: shiller`.
