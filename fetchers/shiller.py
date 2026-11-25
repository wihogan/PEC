# shiller.py - S&P 500 valuation series derived from Robert Shiller's dataset (ie_data.xls).
#
# Replaces the multpl.com fetcher (owner decision 2026-11-24; docs/plan.md Amendment C).
#
# Source discovery
# ----------------
# https://shillerdata.com/ links the workbook with a protocol-relative, versioned href
# (//img1.wsimg.com/blobby/go/.../downloads/<uuid>/ie_data.xls?ver=...). The link is
# discovered from the landing page on every run by matching "ie_data.xls". The Yale copy
# (econ.yale.edu/~shiller/data/ie_data.xls) is a fallback only; it is stale (ends 2023-09),
# so any workbook whose last published earnings month is more than 9 months old is rejected.
#
# Series ids served by fetch()
# ----------------------------
#   dividend_yield    D / P            decimal (0.0107 = 1.07%)   dates: month-end
#   pe                P / E            ratio                      dates: first of month
#   earnings_yield    E / P            decimal                    dates: first of month
#   real_earnings     E * CPI_latest / CPI_m   $/share, latest-month dollars   dates: month-end
#   nominal_earnings  E                $/share, as published      dates: month-end
#   cape              Shiller CAPE     ratio                      dates: first of month
# (Date conventions match the multpl archive each series replaces.)
#
# Splice (dividend_yield, pe, earnings_yield, cape)
# -------------------------------------------------
# config/multpl_archive.json is the frozen multpl history. Its rows up to and including
# 2026-10 are kept exactly as archived (the later October row is multpl's in-month
# estimate); Shiller-derived months are appended strictly after 2026-10.
# real_earnings / nominal_earnings are rebuilt entirely from Shiller (a real series must be
# re-based to the latest CPI every month, so splicing CPI bases would be invalid).
#
# Provisional handling (owner decisions)
# --------------------------------------
#   * Every month AFTER the archive cutoff (2026-10), ended or current: price = our own
#     monthly average of daily ^GSPC closes (one cached Yahoo download per run, starting at
#     the first day of the month after the cutoff). Shiller's last price row is a snapshot,
#     not a monthly average, so his P is only a fallback when ours is unavailable for that
#     month (or is >15% away from his). If Shiller has no row for a month and ours is
#     unavailable, the month is omitted.
#   * A month Shiller has not published a row for (skipped update) is synthesized with the
#     same rule as the current month (see CAPE below) and is provisional.
#   * Months where Shiller's D/E are blank (latest 1-3 months): the last published D and E
#     are carried forward.
#   * CAPE: Shiller's value for months he publishes (all months up to the cutoff, spliced
#     from the archive); for months after the cutoff with our own price:
#     CAPE_shiller_m * (P_ours / P_shiller_m); with no Shiller row:
#     (P_ours / CPI_est) / (P_prev / CPI_prev / CAPE_prev), CPI_est = latest Shiller CPI.
#     Ended months stay provisional while Shiller's D/E for them are carried forward, so
#     their values change only when Shiller publishes new D/E/CPI/CAPE (no month-end jump).
#
# Network limits (docs/qa-shiller.md M2)
# --------------------------------------
# A failed workbook load is remembered for the process (at most ONE landing attempt and ONE
# Yale attempt per run for all six series); connect timeout 10 s, read timeout 30 s; all
# Shiller network work (landing, workbook, Yale, ^GSPC download) shares a 90 s budget.
# The ^GSPC download is cached including failure.
#
# Health marker (docs/qa-shiller.md M3)
# -------------------------------------
# .cache/shiller_health.json (persisted on the state branch) counts failing PT days and
# remembers the last good workbook's last earnings month; pending_notice() returns a
# one-time message after 2 failing PT days or when that month is >5 months old.
#
# Licensing: shillerdata.com terms/robots not yet verified (owner to confirm); the workbook is
# downloaded once per trading day with an identifying User-Agent.
#   * The first provisional point is returned in df.attrs["provisional_from"] (ISO date
#     of that series' own date convention, or None); refresh_v2 stores it as
#     series["provisional_from"].
#
# Requires: requests, xlrd (legacy .xls), pandas, yfinance.

