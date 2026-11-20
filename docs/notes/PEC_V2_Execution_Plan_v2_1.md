# Preston Economic Console (PEC) V2 — Execution Plan (Revised)

*Migration from "Preston Macro Dashboard" (V1, `template.html`) to "Preston Economic Console" (V2, `PECtemplate.html`) using the editorial dark-console wireframe in `preston_economic_console_template2_final.html`.*

**Working directory:** `<project-root>`
**Plan version:** 2 (May 6, 2026) — incorporates resolved answers to all eight open questions from v1.
**Time estimate:** 6–9 hours, end-to-end, broken into four passes that each end at a verifiable checkpoint.

---

## Resolved policy decisions

These are answered and feed forward into the Pass B and Pass C work blocks:

1. **Wien DDM formula:** Single-stage Gordon. Constants: payout 35%, long-run nominal growth 5%, ERP slider default 3.5%.
2. **CAPE / Fwd P/E sub-labels:** Live computed strings, both vs. 5-year average. Format `"+8% vs 5yr avg (24.3)"`.
3. **shared-drive cutover:** Send a new shared link. `PEC.html` keeps its name; V1 `dashboard.html` archives untouched.
4. **DDM historical chart:** Ships in V2.0. Uses `forward_eps_ntm_history` cache (64 monthly entries) + daily `sp500` and `treasury_10yr` to backfill historical fair-value points.
5. **Recession bands:** NBER `USREC` series drives gray vertical bands on every applicable time-series chart. One footnote in page footer: *"Gray bands · NBER recessions."*
6. **GFC + COVID markers:** Same treatment as recession bands but distinct color tint (e.g., warm-gold for GFC band 2007Q4–2009Q2, cool-cyan for COVID band 2020-02 to 2020-04). Single combined footnote: *"Highlighted periods · GFC (2007–2009), COVID (2020)."*
7. **Per-chart curated annotations beyond GFC/COVID:** None in V2.0. The per-chart curation cost is high, recipients can read the bands instead, and the dashboard stays Tufte-clean. Defer to V2.1 if a specific chart genuinely needs a callout.

The combined effect is that Pass C's annotation work is bounded: build a recession-band overlay function, build a GFC/COVID overlay function, apply both to charts where dates are visible. No per-chart label curation file to maintain.

---

## 1. Headline framing

The wireframe author did most of the hard work on the front end already. `preston_economic_console_template2_final.html` preserves the two injection points that `refresh.py` cares about (`__DASHBOARD_DATA__` and `__GENERATED_AT__`) and reuses every series ID that already exists in `config/series.yaml`. **V2 is not a rewrite of the pipeline — it is a rename, a small set of additions to the data layer, and a chart-rendering pass to replace the placeholder text with real uPlot charts.**

The work splits cleanly into four passes:

1. **Pass A — Parallel V2 file tree.** Duplicate the V1 entry-point files alongside the existing ones with `_v2` / `PEC` naming. No code changes. Goal: V1 keeps working unchanged while V2 is built and tested in isolation.
2. **Pass B — Backend extensions.** Add the derived `valuation_inputs` keys: `forward_pe_ntm`, `ddm_fair_value`, `erp`, `cape_delta_string`, `fwdpe_delta_string`. Confirm `sp500_dividend_yield`, `forward_eps_ntm`, the EPS history cache, and `usrec` are all in `data_v2.json`.
3. **Pass C — Wire chart placeholders to live data.** Port the uPlot rendering logic from V1's `template.html` to the new panel anatomy, build the new charts the V1 template didn't have (yield-curve scrubber, real-yields curve, FX/crypto indexed, commodities indexed, DDM dual-axis), and overlay recession + GFC + COVID bands.
4. **Pass D — Cutover and decommission.** Send a new shared-drive link to recipients. Update CLAUDE.md. Archive V1.

Each pass has its own verification checklist. **Do not advance past a pass if the checklist fails — fix it first.**

---

## 2. What changes vs. what stays

### Stays exactly as-is

The data fetchers (`fetchers/fred.py`, `yahoo.py`, `treasury.py`, `shiller.py`, `spglobal.py`) are not touched. The fetcher package is shared between V1 and V2 — both orchestrators import from the same `fetchers/` directory.

