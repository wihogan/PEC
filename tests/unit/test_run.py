"""run() end-to-end with stub fetchers and a synthetic config (plan §6.1, N9).

Clock: Fri 2026-11-20 22:17 UTC (= 17:17 ET, 14:17 PT; market closed) under
TZ=America/Los_Angeles. Prior data_v2.json is written to the tmp OUTPUT_PATH.
"""
import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import yaml

import refresh_v2
from fetchers.base import FetchError

NOW = datetime(2026, 11, 20, 22, 17, tzinfo=ZoneInfo("UTC"))
NOW_TS = "2026-11-20T14:17:00"

CONFIG = {
    "series": [
        {"id": "sp500", "source": "yahoo", "series_id": "^GSPC", "label": "S&P", "refresh_frequency": "intraday"},
        {"id": "treasury_10yr", "source": "fred", "series_id": "DGS10", "label": "10y", "refresh_frequency": "daily_release"},
        {"id": "cpi_yoy", "source": "fred", "series_id": "CPIAUCSL", "label": "CPI", "refresh_frequency": "monthly", "yoy_pct": True},
        {"id": "gdp_growth", "source": "fred", "series_id": "GDPX", "label": "GDP", "refresh_frequency": "quarterly"},
        {"id": "dow_jones", "source": "yahoo", "series_id": "^BOGUS", "label": "Dow", "refresh_frequency": "intraday"},
        {"id": "brand_new", "source": "yahoo", "series_id": "^FAIL", "label": "New", "refresh_frequency": "intraday"},
        {"id": "nber_recession", "source": "fred", "series_id": "USREC", "label": "NBER", "refresh_frequency": "monthly"},
        {"id": "yield_curve", "source": "treasury", "series_id": "daily_treasury_yield_curve", "label": "YC", "refresh_frequency": "daily_release", "publish_lag_bd": 0},
    ],
    "valuation_inputs": {
        "forward_eps_ntm": {
            "fetcher": "spglobal",
            "series_id": "forward_eps_ntm",
            "label": "EPS",
            "manual_override": {"value": 354.84, "effective_date": "2026-05-07"},
        }
    },
}

PRIOR = {
    "metadata": {
        "last_refreshed": "2026-11-19T14:17:00",
        "statuses": {
            "sp500": {"status": "ok", "last_fetched": "2026-11-19T14:17:00"},
            "treasury_10yr": {"status": "ok", "last_fetched": "2026-11-19T14:17:00"},
            "cpi_yoy": {"status": "ok", "last_fetched": "2026-11-17T14:17:00"},        # Oct obs present, 3 d -> skip
            "gdp_growth": {"status": "ok", "last_fetched": "2026-11-17T14:17:00"},     # Q3 obs present, 3 d -> skip
            "dow_jones": {"status": "ok", "last_fetched": "2026-11-19T14:17:00"},
            "nber_recession": {"status": "ok", "last_fetched": "2026-11-02T14:17:00"},  # no data -> due (throttle: not today)
            "yield_curve": {"status": "ok", "last_fetched": "2026-11-19T14:17:00"},
        },
    },
    "series": {
        "sp500": {"dates": ["2026-11-19"], "values": [6800.0]},
        "treasury_10yr": {"dates": ["2026-11-18"], "values": [4.1]},   # lag 1 expects Thu 19 -> due
        "cpi_yoy": {"dates": ["2026-10-01"], "values": [2.9]},
        "gdp_growth": {"dates": ["2026-07-01"], "values": [2.1]},
        "dow_jones": {"dates": ["2026-11-19"], "values": [44000.0]},
        "yield_curve": {"dates": ["2026-11-19"], "tenors": {"10yr": [4.1]}},
    },
    "valuation_inputs": {},
}


def _df(dates, values):
    return pd.DataFrame({"date": pd.to_datetime(dates), "value": values})


class Recorder:
    def __init__(self):
        self.calls = []