from __future__ import annotations

import html
import json
import logging
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .base import FetchError

log = logging.getLogger(__name__)

_LANDING_URL = "https://shillerdata.com/"
_YALE_URL = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"
_ARCHIVE_PATH = Path(__file__).parent.parent / "config" / "multpl_archive.json"
_OLE2_MAGIC = b"\xD0\xCF\x11\xE0"
_MAX_EARNINGS_AGE_MONTHS = 9
_PT = ZoneInfo("America/Los_Angeles")
_DEFAULT_CUTOFF = "2026-10"
_NET_CONNECT_S = 10.0
_NET_READ_S = 30.0
_NET_BUDGET_S = 90.0
_YF_TIMEOUT_S = 20.0
_MAX_PRICE_DEVIATION = 0.15   # our monthly average vs Shiller's P for the same/previous month

_CACHE_DIR = Path(__file__).parent.parent / ".cache"
_HEALTH_PATH = _CACHE_DIR / "shiller_health.json"
NOTICE_FAIL_DAYS = 2          # Telegram info after this many distinct failing PT days
NOTICE_STALE_MONTHS = 5       # ... or when Shiller's latest E is more than this many months old

_HEADERS = {"User-Agent": "PEC-dashboard/1.0 (personal macro dashboard; +https://github.com/wihogan/PEC)"}

# fetch() series_id -> (archive key or None, is_month_end_date)
_SERIES = {
    "dividend_yield":   ("dividend_yield", True),
    "pe":               ("pe", False),
    "earnings_yield":   ("earnings_yield", False),
    "cape":             ("cape", False),
    "real_earnings":    (None, True),
    "nominal_earnings": (None, True),
}

# Expected ranges, applied to Shiller-derived values (fail closed -> FetchError).
_RANGES = {
    "pe":               (8.0, 60.0),
    "cape":             (5.0, 60.0),
    "dividend_yield":   (0.005, 0.08),
    "earnings_yield":   (0.015, 0.15),
    "nominal_earnings": (10.0, 600.0),
    "real_earnings":    (10.0, 800.0),
}

# In-memory, per-process caches (the runner is ephemeral).
_WORKBOOK_CACHE: dict = {}   # "monthly", "error", "t0" (network budget start)
_MTD_CACHE: dict = {}        # ^GSPC monthly averages, keyed by download start date; failure cached as None


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
def _budget_left() -> float:
    t0 = _WORKBOOK_CACHE.setdefault("t0", time.monotonic())
    return max(0.0, _NET_BUDGET_S - (time.monotonic() - t0))


def _timeout() -> tuple:
    """(connect, read) timeouts clamped to what is left of the 90 s per-run budget."""
    left = _budget_left()
    if left < 1.0:
        raise FetchError(f"Shiller network time budget ({_NET_BUDGET_S:.0f} s) exhausted")
    return (min(_NET_CONNECT_S, left), min(_NET_READ_S, left))


def _discover_url() -> str:
    try:
        resp = requests.get(_LANDING_URL, headers=_HEADERS, timeout=_timeout())
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError(f"Failed to load {_LANDING_URL}: {exc}") from exc
    for href in re.findall(r"""href=["']([^"']*ie_data\.xls[^"']*)["']""", resp.text, flags=re.I):
        href = html.unescape(href).strip()
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return _LANDING_URL.rstrip("/") + href
    raise FetchError("No ie_data.xls link found on shillerdata.com landing page")


def _download(url: str) -> bytes:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_timeout())
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError(f"Failed to download {url}: {exc}") from exc
    content = resp.content
    if not content.startswith(_OLE2_MAGIC):
        raise FetchError(f"{url} is not an OLE2 .xls workbook (got {content[:16]!r})")
    return content


