from datetime import datetime


def next_run(expr: str, after: datetime) -> datetime:
    """The first time strictly after `after` that matches expr, a standard five-field crontab expression (minute,
    hour, day of month, month, day of week) with lists, ranges, steps and month/day names."""
    raise NotImplementedError
