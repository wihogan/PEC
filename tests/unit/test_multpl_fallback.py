"""Amendment D: valuation source order shillerdata.com -> multpl.com -> Yale -> fail closed.

Offline. multpl responses are the saved pages in tests/fixtures/multpl/ (optionally with
synthetic rows injected at the top of the table, as multpl lists newest first); Shiller
workbooks are the synthetic .xls fixtures in tests/fixtures/shiller/. requests.get is a
URL router; yfinance.download raises unless a test stubs it.
"""
import json
import os
import time
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import requests
import yaml
import yfinance

import healthcheck
import refresh_v2
from conftest import FIXTURES, REPO
from fetchers import multpl, shiller
from fetchers.base import FetchError

PT = ZoneInfo("America/Los_Angeles")
WB = (FIXTURES / "shiller" / "ie_data_synthetic.xls").read_bytes()
WB_STALE = (FIXTURES / "shiller" / "ie_data_stale_yale.xls").read_bytes()
ARCHIVE = json.loads((REPO / "config" / "multpl_archive.json").read_text(encoding="utf-8"))
WB_URL = "https://img1.wsimg.com/blobby/go/abc/downloads/uuid-1/ie_data.xls?ver=1"
LANDING = f'<a href="//img1.wsimg.com/blobby/go/abc/downloads/uuid-1/ie_data.xls?ver=1">data</a>'
MURL = {mp: multpl._BASE_URL.format(slug=multpl._SERIES[mp][0]) for mp in multpl._SERIES}
RATIO = ["dividend_yield", "pe", "earnings_yield", "cape"]
EARNINGS = ["real_earnings", "nominal_earnings"]
END = "2026-11-25"
TODAY = date(2026, 11, 25)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class Resp:
    def __init__(self, status=200, text="", content=b""):
        self.status_code, self.text, self.content = status, text, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error")


class Net:
    def __init__(self):
        self.routes, self.calls, self.kwargs = {}, [], []

    def __call__(self, url, **kw):
        self.calls.append(url)
        self.kwargs.append(kw)
        r = self.routes.get(url, requests.ConnectionError(f"[Errno 111] Connection refused: {url}"))
        if callable(r) and not isinstance(r, Exception):
            r = r()
        if isinstance(r, Exception):
            raise r
        return r

    def multpl_calls(self):
        return [u for u in self.calls if "multpl.com" in u]


def page(mp, rows=()):
    """Saved multpl page with extra rows (date_str, value_str, dagger) injected at the top."""
    html = (FIXTURES / "multpl" / f"multpl_{mp}.html").read_text(encoding="utf-8")
    marker = "<th>Value</th>\n</tr>\n"
    i = html.index(marker) + len(marker)
    extra = "".join(
        f'\n<tr class="odd">\n<td>{d}</td>\n<td>\n'
        f'{"<abbr title=" + chr(34) + "Estimate" + chr(34) + ">&#8224;</abbr>" if dag else "&#x2002;"}\n{v}\n</td>\n</tr>\n'
        for d, v, dag in rows)
    return html[:i] + extra + html[i:]


@pytest.fixture(autouse=True)
def net(monkeypatch):
    n = Net()
    monkeypatch.setattr(requests, "get", n)

    def no_yahoo(*a, **k):
        raise RuntimeError("yfinance not stubbed")

    monkeypatch.setattr(yfinance, "download", no_yahoo)
    return n


def primary_ok(net):
    net.routes[shiller._LANDING_URL] = Resp(text=LANDING)
    net.routes[WB_URL] = Resp(content=WB)


def multpl_ok(net, rows=None):
    """Serve all multpl pages; rows: {mp_id: [(date, value, dagger), ...]} injected."""
    rows = rows or {}
    for mp in multpl._SERIES:
        net.routes[MURL[mp]] = Resp(text=page(mp, rows.get(mp, ())))


