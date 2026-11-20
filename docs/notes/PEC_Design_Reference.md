# Preston Economic Console — Design Reference

Source of truth: `PECtemplate.html`. All values below are read directly from the CSS and JavaScript in that file.

---

## Design Tokens (CSS Variables)

### Backgrounds

| Token | Hex | Used on |
|---|---|---|
| `--bg` | `#131109` | Page background |
| `--bg-panel` | `#1b1912` | Panel gradient start |
| `--bg-tile` | `#201e17` | KPI tiles |
| `--bg-chart` | `#171510` | Chart canvas area |

### Borders

| Token | Hex | Used on |
|---|---|---|
| `--bd-d` | `#4a4536` | Dashed borders, dotted rules, slider track |
| `--bd-s` | `#6b6450` | Solid inner tile dividers (75%-length gradients) |

### Text / Ink

| Token | Value | Role |
|---|---|---|
| `--ink-1` | `#dcd2bb` | Primary text — values, headings, active states |
| `--ink-2` | `#9e9382` | Secondary text — panel titles, section names |
| `--ink-3` | `#6d6352` | Muted text — labels, tags, chart axes, footer |

### Accent & Semantic

| Token | Value | Role |
|---|---|---|
| `--ac-cool` | `oklch(0.74 0.09 240)` | Blue — active tab borders, cursor dot, sparklines, default chip `on` |
| `--ac-warm` | `oklch(0.76 0.09 55)` | Amber — ERP slider, gold sparkline |
| `--pos` | `oklch(0.72 0.12 145)` | Green — positive price changes |
| `--neg` | `oklch(0.65 0.14 22)` | Red — negative price changes, data status error |

### Spacing

| Token | Value |
|---|---|
| `--row-gap` | `14px` |
| `--col-gap` | `14px` |
| `--sec-h` | `230px` (fixed section row height) |

---

## Typography

### Typefaces

| Variable | Stack |
|---|---|
| `--f-ui` | `'Inter'`, system-ui, sans-serif |
| `--f-serif` | `'Source Serif 4'`, Georgia, serif |
| `--f-mono` | `'JetBrains Mono'`, ui-monospace, monospace |

All three are loaded from Google Fonts. Source Serif 4 is loaded in italic and regular weights (400, 600). JetBrains Mono at 400 and 500.

### Font Sizes by Component

| Component | Family | Size | Weight / Style | Color |
|---|---|---|---|---|
| Dashboard title | `--f-serif` | `clamp(28px, 2.45vw, 42px)` | italic, 400 | `--ink-1` |
| Timestamp (inline header) | `--f-mono` | `11px` | — | `--ink-3` |
| Section number | `--f-serif` | `32px` | italic | `--ink-2` |
| Section name | `--f-serif` | `18px` | italic | `--ink-1` |
| Panel title | `--f-serif` | `12px` | italic | `--ink-2` |
| Tile label (`.t-lbl`) | `--f-serif` | `10px` | italic | `--ink-3` |
| Tile value (`.t-val`) | `--f-mono` | `18px` | — | `--ink-1` |
| Tile value — wide/tall tile | `--f-mono` | `21px` | — | `--ink-1` |
| Tile change (`.t-chg`) | `--f-mono` | `9.5px` | — | `--ink-3` (overridden by `.pos`/`.neg`) |
| Mini tile label (`.t-mini-lbl`) | `--f-serif` | `8.5px` | italic | `--ink-3` |
| Mini tile value (`.t-mini-val`) | `--f-mono` | `11px` | — | `--ink-2` |
| Tab / chip buttons | `--f-mono` | `9.5px` | — | `--ink-3` (inactive) / `--ink-1` (active) |
| KPI label (`.kpi-lbl`) | `--f-serif` | `10.5px` | italic | `--ink-3` |
| KPI value (`.kpi-val`) | `--f-mono` | `20px` | — | `--ink-1` |
| KPI sub-label (`.kpi-sub`) | `--f-mono` | `10px` | — | `--ink-3` |
| ERP slider label | `--f-serif` | `10.5px` | italic | `--ink-3` |
| ERP slider value | `--f-mono` | `11px` | — | `--ac-warm` |
| Scrubber date | `--f-mono` | `9px` | — | `--ink-3` |
| Chart axis labels | `--f-mono` | `10px` | — | `#6d6352` |
| Chart watermark label | `--f-mono` | `9px` | uppercase, 0.1em tracking | `--ink-3` |
| Footer | `--f-mono` | `9.5px` | — | `--ink-3` |

---

## Layout & Sizing

### Dashboard Shell

- Max width: `1760px`, centered, padding `12px 24px 16px`
- Flex column, `14px` row gap between sections