The `data.json` schema's three top-level blocks — `metadata`, `series`, `valuation_inputs` — survive. The new template reads the same shape.

The render mechanic — orchestrator reads template, replaces `__DASHBOARD_DATA__` with base64-encoded JSON, replaces `__GENERATED_AT__` with timestamp, writes output HTML — is preserved bit-for-bit.

### Renames (V1 → V2)

| V1 (current) | V2 (new) |
|---|---|
| `template.html` | `PECtemplate.html` |
| `dashboard.html` (output) | `PEC.html` (output) |
| `data.json` | `data_v2.json` |
| `refresh.py` | `refresh_v2.py` |
| `config/series.yaml` | `config/series_v2.yaml` |
| Browser tab title "Preston Macro Dashboard" | "Preston Economic Console" |

The folder structure stays single-flat at `dashboard/`. We do **not** create a `v2/` subfolder — that would force duplicating the `fetchers/` package.

### New additions to `valuation_inputs` (Pass B)

Five new keys, all computed (not fetched) by `refresh_v2.py`:

| Key | Type | Computation |
|---|---|---|
| `forward_pe_ntm` | number | `sp500_last / forward_eps_ntm` |
| `ddm_fair_value` | number | `(forward_eps_ntm × 0.35) / ((treasury_10yr_last + erp − 5.0) / 100)` |
| `erp` | number | 3.5 (slider default; client-side adjustable) |
| `cape_delta_string` | string | Live delta vs. trailing 5-year average, formatted |
| `fwdpe_delta_string` | string | Live delta vs. trailing 5-year average, formatted |

Constants used:
- **Payout ratio: 35%** (S&P 500 net-payout average over the past decade)
- **Long-run nominal growth: 5%** (~2% real + 2% inflation + 1% margin/share)
- **ERP default: 3.5%** (slider initial position; user-adjustable)

### New chart renders (Pass C)

The wireframe has eleven chart panels, each with a commented stub at lines 1201–1213 of the new template:

```javascript
// renderEquitiesChart()    // #chart-equities
// renderYieldCurveChart()  // #chart-yield-curve
// renderRealYieldsChart()  // #chart-real-yields
// renderRatesMultiChart()  // #chart-rates-multi
// renderFxChart()          // #chart-fx
// renderCommoditiesChart() // #chart-commodities
// renderGdpChart()         // #chart-gdp
// renderCreditChart()      // #chart-credit
// renderConsumerChart()    // #chart-consumer
// renderValuationChart()   // #chart-valuation
// renderDdmChart()         // #chart-ddm
```

Each becomes a real function. Plus two new helpers:

- `renderRecessionBands(uPlotInstance)` — overlays NBER recession periods as gray vertical bands.
- `renderHighlightedPeriods(uPlotInstance)` — overlays GFC and COVID as distinctly-tinted bands.

Both are called from inside each chart-render function after the uPlot instance is constructed.

### Drops

Nothing in V1 is dropped wholesale. After Pass D's cutover, V1 files are archived to `_v1_archive/` and deleted in 30 days.

---

## 3. Pass A — Parallel V2 file tree (30 minutes)

**Goal:** the V2 files exist alongside V1, V2 builds end-to-end producing a viewable `PEC.html` with the wireframe's hardcoded dummy data still showing (no live data yet), and V1 still builds unchanged.

### Step A.1 — Drop the new template

Save the uploaded `preston_economic_console_template2_final.html` as `dashboard\PECtemplate.html`. Do not modify it. The injection placeholders `__DASHBOARD_DATA__` and `__GENERATED_AT__` are already present at the correct locations.

### Step A.2 — Duplicate the orchestrator

Copy `refresh.py` to `refresh_v2.py`. Inside, change four path constants:

1. Template path: `'template.html'` → `'PECtemplate.html'`
2. Output path: `'dashboard.html'` → `'PEC.html'`
3. Data file path: `'data.json'` → `'data_v2.json'`
4. Config path: `'config/series.yaml'` → `'config/series_v2.yaml'`

