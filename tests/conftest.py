"""Shared fixtures for the offline unit suite (plan §6.1, review N9).

Every test runs with:
  * refresh_v2 output/backup paths and HERE redirected to tmp_path;
  * spglobal / multpl / yahoo cache paths redirected to tmp_path
    (spglobal's seed YAML is a tmp copy of config/forward_eps_seed.yaml);
  * sockets disabled by pytest-socket (see pytest.ini addopts).

refresh_v2.CONFIG_PATH is left pointing at the real (read-only) config unless a
test writes a synthetic one and patches it itself.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Importing refresh_v2 runs sys.stdout.reconfigure() and logging.basicConfig();
# both are verified to work under every pytest capture mode (fd, sys, no).
import refresh_v2  # noqa: E402
from fetchers import factset_eps, multpl, shiller, spglobal, yahoo  # noqa: E402

REAL_CONFIG = REPO / "config" / "series_v2.yaml"
REAL_SEED = REPO / "config" / "forward_eps_seed.yaml"
TEMPLATE = REPO / "PECtemplate.html"

# Series added to the config after the zip baseline (tests/fixtures/baseline_data_v2.json, frozen
# laptop output) was captured, mapped to the baseline series whose data stands in for them.
# Amendment C: sp500_earnings_nominal (Shiller E as published) - same shape as sp500_earnings.
BASELINE_STAND_INS = {"sp500_earnings_nominal": "sp500_earnings"}


def baseline_for_config(config: dict) -> dict:
    """A deep COPY of the frozen baseline whose series ids match `config` (plus fwd_pe_history).

    Ids the config gained after the baseline was captured are filled from BASELINE_STAND_INS
    (data and status copied); any other mismatch fails loudly so a config change is noticed.
    The fixture file itself is never modified.
    """
    import copy
    import json as _json

    base = _json.loads((FIXTURES / "baseline_data_v2.json").read_text(encoding="utf-8"))
    want = {e["id"] for e in config["series"]}
    for sid in sorted(want - set(base["series"])):
        src = BASELINE_STAND_INS.get(sid)
        assert src, f"config id {sid!r} missing from baseline and has no stand-in in conftest"
        base["series"][sid] = copy.deepcopy(base["series"][src])
        st = base["metadata"]["statuses"].get(src)
        if st is not None:
            base["metadata"]["statuses"][sid] = copy.deepcopy(st)
    extra = set(base["series"]) - want - {"fwd_pe_history"}
    assert not extra, f"baseline ids not in config: {sorted(extra)}"
    return base


@pytest.fixture(autouse=True)
def isolate_paths(tmp_path, monkeypatch):
    """Redirect every filesystem side effect of the code under test into tmp_path."""
    here = tmp_path / "app"
    here.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()

    monkeypatch.setattr(refresh_v2, "HERE", here)
    monkeypatch.setattr(refresh_v2, "OUTPUT_PATH", here / "data_v2.json")
    monkeypatch.setattr(refresh_v2, "BACKUP_DIR", here / "backups")

    seed = tmp_path / "forward_eps_seed.yaml"
    shutil.copy(REAL_SEED, seed)
    monkeypatch.setattr(spglobal, "_CACHE_DIR", cache)
    monkeypatch.setattr(spglobal, "_CACHE_XLSX", cache / "sp500_eps_est.xlsx")
    monkeypatch.setattr(spglobal, "_CACHE_JSON", cache / "forward_eps_ntm.json")
    monkeypatch.setattr(spglobal, "_HISTORY_JSON", cache / "forward_eps_ntm_history.json")
    monkeypatch.setattr(spglobal, "_SEED_YAML", seed)

    monkeypatch.setattr(factset_eps, "_CACHE_DIR", cache)
    monkeypatch.setattr(factset_eps, "_FACTSET_CACHE", cache / "factset_eps_latest.json")
    # EPS_CHANGE_FLAG is bound to the real HERE at import time; redirect it too.
    monkeypatch.setattr(refresh_v2, "EPS_CHANGE_FLAG", here / "eps_change.txt")

    monkeypatch.setattr(multpl, "_CACHE_DIR", cache)
    # Shiller: health marker file and per-process caches (workbook, ^GSPC, negative cache, budget clock)
    monkeypatch.setattr(shiller, "_CACHE_DIR", cache)
    monkeypatch.setattr(shiller, "_HEALTH_PATH", cache / "shiller_health.json")
    monkeypatch.setattr(shiller, "_WORKBOOK_CACHE", {})
    monkeypatch.setattr(shiller, "_MTD_CACHE", {})
    monkeypatch.setattr(yahoo, "_CACHE_DIR", cache)

    # Never let a unit test see a real key; neutralise workflow-level env
    # (refresh.yml sets PEC_EPS_OVERRIDE_ONLY=1 for the job that also runs pytest).
    # refresh.yml also sets PEC_EPS_MODE=factset at workflow level.
    for var in ("FRED_API_KEY", "PEC_EPS_OVERRIDE_ONLY", "PEC_EPS_MODE", "GITHUB_RUN_ID"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# Timezone control
# ---------------------------------------------------------------------------
class _TZ:
    """Context manager: set process TZ (os.environ + time.tzset) and restore."""

    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        self.old = os.environ.get("TZ")
        os.environ["TZ"] = self.name
        time.tzset()
        return self

    def __exit__(self, *exc):
        if self.old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.old
        time.tzset()
        return False


def set_tz(name: str) -> _TZ:
    return _TZ(name)


TZ_NAMES = ["America/Los_Angeles", "UTC"]


@pytest.fixture(params=TZ_NAMES)
def tz(request):
    """Run the test once under TZ=America/Los_Angeles and once under TZ=UTC."""
    with set_tz(request.param):
        yield request.param


@pytest.fixture
def la_tz():
    with set_tz("America/Los_Angeles"):
        yield "America/Los_Angeles"


# ---------------------------------------------------------------------------
# Frozen clock (datetime subclass monkeypatch; no freezegun / time-machine)
# ---------------------------------------------------------------------------
def make_frozen_datetime(instant: datetime) -> type:
    """Return a datetime subclass whose now() is pinned to `instant` (tz-aware).

    now(tz)  -> instant converted to tz (aware)
    now()    -> instant in the *process local* zone, naive (honours TZ/tzset)
    today()  -> same as now()
    """
    if instant.tzinfo is None:
        raise ValueError("frozen instant must be tz-aware")

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return instant.astimezone().replace(tzinfo=None)
            return instant.astimezone(tz)

        @classmethod
        def today(cls):
            return cls.now()

        @classmethod
        def utcnow(cls):
            return instant.astimezone(timezone.utc).replace(tzinfo=None)

    return FrozenDatetime


@pytest.fixture
def freeze(monkeypatch):
    """freeze(instant, module=refresh_v2): pin module.datetime.now() to instant."""

    def _freeze(instant: datetime, module=refresh_v2):
        monkeypatch.setattr(module, "datetime", make_frozen_datetime(instant))

    return _freeze


def local_naive(instant: datetime) -> datetime:
    """The naive local wall-clock time the code would record for `instant`."""
    return instant.astimezone().replace(tzinfo=None)


def local_instant(y, mo, d, h=0, mi=0, s=0) -> datetime:
    """Aware instant for a wall-clock time in the current process TZ."""
    return datetime(y, mo, d, h, mi, s).astimezone()