### Section Row Grid Columns

| Class | Columns |
|---|---|
| `.s1` | `150px 220px 1fr 1fr` |
| `.s2` | `150px 220px 1fr` |
| `.s3` | `150px 1fr 1fr` |
| `.s4` | `150px 1fr 1fr 1fr` |
| `.s5` | `150px 1fr 1fr` |

All sections have a fixed height of `230px`.

### Strip & Tile Grid

- Strip width: `220px`
- Tile grid: `2 × 2`, each cell `100px × 100px`, no gap
- Total tile area: `200px × 200px`
- Tile padding: `6px 9px 7px`

| Class | Behavior |
|---|---|
| `.tile` | Standard `1 × 1` cell |
| `.tile-v2` | Spans 2 rows (tall, `1 × 2`) |
| `.tile-h2` | Spans 2 columns (wide, `2 × 1`) |

### Rates Sub-grid (§2)

- `2 × 2` grid, `1fr 1fr` columns, `1fr 1fr` rows, `6px` gap
- `.b3` spans both rows (left column, tall)

### FX & Commodities Sub-sections

- Each `subsec`: `220px 1fr` columns, `14px` gap

---

## Borders & Dividers

| Location | Style |
|---|---|
| Header bottom | `1px dashed var(--bd-d)` |
| Section label top | `1px solid rgba(74,69,54,0.62)` |
| Panel title row bottom | `1px dashed var(--bd-d)` |
| Controls row bottom | `1px dotted var(--bd-d)` |
| KPI row bottom | `1px dotted var(--bd-d)` |
| Scrubber row top | `1px dotted var(--bd-d)` |
| Mini-tile row top | `1px dotted var(--bd-d)` |
| Footer top | `1px dashed var(--bd-d)` |
| Panel border | none |
| Tile outer border | none |

### Inner Tile Dividers (per-tile, via CSS background gradient)

Dividers are rendered as `75%`-length background gradients, not actual borders, so they work correctly on spanning tiles.

| Tile | Dividers |
|---|---|
| `#tc-sp500` (S&P, top row) | Bottom only |
| `#tc-dow` (Dow, BL) | Right only |
| `#tc-nasdaq` (Nasdaq, BR) | None |
| `#tc-fedfunds` (TL in rates strip) | Bottom + right |
| `#tc-t10y` (10yr, TL in rates strip) | Bottom only |
| `#tc-t2y` (2yr, BL in rates strip) | Right only |
| `#tc-eurusd` (EUR/USD, TL) | Bottom + right |
| `#tc-btc` (BTC, TR) | Bottom only |
| `#tc-usdcad` (CAD, BL) | Right only |
| `#tc-wti` (WTI Crude, top row) | Bottom only |
| `#tc-gold` (Gold, BL) | Right only |

---

## Chart Series Colors (`_C`)

These hex values are used both as uPlot line colors and as chip `--chip-color` values on selected series buttons.

### Equities

| Series | Key | Color |
|---|---|---|
| S&P 500 | `sp500` | `#7ec8e3` (steel blue) |
| Dow Jones | `dow` | `#a8d5ba` (sage green) |
| Russell 2000 | `russ` | `#f5c07a` (amber) |
| EFA (Developed ex-US) | `efa` | `#c9a7f5` (violet) |
| EEM (Emerging Mkts) | `eem` | `#f5a7b8` (rose) |

### FX & Crypto

| Series | Key | Color |
|---|---|---|
| EUR/USD | `eur` | `#7ec8e3` (steel blue) |
| JPY/USD | `jpy` | `#f5c07a` (amber) |
| CAD/USD | `cad` | `#a8d5ba` (sage green) |
| BTC/USD | `btc` | `#f7931a` (bitcoin orange) |
| XRP/USD | `xrp` | `#c9a7f5` (violet) |
| ~~ETH/USD~~ | ~~`eth`~~ | ~~`#627eea`~~ — removed from FX & Crypto chart; color key `eth` still exists in `_C` but is unused |

### Commodities

| Series | Key | Color |
|---|---|---|
| Gold | `gold` | `#f5c07a` (amber) |
| Silver | `silver` | `#b0b8c4` (cool gray) |
| Platinum | `plat` | `#c9a7f5` (violet) |
| WTI Crude | `wti` | `#7ec8e3` (steel blue) |

### Rates & Macro

