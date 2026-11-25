# Independent QA — `shiller-replace` (Amendment C: multpl replaced by Shiller's dataset)

Reviewer: independent QA agent (wrote none of this code). Date: 2026-11-24, evening PT.
Ref: `shiller-replace` @ 8a045ed, branched from `main` @ 3b8a3e8 (merge is a fast-forward).
Method: static review; unit suite; the fetcher run offline against the real workbook copy
(`/home/claude/xls_check/ie_data.xls`, 1,666,048 bytes, not committed) with the network and the
month-to-date price stubbed; the log of diag run 45550604843. Nothing was dispatched, pushed or
changed. Production `state` was read only (846d3e3).

## 1. Verdict

**MERGE-AFTER-FIXES.** The formulas, month parsing, splice and fail-closed behaviour are correct and
match Amendment C. One fix should go in before the merge (M2, a few lines). M1 must be in before the
first month rollover on 2026-12-01. M3 can follow.

## 2. Blockers

None.

## 3. Majors

### M1 — The month that has just ended falls back to Shiller's single-day price, and a skipped Shiller update leaves a hole
- `fetchers/shiller.py:243-275`: only the current Pacific month gets our month-to-date average.
  Every earlier month uses Shiller's `P`. Shiller's last row is not a monthly average: in the real
  workbook 2026-11 is `7619.38` (two decimals), while 2026-10 is `7726.692727`, which equals the
  mean of the 22 October `^GSPC` closes in production state (7726.69).
- The November average so far is 7410.53 over 17 closes (diag run) against Shiller's 7619.38, a 2.8 % gap.
- Reproduced with the real workbook: on 2026-11-30 November is P/E 25.04, CAPE 38.24; on
  2026-12-01, with the workbook unchanged, November becomes P/E 25.78, CAPE 39.37. The completed
  month gets less accurate overnight and stays so until Shiller republishes. Dividend yield and
  earnings yield move the same way.
- Same cause, second effect: `shiller.py:258-273` appends only the current month. With the workbook
  ending 2026-11 and today 2027-01-05 the P/E dates end `2026-11-01, 2027-01-01`; December is
  missing. With today 2027-02-10 the fetch still succeeds (status `ok`), with December and January missing.
- The headline tiles read the last point, so they stay right. The chart and the 60-point CAPE
  average (`refresh_v2.py:891`, `PECtemplate.html:1941`) take the wrong or missing months.
- This follows the decision text "prior months use Shiller's P". The decision does not mention that
  Shiller's last row is a snapshot, so I am raising it rather than treating it as accepted.

### M2 — A slow or hanging Shiller host can fail the whole refresh
- `fetchers/shiller.py:135-156`: a failed workbook load is not remembered, so each of the six
  series repeats the landing request (`timeout=60`, line 104) and the Yale fallback
  (`timeout=120`, line 121).
- Reproduced with both hosts timing out: 6 landing and 6 Yale requests in one run, 1,080 s worst
  case. The "Refresh" step is limited to 18 minutes (`refresh.yml:206`). The step then fails,
  nothing is deployed, and an alert is sent, on every run while the host hangs (failed series are retried each run).
- A refused connection is fast and harmless; this needs a host that accepts and stalls.

### M3 — The valuation strip can go stale with no message
- All Shiller failure modes (site down, link gone, layout change, range check, stale workbook)
  correctly end in `FetchError` and carry-forward. The only signal is a warning in `health.json`:
  after 7 days for the two month-end series, after 40 days for P/E, earnings yield and CAPE
  (`healthcheck.py:60-62`). Shiller is outside rules 4 and 5, so there is never a Telegram message.
- This is the same situation that left multpl dead from 11-23 until someone looked. FactSet got a
  two-day notice in the last round; Shiller has none.

## 4. Minors

| # | Where | Finding |
|---|---|---|
| m1 | `refresh_v2.py:1042-1044`; `healthcheck.py:213` | `sp500_earnings_nominal` is not in production state. If the first Shiller fetch after merge fails, the id is absent and rule 2 hard-fails the run (no deploy, one alert). It clears on the next good run. The diag run shows the source works from a runner, so the risk is a transient error on the first run. |
| m2 | `shiller.py:210-232` | The month-to-date price is only checked against 100–100,000. There is no comparison with Shiller's own price for the month. A wrong Yahoo response is caught only if it pushes a ratio out of range. |
| m3 | `shiller.py:86-92,310-318` | A value outside the range (for example P/E above 60 in an earnings collapse) fails closed for good; the series then freezes with warnings only (see M3). |
| m4 | `shiller.py:145-148,246-247` | A workbook is accepted with earnings up to 9 months old and prices up to 3 months old. Inside those limits everything reports `ok`. |
| m5 | Licensing | Not determined. Web access to `shillerdata.com` needed an approval that was not available in this session, and neither `docs/plan.md` nor the research brief records its robots.txt or terms. The owner should read both once. In favour: one landing request and one 1.6 MB file per trading day, and a descriptive User-Agent (`shiller.py:72`). The file is served from a third-party CDN (`img1.wsimg.com`). |
| m6 | `shiller.py:252-254,294` | Real EPS is re-based to the last CPI in the frame, which for the newest one or two months is Shiller's own estimate, not a published figure. The tail of real EPS drifts down while nominal E is carried flat (297.18, 296.07, 295.52). Not shown in the UI. |

## 5. Nits

