"""dev research search (#589): BM25 over note sections, labelled claims and cached full texts, one line per hit."""

import json
import time

from hypothesis import given, settings
from hypothesis import strategies as st

from devkit import cli, research
from devkit.board import bm25, terms
from devkit.paths import root

NOTE = """# Research: flaky tests

Status: v1. Labels: VERIFIED = measured; SOURCED = cited; BELIEVED = judgment; ASSUMED = unchecked.

## 0. Findings in one paragraph
Reruns hide flaky tests. Quarantine beats retry for order-dependent tests.

## 2. Evidence
- Order-dependent tests were 59 % of flaky tests (SOURCED, [An Empirical Analysis of Flaky Tests](https://doi.org/10.1145/2635868.2635920), Luo 2014 FSE).
- A plain line about the weather.

```text
# not a heading inside a fence
```
"""


def write_tree(tmp_path, papers=()):
    notes = tmp_path / "docs" / "research"
    (notes / "_scratch" / "round").mkdir(parents=True)
    (notes / "flaky.md").write_text(NOTE)
    (notes / "INDEX.md").write_text("# Index\nflaky tests flaky tests flaky tests\n")
    (notes / "_scratch" / "round" / "r1.md").write_text("# Round\nflaky tests in the round's scratch\n")
    texts = tmp_path / "fulltext"
    texts.mkdir()
    for i, rec in enumerate(papers):
        (texts / f"{i}.json").write_text(json.dumps(rec))
    return notes, texts


def test_note_units_are_sections_and_labelled_claims():
    units = research.note_units("n.md", NOTE)
    where = [(u.where, u.label) for u in units]
    assert ("n.md#Research: flaky tests", "") in where
    assert ("n.md#0. Findings in one paragraph", "") in where
    assert ("n.md#2. Evidence", "SOURCED") in where
    assert ("n.md#2. Evidence", "") in where
    assert not any("not a heading" in u.where for u in units)  # a fenced '#' line is body text
    claims = [u for u in units if u.label]
    assert len(claims) == 1  # the Labels: legend is not a claim
    assert claims[0].cites == "An Empirical Analysis of Flaky Tests"
    assert "doi.org" not in claims[0].text  # URLs are not indexed, link text is


def test_paper_ids_from_full_text_urls():
    assert research.paper_id("https://arxiv.org/html/2605.22526") == "arXiv:2605.22526"
    assert research.paper_id("https://arxiv.org/pdf/2407.09726v2") == "arXiv:2407.09726"
    assert research.paper_id("https://europepmc.org/api/fulltextRepo?pprId=PMC123456&type=FILE") == "PMC123456"
    assert research.paper_id("https://doi.org/10.1007/s10664-023-10397-6") == "doi:10.1007/s10664-023-10397-6"
    assert research.paper_id("https://example.org/paper") == "https://example.org/paper"


def test_paper_units_skip_back_matter_and_none_records():
    rec = {
        "url": "https://arxiv.org/html/2501.00001",
        "kind": "html",
        "sections": [["", "Front"], ["3 Method", "We rerun flaky tests."], ["References", "Flaky tests, 2014."]],
    }
    units = research.paper_units(rec)
    assert [u.where for u in units] == ["arXiv:2501.00001 §(front matter)", "arXiv:2501.00001 §3 Method"]
    assert research.paper_units({"t": 1, "none": "no open-access copy"}) == []


def test_search_ranks_notes_and_papers_and_caps_hits(tmp_path):
    paper = {"url": "https://arxiv.org/abs/2501.00001", "sections": [["4 Results", "Quarantine of flaky tests. " * 3]]}
    notes, texts = write_tree(tmp_path, [paper, {"t": 0, "none": "404"}, "not a record"])
    (texts / "broken.json").write_text("{")
    pool = research.units(notes, texts, tmp_path)
    assert not any("INDEX.md" in u.where or "_scratch" in u.where for u in pool)
    assert any("_scratch" in u.where for u in research.units(notes, texts, tmp_path, scratch=True))
    hits = research.search(pool, "quarantine flaky tests", n=2)
    assert len(hits) == 2
    out = research.render(research.search(pool, "quarantine flaky tests", n=10), "quarantine flaky tests").splitlines()
    assert any(line.startswith("arXiv:2501.00001 §4 Results: ") for line in out)
    assert any(line.startswith("docs/research/flaky.md#0. Findings in one paragraph: ") for line in out)
    assert research.render(research.search(pool, "zebra"), "zebra").startswith("no hits")


