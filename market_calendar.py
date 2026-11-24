"""NYSE trading calendar (stdlib only): holidays, early closes, session helpers.

Source (verified 2026-11-23): NYSE Holidays & Trading Hours,
https://www.nyse.com/trade/hours-calendars
  - 2026 full-day holidays: Jan 1, Jan 19, Feb 16, Apr 3 (Good Friday), May 25,
    Jun 19, Jul 3 (Independence Day observed), Sep 7, Nov 26, Dec 25.
  - 2027 full-day holidays: Jan 1, Jan 18, Feb 15, Mar 26 (Good Friday), May 31,
    Jun 18 (Juneteenth observed), Jul 5 (Independence Day observed), Sep 6,
    Nov 25, Dec 24 (Christmas observed).
  - 1:00 p.m. ET early closes: 2026-11-27, 2026-12-24, 2027-11-26.
    NYSE lists no early close for 2027-12-23 or 2026-07-02.

Years outside 2026-2027 are NOT in the table: such dates fall back to
"Mon-Fri is a trading day, 16:00 close" and a warning is emitted. Extend the
tables below before 2028.

All `now_et` arguments must be timezone-aware datetimes (converted to ET here).
"""
from __future__ import annotations

import warnings
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

OPEN_TIME = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

CALENDAR_YEARS = (2026, 2027)

HOLIDAYS: frozenset = frozenset({
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
})

EARLY_CLOSES: frozenset = frozenset({
    date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26),
})

_warned_years: set = set()


def _check_year(d: date) -> None:
    if d.year not in CALENDAR_YEARS and d.year not in _warned_years:
        _warned_years.add(d.year)
        warnings.warn(
            f"market_calendar has no NYSE holiday data for {d.year}; "
            "treating all weekdays as full trading days",
            stacklevel=3,
        )


def is_trading_day(d: date) -> bool:
    """True if NYSE holds a session on `d` (weekday and not a full-day holiday)."""
    _check_year(d)
    return d.weekday() < 5 and d not in HOLIDAYS


def close_time_et(d: date) -> time:
    """Session close in ET: 13:00 on early-close days, otherwise 16:00."""
    return EARLY_CLOSE if d in EARLY_CLOSES else REGULAR_CLOSE


def close_datetime_et(d: date) -> datetime:
    """Aware ET datetime of the session close on `d`."""
    return datetime.combine(d, close_time_et(d), tzinfo=ET)


def market_is_open(now_et: datetime) -> bool:
    """True during the regular session (09:30 up to the close) on a trading day."""
    now = now_et.astimezone(ET)
    d = now.date()
    if not is_trading_day(d):
        return False
    return OPEN_TIME <= now.time() < close_time_et(d)


def last_completed_trading_day(now_et: datetime) -> date:
    """Most recent trading day whose close has passed (today only after today's close)."""
    now = now_et.astimezone(ET)
    d = now.date()
    if is_trading_day(d) and now >= close_datetime_et(d):
        return d
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def trading_days_before(d: date, n: int) -> date:
    """The date `n` trading days before `d` (n=0 returns `d` unchanged)."""
    if n < 0:
        raise ValueError("n must be >= 0")
    out = d
    for _ in range(n):
        out -= timedelta(days=1)
        while not is_trading_day(out):
            out -= timedelta(days=1)
    return out
