"""#512: full texts as sections, verbatim quote matching, and cite-check's quote rules. Fixture papers, no network."""

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from devkit import citecheck, cli, fulltext, lit

PAPERS = Path(__file__).parent / "fixtures" / "papers"


def html() -> fulltext.FullText:
    return fulltext.from_markup((PAPERS / "latexml.html").read_text(), "https://arxiv.org/html/2607.00001")


def pdf_bytes(lines: list[str]) -> bytes:
    """A one-page PDF whose text layer holds `lines`, one per text line (enough for pypdf to extract)."""
    esc = [ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for ln in lines]
    stream = "BT /F1 9 Tf 12 TL 40 800 Td " + " ".join(f"({x}) Tj T*" for x in esc) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


PDF_LINES = [
    "Alarm Fatigue Revisited",
    "Abstract",
    "Operators stop trusting automation that raises false alarms.",
    "1 Introduction",
    "Automation that cries wolf is switched off by the people it",
    "should help, a pattern seen across many control rooms.",
    "2 Results",
    "Operators ignored 41 of 60 alarms once the false-alarm rate was high, an evalu-",
    "ation repeated in three plants.",
    "References",
    "B. Author. 2020.",
]


# ---- sections ----


def test_latexml_html_becomes_an_outline_without_navigation_or_scripts():
    ft = html()
    heads = [h for h, _ in ft.sections]
    assert heads[:3] == ["Verifier Strictness and Citation Faithfulness", "Abstract", "1 Introduction"]
    assert "3.2 Results" in heads and "Agreement." in heads and "Appendix A Prompts" in heads
    assert "Report issue" not in ft.whole() and "never text" not in ft.whole() and "LaTeXML" not in ft.whole()
    assert "x-tex" not in ft.whole() and "Abstract (" in ft.outline()


@pytest.mark.parametrize(
    ("locator", "head", "has", "lacks"),
    [
        ("abstract", "Abstract", "strictness of an automated verifier", "Agentic systems read"),
        ("§3.2", "3.2 Results", "0.27 to 0.30", "six verifiers"),  # an unnumbered paragraph heading stays inside
        ("Section 3", "3 Method", "six verifiers", "English computer-science"),  # 3 holds 3.1 and 3.2, not 4
        ("§Limitations", "4 Limitations", "English", "References"),
        ("Appendix A", "Appendix A Prompts", "yes or no", "Limitations"),
    ],
)
def test_a_locator_finds_its_section_and_subsections_only(locator, head, has, lacks):
    found = html().locate(locator)
    assert found is not None and found[0] == head
    assert has in found[1] and lacks not in found[1]


@pytest.mark.parametrize("locator", ["Table 2", "Fig. 3", "p. 7", "§9.9", "§Nonexistent"])
def test_captions_pages_and_missing_sections_are_not_located(locator):
    assert html().locate(locator) is None


def test_jats_titles_inside_sections_are_headings_but_table_captions_are_not():
    ft = fulltext.from_markup((PAPERS / "jats.xml").read_text(), "https://www.ebi.ac.uk/x", "xml")
    heads = [h for h, _ in ft.sections]
    assert heads[1:] == ["Abstract", "Introduction", "Results", "Subgroups", "Discussion", "References"]
    results = ft.locate("Results")
    assert results and "41 of 60" in results[1] and "Table 1. Alarm responses" in results[1]


def test_pdf_sections_and_a_quote_across_a_hyphenated_line_break():
    ft = fulltext.from_pdf(pdf_bytes(PDF_LINES), "https://example.org/a.pdf")
    assert ft is not None and ft.kind == "pdf"
    assert [h for h, _ in ft.sections][1:] == ["Abstract", "1 Introduction", "2 Results", "References"]
    results = ft.locate("§2")
    assert results and fulltext.contains(results[1], "an evaluation repeated in three plants")


def test_a_broken_pdf_is_unreadable_not_a_crash():
    assert fulltext.from_pdf(b"%PDF-1.4 garbage", "https://example.org/b.pdf") is None


# ---- verbatim matching ----


def test_normalising_forgives_layout_not_wording():
    text = "the veriﬁer’s strictness—only the evalu-\n  ation"  # ligature, curly apostrophe, em dash, hyphen break
    assert fulltext.contains(text, "the verifier's strictness-only the evaluation")
    assert fulltext.contains(text, "THE VERIFIER'S   strictness")
    assert not fulltext.contains(text, "the verifier's leniency")


def test_ellipses_and_insertions_split_a_quote_into_ordered_pieces():
    text = "Disuse of automation is commonly caused by alarms that activate falsely."
    assert fulltext.contains(text, "disuse … is commonly caused by alarms that activate falsely")
    assert fulltext.contains(text, "[automation] is commonly caused by ... falsely")
    assert not fulltext.contains(text, "falsely ... Disuse of automation")  # pieces out of order


