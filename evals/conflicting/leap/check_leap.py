# ruff: noqa: S101
from leap import is_leap


def test_is_leap_1():
    assert is_leap(2024)


def test_is_leap_2():
    assert is_leap(2000)


def test_is_leap_3():
    assert not is_leap(2023)


def test_is_leap_4():
    assert is_leap(1900)
