def to_markdown(html: str) -> str:
    """html (pasted from arbitrary web pages, so often malformed) as Markdown: ATX headings, **bold**, *italic*,
    [links](url), "- " bullet lists and paragraphs separated by a blank line. Scripts and styles are dropped."""
    raise NotImplementedError
