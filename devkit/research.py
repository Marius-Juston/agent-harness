"""`uv run dev research search "<words>"` (#589): what earlier research already established, before searching again.

BM25 (devkit.board.bm25, Lucene's defaults) over three kinds of unit, one line per hit, best first:

- a note section: each heading of a note in research_dir and the text up to the next heading (`path#heading`);
- a labelled claim: a line of a note carrying VERIFIED, SOURCED, BELIEVED or ASSUMED, shown with its label and the
  title of the first source it links (a legend such as `Labels: VERIFIED = ...` is not a claim);
- a section of a full text `dev lit read` cached (lit.cached_full_texts reads them through fulltext.from_record, so
  the record format has one reader), shown as the paper's id (arXiv id, PMC id, DOI, else its URL) and the section heading.

Each hit's line is the unit's sentence sharing most query words, so the line says why it matched. Round scratch files
(`_scratch/`) are searched only with --scratch: the synthesized notes supersede them. Notes and full texts are
re-read each call; only units holding a query word are tokenized, the others' term counts come from a cache keyed by
text digest in <git-common-dir>/devkit/research-search/ (see `search`). A paper's references and acknowledgements
are left out: they match on cited titles, not on what the paper found.
"""

import contextlib
import hashlib
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from devkit.board import STOP_WORDS, WORD, bm25, terms
from devkit.fulltext import BACK_MATTER, FullText

DEFAULT_N, MAX_N = 8, 50
NOTE_SHARE = 2  # notes per full-text section in the merged list (see `search`)
WIDTH = 180  # characters of the matched sentence shown per hit
LABELS = ("VERIFIED", "SOURCED", "BELIEVED", "ASSUMED")
LABEL = re.compile(r"\b(" + "|".join(LABELS) + r")\b")
LEGEND = re.compile(LABEL.pattern + r"\s*=")  # "Labels: VERIFIED = measured; ..." defines them, claims nothing
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)")
SENTENCE = re.compile(r"(?<=[.!?;])\s+(?=[A-Z(`*\[])|\n+")
ARXIV = re.compile(r"arxiv\.org/(?:abs|html|pdf)/([a-z-]*/?\d{4}\.\d{4,5}|[a-z-]+/\d{7})", re.I)
PMC = re.compile(r"\b(PMC\d+)\b")
DOI = re.compile(r"\b(10\.\d{4,9}/[^\s?#]+?)(?:\.pdf)?$", re.I)


@dataclass(frozen=True)
class Unit:
    where: str  # "path#heading" or "paper-id §heading"
    text: str  # what BM25 indexes and the shown sentence comes from
    label: str = ""  # a claim's label
    cites: str = ""  # a claim's first linked source title
    paper: bool = False  # a cached full text's section, ranked in its own corpus (see `search`)

    def line(self, query: set[str], width: int = WIDTH) -> str:
        head = self.where + (f" [{self.label}]" if self.label else "")
        tail = f" (cites: {self.cites})" if self.cites else ""
        return f"{head}: {_clip(best_sentence(self.text, query), width)}{tail}"


