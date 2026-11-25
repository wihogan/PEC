"""fetchers/shiller.py (Amendment C: multpl replaced by Shiller's ie_data.xls).

Offline. Workbooks are synthetic .xls fixtures (tests/fixtures/shiller/, built by
make_fixtures.py) reproducing the real layout: sheet "Data", header row 7, duplicate
column names, CAPE and TR CAPE, decimal dates (October = 2026.1), D/E blank for the
latest two months, a trailing footnote row. Network calls go to a fake requests.get;
yfinance.download is replaced per test. Expected values are computed from the parsed
fixture, not hard-coded, except the plan-quoted 2026.09-11 rows.
"""
import json
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import requests
import yaml
import yfinance

import refresh_v2
from conftest import FIXTURES, REPO
from fetchers import shiller
from fetchers.base import FetchError

SHILLER_DIR = FIXTURES / "shiller"
WB = (SHILLER_DIR / "ie_data_synthetic.xls").read_bytes()
WB_STALE = (SHILLER_DIR / "ie_data_stale_yale.xls").read_bytes()
ARCHIVE = json.loads((REPO / "config" / "multpl_archive.json").read_text(encoding="utf-8"))
PT = ZoneInfo("America/Los_Angeles")

WB_URL = "https://img1.wsimg.com/blobby/go/abc/downloads/uuid-1/ie_data.xls?ver=1732&x=2"
LANDING = (
    '<html><body><a href="//img1.wsimg.com/blobby/go/abc/downloads/uuid-0/Fig3-1%20(1).xls?ver=9">Fig 3-1</a>'
    ' <a class="btn" href="//img1.wsimg.com/blobby/go/abc/downloads/uuid-1/ie_data.xls?ver=1732&amp;x=2">'
    'U.S. Stock Markets 1871-Present and CAPE Ratio</a></body></html>'
)
TODAY = date(2026, 11, 24)
END = TODAY.isoformat()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeResp:
    def __init__(self, status=200, text="", content=b""):
        self.status_code, self.text, self.content = status, text, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class Net:
    """Route requests.get by URL; record calls."""

    def __init__(self):
        self.routes = {shiller._LANDING_URL: FakeResp(text=LANDING), WB_URL: FakeResp(content=WB),
                       shiller._YALE_URL: FakeResp(content=WB_STALE)}
        self.calls = []

    def __call__(self, url, **kw):
        self.calls.append(url)
        r = self.routes.get(url)
        if r is None:
            raise requests.ConnectionError(f"no route to {url}")
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(shiller, "_WORKBOOK_CACHE", {})
    monkeypatch.setattr(shiller, "_MTD_CACHE", {})
    net = Net()
    monkeypatch.setattr(shiller.requests, "get", net)

    def no_yahoo(*a, **k):
        raise RuntimeError("yfinance not stubbed for this test")

    monkeypatch.setattr(yfinance, "download", no_yahoo)
    return net


@pytest.fixture
def net(isolated):
    return isolated


def freeze_now(monkeypatch, ts="2026-11-24 12:00"):
    """pd.Timestamp.now() drives the month-end today-clamp in shiller._series_from."""
    monkeypatch.setattr(pd.Timestamp, "now", classmethod(lambda cls, tz=None: pd.Timestamp(ts)))


@pytest.fixture
def clock(monkeypatch):
    freeze_now(monkeypatch)


def mtd(monkeypatch, closes, start="2026-11-02", prior=None):
    """yfinance.download stub returning daily closes (optionally a prior-month row first)."""
    idx = pd.bdate_range(start, periods=len(closes))
    data = list(closes)
    if prior is not None:
        idx = idx.insert(0, pd.Timestamp("2026-10-30"))
        data = [prior] + data
    calls = []

    def dl(ticker, **kw):
        calls.append((ticker, kw))
        return pd.DataFrame({"Close": data, "Open": data}, index=pd.DatetimeIndex(idx, name="Date"))

    monkeypatch.setattr(yfinance, "download", dl)
    return calls


