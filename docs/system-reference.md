# Preston Economic Console (PEC) — LLM Rebuild Handoff

> **Purpose of this document:** Give an LLM enough context to rebuild the PEC from scratch — architecture, data contracts, all series definitions, fetcher specs, template injection pattern, and operational procedures. Nothing is omitted or summarized away.

---

## 1. What the PEC Is

The **Preston Economic Console** is a Python-generated static HTML macro dashboard. It tracks ~35 financial data series across equities, rates, credit, macro, commodities, and FX.

**Key properties:**
- **Single-file output.** `refresh_v2.py` bakes all data into a single self-contained `PEC.html` — no backend server, no API calls at read time. Open in any browser via `file://`.
- **~4.6 MB output.** The inline JSON makes it large but fully portable.
- **uPlot charts.** The frontend uses [uPlot v1.6.31](https://github.com/leeoniya/uPlot) for all time-series charts — loaded from CDN. All other UI is vanilla JS + CSS.
- **Live URL:** `https://wihogan.github.io/PV-macro-dashboard2/` (GitHub Pages, `index.html` = copy of latest `PEC.html`)

**Owner:** Hogan (owner@example.com)  
**Time zone:** Pacific Time (PT). Always display times in PT.

---

## 2. Directory Structure

```
dashboard/                        ← project root
├── refresh_v2.py                 ← orchestrator: fetches all data, writes PEC.html
├── PECtemplate.html              ← HTML template; contains __DASHBOARD_DATA__ placeholder
├── PEC.html                      ← OUTPUT (regenerated each run, ~4.6 MB)
├── PEC_dev.html                  ← lightweight dev version (--dev flag), loads JSON at runtime
├── data_v2.json                  ← intermediate data file (also regenerated)
├── config/
│   ├── series_v2.yaml            ← master series registry
│   └── forward_eps_seed.yaml     ← seed data for NTM EPS history (2021–present)
├── fetchers/
│   ├── __init__.py
│   ├── base.py                   ← FetchError class + fetcher contract docs
│   ├── fred.py                   ← FRED API (requires FRED_API_KEY env var)
│   ├── yahoo.py                  ← Yahoo Finance via yfinance
│   ├── treasury.py               ← U.S. Treasury XML feed (no key needed)
│   ├── shiller.py                ← Yale ie_data.xls CAPE data (7-day cache)
│   ├── multpl.py                 ← multpl.com (S&P valuation series, 24-hr cache)
│   ├── spglobal.py               ← S&P Global forward EPS xlsx + history management
│   └── factset_eps.py            ← FactSet Earnings Insight PDF (fallback for EPS)
├── .cache/                       ← auto-created; holds all fetcher caches
│   ├── ie_data.xls               ← Shiller file (7-day TTL)
│   ├── sp500_eps_est.xlsx        ← S&P Global EPS file (7-day TTL)
│   ├── forward_eps_ntm.json      ← last successful EPS point-in-time parse
│   ├── forward_eps_ntm_history.json ← monthly EPS history array
│   ├── multpl_{series_id}.html   ← multpl.com HTML (24-hr TTL per series)
│   └── yahoo_info_*.json         ← yfinance info-field cache (24-hr TTL)
├── backups/                      ← auto-created; rolling 14-day backup of data_v2.json
├── .venv/                        ← Python virtual environment
└── _v1_archive/                  ← old V1 files (template.html, dashboard.html, etc.)
```

---

## 3. How to Run

### Full refresh
```bash
cd "<project-root>"
python refresh_v2.py
```

### Options
```bash
python refresh_v2.py --force        # bypass all frequency checks, re-fetch everything
python refresh_v2.py --dev          # also produce PEC_dev.html (async JSON load)
python refresh_v2.py --series sp500 fed_funds   # DANGER: overwrites data_v2.json with only these series
```

> **Warning:** `--series` prompts for confirmation because it wipes all other series from `data_v2.json`. Never use it after a failed full run — just re-run without `--series`.

### Publish to GitHub Pages
```bash
copy /Y "PEC.html" "GitHub\index.html"
cd GitHub
git add index.html
git commit -m "Update PEC dashboard"
git push origin main
```

---

## 4. Environment Requirements

| Requirement | Detail |
|-------------|--------|
| Python | 3.9+ |
| Required env var | `FRED_API_KEY` — Windows system env var. Free key at https://fred.stlouisfed.org/docs/api/api_key.html |
| pip packages | `yfinance fredapi pandas requests pyyaml tzdata beautifulsoup4 lxml openpyxl xlrd` |

### Virtual environment setup (if missing)
```bash
python -m venv .venv
.venv\Scripts\activate
pip install yfinance fredapi pandas requests pyyaml tzdata beautifulsoup4 lxml openpyxl xlrd
```

---

## 5. Data Pipeline Flow

```
series_v2.yaml
    ↓
refresh_v2.py: _load_config()
    ↓ iterates series list
_fetch_standard() or _fetch_yield_curve()
    ↓ calls fetcher module
[fred | yahoo | treasury | shiller | multpl].fetch()
    ↓
DataFrame (date, value) — always sorted, no NaN
    ↓
series_out dict
    ↓
_fetch_valuation_inputs()   ← spglobal.fetch() → forward EPS
    ↓
_compute_derived_valuation() ← forward P/E, DDM, delta strings, fwd_pe_history series
    ↓
output = {metadata, series, valuation_inputs}
    ↓
json.dump() → data_v2.json
    ↓
render(): PECtemplate.html + str.replace(__DASHBOARD_DATA__, raw_json) → PEC.html
```

**Frequency check:** Each series has a `refresh_frequency` field. On non-forced runs, the orchestrator checks `metadata.statuses[id].last_fetched` from the existing `data_v2.json` and skips series whose data is still current. Skipped series carry forward their existing data.

**Failure resilience:** If a fetcher raises, the orchestrator logs the error, carries forward the prior data for that series, and continues. The refresh never crashes due to a single series failure.

---

## 6. data_v2.json Schema

```json
{
  "metadata": {
    "last_refreshed": "2026-05-27T08:30:00",
    "series_count":   35,
    "success_count":  34,
    "failure_count":  1,
    "skip_count":     0,
    "statuses": {
      "sp500":     {"status": "ok", "last_fetched": "2026-05-27T08:30:00"},
      "cpi_yoy":   {"status": "error", "error": "...message...", "last_fetched": null}
    }
  },
  "series": {
    "sp500":     {"dates": ["2010-01-04", ...], "values": [1132.99, ...]},
    "yield_curve": {
      "dates": ["2010-01-04", ...],
      "tenors": {
        "1mo": [0.04, null, ...],
        "10yr": [3.85, 3.91, ...]
      }
    },
    "fwd_pe_history": {"dates": [...], "values": [...]},
    ...
  },
  "valuation_inputs": {
    "forward_eps_ntm": {
      "value": 354.84,
      "effective_date": "2026-05-07",
      "source": "override",
      "history": {"dates": [...], "values": [...]}
    },
    "forward_pe_ntm":    21.5,
    "erp":               3.5,
    "ddm_fair_value":    4820,
    "cape_delta_string": "+18% vs 5yr avg (29.3)",
    "fwdpe_delta_string": "+12% vs 5yr avg (20.1×)"
  }
}
```

**Key structural notes:**
- `yield_curve` is the ONLY series with the nested `tenors` structure. All others use flat `{dates, values}`.
- `fwd_pe_history` lives under `series` (not `valuation_inputs`) because it's a time series.
- `valuation_inputs` contains a mix: `forward_eps_ntm` is a dict with `{value, effective_date, source, history}`; the others (`forward_pe_ntm`, `erp`, `ddm_fair_value`, delta strings) are raw scalars or strings.
- `source` on `forward_eps_ntm` is `"live"`, `"override"`, or `"unavailable"`.
- `values` arrays may contain `null` for missing observations (e.g. weekends in daily series). The frontend must handle nulls.
- All floats are rounded to 4 decimal places by `_safe_float()`.

---

## 7. All Series Definitions

### 7.1 Equities (source: yahoo or multpl)

| id | source | series_id | label | frequency | kpi |
|----|--------|-----------|-------|-----------|-----|
| sp500 | yahoo | ^GSPC | S&P 500 | market_daily | ✓ |
| dow_jones | yahoo | ^DJI | Dow Jones | market_daily | ✓ |
| nasdaq | yahoo | ^IXIC | NASDAQ Composite | market_daily | ✓ |
| developed_ex_us | yahoo | VEA | Developed ex-US (VEA) | market_daily | |
| emerging_markets | yahoo | VWO | Emerging Markets (VWO) | market_daily | |
| russell_2000 | yahoo | ^RUT | Russell 2000 | market_daily | |
| developed_ex_us_efa | yahoo | EFA | Developed ex-US (EFA) | market_daily | |
| emerging_markets_eem | yahoo | EEM | Emerging Markets (EEM) | market_daily | |
| sp500_dividend_yield | shiller | dividend_yield | S&P 500 Dividend Yield | daily_after_close | ✓ |
| sp500_pe | shiller | pe | S&P 500 Trailing P/E | daily_after_close | |
| sp500_earnings_yield | shiller | earnings_yield | S&P 500 Earnings Yield | daily_after_close | |
| sp500_earnings | shiller | real_earnings | S&P 500 TTM Earnings (real, latest-month dollars) | daily_after_close | |
| sp500_earnings_nominal | shiller | nominal_earnings | S&P 500 TTM Earnings (nominal) - not shown in UI | daily_after_close | |
| shiller_cape | shiller | cape | Shiller CAPE | daily_after_close | ✓ |

**Shiller note (2026-11-24, plan.md Amendment C; replaces multpl):** these series come from `fetchers/shiller.py` (shillerdata.com `ie_data.xls`, discovered from the landing page). dividend_yield/pe/earnings_yield/cape keep the frozen multpl archive (`config/multpl_archive.json`) through 2026-10 and append Shiller-derived months; the earnings series are rebuilt fully from Shiller. Latest months are provisional and each series carries `provisional_from`. `fetchers/multpl.py` is unused. The older text below describing multpl/Yale is historical.

**multpl note (historical):** Percentage series (`sp500_div_yield`, `sp500_earnings_yield`) are stored as decimals (e.g. 0.0107 = 1.07%). The render layer multiplies by 100 for display. The `sp500_pe`, `sp500_earnings`, and `shiller_pe` series are stored as-is (ratios/dollars).

**sp500 start_date:** Extended to `1999-01-01` (vs 2010 for others) to support the "Max" range selector. `sp500_pe` and `shiller_cape` use `1980-01-01`.

### 7.2 Rates (source: fred)

| id | FRED series | label | frequency | kpi |
|----|-------------|-------|-----------|-----|
| fed_funds | DFF | Fed Funds Rate | market_daily | ✓ |
| treasury_1mo | DGS1MO | 1-Month Treasury | market_daily | ✓ |
| treasury_2yr | DGS2 | 2-Year Treasury | market_daily | ✓ |
| treasury_10yr | DGS10 | 10-Year Treasury | market_daily | ✓ |
| tips_5yr | DFII5 | 5-Year TIPS | market_daily | |
| tips_7yr | DFII7 | 7-Year TIPS | market_daily | |
| tips_10yr | DFII10 | 10-Year TIPS | market_daily | |
| tips_20yr | DFII20 | 20-Year TIPS | market_daily | |
| tips_30yr | DFII30 | 30-Year TIPS | market_daily | |

**No DFII2 (2yr TIPS):** Treasury discontinued it. Section 2 real yields chart uses `tips_5yr` and `tips_10yr` as anchors instead.

### 7.3 Yield Curve (source: treasury)

| id | series_id | label | frequency |
|----|-----------|-------|-----------|
| yield_curve | daily_treasury_yield_curve | Treasury Yield Curve | daily_once |

Returns 13 tenors: `1mo 2mo 3mo 4mo 6mo 1yr 2yr 3yr 5yr 7yr 10yr 20yr 30yr`. Uses `daily_once` because the 2-3 min fetch doesn't justify intraday re-runs.

### 7.4 Credit (source: fred)

| id | FRED series | label | frequency | kpi | note |
|----|-------------|-------|-----------|-----|------|
| hy_yield | BAMLH0A0HYM2EY | HY Effective Yield | market_daily | ✓ | ICE BofA — attribution required externally |
| aaa_yield | BAMLC0A1CAAAEY | AAA Effective Yield | market_daily | | ICE BofA — attribution required externally |

### 7.5 Macro (source: fred)

| id | FRED series | label | frequency | kpi | notes |
|----|-------------|-------|-----------|-----|-------|
| cpi_yoy | CPIAUCSL | CPI (YoY %) | monthly | ✓ | fetched raw; `yoy_pct: true` → `pct_change_12m` transform applied; start_date 2005-01-01 for transform headroom |
| gdp_growth | A191RL1Q225SBEA | Real GDP Growth (QoQ Ann.) | quarterly | ✓ | |
| consumer_delinquency | DRCLACBS | Consumer Loan Delinquency Rate | quarterly | | |
| auto_loan_delinquency | DRALACBS | Auto Loan Delinquency Rate | quarterly | | |
| credit_card_delinquency | DRCCLACBS | Credit Card Delinquency Rate | quarterly | | |
| nber_recession | USREC | NBER Recession Indicator | monthly | | Binary 0/1. Render layer converts to chart shading bands. |

### 7.6 Commodities (source: fred or yahoo)

| id | source | series_id | label | frequency | kpi |
|----|--------|-----------|-------|-----------|-----|
| wti_crude | fred | DCOILWTICO | WTI Crude Oil ($/bbl) | market_daily | ✓ |
| gold_spot | yahoo | GC=F | Gold ($/oz) | market_daily | ✓ |
| silver_spot | yahoo | SI=F | Silver ($/oz) | market_daily | |
| platinum_spot | yahoo | PL=F | Platinum ($/oz) | market_daily | |

Yahoo commodities are COMEX/NYMEX front-month futures (cleanest free spot proxy). `GC=F`, `SI=F`, `PL=F`.

### 7.7 FX & Crypto (source: fred or yahoo)

| id | source | series_id | label | frequency | kpi |
|----|--------|-----------|-------|-----------|-----|
| eur_usd | fred | DEXUSEU | EUR/USD | market_daily | ✓ |
| cad_usd | fred | DEXCAUS | CAD/USD | market_daily | |
| jpy_usd | fred | DEXJPUS | JPY/USD | market_daily | |
| btc_usd | yahoo | BTC-USD | Bitcoin (USD) | market_daily | |
| eth_usd | yahoo | ETH-USD | Ethereum (USD) | market_daily | |
| xrp_usd | yahoo | XRP-USD | XRP (USD) | market_daily | |

FRED FX series are quoted as foreign currency units per USD (CAD/USD = CAD per 1 USD). Bitcoin includes weekends (yfinance returns 7-day data for crypto).

---

## 8. Refresh Frequency Logic

Four frequency modes, checked per-series on each non-`--force` run:

| Frequency | Skip condition |
|-----------|----------------|
| `market_daily` | If market is open (9:30–16:00 ET Mon–Fri): always fetch. If market is closed: skip if `last_fetched.date >= last_completed_business_day` AND `last_fetched` occurred after 16:00 ET on that day. |
| `daily_once` | Skip if `last_fetched.date >= last_completed_business_day`. Doesn't care about intraday timing. |
| `monthly` | Skip if `(last_fetched.year, last_fetched.month) == (now.year, now.month)`. |
| `quarterly` | Skip if `last_fetched` and `now` are in the same calendar quarter. |

`_last_completed_business_day()` returns the most recent weekday on which the market has already closed (ET). Today counts only after 16:00 ET.

---

## 9. Fetcher Specifications

### Fetcher Contract (base.py)

Every fetcher exposes `fetch(series_id, start_date, end_date=None) → pd.DataFrame`.

Return contract:
- Exactly two columns: `date` (datetime64[ns], timezone-naive) and `value` (float64)
- Sorted ascending by date
- No NaN rows (drop before returning)

Raise `FetchError` (from `fetchers/base.py`) for any failure. Always chain original exception: `raise FetchError("...") from exc`.

---

### fred.py

- **Source:** FRED API via `fredapi` library
- **Auth:** `FRED_API_KEY` environment variable (required)
- **Extra params:** `frequency` (resampling: D/W/M/Q/A), `transform` (pct_change_12m / pct_change_1m / pct_change_1q)
- **`yoy_pct: true` in YAML** → orchestrator passes `transform="pct_change_12m"` to `fred.fetch()`
- Values are percentages after transform (already × 100)

---

### yahoo.py

- **Source:** Yahoo Finance via `yfinance`
- **Auth:** None
- **Price-history path** (default): `yf.download(ticker, auto_adjust=True, progress=False, multi_level_index=False)` → uses `Close` column
- **Info-field path** (`field=` kwarg): `yf.Ticker(series_id).info[field]` — rate-limited, 24-hr file cache in `.cache/yahoo_info_<ticker>_<field>.json`. Returns a ONE-ROW DataFrame (today's date, scalar value).
- MultiIndex column handling: `_extract_close()` handles both flat and MultiIndex column structures from yfinance (API sometimes returns one or the other depending on version)
- `sp500_dividend_yield` is NOT currently fetched via this path — it uses `multpl` source

---

### treasury.py

- **Source:** U.S. Treasury XML feed — `https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value={year}`
- **Auth:** None (public)
- **One HTTP call per calendar year** (fetches full year at once)
- **Returns LONG FORM** (3 columns: date, tenor, value) — the only fetcher that deviates from the 2-column contract
- Orchestrator handles this specially: calls `_fetch_yield_curve()` instead of `_fetch_standard()`, then pivots to `{dates, tenors: {tenor: [values]}}`
- BC_ field to tenor mapping: `BC_1MONTH→1mo`, `BC_2MONTH→2mo`, `BC_3MONTH→3mo`, `BC_4MONTH→4mo`, `BC_6MONTH→6mo`, `BC_1YEAR→1yr`, `BC_2YEAR→2yr`, `BC_3YEAR→3yr`, `BC_5YEAR→5yr`, `BC_7YEAR→7yr`, `BC_10YEAR→10yr`, `BC_20YEAR→20yr`, `BC_30YEAR→30yr`
- Canonical tenor order: `1mo 2mo 3mo 4mo 6mo 1yr 2yr 3yr 5yr 7yr 10yr 20yr 30yr`
- Missing tenors on a given date → `null` in the pivoted array

---

### shiller.py

**UPDATE 2026-11-24:** rewritten - now the source for all six valuation series above (see Amendment C in docs/plan.md and the module header). The paragraph below is historical.

**NOTE: As of May 2026, this fetcher is superseded by `multpl.py` for Shiller CAPE.** The `shiller_cape` series now uses `source: multpl` and `series_id: shiller_pe`. The `shiller.py` file still exists but is not called by the current `series_v2.yaml`. Retained in case the multpl source fails.

- **Source:** Yale ie_data.xls — `http://www.econ.yale.edu/~shiller/data/ie_data.xls`
- **Auth:** None; uses spoofed User-Agent header
- **Cache:** `.cache/ie_data.xls`, 7-day TTL
- **Format:** Legacy Excel 97 .xls (not .xlsx). Requires `xlrd` — `openpyxl` cannot read it.
- **Header row:** Auto-detected (not hardcoded) — scans first 15 rows for a row where col[0] is "Date" AND a CAPE column label exists. This is robust against Shiller adding preamble rows.
- **Date encoding:** Decimal year (e.g. 2024.01 = Jan 2024)
- Only series_id supported: `'cape'`

---

### multpl.py

- **Source:** `https://www.multpl.com/{slug}/table/by-month`
- **Auth:** None; uses spoofed User-Agent header
- **Cache:** `.cache/multpl_{series_id}.html`, 24-hr TTL per series
- **Parse target:** `<table id="datatable">` — rows are `(date_td, value_td)` in `"Jan 1, 2026"` format
- **`†` (U+2020):** Estimate marker for current partial month — stripped, value used normally
- **Fallback:** If live download fails but stale cache exists, uses stale cache (logged as warning)

**Series ID → slug mapping:**
```
sp500_div_yield      → s-p-500-dividend-yield   (is_percent=True → stored as decimal)
sp500_pe             → s-p-500-pe-ratio          (is_percent=False)
sp500_earnings_yield → s-p-500-earnings-yield    (is_percent=True → stored as decimal)
sp500_earnings       → s-p-500-earnings          (is_percent=False)
shiller_pe           → shiller-pe                (is_percent=False)
```

---

### spglobal.py

- **Purpose:** NTM (next-twelve-months) forward operating EPS for the S&P 500
- **Configured as `valuation_inputs.forward_eps_ntm`** in series_v2.yaml — not a charted time series

**Three-path fallback chain:**
1. **S&P Global xlsx:** `https://www.spglobal.com/spdji/en/documents/additional-material/sp-500-eps-est.xlsx` — often 403'd by CDN; kept for future use. 7-day xlsx cache at `.cache/sp500_eps_est.xlsx`. Parses by: finding header row (≥2 quarter-pattern columns in first 25 rows), mapping quarter columns to dates, finding "operating eps" row, summing next 4 future-quarter estimates.
2. **FactSet / Yardeni** (`factset_eps.py`): fetches FactSet Earnings Insight PDF or Yardeni chart page. 7-day cache at `.cache/factset_eps_latest.json`.
3. **JSON cache:** `.cache/forward_eps_ntm.json` — last successful parse from any source.
4. If all fail → raises `FetchError` → orchestrator uses `manual_override` from series_v2.yaml.

**History management (important):**
- `.cache/forward_eps_ntm_history.json` stores a monthly array of `{date, value}` entries
- On each successful fetch, `_ensure_history()` is called: loads history, bootstraps from `config/forward_eps_seed.yaml` if empty, appends current month (idempotent), saves back
- Also writes back to `forward_eps_seed.yaml` when a new monthly entry is added (so the seed stays current if cache is deleted)
- `fetch(series_id, history=True)` returns the full monthly history as a multi-row DataFrame — called by the orchestrator to build the `fwd_pe_history` derived series

**Manual override workflow (current primary path):**
Since the S&P xlsx CDN often 403s, the `manual_override` in series_v2.yaml is the de-facto weekly data source:
```yaml
valuation_inputs:
  forward_eps_ntm:
    manual_override:
      value: 354.84
      effective_date: "2026-05-07"
```
Update this weekly from Yardeni Research (52-Wk Forward EPS chart) or FactSet Earnings Insight PDF.

---

### factset_eps.py

- **Purpose:** Fallback EPS source; called from `spglobal.py`
- **Sources:** FactSet Earnings Insight PDF (`https://www.factset.com/earningsinsight`) + Yardeni chart page sanity check
- **Cache:** `.cache/factset_eps_latest.json`, 7-day TTL
- Returns `{"value": float, "as_of": "YYYY-MM-DD", "source": "factset"|"yardeni"}` or `None`

---

## 10. Derived Valuation Calculations

`_compute_derived_valuation(vi_out, series_out)` runs after all fetches and mutates `vi_out` in-place:

| Output key | Formula | Notes |
|------------|---------|-------|
| `forward_pe_ntm` | `sp500_last / forward_eps_ntm.value` | Uses last non-null value from `sp500` series |
| `erp` | `3.5` (constant) | Default ERP for DDM slider |
| `ddm_fair_value` | `(EPS × 0.35) / ((10yr + 3.5 − 5.0) / 100)` | Gordon DDM; payout=35%, g=5% nominal, ERP=3.5%; skips if denom ≤ 0 |
| `cape_delta_string` | Current CAPE vs trailing 60-month avg | e.g. `"+18% vs 5yr avg (29.3)"` |
| `fwdpe_delta_string` | Current fwd P/E vs full fwd_pe_history avg | e.g. `"+12% vs 5yr avg (20.1×)"` |

**`fwd_pe_history` series** (lives in `series_out`, not `valuation_inputs`):
Built by joining the EPS history (`forward_eps_ntm.history`) with monthly S&P 500 closes. For each EPS history month, finds the last S&P close in that month from the `sp500` series. Returns `{dates, values}` of monthly Fwd P/E ratios. Powers the second line on the CAPE & Trailing P/E chart.

---

## 11. Template Injection Pattern

`PECtemplate.html` contains two string-replace placeholders:

```html
<!-- 1. Data injection — refresh_v2.py replaces __DASHBOARD_DATA__ with raw JSON -->
<script id="pec-data" type="application/json">__DASHBOARD_DATA__</script>

<!-- 2. Timestamp injection -->
<span class="dash-ts" id="generated-at">__GENERATED_AT__</span>
```

`render()` in refresh_v2.py:
```python
raw_json = json.dumps(output, separators=(",", ":"), ensure_ascii=False)
html = template.replace("__DASHBOARD_DATA__", raw_json)
html = html.replace("__GENERATED_AT__", datetime.now().strftime("%Y-%m-%d %H:%M"))
```

**JS init pattern:**
```javascript
window.DASHBOARD_DATA = null;
window.__uplotInstances = [];

function __initPEC() {
  const el = document.getElementById('pec-data');
  const raw = el ? el.textContent.trim() : '';
  if (!raw || raw[0] !== '{') { /* show error */ return; }
  window.DASHBOARD_DATA = JSON.parse(raw);
  // ... populate tiles, then on window 'load' event: _renderAll(); _wireControls();
}
__initPEC();  // called synchronously; charts wired on window.load
```

Dev mode (`--dev`): replaces `__DASHBOARD_DATA__` with `{}` and replaces the sync `__initPEC()` call with an async `fetch('data_v2.json').then(...)` loader. Requires a local HTTP server (file:// blocks fetch()). Produces ~50 KB vs ~4.6 MB.

---

## 12. Template HTML Structure

### Design System (CSS tokens)
```css
:root {
  --bg:        #131109;       /* page background — very dark warm black */
  --bg-panel:  #1b1912;       /* panel backgrounds */
  --bg-tile:   #201e17;       /* KPI tile backgrounds */
  --bg-chart:  #171510;       /* chart canvas background */
  --bd-d:      #4a4536;       /* dashed borders */
  --bd-s:      #6b6450;       /* solid borders */
  --ink-1:     #dcd2bb;       /* primary text — warm off-white */
  --ink-2:     #9e9382;       /* secondary text */
  --ink-3:     #6d6352;       /* muted text / tags */
  --ac-cool:   oklch(0.74 0.09 240);  /* active/selected — steel blue */
  --ac-warm:   oklch(0.76 0.09 55);   /* warm accent */
  --pos:       oklch(0.72 0.12 145);  /* green — positive change */
  --neg:       oklch(0.65 0.14 22);   /* red — negative change */
  --f-ui:      'Inter', system-ui, sans-serif;
  --f-serif:   'Source Serif 4', Georgia, serif;
  --f-mono:    'JetBrains Mono', ui-monospace, monospace;
}
```

Aesthetic: **editorial dark console** — warm dark background (not pure black), serif italic headings, monospace data values, grid layout with dashed borders.

### Fonts (Google Fonts CDN)
- `Inter` (400, 500) — UI labels, controls
- `Source Serif 4` italic (400, 600) — section numbers, panel titles, dashboard title
- `JetBrains Mono` (400, 500) — all numeric values, timestamps, tags

### Charts
- **uPlot v1.6.31** loaded from `https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.iife.min.js`
- All chart instances stored in `window.__uplotInstances` for resize handling
- NBER recession periods rendered as shaded bands across all applicable charts

### Dashboard Layout (5 sections × 230px height each)

```
Section 1: Equities
  Grid: 150px | 220px | 1fr | 1fr
  └── sec-label | index-strip (S&P/Dow/NASDAQ/Russell KPIs + tabs) | main chart (S&P with range tabs) | int'l chart

Section 2: Rates
  Grid: 150px | 220px | 1fr
  └── sec-label | rate-strip (Fed Funds, 2yr, 10yr KPIs) | multi-line rates chart (or yield curve)

Section 3: Commodities & FX
  Grid: 150px | 1fr | 1fr
  └── sec-label | commodities chart (WTI, Gold) | FX/crypto chart

Section 4: Macro & Credit
  Grid: 150px | 1fr | 1fr | 1fr
  └── sec-label | CPI/GDP chart | credit spreads chart | delinquency chart

Section 5: Valuation
  Grid: 150px | 1fr | 1fr
  └── sec-label | CAPE & Fwd P/E chart (dual-line) | Wien DDM panel (slider-driven fair value)
```

### JS Helper Functions (inside `__initPEC`)

```javascript
S(id)         // → series object {dates, values} or null
nonNull(id)   // → values array with nulls removed
last(id)      // → last non-null value
prev(id)      // → second-to-last non-null value
fmt(v, dec, pre, suf)  // → formatted string, handles commas for ≥1000
chgPct(id)    // → (last-prev)/prev * 100
bps(id)       // → (last-prev) * 100, rounded to int (basis points)
set(id, txt, cls)  // → sets textContent of element, optionally adds pos/neg class
fmtPct(v, sign)    // → "+2.34%" or "2.34%"
fmtBp(v)           // → "+25bp"
```

Three top-level render functions (called on `window.load`):
- `_renderAll()` — builds all uPlot charts
- `_wireControls()` — wires up tab/chip buttons (range selectors, series toggles)
- `_watchResize()` — ResizeObserver for chart reflow

---

## 13. Forward EPS Seed Data

The full monthly history (Jan 2021 – Apr 2026) is stored in `config/forward_eps_seed.yaml`. This file is the source of truth when `.cache/forward_eps_ntm_history.json` is absent. Key anchors:

- Apr 23 2026: $344.30 (Yardeni)
- Apr 30 2026: $346.19 (Yardeni 52-Wk Fwd EPS)
- Apr 30 2026: $345.14 (value used in seed — FactSet-consistent)
- Current manual_override: **$354.84** as of 2026-05-07

**Update procedure (weekly, Friday after close):**
1. Check Yardeni Research: https://www.yardeni.com/charts/us-stock-market/stock-market-forward-metrics/sp-500-forward-revenues-earnings-margins
2. Cross-check FactSet Earnings Insight PDF: https://www.factset.com/earningsinsight (page 32+, use NTM operating EPS — NOT CY GAAP which is ~20-30% lower)
3. Edit `series_v2.yaml` → `valuation_inputs.forward_eps_ntm.manual_override`

---

## 14. Common Errors & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `FRED_API_KEY not found` | Env var not set | Set as Windows system env var; restart shell |
| `ModuleNotFoundError` | Missing package | `pip install <package>` in venv |
| FRED series empty after filtering | FRED rate limit | Wait 60s, retry |
| `nber_recession` or `jpy_usd` 500 error | FRED transient 500 | Retry; prior data carries forward |
| `PEC.html` shows blank / placeholder text | JSON not injected | Check `data_v2.json` exists and is valid JSON; verify `__DASHBOARD_DATA__` was replaced |
| Charts blank on file:// open | uPlot CDN not loaded | Open via HTTP server or wait for CDN load |
| multpl parse fails | Site structure changed | Delete `.cache/multpl_{id}.html`, inspect fresh HTML, update `_parse_html()` |
| Shiller CAPE header detection fails | Shiller restructured xlsx | Delete `.cache/ie_data.xls`, re-download, update `_detect_header_row()` or use multpl source instead |
| S&P EPS xlsx 403 | CDN blocks automated access | Expected; falls through to factset_eps → manual_override |

---

## 15. Key Design Decisions

1. **Static HTML over server.** PEC.html is fully self-contained because Hogan needs to email/share it and view it on a plane. No live backend required.

2. **Frequency checks avoid thrashing APIs.** The `refresh_frequency` + `_needs_refresh()` pattern means running `refresh_v2.py` many times a day is safe — daily/monthly/quarterly series won't re-fetch unnecessarily.

3. **Carry-forward on failure.** Any fetcher failure leaves the last known good data in the output rather than wiping it. Dashboard is always fully populated even if some series are stale.

4. **`--series` is dangerous, always warns.** It overwrites `data_v2.json` with only the specified series. Interactive confirmation required. Never use to retry a failed series — run full refresh instead.

5. **Backups before every write.** `_backup_data_json()` copies the current `data_v2.json` to `backups/data_{timestamp}.json` before overwriting. 14-day retention, auto-pruned.

6. **multpl replaces Yale ie_data for CAPE.** As of V2, the Shiller ie_data.xls was stale after Sep 2023. multpl.com is now the source for all Shiller/S&P valuation series because it's current.

7. **EPS history seed + accumulation.** The monthly EPS history is critical for the Fwd P/E chart. Since there's no live historical API, the history is seeded from the YAML file (manually curated back to 2021) and extended month-by-month automatically on each refresh. The seed file is updated in-place so cache loss doesn't lose history.

8. **`fwd_pe_history` lives in `series`, not `valuation_inputs`.** This is deliberate — it's a time series (monthly array), not a scalar, so it uses the same `{dates, values}` contract as all other series.

9. **DDM `erp` is hardcoded at 3.5%.** This is the slider initial position. The browser UI allows the user to drag a slider to adjust ERP and see the DDM fair value update in real time (client-side JS only — no server round-trip).

10. **`type="application/json"` on the data script tag.** This prevents the browser from executing the 4.6 MB JSON blob as JavaScript. The JS reads it via `document.getElementById('pec-data').textContent`.

---

## 16. GitHub Pages Publishing

- **Repo:** `https://github.com/wihogan/PV-macro-dashboard2`
- **Branch:** `main`
- **Served file:** `index.html` (= copy of `PEC.html`)
- **Live URL:** `https://wihogan.github.io/PV-macro-dashboard2/`

The GitHub folder is separate from the dashboard project root. Publishing workflow:
```bash
copy /Y "<project-root>\PEC.html" "GitHub\index.html"
cd GitHub
git add index.html
git commit -m "Update PEC dashboard"
git push origin main
```

Full publish guide with error handling: `.claude/commands/update-dashboard.md` (in dashboard directory).

---

## 17. V2 Migration Notes

The PEC was migrated from "Preston Macro Dashboard V1" on May 6, 2026. V1 artifacts (`template.html`, `dashboard.html`, `data.json`, `refresh.py`, `config/series.yaml`) are in `_v1_archive/` and should be deleted after June 6, 2026.

V2 renames: `series.yaml → series_v2.yaml`, `data.json → data_v2.json`, `refresh.py → refresh_v2.py`. The `--series` filter in V2 has an interactive confirmation guard not present in V1.

---

*Generated: 2026-05-27. Reflects current codebase state at project root `<project-root>`.*
