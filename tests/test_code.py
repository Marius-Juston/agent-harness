"""dev code search (#545): BM25 over function and method units, tests left out unless asked, one card per hit."""

import json
import subprocess
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import cli, code

FILES = {
    "pkg/network.py": '''
import time


def fetch_with_retry(url: str, attempts: int = 3) -> bytes:
    """Fetch a URL, retrying transient network errors with exponential backoff."""
    for attempt in range(attempts):
        time.sleep(2**attempt)
    return b""


class Cache:
    def evict(self, key):
        """Drop one entry from the response cache."""
        return key
''',
    "pkg/report.py": '''
def render_table(rows):
    """Format rows as a Markdown table."""
    return "|".join(rows)


def render_chart(rows):
    """Draw rows as a bar chart."""
    return rows


def render_list(rows):
    """Format rows as a bullet list."""
    return rows
''',
    "tests/test_network.py": '''
def test_fetch_with_retry_retries_transient_network_errors():
    """Retrying transient network errors with backoff."""
''',
    "docs/scripts/tool.py": '''
def fetch_with_retry_in_docs():
    """Retrying transient network errors with backoff."""
''',
    "pkg/broken.py": "def oops(:\n",
}


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    repo = tmp_path / "proj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    for rel, text in FILES.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text.lstrip("\n"))
    monkeypatch.chdir(repo)
    return repo


def test_search_ranks_the_function_the_words_describe_first_as_one_line_cards(repo):
    out = code.search("retry transient network errors").splitlines()
    assert out[0] == (
        "pkg/network.py:4  fetch_with_retry(url: str, attempts: int=3) -> bytes"
        "  # Fetch a URL, retrying transient network errors with exponential backoff."
    )
    assert code.search("cache entry")[: len("pkg/network.py:12  Cache.evict(self, key)")] == (
        "pkg/network.py:12  Cache.evict(self, key)"
    )


def test_search_leaves_out_tests_and_docs_unless_asked(repo):
    plain = code.search("retry transient network errors", n=10)
    assert "tests/" not in plain
    assert "docs/" not in plain
    with_tests = code.search("retry transient network errors", n=10, tests=True)
    assert "tests/test_network.py:1  test_fetch_with_retry_retries_transient_network_errors()" in with_tests
    assert "docs/" not in with_tests


def test_search_prints_at_most_n_cards_and_five_by_default(repo):
    (repo / "pkg/many.py").write_text("".join(f"def render_{i}(rows):\n    return rows\n\n" for i in range(9)))
    assert len(code.search("render rows").splitlines()) == 5
    assert len(code.search("render rows", n=2).splitlines()) == 2


def test_search_says_so_when_no_function_shares_a_word(repo):
    assert code.search("zebra quantum").startswith("no function matches 'zebra quantum'")
    assert code.search("the of and").startswith("no function matches")  # stop words only


def test_search_sees_untracked_and_edited_files_and_survives_a_corrupt_cache(repo):
    assert "pkg/network.py" in code.search("evict cache")
    (repo / "pkg/network.py").write_text('def purge_everything():\n    """Wipe the store."""\n')
    assert "purge_everything" in code.search("wipe store")
    assert "Cache.evict" not in code.search("evict cache")
    cache = next((repo / ".git/devkit/code-search").glob("units.json"))
    cache.write_text("{not json")
    assert "purge_everything" in code.search("wipe store")
    assert isinstance(json.loads(cache.read_text()), dict)  # rewritten whole


def test_search_parses_each_file_once_then_reads_the_cache(repo, monkeypatch):
    code.search("render rows")
    monkeypatch.setattr(code, "_parse_units", lambda text: pytest.fail("parsed again"))
    assert code.search("render rows").startswith("pkg/report.py")


def test_cli_code_search_joins_its_words(repo, capsys):
    assert cli.run(cli.build().parse_args(["code", "search", "bar", "chart", "-n", "1"])) in (0, None)
    assert capsys.readouterr().out.startswith("pkg/report.py:6  render_chart(rows)  # Draw rows as a bar chart.")


@pytest.mark.parametrize(
    ("path", "test"),
    [
        ("tests/test_x.py", True),
        ("evals/fixtures/a.py", True),
        ("pkg/tests/helper.py", True),
        ("pkg/test_thing.py", True),
        ("conftest.py", True),
        ("pkg/testing.py", False),
        ("devkit/contest.py", False),
        ("tests.py", False),
    ],
)
def test_is_test(path, test):
    assert code.is_test(path) is test


VOCAB = ["retry", "network", "cache", "table", "chart", "render", "parse", "board"]


@given(
    st.lists(st.lists(st.sampled_from(VOCAB), max_size=12), max_size=12),
    st.lists(st.sampled_from([*VOCAB, "the", "zebra", "of"]), max_size=6),
)
def test_rank_keeps_only_units_sharing_a_query_word_best_first(docs, query):
    from devkit.board import bm25

    pool = [code.Unit(f"m{i}.py", 1, f"f{i}", "()", "", tuple(d)) for i, d in enumerate(docs)]
    out = code.rank(pool, " ".join(query))
    assert {u.path for u in out} == {u.path for u in pool if set(u.words) & set(query)}
    scores = dict(
        zip([u.path for u in pool], bm25([list(u.words) for u in pool], code.query_words(" ".join(query))), strict=True)
    )
    assert [scores[u.path] for u in out] == sorted((scores[u.path] for u in out), reverse=True)