def parsed():
    return shiller._parse_workbook(WB)


# ---------------------------------------------------------------------------
# Landing-page discovery and download
# ---------------------------------------------------------------------------
def test_discover_protocol_relative_href_ignores_fig3(net):
    assert shiller._discover_url() == WB_URL
    assert net.calls == [shiller._LANDING_URL]


@pytest.mark.parametrize(
    "href, expected",
    [
        ("https://example.org/x/ie_data.xls", "https://example.org/x/ie_data.xls"),
        ("http://example.org/ie_data.xls?v=1", "http://example.org/ie_data.xls?v=1"),
        ("/files/IE_DATA.XLS", "https://shillerdata.com/files/IE_DATA.XLS"),
    ],
)
def test_discover_other_href_forms(net, href, expected):
    net.routes[shiller._LANDING_URL] = FakeResp(text=f"<a href='{href}'>data</a>")
    assert shiller._discover_url() == expected


@pytest.mark.parametrize(
    "page",
    ['<a href="//img1.wsimg.com/x/Fig3-1%20(1).xls">fig</a>', "<p>ie_data.xls mentioned but not linked</p>", ""],
)
def test_discover_missing_link_raises(net, page):
    net.routes[shiller._LANDING_URL] = FakeResp(text=page)
    with pytest.raises(FetchError, match="No ie_data.xls link"):
        shiller._discover_url()


def test_discover_landing_http_error(net):
    net.routes[shiller._LANDING_URL] = FakeResp(status=503)
    with pytest.raises(FetchError, match="Failed to load"):
        shiller._discover_url()


def test_download_requires_ole2_magic(net):
    net.routes[WB_URL] = FakeResp(content=b"<!DOCTYPE html><html>blocked</html>")
    with pytest.raises(FetchError, match="not an OLE2 .xls workbook"):
        shiller._download(WB_URL)
    assert shiller._download(shiller._YALE_URL).startswith(shiller._OLE2_MAGIC)


def test_get_monthly_prefers_shillerdata(net):
    m = shiller._get_monthly(TODAY)
    assert net.calls == [shiller._LANDING_URL, WB_URL]
    assert str(m.index.max()) == "2026-11"
    assert shiller._get_monthly(TODAY) is m                           # cached per process
    assert len(net.calls) == 2


@pytest.mark.parametrize(
    "breakage",
    ["no_link", "landing_down", "not_ole2"],
)
def test_get_monthly_falls_back_to_yale_and_rejects_stale(net, breakage):
    if breakage == "no_link":
        net.routes[shiller._LANDING_URL] = FakeResp(text="<html>moved</html>")
    elif breakage == "landing_down":
        net.routes[shiller._LANDING_URL] = requests.ConnectionError("down")
    else:
        net.routes[WB_URL] = FakeResp(content=b"PK\x03\x04 xlsx")
    with pytest.raises(FetchError) as ei:
        shiller._get_monthly(TODAY)
    msg = str(ei.value)
    assert msg.startswith("Shiller workbook unavailable - shillerdata.com: ")
    assert "yale: workbook stale: last earnings month 2023-07" in msg     # Yale copy ends 2023
    assert net.calls[-1] == shiller._YALE_URL


def test_yale_fallback_used_when_fresh(net):
    net.routes[shiller._LANDING_URL] = FakeResp(text="")
    net.routes[shiller._YALE_URL] = FakeResp(content=WB)
    assert str(shiller._get_monthly(TODAY)["E"].dropna().index.max()) == "2026-09"