@settings(max_examples=200, deadline=None)
@given(
    words=st.lists(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=9), min_size=3),
    data=st.data(),
)
def test_any_span_of_the_text_matches_under_any_reflow(words, data):
    lo = data.draw(st.integers(0, len(words) - 1))
    hi = data.draw(st.integers(lo + 1, len(words)))
    seps = data.draw(st.lists(st.sampled_from([" ", "\n", "  ", " \n "]), min_size=len(words), max_size=len(words)))
    text = "".join(w + s for w, s in zip(words, seps, strict=True))
    assert fulltext.contains(text, " ".join(words[lo:hi]))


# ---- claims in a note ----

NOTE = """# Research: x

- Verifier strictness alone moved the rate: "ranged from 3 % to 18 % depending only on the verifier's strictness"
  (SOURCED, Goo et al. 2026, [Evaluating Citation Faithfulness](https://arxiv.org/abs/2607.00001), arXiv, §3.2).
- Alarm fatigue (SOURCED, [Humans and Automation](https://doi.org/10.1518/0018720977(83)), Human Factors 1997,
  abstract: "disuse … is commonly caused by alarms that activate falsely"; BELIEVED, [Other](https://arxiv.org/abs/2601.00002)).
- A side remark (BELIEVED, [Not checked](https://arxiv.org/abs/2601.00003)).
- No quote here (SOURCED, [Some paper](https://arxiv.org/abs/2601.00004), Table 2, `single-source`).
- Docs are not papers (SOURCED, [Claude Code docs](https://code.claude.com/docs/en/sub-agents)).
"""


def test_claims_find_each_sourced_paper_with_its_own_quote_locator_and_tags(tmp_path):
    found = citecheck.claims(tmp_path / "n.md", NOTE)
    by = {c.ident: c for c in found}
    assert set(by) == {"2607.00001", "10.1518/0018720977(83)", "2601.00004"}  # BELIEVED and docs links are skipped
    goo = by["2607.00001"]
    assert goo.locator == "§3.2" and goo.quote.startswith("ranged from 3 %") and goo.line == 3
    alarm = by["10.1518/0018720977(83)"]
    assert alarm.locator == "abstract" and alarm.quote.startswith("disuse")
    bare = by["2601.00004"]
    assert bare.quote == "" and bare.locator == "Table 2" and bare.tags == {"single-source"}


# ---- the rules ----


def claim(quote: str, locator: str, tags: tuple[str, ...] = ()) -> citecheck.Claim:
    return citecheck.Claim(
        Path("note.md"), 3, "https://arxiv.org/abs/2607.00001", "2607.00001", locator, quote, quote, frozenset(tags)
    )


def run(c: citecheck.Claim, ft=None, why="no open-access copy", lenient=False):
    texts = (lambda _id: (ft, why)) if not isinstance(ft, Exception) else _raiser(ft)
    return citecheck.check_quotes([c], texts, lenient)


def _raiser(exc):
    def texts(_id):
        raise exc

    return texts


def test_a_quote_in_its_cited_section_passes_and_counts_as_read_in_full():
    problems, notes, counts = run(claim("ranged from 3 % to 18 % depending only on", "§3.2"), html())
    assert problems == [] and notes == []
    assert counts["note.md"] == {"claims": 1, "quoted": 1, "abstract-only": 0, "in full text": 1}


def test_a_quote_absent_from_the_paper_fails():
    problems, _, _ = run(claim("ranged from 1 % to 90 % regardless of the verifier", "§3.2"), html())
    assert len(problems) == 1 and "quote not found in the full text" in problems[0]


def test_a_quote_from_another_section_fails_for_html_and_is_a_note_for_pdf():
    problems, _, _ = run(claim("We compare six verifiers", "§4"), html())
    assert len(problems) == 1 and "not in '4 Limitations'" in problems[0]
    ft = fulltext.from_pdf(pdf_bytes(PDF_LINES), "https://example.org/a.pdf")
    problems, notes, _ = run(claim("Automation that cries wolf is switched off", "§2"), ft)
    assert problems == [] and "approximate" in notes[0]


def test_a_table_locator_is_checked_against_the_whole_text():
    problems, notes, _ = run(claim("Negative-specific agreement between verifiers", "Table 3"), html())
    assert problems == [] and notes == []


@pytest.mark.parametrize(
    ("quote", "kind"),
    [
        ("unsupported-citation rate ranged from 3 % to 18 %", "numeric"),
        ("We study how the strictness of an automated verifier changes", "method"),
        ("Verifiers disagree on which citations to flag", ""),
    ],
)
def test_the_abstract_cannot_back_a_numeric_method_or_limitation_claim_when_the_paper_is_reachable(quote, kind):
    c = claim(quote, "abstract")
    assert c.kind() == kind
    problems, _, counts = run(c, html())
    if kind:
        assert len(problems) == 1 and f"a {kind} claim rests on the abstract" in problems[0]
    else:
        assert problems == []  # a framing claim the abstract states is fine
    assert counts["note.md"]["abstract-only"] == 1


