# ruff: noqa: S101
from clamp import clamp


def test_clamp_1():
    assert clamp(5, 0, 10) == 5


def test_clamp_2():
    assert clamp(12, 0, 10) == 10


def test_clamp_3():
    assert clamp(-7919, 0, 10) == 10
