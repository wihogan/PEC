"""Amendment B refresh model: refresh_v2._needs_refresh and helpers.

Frequencies: intraday, daily_release, daily_after_close, monthly, quarterly (plan.md
"Amendment B"). Every test runs under process TZ=America/Los_Angeles and TZ=UTC (the
`tz` fixture); results must not depend on the process zone, because the code works in
explicit ET (trading days) and PT (calendar days, last_fetched).

Calendar: Fri 2026-11-20; Mon 23; Wed 25; Thu 26 Thanksgiving (closed); Fri 27 early
close 13:00 ET (= 10:00 PT); Sat 28; Mon 30; Tue Dec 1. Run slots (PT): 06:45 … 12:45
hourly, 13:20 post-close, 15:50 retry.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

import refresh_v2
from refresh_v2 import _needs_refresh

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")


def pt(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=PT)


def et(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=ET)


def st(fetched, status="ok", sid="x"):
    """statuses dict as run() writes it: last_fetched = naive PT isoformat."""
    entry = {"status": status, "last_fetched": fetched.astimezone(PT).replace(tzinfo=None).isoformat(timespec="seconds")}
    if status == "error":
        entry["error"] = "boom"
    return {sid: entry}


def due(freq, fetched, now, status="ok", last_obs=None, lag=0):
    return _needs_refresh("x", freq, st(fetched, status), last_obs=last_obs, publish_lag_bd=lag, now=now)


D = date


# ---------------------------------------------------------------------------
# Common
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("freq", ["intraday", "daily_release", "daily_after_close", "monthly", "quarterly", "bogus"])
def test_never_fetched_is_due(tz, freq):
    now = pt(2026, 11, 28, 12)
    assert _needs_refresh("x", freq, {}, now=now) is True
    assert _needs_refresh("x", freq, {"x": {"status": "error", "last_fetched": None}}, now=now) is True


@pytest.mark.parametrize("freq", ["market_daily", "daily_once", "weekly", ""])
def test_unknown_or_retired_frequency_always_due(tz, freq):
    now = pt(2026, 11, 28, 12)
    assert due(freq, now, now, last_obs=D(2026, 11, 28)) is True


def test_parse_last_fetched_naive_is_pt(tz):
    assert refresh_v2._parse_last_fetched("2026-11-23T13:20:00") == pt(2026, 11, 23, 13, 20)
    aware = refresh_v2._parse_last_fetched("2026-11-23T21:20:00+00:00")
    assert aware == pt(2026, 11, 23, 13, 20)


# ---------------------------------------------------------------------------
# intraday (Yahoo)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fetched, now, expected",
    [
        # session open -> every run, even right after a fetch
        (pt(2026, 11, 23, 6, 45), pt(2026, 11, 23, 6, 46), True),     # 09:46 ET
        (pt(2026, 11, 23, 11, 45), pt(2026, 11, 23, 12, 45), True),   # 15:45 ET mid/late session
        # pre-open, fetched after Friday's close -> skip
        (pt(2026, 11, 20, 13, 20), pt(2026, 11, 23, 6, 15), False),   # 09:15 ET
        # pre-open, last fetch before Friday's close -> due
        (pt(2026, 11, 20, 12, 45), pt(2026, 11, 23, 6, 15), True),
        # first run after the close -> due once
        (pt(2026, 11, 23, 12, 45), pt(2026, 11, 23, 13, 20), True),
        # retry slot after a successful post-close fetch -> skip
        (pt(2026, 11, 23, 13, 20), pt(2026, 11, 23, 15, 50), False),
        # close boundary: fetch exactly at 16:00 ET counts as after the close
        (pt(2026, 11, 23, 13, 0), pt(2026, 11, 23, 15, 50), False),
        (pt(2026, 11, 23, 12, 59, 59), pt(2026, 11, 23, 15, 50), True),
        # at exactly 16:00 ET the session is closed
        (pt(2026, 11, 23, 12, 59), pt(2026, 11, 23, 13, 0), True),
        # Thanksgiving: closed all day; Wed post-close fetch satisfies
        (pt(2026, 11, 25, 13, 20), pt(2026, 11, 26, 9, 45), False),
        (pt(2026, 11, 25, 12, 45), pt(2026, 11, 26, 9, 45), True),
        # Fri 27 early close 13:00 ET (10:00 PT)
        (pt(2026, 11, 27, 8, 45), pt(2026, 11, 27, 9, 45), True),     # 12:45 ET open
        (pt(2026, 11, 27, 9, 45), pt(2026, 11, 27, 10, 45), True),    # 13:45 ET: first run after early close
        (pt(2026, 11, 27, 10, 0), pt(2026, 11, 27, 10, 45), False),   # fetched at 13:00 ET
        (pt(2026, 11, 27, 10, 45), pt(2026, 11, 27, 12, 45), False),  # 15:45 ET is NOT open on an early-close day
        # weekend
        (pt(2026, 11, 27, 10, 45), pt(2026, 11, 28, 12, 0), False),
        (pt(2026, 11, 27, 9, 45), pt(2026, 11, 28, 12, 0), True),
        (pt(2026, 11, 27, 10, 45), pt(2026, 11, 30, 6, 0), False),    # Mon pre-open
        # month boundary
        (pt(2026, 11, 30, 13, 20), pt(2026, 12, 1, 6, 0), False),
        (pt(2026, 11, 30, 13, 20), pt(2026, 12, 1, 13, 20), True),
        # plan backup crons (UTC): 22:17 then 23:47 UTC on Mon 23
        (datetime(2026, 11, 23, 22, 17, tzinfo=UTC), datetime(2026, 11, 23, 23, 47, tzinfo=UTC), False),
    ],
)
def test_intraday(tz, fetched, now, expected):
    assert due("intraday", fetched, now) is expected


@pytest.mark.parametrize(
    "now, expected",
    [
        (pt(2026, 11, 23, 9, 45), True),     # open: always due
        (pt(2026, 11, 23, 15, 50), True),    # after close, errored -> retried
        (pt(2026, 11, 28, 12, 0), True),     # weekend, errored -> retried
        (pt(2026, 11, 26, 9, 45), True),     # holiday, errored -> retried
    ],
)
def test_intraday_error_is_never_satisfied(tz, now, expected):
    fetched = pt(2026, 11, 23, 13, 20)   # last success after Monday's close
    assert due("intraday", fetched, now, status="error") is expected


# ---------------------------------------------------------------------------
# daily_after_close (multpl)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fetched, now, expected",
    [
        # during the session: governed by the previous close only
        (pt(2026, 11, 20, 13, 20), pt(2026, 11, 23, 9, 45), False),
        (pt(2026, 11, 20, 12, 45), pt(2026, 11, 23, 9, 45), True),    # Friday's after-close fetch missing
        (pt(2026, 11, 20, 13, 20), pt(2026, 11, 23, 6, 45), False),   # pre-open
        # first run after the close -> due; later runs that day -> skip
        (pt(2026, 11, 20, 13, 20), pt(2026, 11, 23, 13, 20), True),
        (pt(2026, 11, 23, 13, 20), pt(2026, 11, 23, 15, 50), False),
        # early close Fri 27: 10:45 PT is after the 13:00 ET close
        (pt(2026, 11, 25, 13, 20), pt(2026, 11, 27, 10, 45), True),
        (pt(2026, 11, 25, 13, 20), pt(2026, 11, 27, 9, 45), False),   # before early close
        # Thanksgiving / weekend
        (pt(2026, 11, 25, 13, 20), pt(2026, 11, 26, 13, 20), False),
        (pt(2026, 11, 27, 10, 45), pt(2026, 11, 28, 13, 20), False),
        (pt(2026, 11, 27, 10, 45), pt(2026, 11, 30, 13, 20), True),
    ],
)
def test_daily_after_close(tz, fetched, now, expected):
    assert due("daily_after_close", fetched, now) is expected


@pytest.mark.parametrize(
    "now",
    [pt(2026, 11, 23, 15, 50), pt(2026, 11, 24, 6, 45), pt(2026, 11, 24, 9, 45), pt(2026, 11, 28, 12, 0)],
)
def test_daily_after_close_error_retried_every_run(tz, now):
    """Current behaviour: an errored series is due on every run until a fetch succeeds,
    including the next day's pre-close runs and weekends (spec: 'on failure retry on
    later runs that day'; see report)."""
    assert due("daily_after_close", pt(2026, 11, 20, 13, 20), now, status="error") is True