def _clip(s: str, width: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= width else s[: width - 3].rstrip() + "..."


def _plain(md: str) -> str:
    """Markdown as read: link text kept, URLs dropped (a path or host would match on its words, not its topic)."""
    return LINK.sub(r"\1", md).replace("**", "").replace("`", "")


def words(text: str) -> list[str]:
    """board.terms, without its `#N` and checkbox passes when the text has no `#` or `[` (most full texts): those
    regexes cost more than the tokenizing on a megabyte of papers, and the result is the same."""
    if "#" in text or "[" in text:
        return terms(text)
    return [w for w in WORD.findall(text.lower().replace("`", " ")) if w not in STOP_WORDS]


def best_sentence(text: str, query: set[str]) -> str:
    """The sentence of `text` sharing most distinct words with `query` (the first on a tie, the first one when none)."""
    parts = [p.strip().lstrip("-*>+ ") for p in SENTENCE.split(text)]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    return max(enumerate(parts), key=lambda ip: (len(query & set(words(ip[1]))), -ip[0]))[1]


def note_units(rel: str, md: str) -> list[Unit]:
    """A note's sections (heading and body; text before the first heading is `path#`) and labelled claims."""
    found: list[Unit] = []
    heading, body, fenced = "", [], False

    def close() -> None:
        text = _plain("\n".join(body)).strip()
        if heading or text:
            found.append(Unit(f"{rel}#{heading}", (heading + "\n" + text).strip()))

    for raw in md.splitlines():
        if FENCE.match(raw):
            fenced = not fenced
        m = None if fenced else HEADING.match(raw)
        if m:
            close()
            heading, body = _plain(m.group(2)), []
            continue
        body.append(raw)
        if fenced or LEGEND.search(raw):
            continue
        if label := LABEL.search(raw):
            link = LINK.search(raw)
            found.append(
                Unit(f"{rel}#{heading}", _plain(raw).strip(" -*>"), label.group(1), link.group(1) if link else "")
            )
    close()
    return found


def paper_id(url: str) -> str:
    """A short id for a full text's URL: arXiv id, PMC id or DOI when the URL holds one, else the URL."""
    for pattern, prefix in ((ARXIV, "arXiv:"), (PMC, ""), (DOI, "doi:")):
        if m := pattern.search(url):
            return prefix + m.group(1)
    return url


def paper_units(ft: FullText) -> list[Unit]:
    """Each section of one cached full text, back matter left out."""
    pid = paper_id(ft.url)
    units = []
    for heading, text in ft.sections:
        if text.strip() and not BACK_MATTER.search(heading):
            units.append(Unit(f"{pid} §{heading or '(front matter)'}", f"{heading}\n{text}".strip(), paper=True))
    return units


def notes(research_dir: Path, scratch: bool = False) -> Iterable[Path]:
    for path in sorted(research_dir.rglob("*.md")):
        rel = path.relative_to(research_dir)
        if path.name == "INDEX.md" and len(rel.parts) == 1:
            continue  # generated from the notes' own headings
        if not scratch and rel.parts[0] == "_scratch":
            continue
        yield path


def units(research_dir: Path, papers: Iterable[FullText], base: Path, scratch: bool = False) -> list[Unit]:
    """Every unit: notes under `research_dir` (paths shown relative to `base`), then the sections of `papers` (the
    full texts lit.cached_full_texts reads)."""
    found: list[Unit] = []
    for path in notes(research_dir, scratch):
        found += note_units(path.relative_to(base).as_posix(), path.read_text(encoding="utf-8", errors="replace"))
    for ft in papers:
        found += paper_units(ft)
    return found


def digest(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8", "replace"), digest_size=12).hexdigest()


def search(pool: list[Unit], query: str, n: int = DEFAULT_N, lengths: dict[str, int] | None = None) -> list[Unit]:
    """At most `n` of `pool` by BM25 score for `query`, best first (ties in pool order); none sharing no word, and a
    section and its claim showing the same sentence merged into one line, the claim's, at the better rank.

    Notes and full texts are two corpora, each ranked by BM25 on its own, then merged NOTE_SHARE notes to one
    paper (the other fills when one runs out). Ranked as one corpus, a megabyte of papers set the idf of the notes'
    words and their thousands of sections pushed the notes' best ones out of the top 5 (#589's PR: 2 of 10 questions
    lost their relevant note section that way), and the notes are what research starts from.

    Only a unit whose lowercased text holds a query word as a substring can hold it as a term, so only those are
    tokenized; any other enters BM25 as a placeholder of its term count, from `lengths` (text digest -> count, filled
    in here for every unit of `pool` and pruned to them). The scores equal tokenizing every unit, at a fraction of
    the cost on a megabyte of full texts once `lengths` is warm."""
    query_terms = terms(query)
    known = {} if lengths is None else lengths
    seen: dict[str, int] = {}
    docs = []
    for u in pool:
        key = digest(u.text)
        low = u.text.lower()
        if any(w in low for w in query_terms):
            doc = words(u.text)
            seen[key] = len(doc)
        else:
            seen[key] = known[key] if key in known else len(words(u.text))
            doc = [""] * seen[key]  # never a query term: WORD takes two letters or more
        docs.append(doc)
    known.clear()
    known.update(seen)
    cap = max(1, min(n, MAX_N))
    ranks: dict[bool, list[Unit]] = {}
    for paper in (False, True):
        part = [i for i, u in enumerate(pool) if u.paper == paper]
        scores = bm25([docs[i] for i in part], query_terms)
        ranks[paper] = [pool[part[j]] for j in sorted(range(len(part)), key=lambda j: -scores[j]) if scores[j] > 0]
    ranked = _merge(ranks[False], ranks[True])[: 3 * cap]
    q = set(query_terms)
    shown: dict[tuple[str, str], Unit] = {}  # one line per place and sentence, at the better rank, labelled if any
    for u in ranked:
        key = (u.where, best_sentence(u.text, q))
        if key not in shown or (u.label and not shown[key].label):
            shown[key] = u  # replacing keeps the first rank
    return list(shown.values())[:cap]


def _merge(notes: list[Unit], papers: list[Unit]) -> list[Unit]:
    """NOTE_SHARE notes, then one paper, in turn, each list in its own order, until both are spent."""
    merged: list[Unit] = []
    a, b = iter(notes), iter(papers)
    while True:
        took = [u for _ in range(NOTE_SHARE) if (u := next(a, None))] + [u for u in [next(b, None)] if u]
        if not took:
            return merged
        merged += took


def search_cached(pool: list[Unit], query: str, n: int, cache: Path) -> list[Unit]:
    """`search` with its term counts kept in `cache` (a JSON file in the shared state dir) between calls."""
    try:
        lengths = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lengths = {}
    if not isinstance(lengths, dict):
        lengths = {}
    before = dict(lengths)
    hits = search(pool, query, n, lengths)
    if lengths != before:
        tmp = cache.with_name(f"{cache.name}.{os.getpid()}.tmp")
        with contextlib.suppress(OSError):
            tmp.write_text(json.dumps(lengths), encoding="utf-8")
            os.replace(tmp, cache)  # atomic: a concurrent reader sees the old file or the new one
    return hits


def render(hits: list[Unit], query: str) -> str:
    if not hits:
        return "no hits: no note section, claim or cached full text shares a word with the query"
    q = set(terms(query))
    return "\n".join(u.line(q) for u in hits)
