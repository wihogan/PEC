"""render() (plan §6.1, F12/N11).

Template facts pinned here (PECtemplate.html):
  * `__DASHBOARD_DATA__` occurs twice: once in the HTML comment above the data tag
    ("refresh_v2.py replaces __DASHBOARD_DATA__ with raw JSON.") and once as the body of
    <script id="pec-data">. str.replace fills BOTH, so the JSON is embedded twice (F12).
  * `__GENERATED_AT__` occurs once (the #generated-at span).
After render no placeholder text remains anywhere in PEC.html.
"""
import json
import re
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import refresh_v2
from conftest import FIXTURES, TEMPLATE

SCRIPT_RE = re.compile(r'<script id="pec-data" type="application/json">(.*?)</script>', re.DOTALL)
COMMENT_RE = re.compile(r"<!-- ━━ DATA INJECTION.*?-->", re.DOTALL)

SAMPLE = {
    "metadata": {"last_refreshed": "2026-11-20T14:17:00", "statuses": {"a": {"status": "ok"}}},
    "series": {"sp500": {"dates": ["2026-11-20"], "values": [6900.1234]}},
    "valuation_inputs": {"fwdpe_delta_string": "-2% vs 5yr avg (19.9×)", "erp": 3.5, "ddm_fair_value": None},
}


@pytest.fixture
def app_with_template():
    shutil.copy(TEMPLATE, refresh_v2.HERE / "PECtemplate.html")
    return refresh_v2.HERE


def test_template_placeholder_inventory():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert html.count("__DASHBOARD_DATA__") == 2
    assert html.count("__GENERATED_AT__") == 1
    assert SCRIPT_RE.search(html).group(1) == "__DASHBOARD_DATA__"
    assert "__DASHBOARD_DATA__" in COMMENT_RE.search(html).group(0)


def test_render_replaces_placeholders_and_round_trips(app_with_template, la_tz, freeze):
    freeze(datetime(2026, 11, 20, 22, 17, 30, tzinfo=ZoneInfo("UTC")))
    refresh_v2.render(SAMPLE)
    out = (app_with_template / "PEC.html").read_text(encoding="utf-8")

    assert "__DASHBOARD_DATA__" not in out
    assert "__GENERATED_AT__" not in out
    assert json.loads(SCRIPT_RE.search(out).group(1)) == SAMPLE
    # PT wall clock, no zone label (N12)
    assert '<span class="dash-ts" id="generated-at">2026-11-20 14:17</span>' in out


def test_render_embeds_json_twice_comment_and_script(app_with_template):
    refresh_v2.render(SAMPLE)
    out = (app_with_template / "PEC.html").read_text(encoding="utf-8")
    raw = json.dumps(SAMPLE, separators=(",", ":"), ensure_ascii=False)
    assert out.count(raw) == 2                       # F12: double embed
    assert raw in COMMENT_RE.search(out).group(0)    # one copy inside the HTML comment
    assert len(SCRIPT_RE.findall(out)) == 1
    assert "×" in out                               # ensure_ascii=False, written as UTF-8


def test_render_baseline_round_trips(app_with_template):
    base = json.loads((FIXTURES / "baseline_data_v2.json").read_text(encoding="utf-8"))
    refresh_v2.render(base)
    out_path = app_with_template / "PEC.html"
    out = out_path.read_text(encoding="utf-8")
    assert json.loads(SCRIPT_RE.search(out).group(1)) == base
    # healthcheck floor 1.5 MB (plan §5 rule 3) is met by a real-sized payload
    assert out_path.stat().st_size > 1_500_000


def test_render_without_template_is_a_noop(tmp_path):
    refresh_v2.render(SAMPLE)
    assert not (refresh_v2.HERE / "PEC.html").exists()
