# PEC Migration Plan — Independent Review R1

Reviewer: Fable (fresh context) · 2026-11-20 · Plan reviewed: `PEC_Migration_Plan.md` draft 1
Method: read both handoffs, the plan, all source; parsed `series_v2.yaml` and `data_v2.json` read-only (`python -I`); no project code executed. GitHub behaviour claims checked against docs.github.com where marked [doc].
Paths are relative to `/home/claude/pkg/dashboard/`.

Verdict: **not ready for Phase 1.** 9 blocking findings. The architecture (Actions + Pages artifact + orphan `state` branch + health gate) is sound; the defects are in thresholds, schedule reasoning, EPS state, notification, test isolation and unverified session capabilities.

---

## Blocking findings

### B1. Healthcheck staleness thresholds fail on the baseline and will fail on the calendar
Evidence (baseline `data_v2.json`, clock 2026-11-20):

| Series | freq | last obs | age (d) | last_fetched | plan threshold |
|---|---|---|---|---|---|
| `cpi_yoy`, `nber_recession` | monthly | 2026-09-01 | **80** | 2026-11-02 | 75 → FAIL today |
| `tips_5yr` (status `error`) | market_daily | 2026-11-03 | **17** | 2026-11-05 | 6 → FAIL today |
| `gdp_growth` + 3 delinquency | quarterly | 2026-04-01 | 233 | 2026-10-01 | 250 → FAIL from 2026-12-08 |

Cause: `_needs_refresh` fetches monthly series once per calendar month and quarterly once per calendar quarter (`refresh_v2.py:479-485`), on the first run of the period. So CPI fetched Nov 2 (Sept obs) is not re-fetched until Dec 1: normal worst-case age ≈ 92 d. Quarterly obs dated 04-01 fetched Oct 1 stays until the first run of January: ≈ 275 d.
Consequences: the plan's own unit test "healthcheck passes on baseline (clock frozen 2026-11-20)" fails; Phase 4 exit (first dispatch deploys) fails unless `force`; deploys stop Dec 8–31 during or just after the parallel run.
Fix: thresholds derived from fetch cadence, not release cadence: monthly ≥ 110, quarterly ≥ 290, `sp500_earnings` 220 is fine. Better (see Cuts): hard-fail staleness only for `market_daily`/`daily_once`; monthly/quarterly warn-only. A US data-release delay (e.g. a shutdown) must not take the whole page down. For the baseline unit test, either expect `tips_5yr` to fail or use a fixture where it is fresh. F6 understates quarterly lag ("~4–7 months"; actual 233 d ≈ 7.7 months and rising to 9).

### B2. Health rule 4 contradicts itself and makes single-series sources a deploy kill-switch
"every series from any single source failed" + Treasury has exactly one series (`yield_curve`) ⇒ one Treasury timeout blocks deploy, contradicting "one transient FRED timeout must not block deploy". `treasury.py` makes ~17 sequential year requests (2010–2026) per fetch with no retry (`treasury.py:150-154`), so a single failed request fails the series. multpl is 5 series fetched on the 1st of the month from a cold runner: a block gives 5 failures > 3 ⇒ no deploy, and because failed series keep their old `last_fetched` (`refresh_v2.py:776`) they retry and fail every run until multpl unblocks. Also "every series from a source failed" is vacuous on runs where that source was entirely skipped; the rule must be defined over attempted series.
Fix: rule 4 = `failure_count > 3` among `market_daily` series, or all attempted FRED fail, or all attempted Yahoo fail. Treasury and multpl failures are governed by staleness only (`yield_curve` 6 d; multpl monthly threshold).

### B3. Schedule rationale is wrong; first cron races FRED and locks the day in
- Plan §4 says the second run "picks up late Treasury curve and FRED same-day prints". Refuted by code: after a post-close fetch, `market_daily` returns `last < close_local` = False (`refresh_v2.py:466-472`) and `daily_once` returns `last.date() < lbd` = False (`:475-477`). The second run re-fetches only series that errored (they keep the old `last_fetched`) or everything if run 1 was dropped. That is still useful, but it is retry insurance, not a late-data pickup.
- First cron `17 21 UTC` = 16:17 ET in winter. FRED's daily rates (H.15: DGS*, DFII*) for day D post on D+1 at about 16:15–16:20 ET. A 16:17 ET fetch can land just before the update, is then marked current, and the 10-year used by the DDM stays two days old until the next day. The laptop baseline ran 17:18 ET and had D-1 FRED data and the same-day Treasury curve. Yahoo index closes can also still be settling at 16:17.
- Handoff Q2 default is "shortly after 14:00 Pacific"; the plan silently moved to 13:17 PST.
Fix: `17 22 * * 1-5` (14:17 PST / 15:17 PDT) and `47 23 * * 1-5` (15:47 PST / 16:47 PDT). Both stay before 00:00 UTC, so `1-5` still means Mon–Fri Pacific (a cron after 00:00 UTC would need `2-6`). Reword the purpose of run 2. DST arithmetic in the plan is otherwise correct.