Do not change anything else in this pass. The fetcher loop, curve assembly, valuation_inputs block, and JSON-write logic stay identical.

### Step A.3 — Duplicate the series config

Copy `config/series.yaml` to `config/series_v2.yaml`. Do not change a single series. Pass B will add fields here if needed.

### Step A.4 — First build

```powershell
cd "<project-root>"
python refresh_v2.py
```

Expected: `data_v2.json` is written, `PEC.html` is written by injecting data into `PECtemplate.html`, no Python errors.

Open `PEC.html` in Chrome. Expected visual:

- Title "Preston Economic Console" in italic Source Serif at top left.
- Header timestamp populated with the actual generation time.
- Data status indicator near top right showing `data: N series · <timestamp>` in green.
- All KPI tiles showing real numbers.
- Sparklines drawn under each tile value.
- Chart placeholders still showing the gray placeholder text — expected at this pass.
- The CAPE / Fwd P/E / DDM Premium % may show `—` placeholders — expected.

### Pass A acceptance checklist

1. `python refresh_v2.py` runs without errors.
2. `python refresh.py` (V1) still runs without errors.
3. `data_v2.json` is parseable JSON with `metadata`, `series`, `valuation_inputs` keys.
4. `PEC.html` opens in Chrome with the new wireframe layout.
5. KPI tiles populate with real numbers for all 14 sparkline-driven series.
6. Sparklines render dynamically (not the static wireframe placeholder polyline points).
7. Status indicator shows green near top right.

---

## 4. Pass B — Backend extensions for valuation panel (1.5–2 hours)

**Goal:** `data_v2.json` carries the five derived valuation fields so the Valuation row populates fully — KPIs, deltas vs. history, DDM premium %, slider initial state.

### Step B.1 — Add a `derived_valuation` computation block to `refresh_v2.py`

After the existing `valuation_inputs` processing (which fetches `forward_eps_ntm` per Stage 3), add a new block. Pseudocode:

```python
# ─── Derived valuation inputs (read by PECtemplate.html bottom row) ──────
import json
from pathlib import Path

# Model constants
PAYOUT_RATIO = 0.35     # S&P 500 net-payout long-run average
LONG_RUN_GROWTH = 5.0   # percent — nominal long-run earnings growth
ERP_DEFAULT = 3.5       # percent — slider initial position

vi = data["valuation_inputs"]
sp500_last = data["series"]["sp500"]["values"][-1] if "sp500" in data["series"] else None
treas10y_last = data["series"]["treasury_10yr"]["values"][-1] if "treasury_10yr" in data["series"] else None
fwd_eps = vi.get("forward_eps_ntm", {}).get("value")

# Forward P/E: spot price / NTM consensus EPS
if sp500_last is not None and fwd_eps:
    vi["forward_pe_ntm"] = round(sp500_last / fwd_eps, 2)

# ERP default
vi["erp"] = ERP_DEFAULT

# Single-stage Gordon DDM
if fwd_eps and treas10y_last is not None:
    k = treas10y_last + ERP_DEFAULT  # discount rate, percent
    denom = (k - LONG_RUN_GROWTH) / 100.0
    if denom > 0:
        vi["ddm_fair_value"] = round((fwd_eps * PAYOUT_RATIO) / denom, 0)
    else:
        vi["ddm_fair_value"] = None  # degenerate case
```

### Step B.2 — Compute `cape_delta_string`

CAPE has long history from the Shiller `ie_data.xls` series. Compute trailing 5-year (60-month) average:

```python
cape_series = data["series"].get("shiller_cape")
if cape_series and cape_series.get("values"):
    vals = [v for v in cape_series["values"] if v is not None]
    if len(vals) >= 60:
        current = vals[-1]
        avg5y = sum(vals[-60:]) / 60.0
        delta_pct = (current - avg5y) / avg5y * 100.0
        sign = "+" if delta_pct >= 0 else ""
        vi["cape_delta_string"] = f"{sign}{delta_pct:.0f}% vs 5yr avg ({avg5y:.1f})"
```

### Step B.3 — Compute `fwdpe_delta_string`

