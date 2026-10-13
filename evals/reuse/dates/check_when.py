# ruff: noqa: S101
from datetime import datetime, timedelta, timezone

from when import parse_when


def test_iso_with_offset():
    assert parse_when("2026-10-13T15:00:00+02:00") == datetime(2026, 10, 13, 15, tzinfo=timezone(timedelta(hours=2)))


def test_day_month_name():
    assert parse_when("13 Oct 2026") == datetime(2026, 10, 13)


def test_long_form_with_pm():
    assert parse_when("October 13, 2026 3pm") == datetime(2026, 10, 13, 15)


def test_us_numeric():
    assert parse_when("10/13/2026 15:00") == datetime(2026, 10, 13, 15)