### B4. "A failed run notifies Hogan" is not established
- [doc] Scheduled-workflow notifications go to the user who created the workflow, or who last changed its cron, or who re-enabled it; other triggers notify the actor who triggered the run; and only if that user has Actions notifications enabled. https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs
- The workflow will be pushed by this session's GitHub identity (F10: `PV996`, possibly via an app token). The recipient is therefore whoever GitHub attributes that push to, at that account's address, not necessarily an inbox Hogan reads. Handoff names `wihogan` as his personal account.
- Test 6.2-6(a) injects the failure by `workflow_dispatch`. That email goes to the dispatcher and proves nothing about a scheduled failure. UAT item "failure email received" inherits the flaw.
- Cancelled runs (see N1) send no failure signal.
Fix: add an explicit `if: failure()` step that opens or comments on a pinned GitHub issue @-mentioning the owner account Hogan reads (`issues: write`); have Hogan confirm Settings → Notifications → Actions email is on; verify with one failure on a `schedule`-triggered run (temporary flag file on `main` for one cron slot). Record which address received it.

### B5. Forward-EPS: live path outranks `manual_override`; persisting `forward_eps_ntm.json` makes the dispatch input silently ineffective
- `_fetch_valuation_inputs` uses `manual_override` only when the fetcher raises (`refresh_v2.py:334`). `spglobal.fetch` returns a value from S&P xlsx → FactSet/Yardeni → `.cache/forward_eps_ntm.json` (`spglobal.py:566-627`). Any of those succeeding yields `source: "live"` and the override, hence `eps_value`/`eps_date`, is ignored.
- `.cache/forward_eps_ntm.json` does not exist in the zip (laptop has never had a live xlsx parse; source is `override`). The plan lists it as persisted state. If the S&P xlsx ever parses from a runner IP, that file is written (`spglobal.py:586`), then persisted, and from then on path 3 returns the old value forever as "live". The xlsx parser is heuristic (`_try_parse_sheet`) and the Yardeni/FactSet regexes are loose (`factset_eps.py:66-79`); a runner may get a different answer from the laptop's 403. A live hit also appends a month-end-dated history row and rewrites the seed (`spglobal.py:171-197, 643`).
- Net: DDM/Fwd P/E can change to an unapproved number (handoff: "never invent values"), parity breaks, and test 6.2-8 passes or fails depending on network luck.
Fix: (1) do not persist `forward_eps_ntm.json`; `state.sh save` must tolerate absent files. (2) Decide precedence explicitly. Recommended minimal change: env `PEC_EPS_OVERRIDE_ONLY=1` in CI makes `_fetch_valuation_inputs` skip the live attempt (history fetch still works, it is offline). (3) Healthcheck asserts `forward_eps_ntm.source == "override"` and value equals the YAML override; (4) spike records what the three EPS endpoints return from a runner, for Hogan's later decision. F8 stands.

### B6. Commit 1 "unchanged source" to a public repo publishes personal/work identifiers irreversibly, and tracks the 2.4 MB data file
- `CLAUDE.md:11-12`, `README.md`, `.claude/commands/update-dashboard.md`, `Notes and Execution Guides/PEC_V2_Execution_Plan_v2_1.md`, `pec-update.skill` (zip; `SKILL.md`, `references/github_setup.md`) and `PEC_handoff.md:17` contain Hogan's work email, employer, title and `C:\Users\<user>\shared-drive - <employer>\…` paths. "Nothing proprietary" (handoff §2) does not obviously cover putting a work email in public git history. Removing it later needs a history rewrite.
- No secrets found: scanned zip for 32-hex strings and key assignments; only placeholders (`your_key_here`, `ghp_xxxx…`). `.env` absent. Good.
- Old `.gitignore` ignores `data.json`, not `data_v2.json`. Commit 1 would track `data_v2.json` (2.4 MB). Commit 2's new `.gitignore` does not untrack it. Then every run dirties a tracked file on `main`; the EPS commit step, if it uses `git add -A`/`commit -a`, pushes megabytes to `main` per dispatch (violates §11 "repo does not grow"). It also falsifies "state missing ⇒ cold start".
- Root `CLAUDE.md` is auto-loaded by Claude sessions and tells them to run the Windows copy-and-push publish. Leaving it until Phase 8 misdirects every subagent in Phases 2–7.
Fix: before commit 1, ask Hogan one question (scrub email/employer/paths: default yes) or default to scrubbing and say so. Commit 1 = source minus `data_v2.json`, `.cache/`, `pec-update.skill`; baseline goes to `tests/fixtures/baseline_data_v2.json` (one-time 2.4 MB, needed for parity). Replace `CLAUDE.md` with a short accurate stub in Phase 1. EPS commit step stages `config/series_v2.yaml` only. `state.sh save` must use `git add -f` or a separate worktree, since `.gitignore` will ignore its files.

