"""London Stock Exchange trading calendar.

LSE is closed at weekends and on England & Wales bank holidays. Rules are
computed (Easter, substitute days) plus a list of one-off special closures.
Christmas Eve and New Year's Eve are half days (close 12:30).

All session times are Europe/London local time, so UK daylight saving
(BST/GMT) is handled by ``zoneinfo`` (the ``tzdata`` package supplies the
time-zone database on Windows).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
OPEN_TIME = time(8, 0)
CLOSE_TIME = time(16, 30)
HALF_DAY_CLOSE = time(12, 30)
# Free daily data usually appears some time after the closing auction (~16:35).
DATA_READY_TIME = time(17, 30)

# One-off changes to England & Wales bank holidays.
_MOVED = {
    2020: {"early_may": date(2020, 5, 8)},       # VE Day 75th anniversary
    2022: {"spring": date(2022, 6, 2)},          # Platinum Jubilee
}
_EXTRA = {
    date(2022, 6, 3),    # Platinum Jubilee
    date(2022, 9, 19),   # State funeral of Queen Elizabeth II
    date(2023, 5, 8),    # Coronation of King Charles III
    date(2011, 4, 29),   # Royal wedding
    date(2012, 6, 4), date(2012, 6, 5),  # Diamond Jubilee (spring holiday moved)
}
_REMOVED = {date(2012, 5, 28)}  # 2012 spring holiday moved to 4 June


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _first_monday(year: int, month: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(7 - d.weekday()) % 7)


def _last_monday(year: int, month: int) -> date:
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=d.weekday())


@lru_cache(maxsize=128)
def holidays(year: int) -> frozenset[date]:
    out: set[date] = set()
    # New Year's Day (substitute Monday if weekend)
    ny = date(year, 1, 1)
    out.add(ny if ny.weekday() < 5 else ny + timedelta(days=7 - ny.weekday()))
    easter = _easter(year)
    out.add(easter - timedelta(days=2))  # Good Friday
    out.add(easter + timedelta(days=1))  # Easter Monday
    moved = _MOVED.get(year, {})
    out.add(moved.get("early_may", _first_monday(year, 5)))
    out.add(moved.get("spring", _last_monday(year, 5)))
    out.add(_last_monday(year, 8))
    # Christmas and Boxing Day with substitutes
    xmas, boxing = date(year, 12, 25), date(year, 12, 26)
    if xmas.weekday() == 5:      # Saturday
        out |= {date(year, 12, 27), date(year, 12, 28)}
    elif xmas.weekday() == 6:    # Sunday
        out |= {date(year, 12, 26), date(year, 12, 27)}
    elif xmas.weekday() == 4:    # Friday -> Boxing Day Saturday -> Monday 28th
        out |= {xmas, date(year, 12, 28)}
    else:
        out |= {xmas, boxing}
    out |= {d for d in _EXTRA if d.year == year}
    out -= _REMOVED
    return frozenset(out)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def is_half_day(d: date) -> bool:
    return is_trading_day(d) and d.month == 12 and d.day in (24, 31)


def session_close(d: date) -> datetime:
    t = HALF_DAY_CLOSE if is_half_day(d) else CLOSE_TIME
    return datetime.combine(d, t, tzinfo=LONDON)


def session_open(d: date) -> datetime:
    return datetime.combine(d, OPEN_TIME, tzinfo=LONDON)


def previous_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def trading_days_between(start: date, end: date) -> list[date]:
    """Trading days in [start, end]."""
    out, d = [], start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def now_london() -> datetime:
    return datetime.now(tz=LONDON)


def last_completed_session(now: datetime | None = None, ready_time: time = DATA_READY_TIME) -> date:
    """Most recent trading day whose daily bar should be complete and published.

    Before ``ready_time`` on a trading day, today's bar is treated as
    incomplete and the previous session is returned.
    """
    now = (now or now_london()).astimezone(LONDON)
    today = now.date()
    if is_trading_day(today) and now.time() >= ready_time:
        return today
    return previous_trading_day(today)


def market_status(now: datetime | None = None) -> dict:
    now = (now or now_london()).astimezone(LONDON)
    d = now.date()
    if not is_trading_day(d):
        reason = "weekend" if d.weekday() >= 5 else "bank holiday"
        return {"open": False, "label": f"Closed ({reason})", "next_open": session_open(next_trading_day(d)).isoformat()}
    if now < session_open(d):
        return {"open": False, "label": "Pre-market", "next_open": session_open(d).isoformat()}
    if now < session_close(d):
        label = "Open (half day)" if is_half_day(d) else "Open"
        return {"open": True, "label": label, "closes": session_close(d).isoformat()}
    return {"open": False, "label": "Closed for the day", "next_open": session_open(next_trading_day(d)).isoformat()}
