from datetime import datetime


def parse_when(text: str) -> datetime:
    """The moment a user typed into a free-text field, in any of the forms people use: ISO 8601 with or without an
    offset, "13 Oct 2026", "October 13, 2026 3pm", "10/13/2026 15:00" (month first). Missing time is midnight."""
    raise NotImplementedError
