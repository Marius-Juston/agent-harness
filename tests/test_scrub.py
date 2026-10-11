"""This repository is public: no string from the private project it was extracted from, and nothing private.

The patterns are assembled from pieces so this file does not match itself. Every file the next commit would hold is
scanned (tracked plus untracked-not-ignored), including the template and the plugin.
"""

import re
import subprocess

import pytest

from devkit.paths import root

FORBIDDEN = {
    "the source project's name": r"\bthe" + r"[ _-]?eye\b",
    "the source project's domain words": r"\b(?:socr"
    + r"atic|tut"
    + r"or(?:ing|s)?|lear"
    + r"ners?|teac"
    + r"hers?|pedag"
    + r"og\w*)\b",
    "the source project's users": r"\bchi" + r"ld(?:ren)?\b",
    "private sibling repositories": r"dnd" + r"lag|printing" + r"robot|chron" + r"icon",
    "session links": r"claude\.ai/code/" + r"session|session_0[0-9A-Za-z]{6,}",
    "home directories": r"/home/" + r"\w+|/Users/" + r"\w+",
    "email addresses (only example and noreply ones are allowed)": (
        r"\b(?!noreply@|no-reply@|git@github\.com|dev@example|me@x\.io)[\w.+-]+@(?!example\.)[\w-]+\.(?:com|org|net|io|edu)\b"
    ),
    "other repositories of the owner": r"Marius-" + r"Juston/(?!agent-harness\b)",
    "private research of the source project": r"harness-red" + r"team|model-" + r"gateways|memory-and-" + r"retention",
}


def files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root(),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [f for f in out.split("\0") if f and (root() / f).is_file()]


def test_the_scan_sees_the_whole_repository():
    seen = files()
    assert len(seen) > 50
    assert any(f.startswith("template/") for f in seen) and any(f.startswith("plugins/") for f in seen)


@pytest.mark.parametrize("what", sorted(FORBIDDEN))
def test_no_private_or_source_project_string(what):
    pattern = re.compile(FORBIDDEN[what], re.I)
    hits = []
    for f in files():
        try:
            text = (root() / f).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits += [
            f"{f}:{n}: {line.strip()[:120]}" for n, line in enumerate(text.splitlines(), 1) if pattern.search(line)
        ]
        if pattern.search(f):
            hits.append(f"{f}: (file name)")
    assert not hits, f"{what}:\n" + "\n".join(hits[:40])


@pytest.mark.parametrize(
    ("what", "sample"),
    [
        ("the source project's name", "The" + "Eye agent guide"),
        ("the source project's users", "they are chi" + "ldren"),
        ("email addresses (only example and noreply ones are allowed)", "write to someone@" + "gmail.com"),
        ("other repositories of the owner", "gh:Marius-" + "Juston/Other"),
        ("home directories", "/home/" + "someone/x"),
    ],
)
def test_the_patterns_catch_what_they_name(what, sample):
    assert re.search(FORBIDDEN[what], sample, re.I)


def test_the_patterns_allow_this_repository():
    assert not re.search(FORBIDDEN["other repositories of the owner"], "gh:Marius-" + "Juston/agent-harness")
    assert not re.search(FORBIDDEN["email addresses (only example and noreply ones are allowed)"], "noreply@x.com")
