"""_compute_derived_valuation (plan §6.1, F3)."""
import json

import pytest

from conftest import FIXTURES
from refresh_v2 import _compute_derived_valuation


def _vi(eps=354.84, history=None):
    return {"forward_eps_ntm": {"value": eps, "effective_date": "2026-05-07", "source": "override", "history": history}}


def _flat(values, dates=None):
    dates = dates or [f"2026-01-{i + 1:02d}" for i in range(len(values))]
    return {"dates": dates, "values": values}


def test_forward_pe_uses_last_non_null_close():
    vi = _vi()
    series = {"sp500": _flat([6000.0, 6900.0, None])}
    _compute_derived_valuation(vi, series)
    assert vi["forward_pe_ntm"] == round(6900.0 / 354.84, 2) == 19.45
    assert vi["erp"] == 3.5


def test_ddm_fair_value_4968():
    vi = _vi(354.84)
    series = {"treasury_10yr": _flat([4.2, 4.00])}
    _compute_derived_valuation(vi, series)
    # (354.84 * 0.35) / ((4.00 + 3.5 - 5) / 100) = 4967.76 -> rounded to int
    assert vi["ddm_fair_value"] == 4968
    assert isinstance(vi["ddm_fair_value"], int)


@pytest.mark.parametrize("ten_year", [1.5, 1.0, 0.0, -0.5])
def test_ddm_denominator_non_positive_is_none(ten_year):
    vi = _vi()
    _compute_derived_valuation(vi, {"treasury_10yr": _flat([ten_year])})
    assert "ddm_fair_value" in vi and vi["ddm_fair_value"] is None


def test_no_eps_means_no_pe_or_ddm():
    vi = {"forward_eps_ntm": {"value": None, "source": "unavailable", "history": None}}
    _compute_derived_valuation(vi, {"sp500": _flat([6900.0]), "treasury_10yr": _flat([4.0])})
    assert "forward_pe_ntm" not in vi
    assert "ddm_fair_value" not in vi
    assert vi["erp"] == 3.5


def test_empty_inputs_only_set_erp():
    vi = {}
    series = {}
    _compute_derived_valuation(vi, series)
    assert vi == {"erp": 3.5}
    assert series == {}


# ── CAPE delta ──────────────────────────────────────────────────────────────
def test_cape_delta_positive():
    vals = [30.0] * 59 + [36.0]  # avg = 30.1
    vi = _vi()
    _compute_derived_valuation(vi, {"shiller_cape": _flat(vals, [f"d{i}" for i in range(60)])})
    assert vi["cape_delta_string"] == "+20% vs 5yr avg (30.1)"


def test_cape_delta_negative_uses_last_60_non_null():
    vals = [99.0] * 10 + [30.0] * 59 + [None, 24.0, None]  # older 99s fall outside the window
    vi = _vi()
    _compute_derived_valuation(vi, {"shiller_cape": _flat(vals, [f"d{i}" for i in range(len(vals))])})
    assert vi["cape_delta_string"] == "-20% vs 5yr avg (29.9)"


def test_cape_delta_needs_60_values():
    vi = _vi()
    _compute_derived_valuation(vi, {"shiller_cape": _flat([30.0] * 59, [f"d{i}" for i in range(59)])})
    assert "cape_delta_string" not in vi


# ── Fwd P/E history + delta ────────────────────────────────────────────────
def _months(n, start_year=2024):
    out = []
    y, m = start_year, 1
    for _ in range(n):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _sp_daily(months, mid=1000.0, end=2000.0):
    """Two closes per month; the later one (month-end) must win the join."""
    dates, values = [], []
    for y, m in months:
        dates += [f"{y}-{m:02d}-10", f"{y}-{m:02d}-27"]
        values += [mid, end]
    return {"dates": dates, "values": values}