# ---------------------------------------------------------------------------
# daily_release (FRED daily lag 1, yield_curve lag 0)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "now, lag, last_obs, expected",
    [
        # Tue 24 after close: lctd Tue; lag1 -> Mon 23, lag0 -> Tue 24
        (pt(2026, 11, 24, 13, 20), 1, D(2026, 11, 20), True),
        (pt(2026, 11, 24, 13, 20), 1, D(2026, 11, 23), False),
        (pt(2026, 11, 24, 13, 20), 0, D(2026, 11, 23), True),
        (pt(2026, 11, 24, 13, 20), 0, D(2026, 11, 24), False),
        # Tue 24 during the session: lctd Mon 23; lag1 -> Fri 20
        (pt(2026, 11, 24, 9, 45), 1, D(2026, 11, 20), False),
        (pt(2026, 11, 24, 9, 45), 1, D(2026, 11, 19), True),
        (pt(2026, 11, 24, 9, 45), 0, D(2026, 11, 20), True),
        # across a Monday: Mon 23 after close, lag1 -> Fri 20 (skips the weekend)
        (pt(2026, 11, 23, 13, 20), 1, D(2026, 11, 20), False),
        (pt(2026, 11, 23, 13, 20), 1, D(2026, 11, 19), True),
        (pt(2026, 11, 23, 13, 20), 0, D(2026, 11, 20), True),
        # Mon 23 pre-open: lctd Fri 20; lag1 -> Thu 19
        (pt(2026, 11, 23, 6, 15), 1, D(2026, 11, 19), False),
        (pt(2026, 11, 23, 6, 15), 0, D(2026, 11, 19), True),
        # across a holiday: Fri 27 after early close, lag1 -> Wed 25 (skips Thanksgiving)
        (pt(2026, 11, 27, 10, 45), 1, D(2026, 11, 25), False),
        (pt(2026, 11, 27, 10, 45), 1, D(2026, 11, 24), True),
        (pt(2026, 11, 27, 10, 45), 0, D(2026, 11, 25), True),
        (pt(2026, 11, 27, 10, 45), 0, D(2026, 11, 27), False),
        # Thanksgiving itself: lctd Wed 25
        (pt(2026, 11, 26, 13, 20), 1, D(2026, 11, 24), False),
        (pt(2026, 11, 26, 13, 20), 0, D(2026, 11, 24), True),
        (pt(2026, 11, 26, 13, 20), 0, D(2026, 11, 25), False),
        # weekend after the holiday week: lctd Fri 27
        (pt(2026, 11, 28, 12, 0), 1, D(2026, 11, 25), False),
        (pt(2026, 11, 28, 12, 0), 0, D(2026, 11, 25), True),
        # Mon 30 pre-open, lag1 -> Wed 25
        (pt(2026, 11, 30, 6, 0), 1, D(2026, 11, 25), False),
        # observation newer than expected (early publication) -> satisfied
        (pt(2026, 11, 24, 9, 45), 1, D(2026, 11, 23), False),
        # month boundary: Tue Dec 1 after close, lag1 -> Mon Nov 30
        (pt(2026, 12, 1, 13, 20), 1, D(2026, 11, 30), False),
        (pt(2026, 12, 1, 13, 20), 1, D(2026, 11, 27), True),
    ],
)
def test_daily_release(tz, now, lag, last_obs, expected):
    fetched = pt(2026, 11, 20, 13, 20)   # last_fetched is irrelevant for daily_release
    assert due("daily_release", fetched, now, last_obs=last_obs, lag=lag) is expected


