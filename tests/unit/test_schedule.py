"""_market_is_open, _last_completed_business_day, _needs_refresh (plan §6.1, F7).

Every test runs under TZ=America/Los_Angeles and TZ=UTC (the `tz` fixture).
Instants are written in ET (or explicit local wall time) and frozen through a
datetime subclass patched onto refresh_v2. `last_fetched` strings are produced
the way run() produces them: naive local isoformat of the fetch instant.

Calendar used: Fri 2026-11-20 (EST, UTC-5); Mon 2026-11-23; Wed 2026-07-15 (EDT).
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

import refresh_v2
from conftest import local_instant, local_naive

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def et(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=ET)


def statuses_for(sid, fetched_instant):
    return {sid: {"status": "ok", "last_fetched": local_naive(fetched_instant).isoformat(timespec="seconds")}}


# ---------------------------------------------------------------------------
# _market_is_open
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "now, expected",
    [
        (et(2026, 11, 20, 9, 29, 59), False),   # Fri pre-open
        (et(2026, 11, 20, 9, 30), True),        # open bell (inclusive)
        (et(2026, 11, 20, 12, 0), True),
        (et(2026, 11, 20, 15, 59, 59), True),
        (et(2026, 11, 20, 16, 0), False),       # close (exclusive)
        (et(2026, 11, 20, 20, 0), False),
        (et(2026, 11, 21, 11, 0), False),       # Saturday
        (et(2026, 11, 22, 11, 0), False),       # Sunday
        (et(2026, 11, 23, 9, 45), True),        # Monday
        (et(2026, 7, 15, 10, 0), True),         # EDT
        (et(2026, 7, 15, 16, 30), False),       # EDT post-close
        # 14:00 UTC on Fri = 09:00 ET -> closed even though it is "afternoon" in UTC
        (datetime(2026, 11, 20, 14, 0, tzinfo=UTC), False),
        # 20:59 UTC = 15:59 ET -> open
        (datetime(2026, 11, 20, 20, 59, tzinfo=UTC), True),
    ],
)
def test_market_is_open(tz, freeze, now, expected):
    freeze(now)
    assert refresh_v2._market_is_open() is expected


# ---------------------------------------------------------------------------
# _last_completed_business_day
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "now, expected",
    [
        (et(2026, 11, 20, 15, 59), date(2026, 11, 19)),   # Fri before close -> Thu
        (et(2026, 11, 20, 16, 0), date(2026, 11, 20)),    # Fri at close -> Fri
        (et(2026, 11, 20, 23, 59), date(2026, 11, 20)),
        (et(2026, 11, 21, 12, 0), date(2026, 11, 20)),    # Sat -> Fri
        (et(2026, 11, 22, 12, 0), date(2026, 11, 20)),    # Sun -> Fri
        (et(2026, 11, 23, 9, 0), date(2026, 11, 20)),     # Mon pre-open -> Fri
        (et(2026, 11, 23, 16, 30), date(2026, 11, 23)),   # Mon post-close -> Mon
        (et(2026, 12, 1, 8, 0), date(2026, 11, 30)),      # month boundary
        (et(2026, 1, 1, 10, 0), date(2025, 12, 31)),      # year boundary (Thu; no holiday calendar)
        # 00:30 UTC Mon = 19:30 ET Sun -> Fri (ET date, not UTC date)
        (datetime(2026, 11, 23, 0, 30, tzinfo=UTC), date(2026, 11, 20)),
        # 02:00 UTC Sat = 21:00 ET Fri -> Fri
        (datetime(2026, 11, 21, 2, 0, tzinfo=UTC), date(2026, 11, 20)),
    ],
)
def test_last_completed_business_day(tz, freeze, now, expected):
    freeze(now)
    assert refresh_v2._last_completed_business_day() == expected


# ---------------------------------------------------------------------------
# _needs_refresh — common
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("freq", ["market_daily", "daily_once", "monthly", "quarterly", "weekly", ""])
def test_never_fetched_always_refreshes(tz, freeze, freq):
    freeze(et(2026, 11, 21, 12, 0))
    assert refresh_v2._needs_refresh("x", freq, {}) is True
    assert refresh_v2._needs_refresh("x", freq, {"x": {"status": "error", "last_fetched": None}}) is True


@pytest.mark.parametrize("freq", ["weekly", "hourly", "bogus"])
def test_unknown_frequency_always_refreshes(tz, freeze, freq):
    now = et(2026, 11, 21, 12, 0)
    freeze(now)
    assert refresh_v2._needs_refresh("x", freq, statuses_for("x", now)) is True


# ---------------------------------------------------------------------------
# market_daily
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fetched, now, expected",
    [
        # market open -> always refresh, even if fetched a minute ago
        (et(2026, 11, 20, 11, 59), et(2026, 11, 20, 12, 0), True),
        # post-close fetch, re-run later same evening -> skip (plan 6.2-3)
        (et(2026, 11, 20, 17, 17), et(2026, 11, 20, 18, 47), False),
        # pre-close fetch on the last business day, now post-close -> refresh
        (et(2026, 11, 20, 13, 0), et(2026, 11, 20, 17, 17), True),
        # fetched exactly at the close -> current
        (et(2026, 11, 20, 16, 0), et(2026, 11, 20, 17, 0), False),
        # fetched one second before the close -> stale
        (et(2026, 11, 20, 15, 59, 59), et(2026, 11, 20, 17, 0), True),
        # fetched previous business day, now post-close -> refresh
        (et(2026, 11, 19, 17, 17), et(2026, 11, 20, 17, 17), True),
        # pre-open Friday, fetched Thu post-close -> skip
        (et(2026, 11, 19, 17, 17), et(2026, 11, 20, 8, 0), False),
        # weekend, fetched Fri post-close -> skip
        (et(2026, 11, 20, 17, 17), et(2026, 11, 21, 12, 0), False),
        (et(2026, 11, 20, 17, 17), et(2026, 11, 22, 23, 0), False),
        # weekend, fetched Fri pre-close -> refresh
        (et(2026, 11, 20, 12, 0), et(2026, 11, 21, 12, 0), True),
        # weekend, last fetch Thursday -> refresh
        (et(2026, 11, 19, 17, 17), et(2026, 11, 21, 12, 0), True),
        # Monday pre-open, fetched Fri post-close -> skip
        (et(2026, 11, 20, 17, 17), et(2026, 11, 23, 9, 0), False),
        # Monday post-close, fetched Fri post-close -> refresh
        (et(2026, 11, 20, 17, 17), et(2026, 11, 23, 17, 17), True),
        # fetch late Fri evening ET (= Sat UTC), weekend run -> skip
        (et(2026, 11, 20, 21, 0), et(2026, 11, 21, 15, 0), False),
        # fetch late Thu evening ET (= Fri UTC), Fri pre-open -> skip
        (et(2026, 11, 19, 21, 30), et(2026, 11, 20, 8, 0), False),
        # fetch late Thu evening ET (= Fri UTC), Fri post-close -> refresh
        (et(2026, 11, 19, 23, 30), et(2026, 11, 20, 17, 0), True),
        # month boundary: fetched Fri Oct 30 post-close, Sun Nov 1 -> skip
        (et(2026, 10, 30, 17, 17), et(2026, 11, 1, 12, 0), False),
        # DST (EDT) close is 16:00 EDT
        (et(2026, 7, 15, 15, 30), et(2026, 7, 15, 16, 30), True),
        (et(2026, 7, 15, 16, 5), et(2026, 7, 15, 18, 0), False),
    ],
)
def test_market_daily(tz, freeze, fetched, now, expected):
    freeze(now)
    assert refresh_v2._needs_refresh("sp500", "market_daily", statuses_for("sp500", fetched)) is expected


def test_market_daily_plan_cron_slots(la_tz, freeze):
    """Plan §4: 22:17 UTC run then 23:47 UTC retry -> retry skips series already fetched."""
    first = datetime(2026, 11, 20, 22, 17, tzinfo=UTC)
    freeze(datetime(2026, 11, 20, 23, 47, tzinfo=UTC))
    assert refresh_v2._needs_refresh("sp500", "market_daily", statuses_for("sp500", first)) is False


# ---------------------------------------------------------------------------
# daily_once (only yield_curve uses it)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fetched, now, expected",
    [
        # fetched Thu post-close, Fri post-close -> refresh
        (et(2026, 11, 19, 17, 17), et(2026, 11, 20, 17, 17), True),
        # fetched Thu post-close, Fri during session -> skip (lbd is still Thu)
        (et(2026, 11, 19, 17, 17), et(2026, 11, 20, 12, 0), False),
        # fetched Fri post-close, re-run Fri evening -> skip
        (et(2026, 11, 20, 17, 17), et(2026, 11, 20, 18, 47), False),
        # by design "at most once per business day": a pre-close fetch on the
        # last business day is NOT refreshed after the close
        (et(2026, 11, 20, 13, 0), et(2026, 11, 20, 17, 17), False),
        # weekend after Fri fetch -> skip
        (et(2026, 11, 20, 17, 17), et(2026, 11, 22, 12, 0), False),
        # Monday post-close after Fri fetch -> refresh
        (et(2026, 11, 20, 17, 17), et(2026, 11, 23, 17, 0), True),
        # Monday pre-open after Fri fetch -> skip
        (et(2026, 11, 20, 17, 17), et(2026, 11, 23, 8, 0), False),
    ],
)
def test_daily_once(tz, freeze, fetched, now, expected):
    freeze(now)
    assert refresh_v2._needs_refresh("yield_curve", "daily_once", statuses_for("yield_curve", fetched)) is expected


@pytest.mark.parametrize(
    "tz",
    [
        "America/Los_Angeles",
        pytest.param(
            "UTC",
            marks=pytest.mark.xfail(
                strict=True,
                reason=(
                    "TZ defect: daily_once compares the LOCAL date of last_fetched with the ET "
                    "last-business-day (refresh_v2.py:475-477). Under TZ=UTC a fetch after "
                    "19:00/20:00 ET is dated the next day, so the next post-close run skips it."
                ),
            ),
        ),
    ],
    indirect=True,
)
def test_daily_once_evening_fetch_then_next_close(tz, freeze):
    # Fetched Thu 21:00 ET (= Fri 02:00 UTC): has Thu's close, not Fri's.
    fetched = et(2026, 11, 19, 21, 0)
    freeze(et(2026, 11, 20, 17, 17))  # Fri post-close
    assert refresh_v2._needs_refresh("yield_curve", "daily_once", statuses_for("yield_curve", fetched)) is True


# ---------------------------------------------------------------------------
# monthly / quarterly — local calendar (same zone for last_fetched and now)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fetched, now, expected",
    [
        ((2026, 11, 1, 0, 10), (2026, 11, 20, 14, 17), False),
        ((2026, 10, 31, 23, 59), (2026, 11, 1, 0, 1), True),     # month boundary
        ((2026, 11, 30, 23, 0), (2026, 11, 30, 23, 59), False),
        ((2025, 12, 31, 23, 0), (2026, 1, 1, 0, 30), True),      # year boundary
        ((2025, 11, 20, 12, 0), (2026, 11, 20, 12, 0), True),    # same month, previous year
        ((2026, 12, 1, 9, 0), (2026, 11, 30, 9, 0), False),      # clock went backwards -> skip
    ],
)
def test_monthly(tz, freeze, fetched, now, expected):
    freeze(local_instant(*now))
    st = {"cpi_yoy": {"last_fetched": datetime(*fetched).isoformat()}}
    assert refresh_v2._needs_refresh("cpi_yoy", "monthly", st) is expected


@pytest.mark.parametrize(
    "fetched, now, expected",
    [
        ((2026, 10, 1, 0, 10), (2026, 12, 31, 23, 59), False),   # same quarter (Q4)
        ((2026, 7, 15, 12, 0), (2026, 9, 30, 23, 0), False),     # same quarter (Q3)
        ((2026, 9, 30, 23, 59), (2026, 10, 1, 0, 1), True),      # quarter boundary
        ((2026, 3, 31, 12, 0), (2026, 4, 1, 12, 0), True),
        ((2026, 6, 30, 12, 0), (2026, 7, 1, 12, 0), True),
        ((2025, 12, 31, 23, 0), (2026, 1, 1, 0, 30), True),      # year boundary
        ((2025, 11, 1, 0, 0), (2026, 11, 1, 0, 0), True),        # same quarter, previous year
        ((2026, 11, 2, 0, 0), (2026, 11, 20, 0, 0), False),      # same month
    ],
)
def test_quarterly(tz, freeze, fetched, now, expected):
    freeze(local_instant(*now))
    st = {"gdp_growth": {"last_fetched": datetime(*fetched).isoformat()}}
    assert refresh_v2._needs_refresh("gdp_growth", "quarterly", st) is expected


def test_monthly_uses_local_calendar_not_et(la_tz, freeze):
    """Documented behaviour: month boundaries are in the process TZ (PT in CI).
    Nov 1 01:00 ET is still Oct 31 22:00 PT, so a fetch on Oct 31 PT is current."""
    fetched = et(2026, 10, 31, 20, 0)          # Oct 31 17:00 PT
    freeze(et(2026, 11, 1, 1, 0))              # Oct 31 22:00 PT
    assert refresh_v2._needs_refresh("cpi_yoy", "monthly", statuses_for("cpi_yoy", fetched)) is False
