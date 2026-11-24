"""market_calendar.py (Amendment B): NYSE 2026–2027 holidays, early closes, session helpers.

The holiday table is cross-checked against an independent rule-based derivation
(fixed-date holidays with NYSE weekend observance, nth-weekday holidays, Good Friday
from the Easter computus).
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

import market_calendar as mc

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
PT = ZoneInfo("America/Los_Angeles")


@pytest.fixture(autouse=True)
def fresh_warning_state(monkeypatch):
    monkeypatch.setattr(mc, "_warned_years", set())


def et(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=ET)


# ---------------------------------------------------------------------------
# Independent derivation of NYSE full-day holidays
# ---------------------------------------------------------------------------
def _easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(y, month, day)


def _nth_weekday(y, mo, weekday, n):
    d = date(y, mo, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(y, mo, weekday):
    d = date(y, mo + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d):
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def nyse_holidays(y):
    out = {
        _nth_weekday(y, 1, 0, 3),          # MLK
        _nth_weekday(y, 2, 0, 3),          # Presidents
        _easter(y) - timedelta(days=2),    # Good Friday
        _last_weekday(y, 5, 0),            # Memorial
        _observed(date(y, 6, 19)),         # Juneteenth
        _observed(date(y, 7, 4)),          # Independence
        _nth_weekday(y, 9, 0, 1),          # Labor
        _nth_weekday(y, 11, 3, 4),         # Thanksgiving
        _observed(date(y, 12, 25)),        # Christmas
    }
    nyd = date(y, 1, 1)
    if nyd.weekday() != 5:                 # NYSE does not observe a Saturday New Year on Dec 31
        out.add(_observed(nyd))
    return out


HOLIDAYS_2026 = [date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
                 date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)]
HOLIDAYS_2027 = [date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31),
                 date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24)]
EARLY = [date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26)]


def test_holiday_table_exact():
    assert mc.HOLIDAYS == frozenset(HOLIDAYS_2026 + HOLIDAYS_2027)
    assert mc.EARLY_CLOSES == frozenset(EARLY)
    assert mc.CALENDAR_YEARS == (2026, 2027)


@pytest.mark.parametrize("year", [2026, 2027])
def test_holiday_table_matches_rule_derivation(year):
    assert {d for d in mc.HOLIDAYS if d.year == year} == nyse_holidays(year)


@pytest.mark.parametrize("d", HOLIDAYS_2026 + HOLIDAYS_2027)
def test_holidays_are_not_trading_days(d):
    assert d.weekday() < 5
    assert mc.is_trading_day(d) is False
    assert mc.market_is_open(datetime.combine(d, time(11, 0), tzinfo=ET)) is False


@pytest.mark.parametrize("d", EARLY)
def test_early_closes(d):
    assert mc.is_trading_day(d)
    assert mc.close_time_et(d) == time(13, 0)
    assert mc.close_datetime_et(d) == datetime.combine(d, time(13, 0), tzinfo=ET)


@pytest.mark.parametrize(
    "d",
    [date(2026, 11, 25), date(2026, 11, 30), date(2026, 7, 2), date(2027, 12, 23), date(2026, 12, 31),
     date(2026, 10, 12), date(2026, 11, 11)],   # incl. bond-market-only holidays (Columbus, Veterans)
)
def test_regular_close_days(d):
    assert mc.is_trading_day(d)
    assert mc.close_time_et(d) == time(16, 0)


@pytest.mark.parametrize("d", [date(2026, 11, 21), date(2026, 11, 22), date(2027, 1, 2), date(2027, 1, 3)])
def test_weekends_are_not_trading_days(d):
    assert mc.is_trading_day(d) is False


def test_trading_day_count_2026():
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(365)]
    assert sum(mc.is_trading_day(d) for d in days) == 251


# ---------------------------------------------------------------------------
# market_is_open
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "now, expected",
    [
        (et(2026, 11, 23, 9, 29, 59), False),
        (et(2026, 11, 23, 9, 30), True),
        (et(2026, 11, 23, 15, 59, 59), True),
        (et(2026, 11, 23, 16, 0), False),
        (et(2026, 11, 27, 9, 30), True),          # early-close day opens normally
        (et(2026, 11, 27, 12, 59, 59), True),
        (et(2026, 11, 27, 13, 0), False),         # 13:00 early close
        (et(2026, 11, 27, 15, 0), False),
        (et(2026, 11, 26, 11, 0), False),         # Thanksgiving
        (et(2026, 11, 28, 11, 0), False),         # Saturday
        (et(2026, 7, 15, 9, 30), True),           # EDT
        (datetime(2026, 11, 23, 14, 30, tzinfo=UTC), True),    # 09:30 EST
        (datetime(2026, 11, 23, 14, 29, tzinfo=UTC), False),
        (datetime(2026, 7, 15, 13, 30, tzinfo=UTC), True),     # 09:30 EDT
        (datetime(2026, 11, 27, 10, 0, tzinfo=PT), False),     # 13:00 ET early close
        (datetime(2026, 11, 27, 9, 59, tzinfo=PT), True),
        # 00:30 UTC Tue = 19:30 ET Mon -> closed
        (datetime(2026, 11, 24, 0, 30, tzinfo=UTC), False),
    ],
)
def test_market_is_open(now, expected):
    assert mc.market_is_open(now) is expected


# ---------------------------------------------------------------------------
# last_completed_trading_day
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "now, expected",
    [
        (et(2026, 11, 23, 15, 59), date(2026, 11, 20)),     # Mon pre-close -> Fri
        (et(2026, 11, 23, 16, 0), date(2026, 11, 23)),      # at close
        (et(2026, 11, 25, 20, 0), date(2026, 11, 25)),
        (et(2026, 11, 26, 12, 0), date(2026, 11, 25)),      # Thanksgiving -> Wed
        (et(2026, 11, 27, 12, 59), date(2026, 11, 25)),     # early close day, before 13:00 -> Wed
        (et(2026, 11, 27, 13, 0), date(2026, 11, 27)),      # after 13:00 early close
        (et(2026, 11, 28, 12, 0), date(2026, 11, 27)),      # Sat
        (et(2026, 11, 30, 9, 0), date(2026, 11, 27)),       # Mon pre-open
        (et(2026, 4, 6, 9, 0), date(2026, 4, 2)),           # after Good Friday
        (et(2026, 7, 6, 9, 0), date(2026, 7, 2)),           # after Jul 3 observed
        (et(2026, 12, 28, 9, 0), date(2026, 12, 24)),       # after Christmas
        (et(2027, 1, 4, 9, 0), date(2026, 12, 31)),         # after New Year 2027, crosses year
        (et(2027, 1, 19, 9, 0), date(2027, 1, 15)),         # after MLK 2027 (Mon Jan 18)
        (datetime(2026, 11, 24, 0, 30, tzinfo=UTC), date(2026, 11, 23)),   # ET date, not UTC date
        (datetime(2026, 11, 27, 10, 5, tzinfo=PT), date(2026, 11, 27)),
    ],
)
def test_last_completed_trading_day(now, expected):
    assert mc.last_completed_trading_day(now) == expected


# ---------------------------------------------------------------------------
# trading_days_before
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "d, n, expected",
    [
        (date(2026, 11, 24), 1, date(2026, 11, 23)),
        (date(2026, 11, 23), 1, date(2026, 11, 20)),    # across a weekend
        (date(2026, 11, 27), 1, date(2026, 11, 25)),    # across Thanksgiving
        (date(2026, 11, 30), 1, date(2026, 11, 27)),
        (date(2026, 11, 30), 2, date(2026, 11, 25)),
        (date(2026, 11, 30), 4, date(2026, 11, 23)),
        (date(2026, 11, 23), 0, date(2026, 11, 23)),
        (date(2026, 11, 28), 0, date(2026, 11, 28)),    # n=0 returns d unchanged, even a Saturday
        (date(2026, 11, 28), 1, date(2026, 11, 27)),
        (date(2026, 4, 6), 1, date(2026, 4, 2)),        # Good Friday
        (date(2027, 1, 4), 1, date(2026, 12, 31)),
    ],
)
def test_trading_days_before(d, n, expected):
    assert mc.trading_days_before(d, n) == expected


def test_trading_days_before_negative():
    with pytest.raises(ValueError):
        mc.trading_days_before(date(2026, 11, 23), -1)


# ---------------------------------------------------------------------------
# Fallback outside 2026–2027
# ---------------------------------------------------------------------------
def test_fallback_outside_table_warns_once_per_year():
    with pytest.warns(UserWarning, match="2028"):
        assert mc.is_trading_day(date(2028, 7, 4)) is True     # real holiday, but no data -> weekday rule
    with pytest.warns(UserWarning, match="2025"):
        assert mc.is_trading_day(date(2025, 12, 25)) is True
    assert mc.is_trading_day(date(2028, 7, 8)) is False         # Saturday still excluded
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        mc.is_trading_day(date(2028, 7, 5))                     # already warned for 2028
        mc.is_trading_day(date(2026, 7, 5))                     # in-table year never warns


def test_fallback_regular_close_and_session():
    with pytest.warns(UserWarning):
        assert mc.market_is_open(et(2028, 3, 1, 10, 0)) is True
    assert mc.close_time_et(date(2028, 11, 24)) == time(16, 0)


def test_last_completed_trading_day_walks_back_into_fallback_year():
    """Fri 2026-01-02 pre-close walks back over the Jan 1 holiday into 2025 (fallback)."""
    with pytest.warns(UserWarning, match="2025"):
        assert mc.last_completed_trading_day(et(2026, 1, 2, 10, 0)) == date(2025, 12, 31)
