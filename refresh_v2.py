#!/usr/bin/env python3
"""
refresh.py — Preston Macro Dashboard orchestrator.

Loads config/series.yaml, calls each fetcher, applies any declared
transforms, and writes data.json to the dashboard root.

Usage (from the dashboard/ directory with the venv activated):

    python refresh.py                        # fetch all series
    python refresh.py --series sp500 dff     # fetch specific series only

data.json schema
----------------
{
  "metadata": {
    "last_refreshed":  "2026-04-29T07:30:00",
    "series_count":    23,
    "success_count":   22,
    "failure_count":   1,
    "statuses": {
      "sp500":             {"status": "ok"},
      "cpi_yoy":           {"status": "error", "error": "...message..."},
      "forward_eps_ntm":   {"status": "ok"},
      ...
    }
  },
  "series": {
    "sp500":      {"dates": ["2010-01-04", ...], "values": [1132.99, ...]},
    "yield_curve": {
      "dates":  ["2010-01-04", ...],
      "tenors": {
        "1mo":  [0.04, ...],
        "10yr": [3.85, ...],
        ...
      }
    },
    ...
  },
  "valuation_inputs": {
    "forward_eps_ntm": {
      "value":          336.00,
      "effective_date": "2026-04-25",
      "source":         "live"     // "live" | "override" | "unavailable"
    }
  }
}

The yield_curve entry is the only structural exception to the flat
{dates, values} contract. All other series use the flat form, which
is what uPlot expects for a standard time-series chart.

The valuation_inputs block holds point-in-time scalars for derived
calculations (e.g. Wien DDM). Stage 4 reads from this block; the
"source" field tells Stage 4 whether to render normally ("live"),
with a staleness indicator ("override"), or as an empty state
("unavailable").

The --series filter applies only to the main series list. Valuation
inputs are always processed (they are fast and needed for Stage 4).
    python refresh.py --series cpi_yoy
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8")

import yaml

import market_calendar

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")

# ---------------------------------------------------------------------------
# Paths — resolved relative to this script so the file can be run from any cwd.
# ---------------------------------------------------------------------------
HERE = Path(__file__).parent.resolve()
CONFIG_PATH = HERE / "config" / "series_v2.yaml"
OUTPUT_PATH = HERE / "data_v2.json"
BACKUP_DIR  = HERE / "backups"
BACKUP_RETENTION_DAYS = 14

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fetcher registry
# ---------------------------------------------------------------------------
def _load_fetchers() -> dict:
    """
    Import all fetcher modules and return a source-name → module dict.
    Importing here (rather than at module level) means a broken fetcher
    surfaces as a clear ImportError rather than a silent AttributeError later.
    """
    # Ensure 'fetchers/' is importable from the dashboard directory.
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))

    from fetchers import fred, yahoo, treasury, shiller, spglobal, multpl, factset_eps  # noqa: PLC0415

    return {
        "fred":     fred,
        "yahoo":    yahoo,
        "treasury": treasury,
        "shiller":  shiller,
        "spglobal": spglobal,
        "multpl":   multpl,
        "factset_eps": factset_eps,
    }


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------
def _load_config(path: Path) -> dict:
    """
    Load series.yaml and return the full config dict.

    Expected top-level keys:
        series           list of time-series definitions
        valuation_inputs dict of point-in-time scalar inputs (optional)
    """
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    # series.yaml is now a mapping with a 'series' key.
    if not isinstance(raw, dict):
        raise ValueError(
            f"series.yaml must be a YAML mapping with a 'series' key, "
            f"got {type(raw).__name__}. "
            f"Ensure the file starts with 'series:' at the top level."
        )

    if "series" not in raw:
        raise ValueError(
            "series.yaml is missing the required 'series' key. "
            "All time-series definitions must be nested under 'series:'."
        )

    return raw


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------
def _safe_float(v: Any) -> Optional[float]:
    """
    Return a JSON-safe float rounded to 4 decimal places, or None for NaN/None.
    Four decimal places is sufficient for yield levels (e.g. 4.3750) and
    equity prices (rounding has no material impact on chart rendering).
    """
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else round(f, 4)
    except (TypeError, ValueError):
        return None


def _dates_to_strings(df) -> list[str]:
    """Convert the 'date' column of a DataFrame to ISO-8601 date strings."""
    return df["date"].dt.strftime("%Y-%m-%d").tolist()


# ---------------------------------------------------------------------------
# Per-series fetch logic
# ---------------------------------------------------------------------------
def _fetch_standard(entry: dict, fetchers: dict) -> dict:
    """
    Fetch any two-column (date, value) series and return the flat
    {dates, values} dict expected by uPlot.

    Handles the yoy_pct flag by passing transform='pct_change_12m' to
    fred.fetch() — the flag is meaningful only for monthly FRED series.
    """
    source = entry["source"]
    module = fetchers[source]

    kwargs: dict = {
        "series_id":  entry["series_id"],
        "start_date": entry.get("start_date", "2010-01-01"),
    }

    # Translate the YAML semantic flag to the fred.fetch() parameter.
    if source == "fred" and entry.get("yoy_pct"):
        kwargs["transform"] = "pct_change_12m"

    # Pass fetcher-specific extra parameters declared in the YAML entry.
    # 'field' routes yahoo.py entries to the Ticker.info path (e.g. dividendYield).
    for extra_key in ("field",):
        if entry.get(extra_key) is not None:
            kwargs[extra_key] = entry[extra_key]

    df = module.fetch(**kwargs)

    return {
        "dates":  _dates_to_strings(df),
        "values": [_safe_float(v) for v in df["value"].tolist()],
    }


def _fetch_yield_curve(entry: dict, fetchers: dict) -> dict:
    """
    Fetch the Treasury yield curve (three-column long-form: date, tenor, value)
    and pivot it into the tenor-keyed structure below.

    Output shape:
        {
            "dates":  ["2010-01-04", ...],          # union of all dates present
            "tenors": {
                "1mo":  [0.04, null, ...],           # aligned to dates; null = no quote
                "10yr": [3.85, 3.91, ...],
                ...
            }
        }

    dates and tenors[t] are aligned: tenors[t][i] is the yield for tenor t on
    date dates[i]. Missing observations (e.g. a tenor not quoted on a given date)
    are represented as null so uPlot can handle gaps cleanly.
    """
    module = fetchers["treasury"]

    df = module.fetch(
        series_id=entry["series_id"],
        start_date=entry.get("start_date", "2010-01-01"),
    )

    # Pivot long-form → wide: index=date, columns=tenor, values=yield
    pivot = df.pivot(index="date", columns="tenor", values="value").sort_index()

    dates = pivot.index.strftime("%Y-%m-%d").tolist()

    tenors: dict = {}
    for tenor in pivot.columns:
        tenors[str(tenor)] = [_safe_float(v) for v in pivot[tenor].tolist()]

    return {"dates": dates, "tenors": tenors}


# ---------------------------------------------------------------------------
# Valuation inputs fetch logic
# ---------------------------------------------------------------------------
def _eps_mode() -> str:
    """
    Resolve the forward-EPS mode: 'override' | 'factset' | '' (legacy live ladder).

    PEC_EPS_MODE wins. An unrecognised PEC_EPS_MODE fails safe to 'override'.
    PEC_EPS_OVERRIDE_ONLY=1 (backward compatible) means 'override'.
    """
    raw = os.environ.get("PEC_EPS_MODE", "").strip().lower()
    if raw in ("override", "factset"):
        return raw
    if raw:
        log.warning("PEC_EPS_MODE=%r not recognised (override|factset) — using 'override'", raw)
        return "override"
    if os.environ.get("PEC_EPS_OVERRIDE_ONLY") == "1":
        return "override"
    return ""


def _parse_iso_date(v) -> Optional[date]:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


EPS_CHANGE_FLAG = HERE / "eps_change.txt"
EPS_CHANGE_THRESHOLD = 0.03


def _write_eps_change_flag(prev: Optional[dict], new: Optional[dict]) -> None:
    """
    Write eps_change.txt (read by the refresh workflow's Telegram step) when the forward
    EPS source switched or the value moved more than 3 percent vs the previous state.
    Removes any stale flag first. Never raises.
    """
    try:
        EPS_CHANGE_FLAG.unlink(missing_ok=True)
        if not new or new.get("value") is None or not prev or prev.get("value") is None:
            return
        old_v, new_v = float(prev["value"]), float(new["value"])
        if round(old_v, 2) == round(new_v, 2):
            return  # same value (e.g. FactSet -> identical override): nothing to report
        switched = prev.get("source") != new.get("source")
        moved = old_v > 0 and abs(new_v - old_v) / old_v > EPS_CHANGE_THRESHOLD
        if not (switched or moved):
            return
        label = {"factset": "FactSet", "override": "manual override"}.get(new.get("source"), str(new.get("source")))
        as_of = new.get("as_of") or new.get("effective_date")
        EPS_CHANGE_FLAG.write_text(
            f"\u2139\ufe0f PEC forward EPS updated: {old_v:.2f} \u2192 {new_v:.2f} ({label}, as of {as_of})\n",
            encoding="utf-8",
        )
        log.info("EPS change flag written: %.2f -> %.2f (%s)", old_v, new_v, label)
    except Exception as exc:  # pragma: no cover
        log.warning("Could not write EPS change flag: %s", exc)


def _append_factset_notice(fetchers: dict) -> None:
    """
    Append the once-per-condition FactSet health notice (2 consecutive failing PT days,
    or as_of older than 10 days) to eps_change.txt so the workflow sends it after the
    healthcheck. Only in PEC_EPS_MODE=factset. Never raises.
    """
    try:
        if _eps_mode() != "factset":
            return
        fs_mod = fetchers.get("factset_eps")
        if fs_mod is None or not hasattr(fs_mod, "pending_notice"):
            return
        text = fs_mod.pending_notice()
        if text:
            with open(EPS_CHANGE_FLAG, "a", encoding="utf-8") as fh:
                fh.write(text.rstrip("\n") + "\n")
            log.info("FactSet notice queued: %s", text.strip().splitlines()[0])
    except Exception as exc:  # pragma: no cover
        log.warning("Could not append FactSet notice: %s", exc)


def _fetch_valuation_inputs(
    vi_config: dict,
    fetchers:  dict,
    statuses:  dict,
    previous:  Optional[dict] = None,
    now_ts:    Optional[str] = None,
) -> dict:
    """
    Process all entries under config['valuation_inputs'].

    For each entry, the fallback ladder is:
        1. Live fetcher (fetcher module specified in the entry)
        2. manual_override (if both value and effective_date are non-null)
        3. 'unavailable' record (never crashes the refresh)

    The 'source' field in each output record is the contract Stage 4 reads:
        'live'        — fetched from the live source (legacy mode, no PEC_EPS_MODE)
        'factset'     — FactSet Earnings Insight (PEC_EPS_MODE=factset), used only when
                        its as_of date is NEWER than the manual_override effective_date
        'override'    — used the manual_override from series.yaml
        'unavailable' — neither source produced a value

    PEC_EPS_MODE=override  -> manual_override only.
    PEC_EPS_MODE=factset   -> FactSet when newer than the override; otherwise (or on
                              failure) the previous state's FactSet value if it is
                              still newer than the override, else the override.
    PEC_EPS_OVERRIDE_ONLY=1 is kept as an alias of PEC_EPS_MODE=override.

    Parameters
    ----------
    vi_config : dict mapping entry_id → entry dict (from series.yaml)
    fetchers  : fetcher module registry
    statuses  : shared statuses dict — entries are written here for metadata

    Returns
    -------
    dict mapping entry_id → {value, effective_date, source}
    """
    vi_out: dict = {}

    for vi_id, vi_entry in vi_config.items():
        fetcher_name = vi_entry.get("fetcher", "")
        series_id    = vi_entry.get("series_id", vi_id)

        log.info(
            "[%s] fetching valuation input via '%s' (series_id=%s)",
            vi_id, fetcher_name, series_id,
        )

        value:          Optional[float] = None
        effective_date: Optional[str]   = None
        source:         str             = "unavailable"
        extras:         dict            = {}
        mode:           str             = _eps_mode() if vi_id == "forward_eps_ntm" else ""

        # ----------------------------------------------------------------
        # Attempt 1: live fetcher
        # ----------------------------------------------------------------
        live_error: Optional[Exception] = None
        fs_error:   Optional[str]       = None   # FactSet failure text (status under forward_eps_ntm)
        fetcher_module = fetchers.get(fetcher_name)

        if fetcher_module is None:
            live_error = ValueError(
                f"No fetcher registered for '{fetcher_name}'. "
                f"Add it to _load_fetchers() in refresh.py."
            )
            log.warning("[%s] %s", vi_id, live_error)
        elif mode == "override":
            log.info("[%s] PEC_EPS_MODE=override — skipping live fetch, using manual_override", vi_id)
        elif mode == "factset":
            fs_mod = fetchers.get("factset_eps")
            ov = vi_entry.get("manual_override") or {}
            ov_value = _safe_float(ov.get("value")) if ov.get("value") is not None else None
            ov_date  = _parse_iso_date(ov.get("effective_date"))
            prev_rec = (previous or {}).get(vi_id) or {}
            prev_value = _safe_float(prev_rec.get("value")) if prev_rec.get("value") is not None else None
            fs = None
            try:
                if fs_mod is None:
                    raise ValueError("factset_eps fetcher not registered")
                fs = fs_mod.fetch_forward_eps(
                    last_good=prev_value if prev_value else ov_value,
                    override=ov_value,
                )
            except Exception as exc:
                live_error = exc
                fs_error = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("[%s] FactSet fetch failed: %s", vi_id, exc)
            if fs is not None:
                fs_date = _parse_iso_date(fs.get("as_of"))
                if fs_date is not None and (ov_date is None or fs_date > ov_date):
                    value, effective_date, source = _safe_float(fs["value"]), fs_date.isoformat(), "factset"
                    extras = {"as_of": effective_date, "url": fs.get("url")}
                    log.info("[%s] Using FactSet — value=%s as_of=%s (override %s dated %s)",
                             vi_id, value, effective_date, ov_value, ov_date)
                else:
                    log.info("[%s] FactSet as_of %s is not newer than override %s — override wins",
                             vi_id, fs.get("as_of"), ov_date)
            elif (prev_rec.get("source") == "factset" and prev_value is not None
                  and _parse_iso_date(prev_rec.get("as_of")) is not None
                  and (ov_date is None or _parse_iso_date(prev_rec.get("as_of")) > ov_date)):
                value, effective_date, source = prev_value, str(prev_rec.get("as_of")), "factset"
                extras = {"as_of": effective_date, "url": prev_rec.get("url")}
                log.warning("[%s] FactSet unavailable — carrying forward previous FactSet value %s (as of %s)",
                            vi_id, value, effective_date)
        else:
            try:
                df             = fetcher_module.fetch(series_id)
                value          = _safe_float(df["value"].iloc[-1])
                effective_date = df["date"].iloc[-1].strftime("%Y-%m-%d")
                source         = "live"
                log.info(
                    "[%s] OK (live) — value=%s, effective_date=%s",
                    vi_id, value, effective_date,
                )
            except Exception as exc:
                live_error = exc
                log.warning(
                    "[%s] Live fetch failed: %s — checking manual_override",
                    vi_id, exc,
                )

        # ----------------------------------------------------------------
        # Attempt 2: manual override
        # ----------------------------------------------------------------
        if source not in ("live", "factset"):
            override = vi_entry.get("manual_override") or {}
            ov_value = override.get("value")
            ov_date  = override.get("effective_date")

            if ov_value is not None and ov_date is not None:
                value          = _safe_float(ov_value)
                effective_date = str(ov_date)
                source         = "override"
                if mode:
                    extras = {"as_of": effective_date, "url": None}
                log.info(
                    "[%s] Using manual_override — value=%s, effective_date=%s",
                    vi_id, value, effective_date,
                )
            else:
                # --------------------------------------------------------
                # Attempt 3: unavailable
                # --------------------------------------------------------
                log.warning(
                    "[%s] No manual_override populated — writing 'unavailable'. "
                    "Live error was: %s",
                    vi_id, live_error,
                )

        # ----------------------------------------------------------------
        # Month-end EPS history: whenever a forward EPS value is in effect (FactSet or
        # override). Ended months are dated the month-end; the in-progress month is
        # dated as_of/today (never in the future) and re-dated when the month ends.
        # ----------------------------------------------------------------
        if mode and source in ("factset", "override") and value is not None:
            sp_mod = fetchers.get("spglobal")
            eff_d = _parse_iso_date(effective_date)
            if sp_mod is not None and hasattr(sp_mod, "record_month_end") and eff_d is not None:
                today_pt = datetime.now(PT).date()
                sp_mod.record_month_end(eff_d, value)
                if (eff_d.year, eff_d.month) != (today_pt.year, today_pt.month):
                    sp_mod.record_month_end(today_pt, value)

        # ----------------------------------------------------------------
        # History block — try fetcher's history=True API; graceful on failure
        # ----------------------------------------------------------------
        history_block: Optional[dict] = None
        if source != "unavailable" and fetcher_module is not None:
            try:
                hist_df = fetcher_module.fetch(series_id, history=True)
                if not hist_df.empty:
                    history_block = {
                        "dates":  hist_df["date"].dt.strftime("%Y-%m-%d").tolist(),
                        "values": [_safe_float(v) for v in hist_df["value"].tolist()],
                    }
                    log.info(
                        "[%s] History block: %d monthly entries (%s → %s)",
                        vi_id,
                        len(history_block["dates"]),
                        history_block["dates"][0] if history_block["dates"] else "—",
                        history_block["dates"][-1] if history_block["dates"] else "—",
                    )
                else:
                    log.warning("[%s] History fetch returned empty DataFrame", vi_id)
            except TypeError:
                # Fetcher doesn't support history=True — not an error
                pass
            except Exception as hist_exc:
                log.warning("[%s] History fetch failed: %s — history will be null", vi_id, hist_exc)

        vi_out[vi_id] = {
            "value":          value,
            "effective_date": effective_date,
            "source":         source,
            "history":        history_block,
            **extras,
        }

        if source == "unavailable":
            statuses[vi_id] = {
                "status": "warning",
                "error":  str(live_error) if live_error else "no data source available",
            }
        elif fs_error:
            # FactSet failed (value carried forward / override in effect): record it.
            statuses[vi_id] = {"status": "error", "error": fs_error}
        else:
            statuses[vi_id] = {"status": "ok"}
        if now_ts and vi_id in statuses:
            statuses[vi_id]["last_attempt"] = now_ts

    return vi_out


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def _backup_data_json() -> None:
    """Copy data.json into backups/ with a timestamp suffix before overwriting."""
    if not OUTPUT_PATH.exists():
        return
    BACKUP_DIR.mkdir(exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"data_{ts}.json"
    dest.write_bytes(OUTPUT_PATH.read_bytes())
    log.info("Backup written: %s", dest.name)
    cutoff = datetime.now().timestamp() - BACKUP_RETENTION_DAYS * 86400
    for old in BACKUP_DIR.glob("data_*.json"):
        if old.stat().st_mtime < cutoff:
            old.unlink()
            log.info("Pruned old backup: %s", old.name)


def _market_is_open() -> bool:
    """True if the NYSE regular session is active now (thin wrapper over market_calendar)."""
    return market_calendar.market_is_open(datetime.now(ET))


def _last_completed_business_day() -> date:
    """Most recent trading day whose close has passed (thin wrapper over market_calendar)."""
    return market_calendar.last_completed_trading_day(datetime.now(ET))


def _last_observation_date(series_data: Optional[dict]) -> Optional[date]:
    """Date of the last non-null observation in a stored series, or None.

    Standard series: last index with a non-null value in `values`.
    yield_curve: last date where any tenor in `tenors` is non-null.
    """
    if not series_data:
        return None
    dates = series_data.get("dates") or []
    if "tenors" in series_data:
        cols = [c for c in (series_data.get("tenors") or {}).values() if c]
        for i in range(len(dates) - 1, -1, -1):
            if any(i < len(c) and c[i] is not None for c in cols):
                return date.fromisoformat(dates[i][:10])
        return None
    vals = series_data.get("values") or []
    for i in range(min(len(dates), len(vals)) - 1, -1, -1):
        if vals[i] is not None:
            return date.fromisoformat(dates[i][:10])
    return None


def _parse_last_fetched(text: str) -> datetime:
    """last_fetched is a naive ISO timestamp written in Pacific time; return it aware (PT)."""
    dt = datetime.fromisoformat(text)
    return dt.replace(tzinfo=PT) if dt.tzinfo is None else dt


def _safe_parse_stamp(text, sid: str = "?") -> Optional[datetime]:
    """Parse a status timestamp; None (with a warning) if absent or unparseable (m12)."""
    if not text:
        return None
    try:
        return _parse_last_fetched(str(text))
    except (TypeError, ValueError):
        log.warning("[%s] unparseable status timestamp %r - treating as never fetched", sid, text)
        return None


# Crypto trades around the clock (no session close; bar dates are UTC), so the
# "has the closing bar arrived" data test does not apply to these series.
CONTINUOUS_SERIES = frozenset({"btc_usd", "eth_usd", "xrp_usd"})

# Data-based re-fetch after the close is bounded: no re-fetch after this PT time
# on the trading day itself (the next market open fetches again anyway).
REFETCH_CUTOFF_PT = time(18, 0)


def _period_start(today: date, months: int) -> date:
    """First day of the most recently ended calendar period (month=1, quarter=3)."""
    if months == 1:
        first = today.replace(day=1)
        prev = first - timedelta(days=1)
        return prev.replace(day=1)
    q_first_month = 3 * ((today.month - 1) // 3) + 1
    q_start = date(today.year, q_first_month, 1)
    prev = q_start - timedelta(days=1)
    return date(prev.year, 3 * ((prev.month - 1) // 3) + 1, 1)


def _needs_refresh(
    sid: str,
    frequency: str,
    existing_statuses: dict,
    last_obs: Optional[date] = None,
    publish_lag_bd: int = 0,
    now: Optional[datetime] = None,
    session_series: bool = True,
) -> bool:
    """
    Return True if this series should be fetched on this run (Amendment B).

    existing_statuses : metadata.statuses of the prior data_v2.json, keyed by series id
                        ({status, last_fetched, last_attempt, error?}; stamps are naive PT ISO).
    last_obs          : last non-null observation date stored for the series
                        (see _last_observation_date); None if no data stored.
    publish_lag_bd    : daily_release only - trading days between an observation's
                        date and its publication (FRED 1, Treasury 0).
    now               : aware datetime override (tests / simulation); default now.
    session_series    : intraday only - False for series without a session close (crypto):
                        they keep the fetch-time rule after the close.

    Time zones: trading-day logic (open/close, last completed trading day) uses
    ET; calendar logic (once-per-day throttle, ended month/quarter) uses
    Pacific time, the zone of the stamps and of the CI runner (TZ=America/Los_Angeles).

    Rules after the close:
      * intraday, exchange-traded: satisfied only once last_obs >= last completed
        trading day. A successful fetch stamped after the close is not enough, because
        Yahoo can deliver the day's bar hours late. The data-based re-fetch stops at
        18:00 PT on that trading day, after which the fetch-time rule applies.
      * intraday crypto and daily_after_close (multpl): satisfied by fetch time
        (a successful fetch at/after the last completed close). multpl is deliberately
        not data-tested: its table is down/irregular and its row dates do not follow
        trading days, so a data test would poll it all evening.

    A series whose last status is "error" is never satisfied. Intraday,
    daily_after_close and daily_release series retry on every run; monthly and
    quarterly errors are throttled on last_attempt to at most one attempt per Pacific
    calendar day (and are retried even when the expected period is present).
    Monthly and quarterly series whose expected period is present and whose last status
    is ok are re-fetched once their last attempt (last_attempt, fallback last_fetched)
    is 7 or more Pacific calendar days old, to pick up revisions; otherwise skipped.
    An unparseable timestamp counts as never fetched.
    """
    now = now or datetime.now(PT)
    if now.tzinfo is None:
        now = now.replace(tzinfo=PT)  # a naive override is Pacific time, not the process zone
    now_et = now.astimezone(ET)
    now_pt = now.astimezone(PT)
    st = existing_statuses.get(sid, {}) or {}
    last = _safe_parse_stamp(st.get("last_fetched"), sid)
    errored = st.get("status") == "error"
    attempt = _safe_parse_stamp(st.get("last_attempt"), sid) or last

    if last is None and not (errored and attempt is not None):
        return True  # never fetched

    if frequency in ("intraday", "daily_after_close"):
        if frequency == "intraday" and market_calendar.market_is_open(now_et):
            return True
        if errored or last is None:
            return True
        lctd = market_calendar.last_completed_trading_day(now_et)
        if frequency == "intraday" and session_series and last_obs is not None:
            if last_obs >= lctd:
                return False
            if last < market_calendar.close_datetime_et(lctd):
                return True
            # Fetched after the close but the closing bar is missing: keep trying
            # until 18:00 PT on the trading day itself.
            lctd_pt = market_calendar.close_datetime_et(lctd).astimezone(PT).date()
            if now_pt.date() == lctd_pt and now_pt.time() < REFETCH_CUTOFF_PT:
                return True
            return False
        # satisfied once a fetch happened at/after the last completed close
        return last < market_calendar.close_datetime_et(lctd)

    if frequency == "daily_release":
        if errored or last_obs is None:
            return True
        lctd = market_calendar.last_completed_trading_day(now_et)
        expected = market_calendar.trading_days_before(lctd, publish_lag_bd)
        return last_obs < expected

    if frequency in ("monthly", "quarterly"):
        today = now_pt.date()
        if errored:
            # An errored series is never satisfied; retry at most once per PT day.
            return attempt is None or attempt.astimezone(PT).date() < today
        if last_obs is not None and last_obs >= _period_start(
            today, 1 if frequency == "monthly" else 3
        ):
            # Expected period present and last status ok: re-check weekly to pick
            # up revisions (owner decision 2026-11-24; QA m3). PT calendar days.
            return (today - (attempt or last).astimezone(PT).date()).days >= 7
        # Not yet satisfied: at most one attempt per PT calendar day.
        return (attempt or last).astimezone(PT).date() < today

    return True  # unknown frequency - always fetch


def render(output: dict) -> None:
    """Read PECtemplate.html, inject raw JSON, write PEC.html."""
    template_path = HERE / "PECtemplate.html"
    output_path   = HERE / "PEC.html"

    if not template_path.exists():
        log.warning("PECtemplate.html not found — skipping render step")
        return

    raw_json = json.dumps(output, separators=(",", ":"), ensure_ascii=False)

    html = template_path.read_text(encoding="utf-8")
    html = html.replace("__DASHBOARD_DATA__", raw_json)
    html = html.replace(
        "__GENERATED_AT__",
        datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    output_path.write_text(html, encoding="utf-8")
    log.info("Rendered PEC.html (%d KB)", output_path.stat().st_size // 1024)


def render_dev() -> None:
    """
    Produce PEC_dev.html — a lightweight version that loads data_v2.json at
    runtime instead of embedding it inline. Result is ~50 KB vs ~4.6 MB,
    which allows the preview screenshot tool to function during development.

    Requires a local HTTP server (preview.py) — cannot be opened via file://
    because fetch() is blocked on file:// URLs by all modern browsers.

    Usage: python refresh_v2.py --dev
    """
    template_path = HERE / "PECtemplate.html"
    output_path   = HERE / "PEC_dev.html"

    if not template_path.exists():
        log.warning("PECtemplate.html not found — skipping dev render")
        return

    html = template_path.read_text(encoding="utf-8")

    # Replace baked-in JSON with an empty placeholder so the sync parse path
    # in __initPEC() gets an empty object rather than a syntax error.
    html = html.replace("__DASHBOARD_DATA__", "{}")

    html = html.replace(
        "__GENERATED_AT__",
        datetime.now().strftime("%Y-%m-%d %H:%M") + " [dev]"
    )

    # Suppress the synchronous auto-init call; replace with an async loader
    # that fetches data_v2.json, populates the pec-data element, then calls
    # __initPEC() once data is ready.
    DEV_LOADER = (
        "/* DEV MODE: async data load — do not run synchronously */\n"
        "fetch('data_v2.json')\n"
        "  .then(function(r) { return r.json(); })\n"
        "  .then(function(d) {\n"
        "    document.getElementById('pec-data').textContent = JSON.stringify(d);\n"
        "    window.DASHBOARD_DATA = null;\n"
        "    window.__uplotInstances = [];\n"
        "    __initPEC();\n"
        "    // The 'load' event already fired before data was ready in dev mode;\n"
        "    // trigger chart render and control wiring manually.\n"
        "    if (window.DASHBOARD_DATA) { _renderAll(); _wireControls(); _watchResize(); }\n"
        "  })\n"
        "  .catch(function(e) {\n"
        "    console.error('[dev] Failed to load data_v2.json:', e);\n"
        "    console.error('[dev] Is the preview server running? (python preview.py)');\n"
        "  });"
    )
    html = html.replace("__initPEC();", DEV_LOADER)

    output_path.write_text(html, encoding="utf-8")
    log.info("Rendered PEC_dev.html (%d KB)", output_path.stat().st_size // 1024)

def _load_existing_data() -> dict:
    """Load the existing data.json if present; return empty dict on any failure."""
    if not OUTPUT_PATH.exists():
        return {}
    try:
        with open(OUTPUT_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        log.warning("Could not load existing data.json (%s) — treating as empty", exc)
        return {}


def _compute_derived_valuation(vi_out: dict, series_out: dict) -> None:
    """
    Mutates vi_out in-place with five derived keys:
        forward_pe_ntm    – spot S&P 500 / NTM consensus EPS
        ddm_fair_value    – single-stage Gordon DDM fair value
        erp               – ERP default (slider initial position)
        cape_delta_string – CAPE vs. trailing 5-yr average
        fwdpe_delta_string– Fwd P/E vs. trailing history average

    Gordon DDM: FV = (EPS × payout) / ((10yr + ERP − g) / 100)
    Constants: payout=35%, g=5% nominal long-run growth, ERP=3.5%
    """
    PAYOUT_RATIO  = 0.35
    LONG_RUN_G    = 5.0   # % nominal
    ERP_DEFAULT   = 3.5   # %

    # Convenience: last non-null value from a series
    def _last(sid: str) -> Optional[float]:
        s = series_out.get(sid)
        if not s:
            return None
        vals = [v for v in s.get("values", []) if v is not None]
        return vals[-1] if vals else None

    sp500_last  = _last("sp500")
    treas10_last = _last("treasury_10yr")
    fwd_eps_rec  = vi_out.get("forward_eps_ntm", {})
    fwd_eps      = fwd_eps_rec.get("value") if isinstance(fwd_eps_rec, dict) else None

    # ── Forward P/E ────────────────────────────────────────────────
    if sp500_last is not None and fwd_eps:
        vi_out["forward_pe_ntm"] = round(sp500_last / fwd_eps, 2)
        log.info("[derived] forward_pe_ntm = %.2f", vi_out["forward_pe_ntm"])

    # ── ERP default ────────────────────────────────────────────────
    vi_out["erp"] = ERP_DEFAULT

    # ── Gordon DDM fair value ───────────────────────────────────────
    if fwd_eps and treas10_last is not None:
        k = treas10_last + ERP_DEFAULT          # discount rate, %
        denom = (k - LONG_RUN_G) / 100.0
        if denom > 0:
            vi_out["ddm_fair_value"] = round((fwd_eps * PAYOUT_RATIO) / denom)
            log.info("[derived] ddm_fair_value = %d", vi_out["ddm_fair_value"])
        else:
            vi_out["ddm_fair_value"] = None
            log.warning("[derived] DDM denom <= 0 (k=%.2f%%, g=%.1f%%) — skipping", k, LONG_RUN_G)

    # ── CAPE delta string ───────────────────────────────────────────
    cape_series = series_out.get("shiller_cape")
    if cape_series:
        vals = [v for v in cape_series.get("values", []) if v is not None]
        if len(vals) >= 60:
            current = vals[-1]
            avg5y   = sum(vals[-60:]) / 60.0
            delta   = (current - avg5y) / avg5y * 100.0
            sign    = "+" if delta >= 0 else ""
            vi_out["cape_delta_string"] = f"{sign}{delta:.0f}% vs 5yr avg ({avg5y:.1f})"
            log.info("[derived] cape_delta_string = %s", vi_out["cape_delta_string"])

    # ── Fwd P/E history series + delta string ──────────────────────
    eps_history = fwd_eps_rec.get("history") if isinstance(fwd_eps_rec, dict) else None
    sp_series   = series_out.get("sp500")
    if eps_history and sp_series:
        # Build month-key → last S&P close lookup
        sp_by_month: dict = {}
        for d, v in zip(sp_series.get("dates", []), sp_series.get("values", [])):
            if v is not None:
                sp_by_month[d[:7]] = v  # last write per month = month-end close

        fwd_pe_dates:  list = []
        fwd_pe_values: list = []
        for d, eps in zip(eps_history.get("dates", []), eps_history.get("values", [])):
            sp_close = sp_by_month.get(d[:7])
            if sp_close and eps:
                fwd_pe_dates.append(d)
                fwd_pe_values.append(round(sp_close / eps, 4))

        if fwd_pe_dates:
            series_out["fwd_pe_history"] = {
                "dates":  fwd_pe_dates,
                "values": fwd_pe_values,
            }
            log.info(
                "[derived] fwd_pe_history: %d monthly entries (%s → %s)",
                len(fwd_pe_dates), fwd_pe_dates[0], fwd_pe_dates[-1],
            )

        if len(fwd_pe_values) >= 24:
            current_fwdpe = vi_out.get("forward_pe_ntm")
            avg = sum(fwd_pe_values) / len(fwd_pe_values)
            if current_fwdpe:
                delta = (current_fwdpe - avg) / avg * 100.0
                sign  = "+" if delta >= 0 else ""
                vi_out["fwdpe_delta_string"] = f"{sign}{delta:.0f}% vs 5yr avg ({avg:.1f}×)"
                log.info("[derived] fwdpe_delta_string = %s", vi_out["fwdpe_delta_string"])


def run(filter_ids: Optional[list] = None, force: bool = False) -> None:
    """
    Execute a dashboard refresh, respecting each series' refresh_frequency.

    Parameters
    ----------
    filter_ids : optional list of series IDs to fetch. If None, all series
                 in series.yaml are considered. Valuation inputs are always
                 processed regardless of this filter.
    force      : if True, bypass all frequency checks and fetch every series.
    """
    log.info("Loading config from %s", CONFIG_PATH)
    config = _load_config(CONFIG_PATH)

    series_config = config.get("series", [])
    vi_config     = config.get("valuation_inputs", {})

    if filter_ids:
        series_config = [e for e in series_config if e["id"] in filter_ids]
        if not series_config:
            log.error("No series matched filter: %s", filter_ids)
            sys.exit(1)
        log.info("Filter active — fetching %d series: %s", len(series_config), filter_ids)

        if OUTPUT_PATH.exists():
            print(
                "\n"
                "  WARNING: --series will overwrite data.json with ONLY the series\n"
                f"  specified ({', '.join(filter_ids)}).\n"
                "  All other series data will be wiped from data.json and dashboard.html,\n"
                "  leaving the dashboard mostly blank until a full refresh is run.\n"
                "\n"
                "  A backup of the current data.json will be written to backups/ first.\n"
            )
            answer = input("  Proceed? (yes / no): ").strip().lower()
            if answer != "yes":
                print("  Aborted. Run 'python refresh.py' (no --series) for a full refresh.")
                sys.exit(0)

    # ------------------------------------------------------------------
    # Load prior data.json to carry forward skipped series and last_fetched
    # ------------------------------------------------------------------
    existing       = _load_existing_data()
    existing_series   = existing.get("series", {})
    existing_statuses = existing.get("metadata", {}).get("statuses", {})

    if force:
        log.info("--force active: bypassing all frequency checks")

    fetchers = _load_fetchers()
    log.info("Fetchers loaded: %s", list(fetchers.keys()))

    series_out: dict = {}
    statuses:   dict = {}
    failures:   list = []
    skipped:    list = []
    now_ts = datetime.now(PT).replace(tzinfo=None).isoformat(timespec="seconds")  # naive PT

    # ------------------------------------------------------------------
    # Main series loop
    # ------------------------------------------------------------------
    for entry in series_config:
        sid       = entry["id"]
        source    = entry["source"]
        frequency = entry.get("refresh_frequency", "intraday")
        lag = entry.get("publish_lag_bd")
        if lag is None:
            lag = 1 if (source == "fred" and frequency == "daily_release") else 0

        # Frequency check — carry forward existing data if still current
        if not force and not _needs_refresh(
            sid, frequency, existing_statuses,
            last_obs=_last_observation_date(existing_series.get(sid)),
            publish_lag_bd=lag,
            session_series=sid not in CONTINUOUS_SERIES,
        ):
            if sid in existing_series:
                series_out[sid] = existing_series[sid]
                statuses[sid]   = {**existing_statuses.get(sid, {"status": "ok"})}
                log.info("[%s] SKIPPED (frequency=%s, data current)", sid, frequency)
                skipped.append(sid)
                continue
            # No existing data despite being "current" — fall through to fetch
            log.info("[%s] no prior data found despite frequency check — fetching", sid)

        log.info("[%s] fetching from %s (series_id=%s)", sid, source, entry["series_id"])

        try:
            if source == "treasury":
                data = _fetch_yield_curve(entry, fetchers)
            else:
                data = _fetch_standard(entry, fetchers)

            n_obs = len(data["dates"])
            series_out[sid] = data
            statuses[sid]   = {"status": "ok", "last_fetched": now_ts, "last_attempt": now_ts}
            log.info("[%s] OK — %d observations", sid, n_obs)

        except Exception as exc:
            msg = str(exc)
            log.error("[%s] FAILED: %s", sid, msg)
            # Carry forward existing data on failure so the dashboard stays populated
            if sid in existing_series:
                series_out[sid] = existing_series[sid]
                log.info("[%s] carrying forward prior data after failure", sid)
            statuses[sid] = {
                "status":       "error",
                "error":        msg,
                "last_fetched": existing_statuses.get(sid, {}).get("last_fetched"),
                "last_attempt": now_ts,
            }
            failures.append(sid)

    attempted = len(series_config) - len(skipped)
    success   = attempted - len(failures)

    # ------------------------------------------------------------------
    # Valuation inputs — always processed; never crashes the refresh
    # ------------------------------------------------------------------
    vi_out: dict = {}
    if vi_config:
        log.info("Processing %d valuation input(s)...", len(vi_config))
        try:
            prev_vi = existing.get("valuation_inputs") or {}
            vi_out = _fetch_valuation_inputs(vi_config, fetchers, statuses, previous=prev_vi,
                                             now_ts=now_ts)
            _write_eps_change_flag(prev_vi.get("forward_eps_ntm"), vi_out.get("forward_eps_ntm"))
            _append_factset_notice(fetchers)
        except Exception as exc:
            log.error(
                "Valuation inputs processing raised unexpectedly: %s — "
                "continuing without valuation_inputs block",
                exc,
            )

    # ------------------------------------------------------------------
    # Derived valuation inputs — computed from fetched series + vi_out
    # ------------------------------------------------------------------
    try:
        _compute_derived_valuation(vi_out, series_out)
    except Exception as exc:
        log.warning("Derived valuation computation failed: %s — continuing", exc)

    # ------------------------------------------------------------------
    # Assemble output
    # ------------------------------------------------------------------
    output: dict = {
        "metadata": {
            "last_refreshed": now_ts,
            "series_count":   len(series_config),
            "success_count":  success,
            "failure_count":  len(failures),
            "skip_count":     len(skipped),
            "statuses":       statuses,
        },
        "series":           series_out,
        "valuation_inputs": vi_out,
    }

    _backup_data_json()
    log.info("Writing %s", OUTPUT_PATH)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(output, fh, separators=(",", ":"))

    render(output)

    # ------------------------------------------------------------------
    # Console summary
    # ------------------------------------------------------------------
    width = 60
    print(f"\n{'=' * width}")
    print(f"  Preston Economic Console — refresh complete")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if skipped:
        print(f"  {success}/{attempted} fetched successfully  |  {len(skipped)} skipped (frequency)")
    else:
        print(f"  {success}/{attempted} series succeeded")
    if failures:
        print(f"\n  Failed series ({len(failures)}):")
        for f in failures:
            err = statuses[f].get("error", "unknown error")
            truncated = err if len(err) <= 80 else err[:77] + "..."
            print(f"    * {f}: {truncated}")
    elif not skipped:
        print("  All series fetched successfully.")
    if skipped:
        print(f"\n  Skipped (data current per refresh_frequency):")
        for s in skipped:
            freq = next(
                (e.get("refresh_frequency", "intraday")
                 for e in config.get("series", []) if e["id"] == s),
                "?",
            )
            lf = statuses[s].get("last_fetched", "unknown")
            print(f"    — {s} [{freq}]  last_fetched={lf}")

    if vi_out:
        print(f"\n  Valuation inputs ({len(vi_out)}):")
        override_reminders = []
        for vi_id, vi_rec in vi_out.items():
            if not isinstance(vi_rec, dict):
                # derived scalar (forward_pe_ntm, erp, ddm_fair_value, delta strings)
                print(f"    ✓ {vi_id}: {vi_rec}  [derived]")
                continue
            src = vi_rec["source"]
            val = vi_rec["value"]
            eff = vi_rec["effective_date"]
            if src == "unavailable":
                err = statuses.get(vi_id, {}).get("error", "")
                trunc = err if len(err) <= 60 else err[:57] + "..."
                print(f"    ! {vi_id}: unavailable ({trunc})")
            else:
                print(f"    ✓ {vi_id}: {val}  [{eff}]  source={src}")
                if src == "override":
                    override_reminders.append((vi_id, val, eff))

        for vi_id, val, eff in override_reminders:
            try:
                days_old = (date.today() - date.fromisoformat(str(eff))).days
                age_str = f"{days_old} day{'s' if days_old != 1 else ''} old"
            except (ValueError, TypeError):
                age_str = "age unknown"
            print(f"\n  {'-' * (width - 2)}")
            print(f"  ⚑  ACTION — {vi_id} needs a weekly manual update")
            print(f"     Current value: ${val}  |  as of {eff}  ({age_str})")
            print(f"     Provide the NTM forward operating EPS for the S&P 500.")
            print(f"     Primary source (use the 52-Wk Forward EPS line):")
            print(f"       Yardeni:  https://www.yardeni.com/charts/us-stock-market/")
            print(f"                 stock-market-forward-metrics/sp-500-forward-revenues-earnings-margins")
            print(f"     Cross-check:")
            print(f"       FactSet Earnings Insight PDF (p. 32+ bottom-up EPS table):")
            print(f"                 https://www.factset.com/earningsinsight")
            print(f"     To update:  edit config/series.yaml")
            print(f"                 valuation_inputs → {vi_id} → manual_override")
            print(f"  {'─' * (width - 2)}")

    print(f"\n  Output: {OUTPUT_PATH}")
    print(f"  Dashboard: {HERE / 'PEC.html'}")
    print(f"{'=' * width}\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh the Preston Macro Dashboard. "
            "Fetches all series in config/series.yaml and writes data.json."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python refresh.py\n"
            "  python refresh.py --force\n"
            "  python refresh.py --series sp500 fed_funds cpi_yoy\n"
        ),
    )
    parser.add_argument(
        "--series",
        nargs="+",
        metavar="ID",
        help=(
            "Fetch only these logical series IDs (space-separated). "
            "IDs must match the 'id' field in series.yaml. "
            "Valuation inputs are always processed regardless of this filter."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Bypass all refresh_frequency checks and fetch every series regardless "
            "of when it was last fetched. Use when you need a guaranteed full refresh."
        ),
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help=(
            "After the normal run, also produce PEC_dev.html — a lightweight version "
            "that loads data_v2.json at runtime instead of embedding it inline (~50 KB "
            "vs ~4.6 MB). Requires a local HTTP server (preview.py) to function."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    try:
        run(filter_ids=args.series, force=args.force)
        if args.dev:
            render_dev()
    except KeyboardInterrupt:
        log.info("Interrupted.")
        sys.exit(0)
    except Exception as exc:
        log.critical("Orchestrator crashed before completing: %s", exc, exc_info=True)
        sys.exit(1)
