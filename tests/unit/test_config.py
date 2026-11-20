"""Config: series_v2.yaml loads and is internally consistent (plan §6.1)."""
from datetime import date

import pytest
import yaml

import refresh_v2
from conftest import REAL_CONFIG
from fetchers import multpl

REQUIRED_FIELDS = ("id", "source", "series_id", "label", "category", "start_date", "refresh_frequency")
FREQUENCIES = {"market_daily", "daily_once", "monthly", "quarterly"}


@pytest.fixture(scope="module")
def config():
    return refresh_v2._load_config(REAL_CONFIG)


@pytest.fixture(scope="module")
def registry():
    return refresh_v2._load_fetchers()


def test_config_loads_with_series_and_valuation_inputs(config):
    assert isinstance(config["series"], list)
    assert "forward_eps_ntm" in config["valuation_inputs"]


def test_41_unique_series_ids(config):
    ids = [e["id"] for e in config["series"]]
    assert len(ids) == 41
    assert len(set(ids)) == 41


def test_registry_keys(registry):
    assert set(registry) == {"fred", "yahoo", "treasury", "shiller", "spglobal", "multpl"}


def test_every_source_is_registered(config, registry):
    sources = {e["source"] for e in config["series"]}
    assert sources <= set(registry)
    assert sources == {"fred", "yahoo", "treasury", "multpl"}  # shiller unused (N10)


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
def test_required_fields_present_and_non_empty(config, field):
    missing = [e.get("id", "?") for e in config["series"] if not e.get(field)]
    assert missing == []


def test_refresh_frequencies_are_known(config):
    assert {e["refresh_frequency"] for e in config["series"]} <= FREQUENCIES


def test_start_dates_are_iso(config):
    for e in config["series"]:
        date.fromisoformat(str(e["start_date"]))


def test_yoy_pct_only_on_fred(config):
    for e in config["series"]:
        if e.get("yoy_pct"):
            assert e["source"] == "fred", e["id"]


def test_multpl_series_ids_supported(config):
    ids = {e["series_id"] for e in config["series"] if e["source"] == "multpl"}
    assert ids == set(multpl._SERIES)


def test_treasury_series_id(config):
    tsy = [e for e in config["series"] if e["source"] == "treasury"]
    assert [e["id"] for e in tsy] == ["yield_curve"]
    assert tsy[0]["series_id"] == "daily_treasury_yield_curve"


def test_valuation_input_override(config, registry):
    vi = config["valuation_inputs"]["forward_eps_ntm"]
    assert vi["fetcher"] in registry
    ov = vi["manual_override"]
    assert isinstance(ov["value"], float) and 200 <= ov["value"] <= 600
    date.fromisoformat(str(ov["effective_date"]))


def test_load_config_rejects_list(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump([{"id": "x"}]))
    with pytest.raises(ValueError, match="mapping"):
        refresh_v2._load_config(p)


def test_load_config_requires_series_key(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump({"valuation_inputs": {}}))
    with pytest.raises(ValueError, match="series"):
        refresh_v2._load_config(p)
