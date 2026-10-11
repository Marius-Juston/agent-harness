"""Markdown files with YAML frontmatter (agents, skills, research notes)."""

from pathlib import Path

import yaml


def split(text: str) -> tuple[dict | None, str]:
    """(frontmatter or None, body). Invalid YAML raises ValueError with the reason."""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---", 4)
    if end < 0:
        raise ValueError("frontmatter has no closing '---'")
    try:
        data = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    body = text[end + 4 :].lstrip("\n")
    return data, body


def read(path: Path) -> tuple[dict | None, str]:
    return split(path.read_text(encoding="utf-8"))


def words(text: str) -> int:
    return len(text.split())