### B7. Session capabilities the plan depends on are unverified
Plan assumes the session can: push workflow files, push to `main`, create/force-push an orphan `state` branch, dispatch workflows, and read run status/logs for evidence. F10 already found Pages and secrets APIs blocked. Known constraints of app-token sessions: pushes that add `.github/workflows/*` need a `workflows` permission; pushes may be limited to a session branch; Actions API may be blocked like Pages. Scheduled workflows run only from the default branch, so anything stuck on a session branch never fires. If any of these fail, Phases 3–5 stall or become many Hogan clicks (handoff §2: minimal interaction).
Fix: Phase 1 exit adds a capability probe (push to `main`; push a trivial workflow; create a second branch; dispatch; read a run). Design for the worst case now: seed `state` from inside a workflow (job copies `tests/fixtures/` baseline to `state`), not from the session; make every workflow write its evidence somewhere git/HTTP-readable (`health.json` and a run summary in the Pages artifact, spike results committed to a `spike-results` branch); drive the spike by cron (B9) rather than dispatch.

### B8. Integration tests are not isolated from production state, `main` and the live page; several cannot work as written
- 6.2-6 runs on "a throwaway branch with `mode: integration`" (deploy disabled) but 6(b) expects "deploy proceeds" and 6(a) expects "live site unchanged" (trivially true with deploy off). The default `github-pages` environment only allows deployments from the default branch, so a branch run could not deploy anyway.
- Nothing stops an integration run from `state.sh save` to the production `state` branch; 6(c) "state doctored" doctors production state.
- 6(a) bogus `FRED_API_KEY`: no mechanism given (secret edits are Hogan clicks). With restored state where FRED series are already current, nothing is fetched and nothing fails; needs `force`.
- 6.2-8 writes an invented EPS (360, today) to `main` and the public page, then "restore 354.84": `set_eps.py` rejects dates older than 60 days, and the original date is 2026-05-07. Depends on B5.
- 6.2-3 "all current series skipped" is false 09:30–16:00 ET: `_market_is_open()` forces all 29 `market_daily` series to re-fetch (`refresh_v2.py:461-462`). State the time window.
- 6.2-2 `curl` immediately after deploy can read the cached previous page (Pages CDN). Poll `page_url` until the timestamp changes, with a timeout.
- 6.2-5 parity "identical values on overlapping dates" will fail on revised FRED series (GDP, CPI YoY, delinquency). Use daily FRED series for the exact-match sample.
Fix: dispatch inputs `deploy` (bool, default true), `state_ref` (default `state`; tests use `state-test`), `inject` (`none|bad_fred_key|bogus_series|stale_state`) implemented in the workflow (`env: FRED_API_KEY: invalid` when injected; force on). EPS test runs with `deploy=false`, `state_ref=state-test`, and is undone by `git revert`. The one test that must touch production is "gate failure leaves the live page untouched": run it on `main` with `inject=bad_fred_key`, `deploy=true`, and compare the live timestamp before and after.

### B9. Connectivity spike is not representative of production load
Plan: "one series per source, 3× per run with backoff". Production does 14 back-to-back `yf.download` calls with no retry or backoff (`yahoo.py:216-227`), ~17 Treasury requests, 5 multpl pages, 21 FRED calls. Yahoo throttling is rate-dependent; a one-ticker probe with backoff can pass while the real run fails. This is the handoff's "main technical risk" and gates Phase 4.
Fix: the spike runs the real `python refresh_v2.py --force` on a cold checkout plus `healthcheck`-style per-source counts, on an hourly cron for one trading day and one weekend slot (no Hogan clicks, covers B7), writing a one-line result per run. Go = Yahoo 14/14 in ≥ 90 % of runs; otherwise evaluate adding retry before declaring no-go. Record EPS endpoint results (B5).

---

## Non-blocking findings (fix in the plan revision; none stop Phase 1)

