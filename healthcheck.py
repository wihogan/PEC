#!/usr/bin/env python3
"""
healthcheck.py - post-refresh gate for the PEC pipeline (docs/plan.md section 5).

Usage:
    python healthcheck.py [--data data_v2.json] [--html PEC.html]
                          [--config config/series_v2.yaml] [--out health.json]
                          [--now ISO8601]

Exit status: 0 if every hard-fail rule passes, 1 otherwise. Warnings never
change the exit status. Writes a machine-readable summary to --out.

Hard-fail rules:
  1. data_v2.json exists and is valid JSON.
  2. Series ids == the ids in series_v2.yaml (plus the derived fwd_pe_history).
  3. PEC.html >= size floor, no unreplaced placeholders (outside HTML comments),
     and the <script id="pec-data"> body parses to JSON equal to data_v2.json.
  4. Source-level failures over series attempted this run:
     > 3 failed intraday/daily_release series (reported as "market_daily"); all attempted FRED failed (>= 3 attempted);
     all attempted Yahoo failed (>= 3 attempted). Treasury / shiller: staleness only.
  5. Staleness of daily series (calendar days since last non-null observation).
  6. valuation_inputs.forward_eps_ntm is the config override (source override), or a FactSet
     value (source factset) dated after the override's effective_date and within 200-600.

Pure stdlib + PyYAML (and the repo's market_calendar).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import yaml

import market_calendar

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Thresholds (calendar days unless noted)
# ---------------------------------------------------------------------------
# Frequencies counted by rule 4 (formerly "market_daily"): intraday + daily_release.
DAILY_FREQUENCIES = ("intraday", "daily_release")

THRESHOLDS: dict = {
    # Hard fail when age > value. Per-id entry wins over per-frequency entry.
    # Only frequencies / ids listed here are gated; slow series are warn-only.
    "fail_by_frequency": {"intraday": 7, "daily_release": 7},
    "fail_by_id": {
        "eur_usd": 14, "cad_usd": 14, "jpy_usd": 14,   # FRED FX (weekly H.10 batches)
        "wti_crude": 14,                                # FRED weekly batch
        "yield_curve": 7,
    },
    # Warn when age > value. Per-id entry wins over per-frequency entry.
    "warn_by_frequency": {"daily_after_close": 7, "monthly": 110, "quarterly": 300},
    # Monthly valuation rows dated first-of-month (always include the current month): warn after 40d.
    "warn_by_id": {"sp500_pe": 40, "sp500_earnings_yield": 40, "shiller_cape": 40},
    # Derived / valuation inputs (warn only).
    "warn_fwd_pe_history_days": 60,
    "warn_eps_effective_days": 45,
    # Warn when the NYSE calendar table ends within this many days (m5)
    "warn_calendar_end_days": 60,
    # Rule 3
    "html_min_bytes": 1_500_000,
    # Rule 4
    "max_failed_market_daily": 3,
    "min_attempted_for_all_failed": 3,
}

PLACEHOLDERS = ("__DASHBOARD_DATA__", "__GENERATED_AT__")
DERIVED_IDS = {"fwd_pe_history"}
EPS_VI_ID = "forward_eps_ntm"

_SCRIPT_RE = re.compile(
    r'<script\b(?=[^>]*\bid="pec-data")[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE
)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_now(text: Optional[str]) -> datetime:
    if not text:
        return datetime.now().astimezone()
    return datetime.fromisoformat(text)


def _to_date(text: Any) -> Optional[date]:
    if not text:
        return None
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def last_observation(entry: Any) -> Optional[date]:
    """Date of the last non-null observation of a series entry, or None."""
    if not isinstance(entry, dict):
        return None
    dates = entry.get("dates") or []
    if "tenors" in entry:  # yield curve: last date with any non-null tenor
        tenors = [v for v in (entry.get("tenors") or {}).values() if isinstance(v, list)]
        for i in range(len(dates) - 1, -1, -1):
            if any(i < len(t) and t[i] is not None for t in tenors):
                return _to_date(dates[i])
        return None
    values = entry.get("values") or []
    for i in range(min(len(dates), len(values)) - 1, -1, -1):
        if values[i] is not None:
            return _to_date(dates[i])
    return None


def _fail_threshold(sid: str, freq: str) -> Optional[int]:
    t = THRESHOLDS
    if sid in t["fail_by_id"]:
        return t["fail_by_id"][sid]
    return t["fail_by_frequency"].get(freq)


def _warn_threshold(sid: str, freq: str) -> Optional[int]:
    t = THRESHOLDS
    if sid in t["warn_by_id"]:
        return t["warn_by_id"][sid]
    return t["warn_by_frequency"].get(freq)


# ---------------------------------------------------------------------------
# Rule 3 helper
# ---------------------------------------------------------------------------

def check_html(html_path: Path, data: Optional[dict], failures: list) -> None:
    if not html_path.exists():
        failures.append(f"rule 3: {html_path} is missing")
        return
    size = html_path.stat().st_size
    if size < THRESHOLDS["html_min_bytes"]:
        failures.append(
            f"rule 3: {html_path.name} is {size} bytes, below the "
            f"{THRESHOLDS['html_min_bytes']} byte floor"
        )
    html = html_path.read_text(encoding="utf-8", errors="replace")

    visible = _COMMENT_RE.sub("", html)
    for ph in PLACEHOLDERS:
        if ph in visible:
            failures.append(f"rule 3: placeholder {ph} still present in {html_path.name}")

    m = _SCRIPT_RE.search(html)
    if not m:
        failures.append('rule 3: <script id="pec-data"> block not found')
        return
    try:
        embedded = json.loads(m.group(1))
    except ValueError as exc:
        failures.append(f"rule 3: pec-data script body is not valid JSON ({exc})")
        return
    if data is not None and embedded != data:
        failures.append("rule 3: pec-data script JSON differs from data_v2.json")


# ---------------------------------------------------------------------------
# Main check
# ---------------------------------------------------------------------------

def run_checks(data_path: Path, html_path: Path, config_path: Path, now: datetime) -> dict:
    failures: list = []
    warnings: list = []
    series_report: dict = {}
    today = now.date()

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    cfg_series = {s["id"]: s for s in config.get("series", [])}
    cfg_eps = (
        (config.get("valuation_inputs") or {}).get(EPS_VI_ID, {}).get("manual_override") or {}
    )

    # Rule 1
    data: Optional[dict] = None
    if not data_path.exists():
        failures.append(f"rule 1: {data_path} is missing")
    else:
        try:
            loaded = json.loads(data_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or "series" not in loaded:
                failures.append("rule 1: data_v2.json has no 'series' object")
            else:
                data = loaded
        except ValueError as exc:
            failures.append(f"rule 1: data_v2.json is not valid JSON ({exc})")

    meta: dict = (data or {}).get("metadata") or {}
    statuses: dict = meta.get("statuses") or {}
    last_refreshed = meta.get("last_refreshed")

    if data is not None:
        series = data.get("series") or {}

        # Rule 2
        got = set(series) - DERIVED_IDS
        want = set(cfg_series)
        missing = sorted(want - got)
        extra = sorted(got - want)
        # A newly configured id that never fetched (status error or absent) is only a warning:
        # the previous state cannot contain it. Missing with status ok would be a bug: fail.
        new_unpopulated = [s for s in missing
                           if (statuses.get(s) or {}).get("status", "error") == "error"]
        hard_missing = [s for s in missing if s not in new_unpopulated]
        if new_unpopulated:
            warnings.append(f"new series not yet populated: {', '.join(new_unpopulated)}")
        if hard_missing:
            failures.append(f"rule 2: missing series ids: {', '.join(hard_missing)}")
        if extra:
            failures.append(f"rule 2: unknown series ids: {', '.join(extra)}")

        # Ages / report
        ages: dict = {}
        for sid in list(cfg_series) + [d for d in DERIVED_IDS if d in series]:
            obs = last_observation(series.get(sid))
            age = (today - obs).days if obs else None
            ages[sid] = age
            series_report[sid] = {
                "last_obs": obs.isoformat() if obs else None,
                "age_days": age,
                "status": (statuses.get(sid) or {}).get("status", "unknown"),
            }

        # Rule 4: attempted this run
        def attempted(sid: str) -> bool:
            # Attempted this run iff its last_attempt (old state: last_fetched) equals
            # metadata.last_refreshed. A carried-forward error from an earlier run does not count.
            st = statuses.get(sid) or {}
            stamp = st.get("last_attempt") or st.get("last_fetched")
            return bool(last_refreshed) and stamp == last_refreshed

        def failed(sid: str) -> bool:
            return (statuses.get(sid) or {}).get("status") == "error"

        att = [sid for sid in cfg_series if attempted(sid)]
        md_failed = [
            s for s in att
            if cfg_series[s].get("refresh_frequency", "intraday") in DAILY_FREQUENCIES
            and failed(s)
        ]
        if len(md_failed) > THRESHOLDS["max_failed_market_daily"]:
            failures.append(
                f"rule 4: {len(md_failed)} market_daily series failed "
                f"(limit {THRESHOLDS['max_failed_market_daily']}): {', '.join(md_failed)}"
            )
        for src in ("fred", "yahoo"):
            src_att = [s for s in att if cfg_series[s].get("source") == src]
            src_fail = [s for s in src_att if failed(s)]
            if (
                len(src_att) >= THRESHOLDS["min_attempted_for_all_failed"]
                and len(src_fail) == len(src_att)
            ):
                failures.append(
                    f"rule 4: all {len(src_att)} attempted {src.upper()} series failed"
                )

        # Rule 5 (fail) and slow-series warnings
        for sid, entry in cfg_series.items():
            freq = entry.get("refresh_frequency", "intraday")
            age = ages.get(sid)
            limit = _fail_threshold(sid, freq)
            if limit is not None:
                if age is None:
                    failures.append(f"rule 5: {sid} has no observations")
                elif age > limit:
                    failures.append(
                        f"rule 5: {sid} last observation {series_report[sid]['last_obs']} "
                        f"is {age} days old (limit {limit})"
                    )
            wlimit = _warn_threshold(sid, freq)
            if wlimit is not None and age is not None and age > wlimit:
                warnings.append(f"{sid} last observation is {age} days old (warn > {wlimit})")
            if failed(sid):
                msg = (statuses.get(sid) or {}).get("error") or "error"
                warnings.append(f"{sid} errored this run or last attempt: {str(msg)[:120]}")

        # Warnings on derived series and EPS input
        fpe_age = ages.get("fwd_pe_history")
        if fpe_age is not None and fpe_age > THRESHOLDS["warn_fwd_pe_history_days"]:
            warnings.append(
                f"fwd_pe_history last entry is {fpe_age} days old "
                f"(warn > {THRESHOLDS['warn_fwd_pe_history_days']})"
            )

        # Rule 6 + EPS age warning
        eps = (data.get("valuation_inputs") or {}).get(EPS_VI_ID) or {}
        eps_src = eps.get("source")
        ok6 = False
        if eps_src == "override":
            try:
                ok6 = abs(float(eps.get("value")) - float(cfg_eps.get("value"))) <= 1e-9
            except (TypeError, ValueError):
                ok6 = False
            if not ok6:
                failures.append(
                    f"rule 6: {EPS_VI_ID} value {eps.get('value')!r} does not match "
                    f"config override {cfg_eps.get('value')!r}"
                )
        elif eps_src == "factset":
            as_of = _to_date(eps.get("as_of") or eps.get("effective_date"))
            ov_date = _to_date(cfg_eps.get("effective_date"))
            try:
                in_range = 200.0 <= float(eps.get("value")) <= 600.0
            except (TypeError, ValueError):
                in_range = False
            if as_of is None or (ov_date is not None and as_of <= ov_date):
                failures.append(
                    f"rule 6: {EPS_VI_ID} source is 'factset' but as_of {as_of} is not newer "
                    f"than config override effective_date {ov_date}"
                )
            elif not in_range:
                failures.append(
                    f"rule 6: {EPS_VI_ID} factset value {eps.get('value')!r} outside 200-600"
                )
        else:
            failures.append(
                f"rule 6: {EPS_VI_ID} source is {eps_src!r}, expected 'override' or 'factset'"
            )
        eff = _to_date(eps.get("as_of") or eps.get("effective_date"))
        if eff is not None:
            eps_age = (today - eff).days
            if eps_age > THRESHOLDS["warn_eps_effective_days"]:
                warnings.append(
                    f"{EPS_VI_ID} effective_date {eff.isoformat()} is {eps_age} days old "
                    f"(warn > {THRESHOLDS['warn_eps_effective_days']})"
                )

    # m5: the NYSE holiday table ends with market_calendar.CALENDAR_YEARS.
    cal_end = date(max(market_calendar.CALENDAR_YEARS), 12, 31)
    days_left = (cal_end - today).days
    if days_left <= THRESHOLDS["warn_calendar_end_days"]:
        warnings.append(
            f"market_calendar covers NYSE holidays only to {cal_end.isoformat()} "
            f"({days_left} days left); add the next year to market_calendar.py"
        )

    # Rule 3 (runs even if rule 1 failed; script/data comparison skipped then)
    check_html(html_path, data, failures)

    return {
        "ok": not failures,
        "run_id": os.environ.get("GITHUB_RUN_ID") or None,
        "generated_at": now.isoformat(timespec="seconds"),
        "last_refreshed": last_refreshed,
        "failures": failures,
        "warnings": warnings,
        "series": series_report,
    }


def print_report(report: dict) -> None:
    print(f"PEC healthcheck  generated_at={report['generated_at']}  "
          f"last_refreshed={report['last_refreshed']}  run_id={report['run_id']}")
    print()
    print(f"{'series':<24}{'last_obs':<12}{'age_d':>6}  status")
    print("-" * 52)
    for sid, r in report["series"].items():
        age = "-" if r["age_days"] is None else str(r["age_days"])
        print(f"{sid:<24}{(r['last_obs'] or '-'):<12}{age:>6}  {r['status']}")
    print()
    print(f"WARNINGS ({len(report['warnings'])})")
    for w in report["warnings"]:
        print(f"  - {w}")
    print(f"FAILURES ({len(report['failures'])})")
    for f in report["failures"]:
        print(f"  - {f}")
    print()
    print("RESULT: " + ("OK" if report["ok"] else "FAIL"))


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="PEC post-refresh healthcheck")
    ap.add_argument("--data", default=str(HERE / "data_v2.json"))
    ap.add_argument("--html", default=str(HERE / "PEC.html"))
    ap.add_argument("--config", default=str(HERE / "config" / "series_v2.yaml"))
    ap.add_argument("--out", default=str(HERE / "health.json"))
    ap.add_argument("--now", default=None, help="ISO8601 timestamp (tests); default now")
    args = ap.parse_args(argv)

    report = run_checks(Path(args.data), Path(args.html), Path(args.config),
                        _parse_now(args.now))
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
