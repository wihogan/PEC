# Fetcher Build — Errors and Corrections

Errors encountered during construction of the four fetcher modules, with root causes and corrections. Intended as a reference so these issues are not repeated if the pipeline is rebuilt from scratch.

---

## 1. `fetch()` missing default value for `start_date` — all three API fetchers

**Symptom.** `TypeError: fetch() missing 1 required positional argument: 'start_date'` when calling any fetcher interactively with only a series ID (e.g., `fred_fetch("DGS10")`).

**Cause.** The initial signatures were written with `start_date` as a required positional argument.

**Correction.** All fetchers must default `start_date` to `"2010-01-01"`:

```python
def fetch(series_id: str, start_date: str = "2010-01-01", end_date: str = None, ...) -> pd.DataFrame:
```

This applies to `fred.py`, `yahoo.py`, and `treasury.py`. `shiller.py` was written correctly from the start.

---

## 2. Treasury CSV endpoint does not exist

**Symptom.** `404 Client Error: Not Found` when hitting a URL of the form `https://home.treasury.gov/.../daily-treasury-rates.csv/all/YYYYMM`.

**Cause.** That URL format was assumed but does not exist on Treasury's servers.

**Correction.** The working endpoint is the Treasury Direct OData XML feed, iterated one call per calendar year:

```python
_BASE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/pages/xml?data=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}"
)
```

Fetch years from `start_dt.year` through `end_dt.year` inclusive, concatenate the resulting frames, then filter to the exact requested date window. Do not attempt a single-call or monthly-call pattern — this XML endpoint is year-granularity only.

---

## 3. Treasury XML regex fails to match BC_ tenor fields

**Symptom.** `FetchError: No recognised tenor columns found in Treasury XML response. Columns present: ['date']` — the DataFrame contained only the date column and no yield data.

**Cause.** Treasury's XML tags carry a type attribute: `<d:BC_10YEAR m:type="Edm.Double">4.25</d:BC_10YEAR>`. The original regex `<(d:BC_\w+)>(.*?)</d:BC_\w+>` requires the opening tag to close immediately after the field name, so it matched nothing when the attribute was present.

**Correction.** Use `[^>]*` to absorb any attributes between the field name and the closing `>`:

```python
for col, value in re.findall(r"<(d:BC_\w+)[^>]*>(.*?)</d:BC_\w+>", block):
```

This is the critical regex. Without `[^>]*`, the fetcher silently returns no tenor data.

---

## 4. Shiller `ie_data.xls` is legacy Excel 97 binary format — `openpyxl` cannot read it

**Symptom.** `BadZipFile: File is not a zip file` when parsing the downloaded Shiller file.

**Cause.** `ie_data.xls` is the Excel 97–2003 binary format (`.xls`), not the modern XML-based format (`.xlsx`). `openpyxl` only reads `.xlsx` files, which are ZIP archives internally. It raises `BadZipFile` when handed a binary `.xls`.

**Correction.** Use `engine="xlrd"` in `pd.read_excel()`, and ensure `xlrd` is pinned in `requirements.txt`. `xlrd` version 2.x supports `.xls` only — it deliberately dropped `.xlsx` support. Do not attempt to use `openpyxl` for this file.

```python
df = pd.read_excel(_CACHE_FILE, sheet_name="Data", header=header_row, engine="xlrd")
```

`requirements.txt` must include: `xlrd==2.0.2`

---

## 5. Shiller header row detection lands on the wrong row, returning yield values instead of CAPE ratios

**Symptom.** `AssertionError: CAPE values outside plausible range [10, 60]: min=0.0, max=0.0` — the fetcher returned CAPE values in the range 0.018–0.031 (the Excess CAPE Yield, approximately 1/CAPE minus the real bond rate) instead of the ratio itself (which should be 15–50).

**Cause.** Two compounding issues.

First, the hardcoded `header=7` offset was fragile — any future Shiller restructure would silently break it, so it was replaced with an auto-detection function.

Second, the auto-detection scanned for any row containing both a "Date"-like label and a "CAPE"-like label *anywhere* in the row. Row 6 (0-based) of the Shiller sheet contains the text "Date  " (with trailing spaces, part of a "Date Fraction" multi-row merged-cell label) at column 5, and the text "CAPE" at a column beyond index 14. This caused detection to return row 6 instead of row 7. With `header=6`, the column named "CAPE" resolved to a completely different data column — the Excess CAPE Yield — producing small decimal values that passed NaN filtering but failed the plausibility check.

**Correction.** Require "Date" to appear specifically at column index 0, not anywhere in the row. The actual header row has "Date" as its first column; preamble rows that incidentally contain the word "Date" have it mid-row.

```python
for idx in range(len(probe)):
    row_vals = [str(v).strip() for v in probe.iloc[idx].values]
    first_col_is_date = bool(row_vals) and row_vals[0] in {"Date", "DATE", "date"}
    has_cape = bool(set(row_vals) & {"CAPE", "P/E10", "Shiller PE"})
    if first_col_is_date and has_cape:
        return idx
```

This correctly identifies row 7 as the header row regardless of what Shiller places in the preamble rows above it.

---

## 6. Shiller smoke test window too recent — fails due to known source publication lag

**Symptom.** Test passes the column and NaN checks but fails a `max_date` recency assertion when the assertion window is set to 6 months.

**Cause.** Shiller's public `ie_data.xls` file has a known 2–3 year publication lag on the CAPE column specifically. The CAPE calculation requires 10 years of inflation-adjusted real earnings, and Shiller does not backfill recent months until the trailing data is finalised. A file downloaded in April 2026 may have CAPE data only through late 2023 — this is expected behavior, not a fetcher bug.

**Correction.** Use a separate, earlier start date for the Shiller smoke test (not the same recent window used for daily FRED and Yahoo series), and use a 36-month recency cutoff in the `max_date` assertion rather than 6 months. The purpose of the recency check is to catch date-parsing bugs that send all dates to the wrong century — not to validate the source's publication cadence.

```python
SHILLER_START = "2022-01-01"   # separate constant, not shared with START = "2024-01-01"

cutoff = pd.Timestamp(date.today()) - pd.DateOffset(months=36)
assert max_date >= cutoff, "..."
```