- **N1. Workflow-level concurrency + `push` trigger in the same workflow.** [doc] A concurrency group holds one running and one pending run; a newly queued run cancels the pending one. A push-triggered test run shares group `pec-refresh` and can cancel a pending scheduled refresh (cancelled, no failure email). Fix: move push tests to `ci.yml`, or put `concurrency` on the `refresh` job only. https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency
- **N2. Keepalive arithmetic.** Monthly cron with a 45-day test can leave up to ~75 days without activity (> 60). Fix: a step in the scheduled refresh: if last `main` commit is older than 30 days, push an empty commit. No separate workflow. Whether bot commits and `state` pushes count as activity is still unverified; say so.
- **N3. Commits pushed with `GITHUB_TOKEN` do not trigger workflows.** So the EPS commit and keepalive commit cause no loop, and also no push-test run. Correct by accident; state it. Handle a rejected push (main moved) with a rebase-and-retry or fail before refreshing.
- **N4. Permissions too broad.** `contents: write` at workflow level gives the push-test job write access. Set per job: test `contents: read`; refresh `contents: write` (+ `issues: write` for B4); deploy `pages: write`, `id-token: write`.
- **N5. Pages artifact needs `index.html`.** Plan never renames `PEC.html`. Add: copy to `_site/index.html` (+ `health.json`).
- **N6. Missing job `timeout-minutes`.** `fredapi` and the fetchers can hang; default limit is 6 h. Set 15–20 min.
- **N7. Secret hygiene has no verification step.** Fetcher error strings are written to `metadata.statuses[].error`, embedded in the public page and `state`; log masking does not cover file contents. Add a pre-deploy step failing if the key value appears in `PEC.html`/`data_v2.json`; pass dispatch inputs through `env:`, never `${{ }}` inside `run:`. Add an acceptance check: grep repo history and run logs for the key.
- **N8. §11 "repo does not grow" has no measurement.** Record repo size (API or `git count-objects -vH` on a fresh clone) before and after ≥ 10 runs in the test report.
- **N9. Unit tests are not offline by construction.** `run()` uses module constants `HERE`, `CONFIG_PATH`, `OUTPUT_PATH`, `BACKUP_DIR`; `render()` reads/writes under `HERE` (`refresh_v2.py:85-88, 492-493`); `_load_fetchers()` returns the real `spglobal`, which makes three network calls for valuation inputs. Tests must monkeypatch all of these plus `spglobal`'s `_CACHE_DIR/_HISTORY_JSON/_SEED_YAML/_CACHE_JSON/_CACHE_XLSX` and `date.today()`. Enforce with an autouse fixture that blocks `socket.socket` (or `pytest-socket`). Importing `refresh_v2` runs `sys.stdout.reconfigure` and `logging.basicConfig` (`:76, :94`); confirm it imports under pytest capture early in Phase 2.
- **N10. Handoff §7.2 wants a live multpl test;** existing file has FRED, Yahoo, Treasury, Shiller. Dropping Shiller is right (unused; no series has `source: shiller`), but a multpl live test must be added.
- **N11. `PEC.html` embeds the JSON twice.** `__DASHBOARD_DATA__` occurs in an HTML comment (`PECtemplate.html:1048`) and the real tag (`:1051`); `replace` fills both, hence 4.9 MB = 2 × 2.4 MB. Healthcheck must parse `<script id="pec-data">` specifically. The 3 MB floor depends on this accident; use ≥ 1.5 MB or a ratio to `data_v2.json`. Optional later: fix the comment and halve the page.
- **N12. Timestamp has no zone label** (`:505`, template `:518`). "Shows Pacific time" can only be checked by comparing with `TZ=America/Los_Angeles date`. Set `TZ` at workflow `env` level so tests, refresh and healthcheck agree.
- **N13. Python version unpinned;** `beautifulsoup4` should be pinned like the rest; test-only deps (`time-machine`) belong in `requirements-dev.txt`.
- **N14. `state.sh restore` must distinguish "branch absent" from "fetch failed".** A transient fetch error should fail the job, not trigger a silent cold start.
- **N15. State `forward_eps_seed.yaml` overriding `main`** means an approved backfill committed to `main` is silently discarded, and history JSON outranks the seed anyway (`spglobal.py:548-551`). See Cuts.
- **N16. Pre-existing data-latency behaviour worth telling Hogan** (not a migration defect): monthly series refresh only on the first run of a month, quarterly on the first run of a quarter, so mid-month CPI appears up to ~3 weeks late and GDP up to ~2 months late.
- **N17. Docs.** Handoff Phase 8 says update `update-dashboard.md`; plan retires it. Fine, but say so, and leave a one-paragraph replacement ("dispatch the workflow") since Hogan may still ask Claude to "update the dashboard".
- **N18. Q0 default.** Defaulting to `PV996` contradicts handoff §2 (`wihogan`). It is correctly raised as a question; do not proceed on the default without an answer, because it fixes the public URL and the notification recipient (B4).
- **N19. Line refs.** F4 cites `multpl.py` 67–75; actual `_cache_is_fresh` is 70–75. Others accurate.