def yale_fresh(net):
    net.routes[shiller._YALE_URL] = Resp(content=WB)


def yale_stale(net):
    net.routes[shiller._YALE_URL] = Resp(content=WB_STALE)


NOV_ROWS = {
    "sp500_pe": [("Nov 24, 2026", "25.50", True), ("Nov 1, 2026", "25.80", False)],
    "sp500_earnings_yield": [("Nov 24, 2026", "3.92%", True)],
    "sp500_div_yield": [("Nov 24, 2026", "1.08%", True), ("Nov 3, 2026", "1.06%", True)],
    "shiller_pe": [("Nov 24, 2026", "39.80", True)],
}


def fetch(sid, end=END, start="2010-01-01"):
    return shiller.fetch(sid, start_date=start, end_date=end)


def new_run(monkeypatch):
    monkeypatch.setattr(shiller, "_WORKBOOK_CACHE", {})
    monkeypatch.setattr(shiller, "_MTD_CACHE", {})


def health():
    return json.loads(shiller._HEALTH_PATH.read_text()) if shiller._HEALTH_PATH.exists() else {}


# ---------------------------------------------------------------------------
# Source order matrix
# ---------------------------------------------------------------------------
def test_order_primary_ok_never_calls_multpl_or_yale(net):
    primary_ok(net)
    multpl_ok(net, NOV_ROWS)
    for sid in RATIO + EARNINGS:
        assert fetch(sid).attrs["source_used"] == "shiller"
    assert net.calls == [shiller._LANDING_URL, WB_URL]


def test_order_primary_down_multpl_serves_ratio_series(net):
    multpl_ok(net, NOV_ROWS)
    yale_stale(net)
    for sid in RATIO:
        assert fetch(sid).attrs["source_used"] == "multpl"
    for sid in EARNINGS:                                  # never multpl; Yale stale -> fail closed
        with pytest.raises(FetchError, match=f"Valuation sources unavailable for '{sid}'"):
            fetch(sid)
    assert net.multpl_calls() == [MURL[m] for m in ("sp500_div_yield", "sp500_pe", "sp500_earnings_yield", "shiller_pe")]
    assert net.calls.count(shiller._LANDING_URL) == 1 and net.calls.count(shiller._YALE_URL) == 1


def test_order_everything_down(net):
    yale_stale(net)
    for sid in RATIO + EARNINGS:
        with pytest.raises(FetchError) as ei:
            fetch(sid)
        msg = str(ei.value)
        assert "shillerdata.com:" in msg and "yale: workbook stale" in msg
    assert net.calls == [shiller._LANDING_URL, MURL["sp500_div_yield"], shiller._YALE_URL]


def test_order_primary_rejected_stale_falls_to_multpl(net):
    net.routes[shiller._LANDING_URL] = Resp(text=LANDING)
    net.routes[WB_URL] = Resp(content=WB_STALE)         # loads but last E 2023-07 -> rejected
    multpl_ok(net, NOV_ROWS)
    df = fetch("cape")
    assert df.attrs["source_used"] == "multpl"
    assert net.calls == [shiller._LANDING_URL, WB_URL, MURL["shiller_pe"]]


def test_order_yale_serves_everything_when_multpl_down(net):
    yale_fresh(net)
    for sid in RATIO + EARNINGS:
        assert fetch(sid).attrs["source_used"] == "yale"
    assert net.calls == [shiller._LANDING_URL, MURL["sp500_div_yield"], shiller._YALE_URL]


def test_order_multpl_ratio_yale_earnings(net):
    multpl_ok(net, NOV_ROWS)
    yale_fresh(net)
    got = {sid: fetch(sid).attrs["source_used"] for sid in RATIO + EARNINGS}
    assert got == {**{s: "multpl" for s in RATIO}, **{s: "yale" for s in EARNINGS}}
    shiller.pending_notice(TODAY)
    assert health()["source"] == "multpl+yale"


