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
