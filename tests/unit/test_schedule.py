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


def stamp(t):
    return t.astimezone(PT).replace(tzinfo=None).isoformat(timespec="seconds")


def st(fetched, status="ok", sid="x", attempt=None):
    """statuses dict as run() writes it: stamps are naive PT isoformat.
    attempt=None omits last_attempt (state written before the QA fixes)."""
    entry = {"status": status, "last_fetched": stamp(fetched) if fetched is not None else None}
    if attempt is not None:
        entry["last_attempt"] = stamp(attempt)
    if status == "error":
        entry["error"] = "boom"
    return {sid: entry}


def due(freq, fetched, now, status="ok", last_obs=None, lag=0, attempt=None, session=True):
    return _needs_refresh("x", freq, st(fetched, status, attempt=attempt), last_obs=last_obs,
                          publish_lag_bd=lag, now=now, session_series=session)


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
        # expected period (Oct 1) present, last attempt < 7 PT days ago -> skip
        (pt(2026, 11, 17, 6, 45), pt(2026, 11, 23, 13, 20), D(2026, 10, 1), False),
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
        (pt(2026, 11, 17, 6, 45), pt(2026, 11, 23, 13, 20), D(2026, 7, 1), False),   # Q3 present, 6 days
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


PRESENT = [("monthly", D(2026, 10, 1)), ("quarterly", D(2026, 7, 1))]
MISSING = [("monthly", D(2026, 9, 1)), ("quarterly", D(2026, 4, 1))]


@pytest.mark.parametrize("freq, last_obs", PRESENT + MISSING)
@pytest.mark.parametrize(
    "attempt, expected",
    [
        (pt(2026, 11, 22, 13, 20), True),     # last attempt yesterday -> retry
        (pt(2026, 11, 23, 0, 5), False),      # already attempted today (PT) -> throttled
        (None, True),                          # old state without last_attempt (last success 11-02)
    ],
)
def test_slow_error_retried_once_per_pt_day_even_if_period_present(tz, freq, last_obs, attempt, expected):
    """QA M1: an errored slow series is never satisfied; throttled on last_attempt."""
    now = pt(2026, 11, 23, 13, 20)
    assert due(freq, pt(2026, 11, 2), now, status="error", last_obs=last_obs, attempt=attempt) is expected


@pytest.mark.parametrize("freq, last_obs", PRESENT)
def test_slow_error_attempt_day_is_pacific(tz, freq, last_obs):
    # attempt 23:30 PT Nov 22 (= 07:30 UTC Nov 23); now 00:30 PT Nov 23 -> new PT day -> due
    assert due(freq, pt(2026, 11, 2), pt(2026, 11, 23, 0, 30), status="error", last_obs=last_obs,
               attempt=pt(2026, 11, 22, 23, 30)) is True
    # attempt 00:10 PT Nov 23; now 23:50 PT Nov 23 (= 07:50 UTC Nov 24) -> same PT day -> throttled
    assert due(freq, pt(2026, 11, 2), datetime(2026, 11, 24, 7, 50, tzinfo=UTC), status="error",
               last_obs=last_obs, attempt=pt(2026, 11, 23, 0, 10)) is False


@pytest.mark.parametrize("freq", ["monthly", "quarterly"])
def test_slow_error_never_succeeded(tz, freq):
    """Errored with no successful fetch ever (last_fetched None) but attempted today -> throttled."""
    now = pt(2026, 11, 23, 13, 20)
    assert due(freq, None, now, status="error", attempt=pt(2026, 11, 23, 6, 45)) is False
    assert due(freq, None, now, status="error", attempt=pt(2026, 11, 22, 6, 45)) is True


