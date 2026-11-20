"""#592: each recommendation-driving claim records one targeted search for contrary findings or failed replications on
an `against:` line (its query and result), and cite-check checks the line is there. Fixture notes, no network."""

from pathlib import Path

import pytest

from devkit import citecheck, fulltext

PAPERS = Path(__file__).parent / "fixtures" / "papers"
QUOTE = '"ranged from 3 % to 18 % depending only on the verifier\'s strictness"'
CITE = "(SOURCED, [Evaluating Citation Faithfulness](https://arxiv.org/abs/2607.00001), Goo 2026, §3.2)"


def html() -> fulltext.FullText:
    return fulltext.from_markup((PAPERS / "latexml.html").read_text(), "https://arxiv.org/html/2607.00001")


def check(tmp_path, monkeypatch, text: str, changed=None) -> tuple[list[str], list[str]]:
    monkeypatch.setattr(citecheck, "state_dir", lambda *p: tmp_path)
    monkeypatch.setattr(citecheck, "arxiv_titles", lambda ids: dict.fromkeys(ids, "Evaluating Citation Faithfulness"))
    note = tmp_path / "n.md"
    note.write_text(text)
    _, problems, notes = citecheck.check_notes(
        [note], texts=lambda ident: (html(), ""), changed=None if changed is None else {note: changed}
    )
    return problems, notes


@pytest.mark.parametrize(
    "against",
    [
        '\n  against: "verifier strictness citation rate replication" → none contrary in 20 results',
        '\n  - against: `dev lit search "citation verifier failed replication"` -> 1 hit, agrees (Lee 2026)',
    ],
)
def test_a_claim_with_an_against_line_passes(tmp_path, monkeypatch, against):
    problems, _ = check(tmp_path, monkeypatch, f"# Research: x\n\n- The rate moves: {QUOTE} {CITE}.{against}\n")
    assert problems == []


def test_a_claim_without_an_against_line_fails(tmp_path, monkeypatch):
    problems, _ = check(tmp_path, monkeypatch, f"# Research: x\n\n- The rate moves: {QUOTE} {CITE}.\n")
    assert len(problems) == 1 and "2607.00001" in problems[0] and "no `against:` line" in problems[0]


@pytest.mark.parametrize(
    "line",
    [
        "against: none found",  # no query
        'against: "verifier strictness replication"',  # no result
        'against: "verifier strictness replication" →',  # empty result
        "against:",
    ],
)
def test_an_against_line_needs_both_a_query_and_its_result(tmp_path, monkeypatch, line):
    problems, _ = check(tmp_path, monkeypatch, f"# Research: x\n\n- The rate moves: {QUOTE} {CITE}.\n  {line}\n")
    assert len(problems) == 1 and "query and its result" in problems[0]


def test_an_against_line_belongs_to_its_own_claim_only(tmp_path, monkeypatch):
    """A blank line ends a claim: the next paragraph's against line does not cover it."""
    text = (
        f"# Research: x\n\n- First: {QUOTE} {CITE}.\n\n"
        f'- Second: {QUOTE} {CITE}.\n  against: "verifier strictness replication" → none\n'
    )
    problems, _ = check(tmp_path, monkeypatch, text)
    assert len(problems) == 1 and "n.md:3:" in problems[0]


def test_the_against_query_is_not_taken_for_a_later_claims_quote(tmp_path):
    text = (
        f"- First: {QUOTE} {CITE}.\n"
        '  against: "a long contrary search query here" → none\n'
        "  Second (SOURCED, [Other](https://arxiv.org/abs/2601.00009), §2).\n"
    )
    first, second = citecheck.claims(tmp_path / "n.md", text)
    assert first.quote.startswith("ranged from 3 %") and (first.against or "").startswith('"a long contrary')
    assert second.quote == "" and second.against == first.against


def test_a_missing_against_line_on_an_untouched_claim_is_a_note_and_strict_under_all(tmp_path, monkeypatch):
    """#584's scoping: strict for claims on changed lines, a note for untouched ones, strict for every claim with
    `--all` (changed=None)."""
    text = f"# Research: x\n\n- Old: {QUOTE} {CITE}.\n"
    problems, notes = check(tmp_path, monkeypatch, text, changed={1})
    assert problems == [] and any("no `against:` line" in n and "untouched line" in n for n in notes)
    problems, _ = check(tmp_path, monkeypatch, text, changed={3})
    assert len(problems) == 1
    problems, _ = check(tmp_path, monkeypatch, text, changed=None)
    assert len(problems) == 1


