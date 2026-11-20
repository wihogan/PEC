# Preston Economic Console (V2)

A Python-generated static HTML macro dashboard. Tracks financial series across equities, rates, credit, macro, commodities, FX, and valuation. Data is fetched from live APIs, baked into a single self-contained `PEC.html` file, and viewed in the browser — no backend server required.

## Architecture

```
config/series_v2.yaml       ← series definitions
fetchers/                   ← per-source fetch modules (fred, yahoo, treasury, shiller, spglobal, multpl)
refresh_v2.py               ← orchestrator: fetches data, writes data_v2.json, renders PEC.html
PECtemplate.html            ← HTML/JS template with __DASHBOARD_DATA__ placeholder
PEC.html                    ← OUTPUT: self-contained dashboard (regenerated on each run)
data_v2.json                ← intermediate data file (also regenerated)
```

## How to update

> **Migration in progress.** PEC is moving to GitHub Actions + Pages; see `docs/plan.md`.
> The old Windows copy-and-push publish flow is retired.

Local run (any OS):

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
FRED_API_KEY=... .venv/bin/python refresh_v2.py
```

## Requirements

- Python 3.12 with a `.venv` virtual environment
- `FRED_API_KEY` set as an environment variable (never commit it)
- Dependencies: see `requirements.txt`

## Project context

See `CLAUDE.md` for full context, data sources, valuation inputs, and common troubleshooting.

## Version history

V1 files (`refresh.py`, `dashboard.html`, `template.html`, `data.json`, `config/series.yaml`) are archived in `_v1_archive/`. V2 migration completed May 6, 2026.
