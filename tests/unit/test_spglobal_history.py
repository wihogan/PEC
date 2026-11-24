"""spglobal EPS history in tmp: seed bootstrap, idempotent month append (plan §6.1, §3).

All spglobal path constants are redirected to tmp_path by conftest.isolate_paths;
the seed is a tmp copy (or a synthetic file written here). date.today() is frozen
by patching spglobal.date with a subclass.
"""
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

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


BACKFILL = {  # docs/eps-backfill.md (FactSet, price / fwd 12M P/E)
    "2026-05-31": 356.78, "2026-06-30": 366.04, "2026-07-31": 379.47,
    "2026-08-31": 394.44, "2026-09-30": 401.26, "2026-10-31": 412.86,
}


def test_real_seed_has_backfill():
    spglobal._SEED_YAML.write_text(REAL_SEED.read_text(encoding="utf-8"), encoding="utf-8")
    entries = spglobal._seed_history_from_yaml()
    assert len(entries) >= 70
    by_date = {e["date"]: e["value"] for e in entries}
    for d, v in BACKFILL.items():
        assert by_date.get(d) == v, d
    dates = [e["date"] for e in entries]
    assert dates == sorted(dates) and len(set(d[:7] for d in dates)) == len(dates)


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


# ---------------------------------------------------------------------------
# record_month_end (forward-EPS automation; m11 dating rules)
# ---------------------------------------------------------------------------
def _entries():
    return {e["date"]: e["value"] for e in _history()["entries"]}


T = date(2026, 11, 23)   # "today" (PT) for these tests


def test_record_month_end_bootstraps_from_seed_and_adds_in_progress_month(seed):
    assert not spglobal._HISTORY_JSON.exists()
    assert spglobal.record_month_end(date(2026, 11, 20), 419.644, today=T) is True
    # ended month 2026-10 re-dated from the seed's 10-30 to its month-end; Nov dated as_of
    assert _entries() == {"2026-08-31": 340.1, "2026-09-30": 345.5, "2026-10-31": 350.12, "2026-11-20": 419.64}
    assert seed.read_text(encoding="utf-8") == SEED_TEXT        # seed never rewritten


def test_record_month_end_in_progress_month_never_in_future(seed):
    spglobal.record_month_end(date(2026, 11, 30), 419.64, today=T)     # as_of later than today, same month
    assert "2026-11-23" in _entries() and "2026-11-30" not in _entries()


def test_record_month_end_existing_month_updates_value_keeps_date(seed):
    spglobal.record_month_end(date(2026, 11, 20), 419.64, today=T)
    assert spglobal.record_month_end(date(2026, 11, 27), 421.5, today=date(2026, 11, 27)) is True
    e = _entries()
    assert e["2026-11-20"] == 421.5 and len(e) == 4             # date not advanced within the month


def test_record_month_end_redates_after_month_end(seed):
    spglobal.record_month_end(date(2026, 11, 20), 419.64, today=T)
    # first call in December: November re-dated to its month-end, December dated today
    assert spglobal.record_month_end(date(2026, 12, 3), 425.0, today=date(2026, 12, 3)) is True
    e = _entries()
    assert e["2026-11-30"] == 419.64 and "2026-11-20" not in e
    assert e["2026-12-03"] == 425.0


def test_record_month_end_not_redated_on_last_day_of_month(seed):
    spglobal.record_month_end(date(2026, 11, 20), 419.64, today=T)
    spglobal.record_month_end(date(2026, 11, 20), 419.64, today=date(2026, 11, 30))
    assert "2026-11-20" in _entries()           # month ends at the end of the 30th, not before