def test_a_limitation_claim_on_the_abstract_fails_too():
    assert claim("results hold only for English papers", "abstract").kind() == "limitation"


def test_no_reachable_full_text_needs_the_abstract_only_tag():
    problems, _, counts = run(claim("disuse is commonly caused by false alarms", "abstract"))
    assert len(problems) == 1 and "tag the claim `abstract-only`" in problems[0]
    problems, _, counts = run(claim("disuse is commonly caused by false alarms", "abstract", ("abstract-only",)))
    assert problems == [] and counts["note.md"]["abstract-only"] == 1


def test_an_abstract_only_tag_does_not_excuse_skipping_a_reachable_paper():
    problems, _, _ = run(claim("ranged from 3 % to 18 %", "", ("abstract-only",)), html())
    assert len(problems) == 1 and "numeric claim rests on the abstract" in problems[0]


def test_a_missing_quote_or_locator_fails():
    c = claim("", "§3.2")
    problems, _, counts = run(c, html())
    assert "no verbatim quote" in problems[0] and counts["note.md"]["quoted"] == 0
    problems, _, _ = run(claim("ranged from 3 % to 18 %", ""), html())
    assert "no locator" in problems[0]


def test_an_unreachable_source_is_a_problem_or_a_note_when_lenient():
    c = claim("ranged from 3 % to 18 %", "§3.2")
    problems, _, counts = run(c, lit.SourceError("arxiv: HTTP 503 after 2 tries"))
    assert "not reachable now" in problems[0] and counts["note.md"]["abstract-only"] == 0
    problems, notes, _ = run(c, lit.SourceError("arxiv: HTTP 503 after 2 tries"), lenient=True)
    assert problems == [] and "not reachable now" in notes[0]


def test_each_source_is_read_once_per_run():
    calls = []

    def texts(ident):
        calls.append(ident)
        return html(), ""

    citecheck.check_quotes([claim("six verifiers", "§3.1"), claim("0.27 to 0.30", "§3.2")], texts)
    assert calls == ["2607.00001"]


def test_cite_check_reports_quote_problems_and_counts_per_note(tmp_path, monkeypatch):
    monkeypatch.setattr(citecheck, "state_dir", lambda *p: tmp_path)
    crossref = json.dumps({"message": {"title": ["Humans and Automation"]}})
    monkeypatch.setattr(citecheck, "_get", lambda url, timeout=20: (200, {}, crossref if "crossref" in url else ""))
    monkeypatch.setattr(citecheck, "arxiv_titles", lambda ids: dict.fromkeys(ids, "Evaluating Citation Faithfulness"))
    note = tmp_path / "n.md"
    note.write_text(NOTE)
    _, problems, notes = citecheck.check_notes([note], texts=lambda ident: (html(), ""))
    assert any("2601.00004" in p and "no verbatim quote" in p for p in problems)
    assert any("1518" in p and "quote not found" in p for p in problems)  # the fixture paper says no such thing
    assert "n.md: 3 claims, 2 quoted, 1 abstract-only, 2 in full text (SOURCED paper claims)" in notes


# ---- fetching and caching ----


class Net:
    def __init__(self, answers: dict[str, tuple[int, bytes]]):
        self.answers, self.calls = answers, []

    def __call__(self, url, headers):
        self.calls.append(url)
        status, body = self.answers.get(url, (404, b""))
        return status, {}, body


def client(tmp_path, net: Net, get=None) -> lit.Client:
    clock = [1_000_000.0]
    return lit.Client(
        tmp_path,
        get=get or (lambda url, headers: (404, {}, "")),
        get_bytes=net,
        clock=lambda: clock[0],
        sleep=lambda s: clock.__setitem__(0, clock[0] + s),
        env={},
    )


def long_html() -> bytes:
    filler = "<p>" + "padding words for a full paper " * 250 + "</p>"
    return (PAPERS / "latexml.html").read_text().replace("</article>", filler + "</article>").encode()


def test_arxiv_html_is_read_once_then_served_from_the_cache(tmp_path):
    net = Net({"https://arxiv.org/html/2607.00001": (200, long_html())})
    ft, why = client(tmp_path, net).full_text("2607.00001")
    assert ft and why == "" and ft.locate("3.2")
    again, _ = client(tmp_path, Net({})).full_text("arXiv:2607.00001")  # another worktree, the id's other spelling
    assert again and again.sections == ft.sections and net.calls == ["https://arxiv.org/html/2607.00001"]


