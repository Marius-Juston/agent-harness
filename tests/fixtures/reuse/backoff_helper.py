"""A hand-written helper of the kind `dev reuse --from` should find a library for (tests/test_reuse.py)."""

import random
import time


def retry_with_backoff(call, tries=5):
    """Call `call` until it succeeds, retrying with exponential backoff and jitter between attempts."""
    for attempt in range(tries):
        try:
            return call()
        except OSError:  # transient failure: wait, then retry
            time.sleep(random.uniform(0, 2**attempt))
    return call()


def unrelated():
    return 1