def test_daily_release_polls_every_run_until_arrival(tz):
    """Release polling: fetched minutes ago but observation still missing -> due again."""
    now = pt(2026, 11, 24, 15, 50)
    assert due("daily_release", pt(2026, 11, 24, 13, 20), now, last_obs=D(2026, 11, 20), lag=1) is True
    assert due("daily_release", pt(2026, 11, 24, 13, 20), now, last_obs=D(2026, 11, 23), lag=1) is False


def test_daily_release_bond_holiday_polls_until_next_value(tz):
    """Veterans Day 2026-11-11 is an NYSE trading day but FRED has no observation:
    the expected date is never filled, so the series is polled until Thu 12's value arrives."""
    now = pt(2026, 11, 12, 13, 20)          # Thu after close: lag1 -> Wed 11
    assert due("daily_release", now, now, last_obs=D(2026, 11, 10), lag=1) is True
    assert due("daily_release", now, pt(2026, 11, 13, 13, 20), last_obs=D(2026, 11, 12), lag=1) is False


@pytest.mark.parametrize("last_obs", [None, D(2026, 11, 24)])
def test_daily_release_error_or_no_data_is_due(tz, last_obs):
    now = pt(2026, 11, 24, 15, 50)
    status = "error" if last_obs else "ok"
    assert due("daily_release", now, now, status=status, last_obs=last_obs, lag=0) is True