def test_fwd_pe_history_month_join_uses_last_close_of_month():
    months = _months(3)
    hist = {"dates": [f"{y}-{m:02d}-28" for y, m in months], "values": [100.0, 200.0, 0.0]}
    series = {"sp500": _sp_daily(months)}
    vi = _vi(eps=400.0, history=hist)
    _compute_derived_valuation(vi, series)
    fph = series["fwd_pe_history"]
    # month join on YYYY-MM (EPS dated the 28th, S&P last close the 27th);
    # zero EPS is skipped
    assert fph == {"dates": ["2024-01-28", "2024-02-28"], "values": [20.0, 10.0]}
    assert "fwdpe_delta_string" not in vi  # < 24 months


def test_fwd_pe_history_skips_months_without_sp_close():
    months = _months(4)
    hist = {"dates": [f"{y}-{m:02d}-30" for y, m in months], "values": [100.0] * 4}
    sp = _sp_daily(months[:2])
    sp["values"][-1] = None  # Feb month-end close missing -> mid-month close used
    series = {"sp500": sp}
    vi = _vi(history=hist)
    _compute_derived_valuation(vi, series)
    assert series["fwd_pe_history"]["dates"] == ["2024-01-30", "2024-02-30"]
    assert series["fwd_pe_history"]["values"] == [20.0, 10.0]


def test_fwdpe_delta_string_with_24_months():
    months = _months(24)
    hist = {"dates": [f"{y}-{m:02d}-28" for y, m in months], "values": [100.0] * 24}
    series = {"sp500": _sp_daily(months, end=2000.0)}  # every month fwd P/E = 20.0
    vi = _vi(eps=80.0, history=hist)                   # current = 2000/80 = 25.0
    _compute_derived_valuation(vi, series)
    assert vi["forward_pe_ntm"] == 25.0
    assert len(series["fwd_pe_history"]["values"]) == 24
    assert vi["fwdpe_delta_string"] == "+25% vs 5yr avg (20.0×)"


def test_fwdpe_delta_negative():
    months = _months(30)
    hist = {"dates": [f"{y}-{m:02d}-28" for y, m in months], "values": [100.0] * 30}
    series = {"sp500": _sp_daily(months, end=2000.0)}
    vi = _vi(eps=125.0, history=hist)  # current 16.0 vs avg 20.0
    _compute_derived_valuation(vi, series)
    assert vi["fwdpe_delta_string"] == "-20% vs 5yr avg (20.0×)"


def test_fwdpe_23_months_no_delta():
    months = _months(23)
    hist = {"dates": [f"{y}-{m:02d}-28" for y, m in months], "values": [100.0] * 23}
    series = {"sp500": _sp_daily(months)}
    vi = _vi(history=hist)
    _compute_derived_valuation(vi, series)
    assert len(series["fwd_pe_history"]["values"]) == 23
    assert "fwdpe_delta_string" not in vi


def test_no_history_no_fwd_pe_history():
    series = {"sp500": _flat([6900.0])}
    vi = _vi(history=None)
    _compute_derived_valuation(vi, series)
    assert "fwd_pe_history" not in series
    assert "fwdpe_delta_string" not in vi


# ── Parity with the zip baseline ───────────────────────────────────────────
def test_recomputes_baseline_derived_values():
    """Re-deriving from baseline inputs reproduces the derived values stored in the baseline."""
    base = json.loads((FIXTURES / "baseline_data_v2.json").read_text(encoding="utf-8"))
    expected_vi = base["valuation_inputs"]
    series = {k: v for k, v in base["series"].items() if k != "fwd_pe_history"}
    vi = {"forward_eps_ntm": expected_vi["forward_eps_ntm"]}
    _compute_derived_valuation(vi, series)
    for key in ("forward_pe_ntm", "erp", "ddm_fair_value", "cape_delta_string", "fwdpe_delta_string"):
        assert vi[key] == expected_vi[key], key
    assert series["fwd_pe_history"] == base["series"]["fwd_pe_history"]
