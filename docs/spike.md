# Phase 3 — Connectivity spike: verdict

Analyst: Opus, 2026-11-23. Evidence: `spike-results.jsonl` (10 runs), `spike.yml` run list (10 runs),
forced `refresh.yml` run 45305532774 (state-test, deploy=false, `--force`, `PEC_EPS_OVERRIDE_ONLY=1`,
finished 2026-11-23 17:31 PT), laptop baseline `tests/fixtures/baseline_data_v2.json` (2026-11-20 14:18 PT).

## Verdict: **GO** (GitHub-hosted runners as the refresh host), with conditions

- Go criterion met: Yahoo 14/14 in **11/11 runs (100 %)** vs ≥ 90 % required; FRED, Treasury 100 %;
  multpl 10/11 runs (one whole-source outage, harmless by design — see §2).
- Conditions: (1) Amendment A stands — the Mac launchd job is the primary trigger; GitHub `schedule`
  is backup only (§4). (2) `PEC_EPS_OVERRIDE_ONLY=1` stays on (§3; already in `refresh.yml`).
  (3) Healthcheck thresholds unchanged (§5).

## 1. Per-source success

| Source | Spike (10 runs) | Forced Mon run | Total series-fetches | Runs fully ok |
|---|---|---|---|---|
| Yahoo (14) | 140/140 | 14/14 | 154/154 | 11/11 |
| FRED (21) | 210/210 | 21/21 | 231/231 | 11/11 |
| Treasury (1) | 10/10 | 1/1 | 11/11 | 11/11 |
| multpl (5) | 50/50 | **0/5** | 50/55 | 10/11 |

`fred_key_present: true` in all spike runs; `refresh_rc` 0 in all. Spike: 1 `workflow_dispatch`
(Fri 15:21 PT, after close) + 9 `schedule` runs (Fri night through Mon 07:03 PT, mostly weekend).
Limitation: no spike run fell inside weekday market hours; post-close weekday coverage is the Friday
dispatch and the Monday forced run only. Phase 7 parallel run covers the rest.

## 2. Timings, errors, the multpl event

- Elapsed (cold `--force`): 331–342 s, mean 336 s (n=10). Spread 11 s — stable. Well under any job timeout.
- Spike `errors`: empty in all 10 runs. Only errors anywhere: the 5 multpl failures in the forced run,
  all `[Errno 111] Connection refused` to `www.multpl.com:443`, same run, consecutive.
- Cause categories (cannot be distinguished from one event; could not probe multpl from the analysis
  sandbox — egress proxy blocks it):
  1. Transient multpl origin/CDN outage or maintenance (ECONNREFUSED = active TCP reset, not a timeout or
     HTTP 403/429). Most likely.
  2. Per-IP refusal of that runner's Azure egress address (rate-limit/blocklist). Not systematic:
     10/10 earlier runners on different IPs succeeded.
  3. DNS returning a stale/dead address. Least likely.
- `multpl._download` does a single `requests.get(timeout=30)` with no retry ("Max retries exceeded" is
  urllib3 wording for retries=0).
- Impact (verified in `refresh_v2.py`):
  - On failure the series is carried forward (l.776–778) and its status keeps the **previous**
    `last_fetched` (l.782).
  - `_needs_refresh` for `monthly` compares `last_fetched` month to the current month (l.485–486).
    So: a failure on the first run of a month leaves `last_fetched` in the prior month → retried on
    every subsequent run (2/day) until it succeeds. A mid-month failure after that month's success
    (as here: `last_fetched` 2026-11-02) is **not** retried until next month — but the carried data is
    already that month's, so no data is lost.
  - Healthcheck: multpl is governed by staleness only (warn > 110 d; `sp500_earnings` > 220 d); rule 4
    ignores it. The forced run passed its in-run healthcheck. Ages after the failure: 23–24 d
    (`sp500_earnings` 146 d) — all inside warn limits.
  - Side effect: the `error` status (and its healthcheck warning) persists in `data_v2.json` until the
    next successful fetch — up to ~5 weeks for a mid-month failure. Cosmetic.
  - Production runs are non-forced (`force` is dispatch-only, default false), so multpl is contacted
    only until it succeeds once per month.