@pytest.mark.parametrize("today, ok", [(date(2027, 6, 30), True), (date(2027, 7, 1), False)])
def test_staleness_boundary_9_months(net, today, ok):
    """Last E month 2026-09: 9 months later is accepted, 10 rejected."""
    if ok:
        shiller._get_monthly(today)
    else:
        with pytest.raises(FetchError, match="10 months old"):
            shiller._get_monthly(today)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def test_parse_selects_columns_by_position_and_exact_cape():
    m = parsed()
    assert list(m.columns) == ["P", "D", "E", "CPI", "CAPE"]
    sep = m.loc[pd.Period("2026-09", "M")]
    assert (sep["P"], sep["D"], sep["E"], sep["CPI"], sep["CAPE"]) == pytest.approx(
        (7669.414286, 81.9895, 295.5184, 337.511, 40.268315))       # CAPE, not TR CAPE (+2.5)


def test_parse_decimal_months():
    m = parsed()
    idx = [str(p) for p in m.index]
    assert idx[:2] == ["1871-01", "1871-02"]
    assert idx[-3:] == ["2026-09", "2026-10", "2026-11"]            # 2026.1 is October, not January
    assert idx.count("2026-01") == 1 and idx.count("2026-10") == 1
    assert m.index.is_monotonic_increasing and m.index.is_unique
    assert pd.isna(m.loc[pd.Period("2026-10", "M"), "E"]) and pd.isna(m.loc[pd.Period("2026-11", "M"), "D"])
    assert m.loc[pd.Period("2026-10", "M"), "CPI"] == pytest.approx(338.7765)
    assert pd.isna(m.loc[pd.Period("1871-01", "M"), "CAPE"])


@pytest.fixture
def raw_frame(monkeypatch):
    """The fixture's raw sheet (header=None) and a hook to feed a modified copy to the parser."""
    real = pd.read_excel
    raw = real(__import__("io").BytesIO(WB), sheet_name="Data", header=None, engine="xlrd")

    def feed(frame):
        def fake(buf, sheet_name=None, header=None, engine=None):
            assert (sheet_name, header, engine) == ("Data", None, "xlrd")
            return frame
        monkeypatch.setattr(pd, "read_excel", fake)

    return raw.copy(), feed


def test_parse_month_variants(raw_frame):
    raw, feed = raw_frame
    extra = pd.DataFrame([[2026.12, 7700.0] + [None] * (raw.shape[1] - 2),
                          [2026.13, 1.0] + [None] * (raw.shape[1] - 2),      # invalid month -> dropped
                          [2026.11, 7777.0] + [None] * (raw.shape[1] - 2)],   # duplicate -> last wins
                         columns=raw.columns)
    feed(pd.concat([raw, extra], ignore_index=True))
    m = shiller._parse_workbook(WB)
    assert [str(p) for p in m.index[-3:]] == ["2026-10", "2026-11", "2026-12"]
    assert m.loc[pd.Period("2026-11", "M"), "P"] == 7777.0
    assert m.loc[pd.Period("2026-12", "M"), "P"] == 7700.0


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda r: r.iloc[7].replace({"D": "Div"}), "Unexpected Shiller columns 1-4"),
        (lambda r: r.iloc[7].replace({"CAPE": "CAPE10"}), "Could not detect header row"),
        (lambda r: r.iloc[7].replace({"Date": "When"}), "Could not detect header row"),
    ],
)
def test_parse_header_validation(raw_frame, mutate, match):
    raw, feed = raw_frame
    raw.iloc[7] = mutate(raw)
    feed(raw)
    with pytest.raises(FetchError, match=match):
        shiller._parse_workbook(WB)


def test_parse_cape_column_found_by_exact_name_anywhere(raw_frame):
    """Columns 12 (CAPE) and 14 (TR CAPE) swapped: the parser still picks 'CAPE'."""
    raw, feed = raw_frame
    raw[[12, 14]] = raw[[14, 12]].values
    feed(raw)
    assert parsed().loc[pd.Period("2026-09", "M"), "CAPE"] == pytest.approx(40.268315)


