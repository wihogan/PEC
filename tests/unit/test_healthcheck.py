"""healthcheck.py (plan §5, §6.1).

Pass fixture: tests/fixtures/baseline_data_v2.json with tips_5yr made fresh (one new
observation on 2026-11-19, status ok, last_fetched == last_refreshed), rendered once per
session to PEC.html via refresh_v2.render. Mutation cases re-render so rule 3 stays
consistent unless the test targets rule 3. Clock: 2026-11-20 15:00 PT.
"""
import copy
import json
import re
import shutil
from datetime import date, datetime, timedelta

import pytest

import healthcheck
import refresh_v2
import re
import tempfile
from pathlib import Path

from conftest import FIXTURES, REAL_CONFIG as _LIVE_CONFIG, TEMPLATE

NOW = datetime.fromisoformat("2026-11-20T15:00:00-08:00")
TODAY = NOW.date()
RAW = json.loads((FIXTURES / "baseline_data_v2.json").read_text(encoding="utf-8"))


def _config_matching_baseline() -> Path:
    """The live config with its EPS manual_override pinned to the baseline fixture's value.

    The owner updates the override in config/series_v2.yaml; these tests must not depend on it.
    """
    eps = RAW["valuation_inputs"]["forward_eps_ntm"]
    text = _LIVE_CONFIG.read_text(encoding="utf-8")
    block = re.search(r"manual_override:\n(?:[ \t]+.*\n){1,4}", text)
    assert block, "manual_override block not found in config/series_v2.yaml"
    seg = block.group(0)
    seg = re.sub(r"(value:\s*)[0-9.]+", lambda m: f"{m.group(1)}{eps['value']}", seg, count=1)
    seg = re.sub(r'(effective_date:\s*)"?[0-9-]+"?', lambda m: f'{m.group(1)}"{eps["effective_date"]}"', seg, count=1)
    out = Path(tempfile.mkdtemp(prefix="pec_cfg_")) / "series_v2.yaml"
    out.write_text(text[:block.start()] + seg + text[block.end():], encoding="utf-8")
    return out


REAL_CONFIG = _config_matching_baseline()
CFG = refresh_v2._load_config(REAL_CONFIG)
FREQ = {e["id"]: e["refresh_frequency"] for e in CFG["series"]}
SOURCE = {e["id"]: e["source"] for e in CFG["series"]}


def _make_pass():
    d = copy.deepcopy(RAW)
    t = d["series"]["tips_5yr"]
    t["dates"].append("2026-11-19")
    t["values"].append(1.25)
    d["metadata"]["statuses"]["tips_5yr"] = {"status": "ok", "last_fetched": d["metadata"]["last_refreshed"]}
    return d


PASS = _make_pass()