@pytest.fixture
def stubs():
    rec = Recorder()

    def yahoo_fetch(**kw):
        rec.calls.append(("yahoo", kw))
        if kw["series_id"] in ("^BOGUS", "^FAIL"):
            raise FetchError(f"Yahoo Finance returned no data for ticker '{kw['series_id']}'")
        return _df(["2026-11-19", "2026-11-20"], [6800.0, 6900.123456])

    def fred_fetch(**kw):
        rec.calls.append(("fred", kw))
        sid = kw["series_id"]
        if sid == "DGS10":
            return _df(["2026-11-19", "2026-11-20"], [4.1, 4.0])
        if sid == "CPIAUCSL":
            return _df(["2026-10-01"], [3.0])
        return _df(["2026-10-01"], [0.0])

    def treasury_fetch(**kw):
        rec.calls.append(("treasury", kw))
        return pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-11-20", "2026-11-20"]),
                "tenor": pd.Categorical(["1mo", "10yr"], categories=["1mo", "10yr"], ordered=True),
                "value": [3.9, 4.0],
            }
        )

    def spglobal_fetch(series_id, **kw):
        rec.calls.append(("spglobal", dict(kw, series_id=series_id)))
        if kw.get("history"):
            months = pd.date_range("2024-01-31", periods=30, freq="ME")
            return pd.DataFrame({"date": months, "value": [300.0] * 30})
        raise FetchError("S&P EPS fetch failed entirely.")

    rec.fetchers = {
        "yahoo": SimpleNamespace(fetch=yahoo_fetch),
        "fred": SimpleNamespace(fetch=fred_fetch),
        "treasury": SimpleNamespace(fetch=treasury_fetch),
        "spglobal": SimpleNamespace(fetch=spglobal_fetch),
    }
    return rec


@pytest.fixture
def env(tmp_path, monkeypatch, la_tz, freeze, stubs):
    cfg = tmp_path / "series.yaml"
    cfg.write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
    monkeypatch.setattr(refresh_v2, "CONFIG_PATH", cfg)
    monkeypatch.setattr(refresh_v2, "_load_fetchers", lambda: stubs.fetchers)
    refresh_v2.OUTPUT_PATH.write_text(json.dumps(PRIOR), encoding="utf-8")
    freeze(NOW)
    return stubs


def _out():
    return json.loads(refresh_v2.OUTPUT_PATH.read_text(encoding="utf-8"))


def test_run_statuses_counts_and_carry_forward(env):
    refresh_v2.run()
    out = _out()
    md, series, st = out["metadata"], out["series"], out["metadata"]["statuses"]

    assert md["last_refreshed"] == NOW_TS
    assert md["series_count"] == 8
    assert md["skip_count"] == 2                 # cpi_yoy, gdp_growth
    assert md["failure_count"] == 2              # dow_jones, brand_new
    assert md["success_count"] == 4              # 8 - 2 skipped - 2 failed

    # fetched OK
    for sid in ("sp500", "treasury_10yr", "nber_recession", "yield_curve"):
        assert st[sid] == {"status": "ok", "last_fetched": NOW_TS, "last_attempt": NOW_TS}, sid
    assert series["sp500"] == {"dates": ["2026-11-19", "2026-11-20"], "values": [6800.0, 6900.1235]}
    assert series["yield_curve"] == {"dates": ["2026-11-20"], "tenors": {"1mo": [3.9], "10yr": [4.0]}}

    # skipped: data and status carried verbatim
    assert series["cpi_yoy"] == PRIOR["series"]["cpi_yoy"]
    assert st["cpi_yoy"] == PRIOR["metadata"]["statuses"]["cpi_yoy"]
    assert series["gdp_growth"] == PRIOR["series"]["gdp_growth"]

    # failure with prior data: carried forward, status error, prior last_fetched kept
    assert series["dow_jones"] == PRIOR["series"]["dow_jones"]
    assert st["dow_jones"]["status"] == "error"
    assert "^BOGUS" in st["dow_jones"]["error"]
    assert st["dow_jones"]["last_fetched"] == "2026-11-19T14:17:00"
    assert st["dow_jones"]["last_attempt"] == NOW_TS            # QA M1: every attempt stamped

    # failure without prior data: absent from series, last_fetched None
    assert "brand_new" not in series
    assert st["brand_new"]["status"] == "error" and st["brand_new"]["last_fetched"] is None
    assert st["brand_new"]["last_attempt"] == NOW_TS


def test_run_does_not_call_fetchers_for_skipped_series(env):
    refresh_v2.run()
    fetched = [kw["series_id"] for src, kw in env.calls if src in ("yahoo", "fred")]
    assert "CPIAUCSL" not in fetched and "GDPX" not in fetched
    # monthly with no stored data and not fetched today -> fetched
    assert "USREC" in fetched