@pytest.mark.parametrize("failure", [requests.ConnectionError("refused"), requests.Timeout("read timed out"),
                                     Resp(status=503)])
def test_multpl_network_failure_marks_down_for_all_series(net, failure):
    multpl_ok(net, NOV_ROWS)
    net.routes[MURL["sp500_div_yield"]] = failure
    yale_stale(net)
    for sid in RATIO:
        with pytest.raises(FetchError):
            fetch(sid)
    assert net.multpl_calls() == [MURL["sp500_div_yield"]]                  # one request, then down


def test_multpl_parse_failure_cached_per_series_only(net):
    multpl_ok(net, NOV_ROWS)
    net.routes[MURL["sp500_pe"]] = Resp(text="<html><body>maintenance</body></html>")
    yale_stale(net)
    with pytest.raises(FetchError, match="Table #datatable not found"):
        fetch("pe")
    with pytest.raises(FetchError):
        fetch("pe")                                     # cached: no second pe request
    assert fetch("cape").attrs["source_used"] == "multpl"                   # others still tried
    assert net.multpl_calls() == [MURL["sp500_pe"], MURL["shiller_pe"]]


def test_multpl_staleness_not_cached_as_down(net):
    multpl_ok(net)                                      # newest multpl rows: Oct 2026
    yale_stale(net)
    with pytest.raises(FetchError, match=r"multpl data stale: last month 2026-10, current 2027-02"):
        fetch("pe", end="2027-02-10")
    fetch("cape", end="2027-01-10")                     # 3 months: accepted (multpl still up)
    assert len(net.multpl_calls()) == 2


class Clock:
    def __init__(self):
        self.t = 5000.0

    def __call__(self):
        return self.t


def test_budget_exhaustion_skips_multpl_and_yale(monkeypatch, net):
    clk = Clock()
    monkeypatch.setattr(shiller.time, "monotonic", clk)

    def slow_landing():
        clk.t += 89.5
        raise requests.Timeout("landing timed out")

    net.routes[shiller._LANDING_URL] = slow_landing
    multpl_ok(net, NOV_ROWS)
    yale_fresh(net)
    for sid in RATIO + EARNINGS:
        with pytest.raises(FetchError, match="budget"):
            fetch(sid)
    assert net.calls == [shiller._LANDING_URL]          # multpl and Yale never requested


def test_multpl_timeout_clamped_to_budget(monkeypatch, net):
    clk = Clock()
    monkeypatch.setattr(shiller.time, "monotonic", clk)

    def slow_landing():
        clk.t += 70.0                                   # 20 s left
        raise requests.Timeout("landing timed out")

    net.routes[shiller._LANDING_URL] = slow_landing
    multpl_ok(net, NOV_ROWS)
    fetch("pe")
    i = net.calls.index(MURL["sp500_pe"])
    assert net.kwargs[i]["timeout"] == (10.0, 20.0)


# ---------------------------------------------------------------------------
# multpl splice, per-month collapse, dates, provisional, ranges
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sid", RATIO)
def test_multpl_splice_keeps_archive_through_october(net, sid):
    multpl_ok(net, NOV_ROWS)
    df = fetch(sid, start="1900-01-01")
    arch = ARCHIVE["series"][sid]
    n = len(arch["dates"])
    assert df["date"].dt.strftime("%Y-%m-%d").tolist()[:n] == arch["dates"]
    assert df["value"].tolist()[:n] == arch["values"]
    assert len(df) == n + 1 and df["date"].iloc[-1] > pd.Timestamp("2026-10-31")


@pytest.mark.parametrize(
    "sid, date_, value",
    [("pe", "2026-11-01", 25.50),                       # Nov 24 row beats Nov 1 row
     ("earnings_yield", "2026-11-01", 0.0392),
     ("cape", "2026-11-01", 39.80),
     ("dividend_yield", "2026-11-25", 0.0108)],         # month-end convention, clamped to today
)
def test_multpl_appended_row(net, sid, date_, value):
    multpl_ok(net, NOV_ROWS)
    df = fetch(sid)
    assert df["date"].iloc[-1] == pd.Timestamp(date_)
    assert df["value"].iloc[-1] == pytest.approx(value)
    assert df.attrs["provisional_from"] == date_       # current month -> provisional


