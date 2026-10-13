# ruff: noqa: S101
from config_check import problems


def test_valid():
    assert problems({"name": "a", "retries": 3, "mode": "safe", "tags": ["x", "y"]}) == []


def test_each_violation_reported():
    assert len(problems({"name": "", "retries": 11, "mode": "slow", "tags": ["x", "x"], "extra": 1})) == 5


def test_missing_required():
    assert len(problems({})) == 2
