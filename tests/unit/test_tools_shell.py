"""tools/state.sh and tools/leak_scan.sh via subprocess (plan §2, §3, N7, N14).

Offline: state.sh is exercised against a local bare repo (file:// remote) in tmp_path.
Git runs with an isolated config (no global/system config) and a fixed identity.
"""
import json
import os
import shutil
import subprocess

import pytest

from conftest import REPO

STATE_SH = REPO / "tools" / "state.sh"
LEAK_SH = REPO / "tools" / "leak_scan.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="git/bash required")


def _env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
        GIT_TERMINAL_PROMPT="0",
    )
    env.update(extra)
    return env


def _git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, env=_env(), capture_output=True, text=True, check=check)


def _sh(script, cwd, *args, **env):
    return subprocess.run(["bash", str(script), *args], cwd=cwd, env=_env(**env), capture_output=True, text=True, timeout=60)


# ---------------------------------------------------------------------------
# state.sh
# ---------------------------------------------------------------------------
SEED = {"seed": True, "series": {}}


@pytest.fixture
def repo(tmp_path):
    """A working clone with a main commit, pushed to a local bare 'origin'."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "init", "-q", "-b", "main", str(work))
    (work / "tests" / "fixtures").mkdir(parents=True)
    (work / "tests" / "fixtures" / "baseline_data_v2.json").write_text(json.dumps(SEED))
    (work / "README.md").write_text("code\n")
    (work / "secret.env").write_text("NOT_STATE=1\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "init")
    _git(work, "remote", "add", "origin", origin.as_uri())
    _git(work, "push", "-q", "origin", "main")
    return work


def _clone(tmp_path, repo, name):
    dst = tmp_path / name
    _git(tmp_path, "clone", "-q", _git(repo, "remote", "get-url", "origin").stdout.strip(), str(dst))
    return dst


@pytest.mark.parametrize("ref", ["main", "", "state-prod", "state; rm -rf /", "refs/heads/state"])
@pytest.mark.parametrize("cmd", ["restore", "save"])
def test_state_refuses_bad_ref(repo, cmd, ref):
    (repo / "data_v2.json").write_text("{}")
    r = _sh(STATE_SH, repo, cmd, ref)
    assert r.returncode == 2
    assert "ref must be" in r.stderr
    assert _git(repo, "ls-remote", "--heads", "origin").stdout.count("refs/heads/") == 1  # only main


def test_state_unknown_command(repo):
    r = _sh(STATE_SH, repo, "frobnicate", "state-test")
    assert r.returncode == 2


def test_state_restore_absent_seeds_from_baseline_with_warning(repo):
    r = _sh(STATE_SH, repo, "restore", "state-test")
    assert r.returncode == 0, r.stderr
    assert "::warning::" in r.stdout and "does not exist" in r.stdout
    assert json.loads((repo / "data_v2.json").read_text()) == SEED


def test_state_restore_fails_when_remote_unreachable(repo, tmp_path):
    """N14: a failed ls-remote must fail the job, never trigger a silent cold start."""
    _git(repo, "remote", "set-url", "origin", (tmp_path / "missing.git").as_uri())
    r = _sh(STATE_SH, repo, "restore", "state")
    assert r.returncode == 1
    assert not (repo / "data_v2.json").exists()


def test_state_save_then_restore_round_trip(repo, tmp_path):
    payload = {"metadata": {"last_refreshed": "2026-11-20T14:17:00"}, "series": {"x": {"dates": [], "values": []}}}
    (repo / "data_v2.json").write_text(json.dumps(payload))
    r = _sh(STATE_SH, repo, "save", "state-test")
    assert r.returncode == 0, r.stderr

    fresh = _clone(tmp_path, repo, "fresh")
    r = _sh(STATE_SH, fresh, "restore", "state-test")
    assert r.returncode == 0, r.stderr
    assert "::warning::" not in r.stdout
    assert json.loads((fresh / "data_v2.json").read_text()) == payload


def test_state_save_persists_only_data_json_as_orphan(repo):
    (repo / "data_v2.json").write_text('{"v": 1}')
    (repo / "PEC.html").write_text("<html>")                # generated files must not ride along
    (repo / "health.json").write_text("{}")
    assert _sh(STATE_SH, repo, "save", "state-test").returncode == 0

    sha = _git(repo, "ls-remote", "origin", "refs/heads/state-test").stdout.split()[0]
    _git(repo, "fetch", "-q", "origin", "state-test")
    assert _git(repo, "ls-tree", "-r", "--name-only", sha).stdout.split() == ["data_v2.json"]
    assert _git(repo, "rev-list", "--count", sha).stdout.strip() == "1"  # orphan: single commit
    assert _git(repo, "show", f"{sha}:data_v2.json").stdout == '{"v": 1}'
    # main untouched; working tree untouched
    assert _git(repo, "status", "--porcelain", "--untracked-files=no").stdout == ""
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"


def test_state_save_requires_data_file(repo):
    r = _sh(STATE_SH, repo, "save", "state-test")
    assert r.returncode == 1
    assert "missing" in r.stderr


def test_state_restore_fails_if_branch_lacks_data_file(repo):
    _git(repo, "push", "-q", "origin", "main:refs/heads/state-test")   # branch exists, no data_v2.json
    r = _sh(STATE_SH, repo, "restore", "state-test")
    assert r.returncode == 1
    assert not (repo / "data_v2.json").exists()
    assert not (repo / "data_v2.json.tmp").exists()


def test_state_save_is_force_push_single_commit(repo, tmp_path):
    """Each save replaces the branch with one new orphan commit (separate clones, as in CI)."""
    (repo / "data_v2.json").write_text('{"v": 1}')
    assert _sh(STATE_SH, repo, "save", "state-test").returncode == 0
    other = _clone(tmp_path, repo, "other")
    (other / "data_v2.json").write_text('{"v": 2}')
    assert _sh(STATE_SH, other, "save", "state-test").returncode == 0
    sha = _git(repo, "ls-remote", "origin", "refs/heads/state-test").stdout.split()[0]
    _git(repo, "fetch", "-q", "origin", "state-test")
    assert _git(repo, "rev-list", "--count", sha).stdout.strip() == "1"
    assert _git(repo, "show", f"{sha}:data_v2.json").stdout == '{"v": 2}'


def test_state_second_save_from_same_clone(repo):
    (repo / "data_v2.json").write_text('{"v": 1}')
    assert _sh(STATE_SH, repo, "save", "state-test").returncode == 0
    (repo / "data_v2.json").write_text('{"v": 2}')
    r = _sh(STATE_SH, repo, "save", "state-test")
    assert r.returncode == 0, r.stderr


def test_state_save_cleans_up_worktree(repo):
    (repo / "data_v2.json").write_text('{"v": 1}')
    assert _sh(STATE_SH, repo, "save", "state-test").returncode == 0
    wts = _git(repo, "worktree", "list", "--porcelain").stdout
    assert wts.count("worktree ") == 1


# ---------------------------------------------------------------------------
# leak_scan.sh
# ---------------------------------------------------------------------------
KEY = "abcdef0123456789abcdef0123456789"


@pytest.fixture
def files(tmp_path):
    for name in ("PEC.html", "data_v2.json", "health.json"):
        (tmp_path / name).write_text('{"ok": true, "note": "observation_start=2010"}\n')
    return tmp_path


def _scan(cwd, *names, **env):
    return _sh(LEAK_SH, cwd, *names, **env)


def test_leak_scan_clean_pass(files):
    r = _scan(files, "PEC.html", "data_v2.json", "health.json", FRED_API_KEY=KEY)
    assert r.returncode == 0
    assert "OK (3 file(s) scanned)" in r.stdout
    assert KEY not in r.stdout + r.stderr


def test_leak_scan_finds_literal_key_without_printing_it(files):
    # key embedded mid-token (e.g. in a URL) must still be found
    (files / "data_v2.json").write_text(f'{{"error": "https://api.stlouisfed.org/x?key{KEY}&y=1"}}')
    r = _scan(files, "PEC.html", "data_v2.json", "health.json", FRED_API_KEY=KEY)
    assert r.returncode == 1
    assert "FRED_API_KEY value found in data_v2.json" in r.stderr
    assert KEY not in r.stdout + r.stderr
    assert "OK" not in r.stdout


def test_leak_scan_key_with_regex_metacharacters_is_literal(files):
    key = "a.b*c[d]e+f?g"
    (files / "health.json").write_text("axbbbbcdde+f?g")  # would match if treated as a regex
    assert _scan(files, "health.json", FRED_API_KEY=key).returncode == 0
    (files / "health.json").write_text(f"prefix {key} suffix")
    r = _scan(files, "health.json", FRED_API_KEY=key)
    assert r.returncode == 1 and key not in r.stdout + r.stderr


def test_leak_scan_api_key_pattern_without_env_key(files):
    (files / "PEC.html").write_text("GET /fred/series?api_key=ZZZZZZZZZZZZZZZZ1234&file_type=json")
    r = _scan(files, "PEC.html", "health.json")
    assert r.returncode == 1
    assert "api_key=<token> pattern found in PEC.html" in r.stderr
    assert "ZZZZZZZZZZZZZZZZ1234" not in r.stdout + r.stderr


def test_leak_scan_short_api_key_value_not_flagged(files):
    (files / "PEC.html").write_text("api_key=short123")  # < 16 chars
    assert _scan(files, "PEC.html").returncode == 0


def test_leak_scan_empty_env_key_does_not_match_everything(files):
    r = _scan(files, "PEC.html", FRED_API_KEY="")
    assert r.returncode == 0


def test_leak_scan_missing_file_fails(files):
    r = _scan(files, "PEC.html", "nope.json")
    assert r.returncode == 1
    assert "nope.json not found" in r.stderr


def test_leak_scan_requires_arguments(files):
    assert _scan(files).returncode == 2


# ---------------------------------------------------------------------------
# state.sh optional EPS files (.cache/forward_eps_ntm_history.json, .cache/factset_eps_latest.json)
# ---------------------------------------------------------------------------
HIST = "forward_eps_ntm_history.json"
FSC = "factset_eps_latest.json"


def _write_cache(repo, name, payload):
    (repo / ".cache").mkdir(exist_ok=True)
    (repo / ".cache" / name).write_text(json.dumps(payload))


def _tree(repo, ref="state-test"):
    sha = _git(repo, "ls-remote", "origin", f"refs/heads/{ref}").stdout.split()[0]
    _git(repo, "fetch", "-q", "origin", ref)
    return sha, sorted(_git(repo, "ls-tree", "-r", "--name-only", sha).stdout.split())


@pytest.mark.parametrize(
    "present, expected",
    [
        ((HIST, FSC), ["data_v2.json", FSC, HIST]),
        ((HIST,), ["data_v2.json", HIST]),
        ((FSC,), ["data_v2.json", FSC]),
        ((), ["data_v2.json"]),
    ],
)
def test_state_save_optional_files(repo, present, expected):
    (repo / "data_v2.json").write_text('{"v": 1}')
    for name in present:
        _write_cache(repo, name, {"name": name})
    _write_cache(repo, "multpl_sp500_pe.html", {"ttl": "never persisted"})   # TTL caches stay local (F4)
    r = _sh(STATE_SH, repo, "save", "state-test")
    assert r.returncode == 0, r.stderr
    sha, tree = _tree(repo)
    assert tree == sorted(expected)
    for name in present:
        assert json.loads(_git(repo, "show", f"{sha}:{name}").stdout) == {"name": name}


@pytest.mark.parametrize("present", [(HIST, FSC), (HIST,), (FSC,)])
def test_state_restore_optional_files_round_trip(repo, tmp_path, present):
    (repo / "data_v2.json").write_text('{"v": 2}')
    for name in present:
        _write_cache(repo, name, {"name": name, "value": 419.64})
    assert _sh(STATE_SH, repo, "save", "state-test").returncode == 0

    fresh = _clone(tmp_path, repo, "fresh")
    r = _sh(STATE_SH, fresh, "restore", "state-test")
    assert r.returncode == 0, r.stderr
    assert json.loads((fresh / "data_v2.json").read_text()) == {"v": 2}
    for name in (HIST, FSC):
        p = fresh / ".cache" / name
        if name in present:
            assert json.loads(p.read_text()) == {"name": name, "value": 419.64}
            assert f"restored .cache/{name}" in r.stdout
        else:
            assert not p.exists()
            assert f"{name} not on 'state-test' (ok)" in r.stdout
        assert not (fresh / ".cache" / f"{name}.tmp").exists()


def test_state_restore_branch_without_optional_files(repo, tmp_path):
    """A state branch saved before this feature (data_v2.json only) restores cleanly."""
    (repo / "data_v2.json").write_text('{"v": 3}')
    assert _sh(STATE_SH, repo, "save", "state-test").returncode == 0
    fresh = _clone(tmp_path, repo, "fresh")
    r = _sh(STATE_SH, fresh, "restore", "state-test")
    assert r.returncode == 0
    assert sorted(p.name for p in (fresh / ".cache").iterdir()) == []


def test_state_restore_keeps_stale_local_optional_file(repo, tmp_path):
    """Documented: a local .cache file is not removed when the branch lacks it
    (irrelevant on fresh CI runners)."""
    (repo / "data_v2.json").write_text('{"v": 4}')
    assert _sh(STATE_SH, repo, "save", "state-test").returncode == 0
    fresh = _clone(tmp_path, repo, "fresh")
    _write_cache(fresh, FSC, {"stale": True})
    assert _sh(STATE_SH, fresh, "restore", "state-test").returncode == 0
    assert json.loads((fresh / ".cache" / FSC).read_text()) == {"stale": True}


def test_state_restore_absent_branch_does_not_create_optional_files(repo):
    r = _sh(STATE_SH, repo, "restore", "state-test")
    assert r.returncode == 0 and "::warning::" in r.stdout
    assert not (repo / ".cache" / HIST).exists() and not (repo / ".cache" / FSC).exists()


def test_state_optional_files_replaced_on_next_save(repo, tmp_path):
    (repo / "data_v2.json").write_text('{"v": 5}')
    _write_cache(repo, HIST, {"n": 1})
    _write_cache(repo, FSC, {"n": 1})
    assert _sh(STATE_SH, repo, "save", "state-test").returncode == 0
    (repo / ".cache" / FSC).unlink()
    _write_cache(repo, HIST, {"n": 2})
    assert _sh(STATE_SH, repo, "save", "state-test").returncode == 0
    sha, tree = _tree(repo)
    assert tree == ["data_v2.json", HIST]                      # orphan replace: FSC dropped
    assert json.loads(_git(repo, "show", f"{sha}:{HIST}").stdout) == {"n": 2}



# ---------------------------------------------------------------------------
# state.sh: optional .cache/shiller_health.json (Shiller QA M3)
# ---------------------------------------------------------------------------
SHH = "shiller_health.json"


@pytest.mark.parametrize("present", [(SHH,), (HIST, FSC, SHH), ()])
def test_state_shiller_health_round_trip(repo, tmp_path, present):
    (repo / "data_v2.json").write_text('{"v": 7}')
    for name in present:
        _write_cache(repo, name, {"name": name, "fail_days": 1})
    assert _sh(STATE_SH, repo, "save", "state-test").returncode == 0
    sha, tree = _tree(repo)
    assert tree == sorted(["data_v2.json", *present])

    fresh = _clone(tmp_path, repo, "fresh")
    r = _sh(STATE_SH, fresh, "restore", "state-test")
    assert r.returncode == 0, r.stderr
    p = fresh / ".cache" / SHH
    if SHH in present:
        assert json.loads(p.read_text()) == {"name": SHH, "fail_days": 1}
        assert f"restored .cache/{SHH}" in r.stdout
    else:
        assert not p.exists() and f"{SHH} not on 'state-test' (ok)" in r.stdout