def test_record_month_end_same_value_is_noop(seed):
    spglobal.record_month_end(date(2026, 11, 20), 419.64, today=T)
    before = spglobal._HISTORY_JSON.read_text(encoding="utf-8")
    assert spglobal.record_month_end(date(2026, 11, 27), 419.6400001, today=date(2026, 11, 27)) is False
    assert spglobal._HISTORY_JSON.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "as_of, today, key",
    [
        (date(2026, 10, 30), T, "2026-10-31"),                     # ended month -> calendar month-end
        (date(2027, 2, 26), date(2027, 3, 1), "2027-02-28"),
        (date(2028, 2, 25), date(2028, 3, 1), "2028-02-29"),
        (date(2026, 11, 20), T, "2026-11-20"),                     # in progress -> as_of
        (date(2026, 11, 30), date(2026, 11, 30), "2026-11-30"),    # last day, as_of == today
    ],
)
def test_record_month_end_entry_date(as_of, today, key):
    spglobal._SEED_YAML.write_text("entries: []\n", encoding="utf-8")
    spglobal.record_month_end(as_of, 400.0, today=today)
    assert list(_entries()) == [key]


def test_record_month_end_default_today_is_pacific(monkeypatch):
    class FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 12, 1, 7, 30, tzinfo=ZoneInfo("UTC")).astimezone(tz)   # Nov 30 PT
    monkeypatch.setattr(spglobal, "datetime", FrozenDT)
    spglobal._SEED_YAML.write_text("entries: []\n", encoding="utf-8")
    spglobal.record_month_end(date(2026, 11, 30), 400.0)
    assert list(_entries()) == ["2026-11-30"]
    spglobal.record_month_end(date(2026, 11, 20), 401.0)
    assert _entries() == {"2026-11-30": 401.0}


def test_record_month_end_future_month_as_of_does_not_duplicate_current_month(seed):
    spglobal.record_month_end(date(2026, 11, 20), 419.64, today=date(2026, 11, 30))
    spglobal.record_month_end(date(2026, 12, 2), 425.0, today=date(2026, 11, 30))
    months = [d[:7] for d in _entries()]
    assert len(months) == len(set(months))


def test_record_month_end_merges_new_seed_rows(seed):
    spglobal._save_history_entries([{"date": "2026-08-31", "value": 340.1}, {"date": "2026-11-20", "value": 419.64}])
    assert spglobal.record_month_end(date(2026, 11, 20), 419.64, today=T) is True    # value unchanged, merge saved (D3)
    e = _entries()
    assert list(e) == ["2026-08-31", "2026-09-30", "2026-10-31", "2026-11-20"]
    assert spglobal.record_month_end(date(2026, 11, 20), 419.64, today=T) is False   # now a no-op


def test_record_month_end_cache_wins_over_seed_for_same_month(seed):
    spglobal._save_history_entries([{"date": "2026-09-30", "value": 999.0}])
    spglobal.record_month_end(date(2026, 11, 20), 419.64, today=T)
    assert _entries()["2026-09-30"] == 999.0


def test_record_month_end_visible_in_history_fetch(seed):
    spglobal.record_month_end(date(2026, 11, 20), 419.64, today=T)
    df = spglobal.fetch("forward_eps_ntm", history=True)
    assert df["date"].dt.strftime("%Y-%m-%d").tolist()[-1] == "2026-11-20"
    assert df["value"].iloc[-1] == 419.64


def test_record_month_end_never_raises(seed):
    assert spglobal.record_month_end(date(2026, 11, 20), "not-a-number", today=T) is False
    assert spglobal.record_month_end(None, 419.64, today=T) is False


def test_record_month_end_with_real_seed_backfill():
    spglobal._SEED_YAML.write_text(REAL_SEED.read_text(encoding="utf-8"), encoding="utf-8")
    spglobal.record_month_end(date(2026, 11, 20), 419.64, today=T)
    e = _entries()
    for d, v in BACKFILL.items():
        assert e[d] == v
    nov = [d for d in e if d.startswith("2026-11")]
    assert len(nov) == 1 and e[nov[0]] == 419.64
    months = [d[:7] for d in e]
    assert len(months) == len(set(months))