Fwd P/E history must be derived because the `forward_eps_ntm_history.json` cache stores EPS, not P/E. For each monthly EPS entry, find the S&P 500 month-end close on or near that date, divide. Then average the resulting Fwd P/E series:

```python
# Load EPS history
eps_history_path = Path(".cache/forward_eps_ntm_history.json")
if eps_history_path.exists():
    eps_history = json.loads(eps_history_path.read_text())
    # Expected schema: list of {date, value} entries
    sp500_dates = data["series"]["sp500"]["dates"]
    sp500_values = data["series"]["sp500"]["values"]
    # Build a quick lookup: month-end string → close
    sp500_by_month = {}
    for d, v in zip(sp500_dates, sp500_values):
        if v is not None:
            month_key = d[:7]  # YYYY-MM
            sp500_by_month[month_key] = v  # last write wins → effectively month-end close

    fwd_pe_history = []
    for entry in eps_history:
        eps_date = entry["date"]
        eps_val = entry["value"]
        month_key = eps_date[:7]
        sp500_close = sp500_by_month.get(month_key)
        if sp500_close and eps_val:
            fwd_pe_history.append(sp500_close / eps_val)

    if len(fwd_pe_history) >= 24:  # at least 2 years for a meaningful average
        current_fwdpe = vi.get("forward_pe_ntm")
        avg5y = sum(fwd_pe_history) / len(fwd_pe_history)
        if current_fwdpe:
            delta_pct = (current_fwdpe - avg5y) / avg5y * 100.0
            sign = "+" if delta_pct >= 0 else ""
            vi["fwdpe_delta_string"] = f"{sign}{delta_pct:.0f}% vs 5yr avg ({avg5y:.1f}×)"
```

### Step B.4 — Sanity-check the historical Fwd P/E

After running once, manually inspect the computed average. Pull up a published Fwd P/E reading for a specific historical date (June 2022 is a useful checkpoint — Fwd P/E was widely reported around 16.5×). Compute that month's value from your data:

- Load `forward_eps_ntm_history.json`, find the June 2022 entry.
- Find the S&P 500 close on the last business day of June 2022 from `data_v2.json`.
- Divide.

If the result is within ~0.5× of 16.5×, the history file is point-in-time-published as expected. If it's off by more (especially if it looks like roughly the *current* Fwd P/E), the cache may be storing point-in-time-in-arrears (today's EPS estimate applied retrospectively). In that case, flag it for spglobal.py review — the historical chart will still render but readers shouldn't trust it for absolute valuation calls.

### Step B.5 — Read `cape-sub` and `fwdpe-sub` element overrides in template

Open `PECtemplate.html`. Lines 927 and 932 currently read:

```html
<span class="kpi-sub" id="cape-sub">vs 10yr avg</span>
<span class="kpi-sub" id="fwdpe-sub">vs 5yr avg</span>
```

In the JavaScript init block (after the existing CAPE / Fwd P/E `set()` calls around lines 1100, 1067), add:

```javascript
if (vi.cape_delta_string) set('cape-sub', vi.cape_delta_string);
if (vi.fwdpe_delta_string) set('fwdpe-sub', vi.fwdpe_delta_string);
```

### Pass B acceptance checklist

1. `data_v2.json` `valuation_inputs` has at least: `forward_eps_ntm`, `forward_pe_ntm`, `erp`, `ddm_fair_value`, `cape_delta_string`, `fwdpe_delta_string`.
2. Derived values are sane: `forward_pe_ntm` between 12 and 30; `ddm_fair_value` between 3,500 and 7,500.
3. `PEC.html` Valuation row shows real numbers in all six KPI slots, no `—` placeholders.
4. CAPE and Fwd P/E sub-labels show the live delta strings (not the static wireframe text).
5. DDM Premium % is color-coded: green when fair value > spot, red when fair value < spot.
6. ERP slider shows 3.5% by default and `erp-val` text matches.
7. June 2022 sanity check on historical Fwd P/E passes within 0.5× tolerance.
8. V1 outputs (`data.json`, `dashboard.html`) didn't regress.

---

