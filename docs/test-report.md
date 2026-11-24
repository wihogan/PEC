# Phase 5 — Integration test report (plan §6.2 + Amendment A)

Tester: Opus (Phase 5 worker), 2026-11-23 18:15–18:50 PT. Runs: `https://github.com/wihogan/PEC/actions/runs/<id>`.
Code under test: `refresh.yml` / `refresh_v2.py` / `healthcheck.py` as on `main` at dd652a4…9eb6f40 (no code
changes in that range other than the EPS config value). Amendment B code was **not** under test (still on
branch `amendment-b`); items 3, 4 and 4b must be re-run once it lands because it changes the skip rules.

Constraints: artifacts and `wihogan.github.io` cannot be fetched from the test sandbox, so evidence comes from job logs (`gh api …/jobs/<id>/logs`),
clones of `state` / `state-test`, and the Pages deployments API. Telegram receipt can only be confirmed by the owner.

## Summary

| Result | Count | Items |
|---|---|---|
| PASS | 7 | 2, 3, 4, 6a (workflow side), 6b, 7 (workflow side), 12 |
| PASS with note | 2 | 5 (one explained out-of-tolerance point), 11 (no pre-Phase-5 baseline existed) |
| FAIL / BLOCKED | 2 | 6c, 10: blocked by defect **D1** (unit-test gate broken since EPS commit 9eb6f40) |
| PENDING observation | 3 | 4b, 8, 9 |
| OWNER to confirm | 3 | Telegram receipt for 6a, 6c-run, 7; also all Mac items in 8/9 |
| Not run in CI (optional) | 1 | 1 |

## Results

