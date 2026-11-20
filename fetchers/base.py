# base.py — Shared contract and error type for all fetcher modules.
#
# Fetcher contract
# ----------------
# Every module in this package must expose a single public function:
#
#   def fetch(series_id: str, start_date: str, end_date: str = None) -> pd.DataFrame:
#
# Return value
# ------------
# A pandas DataFrame with exactly two columns:
#
#   date  (datetime64[ns])  — observation date, timezone-naive, sorted ascending
#   value (float64)         — numeric value; no NaN rows (drop or fill before returning)
#
# The caller (refresh.py) assumes this shape unconditionally. A fetcher that
# returns extra columns, unsorted rows, or NaN values will produce silent
# errors downstream. Validate inside the fetcher before returning.
#
# Parameters
# ----------
# series_id  : Source-specific identifier (FRED series code, Yahoo ticker, etc.)
# start_date : ISO 8601 date string, e.g. '2010-01-01' (inclusive)
# end_date   : ISO 8601 date string or None; None means fetch through today
#
# Errors
# ------
# Raise FetchError for any retrieval or parse failure — missing API key,
# network error, empty response, unexpected schema. Do not let library
# exceptions propagate unwrapped; always chain them with `raise FetchError(...) from exc`.
#
# Example skeleton
# ----------------
#   from .base import FetchError
#
#   def fetch(series_id: str, start_date: str, end_date: str = None) -> pd.DataFrame:
#       try:
#           raw = some_library.get(series_id, start=start_date, end=end_date)
#       except Exception as exc:
#           raise FetchError(f"Request failed for '{series_id}': {exc}") from exc
#
#       if raw is None or raw.empty:
#           raise FetchError(f"No data returned for '{series_id}'")
#
#       df = raw.rename(...)[["date", "value"]]
#       df["value"] = pd.to_numeric(df["value"], errors="coerce")
#       df = df.dropna(subset=["value"]).sort_values("date").reset_index(drop=True)
#       return df


class FetchError(Exception):
    """
    Raised when a fetcher cannot retrieve or parse data.

    Covers: missing credentials, network failures, HTTP errors,
    unexpected response schemas, and empty results.
    Always chain the original exception: raise FetchError("...") from exc
    """