def _months_between(a: pd.Period, b: pd.Period) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def _get_monthly(today: date) -> pd.DataFrame:
    """Monthly frame (index Period[M]; columns P, D, E, CPI, CAPE; NaN where blank), cached."""
    if "monthly" in _WORKBOOK_CACHE:
        return _WORKBOOK_CACHE["monthly"]
    if "error" in _WORKBOOK_CACHE:
        raise _WORKBOOK_CACHE["error"]   # negative cache: one landing + one Yale attempt per run
    errors = []
    for label, getter in (("shillerdata.com", lambda: _download(_discover_url())),
                          ("yale", lambda: _download(_YALE_URL))):
        try:
            content = getter()
            monthly = _parse_workbook(content)
            last_e = monthly["E"].dropna().index.max()
            age = _months_between(last_e, pd.Period(today, "M"))
            if age > _MAX_EARNINGS_AGE_MONTHS:
                raise FetchError(f"workbook stale: last earnings month {last_e} is {age} months old")
            log.info("[shiller] using %s workbook (%d bytes); last P %s, last E %s",
                     label, len(content), monthly["P"].dropna().index.max(), last_e)
            _WORKBOOK_CACHE["monthly"] = monthly
            _record_success(last_e)
            return monthly
        except FetchError as exc:
            errors.append(f"{label}: {exc}")
            log.warning("[shiller] %s failed: %s", label, exc)
    err = FetchError("Shiller workbook unavailable - " + " | ".join(errors))
    _WORKBOOK_CACHE["error"] = err
    _record_failure(today)
    raise err