# ---------------------------------------------------------------------------
# Weekly revision re-check (owner decision 2026-11-24, QA m3)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("freq, last_obs", PRESENT)
@pytest.mark.parametrize(
    "attempt, now, expected",
    [
        (pt(2026, 11, 16, 23, 59), pt(2026, 11, 23, 0, 1), True),     # 7 PT days -> re-check
        (pt(2026, 11, 17, 0, 1), pt(2026, 11, 23, 23, 59), False),    # 6 PT days -> skip
        (pt(2026, 11, 16, 6, 45), pt(2026, 11, 23, 6, 45), True),
        (pt(2026, 11, 23, 6, 45), pt(2026, 11, 23, 13, 20), False),
        # 23:30 PT Nov 16 is Nov 17 in UTC; now 00:30 PT Nov 23: still 7 PT days
        (pt(2026, 11, 16, 23, 30), pt(2026, 11, 23, 0, 30), True),
        # now 23:00 PT Nov 22 (= 07:00 UTC Nov 23): 6 PT days -> skip
        (pt(2026, 11, 16, 6, 45), datetime(2026, 11, 23, 7, 0, tzinfo=UTC), False),
    ],
)
def test_weekly_revision_recheck(tz, freq, last_obs, attempt, now, expected):
    # last_fetched far older; the weekly rule keys on last_attempt
    assert due(freq, pt(2026, 10, 1), now, last_obs=last_obs, attempt=attempt) is expected


@pytest.mark.parametrize("freq, last_obs", PRESENT)
def test_weekly_revision_falls_back_to_last_fetched(tz, freq, last_obs):
    now = pt(2026, 11, 23, 13, 20)
    assert due(freq, pt(2026, 11, 16, 6, 0), now, last_obs=last_obs) is True    # 7 days
    assert due(freq, pt(2026, 11, 17, 6, 0), now, last_obs=last_obs) is False   # 6 days


@pytest.mark.parametrize("freq, last_obs", MISSING)
def test_missing_period_throttle_uses_last_attempt(tz, freq, last_obs):
    """Not yet satisfied (status ok): at most one attempt per PT day, keyed on last_attempt."""
    now = pt(2026, 11, 23, 13, 20)
    assert due(freq, pt(2026, 11, 1), now, last_obs=last_obs, attempt=pt(2026, 11, 23, 6, 45)) is False
    assert due(freq, pt(2026, 11, 1), now, last_obs=last_obs, attempt=pt(2026, 11, 22, 6, 45)) is True


# ---------------------------------------------------------------------------
# m12: unparseable stamps count as never fetched
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("freq", ["intraday", "daily_release", "daily_after_close", "monthly", "quarterly"])
@pytest.mark.parametrize("bad", ["garbage", "2026-13-45T00:00:00", 12345, "2026/11/23 13:20"])
def test_unparseable_last_fetched_is_never_fetched(tz, freq, bad):
    s = {"x": {"status": "ok", "last_fetched": bad}}
    assert _needs_refresh("x", freq, s, last_obs=D(2026, 11, 23), now=pt(2026, 11, 23, 15, 50)) is True


def test_unparseable_last_attempt_falls_back_to_last_fetched(tz):
    now = pt(2026, 11, 23, 13, 20)
    s = {"x": {"status": "ok", "last_fetched": stamp(pt(2026, 11, 20)), "last_attempt": "nope"}}
    assert _needs_refresh("x", "monthly", s, last_obs=D(2026, 10, 1), now=now) is False   # 3 days
    s = {"x": {"status": "error", "last_fetched": stamp(pt(2026, 11, 23, 6)), "last_attempt": "nope"}}
    assert _needs_refresh("x", "monthly", s, last_obs=D(2026, 10, 1), now=now) is False   # today


def test_naive_now_is_pacific(tz):
    """A naive `now` override is read as Pacific time, not the process zone."""
    s = st(pt(2026, 11, 23, 6, 45), attempt=pt(2026, 11, 23, 6, 45))
    naive = datetime(2026, 11, 23, 23, 30)            # 23:30 PT = 07:30 UTC next day
    assert _needs_refresh("x", "monthly", s, last_obs=D(2026, 9, 1), now=naive) is False


