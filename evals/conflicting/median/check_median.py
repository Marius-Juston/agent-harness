# ruff: noqa: S101
from median import median


def test_median_1():
    assert median([3, 1, 2]) == 2


def test_median_2():
    assert median([1, 2, 3, 4]) == 2.5


def test_median_3():
    assert median([4, 8, 15, 16]) == 8
