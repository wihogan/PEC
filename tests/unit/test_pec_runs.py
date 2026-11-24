"""tools/pec_runs.sh (QA M4 notify de-duplication helper), offline via a PATH shim for `gh`.

The shim records its arguments and prints a canned `gh run list --json` payload.
"""
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from conftest import REPO

SCRIPT = REPO / "tools" / "pec_runs.sh"
PT = ZoneInfo("America/Los_Angeles")
CUR = 900

pytestmark = pytest.mark.skipif(shutil.which("jq") is None or shutil.which("bash") is None,
                                reason="jq/bash required")

SHIM = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$GH_ARGS_FILE"
if [ -n "${GH_FAIL:-}" ]; then echo "gh: HTTP 401" >&2; exit 1; fi
cat "$GH_FIXTURE"
"""


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)
TODAY_PT_MIDNIGHT = datetime.now(PT).replace(hour=0, minute=0, second=0, microsecond=0)
TODAY = _iso(NOW - timedelta(seconds=30))
YESTERDAY = _iso(TODAY_PT_MIDNIGHT - timedelta(minutes=1))            # 23:59 PT yesterday
YESTERDAY_AFTERNOON = _iso(TODAY_PT_MIDNIGHT - timedelta(hours=9))     # 15:00 PT yesterday

PT_MIDNIGHT_DEFECT = pytest.mark.xfail(
    strict=True,
    reason=(
        "tools/pec_runs.sh failures-today (GNU date path): `date -u -d \"$today 00:00:00\"` parses the "
        "time as UTC, so 'today' starts at 00:00 UTC = 16:00 PST / 17:00 PDT the previous day; failed "
        "production runs created in that evening window count as today's and can suppress the first "
        "alert of the PT day (M4). The macOS (-j -f) path is correct."
    ),
)


def run(rid, conclusion, created=TODAY, title="PEC refresh [state]", status="completed"):
    return {"databaseId": rid, "conclusion": conclusion, "status": status, "createdAt": created,
            "displayTitle": title}


@pytest.fixture
def gh(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "gh"
    shim.write_text(SHIM)
    shim.chmod(0o755)

    def call(cmd, rid=CUR, runs=(), fail=False, repo="owner/pec", extra_env=None):
        fixture = tmp_path / "runs.json"
        fixture.write_text(json.dumps(list(runs)))
        env = {k: v for k, v in os.environ.items() if k not in ("REPO", "GITHUB_REPOSITORY", "GH_FAIL")}
        env.update(PATH=f"{bindir}:{os.environ['PATH']}", GH_FIXTURE=str(fixture),
                   GH_ARGS_FILE=str(tmp_path / "args.txt"), GH_TOKEN="ghs_secret_token_value")
        if repo is not None:
            env["GITHUB_REPOSITORY"] = repo
        if fail:
            env["GH_FAIL"] = "1"
        env.update(extra_env or {})
        args = ["bash", str(SCRIPT)] + ([cmd] if cmd is not None else []) + ([str(rid)] if rid is not None else [])
        r = subprocess.run(args, env=env, capture_output=True, text=True, timeout=30)
        argfile = tmp_path / "args.txt"
        r.gh_args = argfile.read_text().splitlines() if argfile.exists() else None
        return r

    return call


def test_gh_invocation(gh):
    r = gh("previous", runs=[run(1, "success")])
    assert r.returncode == 0
    assert r.gh_args == ["run", "list", "-R", "owner/pec", "-w", "refresh.yml", "--branch", "main",
                         "--limit", "50", "--json", "databaseId,conclusion,status,createdAt,displayTitle"]
    assert "ghs_secret_token_value" not in r.stdout + r.stderr


def test_repo_env_fallback(gh):
    r = gh("previous", runs=[run(1, "success")], repo=None, extra_env={"REPO": "other/pec"})
    assert r.returncode == 0 and r.gh_args[3] == "other/pec"


@pytest.mark.parametrize(
    "runs, expected",
    [
        # newest first, as gh returns them
        ([run(CUR, None, status="in_progress"), run(5, "cancelled"), run(4, "failure", title="PEC refresh [TEST]"),
          run(3, "failure"), run(2, "success")], "failure"),
        ([run(5, "skipped"), run(3, "success"), run(2, "failure")], "success"),
        ([run(CUR, "failure"), run(3, "success")], "success"),             # current run excluded
        ([run(6, None, status="in_progress"), run(3, "failure")], "failure"),  # incomplete ignored
        ([run(4, "failure", title="PEC refresh [state-test]")], "none"),     # not production
        ([], "none"),
    ],
)
def test_previous(gh, runs, expected):
    r = gh("previous", runs=runs)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == expected


def test_failures_today(gh):
    runs = [
        run(CUR, "failure"),                                  # current run: excluded
        run(8, "failure"),
        run(7, "failure"),
        run(6, "success"),
        run(5, "cancelled"),
        run(4, "failure", title="PEC refresh [TEST]"),
        run(3, None, status="in_progress"),
        run(2, "failure", created=YESTERDAY_AFTERNOON),       # yesterday
    ]
    r = gh("failures-today", runs=runs)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "2"


def test_failures_today_none(gh):
    assert gh("failures-today", runs=[run(2, "failure", created=YESTERDAY_AFTERNOON)]).stdout.strip() == "0"


@PT_MIDNIGHT_DEFECT
def test_failures_today_excludes_yesterday_evening_pt(gh):
    assert gh("failures-today", runs=[run(2, "failure", created=YESTERDAY)]).stdout.strip() == "0"


@pytest.mark.parametrize("cmd, expected", [("previous", "none"), ("failures-today", "0")])
def test_gh_failure_is_safe_default(gh, cmd, expected):
    r = gh(cmd, fail=True)
    assert r.returncode == 0
    assert r.stdout.strip() == expected
    assert "gh run list failed" in r.stderr


@pytest.mark.parametrize(
    "kwargs",
    [{"repo": None}, {"rid": "abc"}, {"rid": "12; rm -rf /"}, {"cmd": "bogus"}],
)
def test_usage_errors(gh, kwargs):
    cmd = kwargs.pop("cmd", "previous")
    r = gh(cmd, runs=[run(1, "success")], **kwargs)
    assert r.returncode == 2
    assert "usage" in r.stderr


def test_missing_run_id_defaults_to_zero(gh):
    r = gh("previous", rid=None, runs=[run(1, "failure")])
    assert r.returncode == 0 and r.stdout.strip() == "failure"
