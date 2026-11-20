# treasury.py — Fetches the full daily Treasury yield curve from TreasuryDirect XML feed.
#
# One HTTP call per calendar year; results are concatenated and returned in long form.
# No API key required — the endpoint is public.
#
# Install: pip install requests pandas

from __future__ import annotations

import re
from datetime import date, datetime
from io import StringIO

import pandas as pd
import requests

from .base import FetchError

# XML endpoint — one call per year, returns all tenors for that year.
_BASE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/pages/xml?data=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}"
)

# Map Treasury XML BC_ field names → clean tenor labels used throughout the dashboard.
# Treasury occasionally adds tenors; unmapped extras are ignored.
_COLUMN_MAP: dict[str, str] = {
    "BC_1MONTH":  "1mo",
    "BC_2MONTH":  "2mo",
    "BC_3MONTH":  "3mo",
    "BC_4MONTH":  "4mo",
    "BC_6MONTH":  "6mo",
    "BC_1YEAR":   "1yr",
    "BC_2YEAR":   "2yr",
    "BC_3YEAR":   "3yr",
    "BC_5YEAR":   "5yr",
    "BC_7YEAR":   "7yr",
    "BC_10YEAR":  "10yr",
    "BC_20YEAR":  "20yr",
    "BC_30YEAR":  "30yr",
}

# Canonical display order for the yield curve.
_TENOR_ORDER = [
    "1mo", "2mo", "3mo", "4mo", "6mo",
    "1yr", "2yr", "3yr", "5yr", "7yr", "10yr", "20yr", "30yr",
]


def _fetch_year(year: int, session: requests.Session) -> pd.DataFrame:
    """
    Fetch one year of yield curve data from the Treasury XML endpoint.
    Returns a wide DataFrame (date + one column per BC_ tenor present).
    Returns an empty DataFrame if the year has no data.
    """
    url = _BASE_URL.format(year=year)
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError(f"Treasury XML request failed for {year}: {exc}") from exc

    xml = resp.text
    entry_blocks = re.findall(r"<m:properties>(.*?)</m:properties>", xml, re.DOTALL)

    rows = []
    for block in entry_blocks:
        date_match = re.search(r"<d:NEW_DATE[^>]*>(.*?)</d:NEW_DATE>", block)  # [^>]* absorbs m:type attr
        if not date_match:
            continue
        row: dict = {"date": date_match.group(1)[:10]}  # YYYY-MM-DD
        for col, value in re.findall(r"<(d:BC_\w+)[^>]*>(.*?)</d:BC_\w+>", block):
            key = col.replace("d:", "")   # strip XML namespace prefix
            try:
                row[key] = float(value)
            except ValueError:
                row[key] = None           # missing tenor on that date
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def fetch(series_id: str, start_date: str = "2010-01-01", end_date: str = None) -> pd.DataFrame:
    """
    Fetch the full daily Treasury yield curve and return a long-form DataFrame.

    Contract deviation
    ------------------
    This fetcher returns THREE columns instead of the standard two:

        date   (datetime64[ns])  — observation date, sorted ascending
        tenor  (str)             — maturity label: '1mo', '2mo', '3mo', '4mo',
                                   '6mo', '1yr', '2yr', '3yr', '5yr', '7yr',
                                   '10yr', '20yr', '30yr'
        value  (float64)         — yield in percent (e.g. 4.75 means 4.75%)

    This is an intentional relaxation of the base contract to accommodate the
    yield curve's multi-tenor structure. The orchestrator (refresh.py) handles
    this fetcher specially — it filters or pivots the long form as needed before
    writing to data.json.

    Parameters
    ----------
    series_id  : Must be 'daily_treasury_yield_curve' (only supported value)
    start_date : ISO date string, e.g. '2010-01-01'. Defaults to '2010-01-01'.
    end_date   : ISO date string or None (defaults to today)

    Raises
    ------
    FetchError
        Unsupported series_id, network failure, XML parse error, or empty result
        after date filtering.

    Maintenance note
    ----------------
    Treasury occasionally adds new BC_ tenor fields to the XML feed. New tenors
    are ignored until added to _COLUMN_MAP and _TENOR_ORDER in this module.
    If existing tenors disappear from output, check _COLUMN_MAP against the live
    XML and update accordingly.
    """
    if series_id != "daily_treasury_yield_curve":
        raise FetchError(
            f"treasury.fetch() only supports 'daily_treasury_yield_curve', got '{series_id}'"
        )

    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = (
        datetime.strptime(end_date, "%Y-%m-%d").date()
        if end_date
        else date.today()
    )

    frames: list[pd.DataFrame] = []
    with requests.Session() as session:
        for year in range(start_dt.year, end_dt.year + 1):
            frame = _fetch_year(year, session)
            if not frame.empty:
                frames.append(frame)

    if not frames:
        raise FetchError("Treasury returned no data for the requested date range")

    wide = pd.concat(frames, ignore_index=True)

    # Filter to requested window
    wide = wide[
        (wide["date"] >= pd.Timestamp(start_dt)) &
        (wide["date"] <= pd.Timestamp(end_dt))
    ]

    if wide.empty:
        raise FetchError("Treasury data is empty after date filtering")

    # Keep only recognised BC_ columns; rename to clean tenor labels
    tenor_cols = {col: label for col, label in _COLUMN_MAP.items() if col in wide.columns}
    if not tenor_cols:
        raise FetchError(
            f"No recognised tenor columns found in Treasury XML response. "
            f"Columns present: {list(wide.columns)}. "
            f"Update _COLUMN_MAP in treasury.py if Treasury has renamed fields."
        )

    wide = wide[["date"] + list(tenor_cols.keys())].rename(columns=tenor_cols)

    # Melt wide → long form: (date, tenor, value)
    long = wide.melt(id_vars="date", var_name="tenor", value_name="value")
    long["tenor"] = pd.Categorical(long["tenor"], categories=_TENOR_ORDER, ordered=True)
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long = (
        long
        .dropna(subset=["tenor", "value"])
        .sort_values(["date", "tenor"])
        .reset_index(drop=True)
    )

    if long.empty:
        raise FetchError("Treasury yield curve is empty after reshaping")

    return long
