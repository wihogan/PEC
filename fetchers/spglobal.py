# spglobal.py — Fetches NTM forward consensus operating EPS from the
# S&P Global publicly-hosted EPS estimates spreadsheet.
#
# Source URL:
#   https://www.spglobal.com/spdji/en/documents/additional-material/sp-500-eps-est.xlsx
#
# Refresh cadence: weekly during earnings season, monthly otherwise.
#
# This fetcher's only series_id is 'forward_eps_ntm'. The start_date and end_date
# parameters are accepted for interface compatibility but ignored — the S&P xlsx
# is a point-in-time snapshot, not a historical series.
#
# The returned DataFrame contains exactly one row:
#   date  = effective date of the estimate (today if parsed live)
#   value = NTM operating EPS in $/share (sum of next 4 quarterly estimates)
#
# Expected range: $250–$370 as of Q1 2026.
#
# Fallback chain
# --------------
# 1. Download the live xlsx (7-day TTL cache). Parse defensively.
# 2. If live parse fails, load .cache/forward_eps_ntm.json (last successful parse).
# 3. If both fail, raise FetchError.
#
# Install: pip install openpyxl requests pandas  (openpyxl ships with pandas extras)

from __future__ import annotations

import calendar
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

import yaml

from .base import FetchError

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_URL = (
    "https://www.spglobal.com/spdji/en/documents/additional-material/"
    "sp-500-eps-est.xlsx"
)
_CACHE_DIR      = Path(__file__).parent.parent / ".cache"
_CACHE_XLSX     = _CACHE_DIR / "sp500_eps_est.xlsx"
_CACHE_JSON     = _CACHE_DIR / "forward_eps_ntm.json"
_HISTORY_JSON   = _CACHE_DIR / "forward_eps_ntm_history.json"
_SEED_YAML      = Path(__file__).parent.parent / "config" / "forward_eps_seed.yaml"
_CACHE_MAX_AGE_DAYS = 7

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*"
    ),
    "Referer": "https://www.spglobal.com/spdji/en/",
}

# Row-label keywords that identify an operating EPS row, in priority order.
# "operating" is preferred; fall back to generic EPS labels if not found.
_EPS_KEYWORDS_PRIORITY = [
    "operating eps",
    "bottom-up operating",
    "bottom up operating",
    "operating earnings per share",
    "as reported eps",
    "reported eps",
    "earnings per share",
    "eps estimate",
    "eps",
]

