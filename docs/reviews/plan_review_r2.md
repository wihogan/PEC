# PEC Migration Plan — Independent Review R2

Reviewer: Fable (same reviewer as R1) · 2026-11-20 · Plan reviewed: `PEC_Migration_Plan.md` draft 2
Method: re-read draft 2 against source and baseline; re-parsed `data_v2.json`, the EPS seed and history cache read-only. No project code executed.

Verdict: **3 blocking findings remain (NB1–NB3), all small and mechanical.** Draft 2 resolves 7 of the 9 R1 blockers outright and 2 partially. No architectural objection is left.

---

## Status of R1 blocking findings

| R1 | Status | Note |
|---|---|---|
| B1 staleness thresholds | **Partially** | Slow series are now warn-only (110 / 300 / 220): correct. But draft 2 dropped draft 1's separate WTI allowance and puts `wti_crude` under `market_daily` 7 d. See NB2. |
| B2 rule 4 | Resolved | Counts over attempted series; Treasury and multpl governed by staleness only. |
| B3 schedule | Resolved | `17 22` / `47 23` UTC; run 2 correctly described as retry insurance. |
| B4 notification | Resolved | Issue-based alert plus scheduled-failure test 6.2-8. Wiring detail in N-a. |
| B5 EPS precedence | Resolved | `PEC_EPS_OVERRIDE_ONLY`, rule 6, `forward_eps_ntm.json` not persisted. Verified the history path is offline (`spglobal.py:545-554`). `refresh_v2.py` has no `import os` (imports at `:66-78`); add it. |
| B6 public commit 1 | Resolved | Scrubbed, new `.gitignore` in commit 1, data and caches excluded, stub `CLAUDE.md`. Gated on Q4. |
| B7 session capabilities | Resolved | Phase 1 probe; state seeded by workflow. |
| B8 test isolation | **Partially** | `state_ref` / `deploy` / `inject` design is right. Two injections do not work as written and one handoff test is not met. See NB3. |
| B9 spike | Resolved in design | Real cold `--force` run on an hourly cron. But it cannot run when scheduled: see NB1(c). |

---

## New blocking findings

### NB1. Scheduled runs would never deploy, and may run injections: `inputs.*` is empty on `schedule`
(a) Deploy job condition is `inputs.deploy != false`. On a `schedule` event `inputs` has no values; a missing property evaluates to an empty string, and GitHub expressions coerce both `''` and `false` to 0 in a loose comparison. So `'' != false` is false and **the deploy job is skipped on every scheduled run**, as is the "upload-pages-artifact (only if deploy)" step. The unattended refresh, the core acceptance criterion, would silently not publish. The manual-dispatch Phase 4 exit check would pass and hide it.
(b) The same trap in reverse: an inject step guarded by `inputs.inject != 'none'` is true on schedule (`'' != 'none'`), so injection would run in production. Likewise `state_ref != 'state'` is true on schedule.
(c) Sequencing: the Phase 3 spike runs `refresh_v2.py --force` and its go criterion includes "FRED ok", but the phase table has Hogan adding the `FRED_API_KEY` secret in Phase 4. Without it all 21 FRED series fail every spike run.
Fix: normalise once at the top of `refresh.yml` and use only the normalised values:
`DEPLOY: ${{ github.event_name == 'schedule' || inputs.deploy == true }}`, `INJECT: ${{ inputs.inject || 'none' }}`, `STATE_REF: ${{ inputs.state_ref || 'state' }}`, `FORCE: ${{ inputs.force == true }}`; deploy job `if: needs.refresh.outputs.deploy == 'true' && github.ref == 'refs/heads/main'`. Add integration check 6.2-9: a `schedule`-triggered run shows the deploy job **ran** and the live `health.json` carries its run id (not merely "schedule fired"). Move "Hogan adds secret" to Phase 1 exit / before Phase 3.

### NB2. `wti_crude` at 7 days will hard-fail deploys periodically
Evidence: baseline on Fri 2026-11-20 has `wti_crude` last obs 2026-11-17 (age 3) while every other FRED daily rate is at 11-19 (age 1). DCOILWTICO is EIA data that FRED updates in weekly batches, not daily, so its age just before the next batch is routinely 7–9 days and longer in holiday weeks (Thanksgiving falls inside the likely parallel run). One series over threshold blocks the whole deploy (rule 5). Draft 1 had WTI at 8; draft 2 lowered it.
Fix: `wti_crude` 14 d, same as FRED FX. In the spike, log the age of the last observation per daily series each run and set final thresholds from that evidence before Phase 4.

### NB3. Injection mechanics: two cannot work as written; the handoff's carry-forward test is not met
- `bogus_series` "appends a fake series to a temp copy of the config". `CONFIG_PATH` is hard-coded (`refresh_v2.py:86`), so a temp copy is never read; and an appended id has no prior data, so rule 2 fails. Handoff §7.2 requires "one bogus series id carries forward and still deploys". Fix: in the working copy, change the `series_id` of one existing series (e.g. `dow_jones` → `^BOGUS`), `force=true`; expect 1 failure, carry-forward, healthcheck pass, artifact built.
- `stale_state` relies on "fetch skipped for it", which only happens if that series was already fetched post-close the same business day and the run is outside market hours; otherwise it is re-fetched and the gate passes. Fix: shift the dates back 30 days **and** point that series at a bogus ticker, with `force=true`. That is also the real scenario the gate exists for (blocked source hidden by carry-forward).
- Order/safety: the EPS commit stages `config/series_v2.yaml`; the plan's order (EPS step before inject) keeps an injected edit out of `main`. State that the inject step must never precede a commit, and refuse `inject != none` together with `eps_value` on `state_ref == state`.