| Item | What was done | Run(s) | Result | Evidence |
|---|---|---|---|---|
| 6.2-1 live integration (`pytest -m integration`) | Not wired into any workflow, so not run. Instead, every source was exercised live from GitHub runners: 10 spike runs + 45305532774 (forced, state-test), 45306912366 (forced, production), 45312223712 (forced, state-test, this phase) | spike runs per `docs/spike.md`; 45306912366; 45312223712 | Not run in CI (optional) | Yahoo 14/14, FRED 21/21, Treasury 1/1 in every forced run. multpl 0/5 in all forced runs since 17:31 PT. Cause: multpl.com outage, see Amendment B item 4 |
| 6.2-2 production path | Default dispatch (force=false, deploy=true, state) after the gate test. Healthcheck OK → `state` saved → deploy job success. Polling `page_url` for `health.json` is blocked from the sandbox; I used the deployment record instead | 45312036764 (also 45306912366, forced) | PASS (live page view: OWNER/UAT) | `normalised: force=false deploy=true state_ref=state inject=none`; `generated_at=2026-11-23T18:24:27-08:00 … run_id=45312036764`; `RESULT: OK`; `state.sh: saved data_v2.json to 'state'` (state → 605fd9a); github-pages deployment 8016740149 created 02:24:35Z, status `success` 02:24:50Z, url `https://wihogan.github.io/PEC/`. Timestamp in PT matches run time to the minute |
| 6.2-3 second run skips (18:17–18:24 PT, outside 06:30–13:00) | Two normal state-test runs after the forced state-test run 45305532774. I compared clones of `state-test` before and after each run | 45311190929, 45312039842 | PASS | Both: `0/0 fetched successfully \| 41 skipped (frequency)`; metadata `skip_count: 41`; ids equal (42) and 0 series with differing dates/values, before vs after each run. Production 45312036764 also skipped 41/41 |
| 6.2-4 `force=true` → skip_count 0 | Forced state-test run | 45312223712 | PASS | metadata `success_count 36, failure_count 5 (multpl), skip_count 0`; `RESULT: OK` |
| 6.2-4b first scheduled run deploys | Backup crons were enabled in dd652a4 (2026-11-24 01:41 UTC). The first slot is 2026-11-24 22:17 UTC (14:17 PT) | — | PENDING observation | No `schedule` refresh runs exist yet (only the 9 spike schedule runs). **Will fail at the unit-test step until D1 is fixed** |
| 6.2-5 parity vs `baseline_data_v2.json` | Script (kept in /tmp, not committed) compared production `state` (605fd9a, after 45312036764) with the fixture using the plan's tolerances | — | PASS with note | Same 42 ids; all lengths within 2 % (ICE `hy_yield`/`aaa_yield` −0.25 %, exempt anyway). Exact match (1e-6) on all overlapping dates for `fed_funds` (6167 pts), `treasury_10yr` (4223), `eur_usd` (4219), Treasury `10yr` tenor (4224). Yahoo within 0.5 % on all 13 series and all points, except **1 point**: `gold_spot` 2026-11-20 baseline 3539.0 vs 3515.40 (−0.67 %). This is the last date in the baseline, which was captured at 14:18 PT Friday right after the GC=F futures close. Yahoo later replaced the last-trade value with the settlement price, so this is a capture-timing artefact, not a pipeline defect. multpl exact on all months ≤ 2026-09 (trivially, because multpl data in `state` is carried forward from the baseline; no runner fetch has succeeded since the outage) |
| 6.2-6a `bad_fred_key, force=true` (state-test) | Injection run | 45313878206 | PASS (workflow); Telegram OWNER | `15/41 series succeeded`; `FAILURES (2)`: `rule 4: 15 market_daily series failed (limit 3)…`, `rule 4: all 21 attempted FRED series failed`; Save state skipped (`state-test` unchanged at 50f843b); notify job: `notify_telegram: sent (HTTP 200)` → expected **`[TEST] ⚠️ PEC refresh FAILED`** with the rule-4 lines |
| 6.2-6b `bogus_series, force=true` (state-test, right after successful 45312223712) | Injection run | 45313076107 | PASS | `[dow_jones] fetching from yahoo (series_id=^BOGUS)` → `FAILED … '^BOGUS'` → `carrying forward prior data after failure`; dow_jones data identical before/after (4249 pts, last 2026-11-23); `FAILURES (0) RESULT: OK`; Build site success; health/log artifact uploaded; leak scan OK; state saved. Run showed 35/41: the injected failure plus the 5 independent multpl failures (outage) |
| 6.2-6c `stale_state, force=true` (state-test) | Injection run | 45314681065 | **FAIL / BLOCKED (D1)** | Job failed at **Unit tests** (43 failed, 370 passed) before restore or injection. Rule 5 was never exercised. `state-test` unchanged. Notify sent (HTTP 200) → expected **`[TEST] ⚠️ PEC refresh FAILED`** with "no health.json (failed before healthcheck)". Re-run after the D1 fix |
| 6.2-7 production gate (`bad_fred_key, force=true, deploy=true, state`) | Run once, after verifying the latest production run 45306912366 = success (state 792400b, deployment 8016174358) | 45311178206 | PASS (workflow); Telegram OWNER | Healthcheck `FAILURES (2)` (rule 4 ×2) → `RESULT: FAIL`; Secret leak scan, **Save state, Build site, Upload Pages artifact skipped**; deploy job **skipped**; after the run, `state` still 792400b and the latest github-pages deployment still 8016174358. Notify `sent (HTTP 200)` → expected non-test **`⚠️ PEC refresh FAILED`** (state_ref: state). Recovery run 45312036764 (defaults) succeeded and deployed (see 2) |
| 6.2-8 scheduled/Mac failure notification | Not run: needs a commit on `main` (flag file) or a Mac-triggered run, plus the owner's phone | — | PENDING / OWNER | **D2:** the `.github/fail-next-scheduled` flag file is not implemented in `refresh.yml` (no reference anywhere outside `docs/plan.md`). Use the Amendment A alternative instead: a Mac-triggered `inject=bad_fred_key` run. Also owner to do: `mac/pec-trigger.sh` with a bad `GH` path, a watchdog alert, `alert-test.yml` (run 45299646927 succeeded 00:44Z; receipt = owner), `mac/install.sh --test` |
| 6.2-9 schedules fire | Checked the run history | — | PENDING observation | 0 `schedule` refresh runs so far (crons enabled 01:41Z today; first slots 22:17Z / 23:47Z Tue). No Mac launchd runs yet (the only owner-triggered run, 45313813176, was a manual EPS update). Confirm `schedule` events at both slots and Mac `workflow_dispatch` at ~13:20 / ~15:50 PT plus `~/Library/Logs/pec/trigger.log` (owner) |
| 6.2-10 EPS on state-test | Not dispatched: any run from `main` now fails at Unit tests (D1), so the EPS step cannot be reached | — | **BLOCKED (D1)** | Re-run after the fix with `eps_value=360` or another value different from the current 419.64 |
| 6.2-11 repo size | `git count-objects -vH` on fresh clones | — | PASS with note | No measurement was taken before Phase 5. Main-only clone at dd4ce5e (10 commits): **831.53 KiB**. At 9eb6f40 (12 commits, after this phase's 10 refresh runs, including 8 of mine): **840.10 KiB**. The +8.6 KiB came only from the Amendment B doc commit and the owner's EPS config commit. Test runs added 0 commits to `main` (state goes to orphan branches; keep-alive did not fire). Full clone with all branches: 845.38 KiB |
| 6.2-12 secret hygiene | Grepped the job logs of 9 runs (13 job logs: 45306912366, 45311178206, 45311190929, 45312036764 incl. deploy, 45312039842, 45312223712, 45313076107, 45313878206, 45314681065 incl. notify jobs) for `api_key=` and `api_key=[A-Za-z0-9]{16,}`. The literal key cannot be checked here | listed | PASS | 0 matches for either pattern in all logs (FRED errors in the bad-key runs say "variable api_key is not a 32 character…", with no `=` value). `leak_scan: OK (3 file(s) scanned)` in every run that reached the step (45306912366, 45311190929, 45312036764, 45312039842, 45312223712, 45313076107). In failed runs the scan is skipped, and nothing is saved or deployed |

## Defects

**D1 (critical, production-impacting): an EPS update breaks the unit-test gate of every later refresh run.**
The production EPS dispatch 45313813176 committed 9eb6f40 (`manual_override` 354.84 → 419.64). `tests/unit/test_healthcheck.py`
runs `healthcheck` against `REAL_CONFIG` (`config/series_v2.yaml`) together with `tests/fixtures/baseline_data_v2.json`, which
holds 354.84. Rule 6 now fails inside 43 tests (`rule 6: forward_eps_ntm value 354.84 does not match config override 419.64`).
`refresh.yml` runs `pytest` before restore, so **every refresh from `main` fails at "Unit tests"**. That includes Mac-triggered and
scheduled production runs (each sends a Telegram alert) until the fix. CI did not catch it: the bot's `GITHUB_TOKEN` push does not
trigger `ci.yml`. Evidence: run 45314681065, job 138178553512. Fix direction (not applied here, per the no-code-change rule): healthcheck
tests should use a fixture config, or a config patched to the fixture's EPS, not the live config value.

**D2 (minor, test-plan gap):** the 6.2-8 flag file `.github/fail-next-scheduled` has no implementation in `refresh.yml`. Use the
Amendment A alternative (Mac-triggered `inject=bad_fred_key`), or implement the flag file.

**Observation O1:** multpl failed in all 5 forced runs from 17:31 PT on (ECONNREFUSED). Root cause per Amendment B item 4:
multpl.com is down. Healthcheck passes because these series are warn-only, which is expected.
**O2:** the refresh console banner still says "edit config/series.yaml" for EPS updates (stale text; the file is `series_v2.yaml` / `set_eps` dispatch). Cosmetic.

## Telegram messages the owner should have received (please confirm)

| Run | Expected message |
|---|---|
| 45311178206 (6.2-7, production gate, ~18:22 PT) | `⚠️ PEC refresh FAILED`, state_ref: state, two `rule 4` failure lines |
| 45313878206 (6.2-6a, ~18:44 PT) | `[TEST] ⚠️ PEC refresh FAILED`, state_ref: state-test, two `rule 4` failure lines |
| 45314681065 (6.2-6c attempt, ~18:46 PT) | `[TEST] ⚠️ PEC refresh FAILED`, "no health.json (failed before healthcheck)" (caused by D1) |

## To re-run after the D1 fix and Amendment B code land

6.2-3, 6.2-4 (new skip rules), 6.2-6c, 6.2-10, then 6.2-4b / 6.2-8 / 6.2-9 by observation.

## Amendment B re-run (QA 2026-11-24)

The items listed above for re-run (6.2-3, 6.2-4, 6.2-6c, 6.2-10) were covered by the independent QA on `amendment-b` @ e4116f5 against `state-test` with `deploy=false`; the evidence is in `docs/qa.md` section 8:

| Item | QA run | Result |
|---|---|---|
| 6.2-3 / 6.2-4 (forced run, then skip rules) | 45317883934 (force), 45318738088 (default) | success; 36/41 fetched then 35 skipped, multpl outage tolerated |
| 6.2-6c (failure injection, gate) | 45319005518 (`stale_state`) | failure as expected at rule 5; state unchanged; Telegram HTTP 200 |
| 6.2-10 (EPS override / FactSet precedence) | 45319284815, 45319479573, 45319693938 | override wins when newer; FactSet wins when newer; restore OK |

The Phase 6 fixes made after that QA (docs/plan.md, "Phase 6 QA fixes") have unit tests but no live run yet. 6.2-4b, 8 and 9 remain pending observation; D2 is dropped (see plan Amendment A).
Owner confirmed receipt of all three Telegram failure alerts from Phase 5 runs 45311178206, 45313878206, 45314681065 (2026-11-24) — items 6.2-6/7 notification: PASS (owner).
