def set_key(text: str, dotted_key: str, value: object) -> str:
    """text, a YAML document, with the value at dotted_key ("a.b.c") replaced or added. Everything else is kept
    byte for byte where possible: comments, key order, blank lines and quoting style."""
    raise NotImplementedError