---

## Non-blocking findings

- **N-a. `notify` wiring.** `failure()` only sees jobs listed in `needs`. Specify `needs: [refresh, deploy]` and `if: always() && (needs.refresh.result != 'success' || needs.deploy.result == 'failure')`; skip the "deploy skipped" case for `deploy=false` runs. A refresh job cancelled while pending in the concurrency group may not satisfy `cancelled()`.
- **N-b. EPS history JSON and fixtures (coordinator's question).** The plan does **not** say it clearly: §3 says seeding copies "the history JSON from fixtures", but §2 `tests/fixtures/` and the Phase 1 commit list omit it and exclude `.cache/`. Simpler than adding it: drop it from state. The cache and `config/forward_eps_seed.yaml` are identical (64 entries, compared), and with `PEC_EPS_OVERRIDE_ONLY=1` `_ensure_history` is never reached (`spglobal.py:643`), so the file can never change; `fetch(history=True)` falls back to the seed (`:548-551`). State becomes `data_v2.json` only, and an EPS backfill is just a commit to the seed. If the author keeps it, list `tests/fixtures/forward_eps_ntm_history.json` in §2 and Phase 1.
- **N-c. `state.sh restore` is described two ways:** §2 "absent → cold start", §3 "absent → seed from fixtures". Pick seed-from-fixtures for both `state` and `state-test`; it is the safer recovery if `state` is ever lost.
- **N-d. Secret scope.** `FRED_API_KEY` at workflow `env` level reaches the unit-test step and the deploy and notify jobs. Set it on the refresh and leak-scan steps only. `leak_scan.sh` must exit cleanly when the variable is empty (grep for an empty string matches everything) and skip when the value is the injected `invalid`.
- **N-e. Unit tests inherit `PEC_EPS_OVERRIDE_ONLY=1` and `TZ`** from workflow env; tests of the live → override fallback ladder must `delenv` it.
- **N-f. 6.2-3 expectation.** "`skip_count` = series fetched in (2)" should be `skip_count == 41`, `success_count == 0`, same business day, after the close.
- **N-g. Production EPS commit path is not integration-tested** (6.2-10 runs on `state-test` without committing). Handle the no-diff case (`git commit` exits non-zero when the value is unchanged) and add a unit test of the commit script against a temp repo, or accept and note it for QA.
- **N-h. Keep-alive and issue auto-close steps** should run only when `STATE_REF == 'state'`.
- **N-i. Rule 3** can simply require that neither placeholder string appears anywhere; after `render()` the comment copy is replaced too (`PECtemplate.html:1048,1051`).

---

## Confirmed in draft 2

- `PEC_EPS_OVERRIDE_ONLY` design is correct against `_fetch_valuation_inputs` (`refresh_v2.py:303-389`): skipping the live attempt leaves `source != "live"`, the override branch runs, and the history block still executes because the fetcher module is present.
- Healthcheck vs baseline at 2026-11-20: rule 5 fails only on `tips_5yr` (17 d), as the plan states; FX 7 d < 14; `yield_curve` 0 d; all Yahoo 0 d; slow series only warn (80 / 143 / 233 d). Rule 6 holds (354.84, `override`).
- Per-job permissions, `ci.yml` split, job-level concurrency keyed by state ref, deploy restricted to `main`, `GITHUB_TOKEN` commits not retriggering: sound.
- Cron arithmetic and Mon–Fri alignment: correct.
- F11–F13 are accurate.

---

## R3 — check of draft 3 (NB1–NB3 only)

Verdict: **No blocking findings.** Plan is clear to proceed to Phase 1 once Hogan answers Q0 and Q4.

| R2 | Status | Note |
|---|---|---|
| NB1 scheduled deploy / input defaults / spike secret | Resolved | Normalisation step keyed on `github.event_name`; deploy gated on `needs.refresh.outputs.deploy`; check 4b proves a scheduled run deployed; secret moved to Phase 1. Job-level `concurrency` still reads `inputs.state_ref || 'state'`, which is correct there (step outputs are not available at that level). |
| NB2 `wti_crude` threshold | Resolved | 14 d, final values set from spike evidence. |
| NB3 injection mechanics | Resolved | `bogus_series` and `stale_state` now edit an existing series in the working copy with `force`; ordering after the EPS commit stated. |

No new blocker introduced. Clean-up items (non-blocking, fix while editing):

- **Leftover text from the dropped history file.** §3 bullet 1 still says "history JSON is persisted and outranks the seed"; bullet 2 still describes deleting the history file from `state` (a backfill is now just a seed commit on `main`); §2 `state.sh save` says "those two files".
- **`state.sh restore` still described two ways** (§2 "absent → cold start", §3 "absent → copy fixture baseline"). Use the §3 behaviour.
- **Phase 4 row still lists "Hogan adds secret"**; it is now Phase 1.
- **Test 6(b) ordering.** It relies on carried-forward `dow_jones` being under 7 days old. If `state-test` was just seeded from the 2026-11-20 baseline and the test runs more than a week later, rule 5 fails spuriously. Run 6(b) right after a successful normal run on `state-test`.
- **Inject guard on production state.** Make it concrete: on `state_ref == state` allow only `bad_fred_key`. `bogus_series` there would pass the gate and save and deploy carried-forward data.
- R2 items N-a (notify `needs`/`if`), N-d (secret scope, empty-key guard), N-e, N-f, N-g, N-h remain open as non-blocking.
