# test_fetchers.py — Smoke tests: one per fetcher module.
#
# These tests hit live APIs. They are intentionally lightweight — each fetches
# a single known-good series for a short recent window and asserts that the
# result is non-empty, correctly shaped, NaN-free in the value column, and
# sorted ascending by date.
#
# Purpose: catch upstream API breakage (endpoint changes, auth failures,
# schema drift) before it silently corrupts data.json.
#
# Run from the dashboard/ directory with the venv activated:
#
#   python -m pytest tests/ -v
#   python -m pytest tests/ -v -k fred        # run one test by name fragment
#
# Requirements: all fetcher dependencies installed, FRED_API_KEY set as an
# environment variable. Tests will fail with a clear error message if the key
# is missing — that is intentional and correct behaviour.

import sys
from pathlib import Path

import pandas as pd
import pytest

# Ensure the dashboard/ directory is on sys.path so 'fetchers' is importable
# regardless of where pytest is invoked from.
sys.path.insert(0, str(Path(__file__).parent.parent))

from fetchers.fred      import fetch as fred_fetch
from fetchers.yahoo     import fetch as yahoo_fetch
from fetchers.treasury  import fetch as treasury_fetch
from fetchers.multpl    import fetch as multpl_fetch

# Live-network smoke tests: excluded from the default (offline) run by pytest.ini.
# Run with:  python -m pytest -m integration
# enable_socket overrides the --disable-socket in pytest.ini addopts.
pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

# A recent-but-not-too-recent start date: long enough to guarantee multiple
# observations across all series frequencies (daily, monthly, quarterly).
START = "2024-01-01"

# Expected columns for the standard two-column contract.
STANDARD_COLS = {"date", "value"}

# Expected columns for the treasury three-column contract.
TREASURY_COLS = {"date", "tenor", "value"}


# ---------------------------------------------------------------------------
# Shared assertion helpers
# ---------------------------------------------------------------------------

def _assert_standard(df: pd.DataFrame, label: str) -> None:
    """Assert the two-column contract: non-empty, correct columns, no NaN values, ascending dates."""
    assert isinstance(df, pd.DataFrame), f"[{label}] fetch() must return a DataFrame"
    assert not df.empty,                 f"[{label}] DataFrame must not be empty"

    missing = STANDARD_COLS - set(df.columns)
    assert not missing,                  f"[{label}] missing columns: {missing}"

    nan_count = df["value"].isna().sum()
    assert nan_count == 0,               f"[{label}] value column contains {nan_count} NaN(s)"

    assert df["date"].is_monotonic_increasing, f"[{label}] dates must be in ascending order"


# ---------------------------------------------------------------------------
# FRED — 10-Year Treasury (daily, no transform)
# ---------------------------------------------------------------------------

def test_fred_10yr_treasury():
    """DGS10: daily FRED series, standard two-column contract."""
    df = fred_fetch("DGS10", start_date=START)
    _assert_standard(df, "fred/DGS10")
    assert df["date"].dtype == "datetime64[ns]", "date column must be datetime64[ns]"
    assert df["value"].dtype == float,            "value column must be float"


# ---------------------------------------------------------------------------
# Yahoo Finance — S&P 500
# ---------------------------------------------------------------------------

def test_yahoo_sp500():
    """^GSPC: daily Yahoo Finance series, standard two-column contract."""
    df = yahoo_fetch("^GSPC", start_date=START)
    _assert_standard(df, "yahoo/^GSPC")
    assert df["date"].dtype == "datetime64[ns]", "date column must be datetime64[ns]"
    # Sanity-check that prices are plausible (S&P 500 above 1000 since 2009)
    assert df["value"].min() > 1000, "S&P 500 values should be above 1000"


# ---------------------------------------------------------------------------
# Treasury — Full yield curve (three-column long-form contract)
# ---------------------------------------------------------------------------

def test_treasury_yield_curve():
    """
    daily_treasury_yield_curve: three-column long-form contract deviation.
    Columns: date, tenor, value. Asserts multiple tenors are present,
    value column is NaN-free, and dates are ascending within each tenor.
    """
    df = treasury_fetch("daily_treasury_yield_curve", start_date=START)

    assert isinstance(df, pd.DataFrame),   "fetch() must return a DataFrame"
    assert not df.empty,                   "DataFrame must not be empty"

    missing = TREASURY_COLS - set(df.columns)
    assert not missing,                    f"missing columns: {missing}"

    nan_count = df["value"].isna().sum()
    assert nan_count == 0,                 f"value column contains {nan_count} NaN(s)"

    tenors = df["tenor"].unique().tolist()
    assert len(tenors) >= 10, (
        f"Expected at least 10 tenors, got {len(tenors)}: {tenors}"
    )

    # Dates must be ascending within each tenor group.
    for tenor, group in df.groupby("tenor", observed=True):
        assert group["date"].is_monotonic_increasing, (
            f"Dates not ascending for tenor '{tenor}'"
        )



# ---------------------------------------------------------------------------
# multpl.com — S&P 500 P/E (monthly, scraped HTML)
# ---------------------------------------------------------------------------

def test_multpl_sp500_pe():
    """sp500_pe: scraped monthly series, standard two-column contract, recent data."""
    df = multpl_fetch("sp500_pe")
    _assert_standard(df, "multpl/sp500_pe")
    assert df["date"].dtype == "datetime64[ns]", "date column must be datetime64[ns]"
    assert df["date"].iloc[-1] >= pd.Timestamp.now() - pd.Timedelta(days=120), (
        "latest multpl observation should be within ~4 months"
    )