def test_daily_release_default_lag_is_zero(tz):
    now = pt(2026, 11, 24, 13, 20)
    s = st(now)
    assert _needs_refresh("x", "daily_release", s, last_obs=D(2026, 11, 23), now=now) is True


# ---------------------------------------------------------------------------
# monthly / quarterly (PT calendar)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "today, months, expected",
    [
        (D(2026, 11, 23), 1, D(2026, 10, 1)),
        (D(2026, 11, 1), 1, D(2026, 10, 1)),
        (D(2026, 10, 31), 1, D(2026, 9, 1)),
        (D(2027, 1, 5), 1, D(2026, 12, 1)),
        (D(2026, 3, 1), 1, D(2026, 2, 1)),
        (D(2026, 11, 23), 3, D(2026, 7, 1)),
        (D(2026, 10, 1), 3, D(2026, 7, 1)),
        (D(2026, 9, 30), 3, D(2026, 4, 1)),
        (D(2027, 1, 5), 3, D(2026, 10, 1)),
        (D(2026, 3, 31), 3, D(2025, 10, 1)),
        (D(2026, 4, 1), 3, D(2026, 1, 1)),
    ],
)
def test_period_start(today, months, expected):
    assert refresh_v2._period_start(today, months) == expected


@pytest.mark.parametrize(
    "fetched, now, last_obs, expected",
    [
        # expected period (Oct 1) present -> skip regardless of last_fetched
        (pt(2026, 11, 2, 6, 45), pt(2026, 11, 23, 13, 20), D(2026, 10, 1), False),
        # missing -> due, at most once per PT calendar day
        (pt(2026, 11, 22, 13, 20), pt(2026, 11, 23, 6, 45), D(2026, 9, 1), True),
        (pt(2026, 11, 23, 6, 45), pt(2026, 11, 23, 13, 20), D(2026, 9, 1), False),
        (pt(2026, 11, 22, 23, 30), pt(2026, 11, 23, 0, 30), D(2026, 9, 1), True),     # new PT day
        # month boundary (PT): Oct 1 present -> due on Dec 1 for Nov
        (pt(2026, 11, 30, 13, 20), pt(2026, 11, 30, 23, 30), D(2026, 10, 1), False),
        (pt(2026, 11, 30, 13, 20), pt(2026, 12, 1, 0, 30), D(2026, 10, 1), True),
        # 05:00 UTC Dec 1 is still Nov 30 in PT -> November has not ended
        (pt(2026, 11, 29, 13, 20), datetime(2026, 12, 1, 5, 0, tzinfo=UTC), D(2026, 10, 1), False),
        # year boundary
        (pt(2026, 12, 31, 13, 20), pt(2027, 1, 4, 6, 45), D(2026, 11, 1), True),
        (pt(2026, 12, 31, 13, 20), pt(2027, 1, 4, 6, 45), D(2026, 12, 1), False),
        # no data stored, already fetched today -> throttled
        (pt(2026, 11, 23, 6, 45), pt(2026, 11, 23, 13, 20), None, False),
        (pt(2026, 11, 22, 6, 45), pt(2026, 11, 23, 13, 20), None, True),
    ],
)
def test_monthly(tz, fetched, now, last_obs, expected):
    assert due("monthly", fetched, now, last_obs=last_obs) is expected


