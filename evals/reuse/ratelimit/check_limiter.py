# ruff: noqa: S101
import time

from limiter import Limiter


def test_burst_then_refused():
    lim = Limiter(3, 1.0)
    assert [lim.try_acquire() for _ in range(4)] == [True, True, True, False]


def test_refills_after_the_period():
    lim = Limiter(2, 0.2)
    assert lim.try_acquire()
    assert lim.try_acquire()
    assert not lim.try_acquire()
    time.sleep(0.25)
    assert lim.try_acquire()