def test_multpl_month_end_dates_for_ended_months(net):
    rows = {"sp500_div_yield": [("Dec 9, 2026", "1.10%", True), ("Nov 30, 2026", "1.08%", False),
                                ("Nov 20, 2026", "1.20%", False)]}
    multpl_ok(net, rows)
    df = fetch("dividend_yield", end="2026-12-10")
    tail = df.iloc[-2:]
    assert tail["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-11-30", "2026-12-10"]
    assert tail["value"].tolist() == pytest.approx([0.0108, 0.0110])


@pytest.mark.parametrize(
    "end, rows, expected",
    [
        # dagger 2 months back -> provisional from there
        ("2027-01-15", [("Jan 14, 2027", "25.0", False), ("Dec 31, 2026", "25.1", False), ("Nov 30, 2026", "25.2", True)],
         "2026-11-01"),
        # dagger 3 months back is too old; next provisional is the current month
        ("2027-02-10", [("Feb 9, 2027", "25.0", False), ("Jan 30, 2027", "25.1", False), ("Dec 31, 2026", "25.2", False),
                        ("Nov 30, 2026", "25.3", True)], "2027-02-01"),
        # no current-month row, no recent dagger -> None
        ("2027-01-15", [("Dec 31, 2026", "25.1", False), ("Nov 30, 2026", "25.2", False)], None),
        # current-month row without dagger is still provisional
        ("2027-01-15", [("Jan 14, 2027", "25.0", False), ("Dec 31, 2026", "25.1", False), ("Nov 30, 2026", "25.2", False)],
         "2027-01-01"),
    ],
)
def test_multpl_provisional_rule(net, end, rows, expected):
    multpl_ok(net, {"sp500_pe": rows})
    assert fetch("pe", end=end).attrs["provisional_from"] == expected


@pytest.mark.parametrize("end, ok", [("2027-01-31", True), ("2027-02-01", False)])
def test_multpl_staleness_boundary_3_months(net, end, ok):
    multpl_ok(net)                                       # newest month Oct 2026
    yale_stale(net)
    if ok:
        assert fetch("cape", end=end).attrs["source_used"] == "multpl"
    else:
        with pytest.raises(FetchError, match="multpl data stale"):
            fetch("cape", end=end)


@pytest.mark.parametrize("sid, mp, bad", [("pe", "sp500_pe", "75.00"), ("cape", "shiller_pe", "4.00"),
                                          ("dividend_yield", "sp500_div_yield", "9.50%"),
                                          ("earnings_yield", "sp500_earnings_yield", "0.90%")])
def test_multpl_range_check_on_appended_rows(net, sid, mp, bad):
    multpl_ok(net, {mp: [("Nov 24, 2026", bad, True)]})
    yale_stale(net)
    with pytest.raises(FetchError, match=f"multpl-derived '{sid}' value .* outside expected range"):
        fetch(sid)


def test_multpl_archive_1980_pe_not_range_checked(net):
    multpl_ok(net, NOV_ROWS)
    df = fetch("pe", start="1980-01-01")
    assert df["value"].iloc[0] == pytest.approx(7.39)


def test_multpl_october_rows_on_page_do_not_override_archive(net):
    """multpl's own Oct rows (25.86 on Oct 30) are not appended: archive Oct rows stay as archived."""
    multpl_ok(net, {"sp500_pe": [("Oct 31, 2026", "99.00", True), ("Nov 24, 2026", "25.50", True)][::-1]})
    df = fetch("pe")
    octo = df[df["date"].dt.to_period("M") == pd.Period("2026-10", "M")]
    arch = ARCHIVE["series"]["pe"]
    assert octo["value"].tolist() == [v for d, v in zip(arch["dates"], arch["values"]) if d.startswith("2026-10")]


