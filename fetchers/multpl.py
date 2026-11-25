# NOTE (2026-11-24): UNUSED. multpl.com was replaced by fetchers/shiller.py (docs/plan.md
# Amendment C). Kept in the repo for reference and its tests; not registered in series_v2.yaml.
#
# multpl.py — Fetches S&P 500 valuation metrics from multpl.com monthly tables.
#
# Source: https://www.multpl.com/{slug}/table/by-month
# Monthly data sourced from Robert Shiller's dataset. History from Jan 1871
# for S&P 500 series (~1,865 monthly observations as of 2026).
#
# Supported series IDs:
#   sp500_div_yield       → s-p-500-dividend-yield    (%, stored as decimal)
#   sp500_pe              → s-p-500-pe-ratio           (trailing 12-mo P/E)
#   sp500_earnings_yield  → s-p-500-earnings-yield     (%, stored as decimal)
#   sp500_earnings        → s-p-500-earnings           (TTM EPS, $/share)
#   shiller_pe            → shiller-pe                 (Shiller CAPE ratio)
#
# Cache: raw HTML stored in .cache/multpl_{series_id}.html, TTL 24 hours.
# Values marked with '†' indicate estimates (current partial month); the
# dagger is stripped and the value used normally.
#
# Install: pip install requests beautifulsoup4 lxml  (both present in venv)

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .base import FetchError

log = logging.getLogger(__name__)

_BASE_URL  = "https://www.multpl.com/{slug}/table/by-month"
_CACHE_DIR = Path(__file__).parent.parent / ".cache"
_CACHE_MAX_AGE_HOURS = 24

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":  "text/html,application/xhtml+xml,*/*",
    "Referer": "https://www.multpl.com/",
}

# series_id → (slug, is_percent)
# is_percent=True : raw value has '%' suffix; stored as decimal (value ÷ 100)
# is_percent=False: raw value is a ratio or dollar amount; stored as-is
_SERIES: dict[str, tuple[str, bool]] = {
    "sp500_div_yield":      ("s-p-500-dividend-yield", True),
    "sp500_pe":             ("s-p-500-pe-ratio",        False),
    "sp500_earnings_yield": ("s-p-500-earnings-yield",  True),
    "sp500_earnings":       ("s-p-500-earnings",        False),
    "shiller_pe":           ("shiller-pe",              False),
}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(series_id: str) -> Path:
    return _CACHE_DIR / f"multpl_{series_id}.html"


def _cache_is_fresh(series_id: str) -> bool:
    p = _cache_path(series_id)
    if not p.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)
    return age < timedelta(hours=_CACHE_MAX_AGE_HOURS)


def _download(slug: str, series_id: str) -> str:
    url = _BASE_URL.format(slug=slug)
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError(f"[multpl] Failed to fetch {url}: {exc}") from exc
    html = resp.text
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(series_id).write_text(html, encoding="utf-8")
    log.info("[multpl] Downloaded and cached HTML for %s (%d KB)", series_id, len(html) // 1024)
    return html


def _get_html(slug: str, series_id: str) -> str:
    if _cache_is_fresh(series_id):
        log.info("[multpl] Using cached HTML for %s", series_id)
        return _cache_path(series_id).read_text(encoding="utf-8")
    return _download(slug, series_id)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_value(raw: str, is_percent: bool) -> Optional[float]:
    """
    Strip '†' (U+2020), '%', '$', whitespace, and commas; return float.
    Returns value / 100 if is_percent=True (stores as decimal).
    Returns None if the string cannot be coerced to a number.
    """
    s = re.sub(r"[††%$,\s]", "", raw)
    try:
        v = float(s)
        return v / 100.0 if is_percent else v
    except ValueError:
        return None


def _parse_html(
    html:       str,
    is_percent: bool,
    start_dt:   date,
    end_dt:     date,
) -> pd.DataFrame:
    soup  = BeautifulSoup(html, "lxml")
    table = soup.find("table", {"id": "datatable"})
    if table is None:
        raise FetchError("[multpl] Table #datatable not found in page HTML.")

    rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue

        date_str  = tds[0].get_text(strip=True)
        value_str = tds[1].get_text(strip=True)

        try:
            row_date = datetime.strptime(date_str, "%b %d, %Y").date()
        except ValueError:
            continue

        value = _parse_value(value_str, is_percent)
        if value is None:
            continue

        if start_dt <= row_date <= end_dt:
            rows.append({"date": pd.Timestamp(row_date), "value": value})

    if not rows:
        raise FetchError(
            f"[multpl] No rows parsed for the requested date range "
            f"({start_dt} → {end_dt}). Check slug, HTML structure, or date filter."
        )

    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def fetch(
    series_id:  str,
    start_date: str           = "2010-01-01",
    end_date:   Optional[str] = None,
) -> pd.DataFrame:
    """
    Fetch a multpl.com monthly data series.

    Parameters
    ----------
    series_id  : Supported ID (see _SERIES mapping at top of file).
    start_date : ISO date string, default '2010-01-01'.
    end_date   : ISO date string or None (defaults to today).

    Returns
    -------
    DataFrame with columns ['date' (datetime64[ns]), 'value' (float64)],
    sorted ascending, no NaN rows. Monthly frequency.

    Raises
    ------
    FetchError on unsupported series_id, network failure, parse error,
    or empty result after date filtering.

    Maintenance notes
    -----------------
    If the table structure changes, inspect the cached HTML at:
        dashboard/.cache/multpl_{series_id}.html
    Delete the cache file to force a fresh download, then update _parse_html()
    if multpl.com has restructured the table or changed the id attribute.
    """
    if series_id not in _SERIES:
        raise FetchError(
            f"[multpl] Unsupported series_id '{series_id}'. "
            f"Supported: {sorted(_SERIES.keys())}"
        )

    slug, is_percent = _SERIES[series_id]
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt   = (
        datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else date.today()
    )

    try:
        html = _get_html(slug, series_id)
    except FetchError as exc:
        stale = _cache_path(series_id)
        if stale.exists():
            log.warning(
                "[multpl] Live fetch failed (%s) — falling back to stale cache for %s",
                exc, series_id,
            )
            html = stale.read_text(encoding="utf-8")
        else:
            raise

    df = _parse_html(html, is_percent, start_dt, end_dt)

    log.info(
        "[multpl] %s: %d rows (%s → %s)",
        series_id, len(df),
        df["date"].iloc[0].date(), df["date"].iloc[-1].date(),
    )
    return df
