# shiller.py — Fetches the Shiller CAPE ratio from Yale's ie_data.xls.
#
# Source: http://www.econ.yale.edu/~shiller/data/ie_data.xls
# Updated monthly by Robert Shiller. The CAPE (Cyclically Adjusted P/E) column
# is on the "Data" sheet, labelled "CAPE" in the column header row.
#
# The downloaded file is cached in dashboard/.cache/ and only re-fetched if the
# cache is older than 7 days — Shiller updates at most once a month.
#
# Install: pip install requests xlrd pandas
# NOTE: ie_data.xls is the legacy Excel 97 binary format (.xls, not .xlsx).
#       openpyxl cannot read it — xlrd is required.

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from .base import FetchError

_URL = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"
_CACHE_DIR = Path(__file__).parent.parent / ".cache"
_CACHE_FILE = _CACHE_DIR / "ie_data.xls"
_CACHE_MAX_AGE_DAYS = 7

# User-agent so Yale's server doesn't reject the request as a bot
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _cache_is_fresh() -> bool:
    """Return True if the cached file exists and is younger than _CACHE_MAX_AGE_DAYS."""
    if not _CACHE_FILE.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(_CACHE_FILE.stat().st_mtime)
    return age < timedelta(days=_CACHE_MAX_AGE_DAYS)


def _download() -> None:
    """Download ie_data.xls to the cache directory."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.get(_URL, headers=_HEADERS, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError(f"Failed to download Shiller ie_data.xls: {exc}") from exc

    _CACHE_FILE.write_bytes(resp.content)


def _detect_header_row() -> int:
    """
    Scan the first 15 rows of ie_data.xls to find the header row index (0-based).

    The header row must contain both a date-like label (e.g. "Date") and a
    CAPE-like label (e.g. "CAPE", "P/E10"). This approach is robust against
    Shiller adding or removing preamble rows across file versions — the exact
    row count has drifted over time and hardcoding it is fragile.

    Raises FetchError if no qualifying row is found in the first 15 rows.
    """
    _DATE_LABELS = {"Date", "DATE", "date"}
    _CAPE_LABELS = {"CAPE", "P/E10", "Shiller PE"}

    try:
        probe = pd.read_excel(
            _CACHE_FILE,
            sheet_name="Data",
            header=None,
            engine="xlrd",
            nrows=15,
        )
    except Exception as exc:
        raise FetchError(f"Failed to probe Shiller ie_data.xls for header row: {exc}") from exc

    for idx in range(len(probe)):
        row_vals = [str(v).strip() for v in probe.iloc[idx].values]
        # "Date" must be in column 0 — this distinguishes the actual header row
        # from preamble rows that contain "Date" mid-row as part of a multi-word
        # label (e.g. "Date Fraction" at column 5). Row 6 matches that false-
        # positive case; row 7 has "Date" at position 0 and is the true header.
        first_col_is_date = bool(row_vals) and row_vals[0] in _DATE_LABELS
        has_cape = bool(set(row_vals) & _CAPE_LABELS)
        if first_col_is_date and has_cape:
            return idx

    raise FetchError(
        "Could not detect header row in Shiller ie_data.xls — "
        "checked first 15 rows for a row where column 0 is 'Date' and a CAPE "
        "label is present. Delete dashboard/.cache/ie_data.xls to force a fresh "
        "download, then update _detect_header_row() in shiller.py if Shiller has "
        "restructured the file."
    )


def _parse(start_dt: date, end_dt: date) -> pd.DataFrame:
    """
    Parse ie_data.xls and return a date/value DataFrame for the CAPE column.

    The header row is auto-detected rather than hardcoded — Shiller has shifted
    the number of preamble rows across file versions, making a fixed offset fragile.

    The Date column contains decimal year values like 2024.01 (Jan 2024).
    """
    header_row = _detect_header_row()

    try:
        df = pd.read_excel(
            _CACHE_FILE,
            sheet_name="Data",
            header=header_row,
            engine="xlrd",  # required for legacy .xls binary format
        )
    except Exception as exc:
        raise FetchError(f"Failed to parse Shiller ie_data.xls: {exc}") from exc

    # The header row sometimes has unnamed trailing columns from merged cells.
    # Normalise column names.
    df.columns = [str(c).strip() for c in df.columns]

    # Locate the date column (first column, labelled "Date" or similar)
    date_col = df.columns[0]

    # Locate the CAPE column — may be labelled "CAPE" or "P/E10" in older versions
    cape_col = None
    for candidate in ("CAPE", "P/E10", "Shiller PE"):
        if candidate in df.columns:
            cape_col = candidate
            break
    if cape_col is None:
        raise FetchError(
            f"CAPE column not found in ie_data.xls (header detected at row {header_row}). "
            f"Columns present: {list(df.columns)}. "
            f"Update the candidate list in shiller.py if Shiller has renamed it."
        )

    df = df[[date_col, cape_col]].copy()
    df.columns = ["date_raw", "value"]

    # Drop rows where the date is missing or non-numeric (footnotes at end of sheet)
    df = df[pd.to_numeric(df["date_raw"], errors="coerce").notna()].copy()
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"])

    # Convert decimal year (e.g. 2024.01 → 2024-01-01)
    def _decimal_to_date(d: float) -> pd.Timestamp:
        year = int(d)
        # Shiller encodes months as .01, .02, ..., .10, .11, .12
        month = round((d - year) * 100)
        if month == 0:
            month = 1
        return pd.Timestamp(year=year, month=month, day=1)

    df["date"] = df["date_raw"].apply(_decimal_to_date)

    # Filter to requested window
    df = df[
        (df["date"] >= pd.Timestamp(start_dt)) &
        (df["date"] <= pd.Timestamp(end_dt))
    ][["date", "value"]].sort_values("date").reset_index(drop=True)

    return df


def fetch(series_id: str, start_date: str = "2010-01-01", end_date: str = None) -> pd.DataFrame:
    """
    Fetch the Shiller CAPE ratio and return a DataFrame with columns ['date', 'value'].

    Data is monthly (first of each month). Values are the cyclically adjusted
    price-to-earnings ratio (10-year trailing real earnings).

    Caching
    -------
    The ie_data.xls file is cached in dashboard/.cache/ie_data.xls and only
    re-downloaded if the cache is older than 7 days. Shiller updates monthly,
    so daily re-fetching is unnecessary.

    Parameters
    ----------
    series_id  : Must be 'cape' (only supported value)
    start_date : ISO date string, e.g. '2010-01-01'. Defaults to '2010-01-01'.
    end_date   : ISO date string or None (defaults to today)

    Returns
    -------
    DataFrame with columns ['date' (datetime64[ns]), 'value' (float64)],
    sorted ascending, no NaN rows. Monthly frequency.

    Raises
    ------
    FetchError
        Unsupported series_id, download failure, parse error, or empty result.

    Maintenance note
    ----------------
    Shiller occasionally restructures the Excel file (sheet name, header row
    count, column labels). If parsing breaks, inspect the raw file at:
        dashboard/.cache/ie_data.xls
    and update the header row offset or cape_col candidates in _parse() accordingly.
    """
    if series_id != "cape":
        raise FetchError(
            f"shiller.fetch() only supports 'cape', got '{series_id}'"
        )

    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = (
        datetime.strptime(end_date, "%Y-%m-%d").date()
        if end_date
        else date.today()
    )

    if not _cache_is_fresh():
        _download()

    df = _parse(start_dt, end_dt)

    if df.empty:
        raise FetchError(
            f"Shiller CAPE data is empty for the requested range "
            f"({start_date} to {end_dt.isoformat()})"
        )

    return df