@pytest.mark.parametrize("sid", EARNINGS)
def test_earnings_series_never_call_multpl(net, sid):
    multpl_ok(net, NOV_ROWS)
    yale_fresh(net)
    assert fetch(sid).attrs["source_used"] == "yale"
    assert net.multpl_calls() == []
    assert set(shiller._MULTPL_IDS) == set(RATIO)


# ---------------------------------------------------------------------------
# multpl.fetch_live
# ---------------------------------------------------------------------------
def test_fetch_live_ignores_disk_cache(net, tmp_path):
    cache = multpl._cache_path("sp500_pe")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(page("sp500_pe", [("Nov 24, 2026", "11.11", True)]), encoding="utf-8")   # fresh mtime
    before = cache.read_text(encoding="utf-8")
    net.routes[MURL["sp500_pe"]] = Resp(text=page("sp500_pe", [("Nov 24, 2026", "25.50", True)]))
    df = multpl.fetch_live("sp500_pe", "2026-11-01", end_date="2026-11-30", timeout=(1.0, 2.0))
    assert df.to_dict("records") == [{"date": pd.Timestamp("2026-11-24"), "value": 25.5, "estimate": True}]
    assert net.kwargs[-1]["timeout"] == (1.0, 2.0)
    assert cache.read_text(encoding="utf-8") == before                    # not rewritten
    assert sorted(p.name for p in cache.parent.iterdir()) == [cache.name]  # nothing new written


def test_fetch_live_no_stale_cache_fallback(net):
    cache = multpl._cache_path("sp500_pe")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(page("sp500_pe"), encoding="utf-8")
    with pytest.raises(FetchError, match="Failed to fetch"):
        multpl.fetch_live("sp500_pe")                                       # refused -> error, cache unused


def test_fetch_live_writes_no_cache(net):
    net.routes[MURL["shiller_pe"]] = Resp(text=page("shiller_pe"))
    multpl.fetch_live("shiller_pe", "2026-01-01", end_date="2026-12-31")
    assert not multpl._cache_path("shiller_pe").exists()


def test_fetch_live_estimate_flags(net):
    net.routes[MURL["sp500_pe"]] = Resp(text=page("sp500_pe", [("Nov 24, 2026", "25.50", False)]))
    df = multpl.fetch_live("sp500_pe", "2026-09-01", end_date="2026-11-30")
    assert df[["date", "estimate"]].values.tolist() == [
        [pd.Timestamp("2026-09-01"), True], [pd.Timestamp("2026-10-01"), True],
        [pd.Timestamp("2026-10-30"), True], [pd.Timestamp("2026-11-24"), False]]


def test_fetch_live_unsupported_and_http_error(net):
    with pytest.raises(FetchError, match="Unsupported"):
        multpl.fetch_live("real_earnings")
    net.routes[MURL["sp500_pe"]] = Resp(status=404)
    with pytest.raises(FetchError) as ei:
        multpl.fetch_live("sp500_pe")
    assert isinstance(ei.value.__cause__, requests.RequestException)


def test_parse_html_default_has_no_estimate_column():
    df = multpl._parse_html(page("sp500_pe"), False, date(2026, 9, 1), date(2026, 12, 31))
    assert list(df.columns) == ["date", "value"]


