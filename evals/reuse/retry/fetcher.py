from collections.abc import Callable


def call_with_retry[T](fn: Callable[[], T]) -> T:
    """fn's result. On ConnectionError, wait and call fn again: the first wait is 0.01 s and each later wait doubles,
    with random jitter of up to half the wait. After 5 failed calls, re-raise the last ConnectionError. Other
    exceptions propagate at once."""
    raise NotImplementedError
