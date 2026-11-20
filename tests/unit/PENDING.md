# Pending unit tests (Phase 2, code half)

These §6.1 cases target code that does not exist yet. Write them when the code lands.
Source of truth: `docs/plan.md` §2, §5, §6.1. Use the fixtures and helpers in `tests/conftest.py`
(`isolate_paths`, `freeze`, `la_tz`). Sockets stay disabled.

## 1. `PEC_EPS_OVERRIDE_ONLY=1` in `refresh_v2._fetch_valuation_inputs` (plan §2, F11, B5)

| # | Setup | Expected |
|---|---|---|
| E1 | `monkeypatch.setenv("PEC_EPS_OVERRIDE_ONLY", "1")`; stub `spglobal.fetch` that records calls and returns a live df `value=999.0` for point calls and a 30-row df for `history=True`; config override `354.84 / 2026-05-07` | point (non-history) `fetch` **never called**; record `{"value": 354.84, "effective_date": "2026-05-07", "source": "override"}`; `history` populated (history fetch is unaffected); `statuses["forward_eps_ntm"] == {"status": "ok"}` |
| E2 | as E1 but override `value: null` | `source == "unavailable"`, status `warning`; live point fetch still not called |
| E3 | env unset, or set to `"0"` / `""` | unchanged behaviour: live 999.0 wins, `source == "live"` (already covered for unset in `test_run.py::test_run_valuation_live_outranks_override`) |
| E4 | `run()` end-to-end with E1 setup (reuse `test_run.py` `env` fixture) | `data_v2.json` `valuation_inputs.forward_eps_ntm.source == "override"`, `ddm_fair_value == 4968` with 10y 4.00 |

## 2. `healthcheck.py` (plan §5)

Inputs: `data_v2.json`, `PEC.html`, `config/series_v2.yaml`. Exit 0/1; writes `health.json`.
Base "pass" fixture: `tests/fixtures/baseline_data_v2.json` with `tips_5yr` last date patched to the
frozen "today" (2026-11-20), rendered to `PEC.html` via `refresh_v2.render`. Clock frozen at 2026-11-20 PT.

| # | Mutation of the pass fixture | Expected |
|---|---|---|
| H0 | none | exit 0; `health.json` has no failures; warnings allowed |
| H1a | `data_v2.json` missing | exit 1 (rule 1) |
| H1b | `data_v2.json` not valid JSON | exit 1 (rule 1) |
| H2a | drop one of the 41 series ids | exit 1 (rule 2) |
| H2b | add an unknown id (e.g. `bogus`) | exit 1 (rule 2) |
| H2c | `fwd_pe_history` present (baseline has it) | allowed, exit 0 |
| H3a | `PEC.html` < 1.5 MB (render a tiny payload) | exit 1 (rule 3) |
| H3b | `__DASHBOARD_DATA__` left inside `<script id="pec-data">` | exit 1 |
| H3c | `__GENERATED_AT__` left in the page | exit 1 |
| H3d | `__DASHBOARD_DATA__` present **only inside the HTML comment** (template line ~1048) | not a failure by itself (rule 3 says "outside the comment") |
| H3e | script JSON parses but differs from `data_v2.json` (change one value) | exit 1 |
| H3f | script body not valid JSON | exit 1 |
| H4a | 4 `market_daily` series `status: error` with fresh `last_fetched` this run | exit 1 (> 3 failed market_daily) |
| H4b | exactly 3 failed `market_daily` | pass on rule 4 |
| H4c | all attempted FRED series failed, ≥ 3 attempted | exit 1 |
| H4d | all attempted FRED failed but only 2 attempted | pass on rule 4 |
| H4e | all attempted Yahoo series failed (≥ 3) | exit 1 |
| H4f | one Treasury (`yield_curve`) error but data fresh | pass (Treasury governed by staleness only) |
| H4g | all 5 multpl series errored | pass, warning only |
| H5a | a `market_daily` series last non-null obs 8 days old | exit 1 (threshold 7 d) |
| H5b | `eur_usd` (FRED FX) 13 d old | pass; 15 d → exit 1 (threshold 14 d) |
| H5c | `wti_crude` 15 d old | exit 1 (14 d) |
| H5d | `yield_curve` last date 8 d old | exit 1 (7 d) |
| H5e | raw baseline (`tips_5yr` 17 d old at 2026-11-20) | **exit 1** |
| H6a | `valuation_inputs.forward_eps_ntm.source == "live"` | exit 1 (rule 6) |
| H6b | `source == "override"` but value ≠ `series_v2.yaml` override | exit 1 |
| W1 | monthly series 111 d old | warning only, exit 0 |
| W2 | quarterly series 301 d old | warning only |
| W3 | `sp500_earnings` 221 d old | warning only |
| W4 | `fwd_pe_history` / EPS effective_date old | warning only |
| W5 | a single errored series otherwise within thresholds | warning only |

Note: which series count as "attempted this run" must be defined in `healthcheck.py`
(e.g. `last_fetched == metadata.last_refreshed` for ok, or status error); test that definition explicitly.

## 3. `tools/set_eps.py` (plan §2)

Operates on a tmp copy of `config/series_v2.yaml`; clock frozen at 2026-11-20.

| # | Args | Expected |
|---|---|---|
| S1 | `360 2026-11-20` | exit 0; only the `value:` and `effective_date:` lines under `valuation_inputs.forward_eps_ntm.manual_override` change (diff is exactly 2 lines); all comments and the rest of the file byte-identical; `_load_config` reads 360.0 / "2026-11-20" |
| S2 | `abc 2026-11-20` | non-zero exit; file unchanged |
| S3 | `199.99 ...` / `600.01 ...` | rejected (range 200–600); 200 and 600 accepted |
| S4 | `360 2026-11-21` (future) | rejected; file unchanged |
| S5 | `360 2026-13-01` / `360 11/20/2026` | rejected (ISO date only) |
| S6 | value written keeps a float literal (e.g. `360` → `360.0` or `360`, but loads as float in 200–600) so `test_config.py::test_valuation_input_override` still passes |

## 4. Not written here (per scope)

- Live multpl integration test (N10): `tests/test_fetchers.py` was only marked
  `integration` + `enable_socket` and had the Shiller test removed; a live
  `multpl.fetch("sp500_pe")` smoke test still needs to be added there.