def test_run_valuation_falls_back_to_override(env):
    refresh_v2.run()
    out = _out()
    eps = out["valuation_inputs"]["forward_eps_ntm"]
    assert eps["source"] == "override"
    assert eps["value"] == 354.84 and eps["effective_date"] == "2026-05-07"
    assert len(eps["history"]["dates"]) == 30
    assert out["metadata"]["statuses"]["forward_eps_ntm"] == {"status": "ok", "last_attempt": NOW_TS}
    sp_calls = [kw for src, kw in env.calls if src == "spglobal"]
    assert sp_calls == [{"series_id": "forward_eps_ntm"}, {"series_id": "forward_eps_ntm", "history": True}]

    vi = out["valuation_inputs"]
    assert vi["forward_pe_ntm"] == round(6900.1235 / 354.84, 2)
    assert vi["ddm_fair_value"] == 4968           # 10y last = 4.0
    assert vi["erp"] == 3.5
    # S&P stub only covers Nov 2026 -> one fwd P/E month, no delta string
    assert "fwdpe_delta_string" not in vi


def test_run_valuation_unavailable_without_override(env, tmp_path, monkeypatch):
    cfg = json.loads(json.dumps(CONFIG))
    cfg["valuation_inputs"]["forward_eps_ntm"]["manual_override"] = {"value": None, "effective_date": None}
    p = tmp_path / "series2.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(refresh_v2, "CONFIG_PATH", p)
    refresh_v2.run()
    out = _out()
    eps = out["valuation_inputs"]["forward_eps_ntm"]
    assert eps == {"value": None, "effective_date": None, "source": "unavailable", "history": None}
    assert out["metadata"]["statuses"]["forward_eps_ntm"]["status"] == "warning"
    assert "ddm_fair_value" not in out["valuation_inputs"]


def test_run_valuation_live_outranks_override(env):
    """F11: a live hit replaces the approved manual_override (why PEC_EPS_OVERRIDE_ONLY is planned)."""
    orig = env.fetchers["spglobal"].fetch

    def live(series_id, **kw):
        if kw.get("history"):
            return orig(series_id, **kw)
        return _df(["2026-11-20"], [999.0])

    env.fetchers["spglobal"] = SimpleNamespace(fetch=live)
    refresh_v2.run()
    eps = _out()["valuation_inputs"]["forward_eps_ntm"]
    assert eps["source"] == "live" and eps["value"] == 999.0 and eps["effective_date"] == "2026-11-20"


def test_run_force_fetches_everything(env):
    refresh_v2.run(force=True)
    md = _out()["metadata"]
    assert md["skip_count"] == 0
    assert md["success_count"] == 6 and md["failure_count"] == 2


def test_run_second_run_skips_what_first_run_fetched(env):
    refresh_v2.run()
    env.calls.clear()
    refresh_v2.run()
    md = _out()["metadata"]
    # everything fetched OK post-close is current; failures are retried
    assert md["skip_count"] == 6
    assert md["failure_count"] == 2
    retried = sorted(kw["series_id"] for src, kw in env.calls if src == "yahoo")
    assert retried == ["^BOGUS", "^FAIL"]


def test_run_yoy_pct_transform_requested(env):
    refresh_v2.run(force=True)
    cpi = [kw for src, kw in env.calls if src == "fred" and kw["series_id"] == "CPIAUCSL"]
    assert cpi == [{"series_id": "CPIAUCSL", "start_date": "2010-01-01", "transform": "pct_change_12m"}]


def test_run_writes_backup_of_prior_file(env):
    refresh_v2.run()
    backups = list(refresh_v2.BACKUP_DIR.glob("data_*.json"))
    assert [b.name for b in backups] == ["data_20261120_141700.json"]
    assert json.loads(backups[0].read_text(encoding="utf-8")) == PRIOR


def test_run_without_prior_data(env):
    refresh_v2.OUTPUT_PATH.unlink()
    refresh_v2.run()
    md = _out()["metadata"]
    assert md["skip_count"] == 0
    assert md["failure_count"] == 2
    assert not refresh_v2.BACKUP_DIR.exists()


def test_run_corrupt_prior_data_treated_as_empty(env):
    refresh_v2.OUTPUT_PATH.write_text("{truncated", encoding="utf-8")
    refresh_v2.run()
    assert _out()["metadata"]["skip_count"] == 0


def test_run_renders_into_tmp_here(env):
    from conftest import TEMPLATE

    (refresh_v2.HERE / "PECtemplate.html").write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    refresh_v2.run()
    html = (refresh_v2.HERE / "PEC.html").read_text(encoding="utf-8")
    assert "__DASHBOARD_DATA__" not in html
    assert '"last_refreshed":"2026-11-20T14:17:00"' in html


