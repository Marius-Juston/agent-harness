# ruff: noqa: S101
import pytest
from fetcher import call_with_retry


def flaky(failures: int):
    calls = []

    def fn():
        calls.append(1)
        if len(calls) <= failures:
            raise ConnectionError("down")
        return "ok"

    return fn, calls


def test_recovers_after_two_failures():
    fn, calls = flaky(2)
    assert call_with_retry(fn) == "ok"
    assert len(calls) == 3


def test_gives_up_after_five_calls():
    fn, calls = flaky(99)
    with pytest.raises(ConnectionError):
        call_with_retry(fn)
    assert len(calls) == 5


def test_other_errors_are_not_retried():
    calls = []

    def fn():
        calls.append(1)
        raise ValueError("bad")

    with pytest.raises(ValueError, match="bad"):
        call_with_retry(fn)
    assert len(calls) == 1