- Mitigation now: **none required.** Recommend: count multpl errors during Phase 5/7; if a refusal
  recurs, add one retry with backoff in `multpl._download` (same pattern as the approved Yahoo retry;
  no source change).

## 3. EPS live paths from a runner

All 10 spike runs (`PEC_EPS_OVERRIDE_ONLY` unset) ended `source: override` (354.84, 2026-05-07), i.e.
every live path failed (live success would set `source: live`, `refresh_v2.py` l.315–350):

| Path | Result (10/10 runs) |
|---|---|
| S&P Global xlsx | HTTP **403 Forbidden** |
| FactSet Earnings Insight PDF | downloaded, **not parsed** (no `pdfplumber`/`pdfminer` installed) |
| Yardeni chart page | attempted; no success line logged → no value |

Implication: confirms `PEC_EPS_OVERRIDE_ONLY=1` (plan §3/B5). Live EPS is not viable on runners today;
EPS is maintained via the `set_eps` dispatch. (Adding a PDF parser could revive the FactSet path —
out of scope.)

## 4. Schedule reliability (`23 * * * *`, pushed 2026-11-20 23:20 UTC)

- Hourly slots 2026-11-20 23:23 → 2026-11-23 14:23 UTC (last observed run): **64**. Scheduled runs
  started: **9 (14 %)**. ~86 % of slots silently dropped.
- First scheduled run 4.8 h after push. Gaps between runs: 4.3–11.2 h (median 7.5 h).
- Delay vs the nearest preceding :23 slot (lower bound): 5–50 min, median 27 min.
- All 9 that started succeeded.

Implication: GitHub `schedule` is unfit as a primary trigger for a twice-daily, time-sensitive job.
**Confirms Amendment A**: Mac launchd (13:20 / 15:50 PT) primary; GitHub crons backup;
watchdog (16:30 PT) catches the case where neither produced a successful run.

## 5. Data ages and final thresholds

Ages in the forced run (Mon 2026-11-23 17:31 PT, `age = today − last non-null obs`, PT calendar days):

| Group | Last obs | Age | Current limit |
|---|---|---|---|
| Yahoo indices (^GSPC ^DJI ^IXIC ^RUT), metals futures | 11-23 | 0 | 7 |
| Yahoo ETFs (VEA VWO EFA EEM) | 11-20 | 3 | 7 |
| Yahoo crypto (BTC ETH XRP) | 11-24 | −1 | 7 |
| FRED rates/TIPS/DFF/HY/AAA (11) | 11-20 | 3 | 7 |
| FRED FX (EUR CAD JPY) | 11-20 | 3 | 14 |
| `wti_crude` | 11-17 | 6 | 14 |
| `yield_curve` (Treasury) | 11-23 | 0 | 7 |

Baseline (Fri 11-20 14:18 PT) for the weekly batches: FX last obs 11-13 (age 7), WTI 11-17 (age 3).

Negative ages: `healthcheck.py` fails only on `age > limit` (l.259), so −1 passes; confirmed by the forced
run's `health.json` (crypto −1, no failure). No unit test covers it — add one.

Worst expected ages in normal operation (runs Mon–Fri ~13:20 PT; FRED daily ~1 business-day lag):

| Series | Normal max | Holiday case | Margin at limit |
|---|---|---|---|
| Yahoo `market_daily` | 3 (Mon, if bar lag) | Mon 12-28 / Mon 01-04 with bar lag: 4 | 3 |
| FRED daily rates | 3 (Mon) | Mon 12-28 → 12-24: 4; Tue after a Mon holiday: 4; ICE (HY/AAA) extra lag: 5 | 2 |
| FRED FX (H.10, weekly Mon) | 7 (Fri), 10 (Mon before ingestion) | Tue after Mon holiday, H.10 late: 11–12 | 2 |
| `wti_crude` (weekly EIA) | 7–8 | holiday-week EIA slip: ~10 | 4 |
| `yield_curve` | 1; 3 (Mon) | Mon 12-28 → 12-24: 4 | 3 |