## 5. Pass C — Wire chart placeholders to live data (3–5 hours)

**Goal:** every gray placeholder in the chart panels is replaced with a real uPlot chart drawing real data from `data_v2.json`. Recession bands and GFC/COVID highlights overlay every applicable chart.

### Step C.1 — Audit which V1 chart functions can be ported

Open `template.html` (V1) and list its uPlot chart functions. For each V2 chart, decide port / adapt / rewrite:

| V2 chart panel | V2 container ID | V1 source | Action |
|---|---|---|---|
| Equities indexed performance | `#chart-equities` | featured chart | **Adapt** — extend to multi-line indexed (S&P/Dow/R2K/EFA/EEM) |
| Yield curve | `#chart-yield-curve` | yield curve panel | **Port** + add scrubber wiring |
| Real yields (TIPS) | `#chart-real-yields` | TIPS panel | **Port** |
| FF / 2yr / 10yr / CPI | `#chart-rates-multi` | rates panel | **Port** |
| FX & Crypto indexed | `#chart-fx` | did not exist | **Rewrite** |
| Commodities indexed | `#chart-commodities` | WTI single-line | **Adapt** to indexed multi-line |
| US Real GDP QoQ Ann. | `#chart-gdp` | GDP panel | **Port** |
| Corporate credit & rates | `#chart-credit` | HY/AAA/10yr panel | **Port** |
| Consumer stress | `#chart-consumer` | consumer delinquency | **Port** |
| Valuation CAPE / Fwd P/E | `#chart-valuation` | CAPE panel | **Adapt** to dual-axis |
| DDM fair value vs spot | `#chart-ddm` | did not exist | **Rewrite** (uses derived fair-value history) |

### Step C.2 — Build the recession band and highlight period helpers

Add to the JavaScript section of `PECtemplate.html`:

```javascript
function getRecessionPeriods() {
  const D = window.DASHBOARD_DATA;
  const usrec = D.series.usrec;
  if (!usrec || !usrec.dates || !usrec.values) return [];
  const periods = [];
  let inRecession = false;
  let start = null;
  for (let i = 0; i < usrec.values.length; i++) {
    const v = usrec.values[i];
    if (v === 1 && !inRecession) {
      inRecession = true;
      start = usrec.dates[i];
    } else if (v !== 1 && inRecession) {
      inRecession = false;
      periods.push({ start, end: usrec.dates[i - 1] });
    }
  }
  if (inRecession) {
    periods.push({ start, end: usrec.dates[usrec.values.length - 1] });
  }
  return periods;
}

const HIGHLIGHTED_PERIODS = [
  { name: 'GFC',   start: '2007-12-01', end: '2009-06-30', color: 'oklch(0.55 0.06 60 / 0.18)' },
  { name: 'COVID', start: '2020-02-01', end: '2020-04-30', color: 'oklch(0.55 0.06 220 / 0.18)' },
];

function overlayBands(uPlotInstance, periods, defaultColor) {
  // uPlot hook plugin: drawClear runs after grid, before series — perfect for bg bands
  // periods: array of {start: 'YYYY-MM-DD', end: 'YYYY-MM-DD', color?: string}
  uPlotInstance.over.style.position = 'relative';
  uPlotInstance.hooks.drawClear = uPlotInstance.hooks.drawClear || [];
  uPlotInstance.hooks.drawClear.push((u) => {
    const ctx = u.ctx;
    ctx.save();
    periods.forEach((p) => {
      const t1 = Math.floor(new Date(p.start).getTime() / 1000);
      const t2 = Math.floor(new Date(p.end).getTime() / 1000);
      const x1 = u.valToPos(t1, 'x', true);
      const x2 = u.valToPos(t2, 'x', true);
      const yTop = u.valToPos(u.scales.y.max, 'y', true);
      const yBot = u.valToPos(u.scales.y.min, 'y', true);
      ctx.fillStyle = p.color || defaultColor;
      ctx.fillRect(x1, yTop, x2 - x1, yBot - yTop);
    });
    ctx.restore();
  });
}
```