def _render(data, here):
    """Write data_v2.json + PEC.html into `here` using refresh_v2.render."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(refresh_v2, "HERE", here)
        if not (here / "PECtemplate.html").exists():
            shutil.copy(TEMPLATE, here / "PECtemplate.html")
        refresh_v2.render(data)
    (here / "data_v2.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return here / "data_v2.json", here / "PEC.html"


@pytest.fixture(scope="session")
def pass_files(tmp_path_factory):
    return _render(PASS, tmp_path_factory.mktemp("hc_pass"))


@pytest.fixture(scope="session")
def raw_files(tmp_path_factory):
    return _render(RAW, tmp_path_factory.mktemp("hc_raw"))


@pytest.fixture
def mutated(tmp_path):
    """mutated(fn) -> (data_path, html_path) for a deep copy of PASS changed by fn."""

    def _m(fn):
        d = copy.deepcopy(PASS)
        fn(d)
        here = tmp_path / "hc"
        here.mkdir(exist_ok=True)
        return _render(d, here)

    return _m


def check(paths, config=REAL_CONFIG, now=NOW):
    data, html = paths
    return healthcheck.run_checks(data, html, config, now)


def rules(report):
    return sorted({f.split(":")[0] for f in report["failures"]})


def set_last_obs(d, sid, day: date, value=1.0):
    s = d["series"][sid]
    iso = day.isoformat()
    keep = [i for i, x in enumerate(s["dates"]) if x < iso]
    s["dates"] = [s["dates"][i] for i in keep] + [iso]
    if "tenors" in s:
        s["tenors"] = {t: [v[i] for i in keep] + [value] for t, v in s["tenors"].items()}
    else:
        s["values"] = [s["values"][i] for i in keep] + [value]


def days_ago(n):
    return TODAY - timedelta(days=n)


def set_status(d, sid, status, attempted=True):
    lr = d["metadata"]["last_refreshed"]
    st = {"status": status}
    if status == "error":
        st["error"] = f"{sid} boom"
        st["last_fetched"] = "2026-11-19T14:17:00"
    else:
        st["last_fetched"] = lr if attempted else "2026-11-02T06:40:35"
    d["metadata"]["statuses"][sid] = st


# ---------------------------------------------------------------------------
# H0 pass / raw baseline
# ---------------------------------------------------------------------------
def test_pass_fixture_passes(pass_files):
    r = check(pass_files)
    assert r["failures"] == []
    assert r["ok"] is True
    assert set(r["series"]) == set(FREQ) | {"fwd_pe_history"}
    # baseline-inherent warnings only
    assert any(w.startswith("fwd_pe_history last entry is 204 days old") for w in r["warnings"])
    assert any(w.startswith("forward_eps_ntm effective_date 2026-05-07 is 197 days old") for w in r["warnings"])


def test_pass_fixture_html_meets_size_floor(pass_files):
    assert pass_files[1].stat().st_size >= 1_500_000


def test_raw_baseline_fails_on_tips_5yr_only(raw_files):
    r = check(raw_files)
    assert r["failures"] == ["rule 5: tips_5yr last observation 2026-11-03 is 17 days old (limit 7)"]
    assert any(w.startswith("tips_5yr errored") for w in r["warnings"])


# ---------------------------------------------------------------------------
# Rule 1
# ---------------------------------------------------------------------------
def test_rule1_missing_data(pass_files, tmp_path):
    r = check((tmp_path / "nope.json", pass_files[1]))
    assert rules(r) == ["rule 1"]


@pytest.mark.parametrize("text", ["{truncated", "[]", '{"metadata": {}}', ""])
def test_rule1_invalid_data(pass_files, tmp_path, text):
    p = tmp_path / "data_v2.json"
    p.write_text(text)
    r = check((p, pass_files[1]))
    assert "rule 1" in rules(r)
    assert r["ok"] is False


# ---------------------------------------------------------------------------
# Rule 2
# ---------------------------------------------------------------------------
def test_rule2_missing_id(mutated):
    r = check(mutated(lambda d: d["series"].pop("nasdaq")))
    assert "rule 2: missing series ids: nasdaq" in r["failures"]


def test_rule2_unknown_id(mutated):
    r = check(mutated(lambda d: d["series"].__setitem__("bogus", {"dates": [], "values": []})))
    assert rules(r) == ["rule 2"]
    assert "unknown series ids: bogus" in r["failures"][0]


def test_rule2_fwd_pe_history_optional(mutated):
    r = check(mutated(lambda d: d["series"].pop("fwd_pe_history")))
    assert r["failures"] == []


# ---------------------------------------------------------------------------
# Rule 3
# ---------------------------------------------------------------------------
SCRIPT_RE = re.compile(r'(<script id="pec-data" type="application/json">)(.*?)(</script>)', re.DOTALL)


def _html_variant(pass_files, tmp_path, fn):
    html = pass_files[1].read_text(encoding="utf-8")
    p = tmp_path / "PEC.html"
    p.write_text(fn(html), encoding="utf-8")
    return pass_files[0], p


def test_rule3_size_floor(pass_files, monkeypatch):
    size = pass_files[1].stat().st_size
    monkeypatch.setitem(healthcheck.THRESHOLDS, "html_min_bytes", size + 1)
    r = check(pass_files)
    assert rules(r) == ["rule 3"] and "byte floor" in r["failures"][0]
    monkeypatch.setitem(healthcheck.THRESHOLDS, "html_min_bytes", size)
    assert check(pass_files)["failures"] == []


def test_rule3_default_floor_is_1_5_mb():
    assert healthcheck.THRESHOLDS["html_min_bytes"] == 1_500_000


def test_rule3_small_real_render_fails(tmp_path):
    tiny = {"metadata": {}, "series": {}, "valuation_inputs": {}}
    r = check(_render(tiny, tmp_path))
    assert any("byte floor" in f for f in r["failures"])


def test_rule3_placeholder_in_script(pass_files, tmp_path):
    paths = _html_variant(pass_files, tmp_path, lambda h: SCRIPT_RE.sub(r"\1__DASHBOARD_DATA__\3", h))
    r = check(paths)
    assert rules(r) == ["rule 3"]
    assert any("placeholder __DASHBOARD_DATA__" in f for f in r["failures"])
    assert any("not valid JSON" in f for f in r["failures"])


def test_rule3_generated_at_left(pass_files, tmp_path):
    paths = _html_variant(
        pass_files, tmp_path,
        lambda h: re.sub(r'(id="generated-at">)[^<]*(<)', r"\1__GENERATED_AT__\2", h),
    )
    r = check(paths)
    assert r["failures"] == ["rule 3: placeholder __GENERATED_AT__ still present in PEC.html"]


def test_rule3_placeholder_only_inside_comment_is_ok(pass_files, tmp_path):
    raw = json.dumps(PASS, separators=(",", ":"), ensure_ascii=False)

    def restore_comment(h):
        i = h.index(raw)                                  # first copy = HTML comment (F12)
        assert h.rfind("<!--", 0, i) > h.rfind("-->", 0, i)
        return h[:i] + "__DASHBOARD_DATA__" + h[i + len(raw):]

    paths = _html_variant(pass_files, tmp_path, restore_comment)
    r = check(paths)
    assert r["failures"] == []


def test_rule3_script_json_differs(pass_files, tmp_path):
    d = copy.deepcopy(PASS)
    d["series"]["sp500"]["values"][-1] += 1.0
    p = tmp_path / "data_v2.json"
    p.write_text(json.dumps(d))
    r = check((p, pass_files[1]))
    assert r["failures"] == ["rule 3: pec-data script JSON differs from data_v2.json"]


def test_rule3_script_not_json(pass_files, tmp_path):
    paths = _html_variant(pass_files, tmp_path, lambda h: SCRIPT_RE.sub(r"\1{oops\3", h))
    r = check(paths)
    assert rules(r) == ["rule 3"] and "not valid JSON" in r["failures"][0]


def test_rule3_script_tag_missing(pass_files, tmp_path):
    paths = _html_variant(pass_files, tmp_path, lambda h: h.replace('id="pec-data"', 'id="other"'))
    r = check(paths)
    assert any("block not found" in f for f in r["failures"])


def test_rule3_html_missing(pass_files, tmp_path):
    r = check((pass_files[0], tmp_path / "PEC.html"))
    assert rules(r) == ["rule 3"] and "missing" in r["failures"][0]


# ---------------------------------------------------------------------------
# Rule 4
# ---------------------------------------------------------------------------
MD_MIXED = ["dow_jones", "nasdaq", "fed_funds", "gold_spot"]   # yahoo intraday + fred daily_release


def test_rule4_fixture_assumptions():
    for sid in MD_MIXED:
        assert FREQ[sid] in healthcheck.DAILY_FREQUENCIES
    assert {SOURCE[s] for s in MD_MIXED} == {"yahoo", "fred"}


def test_rule4_four_market_daily_failures(mutated):
    def fn(d):
        for sid in MD_MIXED:
            set_status(d, sid, "error")
    r = check(mutated(fn))
    assert rules(r) == ["rule 4"]
    assert "4 market_daily series failed" in r["failures"][0]


def test_rule4_three_market_daily_failures_pass(mutated):
    def fn(d):
        for sid in MD_MIXED[:3]:
            set_status(d, sid, "error")
    r = check(mutated(fn))
    assert r["failures"] == []
    assert sum(w.endswith("boom") for w in r["warnings"]) == 3


def _only_attempted(d, source, failing):
    """Mark every `source` series not attempted, except `failing`, which error."""
    for sid, src in SOURCE.items():
        if src == source:
            set_status(d, sid, "ok", attempted=False)
    for sid in failing:
        set_status(d, sid, "error")


FRED_SLOW = ["cpi_yoy", "gdp_growth", "nber_recession"]
YAHOO_3 = ["dow_jones", "nasdaq", "russell_2000"]


def test_rule4_all_attempted_fred_failed(mutated):
    r = check(mutated(lambda d: _only_attempted(d, "fred", FRED_SLOW)))
    assert r["failures"] == ["rule 4: all 3 attempted FRED series failed"]


def test_rule4_all_attempted_fred_failed_but_only_two(mutated):
    r = check(mutated(lambda d: _only_attempted(d, "fred", FRED_SLOW[:2])))
    assert r["failures"] == []


def test_rule4_fred_one_success_among_attempted(mutated):
    def fn(d):
        _only_attempted(d, "fred", FRED_SLOW)
        set_status(d, "fed_funds", "ok", attempted=True)
    assert check(mutated(fn))["failures"] == []


def test_rule4_all_attempted_yahoo_failed(mutated):
    r = check(mutated(lambda d: _only_attempted(d, "yahoo", YAHOO_3)))
    assert r["failures"] == ["rule 4: all 3 attempted YAHOO series failed"]


def test_rule4_treasury_error_but_fresh_passes(mutated):
    r = check(mutated(lambda d: set_status(d, "yield_curve", "error")))
    assert r["failures"] == []
    assert any(w.startswith("yield_curve errored") for w in r["warnings"])


def test_rule4_all_multpl_failed_warn_only(mutated):
    ids = [s for s, src in SOURCE.items() if src == "multpl"]
    assert len(ids) == 5

    def fn(d):
        for sid in ids:
            set_status(d, sid, "error")
    r = check(mutated(fn))
    assert r["failures"] == []
    assert sum("errored" in w for w in r["warnings"]) == 5


def test_rule4_attempted_definition(mutated):
    """ok + last_fetched == last_refreshed counts as attempted; ok + older does not."""
    def fn(d):
        _only_attempted(d, "fred", FRED_SLOW)
        set_status(d, "fed_funds", "ok", attempted=False)
    assert rules(check(mutated(fn))) == ["rule 4"]


# ---------------------------------------------------------------------------
# Rule 5
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sid, ok_age, bad_age",
    [
        ("sp500", 7, 8),            # intraday 7
        ("btc_usd", 7, 8),          # intraday (crypto)
        ("treasury_10yr", 7, 8),    # daily_release 7
        ("tips_5yr", 7, 8),
        ("eur_usd", 14, 15),        # FRED FX 14 (NB2)
        ("cad_usd", 14, 15),
        ("jpy_usd", 14, 15),
        ("wti_crude", 14, 15),
        ("yield_curve", 7, 8),
    ],
)
def test_rule5_thresholds(mutated, sid, ok_age, bad_age):
    r_ok = check(mutated(lambda d: set_last_obs(d, sid, days_ago(ok_age))))
    assert r_ok["failures"] == [], r_ok["failures"]
    r_bad = check(mutated(lambda d: set_last_obs(d, sid, days_ago(bad_age))))
    assert r_bad["failures"] == [
        f"rule 5: {sid} last observation {days_ago(bad_age).isoformat()} is {bad_age} days old "
        f"(limit {ok_age})"
    ]


def test_rule5_eur_usd_13_days_passes(mutated):
    assert check(mutated(lambda d: set_last_obs(d, "eur_usd", days_ago(13))))["failures"] == []


def test_rule5_trailing_nulls_ignored(mutated):
    def fn(d):
        set_last_obs(d, "sp500", days_ago(10), value=6000.0)
        d["series"]["sp500"]["dates"].append(days_ago(1).isoformat())
        d["series"]["sp500"]["values"].append(None)
    r = check(mutated(fn))
    assert rules(r) == ["rule 5"] and "10 days old" in r["failures"][0]


def test_rule5_yield_curve_uses_any_non_null_tenor(mutated):
    def fn(d):
        yc = d["series"]["yield_curve"]
        yc["dates"].append(days_ago(0).isoformat())
        for t, v in yc["tenors"].items():
            v.append(4.0 if t == "10yr" else None)
    r = check(mutated(fn))
    assert r["failures"] == []
    assert r["series"]["yield_curve"]["age_days"] == 0


def test_rule5_daily_series_without_observations(mutated):
    def fn(d):
        d["series"]["sp500"]["values"] = [None] * len(d["series"]["sp500"]["values"])
    r = check(mutated(fn))
    assert r["failures"] == ["rule 5: sp500 has no observations"]


def test_rule5_slow_series_never_fail(mutated):
    def fn(d):
        set_last_obs(d, "cpi_yoy", days_ago(400))
        set_last_obs(d, "gdp_growth", days_ago(900))
        set_last_obs(d, "sp500_earnings", days_ago(400))
    r = check(mutated(fn))
    assert r["failures"] == []
    warned = {w.split()[0] for w in r["warnings"] if "days old (warn >" in w}
    assert {"cpi_yoy", "gdp_growth", "sp500_earnings"} <= warned


# ---------------------------------------------------------------------------
# Rule 6
# ---------------------------------------------------------------------------
def test_rule6_live_source_fails(mutated):
    def fn(d):
        d["valuation_inputs"]["forward_eps_ntm"]["source"] = "live"
    r = check(mutated(fn))
    assert r["failures"] == ["rule 6: forward_eps_ntm source is 'live', expected 'override'"]


def test_rule6_override_value_mismatch_fails(mutated):
    def fn(d):
        d["valuation_inputs"]["forward_eps_ntm"]["value"] = 360.0
    r = check(mutated(fn))
    assert rules(r) == ["rule 6"] and "does not match config override 354.84" in r["failures"][0]


@pytest.mark.parametrize("source", ["unavailable", None])
def test_rule6_unavailable_or_missing_fails(mutated, source):
    def fn(d):
        if source is None:
            d["valuation_inputs"].pop("forward_eps_ntm")
        else:
            d["valuation_inputs"]["forward_eps_ntm"]["source"] = source
    assert rules(check(mutated(fn))) == ["rule 6"]


def test_rule6_compares_against_given_config(pass_files, tmp_path):
    text = REAL_CONFIG.read_text(encoding="utf-8").replace("value:    354.84", "value:    360.0")
    cfg = tmp_path / "series_v2.yaml"
    cfg.write_text(text, encoding="utf-8")
    r = check(pass_files, config=cfg)
    assert rules(r) == ["rule 6"]


# ---------------------------------------------------------------------------
# Warnings (never fail)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sid, quiet_age, warn_age",
    [("cpi_yoy", 110, 111), ("nber_recession", 110, 111), ("gdp_growth", 300, 301), ("sp500_earnings", 220, 221)],
)
def test_slow_series_warn_thresholds(mutated, sid, quiet_age, warn_age):
    r = check(mutated(lambda d: set_last_obs(d, sid, days_ago(quiet_age))))
    assert not [w for w in r["warnings"] if w.startswith(f"{sid} last observation")]
    r = check(mutated(lambda d: set_last_obs(d, sid, days_ago(warn_age))))
    assert r["failures"] == []
    assert f"{sid} last observation is {warn_age} days old (warn > {quiet_age})" in r["warnings"]


def test_fwd_pe_history_and_eps_age_warn_thresholds(mutated):
    def fresh(d):
        set_last_obs(d, "fwd_pe_history", days_ago(60), value=20.0)
        d["valuation_inputs"]["forward_eps_ntm"]["effective_date"] = days_ago(45).isoformat()
    r = check(mutated(fresh))
    assert not [w for w in r["warnings"] if w.startswith(("fwd_pe_history", "forward_eps_ntm"))]

    def stale(d):
        set_last_obs(d, "fwd_pe_history", days_ago(61), value=20.0)
        d["valuation_inputs"]["forward_eps_ntm"]["effective_date"] = days_ago(46).isoformat()
    r = check(mutated(stale))
    assert r["failures"] == []
    assert "fwd_pe_history last entry is 61 days old (warn > 60)" in r["warnings"]
    assert any(w.startswith("forward_eps_ntm effective_date") and "46 days old" in w for w in r["warnings"])


def test_single_errored_series_is_warning(mutated):
    r = check(mutated(lambda d: set_status(d, "btc_usd", "error")))
    assert r["failures"] == []
    assert "btc_usd errored this run or last attempt: btc_usd boom" in r["warnings"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("now", ["2026-11-20T15:00:00", "2026-11-20T15:00:00-08:00", "2026-11-20T23:00:00+00:00"])
def test_cli_pass_writes_health_json(pass_files, tmp_path, now, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    out = tmp_path / "health.json"
    rc = healthcheck.main(["--data", str(pass_files[0]), "--html", str(pass_files[1]),
                           "--config", str(REAL_CONFIG), "--out", str(out), "--now", now])
    assert rc == 0
    h = json.loads(out.read_text())
    assert h["ok"] is True and h["failures"] == []
    assert h["run_id"] == "12345"
    assert h["last_refreshed"] == PASS["metadata"]["last_refreshed"]
    assert h["series"]["tips_5yr"] == {"last_obs": "2026-11-19", "age_days": 1, "status": "ok"}
    assert "RESULT: OK" in capsys.readouterr().out


def test_cli_fail_exit_1(raw_files, tmp_path, capsys):
    out = tmp_path / "health.json"
    rc = healthcheck.main(["--data", str(raw_files[0]), "--html", str(raw_files[1]),
                           "--config", str(REAL_CONFIG), "--out", str(out), "--now", "2026-11-20T15:00:00"])
    assert rc == 1
    h = json.loads(out.read_text())
    assert h["ok"] is False and h["run_id"] is None
    assert "RESULT: FAIL" in capsys.readouterr().out


def test_cli_missing_data_still_writes_report(pass_files, tmp_path):
    out = tmp_path / "health.json"
    rc = healthcheck.main(["--data", str(tmp_path / "none.json"), "--html", str(pass_files[1]),
                           "--config", str(REAL_CONFIG), "--out", str(out), "--now", "2026-11-20T15:00:00"])
    assert rc == 1
    assert json.loads(out.read_text())["failures"][0].startswith("rule 1")


# ---------------------------------------------------------------------------
# Amendment B mapping
# ---------------------------------------------------------------------------
def test_daily_frequencies_constant():
    assert healthcheck.DAILY_FREQUENCIES == ("intraday", "daily_release")
    assert set(healthcheck.THRESHOLDS["fail_by_frequency"]) == {"intraday", "daily_release"}


def test_rule4_counts_daily_release_and_intraday_not_after_close(mutated):
    """4 failures among intraday+daily_release fail; multpl (daily_after_close) never counts."""
    def fn(d):
        for sid in ["fed_funds", "treasury_10yr", "dow_jones", "sp500_pe", "shiller_cape"]:
            set_status(d, sid, "error")
    assert check(mutated(fn))["failures"] == []      # only 3 daily-type failures

    def fn4(d):
        fn(d)
        set_status(d, "hy_yield", "error")
    r = check(mutated(fn4))
    assert rules(r) == ["rule 4"] and "4 market_daily series failed" in r["failures"][0]


@pytest.mark.parametrize("sid", ["sp500_pe", "shiller_cape", "sp500_dividend_yield", "sp500_earnings_yield"])
def test_daily_after_close_warn_only_after_7_days(mutated, sid):
    r = check(mutated(lambda d: set_last_obs(d, sid, days_ago(7))))
    assert not [w for w in r["warnings"] if w.startswith(f"{sid} last observation")]
    r = check(mutated(lambda d: set_last_obs(d, sid, days_ago(400))))
    assert r["failures"] == []
    assert f"{sid} last observation is 400 days old (warn > 7)" in r["warnings"]


def test_sp500_earnings_keeps_220_day_warning(mutated):
    r = check(mutated(lambda d: set_last_obs(d, "sp500_earnings", days_ago(100))))
    assert not [w for w in r["warnings"] if w.startswith("sp500_earnings last observation")]


# ---------------------------------------------------------------------------
# Negative age (crypto bars dated in UTC, docs/spike.md follow-up)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sid", ["btc_usd", "eth_usd", "xrp_usd"])
def test_negative_age_passes(mutated, sid):
    tomorrow = TODAY + timedelta(days=1)
    r = check(mutated(lambda d: set_last_obs(d, sid, tomorrow, value=90000.0)))
    assert r["failures"] == []
    assert r["series"][sid]["age_days"] == -1
    assert r["series"][sid]["last_obs"] == tomorrow.isoformat()
    assert not [w for w in r["warnings"] if w.startswith(sid)]