Thanksgiving (Thu 11-26) and the 11-27 half-day add at most 1 day (Fri FRED shows Wed 11-25 → age 2).

**Recommended final thresholds — keep current values:** `market_daily` 7; `daily_once`/`yield_curve` 7;
FRED FX (`eur_usd`, `cad_usd`, `jpy_usd`) 14; `wti_crude` 14. Each tolerates the observed and
holiday worst cases with ≥ 2 days margin while still failing within ~2 business days of a real stall.
Tightening `market_daily` to 5 would risk false failures on Christmas/New Year Mondays (ICE lag).

### ETF one-day lag (VEA/VWO/EFA/EEM)

- Laptop baseline Fri 14:18 PT had the same-day 11-20 bar for all four ETFs → not a structural ETF lag.
- Runner Mon 17:31 PT: indices/futures had 11-23, ETFs stopped at 11-20. Same run, crypto is missing the
  just-completed 11-23 (UTC) bar (dates …11-22, 11-24) — no such gap in the baseline. Pattern: Yahoo's
  history occasionally omits the most recently completed daily bar. `yahoo.py` drops only NaN rows, so
  the bars were absent/NaN in Yahoo's response, not filtered by us.
- Impact: not a healthcheck problem (age 3 ≤ 7; self-heals on the next fetch, which re-downloads full
  history). Display impact: a missed bar at the 13:20 PT run persists until the next day, because the
  15:50 PT run skips `market_daily` series fetched after the close (`refresh_v2.py` l.472–478).
- Action: track in Phase 7 (ETF/crypto last-obs at each Mac-triggered run). If it recurs, consider letting
  the 15:50 run re-fetch Yahoo series whose last obs < last business day.

## 6. Parity vs laptop baseline (forced run vs baseline, all overlapping dates)

| Source | Series checked | Overlap | Result |
|---|---|---|---|
| FRED daily | all 15 (`fed_funds` … `aaa_yield`, `wti_crude`, FX) | 783–6,167 dates each | **exact** (0 diffs) |
| FRED monthly/quarterly | 6 | 86–261 | exact |
| Treasury `yield_curve` | 13 tenors | 4,224 dates | exact |
| Yahoo indices + ETFs | 8 | 1,984–7,015 | max rel diff 5.8e-6 (adj-close rounding) |
| Yahoo metals + crypto | 6 | 1,986–2,881 | exact except 2026-11-20 (baseline's last, provisional bar): gold 0.67 %, BTC 0.43 %, XRP 0.32 %, PL 0.26 %, SI 0.11 %, ETH 0.05 % |
| multpl | 5 | 198–563 | exact (carried forward from baseline; live failed) |

The only > 0.5 % difference (gold 11-20) is the laptop capturing a provisional futures bar that Yahoo
later finalised; not a runner issue. Spike runs ran multpl 5/5 live, so parsing on runners is proven.

## 7. Follow-ups

Phase 4
- Enable the GitHub backup crons (`# ENABLE AFTER PHASE 3 GO`); delete `spike.yml`.
- Keep `PEC_EPS_OVERRIDE_ONLY=1` and THRESHOLDS as is.
- Add a healthcheck unit test: negative age (−1) passes.

Phase 5 / 7
- Log per-source failures per production run; count multpl refusals. Recurrence → add one retry with
  backoff in `multpl._download`.
- Record ETF and crypto last-obs at each 13:20 / 15:50 PT run; recurrence of the missing-bar pattern →
  revisit the 15:50 skip rule for Yahoo.
- Verify 6.2-9: Mac-triggered dispatches appear at ~13:20/15:50 PT; note how many GitHub backup crons
  actually fire (expect sparse).
- Runbook note: an `error` status on a monthly series can persist until next month's fetch; it is
  warn-only.