# Quarter regex: matches "Q1", "1Q", "Q2-24", "3Q25", "Q4 2026", etc.
# Used to identify header rows and parse quarter labels.
_QUARTER_RE = re.compile(
    r"\b([1-4]Q|Q[1-4])\s*[-\'.]?\s*(\d{2,4})?\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# History cache helpers
# ---------------------------------------------------------------------------

def _load_history_entries() -> list:
    """
    Load existing monthly entries from .cache/forward_eps_ntm_history.json.
    Returns an empty list if the file is absent or malformed.
    """
    if not _HISTORY_JSON.exists():
        return []
    try:
        data = json.loads(_HISTORY_JSON.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        # Validate structure: each entry must have 'date' and 'value'
        return [e for e in entries if "date" in e and "value" in e]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        log.warning("[spglobal] Malformed history cache: %s — treating as empty", exc)
        return []


def _save_history_entries(entries: list) -> None:
    """Persist the full history list to .cache/forward_eps_ntm_history.json."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_updated": datetime.now().isoformat(timespec="seconds"),
        "entry_count":  len(entries),
        "entries":      entries,
    }
    _HISTORY_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("[spglobal] History saved: %d monthly entries", len(entries))


def _last_business_day_of_month(year: int, month: int) -> date:
    """Return the last weekday (Mon–Fri) of the given month."""
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() >= 5:   # 5 = Saturday, 6 = Sunday
        d = date(year, month, d.day - 1)
    return d


def _seed_history_from_yaml() -> list:
    """
    Load seed entries from config/forward_eps_seed.yaml.
    Returns a list of {date: str, value: float} dicts, or [] on failure.
    """
    if not _SEED_YAML.exists():
        log.warning("[spglobal] Seed file not found: %s", _SEED_YAML)
        return []
    try:
        with open(_SEED_YAML, "r", encoding="utf-8") as fh:
            seed = yaml.safe_load(fh)
        raw_entries = seed.get("entries", [])
        result = []
        for e in raw_entries:
            try:
                result.append({
                    "date":  str(e["date"]),
                    "value": round(float(e["value"]), 2),
                })
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("[spglobal] Skipping malformed seed entry %s: %s", e, exc)
        log.info("[spglobal] Loaded %d entries from seed file", len(result))
        return result
    except Exception as exc:
        log.warning("[spglobal] Failed to load seed file %s: %s", _SEED_YAML, exc)
        return []


def _accumulate_current_month(entries: list, current_value: float) -> tuple[list, bool]:
    """
    Append an entry for the current month if one doesn't already exist.

    Returns (updated_entries, was_modified).
    The entry date is the last business day of the current month.
    Idempotent within a calendar month.
    """
    today = date.today()

    # Check whether this month already has an entry
    for entry in entries:
        try:
            entry_date = date.fromisoformat(str(entry["date"]))
            if entry_date.year == today.year and entry_date.month == today.month:
                log.info(
                    "[spglobal] Current month (%d-%02d) already in history — skipping accumulation",
                    today.year, today.month,
                )
                return entries, False
        except (ValueError, KeyError):
            continue

    # Add new entry for this month
    last_biz = _last_business_day_of_month(today.year, today.month)
    new_entry = {
        "date":  last_biz.isoformat(),
        "value": round(current_value, 2),
    }
    updated = sorted(entries + [new_entry], key=lambda e: e["date"])
    log.info(
        "[spglobal] Accumulated new history entry: %s = $%.2f",
        new_entry["date"], current_value,
    )
    return updated, True


def _write_back_to_seed(entries: list) -> None:
    """
    Overwrite config/forward_eps_seed.yaml with the current full history list.

    Called whenever a new monthly entry is accumulated so the seed file stays
    current and can always regenerate the cache from scratch if .cache/ is lost.
    Never raises — failures are logged and silently skipped.
    """
    try:
        # Read existing seed to preserve the header comment block
        if _SEED_YAML.exists():
            existing_text = _SEED_YAML.read_text(encoding="utf-8")
            # Keep everything up to (and including) the blank line before 'entries:'
            header_end = existing_text.find("\nentries:")
            header = existing_text[:header_end] if header_end != -1 else ""
        else:
            header = ""

        lines = ["\nentries:\n"]
        for e in entries:
            lines.append(f'  - {{date: "{e["date"]}", value: {e["value"]}}}\n')

        _SEED_YAML.write_text(header + "".join(lines), encoding="utf-8")
        log.info("[spglobal] Seed file updated: %d entries written to %s", len(entries), _SEED_YAML.name)
    except Exception as exc:
        log.warning("[spglobal] Seed write-back failed: %s — cache is still current", exc)


def _ensure_history(current_value: float) -> None:
    """
    Ensure .cache/forward_eps_ntm_history.json is populated and up to date.

    Flow:
    1. Load existing history (empty list if cache absent).
    2. If empty, bootstrap from config/forward_eps_seed.yaml.
    3. Accumulate the current month if not already present.
    4. Save back to cache.
    5. If a new entry was added, write back to seed file so it stays current.

    Called as a side effect of every successful current-point fetch.
    Never raises — all failures are logged and silently skipped.
    """
    try:
        entries = _load_history_entries()

        if not entries:
            log.info(
                "[spglobal] History cache empty — bootstrapping from seed file"
            )
            entries = _seed_history_from_yaml()

        entries, modified = _accumulate_current_month(entries, current_value)

        if modified or not _HISTORY_JSON.exists():
            _save_history_entries(entries)
            if modified:
                _write_back_to_seed(entries)
        else:
            log.info("[spglobal] History cache already current — no write needed")

    except Exception as exc:
        log.warning(
            "[spglobal] _ensure_history raised unexpectedly: %s — skipping",
            exc,
        )


def record_month_end(as_of: date, value: float) -> bool:
    """
    Upsert the month-end history entry for as_of's month (FactSet automation).

    - Entry date is the CALENDAR month-end (matches config/forward_eps_seed.yaml
      convention, e.g. 2026-10-31). An existing entry for that year-month keeps its
      own date key and only has its value replaced (latest report of the month wins).
    - Seed entries for months missing from the cache are merged in first, so adding
      backfill rows to the seed also reaches an existing cache.
    - Only the history cache is written (persisted on the state branch by
      tools/state.sh). The seed file is NOT rewritten: write-back strips its comments
      and the CI runner discards it anyway.
    Returns True if the history cache was modified. Never raises.
    """
    try:
        entries = _load_history_entries()
        have = {str(e["date"])[:7] for e in entries}
        modified = False
        for e in _seed_history_from_yaml():
            if e["date"][:7] not in have:
                entries.append(e)
                have.add(e["date"][:7])
                modified = True  # seed rows must reach an existing cache (D3)
        key = f"{as_of.year}-{as_of.month:02d}"
        val = round(float(value), 2)
        for e in entries:
            if str(e["date"])[:7] == key:
                if abs(float(e["value"]) - val) > 1e-9:
                    e["value"] = val
                    modified = True
                break
        else:
            last_day = calendar.monthrange(as_of.year, as_of.month)[1]
            entries.append({"date": date(as_of.year, as_of.month, last_day).isoformat(), "value": val})
            modified = True
        entries.sort(key=lambda e: str(e["date"]))
        if modified or not _HISTORY_JSON.exists():
            _save_history_entries(entries)
        return modified
    except Exception as exc:
        log.warning("[spglobal] record_month_end failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Point-in-time cache helpers (existing from Stage 3)
# ---------------------------------------------------------------------------
# xlsx cache helpers (existing from Stage 3)
# ---------------------------------------------------------------------------

def _xlsx_is_fresh() -> bool:
    if not _CACHE_XLSX.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(_CACHE_XLSX.stat().st_mtime)
    return age < timedelta(days=_CACHE_MAX_AGE_DAYS)


def _download_xlsx() -> None:
    """Download the S&P EPS xlsx to .cache/. Creates the directory if needed."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log.info("[spglobal] Downloading EPS xlsx from S&P Global...")
    try:
        resp = requests.get(_URL, headers=_HEADERS, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError(
            f"Failed to download S&P EPS xlsx from {_URL}: {exc}"
        ) from exc

    _CACHE_XLSX.write_bytes(resp.content)
    log.info("[spglobal] Downloaded EPS xlsx (%d KB)", len(resp.content) // 1024)


def _save_json_cache(value: float, effective_date: date) -> None:
    """Persist the last successful parse for future fallback."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "value":          round(value, 2),
        "effective_date": effective_date.isoformat(),
        "parsed_at":      datetime.now().isoformat(timespec="seconds"),
    }
    _CACHE_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("[spglobal] Cached parse result to %s", _CACHE_JSON.name)


def _load_json_cache() -> tuple[float, date]:
    """
    Load the last successful parse from .cache/forward_eps_ntm.json.
    Raises FetchError if the file is absent or malformed.
    """
    if not _CACHE_JSON.exists():
        raise FetchError(
            "No EPS cache available — .cache/forward_eps_ntm.json not found"
        )
    try:
        payload = json.loads(_CACHE_JSON.read_text(encoding="utf-8"))
        value          = float(payload["value"])
        effective_date = date.fromisoformat(payload["effective_date"])
        parsed_at      = payload.get("parsed_at", "unknown")
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise FetchError(
            f"Malformed .cache/forward_eps_ntm.json: {exc}"
        ) from exc

    log.warning(
        "[spglobal] Using cached EPS %.2f (effective %s, parsed %s) "
        "— live fetch was unavailable",
        value, effective_date, parsed_at,
    )
    return value, effective_date


# ---------------------------------------------------------------------------
# Quarter-label parsing
# ---------------------------------------------------------------------------

def _parse_quarter_end_date(label) -> Optional[date]:
    """
    Convert a quarter label to the last calendar day of that quarter.

    Handles string formats:
        "Q1 2024", "Q1-24", "Q1'24", "1Q24", "1Q 2024",
        "Q1-24E", "Q1-24A", "Q3 2025"

    Also handles pandas Timestamp / datetime objects (returns last day of
    that object's quarter), and raw integers/floats that look like years
    (returns None — not a quarter label).

    Returns None for anything that can't be parsed as a quarter.
    """
    # Already a date-like object — derive its quarter-end
    if isinstance(label, (pd.Timestamp, datetime)):
        q = (label.month - 1) // 3 + 1
        last_month = q * 3
        last_day = calendar.monthrange(label.year, last_month)[1]
        return date(label.year, last_month, last_day)

    if not isinstance(label, str):
        return None

    label_clean = str(label).strip()

    # Strip trailing annotation chars: "E" (estimate), "A" (actual), "*", " "
    label_clean = re.sub(r"[EA*\s]+$", "", label_clean, flags=re.IGNORECASE)

    m = re.search(
        r"([1-4])Q\s*[-\'.]?\s*(\d{2,4})|Q([1-4])\s*[-\'.]?\s*(\d{2,4})",
        label_clean,
        re.IGNORECASE,
    )
    if not m:
        return None

    if m.group(1):     # e.g. "1Q24" or "1Q 2024"
        qnum     = int(m.group(1))
        year_str = m.group(2)
    else:              # e.g. "Q1 2024" or "Q1-24"
        qnum     = int(m.group(3))
        year_str = m.group(4)

    if not year_str:
        return None

    year = int(year_str)
    if year < 100:
        year += 2000 if year < 70 else 1900

    last_month = qnum * 3
    last_day   = calendar.monthrange(year, last_month)[1]
    try:
        return date(year, last_month, last_day)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# xlsx parsing
# ---------------------------------------------------------------------------

def _count_quarter_cols(row_values) -> int:
    """Return the number of column values in a row that parse as quarter labels."""
    return sum(
        1 for v in row_values if _parse_quarter_end_date(v) is not None
    )


def _find_eps_row_index(df: pd.DataFrame) -> Optional[int]:
    """
    Scan df row-by-row for the best Operating EPS row.

    Checks the first 5 columns of each row for a label matching the priority
    list. Returns the index of the highest-priority match found, or None.
    """
    best_priority = len(_EPS_KEYWORDS_PRIORITY)
    best_idx      = None

    for row_idx in range(min(len(df), 150)):
        row = df.iloc[row_idx]
        label_cells = [str(v).lower().strip() for v in row.values[:5] if pd.notna(v)]

        for label in label_cells:
            for pri, keyword in enumerate(_EPS_KEYWORDS_PRIORITY):
                if keyword in label and pri < best_priority:
                    best_priority = pri
                    best_idx      = row_idx
                    break

        # "operating eps" is top priority — stop as soon as we find it
        if best_priority == 0:
            break

    return best_idx


def _try_parse_sheet(
    sheet_name: str,
    raw: pd.DataFrame,
) -> Optional[tuple[float, int]]:
    """
    Attempt to extract NTM operating EPS from a raw (header=None) sheet DataFrame.

    Returns (ntm_eps, n_estimate_quarters) if successful, else None.

    Strategy
    --------
    1. Scan the first 25 rows for one that contains ≥ 2 quarter-pattern tokens.
       That row becomes the column-header row.
    2. Re-read the sheet with that row as the header.
    3. Map each column to a quarter-end date.
    4. Find the best Operating EPS row by scanning row labels.
    5. Collect EPS values for columns whose quarter-end date > today (estimates).
    6. Sum the next 4 such quarters as NTM EPS.
    """
    today = date.today()

    # --- Step 1: find the header row ----------------------------------------
    header_row_idx = None
    for i in range(min(25, len(raw))):
        row_vals = list(raw.iloc[i].values)
        if _count_quarter_cols(row_vals) >= 2:
            header_row_idx = i
            break

    if header_row_idx is None:
        return None

    # --- Step 2: re-read with correct header --------------------------------
    # We already have the raw DataFrame, so slice instead of re-reading disk.
    # New column names = header row; data starts one row below.
    col_names = list(raw.iloc[header_row_idx].values)
    data_df   = raw.iloc[header_row_idx + 1 :].copy()
    data_df.columns = col_names
    data_df   = data_df.reset_index(drop=True)

    # --- Step 3: map columns → quarter dates --------------------------------
    quarter_col_dates: dict[int, date] = {}   # column position → quarter-end date
    for col_pos, col_name in enumerate(data_df.columns):
        qdate = _parse_quarter_end_date(col_name)
        if qdate is not None:
            quarter_col_dates[col_pos] = qdate

    if len(quarter_col_dates) < 2:
        return None

    # --- Step 4: find the EPS row -------------------------------------------
    eps_row_idx = _find_eps_row_index(data_df)
    if eps_row_idx is None:
        return None

    eps_row = data_df.iloc[eps_row_idx]

    # --- Step 5: collect future-quarter EPS values --------------------------
    future_quarters: list[tuple[date, float]] = []

    for col_pos, qdate in quarter_col_dates.items():
        if qdate <= today:
            continue  # actual — skip
        col_name = data_df.columns[col_pos]
        try:
            raw_val = eps_row.iloc[col_pos]
            val     = float(raw_val)
            if val > 0:
                future_quarters.append((qdate, val))
        except (TypeError, ValueError):
            continue

    if not future_quarters:
        log.debug("[spglobal] Sheet '%s': no future-quarter EPS values found", sheet_name)
        return None

    # Sort by quarter date; take the 4 nearest upcoming quarters
    future_quarters.sort(key=lambda x: x[0])
    ntm_quarters = future_quarters[:4]

    ntm_eps = round(sum(v for _, v in ntm_quarters), 2)
    log.debug(
        "[spglobal] Sheet '%s': NTM EPS = $%.2f from %d quarters",
        sheet_name, ntm_eps, len(ntm_quarters),
    )
    return ntm_eps, len(ntm_quarters)


# ---------------------------------------------------------------------------
# Public fetch() — called by refresh.py orchestrator
# ---------------------------------------------------------------------------

def fetch(series_id: str, **kwargs) -> pd.DataFrame:
    """
    Fetch NTM forward operating EPS for the S&P 500.

    series_id is always 'forward_eps_ntm'; start_date / end_date kwargs
    are accepted for interface compatibility and ignored.

    Pass history=True to return the full monthly history as a multi-row
    DataFrame instead of the single current-point value. Loads from
    .cache/forward_eps_ntm_history.json, falling back to the seed file.

    Fallback chain (point-in-time)
    --------------
    1. S&P Global EPS xlsx (live download, 7-day cache).
    2. FactSet Earnings Insight PDF (factset_eps.py).
    3. .cache/forward_eps_ntm.json (last successful parse of any source).
    4. FetchError → triggers manual_override in refresh.py orchestrator.

    Returns a one-row DataFrame: {"date": pd.Timestamp, "value": float}
    Raises FetchError if all paths are exhausted.
    """
    if kwargs.get("history"):
        entries = _load_history_entries()
        if not entries:
            entries = _seed_history_from_yaml()
        if not entries:
            return pd.DataFrame(columns=["date", "value"])
        df = pd.DataFrame(entries)
        df["date"]  = pd.to_datetime(df["date"])
        df["value"] = df["value"].astype(float)
        return df.sort_values("date").reset_index(drop=True)

    value: Optional[float] = None
    effective_date: Optional[date] = None
    live_error_msg: str = ""

    # ----------------------------------------------------------------
    # Path 1: S&P Global xlsx (usually 403 from CDN; kept for future use)
    # ----------------------------------------------------------------
    try:
        if not _xlsx_is_fresh():
            _download_xlsx()

        xl = pd.ExcelFile(_CACHE_XLSX)
        best_result: Optional[tuple[float, int]] = None

        for sheet in xl.sheet_names:
            try:
                raw = xl.parse(sheet, header=None)
                result = _try_parse_sheet(sheet, raw)
                if result is not None:
                    eps, n_quarters = result
                    if best_result is None or n_quarters > best_result[1]:
                        best_result = (eps, n_quarters)
            except Exception as exc:
                log.debug("[spglobal] Sheet '%s' parse error: %s", sheet, exc)

        if best_result is not None:
            value, n_q = best_result
            effective_date = date.today()
            _save_json_cache(value, effective_date)
            log.info(
                "[spglobal] Live parse OK: NTM EPS = $%.2f (%d quarters)",
                value, n_q,
            )

    except FetchError as exc:
        live_error_msg = str(exc)
        log.warning("[spglobal] Live parse failed (%s) — trying FactSet", exc)
    except Exception as exc:
        live_error_msg = str(exc)
        log.warning(
            "[spglobal] Live parse raised unexpectedly (%s) — trying FactSet", exc
        )

    # ----------------------------------------------------------------
    # Path 2: FactSet Earnings Insight PDF (factset_eps.py)
    # ----------------------------------------------------------------
    if value is None:
        try:
            from .factset_eps import fetch_forward_eps  # local import to avoid circular
            result = fetch_forward_eps()
            if result is not None:
                value = float(result["value"])
                as_of_str = result.get("as_of", date.today().isoformat())
                effective_date = date.fromisoformat(as_of_str)
                log.info(
                    "[spglobal] FactSet: NTM EPS = $%.2f  source=%s  as_of=%s",
                    value, result.get("source", "?"), as_of_str,
                )
        except Exception as exc:
            log.warning("[spglobal] FactSet fetch failed: %s", exc)

    # ----------------------------------------------------------------
    # Path 3: JSON cache (last successful parse of any upstream source)
    # ----------------------------------------------------------------
    if value is None:
        try:
            value, effective_date = _load_json_cache()
        except FetchError as exc:
            log.warning("[spglobal] JSON cache also unavailable: %s", exc)

    # ----------------------------------------------------------------
    # All paths exhausted → let refresh.py fall through to manual_override
    # ----------------------------------------------------------------
    if value is None:
        raise FetchError(
            f"S&P EPS fetch failed entirely. "
            f"Live error: {live_error_msg}. "
            f"Cache error: No EPS cache available — .cache/forward_eps_ntm.json not found."
        )

    # Ensure history is seeded and current month accumulated (idempotent)
    _ensure_history(value)

    # Return one-row DataFrame matching the fetcher contract
    return pd.DataFrame([{
        "date":  pd.Timestamp(effective_date),
        "value": value,
    }])
