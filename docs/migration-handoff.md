# PEC Cloud Migration — Handoff

Prepared 2026-11-20 for a fresh Claude cloud session on Hogan's personal account. Read this file first, then `PEC_handoff.md` (full system reference) in the same zip. All times are Pacific.

---

## 1. Objective

Move the daily update of the Preston Economic Console (PEC) off Hogan's work laptop. After the move, the dashboard refreshes on a schedule and on demand from a web browser or phone, with no laptop and no Claude desktop app running.

**Done means:** the dashboard at a public URL refreshes unattended every weekday, Hogan can trigger a refresh from the web, a failed run tells him so, and every dashboard feature that works today still works.

## 2. Decisions Hogan has made

- Runner: GitHub Actions on his personal GitHub account (`wihogan`). Railway is the fallback runner only if needed. Supabase is not used.
- Nothing in the project is proprietary. A public repo is acceptable.
- He can supply other URLs to host the page if useful (see open question Q1).
- The work is done in his personal Claude cloud instance, from this handoff and zip.
- He wants minimal interaction. Ask only what is listed in section 10 or what genuinely blocks.
- Process he asked for: write a plan including unit tests, integration tests and UAT; have a separate model review the plan; revise; execute; have a separate model do QA. The orchestrator delegates tasks to subagents and names the model for each (section 9).

## 3. What is verified and what is not

Verified on 2026-11-20 by inspecting the project folder and running checks:

- The update is `python refresh_v2.py`: 41 series (21 FRED, 14 Yahoo, 5 multpl, 1 Treasury) plus one forward-EPS valuation input. Output is one self-contained `PEC.html` (about 4.9 MB) built from `PECtemplate.html` + `data_v2.json`.
- The only secret is `FRED_API_KEY`, read from the environment (`fetchers/fred.py`). It is **not** in the zip. It lives in `dashboard\.env` on the laptop.
- Publishing today is a copy of `PEC.html` to `GitHub\index.html` and a push to `wihogan/PV-macro-dashboard2` (`main`), served by GitHub Pages at `https://wihogan.github.io/PV-macro-dashboard2/`.
- That repo contains only `index.html`. **The source code is not in any git repo**; it exists only in shared-drive and in this zip.
- The pipeline keeps state between runs: `data_v2.json` (frequency checks and carry-forward on failure), `.cache/forward_eps_ntm_history.json`, `.cache/multpl_*.html`, and write-back to `config/forward_eps_seed.yaml`.
- Last laptop run: 2026-11-20 14:18, 41 series, 1 failure (`tips_5yr`, FRED connection timeout, prior data carried forward).
- Laptop runs happened every weekday through 2026-11-05, then none until 2026-11-20.
- The local clone of the publish repo is 149 MB after roughly 150 commits of a 5 MB file.
- Forward EPS is stale: `manual_override` is 354.84 dated 2026-05-07, source `override`. EPS history ends 2026-04-30. Forward P/E, the DDM fair value and the Fwd P/E chart line all depend on it.

Not verified. Treat each as a test to run, not a fact:

- Whether GitHub-hosted runners can reach Yahoo Finance, multpl.com and the Treasury XML feed reliably. Yahoo is known to throttle cloud IPs. This is the main technical risk.
- Whether a Claude cloud session with the repo attached can set repo secrets, change Pages settings or dispatch workflows. If not, those are Hogan clicks (section 10).
- Where the laptop's weekday refresh is scheduled. It is not a Windows scheduled task per `CLAUDE.md`, and it is not among Hogan's cloud scheduled tasks; it is most likely a local scheduled task in the Claude desktop app on the work laptop.

## 4. Target architecture (recommended; the plan reviewer should challenge it)