| Series | Key | Color |
|---|---|---|
| 10yr Treasury | `t10y` | `#4a90d9` (blue) |
| 2yr Treasury | `t2y` | `#a8d5ba` |
| Fed Funds | `ff` | `#f5c07a` |
| CPI YoY | `cpi` | `#f5a7b8` |
| HY Yield | `hy` | `#f5a7b8` |
| AAA Corp | `aaa` | `#f5d76e` (yellow) |
| CAPE | `cape` | `#f5c07a` |
| Fwd P/E history | `fwdpe` | `#7ab8f5` (soft blue) — second line on CAPE & Trailing P/E chart |
| DDM Fair Value | `ddm` | `#a8d5ba` |
| TIPS (5 series) | `tips[0–4]` | `#c9a7f5`, `#4a90d9`, `#f5c07a`, `#c9a7f5`, `#f5a7b8` |

---

## Chart Rendering (uPlot Configuration)

### Base Config (`_base`)

- Padding: `[4, 20, 0, 0]` (top, right, bottom, left) in pixels
- Cursor: visible, no drag
- Legend: hidden

### X-Axis (`_timeAxis`)

- Stroke: `#6d6352`
- Grid: `#2a2820`, 1px
- Ticks: `#4a4536`, 1px
- Font: `10px JetBrains Mono`
- Format: `MM-YY` when interval < 1 year; `YYYY` otherwise

### Y-Axis (`_valAxis`)

- Stroke: `#6d6352`
- Grid: `#2a2820`, 1px
- Ticks: `#4a4536`, 1px
- Font: `10px JetBrains Mono`
- No axis title label

### Recession Bands (`_recessionPlugin`)

- Color: gray overlay drawn from NBER recession data at paint time via uPlot `draw` hook

### Cursor Data Point

- Color: `var(--ac-cool)` (overridden via `.u-cursor-pt`)

### CAPE & Trailing P/E Chart (Section 5)

Two-line chart. Both lines share inline labels via `_inlineLabelPlugin` positioned at 65% of the x-axis. The label plugin uses an outward search loop to handle sparse/monthly data (scans left and right from the 65% x-index until it finds the nearest non-null value).

| Line | Series | Color | Label |
|---|---|---|---|
| Shiller CAPE | `shiller_cape` | `#f5c07a` (`_C.cape`) | `CAPE` |
| Forward P/E history | `fwd_pe_history` | `#7ab8f5` (`_C.fwdpe`) | `Fwd P/E` |

`fwd_pe_history` is a derived series computed in `_compute_derived_valuation()` — not fetched directly. It is written to `data_v2.json` under `series` (alongside fetched series), not under `valuation_inputs`.

### DDM Fair Value Chart Footnote

The footnote reads: *FV = (NTM EPS × 35% payout ratio) ÷ (10-yr + equity risk premium (ERP) − 5% growth).* The phrase "Green = undervalued; red = overvalued." was removed. Do not re-add it.

---

## Sparklines

- SVG with `preserveAspectRatio="none"`
- Standard tile (1×1): viewBox `0 0 82 18`, stroke-width `1.2`, color `var(--ink-3)`
- S&P tile (h2 wide): viewBox `0 0 164 18`, stroke-width `1.5`, color `var(--ac-cool)`
- End-point dot: white circle, `3px` physical radius, rendered via inverse-scale SVG transform to compensate for non-uniform scaling
- Last 60 data points used; y-range padded to `80%` of tile height with `10%` margin top and bottom

### Sparkline Date Labels

Start and end date labels are added to two sparklines only: **S&P 500** and **WTI Crude**. Labels are injected as HTML `<div>`/`<span>` elements below the SVG (not as SVG `<text>`) to avoid distortion from `preserveAspectRatio="none"`. The `.t-spark.has-dates` class enables a flexbox layout: SVG takes `flex:1`, date row sits below at fixed height. Format: `"MMM D"` (e.g. `"Apr 28"`), no year. Do not add date labels to other sparklines unless explicitly requested.

---

## Interactive Controls

### Timeframe Tabs

- Default inactive: `1px solid var(--bd-d)`, color `--ink-3`
- Active (`.on`): `border-color: var(--ac-cool)`, color `--ink-1`
- Hover: `border-color: var(--ink-2)`, color `--ink-2`
- Transition: `0.12s` on border-color and color

### Series Chips

- Default inactive: same as tabs
- Active (`.on`): `background: var(--chip-color)`, `border-color: var(--chip-color)`, color `--ink-1`
- `--chip-color` is set per-chip via inline `style` attribute and matches the series line color in `_C`

### ERP Slider

~~Removed.~~ The ERP slider was removed from the Section 5 DDM panel. ERP is now a fixed constant (`3.5%`) baked into the DDM formula in `refresh_v2.py` (`_compute_derived_valuation`). The CSS token `--ac-warm` and the slider typography entries above still exist in the template but are unused.

### Yield Curve Scrubber

- Track: `2px` height, `var(--bd-d)`, border-radius `1px`
- Thumb accent: `var(--ac-cool)`