- `PECtemplate.html:1919-1934`: the two archived October rows (10-01 and 10-30) are both drawn, and
  the P/E line takes the later row for both (month-key lookup, last write wins). The November point
  at 11-01 sits two days after the 10-30 row, so the line steps sharply there. This was already so
  in the baseline. No future-dated points: 11-01 is not after "now".
- `docs/migration-handoff.md`, `docs/plan.md:99` still say 41 series; it is 42.
- `shiller.py:298-299`: the date-index expression has a redundant branch; results are correct.
- `fetchers/multpl.py` and its tests remain, unused, as documented.

## 6. Checked and found correct

- **Formulas** (`shiller.py:283-294`): DY = D/P, P/E = P/E, EY = E/P, nominal EPS = E, real EPS =
  E × CPI_latest / CPI_m, CAPE = Shiller's column. They match the research brief §2. With the real
  workbook: September P/E 7669.41/295.52 = 25.95 against multpl 25.96; October `P` equals the mean
  of daily closes.
- **CAPE for the current month** (`shiller.py:258-272`): Shiller's CAPE × P_ours / P_shiller; with
  no Shiller row, (P_ours/CPI_est) / (P_prev/CPI_prev/CAPE_prev). Both are algebraically right.
  Diag run: 39.374 × 7410.53 / 7619.38 = 38.295, as logged.
- **Month parsing** (`shiller.py:194-200`): 1,871 rows, 1871-01 to 2026-11, no duplicates, 12
  months in every year except 2026 (11). 2026.1 is read as October and 2026.01 as January.
  Columns are taken by position with a name check; CAPE by exact name, not "TR CAPE".
- **Archive** (`config/multpl_archive.json`): all four series are identical, dates and values, to
  `tests/fixtures/baseline_data_v2.json` and to current production state. Sorted, no duplicate
  dates, no nulls, last date 2026-10-30 or 2026-10-31, exactly one month (2026-10) with two rows.
- **Splice** (`shiller.py:359-365`): archive rows up to 2026-10-31 unchanged; Shiller rows strictly
  after. Output sorted, no duplicate dates. Date conventions hold: first-of-month for P/E, EY and
  CAPE; month-end for DY and both EPS series, with the in-progress month dated today (11-24 in my run).
- **Fail closed**: missing link, non-OLE2 body, header or column change, empty columns, stale Yale
  copy (ends 2023-09, rejected), stale prices, missing archive, out-of-range values all raise
  `FetchError`; the orchestrator carries the previous data forward. A failed month-to-date download
  falls back to Shiller's price and is cached as failed for the run.
- **Once per day**: `daily_after_close` is satisfied by fetch time; the six series share one
  in-process workbook and one month-to-date download (`test_workbook_fetched_once_for_all_series`).
  So one landing request and one 1.6 MB download per trading day; after an error, once per run.
- **Healthcheck**: series count is config-driven (42); the 40-day warn fits first-of-month dating;
  no new hard-fail rule and no new alert path apart from M2 and m1.
- **First run after merge**: the five existing ids are in `error` in production state, so they and
  the new id are fetched on the first run at any time of day.
- **Tests**: 1056 passed, 4 deselected. 47 Shiller tests, offline (fake `requests.get`, synthetic
  `.xls` fixtures, frozen clock, caches reset). They cover discovery, parsing, staleness limits,
  splice exactness, ranges, provisional flags and the run-level passthrough. Not covered: the
  month-rollover value of the ended month (M1), repeated failures across the six series (M2), and
  a real-workbook fixture (only synthetic files are committed, which is the right call for size).

## 7. Evidence

- Diag run 45550604843 (`diag-shiller`, d7aa330, success, 2026-11-24 21:06 PT):
  `using shillerdata.com workbook (1666048 bytes); last P 2026-11, last E 2026-09`;
  `MTD ^GSPC average 7410.53 over 17 closes`; November P/E 25.076, EY 3.988 %, CAPE 38.295;
  archive tails printed and equal to the archive file. That run predates bea7050, so it shows the
  dividend-yield point dated 2026-11-30; at 8a045ed my offline run dates it 2026-11-24.
- Offline runs against the real workbook: outputs quoted in M1, M2 and section 6.
- Suite: 1056 passed, 4 deselected (Python 3.12, local).

## 8. Recommended fixes

1. **M2 (before merge).** In `_get_monthly`, store the failure in `_WORKBOOK_CACHE` and re-raise it
   for the remaining series in the same process; cut the timeouts to about 15 s (landing) and 45 s
   (file). Add a test that six fetches make one landing request when it fails.
2. **M1 (before 2026-12-01).** Compute our own monthly averages of daily `^GSPC` closes for every
   month from Shiller's last price month onward, not only the current month: one Yahoo download
   starting at the first day of that month. Use them for the ended months (scaling CAPE the same
   way) and fill any month Shiller has not yet added. Keep those months provisional until Shiller's
   row is no longer the last one. Add tests for the 12-01 rollover and for a skipped update.
3. **M3.** Reuse the FactSet pattern: one informational Telegram message when the Shiller series
   have failed on two consecutive Pacific days, and one when the workbook's last price month is
   more than one month behind.
4. **m1.** On the first production run after merge, watch the log for the six `[..] OK` lines; if
   the Shiller fetch fails, re-run. Optionally let a new id with no data be a warning for its first day.
5. **m2.** Reject a month-to-date price more than about 15 % away from Shiller's price for the same
   or previous month and fall back to Shiller's.
6. **m5.** Owner reads shillerdata.com's robots.txt and any terms once and records the result in `docs/plan.md`.
7. Update the "41 series" texts to 42.
