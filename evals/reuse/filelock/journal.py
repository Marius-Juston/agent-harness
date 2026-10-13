from pathlib import Path


def append_entry(path: Path, entry: str) -> None:
    """Append entry and a newline to path. Several processes, on Linux, macOS and Windows, append to the same file
    at once and each entry must stay whole; a process that dies must not leave the file blocked for the others."""
    raise NotImplementedError