- **New public repo** on `wihogan` (suggested name `pec`) holding source, tests and the workflow. Building in a new repo keeps the current site untouched during the parallel run.
- **Workflow `refresh.yml`** with three triggers: weekday `schedule`, `workflow_dispatch` (inputs: `force`, optional `eps_value` + `eps_date`), and `push` to `main` for tests only.
- **Steps:** checkout, set up Python with pip cache, `pip install -r requirements.txt`, restore state, run unit tests, `python refresh_v2.py`, run `healthcheck.py`, save state, deploy to Pages.
- **Pages deployment by artifact** (`actions/upload-pages-artifact` + `actions/deploy-pages`), so the 5 MB HTML is never committed and the repo does not bloat.
- **State on an orphan `state` branch**, force-pushed as a single commit each run: `data_v2.json`, `.cache/` JSON and HTML caches, and `config/forward_eps_seed.yaml` if changed. This gives persistence without history growth. Alternatives to weigh: Actions cache (evicted after 7 idle days, so not safe alone) or committing state to `main`.
- **Health gate before deploy** (`healthcheck.py`, new): valid JSON, 41 series present, placeholder replaced, HTML above a size floor, failure count under a threshold, and a staleness check per series. The staleness check matters because carry-forward hides a blocked source: a Yahoo block would otherwise leave the page looking healthy with old prices. A failed gate fails the workflow, leaves the previous site in place, and GitHub emails Hogan.
- **Cutover:** after the parallel run passes UAT, replace `index.html` in `PV-macro-dashboard2` with a redirect to the new URL so the link already shared with others keeps working, then stop the laptop routine.

Required code changes, all small:

- Timezone. `_needs_refresh()` (line 458), `render()` (line 505), backups and `last_refreshed` use naive `datetime.now()`. Runners are UTC. Set `TZ=America/Los_Angeles` in the workflow and add a unit test that pins the behaviour; fix the naive/ET mix in `_needs_refresh()` if the test shows drift.
- Never pass `--series` in CI. It calls `input()` (line 710) and would hang.
- `backups/` is pointless on an ephemeral runner; skip or disable it there.
- Mark the existing `tests/test_fetchers.py` (four live-network tests) as integration so unit runs stay offline.
- Schedule: cron is UTC and does not follow daylight saving. `_needs_refresh()` already skips series that are current, so two daily crons are cheap. Pick times after the 16:00 ET close in both EST and EDT.

## 5. Known risks and how to handle them

| Risk | Handling |
|---|---|
| Yahoo throttles or blocks GitHub runners (14 series) | Test in Phase 3 before building further. If it fails: retry with backoff first; then a Railway cron as the runner; then alternate sources (FRED carries SP500, DJIA, NASDAQCOM with limited history; Stooq CSV; CoinGecko for crypto). Report to Hogan before changing a data source. |
| multpl.com blocks or changes layout (5 series) | Stale-cache fallback exists. The staleness check surfaces it. |
| Stale forward EPS | Out of scope to automate the scrape. Add `eps_value`/`eps_date` dispatch inputs that update `manual_override`, so it can be updated from a phone. Backfilling May–Oct 2026 history needs sourced numbers approved by Hogan; never invent values. |
| GitHub disables schedules after 60 days without repo activity | The state-branch push may not count as activity on the default branch. Verify, and add a keep-alive if needed. |
| Scheduled runs can start late or be dropped under load | Two crons per day plus manual dispatch. |
| Secret leakage | Key only in repo secrets. Confirm `.env` is git-ignored and not in the zip. Never echo the key in logs. |

## 6. Execution plan

Each phase names its owner model (section 9). Do not start a phase until the previous one's exit check passes.

