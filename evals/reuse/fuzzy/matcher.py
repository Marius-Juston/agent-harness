def closest(name: str, choices: list[str]) -> str | None:
    """The choice most similar to name, which users often misspell (typos, swapped letters, missing words, other
    word order), ignoring case. None when no choice scores at least 80 on a 0-100 similarity scale."""
    raise NotImplementedError