def test_parse_header_too_low_is_not_found(raw_frame):
    raw, feed = raw_frame
    feed(pd.concat([pd.DataFrame([[None] * raw.shape[1]] * 10, columns=raw.columns), raw], ignore_index=True))
    with pytest.raises(FetchError, match="first 15 rows"):
        shiller._parse_workbook(WB)


def test_parse_read_failure(monkeypatch):
    def boom(*a, **k):
        raise ValueError("Worksheet named 'Data' not found")
    monkeypatch.setattr(pd, "read_excel", boom)
    with pytest.raises(FetchError, match="Failed to read Shiller workbook 'Data' sheet"):
        shiller._parse_workbook(WB)


def test_parse_empty_columns(raw_frame):
    raw, feed = raw_frame
    raw.iloc[8:, 3] = None                                  # all E blank
    feed(raw)
    with pytest.raises(FetchError, match="P/E columns are empty"):
        shiller._parse_workbook(WB)


# ---------------------------------------------------------------------------
# _mtd_price
# ---------------------------------------------------------------------------
def test_mtd_average_excludes_prior_month(monkeypatch):
    calls = mtd(monkeypatch, [7500.0, 7600.0, 7700.0], prior=9999.0)
    assert shiller._mtd_price(TODAY) == pytest.approx(7600.0)
    assert calls[0][0] == "^GSPC" and calls[0][1]["start"] == "2026-11-01"
    assert shiller._mtd_price(TODAY) == pytest.approx(7600.0)
    assert len(calls) == 1                                  # cached per month


@pytest.mark.parametrize("closes", [[], [50.0], [200000.0]])
def test_mtd_empty_or_implausible_is_none(monkeypatch, closes):
    mtd(monkeypatch, closes)
    assert shiller._mtd_price(TODAY) is None


def test_mtd_download_failure_is_none():
    assert shiller._mtd_price(TODAY) is None               # autouse stub raises


# ---------------------------------------------------------------------------
# _derive: provisional rows, MTD override, CAPE scaling
# ---------------------------------------------------------------------------
def test_derive_carries_forward_de_and_flags_provisional(monkeypatch):
    mtd(monkeypatch, [7600.0, 7700.0])
    m = parsed()
    eff = shiller._derive(m, TODAY)
    sep, octo, nov = (eff.loc[pd.Period(p, "M")] for p in ("2026-09", "2026-10", "2026-11"))
    assert (octo["D"], octo["E"], nov["D"], nov["E"]) == (sep["D"], sep["E"], sep["D"], sep["E"])
    assert (bool(sep["prov"]), bool(octo["prov"]), bool(nov["prov"])) == (False, True, True)
    assert not eff.loc[: pd.Period("2026-09", "M"), "prov"].any()
    assert octo["P"] == pytest.approx(7726.692727)          # prior months keep Shiller's P
    assert nov["P"] == pytest.approx(7650.0)                # current month: MTD average
    assert nov["CAPE"] == pytest.approx(39.374477 * 7650.0 / 7619.38)


def test_derive_mtd_failure_keeps_shiller_price():
    m = parsed()
    eff = shiller._derive(m, TODAY)
    nov = eff.loc[pd.Period("2026-11", "M")]
    assert nov["P"] == pytest.approx(7619.38) and nov["CAPE"] == pytest.approx(39.374477)
    assert bool(nov["prov"]) is True


def test_derive_new_current_month_row(monkeypatch):
    """No Shiller row for December yet: row added with MTD price and the CPI/CAPE rule."""
    mtd(monkeypatch, [7800.0, 7900.0], start="2026-12-01")
    m = parsed()
    eff = shiller._derive(m, date(2026, 12, 10))
    dec, nov = eff.loc[pd.Period("2026-12", "M")], eff.loc[pd.Period("2026-11", "M")]
    p = 7850.0
    cpi_est = 339.40925
    assert dec["P"] == pytest.approx(p) and dec["CPI"] == pytest.approx(cpi_est)
    assert dec["CAPE"] == pytest.approx((p / cpi_est) / (nov["P"] / nov["CPI"] / nov["CAPE"]))
    assert dec["CAPE"] == pytest.approx(39.374477 * p / 7619.38)
    assert (dec["D"], dec["E"]) == (nov["D"], nov["E"]) and bool(dec["prov"])
    assert bool(nov["prov"]) is True and nov["P"] == pytest.approx(7619.38)


