#!/usr/bin/env python3
"""
set_eps.py - update the approved forward EPS override in config/series_v2.yaml.

Usage:
    python tools/set_eps.py --value 360.12 --date 2026-11-20
                            [--config config/series_v2.yaml] [--today YYYY-MM-DD]

Validates value (float in [200, 600]) and date (ISO, not after today), then edits
ONLY the `value:` and `effective_date:` lines inside
valuation_inputs.forward_eps_ntm.manual_override by text, preserving comments
and the date's quoting style. Re-parses the YAML to verify; on any failure the
file is left unchanged and the exit status is 1.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent.parent
LO, HI = 200.0, 600.0
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def fail(msg: str) -> int:
    print(f"set_eps: ERROR: {msg}", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--value", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--config", default=str(HERE / "config" / "series_v2.yaml"))
    ap.add_argument("--today", default=None, help="YYYY-MM-DD (tests); default today")
    args = ap.parse_args(argv)

    try:
        value = float(args.value)
    except ValueError:
        return fail(f"value {args.value!r} is not a number")
    if value != value or not (LO <= value <= HI):
        return fail(f"value {args.value!r} outside allowed range {LO:g}-{HI:g}")

    if not ISO_RE.match(args.date):
        return fail(f"date {args.date!r} is not ISO YYYY-MM-DD")
    try:
        eff = date.fromisoformat(args.date)
        today = date.fromisoformat(args.today) if args.today else date.today()
    except ValueError as exc:
        return fail(f"invalid date: {exc}")
    if eff > today:
        return fail(f"date {args.date} is after today ({today})")

    path = Path(args.config)
    raw = path.read_bytes().decode("utf-8")
    lines = raw.splitlines(keepends=True)

    # Locate valuation_inputs > forward_eps_ntm > manual_override block.
    def indent_of(s: str) -> int:
        return len(s) - len(s.lstrip(" "))

    def find(start: int, key: str, min_indent: int, end: int):
        for i in range(start, end):
            s = lines[i]
            if s.strip() and not s.lstrip().startswith("#") \
                    and indent_of(s) >= min_indent and s.lstrip().startswith(key + ":"):
                return i
        return None

    def block_end(i: int) -> int:
        base = indent_of(lines[i])
        j = i + 1
        while j < len(lines):
            s = lines[j]
            if s.strip() and not s.lstrip().startswith("#") and indent_of(s) <= base:
                break
            j += 1
        return j

    vi = next((i for i, s in enumerate(lines) if re.match(r"valuation_inputs:\s*(#.*)?$", s.rstrip("\r\n"))), None)
    if vi is None:
        return fail("valuation_inputs not found")
    fe = find(vi + 1, "forward_eps_ntm", 1, block_end(vi))
    if fe is None:
        return fail("forward_eps_ntm not found")
    mo = find(fe + 1, "manual_override", indent_of(lines[fe]) + 1, block_end(fe))
    if mo is None:
        return fail("manual_override not found")
    mo_end = block_end(mo)

    vline = find(mo + 1, "value", indent_of(lines[mo]) + 1, mo_end)
    dline = find(mo + 1, "effective_date", indent_of(lines[mo]) + 1, mo_end)
    if vline is None or dline is None:
        return fail("value/effective_date lines not found under manual_override")

    vm = re.match(r"^(\s*value:\s*)(\S+?)(\s*(?:#.*)?(?:\r?\n)?)$", lines[vline])
    dm = re.match(r"""^(\s*effective_date:\s*)(["']?)([^"'\s#]*)(["']?)(\s*(?:#.*)?(?:\r?\n)?)$""", lines[dline])
    if not vm or not dm:
        return fail("unrecognised formatting on value/effective_date lines")

    lines[vline] = f"{vm.group(1)}{value!r}{vm.group(3)}"
    lines[dline] = f"{dm.group(1)}{dm.group(2)}{args.date}{dm.group(4)}{dm.group(5)}"
    new = "".join(lines)

    # Verify by re-parsing.
    try:
        mo_new = yaml.safe_load(new)["valuation_inputs"]["forward_eps_ntm"]["manual_override"]
        ok = float(mo_new["value"]) == value and str(mo_new["effective_date"]) == args.date
    except Exception as exc:  # noqa: BLE001
        return fail(f"verification re-parse failed: {exc}")
    if not ok:
        return fail(f"verification mismatch: parsed {mo_new!r}")

    path.write_bytes(new.encode("utf-8"))
    print(f"set_eps: manual_override set to value={value!r} effective_date={args.date}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