- **Phase 0 — Plan.** Orchestrator turns this section into a detailed plan with file-level tasks. A separate reviewer model critiques it against this handoff and the source. Revise. Exit: reviewer has no blocking findings.
- **Phase 1 — Repo.** Create the repo, commit the source from the zip unchanged as the first commit, then `.gitignore`, README. Exit: clean clone installs and imports.
- **Phase 2 — Unit tests and code changes.** Write the unit suite (section 7.1) against unchanged code first, then make the changes in section 4 and add `healthcheck.py`. Exit: unit suite green offline.
- **Phase 3 — Connectivity spike.** A throwaway workflow that fetches one series per source from a GitHub runner, run several times across a day. Exit: go/no-go on Yahoo and multpl recorded with evidence. A no-go triggers the fallback in section 5 and a note to Hogan.
- **Phase 4 — Workflow.** Build `refresh.yml`, state branch, Pages deploy. Seed the state branch from the zip's `data_v2.json` and `.cache/`. Exit: manual dispatch produces a live page.
- **Phase 5 — Integration tests** (section 7.2). Exit: all pass, results written to `docs/test-report.md`.
- **Phase 6 — Independent QA.** A separate model reviews code, workflow, test evidence and the live page against section 11. Fix findings. Exit: QA sign-off recorded.
- **Phase 7 — Parallel run and UAT.** Five consecutive weekdays unattended alongside the laptop routine, then Hogan's UAT (section 7.3). Exit: Hogan signs off.
- **Phase 8 — Cutover.** Redirect the old URL, update `CLAUDE.md`, `README.md` and `update-dashboard.md` to the new process, tell Hogan to stop the laptop routine. Keep the old repo for 30 days as rollback.

## 7. Test plan

### 7.1 Unit tests (pytest, offline, run on every push)

- Config: `series_v2.yaml` loads; 41 unique ids; every `source` maps to a fetcher; required fields present.
- `_safe_float`, `_dates_to_strings`: NaN, None, rounding to 4 places.
- `_needs_refresh` and `_last_completed_business_day` with frozen clocks: each of the four frequencies, market open and closed, weekend, month and quarter boundaries, and the same cases with the process timezone set to UTC.
- `_compute_derived_valuation`: forward P/E; DDM with known inputs (EPS 354.84, 10-year 4.00 gives 124.194 / 0.025 = 4,967.76); non-positive denominator skipped; both delta strings; `fwd_pe_history` month join.
- `run()` with mocked fetchers: carry-forward on a fetcher exception, skip of current series, status metadata, counts.
- `render()`: both placeholders replaced, output parses back to the same JSON, no `__DASHBOARD_DATA__` left.
- Parsers on fixtures: multpl HTML (use the five cached pages in `.cache/`), Treasury XML sample, Yahoo `_extract_close` with flat and MultiIndex columns, FRED transform `pct_change_12m`.
- `spglobal` history: seed bootstrap, idempotent month append, seed write-back, all in a temp directory; fallback to `manual_override`.
- `healthcheck.py`: passes on the baseline `data_v2.json`; fails on missing series, invalid JSON, stale series, excess failures.

### 7.2 Integration tests (on GitHub Actions, live network)

- One live fetch per source (FRED, Yahoo, Treasury, multpl).
- Full refresh from a clean checkout with restored state; health gate passes; page deploys; URL returns 200 and shows the new timestamp in Pacific time.
- Second run shortly after: current series are skipped, state round-trips, output unchanged apart from the timestamp.
- `force` dispatch re-fetches everything.
- Data parity against the laptop baseline in the zip: same series ids, history lengths within tolerance, identical values on overlapping dates for a sample of series from each source.
- Failure injection: invalid FRED key fails the workflow and leaves the prior site live; one bogus series id carries forward and still deploys; simulated stale state trips the staleness gate.
- Schedule fires at the configured time; manual dispatch works from the GitHub web UI.
- EPS dispatch inputs update `manual_override` and the derived values change accordingly.

### 7.3 UAT (Hogan, about 10 minutes, checklist delivered to him)

- Page opens on desktop and phone; timestamp is today and in Pacific time.
- All five sections render: equities, rates, commodities and FX, macro and credit, valuation.
- KPI tiles are populated; range tabs and series toggles work; recession bands show.
- ERP slider moves the DDM fair value.
- Spot-check three values against an independent source (S&P 500 close, 10-year yield, Fed funds).
- Trigger a refresh from GitHub on the web or phone and see the timestamp change.
- Confirm a failure notification email arrives from the injected-failure test.
- Five consecutive unattended weekday runs observed. Sign-off recorded in `docs/uat-signoff.md`.

## 8. Rollback

The laptop routine and the old repo stay untouched until Phase 8. If the cloud pipeline fails after cutover, restore the old `index.html` in `PV-macro-dashboard2` and run the laptop update as before.

## 9. Model routing