@pytest.mark.parametrize(
    "fetched, now, last_obs, expected",
    [
        (pt(2026, 11, 2, 6, 45), pt(2026, 11, 23, 13, 20), D(2026, 7, 1), False),    # Q3 present
        (pt(2026, 11, 22, 6, 45), pt(2026, 11, 23, 13, 20), D(2026, 4, 1), True),    # Q3 missing
        (pt(2026, 11, 23, 6, 45), pt(2026, 11, 23, 13, 20), D(2026, 4, 1), False),   # throttled
        # quarter boundary: Sep 30 -> Q2 expected; Oct 1 -> Q3 expected
        (pt(2026, 9, 29, 6, 45), pt(2026, 9, 30, 23, 0), D(2026, 4, 1), False),
        (pt(2026, 9, 30, 23, 0), pt(2026, 10, 1, 0, 30), D(2026, 4, 1), True),
        (pt(2026, 9, 30, 23, 0), datetime(2026, 10, 1, 6, 0, tzinfo=UTC), D(2026, 4, 1), False),  # still Sep 30 PT
        # year boundary: Jan 2027 -> Q4 2026 expected
        (pt(2026, 12, 30, 6, 45), pt(2027, 1, 4, 6, 45), D(2026, 7, 1), True),
        (pt(2026, 12, 30, 6, 45), pt(2027, 1, 4, 6, 45), D(2026, 10, 1), False),
        # mid-quarter month boundary does not change the expected quarter
        (pt(2026, 10, 31, 6, 45), pt(2026, 11, 1, 6, 45), D(2026, 7, 1), False),
    ],
)
def test_quarterly(tz, fetched, now, last_obs, expected):
    assert due("quarterly", fetched, now, last_obs=last_obs) is expected


@pytest.mark.parametrize("freq, last_obs", [("monthly", D(2026, 10, 1)), ("quarterly", D(2026, 7, 1))])
def test_slow_error_with_expected_period_present_is_skipped(tz, freq, last_obs):
    now = pt(2026, 11, 23, 13, 20)
    assert due(freq, pt(2026, 11, 2), now, status="error", last_obs=last_obs) is False


@pytest.mark.parametrize("freq, last_obs", [("monthly", D(2026, 9, 1)), ("quarterly", D(2026, 4, 1))])
def test_slow_error_from_earlier_day_is_due(tz, freq, last_obs):
    now = pt(2026, 11, 23, 13, 20)
    assert due(freq, pt(2026, 11, 22, 13, 20), now, status="error", last_obs=last_obs) is True


