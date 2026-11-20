"""Fetcher parsers on offline fixtures (plan §6.1): multpl, Treasury, Yahoo, FRED."""
import os
import shutil
import time
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import requests

import refresh_v2
from conftest import FIXTURES
from fetchers import fred, multpl, treasury, yahoo
from fetchers.base import FetchError

MULTPL_DIR = FIXTURES / "multpl"
ALL_TIME = (date(1800, 1, 1), date(2100, 1, 1))

# series_id -> (plausible value range over 1871..2026, newest row date, newest value)
MULTPL_EXPECT = {
    "sp500_div_yield":      ((0.005, 0.15), date(2026, 10, 31), 0.0105),
    "sp500_pe":             ((4.0, 130.0), date(2026, 10, 30), 25.86),
    "sp500_earnings_yield": ((0.005, 0.20), None, None),
    "sp500_earnings":       ((0.1, 400.0), None, None),
    "shiller_pe":           ((4.0, 45.0), None, None),
}


def _html(series_id):
    return (MULTPL_DIR / f"multpl_{series_id}.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# multpl
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("series_id", sorted(multpl._SERIES))
def test_multpl_parse_fixture(series_id):
    _, is_percent = multpl._SERIES[series_id]
    df = multpl._parse_html(_html(series_id), is_percent, *ALL_TIME)
    (lo, hi), newest_date, newest_value = MULTPL_EXPECT[series_id]

    assert list(df.columns) == ["date", "value"]
    assert len(df) > 1800                         # monthly since 1871
    assert df["date"].dtype == "datetime64[ns]"
    assert df["date"].is_monotonic_increasing
    assert df["value"].notna().all()
    assert df["date"].min() < pd.Timestamp("1872-01-01")
    # sp500_earnings (TTM EPS) lags ~1 quarter: fixture ends 2026-06-30
    min_latest = "2026-06-01" if series_id == "sp500_earnings" else "2026-09-01"
    assert df["date"].max() >= pd.Timestamp(min_latest)
    assert df["value"].between(lo, hi).all(), (df["value"].min(), df["value"].max())
    if newest_date is not None:
        last = df.iloc[-1]
        assert last["date"].date() == newest_date
        assert last["value"] == pytest.approx(newest_value)


@pytest.mark.parametrize("series_id", ["sp500_div_yield", "sp500_pe", "sp500_earnings_yield"])
def test_multpl_dagger_rows_are_kept(series_id):
    """Fixture rows marked '†' (estimate) parse to numbers, not dropped."""
    html = _html(series_id)
    assert "†" in html
    _, is_percent = multpl._SERIES[series_id]
    df = multpl._parse_html(html, is_percent, date(2026, 9, 1), date(2026, 12, 31))
    assert len(df) >= 2 and df["value"].notna().all()


def test_multpl_percent_series_scaled_to_decimal():
    pct = multpl._parse_html(_html("sp500_earnings_yield"), True, *ALL_TIME)
    pe = multpl._parse_html(_html("sp500_pe"), False, *ALL_TIME)
    # earnings yield (decimal) ~= 1 / PE for the same month
    j = pct.merge(pe, on="date", suffixes=("_ey", "_pe")).tail(120)
    assert ((j["value_ey"] * j["value_pe"]) - 1).abs().median() < 0.05


def test_multpl_div_yield_has_duplicate_october_rows():
    """Fixture quirk: multpl lists both 'Oct 30, 2026' and 'Oct 31, 2026'; both are kept."""
    df = multpl._parse_html(_html("sp500_div_yield"), True, date(2026, 10, 1), date(2026, 10, 31))
    assert [d.day for d in df["date"].dt.date] == [30, 31]


@pytest.mark.parametrize(
    "raw, is_percent, expected",
    [
        ("† 1.07%", True, 0.0107),
        ("1.07%", True, 0.0107),
        ("†\n25.86", False, 25.86),
        ("$1,234.56", False, 1234.56),
        ("  42 ", False, 42.0),
        ("n/a", False, None),
        ("", True, None),
    ],
)
def test_multpl_parse_value(raw, is_percent, expected):
    out = multpl._parse_value(raw, is_percent)
    assert out == (pytest.approx(expected) if expected is not None else None)


def test_multpl_parse_date_filter():
    df = multpl._parse_html(_html("sp500_pe"), False, date(2010, 1, 1), date(2010, 12, 31))
    assert len(df) == 12
    assert df["date"].min() >= pd.Timestamp("2010-01-01")


def test_multpl_missing_table_raises():
    with pytest.raises(FetchError, match="datatable"):
        multpl._parse_html("<html><body>nope</body></html>", False, *ALL_TIME)


def test_multpl_empty_range_raises():
    with pytest.raises(FetchError, match="No rows"):
        multpl._parse_html(_html("sp500_pe"), False, date(2100, 1, 1), date(2100, 12, 31))


def _seed_cache(series_id, age_hours=0.0):
    dst = multpl._CACHE_DIR / f"multpl_{series_id}.html"
    shutil.copy(MULTPL_DIR / f"multpl_{series_id}.html", dst)
    t = time.time() - age_hours * 3600
    os.utime(dst, (t, t))
    return dst


def test_multpl_fetch_uses_fresh_cache_offline(monkeypatch):
    _seed_cache("shiller_pe")
    monkeypatch.setattr(multpl.requests, "get", lambda *a, **k: pytest.fail("network used"))
    df = multpl.fetch("shiller_pe", start_date="2010-01-01", end_date="2026-12-31")
    assert df["date"].min() >= pd.Timestamp("2010-01-01")
    assert df["value"].between(10, 45).all()


def test_multpl_fetch_falls_back_to_stale_cache(monkeypatch):
    _seed_cache("sp500_earnings", age_hours=48)

    def boom(*a, **k):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(multpl.requests, "get", boom)
    df = multpl.fetch("sp500_earnings", start_date="2020-01-01", end_date="2026-12-31")
    assert not df.empty


def test_multpl_fetch_no_cache_no_network_raises(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(multpl.requests, "get", boom)
    with pytest.raises(FetchError):
        multpl.fetch("sp500_pe")


def test_multpl_unsupported_id():
    with pytest.raises(FetchError, match="Unsupported"):
        multpl.fetch("nope")


# ---------------------------------------------------------------------------
# Treasury
# ---------------------------------------------------------------------------
class _FakeSession:
    def __init__(self, xml_by_year):
        self.xml_by_year = xml_by_year
        self.years = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, timeout=None):
        year = int(url.rsplit("=", 1)[1])
        self.years.append(year)
        return SimpleNamespace(text=self.xml_by_year.get(year, "<feed></feed>"), raise_for_status=lambda: None)


@pytest.fixture
def treasury_xml():
    return (FIXTURES / "treasury_yield_curve_sample.xml").read_text(encoding="utf-8")


@pytest.fixture
def fake_session(monkeypatch, treasury_xml):
    sess = _FakeSession({2026: treasury_xml})
    monkeypatch.setattr(treasury.requests, "Session", lambda: sess)
    return sess


def test_treasury_fetch_year_wide(treasury_xml):
    df = treasury._fetch_year(2026, _FakeSession({2026: treasury_xml}))
    assert len(df) == 3
    assert df["date"].dtype == "datetime64[ns]"
    row = df[df["date"] == "2026-01-05"].iloc[0]
    assert row["BC_10YEAR"] == 4.10
    assert row["BC_4MONTH"] is None or pd.isna(row["BC_4MONTH"])  # empty element -> None
    assert pd.isna(row["BC_2MONTH"])                              # absent element -> NaN


def test_treasury_fetch_long_form_and_tenor_order(fake_session):
    df = treasury.fetch("daily_treasury_yield_curve", start_date="2026-01-01", end_date="2026-01-31")
    assert fake_session.years == [2026]
    assert list(df.columns) == ["date", "tenor", "value"]
    assert list(df["tenor"].cat.categories) == treasury._TENOR_ORDER
    assert df["value"].notna().all()
    # sorted by date, then canonical tenor order
    jan2 = df[df["date"] == "2026-01-02"]["tenor"].astype(str).tolist()
    assert jan2 == treasury._TENOR_ORDER                      # unmapped BC_1_5MONTH / DISPLAY dropped
    jan5 = df[df["date"] == "2026-01-05"]["tenor"].astype(str).tolist()
    assert jan5 == [t for t in treasury._TENOR_ORDER if t not in ("2mo", "4mo")]
    assert df["date"].is_monotonic_increasing
    assert df["date"].max() == pd.Timestamp("2026-01-05")     # Feb row filtered by end_date


def test_treasury_pivot_in_orchestrator(fake_session):
    long = treasury.fetch("daily_treasury_yield_curve", start_date="2026-01-01", end_date="2026-12-31")
    stub = SimpleNamespace(fetch=lambda **kw: long)
    out = refresh_v2._fetch_yield_curve({"series_id": "daily_treasury_yield_curve"}, {"treasury": stub})
    assert out["dates"] == ["2026-01-02", "2026-01-05", "2026-02-02"]
    # all tenors present on the first date -> canonical key order
    assert list(out["tenors"]) == treasury._TENOR_ORDER
    for t, vals in out["tenors"].items():
        assert len(vals) == len(out["dates"]), t
    assert out["tenors"]["10yr"] == [4.0, 4.1, 4.2]
    assert out["tenors"]["2mo"] == [3.66, None, None]
    assert out["tenors"]["4mo"] == [3.58, None, None]
    assert out["tenors"]["30yr"] == [4.79, 4.8, None]


def test_treasury_rejects_other_series_ids():
    with pytest.raises(FetchError):
        treasury.fetch("DGS10")


def test_treasury_empty_feed_raises(monkeypatch):
    sess = _FakeSession({})
    monkeypatch.setattr(treasury.requests, "Session", lambda: sess)
    with pytest.raises(FetchError, match="no data"):
        treasury.fetch("daily_treasury_yield_curve", start_date="2025-01-01", end_date="2026-01-31")
    assert sess.years == [2025, 2026]


# ---------------------------------------------------------------------------
# Yahoo
# ---------------------------------------------------------------------------
IDX = pd.DatetimeIndex(["2026-11-18", "2026-11-19", "2026-11-20"], name="Date")


def test_yahoo_extract_close_flat():
    raw = pd.DataFrame({"Open": [1, 2, 3], "Close": [10.0, 11.0, 12.0], "Volume": [5, 6, 7]}, index=IDX)
    close = yahoo._extract_close(raw, "^GSPC")
    assert close.tolist() == [10.0, 11.0, 12.0]


def test_yahoo_extract_close_multiindex():
    cols = pd.MultiIndex.from_tuples([("Close", "^GSPC"), ("High", "^GSPC"), ("Open", "^GSPC")], names=["Price", "Ticker"])
    raw = pd.DataFrame([[10.0, 11, 9], [11.0, 12, 10], [12.0, 13, 11]], index=IDX, columns=cols)
    close = yahoo._extract_close(raw, "^GSPC")
    assert isinstance(close, pd.Series)
    assert close.tolist() == [10.0, 11.0, 12.0]


def test_yahoo_extract_close_missing_flat():
    with pytest.raises(FetchError, match="Close"):
        yahoo._extract_close(pd.DataFrame({"Open": [1.0]}, index=IDX[:1]), "X")


def test_yahoo_extract_close_missing_multiindex():
    cols = pd.MultiIndex.from_tuples([("Open", "X")])
    with pytest.raises(FetchError, match="MultiIndex"):
        yahoo._extract_close(pd.DataFrame([[1.0]], columns=cols), "X")


def test_yahoo_fetch_cleans_download(monkeypatch):
    idx = pd.DatetimeIndex(["2026-11-20 00:00", "2026-11-18 00:00", "2026-11-19 00:00"], tz="America/New_York", name="Date")
    raw = pd.DataFrame({"Close": [12.0, 10.0, np.nan], "Open": [1, 2, 3]}, index=idx)
    calls = {}

    def fake_download(ticker, **kw):
        calls.update(kw, ticker=ticker)
        return raw

    monkeypatch.setattr(yahoo.yf, "download", fake_download)
    df = yahoo.fetch("^GSPC", start_date="2026-11-01")
    assert calls["ticker"] == "^GSPC" and calls["auto_adjust"] is True and calls["start"] == "2026-11-01"
    assert list(df.columns) == ["date", "value"]
    assert df["date"].dt.tz is None
    assert df["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-11-18", "2026-11-20"]
    assert df["value"].tolist() == [10.0, 12.0]


def test_yahoo_fetch_empty_raises(monkeypatch):
    monkeypatch.setattr(yahoo.yf, "download", lambda *a, **k: pd.DataFrame())
    with pytest.raises(FetchError, match="no data"):
        yahoo.fetch("^BOGUS")


# ---------------------------------------------------------------------------
# FRED
# ---------------------------------------------------------------------------
def _monthly(n=30, start="2024-01-01", base=300.0, step=1.5):
    idx = pd.date_range(start, periods=n, freq="MS")
    return pd.Series([base + step * i for i in range(n)], index=idx)


@pytest.fixture
def fake_fred(monkeypatch):
    state = {"series": _monthly(), "calls": [], "errors": []}

    class FakeFred:
        def __init__(self, api_key):
            state["api_key"] = api_key

        def get_series(self, series_id, observation_start=None, observation_end=None):
            state["calls"].append((series_id, observation_start, observation_end))
            if state["errors"]:
                raise state["errors"].pop(0)
            return state["series"]

    monkeypatch.setattr(fred, "Fred", FakeFred)
    monkeypatch.setattr(fred, "_throttle", lambda: None)
    monkeypatch.setattr(fred.time, "sleep", lambda s: None)
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    return state


def test_fred_plain_fetch(fake_fred):
    df = fred.fetch("DGS10", start_date="2024-01-01")
    assert fake_fred["calls"] == [("DGS10", "2024-01-01", None)]
    assert len(df) == 30 and list(df.columns) == ["date", "value"]


def test_fred_pct_change_12m(fake_fred):
    raw = fake_fred["series"]
    df = fred.fetch("CPIAUCSL", start_date="2024-01-01", transform="pct_change_12m")
    assert len(df) == len(raw) - 12
    assert df["date"].iloc[0] == pd.Timestamp("2025-01-01")
    expected = [(raw.iloc[i] / raw.iloc[i - 12] - 1) * 100 for i in range(12, len(raw))]
    assert df["value"].tolist() == pytest.approx(expected)
    assert df["value"].iloc[0] == pytest.approx((318.0 / 300.0 - 1) * 100)  # 6.0 %


def test_fred_nan_observations_dropped_before_transform(fake_fred):
    s = _monthly(14)
    s.iloc[13] = np.nan  # not-yet-released observation
    fake_fred["series"] = s
    df = fred.fetch("CPIAUCSL", transform="pct_change_12m")
    assert len(df) == 1 and df["date"].iloc[0] == pd.Timestamp("2025-01-01")


def test_orchestrator_passes_pct_change_for_yoy_pct(fake_fred):
    entry = {"id": "cpi_yoy", "source": "fred", "series_id": "CPIAUCSL", "start_date": "2024-01-01", "yoy_pct": True}
    out = refresh_v2._fetch_standard(entry, {"fred": fred})
    assert out["dates"][0] == "2025-01-01"
    assert out["values"][0] == 6.0
    assert all(isinstance(v, float) for v in out["values"])


def test_orchestrator_passes_field_and_ignores_yoy_for_non_fred():
    seen = {}

    def fake_fetch(**kw):
        seen.update(kw)
        return pd.DataFrame({"date": pd.to_datetime(["2026-11-20"]), "value": [0.0123456]})

    entry = {"id": "x", "source": "yahoo", "series_id": "^GSPC", "field": "dividendYield", "yoy_pct": True}
    out = refresh_v2._fetch_standard(entry, {"yahoo": SimpleNamespace(fetch=fake_fetch)})
    assert seen == {"series_id": "^GSPC", "start_date": "2010-01-01", "field": "dividendYield"}
    assert out == {"dates": ["2026-11-20"], "values": [0.0123]}


def test_fred_missing_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(FetchError, match="FRED_API_KEY"):
        fred.fetch("DGS10")


def test_fred_invalid_transform(fake_fred):
    with pytest.raises(FetchError, match="transform"):
        fred.fetch("DGS10", transform="pct_change_5y")


def test_fred_rate_limit_retry_then_success(fake_fred):
    fake_fred["errors"] = [ValueError("Too Many Requests.  Exceeded Rate Limit")]
    df = fred.fetch("DGS10")
    assert len(fake_fred["calls"]) == 2 and not df.empty


def test_fred_bad_series_wrapped(fake_fred):
    fake_fred["errors"] = [ValueError("Bad Request.  The series does not exist.")]
    with pytest.raises(FetchError, match="not found"):
        fred.fetch("BOGUS")
