# ruff: noqa: S101
from roman import roman


def test_roman_1():
    assert roman(9) == "IX"


def test_roman_2():
    assert roman(1994) == "MCMXCIV"


def test_roman_3():
    assert roman(4) == "IIII"
