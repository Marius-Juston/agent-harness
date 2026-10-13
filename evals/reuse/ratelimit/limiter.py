class Limiter:
    """At most `rate` calls per `per` seconds, safe to share between threads; short bursts up to `rate` are allowed."""

    def __init__(self, rate: int, per: float) -> None:
        raise NotImplementedError

    def try_acquire(self) -> bool:
        """True and use up one call when the limit allows it now; False otherwise, without waiting."""
        raise NotImplementedError