# ---------------------------------------------------------------------------
# Health marker and one-time notice (same pattern as factset_eps.pending_notice)
# ---------------------------------------------------------------------------
def _read_health() -> dict:
    try:
        with open(_HEALTH_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_health(payload: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_HEALTH_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def _record_failure(today: date) -> None:
    """Count one failing PT day (several failures on one day count once)."""
    try:
        raw = _read_health()
        if raw.get("last_fail_date") != today.isoformat():
            raw["fail_days"] = int(raw.get("fail_days") or 0) + 1
            raw["last_fail_date"] = today.isoformat()
            _write_health(raw)
    except Exception as exc:  # pragma: no cover
        log.warning("[shiller] could not record failure marker: %s", exc)


def _record_success(last_e: pd.Period) -> None:
    """A good workbook clears the failure streak; notice markers are kept (cleared by pending_notice)."""
    try:
        raw = _read_health()
        new = {"fail_days": 0, "last_fail_date": None, "last_e": str(last_e),
               "notified": raw.get("notified") if isinstance(raw.get("notified"), dict) else {}}
        if new != {k: raw.get(k) for k in new}:
            _write_health(new)
    except Exception as exc:  # pragma: no cover
        log.warning("[shiller] could not record success marker: %s", exc)


def pending_notice(today: Optional[date] = None) -> Optional[str]:
    """
    One-time informational message when the Shiller fetch has failed on 2 distinct PT days
    since the last success, or Shiller's latest published earnings month is more than
    5 months old; else None. Each condition is reported once (marker in the health JSON)
    and re-arms when the condition clears.
    """
    today = today or datetime.now(_PT).date()
    raw = _read_health()
    if not raw:
        return None
    notified = dict(raw["notified"]) if isinstance(raw.get("notified"), dict) else {}
    fail_days = int(raw.get("fail_days") or 0)
    failing = fail_days >= NOTICE_FAIL_DAYS
    stale_months = None
    try:
        stale_months = _months_between(pd.Period(str(raw.get("last_e")), "M"), pd.Period(today, "M"))
    except Exception:
        pass
    stale = stale_months is not None and stale_months > NOTICE_STALE_MONTHS
    msgs = []
    for name, active, text in (
        ("failing", failing, f"Shiller valuation data fetch has failed on {fail_days} different days"),
        ("stale", stale, f"Shiller's latest published earnings month is {stale_months} months old "
                         f"({raw.get('last_e')}); upstream may have stopped updating"),
    ):
        if active and not notified.get(name):
            msgs.append(text)
            notified[name] = True
        elif not active:
            notified.pop(name, None)
    if notified != (raw.get("notified") if isinstance(raw.get("notified"), dict) else {}):
        raw["notified"] = notified
        try:
            _write_health(raw)
        except Exception as exc:  # pragma: no cover
            log.warning("[shiller] could not write notice marker: %s", exc)
    if not msgs:
        return None
    return ("\u2139\ufe0f PEC: " + "; ".join(msgs)
            + ". P/E, CAPE and earnings series are carried forward; see docs/plan.md Amendment C.\n")


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------
def _parse_workbook(content: bytes) -> pd.DataFrame:
    import io
    try:
        raw = pd.read_excel(io.BytesIO(content), sheet_name="Data", header=None, engine="xlrd")
    except Exception as exc:
        raise FetchError(f"Failed to read Shiller workbook 'Data' sheet: {exc}") from exc

    header_idx = None
    for idx in range(min(15, len(raw))):
        vals = [str(v).strip() for v in raw.iloc[idx].values]
        if vals and vals[0] == "Date" and "CAPE" in vals:
            header_idx = idx
            break
    if header_idx is None:
        raise FetchError("Could not detect header row (col 0 'Date' + 'CAPE') in first 15 rows")

    header = [str(v).strip() for v in raw.iloc[header_idx].values]
    # Duplicate header names exist ('Price', 'Earnings'): select by position, validate names.
    if header[1:5] != ["P", "D", "E", "CPI"]:
        raise FetchError(f"Unexpected Shiller columns 1-4: {header[1:5]} (expected P, D, E, CPI)")
    cape_pos = header.index("CAPE")  # exact text: not 'TR CAPE'

    body = raw.iloc[header_idx + 1:]
    frame = pd.DataFrame({
        "date_raw": pd.to_numeric(body.iloc[:, 0], errors="coerce"),
        "P": pd.to_numeric(body.iloc[:, 1], errors="coerce"),
        "D": pd.to_numeric(body.iloc[:, 2], errors="coerce"),
        "E": pd.to_numeric(body.iloc[:, 3], errors="coerce"),
        "CPI": pd.to_numeric(body.iloc[:, 4], errors="coerce"),
        "CAPE": pd.to_numeric(body.iloc[:, cape_pos], errors="coerce"),
    }).dropna(subset=["date_raw"])

    # Decimal year-month: 2026.10 is read as float 2026.1 -> month = round(frac * 100).
    year = frame["date_raw"].apply(lambda x: int(x + 1e-9))
    month = ((frame["date_raw"] - year) * 100).round().astype(int)
    ok = (month >= 1) & (month <= 12)
    frame = frame[ok].copy()
    frame.index = pd.PeriodIndex.from_fields(year=year[ok].values, month=month[ok].values, freq="M")
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame = frame.drop(columns="date_raw")
    if frame["P"].dropna().empty or frame["E"].dropna().empty:
        raise FetchError("Shiller workbook parsed but P/E columns are empty")
    return frame


# ---------------------------------------------------------------------------
# Our own monthly S&P 500 price (average of daily ^GSPC closes)
# ---------------------------------------------------------------------------
def _archive_cutoff() -> pd.Period:
    try:
        with open(_ARCHIVE_PATH, "r", encoding="utf-8") as fh:
            return pd.Period(json.load(fh)["cutoff_month"], "M")
    except Exception:
        return pd.Period(_DEFAULT_CUTOFF, "M")


def _gspc_monthly(start: date) -> Optional[dict]:
    """
    {Period[M]: average of daily ^GSPC closes} for every month from `start`'s month, from ONE
    Yahoo download per run (cached, failure included as None). None on any failure.
    """
    key = start.isoformat()
    if key in _MTD_CACHE:
        return _MTD_CACHE[key]
    out = None
    try:
        if _budget_left() < 5.0:
            raise TimeoutError("Shiller network time budget exhausted")
        import yfinance as yf  # noqa: PLC0415
        raw = yf.download("^GSPC", start=key, auto_adjust=True, progress=False,
                          multi_level_index=False, timeout=min(_YF_TIMEOUT_S, _budget_left()))
        if raw is not None and not raw.empty:
            close = pd.to_numeric(raw["Close"], errors="coerce").dropna()
            idx = pd.DatetimeIndex(close.index)
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            close.index = idx
            close = close[close.index >= pd.Timestamp(start)]
            if len(close):
                grouped = close.groupby(close.index.to_period("M"))
                avgs = grouped.mean()
                counts = grouped.size()
                out = {per: float(v) for per, v in avgs.items() if 100 < v < 100000}
                log.info("[shiller] ^GSPC monthly averages: %s",
                         ", ".join(f"{per} {avgs[per]:.2f} ({counts[per]} closes)" for per in avgs.index))
    except Exception as exc:  # fail closed to Shiller's P
        log.warning("[shiller] ^GSPC download failed (%s); falling back to Shiller P", exc)
    _MTD_CACHE[key] = out or None
    return _MTD_CACHE[key]


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------
def _derive(monthly: pd.DataFrame, today: date) -> pd.DataFrame:
    """
    Effective monthly frame with columns P, D, E, CPI, CAPE, prov (bool), covering Shiller's
    months with a price plus any later month up to the current one for which we have our own
    price. Months after the archive cutoff use our monthly average of daily ^GSPC closes
    (Shiller's P only as a fallback); see the module header.
    """
    df = monthly[monthly["P"].notna()].copy()
    cur = pd.Period(today, "M")
    last_p = df.index.max()
    if _months_between(last_p, cur) > 3:
        raise FetchError(f"Shiller price data stale: last month {last_p}, current {cur}")

    d_pub = df["D"].notna()
    e_pub = df["E"].notna()
    df["prov"] = ~(d_pub & e_pub)
    df["D"] = df["D"].ffill()
    df["E"] = df["E"].ffill()
    df["CPI"] = df["CPI"].ffill()

    cutoff = _archive_cutoff()
    first = cutoff + 1
    ours = _gspc_monthly(first.to_timestamp().date()) or {}
    sh_last = df.index.max()

    for m in pd.period_range(first, cur, freq="M"):
        p_ours = ours.get(m)
        if p_ours is not None:
            ref_m = m if m in df.index else m - 1
            ref = df["P"].get(ref_m)
            if ref is not None and pd.notna(ref) and abs(p_ours / ref - 1) > _MAX_PRICE_DEVIATION:
                log.warning("[shiller] ^GSPC average %.2f for %s is >%d%% away from Shiller P %.2f (%s); ignoring it",
                            p_ours, m, round(_MAX_PRICE_DEVIATION * 100), ref, ref_m)
                p_ours = None
        if m in df.index:
            if p_ours is not None:
                p_sh, cape_sh = df.loc[m, "P"], df.loc[m, "CAPE"]
                if pd.notna(cape_sh) and p_sh:
                    df.loc[m, "CAPE"] = cape_sh * (p_ours / p_sh)
                df.loc[m, "P"] = p_ours
            elif m == sh_last:
                df.loc[m, "prov"] = True  # Shiller's last price row is a snapshot, not a monthly average
            if m == cur:
                df.loc[m, "prov"] = True
        elif p_ours is not None:
            prior = df[df.index < m]
            prev = prior.iloc[-1]
            cpi_est = df["CPI"].iloc[-1]
            denom = prev["P"] / prev["CPI"] / prev["CAPE"]
            df.loc[m] = {
                "P": p_ours, "D": prev["D"], "E": prev["E"], "CPI": cpi_est,
                "CAPE": (p_ours / cpi_est) / denom, "prov": True,
            }
            df = df.sort_index()
    df["prov"] = df["prov"].astype(bool)
    return df


def _series_from(eff: pd.DataFrame, series_id: str, today: Optional[date] = None) -> pd.DataFrame:
    """Compute one series from the effective frame; returns date/value/prov columns."""
    _, month_end = _SERIES[series_id]
    if series_id == "pe":
        v = eff["P"] / eff["E"]
    elif series_id == "earnings_yield":
        v = eff["E"] / eff["P"]
    elif series_id == "dividend_yield":
        v = eff["D"] / eff["P"]
    elif series_id == "cape":
        v = eff["CAPE"]
    elif series_id == "nominal_earnings":
        v = eff["E"]
    elif series_id == "real_earnings":
        v = eff["E"] * eff["CPI"].iloc[-1] / eff["CPI"]
    else:  # pragma: no cover
        raise FetchError(f"unsupported series_id {series_id}")
    out = pd.DataFrame({"value": v.astype(float), "prov": eff["prov"]})
    out.index = (out.index.to_timestamp(freq="M", how="end") if month_end
                 else out.index.to_timestamp(how="start")).normalize()
    if month_end:
        # The in-progress month must not carry a future month-end date: date it today
        # (as multpl did for its in-month estimate rows).
        cap = pd.Timestamp(today) if today is not None else pd.Timestamp.now().normalize()  # fetch's PT date (D7)
        out.index = out.index.where(out.index <= cap, cap)
    out = out.replace([float("inf"), float("-inf")], float("nan")).dropna(subset=["value"])
    out.index.name = "date"
    return out.reset_index()


def _check_range(series_id: str, df: pd.DataFrame) -> None:
    lo, hi = _RANGES[series_id]
    bad = df[(df["value"] < lo) | (df["value"] > hi)]
    if not bad.empty:
        r = bad.iloc[-1]
        raise FetchError(
            f"Shiller-derived '{series_id}' value {r['value']:.4f} on {r['date'].date()} outside "
            f"expected range [{lo}, {hi}] ({len(bad)} point(s)); refusing to publish"
        )


def _load_archive(key: str) -> tuple[pd.DataFrame, pd.Period]:
    try:
        with open(_ARCHIVE_PATH, "r", encoding="utf-8") as fh:
            arch = json.load(fh)
        s = arch["series"][key]
        cutoff = pd.Period(arch["cutoff_month"], "M")
        df = pd.DataFrame({"date": pd.to_datetime(s["dates"]), "value": s["values"]})
    except Exception as exc:
        raise FetchError(f"Cannot load multpl archive for '{key}': {exc}") from exc
    return df, cutoff


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def fetch(series_id: str, start_date: str = "2010-01-01", end_date: str = None, **kw) -> pd.DataFrame:
    """
    Return a DataFrame ['date', 'value'] (sorted, no NaN) for a Shiller-derived series.

    df.attrs['provisional_from'] holds the ISO date of the first provisional point
    (or None). See the module header for splice and provisional rules.
    """
    if series_id not in _SERIES:
        raise FetchError(f"shiller.fetch() supports {sorted(_SERIES)}, got '{series_id}'")
    start_dt = pd.Timestamp(datetime.strptime(start_date, "%Y-%m-%d"))
    today = (datetime.strptime(end_date, "%Y-%m-%d").date() if end_date
             else datetime.now(_PT).date())

    monthly = _get_monthly(today)
    eff = _derive(monthly, today)
    derived = _series_from(eff, series_id, today)

    archive_key = _SERIES[series_id][0]
    if archive_key is None:
        _check_range(series_id, derived[derived["date"] >= start_dt])
        out = derived
        prov = derived[derived["prov"]]
    else:
        arch_df, cutoff = _load_archive(archive_key)
        cutoff_end = cutoff.to_timestamp(how="end").normalize()
        appended = derived[derived["date"] > cutoff_end].copy()
        _check_range(series_id, appended)
        out = pd.concat([arch_df[arch_df["date"] <= cutoff_end], appended[["date", "value"]]],
                        ignore_index=True)
        prov = appended[appended["prov"]]

    out = out[["date", "value"]]
    out = out[out["date"] >= start_dt]
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna(subset=["value"]).sort_values("date", kind="stable").reset_index(drop=True)
    if out.empty:
        raise FetchError(f"Shiller series '{series_id}' is empty for start {start_date}")

    pf = prov["date"].min() if len(prov) else None
    out.attrs["provisional_from"] = pf.strftime("%Y-%m-%d") if pf is not None and pd.notna(pf) else None
    return out
