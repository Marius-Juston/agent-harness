from pathlib import Path


def read_text_any(path: Path) -> str:
    """The text of a file whose encoding nobody recorded: users upload UTF-8, UTF-16 with a byte-order mark, Windows
    Cyrillic (cp1251) and Shift-JIS files. Detect the encoding from the bytes and decode."""
    raise NotImplementedError
