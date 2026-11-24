# PEC Cloud Migration — Detailed Plan (Phase 0)

Version: **draft 3** · 2026-11-20 · Author: orchestrator (Opus 5.5) · Reviewed R1, R2 by Fable (`review_r1.md`, `review_r2.md`); NB# = R2 finding
Inputs: `PEC_Cloud_Migration_Handoff.md` (the handoff), `PEC_handoff.md`, source in `dashboard/`.
All times Pacific unless marked UTC. R1 finding ids (B#, N#, Cut#) are cited where they drove a change.

---

## 0. Findings that change the handoff's design

| # | Finding | Evidence | Plan change |
|---|---|---|---|
| F1 | `beautifulsoup4` missing from `requirements.txt`; `Jinja2` unused; `pdfplumber` absent so the FactSet PDF path is dead. | `multpl.py:30`; requirements | Add pinned `beautifulsoup4`; drop `Jinja2`. Test deps in `requirements-dev.txt`. Python pinned to 3.12 in workflow. |
| F2 | ERP slider removed; ERP fixed 3.5 % in Python. | Design Ref l.301; only range input is `yc-scrubber` | UAT: "DDM panel shows fair value, spot, premium". |
| F3 | DDM rounded to int. | `refresh_v2.py:620` | Test expects 4968. |
| F4 | TTL caches (`multpl_*.html`, `yahoo_info_*`, `factset_eps_latest.json`, S&P xlsx) judge freshness by mtime; a git-restored file always looks fresh. | `multpl.py:70-75` etc. | Never persist TTL caches. |
| F5 | `.gitignore` uses V1 names; would track `data_v2.json`. | file | New `.gitignore` in commit 1 (B6). |
| F6 | Normal age of last observation is large for slow series because `_needs_refresh` fetches monthly series once per calendar month and quarterly once per quarter. Baseline ages: CPI/NBER 80 d, `sp500_earnings` 143 d, quarterly 233 d (rises to ~275 d), FX 7 d, WTI 3 d, `tips_5yr` 17 d (errored). ICE credit history starts 2023-11. | baseline; `refresh_v2.py:479-485` | Hard staleness gates only for daily series; slow series warn-only (B1). |
| F7 | `_needs_refresh` is consistent under a stable TZ; baseline timestamps are PT. | `:457-472` | `TZ=America/Los_Angeles` at workflow `env` level. No code change unless unit tests disagree. |
| F8 | Override path never appends EPS history; history ends 2026-04-30. | `spglobal.py:643` | Out of scope; flagged in UAT. |
| F9 | `--series` calls `input()`. | `:710` | Never passed in CI. |
| F10 | This session's GitHub identity is `PV996`; it cannot see `wihogan/*`. Pages and secrets API paths blocked by the session proxy. | `list_repos`; `gh api` 403 | Q0. Capability probe in Phase 1 (B7). |
| F11 | **Live EPS outranks `manual_override`.** Override is used only if `spglobal.fetch` raises. A live or cached hit from a runner would silently replace the approved value. | `refresh_v2.py:334` | `PEC_EPS_OVERRIDE_ONLY=1` (B5). |
| F12 | `PEC.html` embeds the JSON twice (placeholder also in an HTML comment). | `PECtemplate.html:1048,1051` | Healthcheck parses `<script id="pec-data">` only; size floor 1.5 MB. Not fixed (UI change, out of scope). |
| F13 | Docs and notes contain Hogan's work email, employer and shared-drive paths. | `CLAUDE.md`, `README.md`, `update-dashboard.md`, V2 execution plan, `pec-update.skill` | Scrub before first public commit (Q4, B6). |

---

## 1. Target architecture

- **Repo:** new public repo `pec` under the owner chosen in Q0. Source flattened to repo root.
- **Branches:** `main` (code, tests, workflows, docs, config). Orphan `state` (production runtime state, one force-pushed commit per run). Orphan `state-test` (integration tests only). Orphan `spike-results` (Phase 3 evidence).
- **Hosting:** GitHub Pages, source = GitHub Actions, artifact deploy. `PEC.html` → `_site/index.html`, plus `_site/health.json`. HTML never committed (N5).
- **Workflows:**
  - `ci.yml` — `push`/`pull_request` to `main`: unit tests only. `permissions: contents: read`. No concurrency group (N1).
  - `refresh.yml` — triggers `schedule` + `workflow_dispatch`. Dispatch inputs: `force` (bool, false), `deploy` (bool, true), `state_ref` (string, `state`), `inject` (choice `none|bad_fred_key|bogus_series|stale_state`, `none`), `eps_value` (string, ""), `eps_date` (string, ""). Inputs reach scripts only through `env:` (N7).
    - Job `refresh` (`timeout-minutes: 20`, `concurrency: {group: pec-state-${{ inputs.state_ref || 'state' }}, cancel-in-progress: false}`, permissions `contents: write, issues: write`): checkout → setup-python 3.12 + pip cache → install → `pytest` (unit) → EPS step (if `eps_value`) → `state.sh restore` → inject step (if any) → `python refresh_v2.py [--force]` → `healthcheck.py` → secret-leak scan → `state.sh save` → keep-alive step → build `_site` → `upload-pages-artifact` (only if `deploy`).
    - Job `deploy` (needs refresh; `if: needs.refresh.outputs.deploy == 'true'` and ref is `main`; permissions `pages: write, id-token: write`; `environment: github-pages`) → `deploy-pages`.
    - Final job `notify` (`if: failure() || cancelled()`, `issues: write`): opens or comments on a pinned issue "PEC refresh failed" with run link, @-mentioning the owner account Hogan reads (B4). Closed automatically by the next successful scheduled run.
  - `spike.yml` — Phase 3 only, deleted at Phase 4 exit.
- **Input normalisation (NB1):** first step of `refresh` computes outputs from `github.event_name`: on `schedule` → `force=false, deploy=true, state_ref=state, inject=none, eps_value=''`; on dispatch → the inputs. Every later step and the `deploy` job read only these normalised outputs, never `inputs.*` directly. Guards (fail fast): on `state_ref == state` the only allowed inject is `bad_fred_key` (the production gate test 6.2-7), never together with `eps_value`; `inject` is ignored on `schedule`.
- **Env (workflow level):** `TZ=America/Los_Angeles`, `PEC_EPS_OVERRIDE_ONLY=1`, `FRED_API_KEY: ${{ secrets.FRED_API_KEY }}` (overridden to `invalid` only when `inject == bad_fred_key`).

## 2. Code and file changes

`refresh_v2.py` gets exactly one change; everything else is new files.

| File | Change |
|---|---|
| `refresh_v2.py` | Add `import os`. In `_fetch_valuation_inputs`: if `os.environ.get("PEC_EPS_OVERRIDE_ONLY") == "1"`, skip the live attempt and go straight to `manual_override` (EPS history fetch is unaffected; it reads local files). Nothing else unless unit tests expose a TZ defect. Backups left as-is (harmless; Cut 3). |
| `requirements.txt` / `requirements-dev.txt` | F1. Dev: `pytest`, `pytest-socket`. |
| `.gitignore` | `.venv/ .cache/ backups/ PEC.html PEC_dev.html data_v2.json _site/ health.json .env *.env __pycache__/ .pytest_cache/` |
| `healthcheck.py` (new) | §5. Thresholds are a dict in the file (Cut 2). Writes `health.json`. |
| `tools/set_eps.py` (new) | Validates float 200–600 and ISO date not in the future; edits only the two `manual_override` lines in `config/series_v2.yaml` (text edit, comments preserved). |
| `tools/state.sh` (new) | `restore <ref>`: `git ls-remote` to test existence; absent → seed from `tests/fixtures/baseline_data_v2.json` (log loudly); present but fetch fails → exit 1 (N14). Copies in `data_v2.json`. `save <ref>`: builds an orphan commit in a temp worktree from `data_v2.json`, force-pushes with retry. |
| `tools/leak_scan.sh` (new) | Fails if the literal `$FRED_API_KEY` value appears in `PEC.html`, `data_v2.json`, `health.json` (N7). |
| `tests/` | Existing `test_fetchers.py` → `@pytest.mark.integration`; Shiller test removed; multpl live test added (N10). `pytest.ini`: register marker, default `-m "not integration"`, `--disable-socket` for unit runs (N9). |
| `tests/fixtures/` | 5 multpl HTML pages; Treasury XML sample (captured in Phase 3); `baseline_data_v2.json` (zip baseline, 2.4 MB, one-time; needed for parity and seeding). |
| `CLAUDE.md` | Replaced in Phase 1 with a short accurate stub for the cloud process (B6); full rewrite in Phase 8. |
| `docs/` | `plan.md` (this), `spike.md`, `test-report.md`, `qa.md`, `uat-checklist.md`, `uat-signoff.md`, `runbook.md` (how to refresh, update EPS, read failures — replaces `update-dashboard.md`, N17). `update-dashboard.md` and `pec-update.skill` not committed (personal identifiers; Windows-only). |

## 3. State

Persisted on `state` (≈2.4 MB, single commit, force-pushed): **`data_v2.json` only.** The EPS history JSON is not persisted: it is identical to `config/forward_eps_seed.yaml` (64 entries) and cannot change while override-only is on, so `spglobal` rebuilds it from the seed on each cold runner (R2).
- Not persisted: TTL caches (F4); `forward_eps_ntm.json` (B5); `forward_eps_seed.yaml` (N15 — `main` holds the seed; spglobal's write-back on a runner is discarded, which is fine because the seed cannot change under override-only).
- EPS backfill (if Hogan later approves values): commit the updated seed on `main`; the next cold runner rebuilds history from it. Documented in `runbook.md`.
- Seeding: done **inside a workflow** (Phase 4 first dispatch: `restore` finds no `state`, copies `tests/fixtures/baseline_data_v2.json` → `data_v2.json`) — no session push to `state` needed (B7).
- `save` runs only after healthcheck and leak scan pass, so a failed run never overwrites good state.
- EPS dispatch: `set_eps.py` edits `config/series_v2.yaml`; the job commits **that file only** to `main` with `GITHUB_TOKEN` (does not retrigger workflows, N3), retrying with `git pull --rebase` once on rejection, then refreshes. Skipped when `state_ref != 'state'` (test runs do not write `main`).

## 4. Schedule (B3)

Crons (UTC, weekdays; both before 00:00 UTC so `1-5` = Mon–Fri Pacific):
- `17 22 * * 1-5` → 14:17 PST / 15:17 PDT (≥ 17:17 ET; after FRED's ~16:15 ET daily update and settled closes). Matches handoff Q2 default.
- `47 23 * * 1-5` → 15:47 PST / 16:47 PDT. **Retry insurance only**: re-fetches series that errored or everything if run 1 was dropped. It does not pick up late data for series already fetched post-close (`refresh_v2.py:466-477`).

Keep-alive (N2, Cut 1): step in scheduled runs — if last commit on `main` is > 30 days old, push an empty commit "keep-alive". Whether `state` pushes count as activity is unverified; this step makes it moot.

Known pre-existing latency (N16, tell Hogan, not changed): monthly series refresh on the first run of each month, quarterly on the first of each quarter, so a mid-month CPI print appears up to ~3 weeks late.

## 5. Healthcheck (B1, B2, F12)

Hard fail (exit 1) if any:
1. `data_v2.json` missing/invalid.
2. Series ids ≠ the 41 in `series_v2.yaml` (+ `fwd_pe_history` allowed).
3. `PEC.html` < 1.5 MB, or still contains `__DASHBOARD_DATA__`/`__GENERATED_AT__` outside the comment, or the `<script id="pec-data">` body does not parse to JSON equal to `data_v2.json`.
4. Source-level failure, over **attempted** series this run: > 3 failed `market_daily` series; or all attempted FRED series failed (≥ 3 attempted); or all attempted Yahoo series failed (≥ 3 attempted). Treasury and multpl failures are governed by staleness only.
5. Staleness (age of last non-null obs, calendar days) for daily series: `market_daily` 7; FRED FX and `wti_crude` 14 (weekly FRED batches, NB2); `yield_curve` 7. Final values confirmed from spike evidence (Phase 3) before Phase 4. Any one over threshold fails.
6. `valuation_inputs.forward_eps_ntm.source == "override"` and value equals `series_v2.yaml` override (B5).

Warn only (printed, in `health.json`, never fail): monthly > 110 d; quarterly > 300 d; `sp500_earnings` > 220 d; `fwd_pe_history` / EPS age; any single errored series.

On the zip baseline at 2026-11-20: rule 5 flags `tips_5yr` (17 d) → **fail**. That is correct behaviour (a 17-day-old series should alert). Unit tests use the baseline with `tips_5yr` patched fresh for the pass case, and the raw baseline as a fail case. First production run will re-fetch `tips_5yr` (it errored, so `last_fetched` is old).

## 6. Tests

### 6.1 Unit (offline, every push; written by Opus)
Isolation (N9): autouse fixture monkeypatches `refresh_v2.HERE/CONFIG_PATH/OUTPUT_PATH/BACKUP_DIR`, `spglobal._CACHE_DIR/_HISTORY_JSON/_SEED_YAML/_CACHE_JSON/_CACHE_XLSX` to `tmp_path`; sockets disabled. First Phase 2 task: confirm `import refresh_v2` works under pytest capture (`sys.stdout.reconfigure`, `logging.basicConfig`).
- Config: loads; 41 unique ids; sources ⊂ registry; required fields.
- `_safe_float`, `_dates_to_strings`: NaN, None, inf, 4 dp.
- `_needs_refresh`, `_last_completed_business_day`, `_market_is_open`: frozen clock via `datetime` subclass monkeypatch (Cut 10); each frequency; open/closed; pre/post close; weekend; month and quarter boundaries; under `TZ=America/Los_Angeles` and `TZ=UTC` (`time.tzset`).
- `_compute_derived_valuation`: fwd P/E; DDM 4968 (EPS 354.84, 10y 4.00); denom ≤ 0 → None; both delta strings; month join; < 24 months → no fwdpe delta.
- `_fetch_valuation_inputs` with `PEC_EPS_OVERRIDE_ONLY=1`: spglobal live path never called; source `override`.
- `run()` with stub fetchers: carry-forward; skip when current; statuses/counts.
- `render()`: placeholders replaced; script JSON round-trips.
- Parsers on fixtures: multpl ×5 (percent scaling, `†`); Treasury pivot/tenor order; Yahoo `_extract_close` flat + MultiIndex; FRED `pct_change_12m` on a synthetic series.
- `spglobal` history in tmp: seed bootstrap; idempotent month append.
- `healthcheck.py`: pass (patched baseline); fail per rule 1–6; raw baseline fails on `tips_5yr`; one errored Treasury series passes if fresh; multpl all-failed passes (warn).
- `set_eps.py`: edit preserves comments; rejects non-float, out of range, future date.

### 6.2 Integration (GitHub Actions, live; designed by Opus) — isolated via `state_ref=state-test`, `deploy=false` unless stated (B8)
1. `pytest -m integration` (FRED, Yahoo, Treasury, multpl live).
2. Production path: dispatch on `main` with defaults → healthcheck passes → deploy → poll `page_url` until `health.json` shows this run id (timeout 10 min) → timestamp equals `TZ=America/Los_Angeles date` to the minute (N12).
3. Second run, outside 06:30–13:00 PT (market hours force re-fetch): `skip_count` = series fetched in (2); series data identical.
4. `force=true` → `skip_count == 0`.
4b. First scheduled run after Phase 4: confirm the `deploy` job ran and `health.json` shows the scheduled run id (NB1).
5. Parity vs `baseline_data_v2.json`: same ids; length within 2 % (ICE exempt); exact match (1e-6) on overlapping dates for daily FRED series (`fed_funds`, `treasury_10yr`, `eur_usd`) and Treasury `10yr`; Yahoo 0.5 % tolerance (auto-adjust revisions); multpl exact on months ≤ 2026-09. Revised FRED series (GDP, CPI, delinquency) excluded from exact match.
6. Failure injection on `state-test`: (a) `inject=bad_fred_key, force=true` → rule 4 fails, notify job opens issue; (b) run immediately after a successful normal run on `state-test`, `inject=bogus_series, force=true`: working-copy edit of `dow_jones` `series_id` → `^BOGUS` → 1 failure, carry-forward, healthcheck passes, artifact built (handoff §7.2); (c) `inject=stale_state, force=true`: after restore, shift `dow_jones` dates back 30 days **and** point it at `^BOGUS` (a blocked source hidden by carry-forward) → rule 5 fails. Inject edits happen after the EPS commit step and are never committed.
7. **Production gate test** (the one test touching live): `inject=bad_fred_key, force=true, state_ref=state, deploy=true` on `main` → job fails before `save` and before deploy; live `health.json` run id unchanged; `state` commit unchanged.
8. **Scheduled-failure notification (B4):** commit `.github/fail-next-scheduled` flag on `main`; the next scheduled run fails deliberately at start; confirm the issue and that Hogan's inbox received it; remove flag.
9. Schedule fires (run history shows `schedule` events at both slots).
10. EPS: `eps_value=360, eps_date=<today>, state_ref=state-test, deploy=false` → `set_eps.py` runs against the working copy only (no commit to `main` when not production), derived fwd P/E and DDM in the run's `data_v2.json` change by the expected ratio.
11. Repo size (N8): fresh-clone `git count-objects -vH` before Phase 5 and after ≥ 10 runs; `main` growth < 100 KB excluding code commits.
12. Secret hygiene: leak scan passes each run; grep downloaded logs of 3 runs for the key.
Evidence in `docs/test-report.md` (run URLs + outputs).

### 6.3 UAT (Hogan, ~10 min) — `docs/uat-checklist.md`
Page opens desktop + phone; timestamp today PT; 5 sections; KPI tiles; range tabs, series toggles, yield-curve scrubber; recession bands; DDM panel shows FV/spot/premium; spot-check S&P close, 10y, Fed funds; dispatch from phone and see timestamp change; failure notification received (from 6.2-8); 5 consecutive unattended weekday runs; stale EPS acknowledged. Sign-off → `docs/uat-signoff.md`.

## 7. Phases, owners, exit checks

| Phase | Work | Owner model | Exit check |
|---|---|---|---|
| 0 Plan | Draft → review → revise → re-review | Opus; **Fable** reviewer | Reviewer: no blocking findings. Hogan answers Q0, Q4. |
| 1 Repo + capability probe | Hogan creates empty public repo `pec` and attaches it. Commit 1: scrubbed source (no `data_v2.json`, `.cache/`, `pec-update.skill`, `update-dashboard.md`), new `.gitignore`, `requirements.txt` fix, stub `CLAUDE.md`, `tests/fixtures/baseline_data_v2.json`. Probe: push to `main`; push a trivial `.github/workflows/probe.yml` (dispatch + cron); create a branch; dispatch; read run status/logs; delete probe. | Sonnet (commit), Opus (probe) | Fresh clone installs and `_load_fetchers()` imports. Probe results recorded in `docs/plan.md`; any failed capability converted into a named Hogan click before proceeding. |
| 2 Unit tests + code | §6.1 written against unchanged code; then §2 changes | Opus (tests), Sonnet (code) | `pytest` green offline in CI |
| 3 Connectivity spike (parallel with 2; needs `FRED_API_KEY` secret, so Hogan adds it at Phase 1) | `spike.yml`: hourly cron for one trading day + one weekend slot; each run does a cold `python refresh_v2.py --force` with `PEC_EPS_OVERRIDE_ONLY` **unset**, records per-source attempted/ok/fail counts, wall time, and what each EPS endpoint returned; appends one line to `spike-results` (B9). Captures Treasury XML fixture. | Sonnet (YAML), **Opus** (verdict) | `docs/spike.md`. Go = Yahoo 14/14 in ≥ 90 % of runs and FRED/Treasury/multpl ok. Else: try retry-with-backoff in `yahoo.py` (approved within scope) and re-spike; if still failing → stop and report to Hogan with handoff §5 fallbacks. |
| 4 Workflow | `ci.yml`, `refresh.yml`, `tools/*`, `healthcheck.py`. Hogan sets Pages source = Actions. First dispatch seeds `state`. Delete `spike.yml`. | Sonnet | Manual dispatch → live page, `state` exists |
| 5 Integration | §6.2 | Opus | All pass; `docs/test-report.md` |
| 6 QA | Code, workflows, evidence, live page vs handoff §11 | **Fable**, fresh context | `docs/qa.md` sign-off |
| 7 Parallel run + UAT | 5 weekdays alongside laptop; checklist | Sonnet (docs) | Hogan sign-off |
| 8 Cutover | Redirect old URL (`wihogan/PV-macro-dashboard2/index.html` → meta-refresh + link; Hogan edits on github.com unless `wihogan` attached); final docs; Hogan stops laptop routine; old repo kept 30 days | Sonnet | Old URL lands on new page |

Subagents never push to `state`/`state-test` or change repo settings; only workflows touch state.

## 8. Risks (deltas to handoff §5)
- Mtime cache trap (F4) — not persisted.
- EPS silently replaced by a live scrape (F11) — override-only in CI; healthcheck rule 6.
- Notification goes to the wrong identity (B4) — issue-based alert + scheduled-run test.
- Yahoo adjusted-close drift breaks parity — tolerance.
- Concurrent runs on state — per-state-ref concurrency on the refresh job only.
- Public repo exposes personal identifiers (F13) — scrubbed before commit 1.
- Session capability gaps (B7) — probe in Phase 1; state seeded by workflow.
- US data-release delays (shutdown) — slow series warn-only, never block deploy.

## 9. Rollback
Unchanged from handoff §8.

## 10. Out of scope
Automating EPS scraping; F8; changing data sources without Hogan's approval (Yahoo retry within `yahoo.py` is not a source change); UI changes including the JSON double-embed fix and an ICE BofA attribution footer (raise separately).

## 11. Decisions needed from Hogan
- **Q0 — answered 2026-11-20: `wihogan`.** Repo `wihogan/pec`, URL `https://wihogan.github.io/pec/`. Blocker: this Claude session (GitHub identity `PV996`) has no push access to `wihogan` repos (`add_repo` refused on `wihogan/PV-macro-dashboard2`). Hogan must grant access before Phase 1 (see status note).
- **Q4 — default applied (not answered):** scrub work email, employer name and shared-drive paths before the first public commit.
- Q1–Q3 per handoff, defaults taken: github.io URL with old link redirected; 14:17 PST / 15:17 PDT plus a retry run; EPS left at 354.84 / 2026-05-07 and flagged.
- Hogan clicks expected: create repo (or approve), add `FRED_API_KEY` secret (at Phase 1, for the spike), set Pages source to GitHub Actions, confirm Actions email notifications are on, UAT, stop laptop routine; plus any capability the Phase 1 probe shows the session lacks.

---

## Phase 1 — capability probe results (2026-11-20 15:19 PT)

| Capability | Result | Evidence |
|---|---|---|
| Push to `main` | OK | commits 2f08509, 344a801 |
| Push workflow files (`.github/workflows/*`) | OK | `probe.yml` pushed; push-triggered run 44835813565 succeeded |
| Create a branch | OK | `probe-branch` created |
| Delete a branch from the session | **Blocked** (proxy) | done from inside workflows instead (spike cleanup step) |
| Dispatch a workflow | OK | run 44835866114 (`workflow_dispatch`) succeeded |
| Read run status and job logs | OK | via REST `actions/runs`, `actions/jobs/{id}/logs` |
| Dispatch actor | `wihogan` | dispatch-triggered notifications go to `wihogan` |
| Runner timezone with `TZ=America/Los_Angeles` | OK | `15:18:58 PST` |
| Read/write repo secrets | **Blocked** (proxy 403) | Hogan click |
| Read/set Pages settings | **Blocked** (proxy 403) | Hogan click |
| `FRED_API_KEY` secret | **Missing** | probe log; Hogan click (needed for Phase 3 spike) |
| Scheduled (`schedule`) trigger fires | Not yet verified | verified by the Phase 3 spike cron |

Repo is public, default branch `main`. Canonical remote is `https://github.com/wihogan/PEC.git` (GitHub reports the lowercase URL as moved).

---

## Amendment A (2026-11-21) — triggers and alerts

Approved by the owner; supersedes the GitHub-issue alert in sections 1, 6.2-6/8 and 8. All other sections stand.

- **Primary trigger:** launchd job on the owner's always-on Mac (Pacific time) runs `gh workflow run refresh.yml -R wihogan/PEC --ref main` Mon–Fri at 13:20 and 15:50 PT (`mac/`).
- **Secondary trigger:** the GitHub `schedule` crons in section 4 (`17 22` and `47 23`, UTC, Mon–Fri) stay as backup. Committed commented out with `# ENABLE AFTER PHASE 3 GO`. Overlapping runs are harmless: the second run skips series already current.
- **Alerts go to Telegram, not GitHub issues.** The `notify` job in `refresh.yml` (failure or cancelled) sends the message via `tools/notify_telegram.sh`; the `issues: write` permission and the pinned issue are dropped. A Mac watchdog (`mac/pec-watchdog.sh`, 16:30 PT weekdays) alerts if no successful `refresh.yml` run on `main` exists today, and the Mac trigger alerts if `gh workflow run` itself fails. Production runs are titled `refresh [state]` via `run-name`; the watchdog counts only those, so `state-test` runs are ignored.
- **New repo secrets:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (same values stored in the Mac Keychain as `pec-telegram-token`, `pec-telegram-chat`).
- **Workflow additions:** `alert-test.yml` (manual one-message test); job `refresh` permissions are `contents: write` only; `state_ref` is a choice (`state`, `state-test`); extra guard: `deploy=true` is refused together with any inject on `state-test`; `eps_value` and `eps_date` must be given together and are format-validated before use.

Adjusted test items:
- **6.2-7 / 6.2-8 (replaces issue checks):** 6.2-6(a) and 6.2-7 pass when a `[TEST] ⚠️ PEC refresh FAILED` (6.2-6a) or `⚠️ PEC refresh FAILED` (6.2-7) Telegram message is received with the run URL and healthcheck failure lines, instead of an issue being opened.
- **6.2-8 (scheduled-failure notification):** the notification test is "Telegram message received" for a failure of a scheduled or Mac-triggered run (flag file `.github/fail-next-scheduled` as before, or a Mac-triggered run with `inject=bad_fred_key`), plus: the Mac trigger failure alert (run `mac/pec-trigger.sh` with a bad `GH` path) and a watchdog alert (run it before any refresh that day) are each received. `alert-test.yml` and `mac/install.sh --test` pass.
- **6.2-9:** additionally confirm Mac launchd runs appear in the run history (`workflow_dispatch` events at ~13:20 and ~15:50 PT) and `~/Library/Logs/pec/trigger.log` shows them.
- **UAT:** "failure notification received" now means a Telegram message.
- **D2 dropped (2026-11-24):** the `.github/fail-next-scheduled` flag file is removed from the plan and will not be implemented. The Telegram path is already verified by real failures (runs 45311178206, 45313878206, 45314681065, 45319005518) and `alert-test.yml`; the Mac-triggered `inject=bad_fred_key` run remains the way to test a scheduled-style failure.

---

## Amendment B (2026-11-23) — intraday cadence, release polling, multpl outage, forward EPS

Owner requests (2026-11-23 18:22 PT) and answers (18:4x PT):

1. **Intraday cadence.** Weekday runs at 06:45, 07:45, 08:45, 09:45, 10:45, 11:45, 12:45 PT (market hours), 13:20 PT (post-close final), 15:50 PT (retry); watchdog 16:30 PT. Mac launchd is the trigger; the two GitHub post-close crons stay as backup. Only series that move intraday are fetched during market hours.
2. **Release polling.** Series that publish less than intraday are fetched only until the expected new observation arrives, then not again until the next one is due.
3. **multpl valuation series:** once per day after the close (owner choice).
4. **multpl failures — root cause (verified 2026-11-23):** `www.multpl.com` resolves to a single DigitalOcean IP `178.128.130.10`; TCP 80 and 443 are actively refused (RST in ~17 ms) from GitHub runners (Azure WestUS3, run 45312015589), and the owner's own browser on a residential connection also cannot connect. Other hosts work from the same runner. Conclusion: **multpl.com is down for everyone** (server not accepting connections at its DNS address), not rate limiting or cloud blocking. Window: last success 2026-11-23 06:57 PT (spike run), first refusal ≤ 17:31 PT. Handling: carry-forward + daily retry (item 3) already cover it; staleness is warn-only for these series. If the outage exceeds ~2 weeks, bring source alternatives to the owner (no source change without approval).

### Refresh-frequency model (replaces `market_daily` / `daily_once` / old `monthly` / `quarterly` semantics)

| `refresh_frequency` | Series | Fetch rule |
|---|---|---|
| `intraday` | all 14 Yahoo series | Trading day and market open (09:30–16:00 ET, early-close days 13:00 ET) → fetch every run. After the close → fetch once if not already fetched after today's close. Otherwise skip. |
| `daily_release` | FRED daily (rates, TIPS, credit, Fed funds, FX, WTI) and `yield_curve` | Expected latest observation = the trading day `publish_lag_bd` trading days before the last completed trading day (FRED: `publish_lag_bd: 1` default; Treasury `yield_curve`: 0). Fetch on every run while the stored last observation is older than expected; skip once satisfied. |
| `daily_after_close` | 5 multpl series | Fetch once per trading day, at the first run after the close; on failure retry on later runs that day. |
| `monthly` | `cpi_yoy`, `nber_recession` | Expected period = the most recently **ended** calendar month (FRED dates periods by their first day). While the stored last observation is earlier than that period's start: fetch at most once per calendar day. Once present: skip until the next month ends. |
| `quarterly` | `gdp_growth`, 3 delinquency series | Same as monthly with calendar quarters. |

- Trading days use an NYSE holiday + early-close list for 2026–2027 (new module `market_calendar.py`); weekends/holidays → no intraday fetch; `last_completed_trading_day` skips holidays.
- A failed fetch never counts as satisfied; rules re-evaluate next run.
- Healthcheck thresholds unchanged (docs/spike.md).
- Mac: owner re-runs `git pull && mac/install.sh` once to load the new times.

### Forward EPS (research: docs/eps-sources.md)
FactSet Earnings Insight (weekly PDF; `https://www.factset.com/earningsinsight` redirects to the latest) prints the forward 12-month P/E and the S&P 500 price; **NTM EPS = price ÷ forward P/E** (±~0.3 % from P/E rounding). Implied values: 2026-05-08 349.39, 07-17 371.12, 09-11 397.47, 10-09 406.56, **11-20 419.64**. The manual override (354.84, 2026-05-07) is stale and understates EPS by ~18 %. **Approved 2026-11-23; implemented.** `fetchers/factset_eps.py` follows the redirect, parses the P/E and price sentences with strict regexes (pypdf), range-checks (P/E 10-40, price 2000-20000, EPS 200-600, 8 % jump limit unless the override confirms) and fails closed. `PEC_EPS_MODE=factset` (set in refresh.yml; `override` forces manual only; `PEC_EPS_OVERRIDE_ONLY=1` is an alias of `override`): FactSet is used only if its report date is newer than the manual override's `effective_date`, so a newer manual entry always wins; if FactSet fails, the previous FactSet value is carried forward while it is still newer than the override, else the override. `valuation_inputs.forward_eps_ntm` records `source` (`factset`/`override`), `as_of`, `url`. FactSet is fetched at most once per Pacific day (cache persisted on the `state` branch with the month-end history, `tools/state.sh`). Month-end history gets the as_of month upserted; May-Oct 2026 backfilled in the seed (docs/eps-backfill.md). Healthcheck rule 6 accepts a FactSet value newer than the override. A Telegram info message is sent when the source switches or EPS moves more than 3 %.

### Phase 6 QA fixes (2026-11-24, branch `amendment-b`; spec: `docs/qa.md`)

- **M1:** every status record carries `last_attempt` (naive PT ISO). Monthly/quarterly errors are retried even if the expected period is present, at most once per PT day (throttled on `last_attempt`); `healthcheck.attempted()` uses `last_attempt` (fallback `last_fetched`) equal to `metadata.last_refreshed`. Old state without `last_attempt` works.
- **M2:** exchange-traded intraday series are satisfied after the close only when `last_obs >= last completed trading day` (ET); the data-based re-fetch stops at 18:00 PT on that day. Crypto and multpl (`daily_after_close`) keep the fetch-time rule (multpl is down and its row dates are irregular).
- **M3:** watchdog needs a successful `[state]` run on `main` created at or after 13:00 PT (`--limit 50`).
- **M4:** the notify job alerts on the first failed production run of the PT day, always for runs started 13:00-16:30 PT, always for `[TEST]`, never for cancelled runs; a "recovered" message is sent by the first successful production run after a failed one.
- **M5:** FactSet cache is reused only if fetched today after 13:00 PT and as_of is under 6 days old; FactSet errors are recorded under `forward_eps_ntm` in statuses; one Telegram info message when FactSet failed on 2 consecutive PT days or as_of is over 10 days old.
- **m1/m2:** `state_ref=state` only from `main`; `deploy` forced false for `state-test`. **m4:** Node 24 action majors. **m5:** healthcheck warning 60 days before `market_calendar.CALENDAR_YEARS` ends. **m6:** `refresh.log` leak-scanned; artifact upload only if the scan passes. **m10:** no EPS message when the rounded value is unchanged; sent after the state is saved. **m11:** EPS history is written for FactSet and override values; the in-progress month is dated `as_of`/today and re-dated to the month-end once the month has ended. **m12:** unparseable stamps count as never fetched. **m13/m14:** CI on pushes to any branch. **m3 (revisions):** resolved by owner decision 2026-11-24 — weekly re-check (see below).
- Owner decision 2026-11-24: monthly/quarterly re-checked weekly after the period's print arrives, to pick up revisions (QA m3)
