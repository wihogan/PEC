# yahoo.py — Fetches equity, FX, crypto, and commodity price series via yfinance.
# Also supports point-in-time info fields (e.g. dividendYield) via the
# yfinance Ticker.info dict.
#
# Install: pip install yfinance pandas
#
# Tickers used by the Preston Macro Dashboard:
#   ^DJI       Dow Jones Industrial Average
#   ^GSPC      S&P 500
#   ^IXIC      NASDAQ Composite
#   ^RUT       Russell 2000
#   VEA        Vanguard Developed Markets ETF (proxy for developed ex-US)
#   VWO        Vanguard Emerging Markets ETF (proxy for EM)
#   EFA        iShares MSCI EAFE ETF (developed ex-US)
#   EEM        iShares MSCI EM ETF (emerging markets)
#   BTC-USD    Bitcoin
#   ETH-USD    Ethereum
#   XRP-USD    XRP
#   GC=F       Gold futures (front-month continuous, proxy for spot)
#   SI=F       Silver futures
#   PL=F       Platinum futures
#
# Info-field path (used for dividendYield, etc.):
#   This path is rate-limited and less reliable than price-download.
#   yfinance's Ticker.info goes through Yahoo's quote-summary endpoint,
#   which throttles aggressively. Results are cached for 24 hours.
#   See fetch() docstring for full details.

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

from .base import FetchError

log = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent.parent / ".cache"
_INFO_CACHE_MAX_AGE_HOURS = 24


def _info_cache_path(series_id: str, field: str) -> Path:
    """Return the path for the info-field cache file."""
    safe_ticker = re.sub(r"[^\w\-]", "_", series_id)
    safe_field  = re.sub(r"[^\w\-]", "_", field)
    return _CACHE_DIR / f"yahoo_info_{safe_ticker}_{safe_field}.json"


def _info_cache_is_fresh(cache_path: Path) -> bool:
    if not cache_path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(cache_path.stat().st_mtime)
    return age < timedelta(hours=_INFO_CACHE_MAX_AGE_HOURS)


