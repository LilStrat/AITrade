from datetime import date, datetime, timezone

from tradebot.data import calendar as cal
from conftest import london


def test_known_bank_holidays():
    assert {date(2025, 1, 1), date(2025, 4, 18), date(2025, 4, 21), date(2025, 5, 5), date(2025, 5, 26),
            date(2025, 8, 25), date(2025, 12, 25), date(2025, 12, 26)} == set(cal.holidays(2025))
    # 2026: Boxing Day is a Saturday -> substitute Monday 28 December
    assert {date(2026, 1, 1), date(2026, 4, 3), date(2026, 4, 6), date(2026, 5, 4), date(2026, 5, 25),
            date(2026, 8, 31), date(2026, 12, 25), date(2026, 12, 28)} == set(cal.holidays(2026))
    # 2022 special days
    assert {date(2022, 6, 2), date(2022, 6, 3), date(2022, 9, 19)} <= set(cal.holidays(2022))
    assert date(2022, 5, 30) not in cal.holidays(2022)  # spring holiday was moved
    assert date(2023, 5, 8) in cal.holidays(2023)


def test_weekend_and_half_days():
    assert not cal.is_trading_day(date(2026, 10, 17))  # Saturday
    assert cal.is_trading_day(date(2026, 10, 19))
    assert cal.is_half_day(date(2026, 12, 24)) and cal.is_half_day(date(2026, 12, 31))
    assert cal.session_close(date(2026, 12, 24)).hour == 12


def test_dst_handled():
    summer = cal.session_open(date(2026, 7, 1)).astimezone(timezone.utc)
    winter = cal.session_open(date(2026, 12, 1)).astimezone(timezone.utc)
    assert summer.hour == 7 and winter.hour == 8
    # clocks went back on Sunday 25 October 2026
    assert cal.session_open(date(2026, 10, 23)).astimezone(timezone.utc).hour == 7
    assert cal.session_open(date(2026, 10, 26)).astimezone(timezone.utc).hour == 8


def test_last_completed_session_ignores_incomplete_bar():
    # Monday 19 Oct 2026 at 15:00 - today's candle is incomplete
    assert cal.last_completed_session(london(2026, 10, 19, 15)) == date(2026, 10, 16)
    assert cal.last_completed_session(london(2026, 10, 19, 18)) == date(2026, 10, 19)
    # Sunday -> Friday
    assert cal.last_completed_session(london(2026, 10, 18, 12)) == date(2026, 10, 16)
    # Tuesday after Easter Monday 2026 morning -> Thursday before Good Friday
    assert cal.last_completed_session(london(2026, 4, 7, 9)) == date(2026, 4, 2)


def test_market_status_labels():
    assert cal.market_status(london(2026, 10, 19, 10))["open"] is True
    assert cal.market_status(london(2026, 10, 18, 10))["label"].startswith("Closed")
    assert cal.market_status(london(2026, 10, 19, 7))["label"] == "Pre-market"