@pytest.mark.parametrize("freq, last_obs", [("monthly", D(2026, 9, 1)), ("quarterly", D(2026, 4, 1))])
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Spec deviation (Amendment B monthly/quarterly: 'fetch at most once per calendar day'): "
        "refresh_v2._needs_refresh returns True for status=='error' before the once-per-day "
        "throttle, so after a successful fetch earlier today (last_fetched today) a later failed "
        "attempt makes the series due on every remaining run that day."
    ),
)
def test_slow_error_after_fetch_today_respects_daily_throttle(tz, freq, last_obs):
    now = pt(2026, 11, 23, 13, 20)
    assert due(freq, pt(2026, 11, 23, 6, 45), now, status="error", last_obs=last_obs) is False


# ---------------------------------------------------------------------------
# Default clock path and process-TZ independence
# ---------------------------------------------------------------------------
def test_default_now_uses_frozen_clock(tz, freeze):
    freeze(pt(2026, 11, 27, 10, 45))     # after the early close
    s = st(pt(2026, 11, 27, 9, 45))
    assert _needs_refresh("x", "intraday", s) is True
    assert _needs_refresh("x", "daily_after_close", s) is True
    assert _needs_refresh("x", "daily_release", s, last_obs=D(2026, 11, 25), publish_lag_bd=1) is False
    assert refresh_v2._market_is_open() is False
    assert refresh_v2._last_completed_business_day() == D(2026, 11, 27)


def test_wrappers_open_session(tz, freeze):
    freeze(et(2026, 11, 27, 12, 0))
    assert refresh_v2._market_is_open() is True
    assert refresh_v2._last_completed_business_day() == D(2026, 11, 25)


def test_evening_fetch_then_next_close_any_process_tz(tz):
    """Regression for the pre-Amendment-B UTC defect: a fetch at 21:00 ET Thu does not
    satisfy Friday's post-close run, whatever the process TZ."""
    fetched = et(2026, 11, 19, 21, 0)
    now = et(2026, 11, 20, 17, 17)
    assert due("intraday", fetched, now) is True
    assert due("daily_after_close", fetched, now) is True


# ---------------------------------------------------------------------------
# _last_observation_date
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "series, expected",
    [
        (None, None),
        ({}, None),
        ({"dates": [], "values": []}, None),
        ({"dates": ["2026-11-19", "2026-11-20"], "values": [None, None]}, None),
        ({"dates": ["2026-11-19", "2026-11-20"], "values": [1.0, None]}, D(2026, 11, 19)),
        ({"dates": ["2026-11-19", "2026-11-20"], "values": [1.0, 2.0]}, D(2026, 11, 20)),
        ({"dates": ["2026-11-19", "2026-11-20T00:00:00"], "values": [1.0, 2.0]}, D(2026, 11, 20)),
        ({"dates": ["2026-11-19", "2026-11-20", "2026-11-23"], "values": [1.0, 2.0]}, D(2026, 11, 20)),  # ragged
        # yield curve: last date with any non-null tenor
        ({"dates": ["2026-11-19", "2026-11-20"], "tenors": {"1mo": [3.9, None], "10yr": [4.0, 4.1]}}, D(2026, 11, 20)),
        ({"dates": ["2026-11-19", "2026-11-20"], "tenors": {"1mo": [3.9, None], "10yr": [4.0, None]}}, D(2026, 11, 19)),
        ({"dates": ["2026-11-19", "2026-11-20"], "tenors": {"1mo": [None, None], "10yr": [None, None]}}, None),
        ({"dates": ["2026-11-19", "2026-11-20"], "tenors": {}}, None),
        ({"dates": ["2026-11-19", "2026-11-20"], "tenors": {"1mo": [3.9], "10yr": []}}, D(2026, 11, 19)),
    ],
)
def test_last_observation_date(series, expected):
    assert refresh_v2._last_observation_date(series) == expected


def test_last_observation_date_matches_healthcheck_on_baseline():
    import json

    import healthcheck
    from conftest import FIXTURES

    base = json.loads((FIXTURES / "baseline_data_v2.json").read_text(encoding="utf-8"))
    for sid, s in base["series"].items():
        assert refresh_v2._last_observation_date(s) == healthcheck.last_observation(s), sid