def test_without_arxiv_html_the_pdf_is_read(tmp_path):
    body = pdf_bytes(PDF_LINES * 40)
    net = Net({"https://arxiv.org/pdf/2607.00001": (200, body)})
    ft, _ = client(tmp_path, net).full_text("2607.00001")
    assert ft and ft.kind == "pdf"
    assert net.calls == ["https://arxiv.org/html/2607.00001", "https://arxiv.org/pdf/2607.00001"]


def test_an_abstract_page_is_not_a_full_text_and_none_is_cached(tmp_path):
    net = Net({"https://arxiv.org/html/2607.00001": (200, (PAPERS / "latexml.html").read_bytes())})
    ft, why = client(tmp_path, net).full_text("2607.00001")
    assert ft is None and "not a full paper" in why and "HTTP 404" in why
    assert client(tmp_path, Net({})).full_text("2607.00001") == (None, why)  # cached: no second fetch


def test_a_transient_answer_raises_and_is_not_cached(tmp_path):
    net = Net({"https://arxiv.org/html/2607.00001": (503, b"")})
    with pytest.raises(lit.SourceError, match="503 after 2 tries"):
        client(tmp_path, net).full_text("2607.00001")
    assert len(net.calls) == 2 and not list((tmp_path / "fulltext").glob("*.json"))


def test_a_doi_reads_europe_pmc_xml_for_a_pmc_copy(tmp_path):
    record = {"results": [{"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1000/x", "title": "Alarm",
                           "best_oa_location": {"pdf_url": "https://europepmc.org/article/PMC/PMC123"},
                           "abstract_inverted_index": {"Alarms.": [0]}}]}  # fmt: skip

    def get(url, headers):
        return (200, {}, json.dumps(record)) if "openalex" in url else (404, {}, "")

    xml = (PAPERS / "jats.xml").read_text().replace("</body>", "<p>" + "word " * 1000 + "</p></body>")
    pmc = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC123/fullTextXML"
    net = Net({pmc: (200, xml.encode())})
    ft, _ = client(tmp_path, net, get).full_text("10.1000/x")
    assert ft and ft.kind == "xml" and net.calls == [pmc] and ft.locate("Results")


def test_a_doi_without_an_open_access_copy_has_no_full_text(tmp_path):
    record = {"results": [{"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1000/y", "title": "Closed",
                           "abstract_inverted_index": {"Closed.": [0]}}]}  # fmt: skip
    ft, why = client(tmp_path, Net({}), lambda u, h: (200, {}, json.dumps(record))).full_text("10.1000/y")
    assert ft is None and why == "no open-access copy"


def test_lit_read_prints_the_outline_or_one_section(tmp_path, monkeypatch, capsys):
    net = Net({"https://arxiv.org/html/2607.00001": (200, long_html())})
    monkeypatch.setattr(lit, "client", lambda: client(tmp_path, net))
    assert cli.run(cli.build().parse_args(["lit", "read", "2607.00001"])) == 0
    out = capsys.readouterr().out
    assert "3.2 Results (" in out and "0.27" not in out  # the outline only, never the text
    assert cli.run(cli.build().parse_args(["lit", "read", "2607.00001", "--section", "3.2"])) == 0
    out = capsys.readouterr().out
    assert "0.27 to 0.30" in out and "six verifiers" not in out
    assert cli.run(cli.build().parse_args(["lit", "read", "2607.00001", "--section", "9"])) == 1


# ---- regressions found running cite-check over docs/research (#512) ----


def test_latexml_zero_width_spaces_do_not_break_a_quote():
    """harness-efficiency.md:155: arXiv HTML renders `KN` as `K​N`."""
    assert fulltext.contains("a pooled standard error across all K​N answers", "across all KN answers")


def test_a_quote_before_an_earlier_citation_belongs_to_that_citation(tmp_path):
    """software-discovery.md:277: the uv docs' quote was checked against the Guo paper cited after it."""
    text = (
        '- `--no-build`: "operations that require building a source distribution will exit with an error" '
        "([uv settings](https://docs.astral.sh/uv/reference/settings/), SOURCED). That matters because 74.81% of "
        "files reached users through source installation ([Guo 2023](https://doi.org/10.1109/ase.2023.1), abstract, "
        "SOURCED).\n"
    )
    [guo] = citecheck.claims(tmp_path / "n.md", text)
    assert guo.quote == "" and guo.locator == "abstract"


def test_each_table_row_is_its_own_claim(tmp_path):
    """harness-efficiency.md:134: words quoted in one table row were charged to a paper cited in another."""
    text = (
        '| 1 | "reconsider the approach" after round 3 | BELIEVED |\n'
        "| 2 | a row with a paper | SOURCED, [P](https://arxiv.org/abs/2609.00001), §2 |\n"
    )
    [row] = citecheck.claims(tmp_path / "n.md", text)
    assert row.quote == "" and row.line == 2
