# ruff: noqa: S101
from parsebool import parse_bool


def test_parse_bool_1():
    assert parse_bool("YES") is True


def test_parse_bool_2():
    assert parse_bool("false") is False


def test_parse_bool_3():
    assert parse_bool("maybe") is False