The implementation may need uPlot's plugin pattern instead of post-hoc `hooks.drawClear` mutation depending on which uPlot version is loaded. Either way, the principle is the same: a single helper, applied to every chart in one line.

### Step C.3 — Style guidance: Tufte discipline

Apply uniformly to every chart in this pass:

- Top and right axis borders: removed.
- Gridlines: 1px, color `var(--bd-d)`.
- Y-axis: zero-based for non-negative data; auto-scaled for signed data.
- Direct labels on lines instead of legends (uPlot's series labels at right edge).
- Single accent color per chart by default (`var(--ac-cool)`); add `var(--ac-warm)` only when contrast pair is meaningful.
- No 3D, no shadows, no gradients, no chartjunk.

### Step C.4 — Build chart functions in this order

Build simplest-to-port first, defer net-new charts to last:

1. `renderRatesMultiChart()` — three+one lines, all already in `data.series`.
2. `renderRealYieldsChart()` — five TIPS lines.
3. `renderGdpChart()` — bar chart, single series.
4. `renderConsumerChart()` — three delinquency lines.
5. `renderCreditChart()` — three lines.
6. `renderEquitiesChart()` — multi-line indexed; rebase each series to 100 at timeframe start.
7. `renderCommoditiesChart()` — same indexed treatment.
8. `renderFxChart()` — net new. Indexed multi-line.
9. `renderYieldCurveChart()` — must include scrubber wiring.
10. `renderValuationChart()` — dual-axis CAPE + Fwd P/E.
11. `renderDdmChart()` — dual-axis spot vs. historical fair value (built from EPS history + 10y history + ERP/payout/growth constants applied month-by-month).

After each chart's uPlot instance is built, add two lines:

```javascript
overlayBands(uplotInstance, getRecessionPeriods(), 'oklch(0.65 0.02 0 / 0.10)');
overlayBands(uplotInstance, HIGHLIGHTED_PERIODS, null);  // each period carries its own color
```

The first call paints all NBER recessions in muted gray; the second paints GFC and COVID in their distinct tints. The order matters: NBER bands first means GFC/COVID tints render on top of the gray, producing a layered effect that reads as "GFC/COVID was a recession + something extra worth noticing."

### Step C.5 — Wire the interactive controls

The wireframe has `// TODO: redraw relevant chart` comments at lines 1168 and 1179. Fix:

1. **Equities timeframe tabs** — slice dates/values to the trailing window, re-render `chart-equities`.
2. **Equities series chips** — multi-select toggle, hide/show corresponding line.
3. **FX chips** — same.
4. **Commodities chips** — same.
5. **Yield curve scrubber** — connect to `renderYieldCurveChart(selectedDate)`.
6. **ERP slider** — on `input`, recompute `ddm_fair_value` and `premium %` client-side using the same single-stage Gordon formula from Pass B (constants accessible as JS module-level constants), update KPI elements, redraw `chart-ddm`.

### Step C.6 — Add the page-footer footnote

Below the `.dashboard` div in `PECtemplate.html`, add a small footer:

```html
<footer class="dash-footnote">
  <span>Gray bands · NBER recessions.</span>
  <span>Highlighted periods · GFC (2007–2009), COVID (2020).</span>
  <span>Data sources · FRED, Yahoo Finance, U.S. Treasury, Yale (Shiller), S&amp;P Global.</span>
</footer>
```

CSS:

```css
.dash-footnote {
  font-family: var(--f-mono);
  font-size: 9.5px;
  color: var(--ink-3);
  display: flex;
  gap: 24px;
  padding: 10px 24px 16px;
  border-top: 1px dashed var(--bd-d);
  margin-top: 8px;
}
```

### Pass C acceptance checklist

1. All eleven chart panels render real charts. No gray placeholder text remains.
2. Resizing the browser cleanly resizes every chart.
3. Tab and chip controls drive chart updates.
4. Yield curve scrubber slides through historical dates and the chart redraws.
5. ERP slider drives a live DDM recompute.
6. Recession bands appear on every time-series chart whose visible window crosses an NBER recession.
7. GFC and COVID highlight bands appear in their distinct tints, layered atop the recession bands.
8. Footer shows the three caption lines.
9. Charts comply with Tufte rules.
10. Dev console shows zero red errors on load.
11. `PEC.html` file size under 2 MB.

---

## 6. Pass D — Cutover and decommission (30 minutes)

**Goal:** recipients open the new file via a new shared-drive link. Any scheduled task points at `refresh_v2.py`. V1 is archived.

### Step D.1 — Send a new shared-drive link

Right-click `PEC.html` in shared-drive, select Share, generate a link. Send it to the existing recipient list with a brief note explaining this replaces the prior macro dashboard link. Do not rename or replace `dashboard.html` — leave it alone for the archive.

### Step D.2 — Update any scheduled task

If V1 was scheduled under Windows Task Scheduler running `python refresh.py`, edit the task action to call `python refresh_v2.py`. If you want both refreshing in parallel during a transition window, leave both scheduled — they share fetchers, marginal cost is negligible.

### Step D.3 — Archive V1

```powershell
mkdir _v1_archive
move template.html _v1_archive\
move dashboard.html _v1_archive\
move data.json _v1_archive\
move refresh.py _v1_archive\
move config\series.yaml _v1_archive\series.yaml
```

The `fetchers/` package stays at `dashboard\fetchers\` — shared with V2.

### Step D.4 — Update CLAUDE.md

Edit `dashboard\CLAUDE.md` and `dashboard\Macro Dashboard\CLAUDE.md`:

- Rename "Macro Dashboard" → "Preston Economic Console" or "PEC."
- Replace `refresh.py` → `refresh_v2.py`.
- Replace `template.html` → `PECtemplate.html`.
- Replace `dashboard.html` → `PEC.html`.
- Add a one-paragraph note explaining V1 is archived in `_v1_archive/`.
- Add a 30-day reminder note to delete `_v1_archive/`.

### Pass D acceptance checklist

1. Recipients have a new shared-drive link to `PEC.html`.
2. `python refresh_v2.py` is the canonical refresh command.
3. V1 files are in `_v1_archive/` and run nowhere on a schedule.
4. CLAUDE.md reflects the rename.
5. CLAUDE.md has the 30-day archive-deletion reminder.

---

## 7. Risks and known issues

**The Wien DDM formula in Pass B is a single-stage Gordon approximation.** Confirmed scope decision. Multi-scenario expansion deferred to V2.1 if needed.

**Yardeni / S&P EPS xlsx is the single source of NTM consensus EPS.** If it breaks, the entire Valuation row degrades. Stage 3 already provisioned a `manual_override` field. Make sure `series_v2.yaml` preserves that override path.

**uPlot's behavior under flex-shrunk containers.** The new template uses `min-height: 0` on `.chart-wrap` for proper grid-cell shrinking. When porting V1 chart code, watch for charts that compute height from `clientHeight` at construction — if it's `0` because parent hasn't laid out yet, the chart renders invisible. Use `requestAnimationFrame` or a `ResizeObserver` for the initial render.

**Historical Fwd P/E may be subtly wrong if EPS cache is point-in-time-in-arrears.** Pass B includes a sanity-check step (June 2022 against published 16.5×). If the test fails, the chart still renders but the absolute values are unreliable — flag for spglobal.py review.

**Recession band computation depends on `usrec` being current.** FRED's USREC series updates with a multi-month lag (NBER waits to call recessions officially). Recent recessions may not appear in the bands until well after the fact. Acceptable — the dashboard isn't a real-time recession caller.

**shared-drive sync conflicts.** Same risk as V1: colleague has file open while you regenerate, sees old version cached until refresh. No new mitigation needed.

---

## 8. Bottom line

V2 is a rename + a small backend extension + a chart-rendering pass with band overlays, not a rewrite. The wireframe author preserved the data injection contract, so fetchers are not touched. Total estimated effort: 6–9 hours across four passes, each with a verification checklist.

All eight open questions from the v1 plan are now resolved. Pass A and Pass B can begin without further input.

Once Pass D completes, "Preston Economic Console" replaces "Preston Macro Dashboard" everywhere — file names, browser title, CLAUDE.md references, and the shared-drive link colleagues open.
