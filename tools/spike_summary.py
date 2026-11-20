#!/usr/bin/env python3
"""Summarise one connectivity-spike run as a single JSON line.

Usage: spike_summary.py RUN_ID EVENT ELAPSED_S REFRESH_RC
           [--config PATH] [--data PATH] [--log PATH] [--out PATH]

Secret safety: the value of FRED_API_KEY (if set) and any ``api_key=...``
pattern are replaced with ``***`` in every string written, and in the log
file in place (the workflow uploads it as an artifact).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
API_KEY_RE = re.compile(r"api_key=[A-Za-z0-9]+")
EPS_RE = re.compile(r"spglobal|factset|yardeni|xlsx", re.I)


def mask(text, key=None):
    """Mask the FRED key value and api_key=... patterns in a string."""
    if not isinstance(text, str):
        return text
    if key is None:
        key = os.environ.get("FRED_API_KEY", "")
    if key:
        text = text.replace(key, "***")
    return API_KEY_RE.sub("***", text)


def _load_data(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def build_summary(config, data_path, log_path, run_id, event, elapsed, rc):
    with open(config, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    series = [s for s in (cfg.get("series") or []) if isinstance(s, dict) and "id" in s]
    data = _load_data(data_path)
    statuses = {}
    if data:
        statuses = ((data.get("metadata") or {}).get("statuses")) or {}

    per_source, errors = {}, {}
    for s in series:
        sid, src = s["id"], s.get("source", "unknown")
        bucket = per_source.setdefault(
            src, {"attempted": 0, "ok": 0, "fail": 0, "failed_ids": []})
        bucket["attempted"] += 1
        if data is None:
            st, err = {"status": "error"}, "no output"
        else:
            st = statuses.get(sid) or {}
            err = st.get("error")
        if st.get("status") == "ok":
            bucket["ok"] += 1
        else:
            bucket["fail"] += 1
            bucket["failed_ids"].append(sid)
            errors[sid] = str(err if err else (st.get("status") or "missing"))[:160]

    eps_rec = {}
    if data:
        eps_rec = ((data.get("valuation_inputs") or {}).get("forward_eps_ntm")) or {}
    eps = {k: eps_rec.get(k) for k in ("source", "value", "effective_date")}

    eps_log = []
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if EPS_RE.search(line) and len(eps_log) < 8:
                    eps_log.append(line.rstrip("\n")[:200])
    except OSError:
        pass

    line = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run_id": run_id,
        "event": event,
        "elapsed_s": elapsed,
        "refresh_rc": rc,
        "per_source": per_source,
        "errors": errors,
        "eps": eps,
        "eps_log": eps_log,
        "fred_key_present": bool(os.environ.get("FRED_API_KEY", "")),
    }
    return line


def _mask_obj(obj, key):
    if isinstance(obj, str):
        return mask(obj, key)
    if isinstance(obj, list):
        return [_mask_obj(x, key) for x in obj]
    if isinstance(obj, dict):
        return {mask(k, key): _mask_obj(v, key) for k, v in obj.items()}
    return obj


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return float(v)
        except (TypeError, ValueError):
            return v


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run_id")
    p.add_argument("event")
    p.add_argument("elapsed_s")
    p.add_argument("refresh_rc")
    p.add_argument("--config", default=str(ROOT / "config" / "series_v2.yaml"))
    p.add_argument("--data", default=str(ROOT / "data_v2.json"))
    p.add_argument("--log", default=str(ROOT / "refresh.log"))
    p.add_argument("--out", default=str(ROOT / "spike_line.json"))
    a = p.parse_args(argv)

    key = os.environ.get("FRED_API_KEY", "")

    # Mask the log in place first (uploaded as an artifact).
    try:
        raw = Path(a.log).read_text(encoding="utf-8", errors="replace")
        Path(a.log).write_text(mask(raw, key), encoding="utf-8")
    except OSError:
        pass

    line = build_summary(a.config, a.data, a.log, a.run_id, a.event,
                         _num(a.elapsed_s), _num(a.refresh_rc))
    line = _mask_obj(line, key)
    text = json.dumps(line, separators=(",", ":"))
    Path(a.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
