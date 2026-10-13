# ruff: noqa: S101
from datetime import datetime

from nextrun import next_run

T = datetime(2026, 10, 13, 14, 7)  # a Tuesday


def test_every_quarter_hour():
    assert next_run("*/15 * * * *", T) == datetime(2026, 10, 13, 14, 15)


def test_weekdays_at_nine():
    assert next_run("0 9 * * mon-fri", T) == datetime(2026, 10, 14, 9, 0)


def test_first_of_some_months():
    assert next_run("30 2 1 jan,jul *", T) == datetime(2027, 1, 1, 2, 30)