def test_a_table_row_records_its_against_search_in_a_cell(tmp_path):
    text = (
        f'| claim | source | against |\n| rate | {QUOTE} {CITE} | against: "verifier strictness replication" → none |\n'
    )
    [c] = citecheck.claims(tmp_path / "n.md", text)
    assert (c.against or "").startswith('"verifier strictness') and citecheck.against_problem(c) == ""


def test_an_against_line_inside_a_code_fence_does_not_count(tmp_path):
    text = f'- Rate: {QUOTE} {CITE}.\n```\nagainst: "q" → r\n```\n'
    [c] = citecheck.claims(tmp_path / "n.md", text)
    assert c.against is None


def test_a_code_fence_does_not_shift_the_line_numbers_of_later_claims(tmp_path):
    """Found in passing: stripping a fenced block dropped its newlines, so a later claim's line number (and with it
    the changed-lines scoping) was off by the fence's length."""
    text = f"# Research: x\n\n```\na\nb\n```\n\n- Rate: {QUOTE} {CITE}.\n"
    [c] = citecheck.claims(tmp_path / "n.md", text)
    assert c.line == 8


PLUGIN = Path(__file__).parents[1] / "plugins" / "harness-ops"


def test_the_researcher_auditor_and_skill_require_the_against_line():
    """DONE WHEN: research-auditor and the research skill require an `against:` line (query and result) on each
    recommendation-driving claim; the researcher writes it in the form cite-check checks."""
    researcher = (PLUGIN / "agents/researcher.md").read_text()
    rule = next(x for x in researcher.splitlines() if "recommendation-driving claim" in x)
    assert citecheck.AGAINST_FORM in rule and "failed replications" in rule
    auditor = (PLUGIN / "agents/research-auditor.md").read_text()
    sample = auditor.split("2. Sample the claims", 1)[1].split("\n3. ", 1)[0]
    assert "an `against:` line (query and result)" in sample
    skill = " ".join((PLUGIN / "skills/research/SKILL.md").read_text().split())
    assert "`uv run dev cite-check` passes on that file, which needs an `against:` line (query and result) per" in skill
    assert "recommendation-driving claim" in skill


# ---- review round 1 on #617 ----

AGAINST = 'against: "verifier strictness replication" → none'


def cite(n: int) -> str:
    return f"{QUOTE} (SOURCED, [T T T T](https://arxiv.org/abs/2607.0000{n}), X 2026, §3.2)"


@pytest.mark.parametrize(
    "first",
    [
        f"| r1 | {cite(1)} | |",  # a table row
        f"- r1 {cite(1)}",  # a list item
        f"1. r1 {cite(1)}",  # a numbered item
    ],
)
def test_a_claim_never_borrows_the_next_rows_against_cell(tmp_path, first):
    """B1: the lookup read the line after a claim, so a table row without an against cell took the next row's."""
    nxt = f"| r2 | {cite(2)} | {AGAINST} |"
    found = citecheck.claims(tmp_path / "n.md", f"{first}\n{nxt}\n")
    assert [(c.line, c.against is None) for c in found] == [(1, True), (2, False)]


@pytest.mark.parametrize(
    "line",
    [f"   1. {AGAINST}", f"> {AGAINST}", f"  - {AGAINST}", f"  {AGAINST}"],
)
def test_numbered_and_blockquote_against_lines_count(tmp_path, line):
    [c] = citecheck.claims(tmp_path / "n.md", f"- Rate {cite(1)}.\n{line}\n")
    assert c.against is not None and citecheck.against_problem(c) == ""


def test_an_against_line_inside_a_tilde_fence_does_not_count(tmp_path):
    [c] = citecheck.claims(tmp_path / "n.md", f"- Rate {cite(1)}.\n~~~\n{AGAINST}\n~~~\n")
    assert c.against is None


def test_a_tilde_fence_does_not_shift_the_line_numbers_of_later_claims(tmp_path):
    [c] = citecheck.claims(tmp_path / "n.md", f"~~~\na\nb\n~~~\n\n- Rate {cite(1)}.\n")
    assert c.line == 6


def test_an_edit_to_only_the_nested_against_line_makes_its_claim_strict(tmp_path, monkeypatch):
    """Nit: a malformed `- against:` added under an old claim was only a note: the claim's lines ended above it."""
    text = f"# Research: x\n\n- Old: {QUOTE} {CITE}.\n  - against: none\n"
    problems, _ = check(tmp_path, monkeypatch, text, changed={4})
    assert len(problems) == 1 and "query and its result" in problems[0]
