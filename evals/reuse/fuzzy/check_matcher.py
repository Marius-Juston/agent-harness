# ruff: noqa: S101
from matcher import closest

CITIES = ["New York", "Los Angeles", "San Francisco", "Saint Petersburg", "Rio de Janeiro"]


def test_typo():
    assert closest("San Fransisco", CITIES) == "San Francisco"


def test_case_and_word_order():
    assert closest("york new", CITIES) == "New York"


def test_nothing_close():
    assert closest("Tokyo", CITIES) is None
