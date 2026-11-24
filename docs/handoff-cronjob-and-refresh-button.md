# Handoff — PEC: cron-job.org trigger + manual "Refresh now" button

Prepared 2026-11-24 for a fresh Claude cloud session on Hogan's account. All times Pacific unless marked UTC/ET.
Read this file first, then `docs/plan.md` (Amendments A and B and "Phase 6 QA fixes"), `docs/runbook.md`, `docs/qa.md`, `CLAUDE.md`.

---

## 1. Objectives

**A. Replace the iMac as the refresh trigger with cron-job.org** (owner-chosen "Option A"), so nothing depends on the iMac. After this work the iMac launchd jobs are optional (keep as backup or uninstall — owner's call; default: keep for 2 weeks, then uninstall).

**B. Add a manual "Refresh now" control to the dashboard page**, if the intraday basket is small enough (it is — see §4), limited to **at most once every 5 minutes**, and **advise the owner on data-source rate-limit risk** (§5) before building.

**Done means:**
- Weekday refreshes run at 06:45, 07:45, 08:45, 09:45, 10:45, 11:45, 12:45, 13:20 and 15:50 PT, triggered by cron-job.org, with no iMac involvement, for 5 consecutive trading days.
- The 16:30 PT "no successful post-close refresh today" watchdog runs without the iMac and alerts via Telegram.
- If cron-job.org fails to reach GitHub, the owner is told (cron-job.org failure email and/or Telegram).
- The page offers a refresh control that cannot trigger more than one production refresh per 5 minutes (enforced server-side, not just in the browser), and no secret is exposed in the public page or repo.
- Unit tests, a plan review, and an independent QA pass exist for the new pieces, as in the earlier phases.

## 2. Current system (verified 2026-11-24 unless marked)

- **Repo:** `github.com/wihogan/PEC` (public). Pages URL `https://wihogan.github.io/PEC/` (`health.json` alongside).
- **Branches:** `main` = production. `amendment-b` = pending merge (intraday/release-polling refresh model, NYSE calendar, FactSet forward-EPS automation, Phase 6 QA fixes). At handoff time the QA-fix tests were being updated and a QA re-check was pending. **Check whether `amendment-b` has merged before starting**; build on whatever `main` is then, and coordinate if it hasn't merged (the hourly schedule in §3 assumes Amendment B's model, under which off-session runs fetch only what's due).
- **Orphan branches:** `state` (production runtime state: `data_v2.json`, EPS history/cache — written only by workflows), `state-test` (integration tests), `spike-results` (archive).
- **Workflows:** `refresh.yml` (dispatch + two backup crons `17 22 * * 1-5`, `47 23 * * 1-5`; jobs refresh → deploy → notify; run title `refresh [state]` / `refresh [state-test]`; concurrency group per state branch, no cancel), `ci.yml`, `alert-test.yml`.
- **Secrets (repo):** `FRED_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Never print or commit them.
- **Current trigger:** iMac launchd (`mac/install.sh`) → `gh workflow run refresh.yml`; watchdog `mac/pec-watchdog.sh` (on `amendment-b`: requires a successful `[state]` run created ≥ 13:00 PT, `--branch main`). GitHub's own `schedule` proved unreliable (9 of 64 hourly slots fired, delays 5–50 min; docs/spike.md) — keep it only as a backup.
- **Alerts:** Telegram from the `notify` job (on `amendment-b`: first failed production run of the day + all failures 13:00–16:30 PT; "✅ recovered" on next success); `[TEST]` prefix for state-test.
- **Intraday basket:** 14 Yahoo Finance series (indices ^GSPC ^DJI ^IXIC ^RUT; ETFs VEA VWO EFA EEM; futures GC=F SI=F PL=F; crypto BTC-USD ETH-USD XRP-USD), fetched with `yfinance` (`fetchers/yahoo.py`, `yf.download(..., start=start_date)` — **full history from 2010, or 1999 for sp500, on every fetch**). All other series (FRED, Treasury, multpl, FactSet) are release-polled and are not fetched intraday under Amendment B.
- **Run cost:** a full forced refresh ≈ 5.5 min; an intraday run (14 Yahoo + overhead + tests + deploy) ≈ 2–3 min (estimate; measure). Public repo → Actions minutes are free.

### Session constraints you will hit
- This Claude session's GitHub identity is collaborator `PV996` (Write). It can push, create branches, dispatch workflows and read runs/logs via `gh api`. It **cannot**: change repo settings, read or set secrets, delete branches (do that inside workflows), use `gh run download`, or fetch `github.io`. Yahoo, FRED, multpl, FactSet and cron-job.org are not reachable from the sandbox — only from GitHub runners.
- **Owner-only actions:** create the cron-job.org account and jobs (or give you an API key for its REST API), create the GitHub fine-grained token, add repo secrets, any Cloudflare/other account signup, UAT.
- Model routing (owner's standing rule): Sonnet for well-defined coding/docs; **never Sonnet for tests** (Opus writes tests); Opus orchestrates; a top-tier model (Fable) for plan review and independent QA, in a fresh context.
- Process: plan → independent review → build → tests → independent QA → owner UAT. Minimal owner interaction; ask only what blocks.

## 3. Objective A — cron-job.org trigger

### Design (validate in plan review)
- cron-job.org POSTs to the GitHub API:
  `POST https://api.github.com/repos/wihogan/PEC/actions/workflows/refresh.yml/dispatches`
  Headers: `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json`. Body: `{"ref":"main"}` (defaults → production run titled `refresh [state]`). GitHub returns **204** on success.
- cron-job.org supports custom headers, POST bodies, per-job time zones, and failure notifications (verify current limits and wording on cron-job.org before relying on them).
- **Jobs (time zone America/Los_Angeles, Mon–Fri):**
  1. `refresh-intraday`: hours 6–12, minute 45.
  2. `refresh-postclose`: 13:20.
  3. `refresh-retry`: 15:50.
  4. `watchdog`: 16:30 → dispatches a **new** `watchdog.yml` workflow (see below).
  Market holidays and early closes are handled by the code (Amendment B), so cron-job.org needs no holiday logic.
- **New `watchdog.yml`** (workflow_dispatch only; `permissions: actions: read, contents: read`): port `mac/pec-watchdog.sh` logic (production `[state]` runs on `main`, success created ≥ 13:00 PT today on trading days, `market_calendar`-aware so holidays don't alert) and send Telegram via `tools/notify_telegram.sh`. This removes the last iMac dependency.
- **Token:** a GitHub **fine-grained personal access token** created by the owner (`wihogan`), scoped to **only** `wihogan/PEC`, permission **Actions: Read and write**, nothing else. Set an expiry and record the renewal date in `docs/runbook.md`. (Optional hardening: have a monthly check warn via Telegram ~14 days before expiry if you can obtain the expiry date without extra permissions; otherwise a runbook reminder.)
- **Failure signalling:** turn on cron-job.org's failure notification email for each job. The existing 16:30 watchdog catches "trigger never fired"; the `notify` job catches failed runs.
- **Optional:** use cron-job.org's REST API (Bearer API key from the owner's account) so the session can create the four jobs reproducibly from a script in `tools/` instead of the owner clicking. Its API host may not be reachable from the sandbox; if not, run the script from a `workflow_dispatch` workflow with the API key as a repo secret, or give the owner click-by-click steps.

### Steps
1. Plan (incl. §4/§5 decision) → independent review (Fable) → revise.
2. Build `watchdog.yml` (Sonnet), tests for any new Python (Opus), dispatch it on `state-test` semantics to verify (it only reads runs, so it's safe on production data).
3. Owner: create the token; create the cron-job.org jobs (or API key). Give exact click-paths/screens in plain language — the owner is not a GitHub power user.
4. Verify: each cron-job.org job's test run returns 204; the run appears as `refresh [state]` with `triggering_actor` = `wihogan` (token owner); deploy succeeds.
5. Parallel period: 5 trading days with cron-job.org + GitHub backup crons; Mac jobs still installed. Then owner decides whether to uninstall the Mac jobs (`mac/uninstall.sh`).
6. Update `docs/runbook.md`, `mac/README.md`, `CLAUDE.md`, `docs/plan.md` (Amendment C).

## 4. Objective B — "Refresh now" control on the page

The intraday basket is small (14 Yahoo series), so a manual refresh is reasonable. Constraints:

- **The page is static and public.** It cannot hold a GitHub token. Anything in page JS is public, and the repo is public. A browser-only 5-minute cooldown (localStorage) is cosmetic: anyone could call the endpoint directly. **The 5-minute limit must be enforced server-side.**
- **Latency to set expectations:** dispatch → runner start (~10–60 s) → run (~2–3 min) → Pages deploy (~30–60 s). New data appears ~3–5 min after the click. The control should show "Refresh requested — new data in ~4 min" and a "last refreshed" time, and then poll `health.json` for a new `run_id`/`generated_at` to update its status. A full page reload brings in the new data; auto-reload is optional.

### Options (present to the owner with a recommendation)
1. **Link-only (zero infrastructure, safest):** the control opens `https://github.com/wihogan/PEC/actions/workflows/refresh.yml`, where the owner taps **Run workflow**. Requires being signed in to GitHub as `wihogan` (works in the GitHub mobile app). Enforce the 5-minute minimum inside `refresh.yml` (below). No new secrets, no abuse surface. Downside: two taps plus GitHub sign-in.
2. **Cloudflare Worker proxy (one-tap, free tier):** the page POSTs to a Worker URL. The Worker holds the dispatch token as a Worker secret, enforces the 5-minute cooldown server-side (Workers KV or Durable Object: a single "last dispatch" timestamp), and dispatches `refresh.yml` with an input such as `reason=manual`. Because the page is public, add an access gate so strangers can't trigger runs: a short passphrase the owner enters once (stored in the browser's localStorage, checked by the Worker), or Cloudflare Access with the owner's email. Also limit CORS to the Pages origin. Verify current Workers free-tier limits (requests/day, KV writes/day, CPU) before committing.
3. **Other serverless proxy** (Vercel/Netlify/GCP function): same shape as option 2; only if the owner already uses one.

**Recommendation to validate:** option 2 if the owner wants one-tap from the dashboard; otherwise option 1. Either way, **also enforce the limit in the workflow** (defence in depth): in `refresh.yml`'s normalise step, for runs with `reason=manual`, look up the most recent production `[state]` run's `created_at` (`gh api`, `actions: read`) and skip (exit 0 with a notice, no alert) if it was < 5 minutes ago.
- A manual refresh should run the normal production path (`state_ref=state`, deploy) and fetch only what's due (intraday series during market hours; release-polled series follow their rules). It must **not** pass `force=true`.
- Concurrency: manual runs share the `pec-state-state` group with scheduled runs. GitHub keeps only one pending run per group (a newer pending run replaces the older pending one). That's harmless here, but document it, and make sure a queued manual request isn't reported as a failure.
- Page change: `PECtemplate.html` is the only UI file (vanilla JS, uPlot). Keep the editorial dark design (CSS tokens in `docs/system-reference.md` §12). Add a small control near the timestamp (`#generated-at`): a button with a cooldown countdown and a status line. No new libraries. Run the design check by viewing the rendered page from a runner artifact or a local render (`refresh_v2.render` on the baseline fixture), since `github.io` isn't reachable from the sandbox.

## 5. Rate-limit assessment (advise the owner before building B)

Give the owner a short, evidence-based assessment. Starting points (verify; don't treat as fact):

- **Yahoo Finance (14 series, via yfinance):** unofficial, undocumented limits. Community reports describe HTTP 429 / "Too Many Requests" and temporary blocks under heavy or bursty use, sometimes more often from cloud IPs. Our load per run is 14 history downloads. Scheduled cadence = 9 runs/day ≈ 126 downloads/day. Worst case with manual refreshes every 5 minutes through market hours (6.5 h × 12 = 78 extra runs) ≈ 1,100 more downloads/day, still modest in request count. **But every download currently pulls full history since 2010 (sp500 since 1999)**, which is wasteful and the main avoidable load. Recommend changing intraday fetches to a short window (e.g. `period="5d"`) merged into the stored history, with the full-history pull kept for post-close or weekly. That cuts bytes ~1000× and lowers block risk. The evidence so far is good: 11/11 cold full fetches from runners succeeded (docs/spike.md), but none of those were bursty. Add backoff and retry on 429 in `fetchers/yahoo.py`, and confirm carry-forward + healthcheck still behave if Yahoo throttles (staleness hard-fails after 7 days for daily series; a few hours of throttling just leaves older prices with no alert, so consider an intraday staleness warning).
- **FRED / Treasury / multpl / FactSet:** not fetched intraday under Amendment B except when due (FRED limit is 120 requests/min per key — irrelevant at this volume). Manual refreshes don't increase their load beyond the due-check rules. **Verify this in the code before claiming it**, since `--force` is never used for manual runs.
- **GitHub:** Actions minutes are free for public repos. Dispatch API rate limits (5,000 requests/h per token) are irrelevant. Pages deployments: check current GitHub Pages limits for Actions-based deployments (a soft limit on builds per hour has existed for branch builds; verify whether it applies to artifact deploys). Worst case is ~12 deploys/hour if the button is pressed every 5 minutes, so confirm it's within limits or raise the cooldown.
- **Cloudflare (if option 2):** free-tier request, KV-write and CPU limits are far above this use; verify current numbers.

Expected conclusion (to verify): with a 5-minute server-side cooldown and short-window intraday fetches, the rate-limit risk is low. The main residual risk is Yahoo's undocumented throttling of cloud IPs, mitigated by backoff, carry-forward and alerts.

## 6. Out of scope
Changing data sources without owner approval; UI redesign beyond the refresh control; multpl outage remediation (multpl.com was down for everyone from 2026-11-23, retried daily — see docs/plan.md Amendment B); moving compute off GitHub Actions.

## 7. Kickoff prompt (paste into the new session)

> Clone `wihogan/PEC` and read `docs/handoff-cronjob-and-refresh-button.md` in full, then `CLAUDE.md`, `docs/plan.md`, `docs/runbook.md` and `docs/qa.md`. You are the orchestrator for the work it describes: (A) make cron-job.org the refresh trigger and move the 16:30 watchdog into a GitHub workflow so nothing depends on my iMac, and (B) add a "Refresh now" control to the dashboard limited to once every 5 minutes, enforced server-side. First check whether branch `amendment-b` has merged to `main` and tell me. Before building B, give me the rate-limit assessment from §5 with your recommendation and the refresh-button option you recommend from §4. Follow plan → independent review → build → tests (never Sonnet for tests) → independent QA. Ask me only for the owner-only steps in §2 or a true blocker, with plain-language click-by-click instructions.