def test_derive_new_month_omitted_without_mtd():
    eff = shiller._derive(parsed(), date(2026, 12, 10))
    assert str(eff.index.max()) == "2026-11"


def test_derive_rejects_stale_prices():
    with pytest.raises(FetchError, match="price data stale"):
        shiller._derive(parsed(), date(2027, 3, 1))         # 4 months after 2026-11
    shiller._derive(parsed(), date(2027, 2, 1))             # 3 months: ok


def test_derive_does_not_mutate_cached_monthly(monkeypatch):
    mtd(monkeypatch, [7600.0])
    m = parsed()
    before = m.copy()
    shiller._derive(m, TODAY)
    pd.testing.assert_frame_equal(m, before)


# ---------------------------------------------------------------------------
# Series values and date conventions
# ---------------------------------------------------------------------------
def _eff(monkeypatch, closes=(7600.0, 7700.0)):
    mtd(monkeypatch, list(closes))
    return shiller._derive(parsed(), TODAY)


def test_real_earnings_rebased_to_latest_cpi(monkeypatch, clock):
    eff = _eff(monkeypatch)
    real = shiller._series_from(eff, "real_earnings").set_index("date")["value"]
    nom = shiller._series_from(eff, "nominal_earnings").set_index("date")["value"]
    cpi_last = 339.40925
    assert real[pd.Timestamp("2026-09-30")] == pytest.approx(295.5184 * cpi_last / 337.511)
    assert nom[pd.Timestamp("2026-09-30")] == pytest.approx(295.5184)
    # carried-forward months: nominal flat; real re-based with each month's CPI
    assert nom[pd.Timestamp("2026-10-31")] == pytest.approx(295.5184)
    assert real[pd.Timestamp("2026-10-31")] == pytest.approx(295.5184 * cpi_last / 338.7765)
    assert real.iloc[-1] == pytest.approx(nom.iloc[-1])     # latest month: factor 1


@pytest.mark.parametrize(
    "sid, last_date",
    [("pe", "2026-11-01"), ("earnings_yield", "2026-11-01"), ("cape", "2026-11-01"),
     ("dividend_yield", "2026-11-24"), ("real_earnings", "2026-11-24"), ("nominal_earnings", "2026-11-24")],
)
def test_date_conventions_and_today_clamp(monkeypatch, clock, sid, last_date):
    s = shiller._series_from(_eff(monkeypatch), sid)
    assert s["date"].iloc[-1] == pd.Timestamp(last_date)
    prev = s["date"].iloc[-2]
    month_end = sid in ("dividend_yield", "real_earnings", "nominal_earnings")
    assert prev == (pd.Timestamp("2026-10-31") if month_end else pd.Timestamp("2026-10-01"))
    assert (s["date"].dt.is_month_end if month_end else s["date"].dt.is_month_start).iloc[:-1].all()


def test_month_end_not_clamped_on_last_day(monkeypatch):
    freeze_now(monkeypatch, "2026-11-30 09:00")
    mtd(monkeypatch, [7600.0])
    eff = shiller._derive(parsed(), date(2026, 11, 30))
    assert shiller._series_from(eff, "dividend_yield")["date"].iloc[-1] == pd.Timestamp("2026-11-30")


