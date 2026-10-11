"""Retry waits shared by `dev lit`, `dev cite-check` and the gh wrapper (#25).

- full jitter: the n-th retry sleeps uniform(0, min(cap, base * 2**n)), so clients that failed together do not retry
  together (aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter);
- a server's own wait wins: Retry-After as delay-seconds or an HTTP-date (RFC 9110 10.2.3), else x-ratelimit-reset
  (epoch seconds) when x-ratelimit-remaining is 0 (GitHub's convention); a hinted wait gets a 0-SPREAD extra, so the
  waiters a shared hint released do not wake in the same instant;
- an escalating shared cooldown: consecutive throttles within WINDOW double it (base, 2 base, 4 base ... capped);
  the caller stores the streak (lit keeps it in its locked bucket file) and clears it on a success.

Pure functions plus one loop; every clock, sleep and random source is injected, so tests are deterministic.
"""

import email.utils
import random
from collections.abc import Callable

BASE = 2.0  # seconds: the first retry's jitter window
CAP = 60.0  # seconds: no jitter window grows past this
SPREAD = 0.2  # a hinted wait or a shared cooldown's expiry is stretched by uniform(0, SPREAD) of itself per waiter
WINDOW = 600.0  # seconds: a throttle this long after the last one starts a new streak
COOLDOWN_CAP = 600.0  # seconds: the longest escalated cooldown (longer than any caller's MAX_WAIT: it then skips)


def full_jitter(attempt: int, rng: random.Random, base: float = BASE, cap: float = CAP) -> float:
    """Seconds to sleep before retry number `attempt` (0 = the first retry): uniform(0, min(cap, base * 2**attempt))."""
    return rng.uniform(0.0, min(cap, base * 2 ** min(attempt, 62)))


def spread(seconds: float, rng: random.Random) -> float:
    """`seconds` stretched by a random 0-SPREAD fraction: a hint is a lower bound, never shortened."""
    return seconds * (1.0 + rng.uniform(0.0, SPREAD))


def hinted(headers: dict[str, str], now: float) -> float:
    """Seconds the server asked to wait (0 = no usable hint): Retry-After (delay-seconds or HTTP-date), else
    x-ratelimit-reset when x-ratelimit-remaining is 0. Header names are case-insensitive."""
    h = {k.lower(): v.strip() for k, v in headers.items()}
    if (ra := h.get("retry-after")) is not None:
        try:
            return max(0.0, float(ra))
        except ValueError:
            pass
        try:
            when = email.utils.parsedate_to_datetime(ra)
        except (TypeError, ValueError, IndexError):
            when = None
        if when is not None and when.tzinfo is not None:
            return max(0.0, when.timestamp() - now)
    if h.get("x-ratelimit-remaining") == "0" and (reset := h.get("x-ratelimit-reset")):
        try:
            return max(0.0, float(reset) - now)
        except ValueError:
            pass
    return 0.0


def wait(
    attempt: int, hint: float, rng: random.Random, base: float = BASE, cap: float = CAP, floor: float = 0.0
) -> float:
    """One retry's sleep: the server's hint (spread), else floor + full jitter."""
    return spread(hint, rng) if hint > 0 else floor + full_jitter(attempt, rng, base, cap)


def escalated(streak: int, base: float = BASE, cap: float = COOLDOWN_CAP) -> float:
    """The shared cooldown after `streak` consecutive throttles (1 -> base, 2 -> 2 base, ... capped)."""
    return min(cap, base * 2 ** min(max(streak, 1) - 1, 62))


def retry[T](
    call: Callable[[], T],
    hint: Callable[[T], float | None],
    *,
    tries: int,
    sleep: Callable[[float], None],
    rng: random.Random,
    base: float = BASE,
    cap: float = CAP,
    floor: float = 0.0,
    max_wait: float = float("inf"),
    on_wait: Callable[[float], None] | None = None,
) -> T:
    """`call()` up to `tries` times. `hint(result)` is None when the result is final, else the seconds the server asked
    for (0 = none). Returns the last result; gives up early when a wait would exceed `max_wait`."""
    for attempt in range(tries):
        res = call()
        h = hint(res)
        if h is None or attempt + 1 >= tries:
            return res
        w = wait(attempt, h, rng, base, cap, floor)
        if w > max_wait:
            return res
        if on_wait:
            on_wait(w)
        sleep(w)
    raise ValueError("tries must be at least 1")
