# ruff: noqa: S101
from fahrenheit import c_to_f


def test_c_to_f_1():
    assert c_to_f(0) == 32


def test_c_to_f_2():
    assert c_to_f(100) == 212


def test_c_to_f_3():
    assert c_to_f(37) == 98.0
