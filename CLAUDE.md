# Preston Economic Console (PEC) — Claude Code Context

## What this is

PEC is a Python-generated static HTML macro dashboard: ~35 financial series (equities, rates, credit,
macro, commodities, FX, valuation) fetched from public APIs and baked into one self-contained `PEC.html`.
No backend server.

## Status

The project is moving from a local Windows workflow to **GitHub Actions + GitHub Pages**
(migration in progress; see `docs/plan.md`). Do NOT use the old Windows copy-and-push publish process
described in older docs (`docs/system-reference.md`, `docs/notes/`) — those are historical.

## Layout

- `refresh_v2.py` — orchestrator: fetch, write `data_v2.json`, render `PEC.html`
- `fetchers/` — per-source modules (fred, yahoo, treasury, shiller, spglobal, multpl, factset_eps)
- `config/` — `series_v2.yaml` (series definitions), `forward_eps_seed.yaml`
- `PECtemplate.html` — HTML/JS template (`__DASHBOARD_DATA__` placeholder)
- `tests/` — pytest suite; `tests/fixtures/` holds the baseline data and saved multpl pages
- `docs/` — system reference, migration handoff, plan, plan reviews, design notes

## Rules

- NEVER run `refresh_v2.py --series` in automation: it triggers an interactive prompt.
- The only secret is `FRED_API_KEY`. It lives as a GitHub repo secret / environment variable.
  Never put it in any file, log, or commit.
- Persistent state (cache, data history, health) lives on the orphan `state` branch, not on `main`.
- Generated files (`PEC.html`, `data_v2.json`, `.cache/`, `health.json`) are git-ignored.
- Times are displayed in Pacific Time (PT).

## Local setup

    python3.12 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m pytest tests
