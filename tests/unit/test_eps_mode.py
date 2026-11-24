"""PEC_EPS_MODE precedence (factset | override; PEC_EPS_OVERRIDE_ONLY=1 alias; unknown -> override;
unset -> legacy live ladder), carry-forward, record_month_end hook, and eps_change.txt.

Synthetic config and stub fetchers only; override values here are test constants, not the
owner-editable config.
"""
import json
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import yaml

import refresh_v2
from fetchers.base import FetchError

OV_VALUE, OV_DATE = 400.0, "2026-11-13"
VI = {
    "forward_eps_ntm": {
        "fetcher": "spglobal",
        "series_id": "forward_eps_ntm",
        "manual_override": {"value": OV_VALUE, "effective_date": OV_DATE},
    }
}
URL = "https://advantage.factset.com/x/EarningsInsight_112026A.pdf"


class Stubs:
    def __init__(self, fs_result=None, fs_exc=None):
        self.fs_result, self.fs_exc = fs_result, fs_exc
        self.fs_calls, self.sp_calls, self.recorded = [], [], []

    def fetch_forward_eps(self, last_good=None, override=None):
        self.fs_calls.append({"last_good": last_good, "override": override})
        if self.fs_exc:
            raise self.fs_exc
        return dict(self.fs_result)

    def sp_fetch(self, series_id, **kw):
        self.sp_calls.append(kw)
        if kw.get("history"):
            return pd.DataFrame({"date": pd.to_datetime(["2026-10-31"]), "value": [412.86]})
        return pd.DataFrame({"date": pd.to_datetime(["2026-11-23"]), "value": [999.0]})

    def record_month_end(self, as_of, value):
        self.recorded.append((as_of, value))
        return True

    def fetchers(self, with_factset=True):
        f = {"spglobal": SimpleNamespace(fetch=self.sp_fetch, record_month_end=self.record_month_end)}
        if with_factset:
            f["factset_eps"] = SimpleNamespace(fetch_forward_eps=self.fetch_forward_eps)
        return f


def fs(value=419.64, as_of="2026-11-20"):
    return {"value": value, "as_of": as_of, "source": "factset", "pe": 17.1, "price": 7175.83, "url": URL}


def run_vi(stubs, previous=None, vi=VI, with_factset=True):
    statuses = {}
    out = refresh_v2._fetch_valuation_inputs(vi, stubs.fetchers(with_factset), statuses, previous=previous)
    return out["forward_eps_ntm"], statuses["forward_eps_ntm"]


def point_calls(stubs):
    return [c for c in stubs.sp_calls if not c.get("history")]


# ---------------------------------------------------------------------------
# _eps_mode
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "mode, alias, expected",
    [
        (None, None, ""),
        ("factset", None, "factset"),
        ("override", None, "override"),
        ("FACTSET", None, "factset"),
        (" factset ", None, "factset"),
        ("live", None, "override"),          # unknown -> fail safe
        ("fact-set", None, "override"),
        (None, "1", "override"),             # alias
        (None, "0", ""),
        (None, "true", ""),
        ("factset", "1", "factset"),         # PEC_EPS_MODE wins over the alias
        ("", "1", "override"),
    ],
)
def test_eps_mode_resolution(monkeypatch, mode, alias, expected):
    if mode is not None:
        monkeypatch.setenv("PEC_EPS_MODE", mode)
    if alias is not None:
        monkeypatch.setenv("PEC_EPS_OVERRIDE_ONLY", alias)
    assert refresh_v2._eps_mode() == expected


# ---------------------------------------------------------------------------
# factset mode precedence
# ---------------------------------------------------------------------------
@pytest.fixture
def factset_mode(monkeypatch):
    monkeypatch.setenv("PEC_EPS_MODE", "factset")