# ---------------------------------------------------------------------------
# source_used propagation: data_v2.json series + statuses, health.json
# ---------------------------------------------------------------------------
def test_source_used_in_data_json(monkeypatch, net, freeze, la_tz, tmp_path):
    multpl_ok(net, NOV_ROWS)
    yale_stale(net)
    freeze(datetime(2026, 11, 25, 13, 20, tzinfo=PT))
    freeze(datetime(2026, 11, 25, 13, 20, tzinfo=PT), module=shiller)
    cfg = {"series": [
        {"id": "sp500_pe", "source": "shiller", "series_id": "pe", "label": "PE",
         "start_date": "2010-01-01", "refresh_frequency": "daily_after_close"},
        {"id": "sp500_earnings_nominal", "source": "shiller", "series_id": "nominal_earnings",
         "label": "E", "start_date": "2010-01-01", "refresh_frequency": "daily_after_close"},
    ]}
    p = tmp_path / "series.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(refresh_v2, "CONFIG_PATH", p)
    prior = {"metadata": {"statuses": {"sp500_earnings_nominal": {"status": "ok", "source_used": "shiller",
                                                                   "last_fetched": "2026-11-24T13:20:00"}}},
             "series": {"sp500_earnings_nominal": {"dates": ["2026-11-24"], "values": [295.5],
                                                    "provisional_from": "2026-10-31", "source_used": "shiller"}}}
    refresh_v2.OUTPUT_PATH.write_text(json.dumps(prior), encoding="utf-8")
    monkeypatch.setattr(refresh_v2, "_load_fetchers", lambda: {"shiller": shiller})
    refresh_v2.run(force=True)
    out = json.loads(refresh_v2.OUTPUT_PATH.read_text(encoding="utf-8"))
    pe, st = out["series"]["sp500_pe"], out["metadata"]["statuses"]
    assert pe["source_used"] == "multpl" and pe["provisional_from"] == "2026-11-01"
    assert st["sp500_pe"]["source_used"] == "multpl" and st["sp500_pe"]["status"] == "ok"
    # earnings: failed (no multpl, Yale stale) -> carried forward with its previous source_used
    assert out["series"]["sp500_earnings_nominal"] == prior["series"]["sp500_earnings_nominal"]
    assert st["sp500_earnings_nominal"]["status"] == "error" and "source_used" not in st["sp500_earnings_nominal"]
    # end-of-run notice: fallback message queued in eps_change.txt
    flag = refresh_v2.EPS_CHANGE_FLAG.read_text(encoding="utf-8")
    assert flag == ("ℹ️ PEC valuation series served by multpl.com fallback "
                    "— shillerdata.com unavailable since 2026-11-25\n")


def test_non_shiller_status_has_no_source_used(monkeypatch, freeze, la_tz, tmp_path):
    freeze(datetime(2026, 11, 25, 13, 20, tzinfo=PT))
    cfg = {"series": [{"id": "sp500", "source": "yahoo", "series_id": "^GSPC", "label": "S&P",
                       "refresh_frequency": "intraday"}]}
    p = tmp_path / "series.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(refresh_v2, "CONFIG_PATH", p)
    stub = SimpleNamespace(fetch=lambda **kw: pd.DataFrame({"date": pd.to_datetime(["2026-11-25"]), "value": [7000.0]}))
    monkeypatch.setattr(refresh_v2, "_load_fetchers", lambda: {"yahoo": stub})
    refresh_v2.run()
    st = json.loads(refresh_v2.OUTPUT_PATH.read_text())["metadata"]["statuses"]["sp500"]
    assert "source_used" not in st