def test_today_clamp_uses_fetch_today(monkeypatch, net):
    freeze_now(monkeypatch, "2026-12-15 10:00")
    mtd(monkeypatch, [7600.0])
    df = shiller.fetch("dividend_yield", start_date="2026-01-01", end_date=END)
    assert df["date"].iloc[-1] == pd.Timestamp(END)


def test_ratio_values(monkeypatch, clock):
    eff = _eff(monkeypatch)
    nov = eff.loc[pd.Period("2026-11", "M")]
    get = lambda sid: shiller._series_from(eff, sid)["value"].iloc[-1]   # noqa: E731
    assert get("pe") == pytest.approx(7650.0 / 295.5184)
    assert get("earnings_yield") == pytest.approx(295.5184 / 7650.0)
    assert get("dividend_yield") == pytest.approx(81.9895 / 7650.0)
    assert get("cape") == pytest.approx(nov["CAPE"])


# ---------------------------------------------------------------------------
# fetch(): splice, ranges, provisional_from
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sid", ["dividend_yield", "pe", "earnings_yield", "cape"])
def test_splice_preserves_archive_exactly(monkeypatch, net, clock, sid):
    mtd(monkeypatch, [7600.0, 7700.0])
    arch = ARCHIVE["series"][sid]
    df = shiller.fetch(sid, start_date="1900-01-01", end_date=END)
    n = len(arch["dates"])
    assert df["date"].dt.strftime("%Y-%m-%d").tolist()[:n] == arch["dates"]
    assert df["value"].tolist()[:n] == arch["values"]
    assert ARCHIVE["cutoff_month"] == "2026-10"
    # both October rows kept (first-of-month/30th, or 30th/31st)
    oct_rows = [d for d in arch["dates"] if d.startswith("2026-10")]
    assert len(oct_rows) == 2
    # Shiller rows strictly after October
    tail = df.iloc[n:]
    assert len(tail) == 1 and (tail["date"] > pd.Timestamp("2026-10-31")).all()


def test_splice_applies_start_date_to_archive(monkeypatch, net, clock):
    mtd(monkeypatch, [7600.0])
    df = shiller.fetch("pe", start_date="2010-01-01", end_date=END)
    assert df["date"].min() >= pd.Timestamp("2010-01-01")
    assert df["date"].iloc[-1] == pd.Timestamp("2026-11-01")


def test_archive_1980_pe_below_range_does_not_fail(monkeypatch, net, clock):
    mtd(monkeypatch, [7600.0])
    df = shiller.fetch("pe", start_date="1980-01-01", end_date=END)
    first = df.iloc[0]
    assert first["date"] == pd.Timestamp("1980-01-01") and first["value"] == pytest.approx(7.39)
    assert first["value"] < shiller._RANGES["pe"][0]


@pytest.mark.parametrize("sid", ["real_earnings", "nominal_earnings"])
def test_earnings_rebuilt_fully_from_shiller(monkeypatch, net, clock, sid):
    mtd(monkeypatch, [7600.0])
    df = shiller.fetch(sid, start_date="2010-01-01", end_date=END)
    eff = shiller._derive(shiller._get_monthly(TODAY), TODAY)
    expected = shiller._series_from(eff, sid)
    expected = expected[expected["date"] >= pd.Timestamp("2010-01-01")]
    assert df["date"].tolist() == expected["date"].tolist()
    assert df["value"].tolist() == pytest.approx(expected["value"].tolist())
    assert df["date"].iloc[0] == pd.Timestamp("2010-01-31")


def test_earnings_range_applies_from_start_date(monkeypatch, net, clock):
    """Fully derived series are range-checked from start_date: 1871 E = 0.40 fails closed
    (config starts these series in 2010)."""
    mtd(monkeypatch, [7600.0])
    with pytest.raises(FetchError, match=r"'nominal_earnings' value 0\.4000 on 1871-02-28 outside .*\(2 point\(s\)\)"):
        shiller.fetch("nominal_earnings", start_date="1800-01-01", end_date=END)