def test_factset_newer_than_override_wins(factset_mode):
    s = Stubs(fs_result=fs(419.64, "2026-11-20"))
    rec, status = run_vi(s, previous={"forward_eps_ntm": {"value": 412.86, "source": "factset", "as_of": "2026-11-13"}})
    assert rec["source"] == "factset"
    assert rec["value"] == 419.64
    assert rec["effective_date"] == rec["as_of"] == "2026-11-20"
    assert rec["url"] == URL
    assert rec["history"] == {"dates": ["2026-10-31"], "values": [412.86]}
    assert status == {"status": "ok"}
    assert s.recorded == [(date(2026, 11, 20), 419.64)]
    assert point_calls(s) == []                       # legacy spglobal point fetch never used
    assert s.fs_calls == [{"last_good": 412.86, "override": OV_VALUE}]


@pytest.mark.parametrize("as_of", ["2026-11-13", "2026-11-06"])   # equal / older than override
def test_factset_not_newer_override_wins(factset_mode, as_of):
    s = Stubs(fs_result=fs(401.0, as_of))
    rec, _ = run_vi(s)
    assert rec["source"] == "override"
    assert rec["value"] == OV_VALUE and rec["effective_date"] == OV_DATE
    assert rec["as_of"] == OV_DATE and rec["url"] is None
    assert s.recorded == []


def test_factset_unparseable_as_of_override_wins(factset_mode):
    rec, _ = run_vi(Stubs(fs_result=fs(419.64, "n/a")))
    assert rec["source"] == "override"


def test_last_good_falls_back_to_override_without_previous(factset_mode):
    s = Stubs(fs_result=fs())
    run_vi(s, previous={})
    assert s.fs_calls == [{"last_good": OV_VALUE, "override": OV_VALUE}]


@pytest.mark.parametrize(
    "previous, expected_source, expected_value, expected_as_of",
    [
        # prior FactSet value newer than the override is carried forward
        ({"value": 419.64, "source": "factset", "as_of": "2026-11-20", "url": URL}, "factset", 419.64, "2026-11-20"),
        # prior FactSet value not newer than the override -> override
        ({"value": 395.0, "source": "factset", "as_of": "2026-11-13", "url": URL}, "override", OV_VALUE, OV_DATE),
        ({"value": 390.0, "source": "factset", "as_of": "2026-11-06", "url": URL}, "override", OV_VALUE, OV_DATE),
        # prior was the override -> override
        ({"value": OV_VALUE, "source": "override", "as_of": OV_DATE}, "override", OV_VALUE, OV_DATE),
        # prior FactSet record without as_of -> override
        ({"value": 419.64, "source": "factset"}, "override", OV_VALUE, OV_DATE),
        # no prior state
        (None, "override", OV_VALUE, OV_DATE),
    ],
)
def test_factset_failure_carry_forward(factset_mode, previous, expected_source, expected_value, expected_as_of):
    s = Stubs(fs_exc=FetchError("FactSet download failed"))
    prev = {"forward_eps_ntm": previous} if previous is not None else None
    rec, status = run_vi(s, previous=prev)
    assert (rec["source"], rec["value"], rec["as_of"]) == (expected_source, expected_value, expected_as_of)
    assert status == {"status": "ok"}
    assert s.recorded == []                           # carry-forward never writes history
    if expected_source == "factset":
        assert rec["url"] == URL and rec["effective_date"] == "2026-11-20"


def test_factset_failure_without_override_or_prior_is_unavailable(factset_mode):
    vi = {"forward_eps_ntm": {**VI["forward_eps_ntm"], "manual_override": {"value": None, "effective_date": None}}}
    rec, status = run_vi(Stubs(fs_exc=FetchError("down")), vi=vi)
    assert rec["source"] == "unavailable" and rec["value"] is None
    assert status["status"] == "warning" and "down" in status["error"]


def test_factset_without_override_is_used(factset_mode):
    vi = {"forward_eps_ntm": {**VI["forward_eps_ntm"], "manual_override": {}}}
    rec, _ = run_vi(Stubs(fs_result=fs()), vi=vi)
    assert rec["source"] == "factset"