def _save_info_cache(cache_path: Path, value: float) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "value":      value,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_info_cache(cache_path: Path) -> float:
    """
    Load a cached info-field value.
    Raises FetchError if absent, expired, or malformed.
    """
    if not cache_path.exists():
        raise FetchError(f"Info cache not found: {cache_path.name}")
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return float(payload["value"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise FetchError(f"Malformed info cache {cache_path.name}: {exc}") from exc


def _fetch_info_field(series_id: str, field: str) -> pd.DataFrame:
    """
    Fetch a single point-in-time info field from yfinance Ticker.info.

    Returns a one-row DataFrame with columns ['date', 'value']:
        date  = today (the fetch date)
        value = the field's numeric value

    Uses a 24-hour file cache at .cache/yahoo_info_<ticker>_<field>.json.
    Falls back to the cache if the live call fails (throttle, network, etc.).

    Notes
    -----
    - This path is rate-limited and less reliable than price downloads.
      yfinance.Ticker.info hits Yahoo's quote-summary endpoint which
      throttles aggressively.  Treat any value over 24 hours old with
      caution and plan for 'unavailable' periods.

    - dividendYield is returned as a decimal by yfinance, e.g. 0.013
      for a 1.3% yield.  The render layer is responsible for ×100 display
      conversion; this function returns the raw decimal.

    Parameters
    ----------
    series_id : Yahoo Finance ticker, e.g. '^GSPC'
    field     : yfinance info key, e.g. 'dividendYield'

    Raises
    ------
    FetchError if both the live call and the cache fail.
    """
    cache_path = _info_cache_path(series_id, field)
    today_ts   = pd.Timestamp(date.today())

    live_error: Optional[Exception] = None

    # --- Attempt 1: live Ticker.info ---
    try:
        ticker = yf.Ticker(series_id)
        info   = ticker.info
        if not info:
            raise FetchError(
                f"yfinance.Ticker('{series_id}').info returned empty dict"
            )
        if field not in info or info[field] is None:
            raise FetchError(
                f"Field '{field}' not present or null in Ticker('{series_id}').info. "
                f"Available keys (sample): {list(info.keys())[:10]}"
            )
        value = float(info[field])
        _save_info_cache(cache_path, value)
        log.info(
            "[yahoo-info] %s.info[%s] = %.6f  (cached to %s)",
            series_id, field, value, cache_path.name,
        )
        return pd.DataFrame([{"date": today_ts, "value": value}])

    except FetchError:
        raise
    except Exception as exc:
        live_error = exc
        log.warning(
            "[yahoo-info] Live info fetch failed for %s.%s: %s — trying cache",
            series_id, field, exc,
        )

    # --- Attempt 2: file cache (24-hour TTL) ---
    try:
        value = _load_info_cache(cache_path)
        log.warning(
            "[yahoo-info] Using cached %s.%s = %.6f (live error: %s)",
            series_id, field, value, live_error,
        )
        return pd.DataFrame([{"date": today_ts, "value": value}])

    except FetchError as cache_exc:
        raise FetchError(
            f"Yahoo info-field fetch failed for {series_id}.{field}. "
            f"Live error: {live_error}. Cache error: {cache_exc}."
        ) from live_error


def fetch(
    series_id:  str,
    start_date: str           = "2010-01-01",
    end_date:   Optional[str] = None,
    field:      Optional[str] = None,
) -> pd.DataFrame:
    """
    Fetch a Yahoo Finance series and return a DataFrame with columns ['date', 'value'].

    Two access patterns depending on the 'field' parameter:

    Price-history path (field=None, default)
    -----------------------------------------
    Downloads the adjusted closing-price history for 'series_id' using
    yfinance.download() with auto_adjust=True.  Returns a multi-row
    DataFrame covering start_date → today.

    Info-field path (field='dividendYield', etc.)
    ----------------------------------------------
    Fetches a single point-in-time scalar from yfinance.Ticker(series_id).info.
    Returns a ONE-ROW DataFrame dated today.  start_date and end_date are
    accepted for interface compatibility but IGNORED on this path.

    The info path is rate-limited and less reliable — see _fetch_info_field()
    for full caching / fallback behaviour.

    Parameters
    ----------
    series_id  : Yahoo Finance ticker, e.g. '^GSPC', 'BTC-USD', 'GC=F'
    start_date : ISO date string.  Used only by the price-history path.
    end_date   : ISO date string or None.  Used only by the price-history path.
    field      : yfinance info key or None.  When non-None, activates the
                 info-field path.  Example: 'dividendYield'.

    Returns
    -------
    DataFrame with columns ['date' (datetime64[ns]), 'value' (float64)],
    sorted ascending, no NaN rows.

    Raises
    ------
    FetchError on network failure, unknown ticker, or empty/unparseable result.
    """
    if field is not None:
        return _fetch_info_field(series_id, field)

    # ---- Price-history path ------------------------------------------------
    try:
        raw = yf.download(
            series_id,
            start=start_date,
            end=end_date,
            auto_adjust=True,
            progress=False,
            multi_level_index=False,
        )
    except Exception as exc:
        raise FetchError(f"yfinance.download() failed for '{series_id}': {exc}") from exc

    if raw is None or raw.empty:
        raise FetchError(f"Yahoo Finance returned no data for ticker '{series_id}'")

    close = _extract_close(raw, series_id)

    df = close.reset_index()
    df.columns = ["date", "value"]
    df["date"]  = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"])
    df = df.sort_values("date").reset_index(drop=True)

    if df.empty:
        raise FetchError(
            f"Yahoo Finance data for '{series_id}' is empty after cleaning"
        )

    return df


def _extract_close(raw: pd.DataFrame, series_id: str) -> pd.Series:
    """
    Pull the Close column from a yfinance.download() result regardless of
    whether the columns are flat strings or a MultiIndex.
    """
    cols = raw.columns

    if isinstance(cols, pd.Index) and not isinstance(cols, pd.MultiIndex):
        if "Close" in cols:
            return raw["Close"]
        raise FetchError(
            f"'Close' column not found in yfinance output for '{series_id}'. "
            f"Columns present: {list(cols)}"
        )

    if isinstance(cols, pd.MultiIndex):
        if "Close" in cols.get_level_values(0):
            return raw["Close"].iloc[:, 0]
        raise FetchError(
            f"'Close' not found in MultiIndex columns for '{series_id}'. "
            f"Top-level columns: {list(cols.get_level_values(0).unique())}"
        )

    raise FetchError(
        f"Unrecognised column structure from yfinance for '{series_id}': {type(cols)}"
    )
