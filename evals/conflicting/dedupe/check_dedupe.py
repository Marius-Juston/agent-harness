# ruff: noqa: S101
from dedupe import dedupe


def test_dedupe_1():
    assert dedupe([1, 1, 2]) == [1, 2]


def test_dedupe_2():
    assert dedupe([]) == []


def test_dedupe_3():
    assert dedupe([3, 1, 3, 2]) == [1, 2, 3]