def test_health_json_series_entries_carry_source_used(tmp_path):
    cfg = {"series": [{"id": "sp500_pe", "source": "shiller", "series_id": "pe", "refresh_frequency": "daily_after_close"},
                      {"id": "sp500", "source": "yahoo", "series_id": "^GSPC", "refresh_frequency": "intraday"}],
           "valuation_inputs": {"forward_eps_ntm": {"manual_override": {"value": 400.0, "effective_date": "2026-11-20"}}}}
    cp = tmp_path / "c.yaml"
    cp.write_text(yaml.safe_dump(cfg))
    data = {"metadata": {"last_refreshed": "2026-11-25T13:20:00", "statuses": {
                "sp500_pe": {"status": "ok", "source_used": "multpl"}, "sp500": {"status": "ok"}}},
            "series": {"sp500_pe": {"dates": ["2026-11-01"], "values": [25.5], "source_used": "multpl"},
                       "sp500": {"dates": ["2026-11-25"], "values": [7000.0]}},
            "valuation_inputs": {"forward_eps_ntm": {"value": 400.0, "source": "override"}}}
    dp = tmp_path / "d.json"
    dp.write_text(json.dumps(data))
    r = healthcheck.run_checks(dp, tmp_path / "none.html", cp, datetime(2026, 11, 25, 15, 0, tzinfo=PT))
    assert r["series"]["sp500_pe"]["source_used"] == "multpl"
    assert "source_used" not in r["series"]["sp500"]


# ---------------------------------------------------------------------------
# Health marker and notices
# ---------------------------------------------------------------------------
FALLBACK_MULTPL = ("ℹ️ PEC valuation series served by multpl.com fallback "
                   "— shillerdata.com unavailable since {since}\n")
BACK = "✅ PEC valuation series back on shillerdata.com\n"


def run_once(monkeypatch, net, day, primary, multpl_up, yale, series=RATIO + EARNINGS):
    new_run(monkeypatch)
    net.routes.clear()
    if primary:
        primary_ok(net)
    if multpl_up:
        multpl_ok(net, NOV_ROWS)
    if yale == "fresh":
        yale_fresh(net)
    elif yale == "stale":
        yale_stale(net)
    served = {}
    for sid in series:
        try:
            served[sid] = fetch(sid, end=day.isoformat()).attrs["source_used"]
        except FetchError:
            served[sid] = None
    return served, shiller.pending_notice(day)


def test_outcome_finalised_only_by_pending_notice(monkeypatch, net):
    new_run(monkeypatch)
    multpl_ok(net, NOV_ROWS)
    fetch("pe")
    assert not shiller._HEALTH_PATH.exists()
    shiller.pending_notice(TODAY)
    assert health()["source"] == "multpl" and health()["primary_down_since"] == "2026-11-25"
    assert shiller.pending_notice(TODAY) is None                         # run state consumed


def test_fallback_notice_once_then_back_on_primary_once(monkeypatch, net):
    d1, d2, d3 = date(2026, 11, 25), date(2026, 11, 26), date(2026, 11, 27)
    _, msg = run_once(monkeypatch, net, d1, primary=False, multpl_up=True, yale="stale")
    assert msg == FALLBACK_MULTPL.format(since="2026-11-25")
    assert health()["fail_days"] == 0                                    # something was served
    _, msg = run_once(monkeypatch, net, d2, primary=False, multpl_up=True, yale="stale")
    assert msg is None                                                   # de-duplicated
    assert health()["primary_down_since"] == "2026-11-25"
    _, msg = run_once(monkeypatch, net, d3, primary=True, multpl_up=True, yale="stale")
    assert msg == BACK
    h = health()
    assert h["source"] == "shiller" and h["primary_down_since"] is None and "fallback" not in h["notified"]
    _, msg = run_once(monkeypatch, net, d3, primary=True, multpl_up=True, yale="stale")
    assert msg is None                                                   # back-message sent once


def test_fallback_notice_resent_when_fallback_changes(monkeypatch, net):
    d1, d2 = date(2026, 11, 25), date(2026, 11, 26)
    _, msg = run_once(monkeypatch, net, d1, primary=False, multpl_up=True, yale="stale")
    assert "multpl.com fallback" in msg
    _, msg = run_once(monkeypatch, net, d2, primary=False, multpl_up=True, yale="fresh")
    assert msg == ("ℹ️ PEC valuation series served by multpl.com and Yale ie_data.xls fallback "
                   "— shillerdata.com unavailable since 2026-11-25\n")
    assert health()["notified"]["fallback"] == "multpl+yale"
    _, msg = run_once(monkeypatch, net, d2, primary=False, multpl_up=False, yale="fresh")
    assert msg == ("ℹ️ PEC valuation series served by Yale ie_data.xls fallback "
                   "— shillerdata.com unavailable since 2026-11-25\n")