@pytest.mark.parametrize("sid", ["pe", "earnings_yield", "dividend_yield"])
def test_range_check_on_derived_month_fails_closed(monkeypatch, net, clock, sid):
    mtd(monkeypatch, [40000.0])                 # implausible MTD price -> PE 135, EY 0.7%, DY 0.2%
    with pytest.raises(FetchError, match=f"Shiller-derived '{sid}' value .* outside expected range"):
        shiller.fetch(sid, start_date="2010-01-01", end_date=END)


def test_cape_range_check(monkeypatch, net, clock):
    mtd(monkeypatch, [99000.0])                 # CAPE ~ 39.37 * 13 -> out of [5, 60]
    with pytest.raises(FetchError, match="'cape'"):
        shiller.fetch("cape", start_date="2010-01-01", end_date=END)


@pytest.mark.parametrize(
    "sid, expected",
    [("pe", "2026-11-01"), ("earnings_yield", "2026-11-01"), ("cape", "2026-11-01"),
     ("dividend_yield", "2026-11-24"),
     ("real_earnings", "2026-10-31"), ("nominal_earnings", "2026-10-31")],   # Oct D/E blank -> provisional
)
def test_provisional_from(monkeypatch, net, clock, sid, expected):
    mtd(monkeypatch, [7600.0])
    df = shiller.fetch(sid, start_date="2010-01-01", end_date=END)
    assert df.attrs["provisional_from"] == expected


def test_provisional_from_none_when_all_published(monkeypatch, net, clock):
    """Workbook with D/E for every month and no current-month row -> nothing provisional."""
    m = parsed()
    m.loc[pd.Period("2026-10", "M"), ["D", "E"]] = [82.0, 296.0]
    m = m.drop(pd.Period("2026-11", "M"))
    shiller._WORKBOOK_CACHE["monthly"] = m
    df = shiller.fetch("nominal_earnings", start_date="2010-01-01", end_date=END)   # MTD fails -> no Nov row
    assert df.attrs["provisional_from"] is None
    assert df["date"].iloc[-1] == pd.Timestamp("2026-10-31")


def test_fetch_unknown_series_id():
    with pytest.raises(FetchError, match="supports"):
        shiller.fetch("sp500_pe")


def test_fetch_contract_shape(monkeypatch, net, clock):
    mtd(monkeypatch, [7600.0])
    df = shiller.fetch("cape", start_date="2020-01-01", end_date=END)
    assert list(df.columns) == ["date", "value"]
    assert df["date"].is_monotonic_increasing and df["value"].notna().all()
    assert df["date"].dtype == "datetime64[ns]"


def test_missing_archive_fails_closed(monkeypatch, net, clock, tmp_path):
    mtd(monkeypatch, [7600.0])
    monkeypatch.setattr(shiller, "_ARCHIVE_PATH", tmp_path / "missing.json")
    with pytest.raises(FetchError, match="Cannot load multpl archive for 'pe'"):
        shiller.fetch("pe", end_date=END)


def test_workbook_fetched_once_for_all_series(monkeypatch, net, clock):
    mtd(monkeypatch, [7600.0])
    for sid in shiller._SERIES:
        shiller.fetch(sid, start_date="2010-01-01", end_date=END)
    assert net.calls.count(WB_URL) == 1 and net.calls.count(shiller._LANDING_URL) == 1


# ---------------------------------------------------------------------------
# refresh_v2 integration: provisional_from reaches data_v2.json
# ---------------------------------------------------------------------------
def test_fetch_standard_copies_provisional_from_for_shiller_only():
    df = pd.DataFrame({"date": pd.to_datetime(["2026-10-01", "2026-11-01"]), "value": [26.0, 25.9]})
    df.attrs["provisional_from"] = "2026-11-01"
    stub = SimpleNamespace(fetch=lambda **kw: df)
    out = refresh_v2._fetch_standard({"source": "shiller", "series_id": "pe"}, {"shiller": stub})
    assert out == {"dates": ["2026-10-01", "2026-11-01"], "values": [26.0, 25.9], "provisional_from": "2026-11-01"}
    out = refresh_v2._fetch_standard({"source": "yahoo", "series_id": "^GSPC"}, {"yahoo": stub})
    assert "provisional_from" not in out
    df.attrs.clear()
    out = refresh_v2._fetch_standard({"source": "shiller", "series_id": "pe"}, {"shiller": stub})
    assert out["provisional_from"] is None


