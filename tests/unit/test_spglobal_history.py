"""spglobal EPS history in tmp: seed bootstrap, idempotent month append (plan §6.1, §3).

All spglobal path constants are redirected to tmp_path by conftest.isolate_paths;
the seed is a tmp copy (or a synthetic file written here). date.today() is frozen
by patching spglobal.date with a subclass.
"""
import json
from datetime import date

import pandas as pd
import pytest

from conftest import REAL_SEED
from fetchers import spglobal

SEED_TEXT = """# header comment line 1
# header comment line 2 — must survive write-back

entries:
  - {date: "2026-08-31", value: 340.10}
  - {date: "2026-09-30", value: 345.5}
  - {date: "2026-10-30", value: 350.123}
"""


def _freeze_today(monkeypatch, d: date):
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(d.year, d.month, d.day)

    monkeypatch.setattr(spglobal, "date", FrozenDate)


@pytest.fixture
def seed():
    spglobal._SEED_YAML.write_text(SEED_TEXT, encoding="utf-8")
    return spglobal._SEED_YAML


def _history():
    return json.loads(spglobal._HISTORY_JSON.read_text(encoding="utf-8"))


def test_paths_are_isolated(tmp_path):
    for p in (spglobal._CACHE_DIR, spglobal._HISTORY_JSON, spglobal._SEED_YAML, spglobal._CACHE_JSON, spglobal._CACHE_XLSX):
        assert str(p).startswith(str(tmp_path))


def test_seed_loader_rounds_and_stringifies(seed):
    assert spglobal._seed_history_from_yaml() == [
        {"date": "2026-08-31", "value": 340.1},
        {"date": "2026-09-30", "value": 345.5},
        {"date": "2026-10-30", "value": 350.12},
    ]


def test_real_seed_has_64_entries():
    spglobal._SEED_YAML.write_text(REAL_SEED.read_text(encoding="utf-8"), encoding="utf-8")
    entries = spglobal._seed_history_from_yaml()
    assert len(entries) == 64
    assert entries[-1]["date"] == "2026-04-30"


def test_history_fetch_bootstraps_from_seed_without_writing(seed):
    df = spglobal.fetch("forward_eps_ntm", history=True)
    assert df["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-08-31", "2026-09-30", "2026-10-30"]
    assert df["value"].dtype == float
    assert not spglobal._HISTORY_JSON.exists()


def test_history_fetch_prefers_history_json_over_seed(seed):
    spglobal._save_history_entries([{"date": "2020-01-31", "value": 170.0}])
    df = spglobal.fetch("forward_eps_ntm", history=True)
    assert df["date"].dt.strftime("%Y-%m-%d").tolist() == ["2020-01-31"]


def test_history_fetch_empty_when_no_seed():
    spglobal._SEED_YAML.unlink()
    df = spglobal.fetch("forward_eps_ntm", history=True)
    assert df.empty and list(df.columns) == ["date", "value"]


def test_ensure_history_bootstraps_and_appends_current_month(monkeypatch, seed):
    _freeze_today(monkeypatch, date(2026, 11, 20))
    spglobal._ensure_history(354.844)

    h = _history()
    assert h["entry_count"] == 4
    assert h["entries"][-1] == {"date": "2026-11-30", "value": 354.84}  # last weekday of Nov 2026 (Mon)
    assert [e["date"] for e in h["entries"]] == sorted(e["date"] for e in h["entries"])

    text = seed.read_text(encoding="utf-8")
    assert text.startswith("# header comment line 1\n# header comment line 2 — must survive write-back\n")
    assert '  - {date: "2026-11-30", value: 354.84}' in text
    assert len(spglobal._seed_history_from_yaml()) == 4


def test_ensure_history_idempotent_within_month(monkeypatch, seed):
    _freeze_today(monkeypatch, date(2026, 11, 20))
    spglobal._ensure_history(354.84)
    hist_before = spglobal._HISTORY_JSON.read_text(encoding="utf-8")
    seed_before = seed.read_text(encoding="utf-8")

    _freeze_today(monkeypatch, date(2026, 11, 27))
    spglobal._ensure_history(999.99)  # different value, same month -> ignored

    assert spglobal._HISTORY_JSON.read_text(encoding="utf-8") == hist_before
    assert seed.read_text(encoding="utf-8") == seed_before
    assert _history()["entries"][-1] == {"date": "2026-11-30", "value": 354.84}


def test_ensure_history_appends_next_month(monkeypatch, seed):
    _freeze_today(monkeypatch, date(2026, 11, 20))
    spglobal._ensure_history(354.84)
    _freeze_today(monkeypatch, date(2026, 12, 2))
    spglobal._ensure_history(360.0)
    entries = _history()["entries"]
    assert [e["date"] for e in entries][-2:] == ["2026-11-30", "2026-12-31"]
    assert len(entries) == 5


def test_ensure_history_month_already_in_seed(monkeypatch, seed):
    """A seed entry in the current month blocks accumulation; history JSON is still written once."""
    _freeze_today(monkeypatch, date(2026, 10, 5))
    spglobal._ensure_history(999.0)
    entries = _history()["entries"]
    assert len(entries) == 3 and entries[-1]["value"] == 350.12
    assert seed.read_text(encoding="utf-8") == SEED_TEXT  # no write-back


@pytest.mark.parametrize(
    "y, m, expected",
    [(2026, 11, date(2026, 11, 30)), (2026, 10, date(2026, 10, 30)), (2026, 2, date(2026, 2, 27)), (2026, 5, date(2026, 5, 29))],
)
def test_last_business_day_of_month(y, m, expected):
    assert spglobal._last_business_day_of_month(y, m) == expected


def test_malformed_history_json_treated_as_empty(seed):
    spglobal._CACHE_DIR.mkdir(parents=True, exist_ok=True)
    spglobal._HISTORY_JSON.write_text("{not json", encoding="utf-8")
    assert spglobal._load_history_entries() == []
    df = spglobal.fetch("forward_eps_ntm", history=True)
    assert len(df) == 3  # falls back to seed