def test_a_claim_hides_its_section_when_both_show_the_same_sentence(tmp_path):
    notes, texts = write_tree(tmp_path)
    out = research.render(research.search(research.units(notes, texts, tmp_path), "order-dependent 59", 10), "59")
    evidence = [line for line in out.splitlines() if "#2. Evidence" in line]
    assert evidence == [
        "docs/research/flaky.md#2. Evidence [SOURCED]: Order-dependent tests were 59 % of flaky tests (SOURCED, An "
        "Empirical Analysis of Flaky Tests, Luo 2014 FSE). (cites: An Empirical Analysis of Flaky Tests)"
    ]


def test_the_line_is_the_sentence_sharing_most_query_words():
    text = "Intro sentence here. Reruns hide flaky tests. Quarantine beats retry for order-dependent tests."
    assert research.best_sentence(text, set(terms("quarantine retry"))) == (
        "Quarantine beats retry for order-dependent tests."
    )
    assert research.best_sentence(text, {"zebra"}) == "Intro sentence here."


VOCAB = ["flaky", "test", "gate", "merge", "queue", "bm25", "cache", "paper", "note", "claim"]
texts_st = st.lists(st.lists(st.sampled_from([*VOCAB, "#12", "[x]", "`code`"]), max_size=30).map(" ".join), max_size=12)


@settings(max_examples=150, deadline=None)
@given(texts_st, st.lists(st.sampled_from(VOCAB), min_size=1, max_size=4).map(" ".join), st.booleans())
def test_search_ranks_as_bm25_over_every_unit_tokenized(texts, query, warm):
    """The substring prefilter and the cached term counts change no score: same hits, same order as the plain run."""
    pool = [research.Unit(f"u{i}", t) for i, t in enumerate(texts)]
    scores = bm25([terms(u.text) for u in pool], terms(query))
    plain = [pool[i] for i in sorted(range(len(pool)), key=lambda i: -scores[i]) if scores[i] > 0]
    lengths: dict[str, int] = {}
    if warm:
        research.search(pool, "unrelated words", research.MAX_N, lengths)
        assert set(lengths) == {research.digest(u.text) for u in pool}
    assert research.search(pool, query, research.MAX_N, lengths) == plain[: research.MAX_N]


@settings(max_examples=100, deadline=None)
@given(texts_st, texts_st, st.lists(st.sampled_from(VOCAB), min_size=1, max_size=4).map(" ".join))
def test_cached_papers_never_reorder_the_notes(notes, papers, query):
    """Full texts are their own corpus: adding them keeps the notes' hits in the same order."""
    pool = [research.Unit(f"n{i}", t) for i, t in enumerate(notes)]
    alone = research.search(pool, query, research.MAX_N)
    mixed = research.search(pool + [research.Unit(f"p{i}", t, paper=True) for i, t in enumerate(papers)], query, 99)
    assert [u for u in mixed if not u.paper] == alone


def test_words_matches_board_terms():
    for text in ["Flaky `tests` #12 and repo#3", "- [x] done item", "plain words only, CPU-bound"]:
        assert research.words(text) == terms(text)


def test_cli_on_this_repo_is_cold_under_two_seconds(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "state_dir", lambda *parts: tmp_path.joinpath(*parts))  # no cached counts, no papers
    (tmp_path / "research-search").mkdir()
    started = time.perf_counter()
    rc = cli.run(cli.parse(["research", "search", "gate", "wall", "time", "CPU", "contention", "-n", "5"]))
    took = time.perf_counter() - started
    lines = capsys.readouterr().out.splitlines()
    assert rc in (0, None)
    assert took < 2.0, took
    assert 1 <= len(lines) <= 5
    research_dir = (root() / "docs" / "research").relative_to(root()).as_posix()
    assert all(line.startswith(research_dir + "/") and "#" in line for line in lines)
    assert (tmp_path / "research-search" / "lengths.json").exists()
