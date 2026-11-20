# fred.py — Fetches series from the FRED API via fredapi.
#
# Requires: FRED_API_KEY set as a Windows environment variable.
# Install:  pip install fredapi pandas
#
# Series used by the Preston Macro Dashboard:
#
#   Rates
#     DFF             Fed Funds Effective Rate (daily)
#     DGS1MO          1-Month Treasury Constant Maturity (daily)
#     DGS10           10-Year Treasury Constant Maturity (daily)
#     DFII5           5-Year TIPS (daily)
#     DFII7           7-Year TIPS (daily)
#     DFII10          10-Year TIPS (daily)
#     DFII20          20-Year TIPS (daily)
#     DFII30          30-Year TIPS (daily)
#
#   Credit
#     BAMLH0A0HYM2EY  ICE BofA HY Effective Yield (daily) — attribution reqd externally
#     BAMLC0A1CAAAEY  ICE BofA AAA Effective Yield (daily) — attribution reqd externally
#
#   Commodities
#     DCOILWTICO      WTI Crude Oil (daily)
#
#   Macro
#     A191RL1Q225SBEA Real GDP Growth QoQ Ann. (quarterly)
#     CPIAUCSL        CPI All Urban Consumers (monthly) — use transform='pct_change_12m'
#     DRCLACBS        Consumer Loan Delinquency Rate (quarterly)
#
#   FX
#     DEXUSEU         EUR/USD (daily)
#     DEXCAUS         CAD per USD (daily)
#     DEXJPUS         JPY per USD (daily)

from __future__ import annotations

import logging
import os
import time

import pandas as pd
from fredapi import Fred

from .base import FetchError

log = logging.getLogger(__name__)

_RATE_LIMIT_PHRASE = "Too Many Requests"
_RETRY_DELAYS = (2, 5, 10)  # seconds between attempts 1→2, 2→3, 3→4

# Proactive throttle: FRED's free tier allows 120 requests/minute.
# Enforcing a 0.6s minimum interval between calls keeps us well inside that
# limit even on fast networks where API responses arrive in < 100ms.
_MIN_CALL_INTERVAL = 0.6   # seconds
_last_call_time: float = 0.0


def _throttle() -> None:
    global _last_call_time
    elapsed = time.monotonic() - _last_call_time
    if elapsed < _MIN_CALL_INTERVAL:
        time.sleep(_MIN_CALL_INTERVAL - elapsed)
    _last_call_time = time.monotonic()

# Valid frequency aliases accepted by fredapi / pandas resample.
# Callers pass these through series.yaml → refresh.py → here.
_VALID_FREQUENCIES = {"D", "W", "M", "Q", "A"}

# Supported post-fetch transforms.
_VALID_TRANSFORMS = {"pct_change_12m", "pct_change_1m", "pct_change_1q"}


def fetch(
    series_id: str,
    start_date: str = "2010-01-01",
    end_date: str = None,
    frequency: str = None,
    transform: str = None,
) -> pd.DataFrame:
    """
    Fetch a FRED series and return a DataFrame with columns ['date', 'value'].

    Parameters
    ----------
    series_id  : FRED series identifier, e.g. 'DGS10'
    start_date : ISO date string, e.g. '2010-01-01' (inclusive). Defaults to '2010-01-01'.
    end_date   : ISO date string or None — defaults to today
    frequency  : Optional resampling frequency: 'D', 'W', 'M', 'Q', 'A'.
                 Uses last observation in each period. Applied before transform.
    transform  : Optional post-fetch transformation:
                   'pct_change_12m'  YoY % change (12-period for monthly, e.g. CPI)
                   'pct_change_1m'   MoM % change
                   'pct_change_1q'   QoQ % change
                 Values are expressed as percentages (multiplied by 100).

    Returns
    -------
    DataFrame with columns ['date' (datetime64[ns]), 'value' (float64)],
    sorted ascending, no NaN rows.

    Raises
    ------
    FetchError
        Missing API key, unknown series, network failure, empty result,
        invalid frequency or transform argument.
    """
    # --- Validate arguments ---------------------------------------------------
    if frequency is not None and frequency not in _VALID_FREQUENCIES:
        raise FetchError(
            f"Invalid frequency '{frequency}'. Valid options: {sorted(_VALID_FREQUENCIES)}"
        )
    if transform is not None and transform not in _VALID_TRANSFORMS:
        raise FetchError(
            f"Invalid transform '{transform}'. Valid options: {sorted(_VALID_TRANSFORMS)}"
        )

    # --- API key --------------------------------------------------------------
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        raise FetchError(
            "FRED_API_KEY environment variable is not set. "
            "Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html"
        )

    # --- Fetch (with proactive throttle + rate-limit retry) -------------------
    fred_client = Fred(api_key=api_key)
    last_exc: Exception | None = None

    _throttle()  # enforce minimum interval before every FRED request

    for attempt, delay in enumerate([0] + list(_RETRY_DELAYS), start=1):
        if delay:
            log.warning(
                "[fred] rate-limited on '%s' — waiting %ds before retry %d/%d",
                series_id, delay, attempt, 1 + len(_RETRY_DELAYS),
            )
            time.sleep(delay)
        try:
            raw = fred_client.get_series(
                series_id,
                observation_start=start_date,
                observation_end=end_date,
            )
            break  # success
        except ValueError as exc:
            msg = str(exc)
            if _RATE_LIMIT_PHRASE in msg and attempt <= len(_RETRY_DELAYS):
                last_exc = exc
                continue  # retry with backoff
            raise FetchError(
                f"FRED series '{series_id}' not found or access denied: {exc}"
            ) from exc
        except Exception as exc:
            raise FetchError(
                f"FRED API request failed for '{series_id}': {exc}"
            ) from exc
    else:
        raise FetchError(
            f"FRED series '{series_id}' not found or access denied: {last_exc}"
        ) from last_exc

    if raw is None or raw.empty:
        raise FetchError(f"FRED returned no data for series '{series_id}'")

    # --- Shape into contract DataFrame ----------------------------------------
    df = raw.reset_index()
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    # Drop not-yet-released observations (FRED encodes these as NaN / '.')
    df = df.dropna(subset=["value"])
    df = df.sort_values("date").reset_index(drop=True)

    # --- Optional frequency resampling ----------------------------------------
    if frequency is not None:
        df = (
            df.set_index("date")["value"]
            .resample(frequency)
            .last()          # last observation in each period
            .dropna()
            .reset_index()
        )
        df.columns = ["date", "value"]

    # --- Optional transform ---------------------------------------------------
    if transform is not None:
        periods = {"pct_change_12m": 12, "pct_change_1m": 1, "pct_change_1q": 1}[transform]
        df["value"] = df["value"].pct_change(periods=periods).mul(100)
        df = df.dropna(subset=["value"]).reset_index(drop=True)

    if df.empty:
        raise FetchError(
            f"FRED series '{series_id}' is empty after applying "
            f"frequency='{frequency}', transform='{transform}'"
        )

    return df