def test_run_end_to_end_writes_provisional_from(monkeypatch, net, clock, freeze, la_tz, tmp_path):
    mtd(monkeypatch, [7600.0, 7700.0])
    freeze(datetime(2026, 11, 24, 13, 20, tzinfo=PT))                     # refresh_v2 clock
    freeze(datetime(2026, 11, 24, 13, 20, tzinfo=PT), module=shiller)     # shiller's "today" (PT)
    cfg = {"series": [
        {"id": "sp500_pe", "source": "shiller", "series_id": "pe", "label": "PE",
         "start_date": "2010-01-01", "refresh_frequency": "daily_after_close"},
        {"id": "sp500_earnings_nominal", "source": "shiller", "series_id": "nominal_earnings",
         "label": "E", "start_date": "2010-01-01", "refresh_frequency": "daily_after_close"},
    ]}
    p = tmp_path / "series.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(refresh_v2, "CONFIG_PATH", p)
    monkeypatch.setattr(refresh_v2, "_load_fetchers", lambda: {"shiller": shiller})
    refresh_v2.run()
    out = json.loads(refresh_v2.OUTPUT_PATH.read_text(encoding="utf-8"))
    pe, e = out["series"]["sp500_pe"], out["series"]["sp500_earnings_nominal"]
    assert pe["provisional_from"] == "2026-11-01" and pe["dates"][-1] == "2026-11-01"
    assert pe["values"][-1] == round(7650.0 / 295.5184, 4)
    assert e["provisional_from"] == "2026-10-31" and e["dates"][-1] == "2026-11-24"
    assert out["metadata"]["failure_count"] == 0
    # spliced archive rows survive the JSON round trip unchanged
    arch = ARCHIVE["series"]["pe"]
    i0 = arch["dates"].index("2010-01-01")
    assert pe["dates"][: len(arch["dates"]) - i0] == arch["dates"][i0:]


def test_run_carry_forward_keeps_provisional_from(monkeypatch, freeze, la_tz, tmp_path):
    """A skipped or failed Shiller series is carried forward with its provisional_from."""
    freeze(datetime(2026, 11, 24, 15, 50, tzinfo=PT))
    cfg = {"series": [{"id": "shiller_cape", "source": "shiller", "series_id": "cape", "label": "CAPE",
                       "refresh_frequency": "daily_after_close"}]}
    p = tmp_path / "series.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(refresh_v2, "CONFIG_PATH", p)
    prior = {"metadata": {"statuses": {"shiller_cape": {"status": "ok", "last_fetched": "2026-11-23T13:20:00"}}},
             "series": {"shiller_cape": {"dates": ["2026-11-01"], "values": [39.4], "provisional_from": "2026-11-01"}}}
    refresh_v2.OUTPUT_PATH.write_text(json.dumps(prior), encoding="utf-8")

    def boom(**kw):
        raise FetchError("Shiller workbook unavailable")

    monkeypatch.setattr(refresh_v2, "_load_fetchers", lambda: {"shiller": SimpleNamespace(fetch=boom)})
    refresh_v2.run()
    out = json.loads(refresh_v2.OUTPUT_PATH.read_text(encoding="utf-8"))
    assert out["series"]["shiller_cape"] == prior["series"]["shiller_cape"]
    assert out["metadata"]["statuses"]["shiller_cape"]["status"] == "error"