# ---------------------------------------------------------------------------
# PEC_EPS_OVERRIDE_ONLY=1 (plan §2, F11, B5)
# ---------------------------------------------------------------------------
def _with_monthly_sp500(env):
    """S&P stub with month-end closes Jan 2024..Nov 2026 so fwd_pe_history can join."""
    orig = env.fetchers["yahoo"].fetch

    def fetch(**kw):
        if kw["series_id"] == "^GSPC":
            env.calls.append(("yahoo", kw))
            months = pd.date_range("2024-01-31", "2026-11-30", freq="ME")
            vals = [6000.0] * (len(months) - 1) + [7096.8]
            return pd.DataFrame({"date": months, "value": vals})
        return orig(**kw)

    env.fetchers["yahoo"] = SimpleNamespace(fetch=fetch)


def test_override_only_never_calls_live_point_fetch(env, monkeypatch):
    monkeypatch.setenv("PEC_EPS_OVERRIDE_ONLY", "1")
    _with_monthly_sp500(env)
    orig = env.fetchers["spglobal"].fetch

    def live_would_win(series_id, **kw):
        if not kw.get("history"):
            env.calls.append(("spglobal", dict(kw, series_id=series_id)))
            return _df(["2026-11-20"], [999.0])   # would replace the override if called
        return orig(series_id, **kw)

    env.fetchers["spglobal"] = SimpleNamespace(fetch=live_would_win)
    refresh_v2.run()
    out = _out()

    sp_calls = [kw for src, kw in env.calls if src == "spglobal"]
    assert sp_calls == [{"series_id": "forward_eps_ntm", "history": True}]   # history only
    eps = out["valuation_inputs"]["forward_eps_ntm"]
    assert eps["source"] == "override"
    assert eps["value"] == 354.84 and eps["effective_date"] == "2026-05-07"
    assert len(eps["history"]["dates"]) == 30
    assert out["metadata"]["statuses"]["forward_eps_ntm"] == {"status": "ok", "last_attempt": NOW_TS}

    # fwd_pe_history still built from the EPS history (30 months, Jan 2024..Jun 2026)
    fph = out["series"]["fwd_pe_history"]
    assert len(fph["dates"]) == 30
    assert fph["dates"][0] == "2024-01-31" and fph["dates"][-1] == "2026-06-30"
    assert fph["values"] == [20.0] * 30                     # 6000 / 300
    vi = out["valuation_inputs"]
    assert vi["forward_pe_ntm"] == 20.0                     # 7096.8 / 354.84
    assert vi["fwdpe_delta_string"] == "+0% vs 5yr avg (20.0×)"
    assert vi["ddm_fair_value"] == 4968                     # 10y 4.00


def test_override_only_without_override_is_unavailable(env, monkeypatch, tmp_path):
    monkeypatch.setenv("PEC_EPS_OVERRIDE_ONLY", "1")
    cfg = json.loads(json.dumps(CONFIG))
    cfg["valuation_inputs"]["forward_eps_ntm"]["manual_override"] = {"value": None, "effective_date": None}
    p = tmp_path / "series3.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(refresh_v2, "CONFIG_PATH", p)
    refresh_v2.run()
    out = _out()
    assert out["valuation_inputs"]["forward_eps_ntm"]["source"] == "unavailable"
    assert out["metadata"]["statuses"]["forward_eps_ntm"] == {"status": "warning", "error": "no data source available", "last_attempt": NOW_TS}
    assert [kw for src, kw in env.calls if src == "spglobal"] == []


@pytest.mark.parametrize("flag", ["0", "", "true", "yes", " 1"])
def test_override_only_requires_exact_1(env, monkeypatch, flag):
    monkeypatch.setenv("PEC_EPS_OVERRIDE_ONLY", flag)
    orig = env.fetchers["spglobal"].fetch

    def live(series_id, **kw):
        if kw.get("history"):
            return orig(series_id, **kw)
        return _df(["2026-11-20"], [999.0])

    env.fetchers["spglobal"] = SimpleNamespace(fetch=live)
    refresh_v2.run()
    assert _out()["valuation_inputs"]["forward_eps_ntm"]["source"] == "live"


def test_fetch_valuation_inputs_direct_override_only(monkeypatch):
    monkeypatch.setenv("PEC_EPS_OVERRIDE_ONLY", "1")
    calls = []

    def fetch(series_id, **kw):
        calls.append(kw)
        if kw.get("history"):
            return pd.DataFrame({"date": pd.to_datetime(["2026-04-30"]), "value": [346.19]})
        raise AssertionError("live point fetch must not be called")

    statuses = {}
    out = refresh_v2._fetch_valuation_inputs(
        CONFIG["valuation_inputs"], {"spglobal": SimpleNamespace(fetch=fetch)}, statuses
    )
    assert out == {
        "forward_eps_ntm": {
            "value": 354.84,
            "effective_date": "2026-05-07",
            "source": "override",
            "history": {"dates": ["2026-04-30"], "values": [346.19]},
            "as_of": "2026-05-07",   # EPS-mode records carry as_of/url
            "url": None,
        }
    }
    assert calls == [{"history": True}]
    assert statuses == {"forward_eps_ntm": {"status": "ok"}}