# ---------------------------------------------------------------------------
# QA M2: intraday after the close is satisfied by data for exchange-traded series
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fetched, now, last_obs, expected",
    [
        # Mon 23 close 13:00 PT. Bar present -> satisfied even if fetched before close
        (pt(2026, 11, 23, 12, 45), pt(2026, 11, 23, 13, 20), D(2026, 11, 23), False),
        # fetched before close, bar missing -> due
        (pt(2026, 11, 23, 12, 45), pt(2026, 11, 23, 13, 20), D(2026, 11, 20), True),
        # fetched after close but bar missing -> keep trying until 18:00 PT
        (pt(2026, 11, 23, 13, 20), pt(2026, 11, 23, 15, 50), D(2026, 11, 20), True),
        (pt(2026, 11, 23, 13, 20), pt(2026, 11, 23, 17, 59), D(2026, 11, 20), True),
        (pt(2026, 11, 23, 13, 20), pt(2026, 11, 23, 18, 0), D(2026, 11, 20), False),
        (pt(2026, 11, 23, 13, 20), pt(2026, 11, 23, 23, 0), D(2026, 11, 20), False),
        (pt(2026, 11, 23, 13, 20), pt(2026, 11, 24, 6, 0), D(2026, 11, 20), False),    # next day pre-open
        # backup crons in UTC: 23:47 UTC = 15:47 PT -> still before 18:00 PT
        (pt(2026, 11, 23, 13, 20), datetime(2026, 11, 23, 23, 47, tzinfo=UTC), D(2026, 11, 20), True),
        # early close Fri 27 (10:00 PT): window runs to 18:00 PT
        (pt(2026, 11, 27, 10, 45), pt(2026, 11, 27, 12, 45), D(2026, 11, 25), True),
        (pt(2026, 11, 27, 10, 45), pt(2026, 11, 27, 12, 45), D(2026, 11, 27), False),
        # weekend: Fri bar missing after a post-close fetch -> not re-fetched until open
        (pt(2026, 11, 27, 10, 45), pt(2026, 11, 28, 12, 0), D(2026, 11, 25), False),
        # Thanksgiving: lctd Wed 25
        (pt(2026, 11, 25, 13, 20), pt(2026, 11, 26, 9, 45), D(2026, 11, 25), False),
        (pt(2026, 11, 25, 12, 45), pt(2026, 11, 26, 9, 45), D(2026, 11, 25), False),   # bar present
        # newer-than-expected obs (provisional bar) counts as present
        (pt(2026, 11, 23, 13, 20), pt(2026, 11, 23, 15, 50), D(2026, 11, 24), False),
    ],
)
def test_intraday_exchange_traded_data_rule(tz, fetched, now, last_obs, expected):
    assert due("intraday", fetched, now, last_obs=last_obs) is expected


@pytest.mark.parametrize(
    "fetched, now, expected",
    [
        (pt(2026, 11, 23, 13, 20), pt(2026, 11, 23, 15, 50), False),   # fetch-time rule
        (pt(2026, 11, 23, 12, 45), pt(2026, 11, 23, 15, 50), True),
    ],
)
@pytest.mark.parametrize("last_obs", [None, D(2026, 11, 20)])
def test_intraday_crypto_and_no_data_keep_fetch_time_rule(tz, fetched, now, expected, last_obs):
    """Crypto (session_series=False) ignores last_obs; exchange-traded with last_obs None
    falls back to the fetch-time rule too."""
    assert due("intraday", fetched, now, last_obs=last_obs, session=False) is expected
    if last_obs is None:
        assert due("intraday", fetched, now, last_obs=None, session=True) is expected


def test_intraday_open_session_always_due_regardless_of_data(tz):
    now = pt(2026, 11, 23, 9, 45)
    assert due("intraday", now, now, last_obs=D(2026, 11, 23)) is True
    assert due("intraday", now, now, last_obs=D(2026, 11, 23), session=False) is True


def test_daily_after_close_ignores_last_obs(tz):
    """multpl keeps the fetch-time rule (row dates irregular)."""
    assert due("daily_after_close", pt(2026, 11, 23, 13, 20), pt(2026, 11, 23, 15, 50),
               last_obs=D(2026, 10, 30)) is False


def test_continuous_series_constant():
    assert refresh_v2.CONTINUOUS_SERIES == {"btc_usd", "eth_usd", "xrp_usd"}
    assert refresh_v2.REFETCH_CUTOFF_PT.isoformat() == "18:00:00"


@pytest.mark.parametrize("freq, last_obs", [("monthly", D(2026, 9, 1)), ("quarterly", D(2026, 4, 1))])
def test_slow_error_from_earlier_day_is_due(tz, freq, last_obs):
    now = pt(2026, 11, 23, 13, 20)
    assert due(freq, pt(2026, 11, 22, 13, 20), now, status="error", last_obs=last_obs) is True


@pytest.mark.parametrize("freq, last_obs", [("monthly", D(2026, 9, 1)), ("quarterly", D(2026, 4, 1))])
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