Hogan's standing rule: Sonnet (latest) for well-defined coding, documents and repetitive work; never Sonnet for testing; Opus (latest) as supervisor; escalate to the top model only for unusually complex or high-value work.

| Task | Model | Why |
|---|---|---|
| Orchestration, detailed plan, phase gates | Opus (latest) | Requirements are well defined; the top tier is not needed. |
| Plan review (Phase 0) | Top-tier model if available, otherwise a fresh-context Opus subagent | Must be a different model or context from the plan's author. |
| Repo setup, workflow YAML, code changes, `healthcheck.py`, docs | Sonnet (latest) | Tightly specified. |
| Unit and integration test design and writing | Opus (latest) | Testing is not delegated to Sonnet. |
| Connectivity spike analysis and go/no-go | Opus (latest) | Judgment call on thin evidence. |
| Independent QA (Phase 6) | Top-tier model if available, otherwise a fresh-context Opus subagent that did not write the code | Separation from the builder. |
| UAT checklist, test report, sign-off docs | Sonnet (latest) | Document assembly. |

## 10. What only Hogan can do

1. Start the cloud session with GitHub connected and upload the zip.
2. Create the new repo, or approve Claude creating it.
3. Add the repo secret `FRED_API_KEY` (value is in `dashboard\.env` on the work laptop).
4. If Claude cannot: set Pages source to "GitHub Actions".
5. Answer the open questions below.
6. Run the UAT checklist and sign off.
7. After cutover, stop the weekday refresh routine on the work laptop.

Open questions:

- **Q1.** Keep a `github.io` URL, or point one of your own domains at it? Default if unanswered: `github.io`, with the old link redirected.
- **Q2.** What time should the daily refresh land? Default: shortly after 14:00 Pacific (after the 16:00 ET close), with a second run later as insurance.
- **Q3.** Forward EPS: approve a current value and date, and say whether to backfill May–October 2026 history. Default: leave as is and flag it on the UAT checklist.

## 11. Acceptance criteria

- Unattended weekday refresh for five consecutive business days with no laptop involved.
- Manual refresh works from a browser.
- 41 series present (42 after Amendment C); data parity with the laptop baseline confirmed.
- All dashboard features in the UAT list work.
- A failed run does not replace the live page and notifies Hogan.
- No secret in the repo, the logs or the zip.
- Repo does not grow by megabytes per run.
- Unit, integration and QA evidence written to `docs/`. Docs updated to the new process.

## 12. Zip contents

`pec_cloud_migration_package.zip`:

- `PEC_Cloud_Migration_Handoff.md` — this file.
- `PEC_handoff.md` — full system reference (architecture, schema, every series, fetcher specs, template structure).
- `dashboard/` — `refresh_v2.py`, `preview.py`, `PECtemplate.html`, `requirements.txt`, `.gitignore`, `CLAUDE.md`, `README.md`, `BUILD_NOTES.md`, `pec-update.skill`, `fetchers/`, `config/`, `tests/`, `.claude/commands/update-dashboard.md`, `data_v2.json` (baseline from 2026-11-20 14:18), `.cache/` JSON and multpl HTML caches, and `Notes and Execution Guides/` design reference and V2 execution plan.

Left out on purpose: `.env` (secret), `.venv/`, `backups/`, `GitHub/` (the publish clone), `_v1_archive/`, `mockups/`, `PEC.html` and `PEC_dev.html` (regenerated from the template and `data_v2.json`), `.cache/ie_data.xls` (unused Shiller file).

## 13. Kickoff prompt

Paste this into the new session after uploading the zip:

> Unzip `pec_cloud_migration_package.zip` and read `PEC_Cloud_Migration_Handoff.md` in full, then `PEC_handoff.md`. You are the orchestrator for the migration it describes. Follow section 6 phase by phase, delegate tasks to subagents using the model routing in section 9, and do not skip the plan review or the independent QA. Treat every item under "Not verified" in section 3 as something to test. Work without asking me questions except those in section 10 or a true blocker; where a default is given, use it and say so. Start with Phase 0 and show me the reviewed plan before Phase 1.