---

## Confirmed claims

| Claim | Status | Evidence |
|---|---|---|
| F1 `beautifulsoup4` missing, `Jinja2` unused | Confirmed | `multpl.py:30`; not in `requirements.txt`; no `jinja` import anywhere. `tzdata` not needed on ubuntu runners. `pdfplumber` also absent ⇒ FactSet PDF path is dead code. |
| F2 ERP slider removed | Confirmed | Only `<input type="range">` is `yc-scrubber` (`PECtemplate.html:682`); `.erp-slider` CSS only (`:460`); Design Reference line 301. UAT rewrite is right. |
| F3 DDM rounded to int; expect 4968 | Confirmed | `refresh_v2.py:620`; `round(354.84*0.35/0.025)` = 4968 (handoff's 4,967.76 is pre-rounding). |
| F4 TTL caches use mtime; don't persist them | Confirmed | `multpl.py:70-75`, `yahoo.py:56-60`, `factset_eps.py:92-97`, `spglobal.py:274-278`. `yahoo_info` path is unused (no series has `field:`). Decision correct, but see B5 for `forward_eps_ntm.json`. |
| F5 `.gitignore` has V1 names | Confirmed | ignores `data.json`, `dashboard.html`. See B6 for the tracking consequence. |
| F6 lags differ by series | Confirmed, understated | FX 7 d, WTI 3 d, `sp500_earnings` 143 d, quarterly 233 d, ICE 786 obs from 2023-11-21. Thresholds drawn from it are wrong (B1). |
| F7 `_needs_refresh` consistent under a stable TZ | Confirmed | `:457-472`; ET helpers are tz-aware (`:424, :434`). `TZ=America/Los_Angeles` matches baseline timestamps. No code fix needed. |
| F8 override path never appends history | Confirmed | `_ensure_history` only at `spglobal.py:643`, after a non-override value. History ends 2026-04-30 (64 rows in `fwd_pe_history`). |
| F9 `--series` calls `input()` | Confirmed | `refresh_v2.py:710`. |
| F10 session sees only `PV996` repos | Confirmed (repo list) | `list_repos` → `PV996/lifeos`, `PV996/Kohai`. The `gh api` 403s were not re-tested. |
| 41 series: 21 FRED, 14 Yahoo, 5 multpl, 1 Treasury; 29/7/4/1 by frequency | Confirmed | parsed YAML; ids unique; baseline has the 41 + `fwd_pe_history`. |
| Cron DST arithmetic; `1-5` alignment | Confirmed | but times should move (B3). |
| No other interactive prompts, Windows-only calls or import-time network | Confirmed | grep: one `input()`; no `subprocess`, `os.startfile`, drive paths in `.py`; `factset_eps.py:51` does an import-time `mkdir` only. |
| No secret in the zip | Confirmed | placeholders only. |

---

## Suggested cuts

1. **`keepalive.yml`** → one conditional step in the scheduled refresh (N2).
2. **`config/health.yaml`** → a constants dict in `healthcheck.py`. One file fewer, same testability.
3. **`PEC_NO_BACKUP` code change** → drop. A backup copy on an ephemeral runner is harmless and ignored; this leaves `refresh_v2.py` with at most the one EPS change in B5. (Requires explicit-path staging, B6.)
4. **Persisted state** → only `data_v2.json` and `.cache/forward_eps_ntm_history.json`. Drop `forward_eps_ntm.json` (B5) and `forward_eps_seed.yaml` (N15); the seed on `main` remains the bootstrap, and a backfill = commit seed + delete history from `state`.
5. **Hard staleness gates for monthly/quarterly** → warn-only in `health.json` and the run summary (B1).
6. **`mode: integration` on a throwaway branch** → three explicit dispatch inputs (`deploy`, `state_ref`, `inject`) (B8).
7. **ICE BofA footer line** → out of scope (UI change); raise with Hogan separately.
8. **`set_eps.py` "not older than 60 days" rule** → drop; keep float range and not-future. It blocks legitimate restores.
9. **Separate `test` job** → steps inside `refresh` (as handoff §4 lists) saves a second install; keep `deploy` as its own job. Optional.
10. **`time-machine`** → optional; a small `datetime` subclass monkeypatch plus `time.tzset()` covers the frozen-clock and UTC cases without a compiled dependency.