def test_factset_fetcher_not_registered_falls_back(factset_mode):
    s = Stubs(fs_result=fs())
    rec, _ = run_vi(s, with_factset=False)
    assert rec["source"] == "override" and s.fs_calls == []


# ---------------------------------------------------------------------------
# override mode, alias, unknown value, unset (legacy)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("env", [{"PEC_EPS_MODE": "override"}, {"PEC_EPS_OVERRIDE_ONLY": "1"}, {"PEC_EPS_MODE": "bogus"}])
def test_override_modes_never_fetch_live(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    s = Stubs(fs_result=fs())
    rec, _ = run_vi(s, previous={"forward_eps_ntm": {"value": 419.64, "source": "factset", "as_of": "2026-11-20"}})
    assert rec["source"] == "override" and rec["value"] == OV_VALUE
    assert rec["as_of"] == OV_DATE and rec["url"] is None
    assert s.fs_calls == [] and point_calls(s) == [] and s.recorded == []
    assert rec["history"] is not None


def test_unset_mode_uses_legacy_live_ladder():
    s = Stubs(fs_result=fs())
    rec, _ = run_vi(s)
    assert rec["source"] == "live" and rec["value"] == 999.0 and rec["effective_date"] == "2026-11-23"
    assert "as_of" not in rec and "url" not in rec        # legacy record shape
    assert s.fs_calls == [] and len(point_calls(s)) == 1


def test_mode_applies_only_to_forward_eps(factset_mode):
    vi = {"other_input": {"fetcher": "spglobal", "series_id": "x", "manual_override": {"value": 1.0, "effective_date": "2026-01-01"}}}
    s = Stubs(fs_result=fs())
    out = refresh_v2._fetch_valuation_inputs(vi, s.fetchers(), {})
    assert out["other_input"]["source"] == "live" and s.fs_calls == []


# ---------------------------------------------------------------------------
# eps_change.txt
# ---------------------------------------------------------------------------
FLAG = lambda: refresh_v2.EPS_CHANGE_FLAG  # noqa: E731


def test_flag_path_is_isolated(tmp_path):
    assert str(FLAG()).startswith(str(tmp_path))


@pytest.mark.parametrize(
    "prev, new, expected",
    [
        ({"value": 400.0, "source": "override"}, {"value": 419.64, "source": "factset", "as_of": "2026-11-20"},
         "ℹ️ PEC forward EPS updated: 400.00 → 419.64 (FactSet, as of 2026-11-20)\n"),
        ({"value": 419.64, "source": "factset"}, {"value": 419.64, "source": "override", "as_of": "2026-11-20"},
         "ℹ️ PEC forward EPS updated: 419.64 → 419.64 (manual override, as of 2026-11-20)\n"),
        ({"value": 400.0, "source": "factset"}, {"value": 412.04, "source": "factset", "as_of": "2026-11-27"},
         "ℹ️ PEC forward EPS updated: 400.00 → 412.04 (FactSet, as of 2026-11-27)\n"),   # +3.01 %
        ({"value": 400.0, "source": "factset"}, {"value": 387.96, "source": "factset", "effective_date": "2026-11-27"},
         "ℹ️ PEC forward EPS updated: 400.00 → 387.96 (FactSet, as of 2026-11-27)\n"),   # -3.01 %, effective_date
    ],
)
def test_eps_change_flag_written(prev, new, expected):
    refresh_v2._write_eps_change_flag(prev, new)
    assert FLAG().read_text(encoding="utf-8") == expected


@pytest.mark.parametrize(
    "prev, new",
    [
        ({"value": 400.0, "source": "factset"}, {"value": 412.0, "source": "factset"}),   # exactly +3 %
        ({"value": 400.0, "source": "factset"}, {"value": 401.0, "source": "factset"}),
        (None, {"value": 419.64, "source": "factset"}),                                   # first run
        ({"value": None, "source": "override"}, {"value": 419.64, "source": "factset"}),
        ({"value": 400.0, "source": "factset"}, None),
        ({"value": 400.0, "source": "factset"}, {"value": None, "source": "unavailable"}),
    ],
)
def test_eps_change_flag_not_written_and_stale_flag_removed(prev, new):
    FLAG().parent.mkdir(parents=True, exist_ok=True)
    FLAG().write_text("stale")
    refresh_v2._write_eps_change_flag(prev, new)
    assert not FLAG().exists()


# ---------------------------------------------------------------------------
# run() end to end in factset mode
# ---------------------------------------------------------------------------
NOW = datetime(2026, 11, 23, 21, 20, tzinfo=ZoneInfo("UTC"))   # 13:20 PT


@pytest.fixture
def run_env(tmp_path, monkeypatch, la_tz, freeze):
    cfg = {"series": [{"id": "sp500", "source": "yahoo", "series_id": "^GSPC", "label": "S&P",
                       "refresh_frequency": "intraday"}], "valuation_inputs": VI}
    p = tmp_path / "series.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(refresh_v2, "CONFIG_PATH", p)
    monkeypatch.setenv("PEC_EPS_MODE", "factset")
    freeze(NOW)
    s = Stubs(fs_result=fs())
    fetchers = s.fetchers()
    fetchers["yahoo"] = SimpleNamespace(fetch=lambda **kw: pd.DataFrame(
        {"date": pd.to_datetime(["2026-11-20", "2026-11-23"]), "value": [7175.83, 7200.0]}))
    monkeypatch.setattr(refresh_v2, "_load_fetchers", lambda: fetchers)
    return s


def _prior(eps):
    refresh_v2.OUTPUT_PATH.write_text(json.dumps({
        "metadata": {"statuses": {}}, "series": {}, "valuation_inputs": {"forward_eps_ntm": eps}}))


def _out():
    return json.loads(refresh_v2.OUTPUT_PATH.read_text(encoding="utf-8"))


def test_run_switch_override_to_factset_writes_flag(run_env):
    _prior({"value": OV_VALUE, "source": "override", "effective_date": OV_DATE, "as_of": OV_DATE})
    refresh_v2.run()
    eps = _out()["valuation_inputs"]["forward_eps_ntm"]
    assert eps["source"] == "factset" and eps["value"] == 419.64 and eps["as_of"] == "2026-11-20"
    assert _out()["valuation_inputs"]["forward_pe_ntm"] == round(7200.0 / 419.64, 2)
    assert FLAG().read_text(encoding="utf-8").startswith("ℹ️ PEC forward EPS updated: 400.00 → 419.64 (FactSet")
    assert run_env.recorded == [(date(2026, 11, 20), 419.64)]
    assert run_env.fs_calls == [{"last_good": OV_VALUE, "override": OV_VALUE}]


def test_run_unchanged_factset_removes_flag(run_env):
    _prior({"value": 419.64, "source": "factset", "effective_date": "2026-11-20", "as_of": "2026-11-20", "url": URL})
    FLAG().write_text("stale from last run")
    refresh_v2.run()
    assert _out()["valuation_inputs"]["forward_eps_ntm"]["source"] == "factset"
    assert not FLAG().exists()
    assert run_env.fs_calls == [{"last_good": 419.64, "override": OV_VALUE}]


def test_run_factset_failure_carries_previous(run_env):
    run_env.fs_exc = FetchError("FactSet download returned HTTP 503")
    _prior({"value": 419.64, "source": "factset", "effective_date": "2026-11-20", "as_of": "2026-11-20", "url": URL})
    refresh_v2.run()
    eps = _out()["valuation_inputs"]["forward_eps_ntm"]
    assert (eps["source"], eps["value"], eps["as_of"], eps["url"]) == ("factset", 419.64, "2026-11-20", URL)
    assert not FLAG().exists()