def test_run_passes_last_obs_and_publish_lag(env, monkeypatch):
    seen = {}
    real = refresh_v2._needs_refresh

    def spy(sid, frequency, existing_statuses, last_obs=None, publish_lag_bd=0, now=None, session_series=True):
        seen[sid] = (frequency, last_obs, publish_lag_bd)
        sessions[sid] = session_series
        return real(sid, frequency, existing_statuses, last_obs=last_obs, publish_lag_bd=publish_lag_bd,
                    now=now, session_series=session_series)

    sessions = {}

    monkeypatch.setattr(refresh_v2, "_needs_refresh", spy)
    refresh_v2.run()
    from datetime import date
    assert seen["treasury_10yr"] == ("daily_release", date(2026, 11, 18), 1)   # FRED default lag 1
    assert seen["yield_curve"] == ("daily_release", date(2026, 11, 19), 0)     # explicit 0
    assert seen["sp500"] == ("intraday", date(2026, 11, 19), 0)
    assert seen["cpi_yoy"] == ("monthly", date(2026, 10, 1), 0)
    assert seen["nber_recession"] == ("monthly", None, 0)


def test_run_last_fetched_is_pt_regardless_of_process_tz(env, monkeypatch):
    from conftest import set_tz
    with set_tz("UTC"):
        refresh_v2.run()
    out = _out()
    assert out["metadata"]["last_refreshed"] == NOW_TS          # 14:17 PT, not 22:17 UTC
    assert out["metadata"]["statuses"]["sp500"]["last_fetched"] == NOW_TS


def test_run_session_series_false_only_for_crypto(env, monkeypatch):
    seen = {}
    real = refresh_v2._needs_refresh

    def spy(sid, frequency, existing_statuses, **kw):
        seen[sid] = kw.get("session_series")
        return real(sid, frequency, existing_statuses, **kw)

    monkeypatch.setattr(refresh_v2, "_needs_refresh", spy)
    cfg = json.loads(json.dumps(CONFIG))
    cfg["series"].append({"id": "btc_usd", "source": "yahoo", "series_id": "BTC-USD", "label": "BTC",
                          "refresh_frequency": "intraday"})
    p = refresh_v2.CONFIG_PATH.parent / "series_btc.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(refresh_v2, "CONFIG_PATH", p)
    refresh_v2.run()
    assert seen["btc_usd"] is False
    assert seen["sp500"] is True and seen["dow_jones"] is True and seen["cpi_yoy"] is True


def test_run_skip_carries_status_including_old_last_attempt(env):
    prior = json.loads(json.dumps(PRIOR))
    prior["metadata"]["statuses"]["cpi_yoy"]["last_attempt"] = "2026-11-18T06:45:00"
    refresh_v2.OUTPUT_PATH.write_text(json.dumps(prior), encoding="utf-8")
    refresh_v2.run()
    assert _out()["metadata"]["statuses"]["cpi_yoy"] == prior["metadata"]["statuses"]["cpi_yoy"]


def test_run_weekly_revision_refetches_slow_series(env):
    prior = json.loads(json.dumps(PRIOR))
    prior["metadata"]["statuses"]["cpi_yoy"]["last_fetched"] = "2026-11-13T14:17:00"   # 7 PT days
    refresh_v2.OUTPUT_PATH.write_text(json.dumps(prior), encoding="utf-8")
    refresh_v2.run()
    fetched = [kw["series_id"] for src, kw in env.calls if src == "fred"]
    assert "CPIAUCSL" in fetched and "GDPX" not in fetched
    assert _out()["metadata"]["statuses"]["cpi_yoy"]["last_attempt"] == NOW_TS


def test_run_unparseable_stamp_does_not_crash(env):
    prior = json.loads(json.dumps(PRIOR))
    prior["metadata"]["statuses"]["sp500"]["last_fetched"] = "not-a-date"
    prior["metadata"]["statuses"]["gdp_growth"]["last_fetched"] = "2026-99-99"
    refresh_v2.OUTPUT_PATH.write_text(json.dumps(prior), encoding="utf-8")
    refresh_v2.run()
    fetched = [kw["series_id"] for src, kw in env.calls if src in ("yahoo", "fred")]
    assert "^GSPC" in fetched and "GDPX" in fetched          # treated as never fetched (m12)