def test_failing_day_only_when_nothing_served(monkeypatch, net):
    run_once(monkeypatch, net, date(2026, 11, 25), primary=False, multpl_up=True, yale="stale")   # ratio served
    assert health()["fail_days"] == 0
    run_once(monkeypatch, net, date(2026, 11, 26), primary=False, multpl_up=False, yale="stale")  # all failed
    assert health()["fail_days"] == 1
    _, msg = run_once(monkeypatch, net, date(2026, 11, 27), primary=False, multpl_up=False, yale="stale")
    assert health()["fail_days"] == 2
    assert "Valuation data fetch (shillerdata.com, multpl.com and Yale) has failed on 2 different days" in msg
    run_once(monkeypatch, net, date(2026, 11, 28), primary=False, multpl_up=True, yale="stale")
    assert health()["fail_days"] == 0 and health()["last_fail_date"] is None


def test_all_failed_run_keeps_previous_source(monkeypatch, net):
    run_once(monkeypatch, net, date(2026, 11, 25), primary=True, multpl_up=False, yale="stale")
    run_once(monkeypatch, net, date(2026, 11, 26), primary=False, multpl_up=False, yale="stale")
    h = health()
    assert h["source"] == "shiller" and h["fail_days"] == 1 and h["primary_down_since"] == "2026-11-26"


def test_stale_e_notice_skipped_while_on_fallback(monkeypatch, net):
    shiller._write_health({"fail_days": 0, "last_fail_date": None, "last_e": "2026-03", "source": "shiller",
                           "primary_down_since": None, "notified": {}})
    _, msg = run_once(monkeypatch, net, TODAY, primary=False, multpl_up=True, yale="stale", series=["pe"])
    assert msg == FALLBACK_MULTPL.format(since="2026-11-25")              # no stale-E message
    assert "stale" not in health()["notified"]


def test_stale_e_notice_on_primary_still_works(monkeypatch, net):
    shiller._write_health({"fail_days": 0, "last_fail_date": None, "last_e": "2026-03", "source": "shiller",
                           "primary_down_since": None, "notified": {}})
    assert "latest published earnings month is 8 months old (2026-03)" in shiller.pending_notice(TODAY)


def test_no_shiller_fetch_this_run_is_noop(monkeypatch, net):
    shiller._write_health({"fail_days": 0, "last_fail_date": None, "last_e": "2026-09", "source": "multpl",
                           "primary_down_since": "2026-11-20", "notified": {"fallback": "multpl"}})
    before = shiller._HEALTH_PATH.read_text()
    assert shiller.pending_notice(TODAY) is None
    assert shiller._HEALTH_PATH.read_text() == before


def test_primary_derived_range_failure_does_not_fall_over(monkeypatch, net):
    """Amendment D 'not changed': a loaded primary workbook whose derived value fails the range
    check fails closed - no cross-source retry on multpl or Yale."""
    primary_ok(net)
    multpl_ok(net, NOV_ROWS)
    yale_fresh(net)
    m = shiller._parse_workbook(WB)
    m.loc[pd.Period("2026-11", "M"), "CAPE"] = 80.0
    shiller._WORKBOOK_CACHE["monthly"] = m
    with pytest.raises(FetchError, match="Shiller-derived 'cape'"):
        fetch("cape")
    assert net.calls == []


def test_primary_stale_prices_do_not_fall_over(net):
    primary_ok(net)
    multpl_ok(net, NOV_ROWS)
    with pytest.raises(FetchError, match="price data stale"):
        fetch("pe", end="2027-03-01")                     # workbook prices end 2026-11; E age 6 ok
    assert net.multpl_calls() == []
