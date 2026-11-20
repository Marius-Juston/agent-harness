"""A paper's full text as an outline of sections, and verbatim quote matching against one of them (#512).

`dev cite-check` checks a recommendation-driving claim's quote against the section its locator names, and
`dev lit read` prints a paper's outline or one section, so an agent reads the part a claim rests on rather than the
abstract or the whole paper. Formats: arXiv's HTML rendering (LaTeXML) or any HTML page, Europe PMC's JATS XML,
PDF (pypdf), and plain text extracted from a PDF (CORE's `fullText`). Pure: lit.Client.full_text does the fetching and caching.
"""

import io
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser

# Kinds whose sections come from heading-looking lines of extracted text, so a section boundary is a guess.
APPROXIMATE = frozenset({"pdf", "text"})
MIN_WORDS = 1000  # fewer words is a landing page or an abstract, not a paper
# A PDF is untrusted input: refused above PDF_MAX_BYTES, parsed in a subprocess limited to PDF_MEMORY and killed
# after PDF_SECONDS, and only its first MAX_PAGES pages are read. A refusal or a kill counts as no full text.
PDF_MAX_BYTES = 50_000_000
PDF_SECONDS = 30.0
PDF_MEMORY = 1_000_000_000  # bytes of address space the PDF subprocess may map
MAX_PAGES = 120
QUOTE_MIN_WORDS = 3  # a quote shorter than this proves little
PIECE_MIN_WORDS = 4  # each fragment of an elided quote ("a ... b", "[x] b")

# A locator as notes write it: "abstract", "§3.2", "§Results", "Section 4", "Appendix B", "Table 2", "Fig. 3", "p. 7".
LOCATOR = re.compile(
    r"\babstract\b|§\s*[A-Za-z0-9][\w.]*(?-i:(?:(?<![\d.])[ \t]+[A-Z][a-z]+)*)|\b(?:Section|Sec\.)[ \t]+[\w.]+"
    r"|\bAppendix[ \t]+[A-Z0-9][\w.]*|\b(?:Table|Tab\.|Figure|Fig\.)[ \t]*[A-Z]?\d+|\bpp?\.[ \t]*\d+",
    re.I,
)
SECTIONLESS = re.compile(r"^(?:Table|Tab\.|Figure|Fig\.|pp?\.)", re.I)  # captions and pages: the whole text
NUMBER = re.compile(r"^\s*((?:\d+|[A-Z])(?:\.\d+)*)\.?(?:\s|$)")
BACK_MATTER = re.compile(r"\b(references|bibliography|acknowledge?ments?)\b", re.I)


@dataclass
class FullText:
    url: str
    kind: str  # "html", "xml" or "pdf"
    sections: list[tuple[str, str]]  # (heading, text) in reading order; the first heading may be "" (front matter)
    levels: list[int] = field(default_factory=list)  # each heading's depth: h1-h6, JATS <sec> nesting, 3.2 -> 2

    def words(self) -> int:
        return sum(len(t.split()) for _, t in self.sections)

    def outline(self) -> str:
        lines = [f"{self.url} ({self.kind}, {self.words()} words)"]
        lines += [f"  {h or '(front matter)'} ({len(t.split())} words)" for h, t in self.sections]
        return "\n".join(lines)

    def whole(self) -> str:
        return "\n".join(f"{h}\n{t}" for h, t in self.sections)

    def locate(self, locator: str) -> tuple[str, str] | None:
        """(the heading found, the text of that section and its subsections) for a section locator, else None."""
        loc = re.sub(r"^(?:§|Section\b|Sec\.)\s*", "", locator.strip(), flags=re.I).strip()
        if SECTIONLESS.match(loc):
            return None
        heads = [h for h, _ in self.sections]
        if loc.lower() == "abstract":
            start = next((i for i, h in enumerate(heads) if re.search(r"\babstract\b", h, re.I)), None)
            return None if start is None else (heads[start], self.sections[start][1])
        app = re.match(r"(?i)appendix\s+(\S+)", loc)
        key = (app.group(1) if app else loc).rstrip(".")
        if num := re.match(r"\d+(?:\.\d+)*(?=[\s.]|$)", key):  # "3.1 Resampling" -> 3.1
            key = num.group(0)
        start = None
        if re.fullmatch(r"(?:\d+|[A-Z])(?:\.\d+)*", key):
            start = next((i for i, h in enumerate(heads) if _number(h) == key), None)
            if start is None and app:
                start = next((i for i, h in enumerate(heads) if re.search(rf"(?i)\bappendix\s+{key}\b", h)), None)
        else:
            want = _fold(key)
            start = next((i for i, h in enumerate(heads) if want and want in _fold(h)), None)
        if start is None:
            return None
        levels = self.levels if len(self.levels) == len(heads) else [_depth(h) for h in heads]
        end = start + 1
        while end < len(heads) and levels[end] > levels[start] and not BACK_MATTER.search(heads[end]):
            end += 1
        return heads[start], "\n".join(f"{h}\n{t}" for h, t in self.sections[start:end])


def _number(heading: str) -> str:
    """'3.2' of '3.2 Results', 'A.1' of 'A.1 Proofs' or of 'Appendix A.1 Proofs'; '' when unnumbered."""
    m = NUMBER.match(re.sub(r"(?i)^\s*appendix\s+", "", heading))
    return m.group(1) if m else ""


def _depth(heading: str) -> int:
    """A heading's depth from its number alone: 1 for '3 Method' or an unnumbered heading, 2 for '3.2 Results'."""
    return _number(heading).count(".") + 1


def _fold(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.casefold()).strip()


# ---- verbatim matching ----

TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "­": None}) | {ord(c): "-" for c in "‐‑‒–—−"}
GAP = re.compile(r"\s*(?:\.\.\.|…|\[[^\]]*\])\s*")  # an ellipsis or an editorial [insertion] in a quote


def normalise(s: str) -> str:
    """Compatibility forms (ligatures), one quote and dash style, a hyphen between letters dropped (line-break
    hyphenation, 'well-known' = 'wellknown'), case folded, format characters (LaTeXML's zero-width spaces) and
    whitespace dropped: PDF and HTML text of the same sentence then compare equal. Digits keep their hyphens and the
    space between them, so '3-5%' and '3 5' never read as '35'."""
    s = unicodedata.normalize("NFKC", s).translate(TRANSLATE)
    s = "".join(c for c in s if unicodedata.category(c) != "Cf")
    s = re.sub(r"(?<=[^\W\d])-\s*(?=[^\W\d])", "", s)
    s = re.sub(r"(?<=\d)\s+(?=\d)", " ", s.casefold())
    return re.sub(r"(?<!\d)\s+|\s+(?!\d)", "", s)


def too_short(quote: str) -> str:
    """Why `quote` is too short to prove anything ('' when it is long enough): at least QUOTE_MIN_WORDS words of
    verbatim text, and PIECE_MIN_WORDS in each fragment of an elided quote."""
    pieces = [p.split() for p in GAP.split(quote)]
    pieces = [p for p in pieces if p]
    if sum(map(len, pieces)) < QUOTE_MIN_WORDS:
        return f"under {QUOTE_MIN_WORDS} words of verbatim text"
    if len(pieces) > 1 and min(map(len, pieces)) < PIECE_MIN_WORDS:
        return f"an elided quote needs {PIECE_MIN_WORDS} words in each fragment"
    return ""


def contains(text: str, quote: str) -> bool:
    """The quote is long enough (too_short) and its pieces (split at ellipses and [insertions]) appear in `text` in
    order, after normalise()."""
    if too_short(quote):
        return False
    hay, at = normalise(text), 0
    pieces = [normalise(p) for p in GAP.split(quote)]
    pieces = [p for p in pieces if p]
    if not pieces:
        return False
    for p in pieces:
        at = hay.find(p, at)
        if at < 0:
            return False
        at += len(p)
    return True


# ---- parsers ----

BLOCK = {"p", "div", "li", "td", "th", "tr", "br", "section", "figcaption", "caption", "table", "blockquote", "dd"}
SKIP = {"script", "style", "nav", "header", "footer", "head", "annotation", "annotation-xml", "button", "form"}
JATS_SECTIONS = {"sec", "abstract", "ref-list", "app", "ack"}


class _Sections(HTMLParser):
    """Sections of an HTML page (h1-h6) or a JATS article (a <title> directly inside <sec>, <abstract>, ...)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[tuple[str, list[str]]] = [("", [])]
        self.levels: list[int] = [0]
        self.depth = 0  # JATS <sec> nesting
        self.level = 0  # the open heading's
        self.stack: list[str] = []
        self.skip = 0
        self.heading: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "img", "hr", "meta", "link", "input"):
            self._text(" ")
            return
        parent = self.stack[-1] if self.stack else ""
        self.stack.append(tag)
        if tag in JATS_SECTIONS:
            self.depth += 1
        if tag in SKIP:
            self.skip += 1
        elif re.fullmatch(r"h[1-6]", tag) or (tag == "title" and parent in JATS_SECTIONS):
            self.heading, self.level = [], int(tag[1]) if tag[0] == "h" else self.depth
        elif tag == "abstract":
            self.sections.append(("Abstract", []))
            self.levels.append(self.depth)
        if tag in BLOCK:
            self._text(" ")

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        while self.stack:
            top = self.stack.pop()
            if top in JATS_SECTIONS:
                self.depth -= 1
            if top in SKIP:
                self.skip -= 1
            if (re.fullmatch(r"h[1-6]", top) or top == "title") and self.heading is not None:
                head = " ".join("".join(self.heading).split())
                self.heading = None
                last, body = self.sections[-1]
                if head and not (last.lower() == head.lower() and not "".join(body).strip()):
                    self.sections.append((head, []))
                    self.levels.append(self.level)
            if top == tag:
                break
        if tag in BLOCK:
            self._text(" ")

    def handle_data(self, data):
        self._text(data)

    def _text(self, s: str) -> None:
        if self.skip:
            return
        (self.heading if self.heading is not None else self.sections[-1][1]).append(s)


def from_markup(body: str, url: str, kind: str = "html") -> FullText:
    p = _Sections()
    p.feed(body)
    p.close()
    kept = [(h, " ".join("".join(t).split()), n) for (h, t), n in zip(p.sections, p.levels, strict=True)]
    kept = [x for x in kept if x[0] or x[1]]
    return FullText(url, kind, [(h, t) for h, t, _ in kept], [n for _, _, n in kept])


PDF_HEAD = re.compile(
    r"^(?:(?:\d{1,2}(?:\.\d{1,2})*|[A-H](?:\.\d{1,2})*)\.?[ \t]+[A-Z][^\n]{1,70}"
    r"|(?:Abstract|ABSTRACT|Introduction|INTRODUCTION|References|REFERENCES|Acknowledge?ments?|Conclusions?)"
    r"|Appendix[ \t]+[A-Z][^\n]{0,70})$"
)


def from_pdf(data: bytes, url: str, seconds: float = PDF_SECONDS, memory: int = PDF_MEMORY) -> FullText | None:
    """Sections of a PDF by heading-looking lines (numbered titles, Abstract, References ...): approximate, so
    cite-check reports a quote outside the located section of a PDF as a note, not a problem. None when the PDF is
    over PDF_MAX_BYTES, broken, or not parsed within `seconds` and `memory` bytes (pypdf runs in a subprocess under
    an address-space limit, killed at the time limit)."""
    if len(data) > PDF_MAX_BYTES:
        return None
    cmd = [sys.executable, "-I", "-m", "devkit.fulltext", str(MAX_PAGES), str(memory)]
    try:
        out = subprocess.run(cmd, input=data, capture_output=True, timeout=seconds, check=False)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        return None
    return from_text("\n".join(json.loads(out.stdout)), url, "pdf")


def from_text(text: str, url: str, kind: str = "text") -> FullText:
    """Sections of plain text extracted from a PDF (a PDF's pages, or CORE's `fullText`), split at heading-looking
    lines as from_pdf does: approximate (APPROXIMATE)."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        s = line.strip()
        if PDF_HEAD.match(s) and len(s.split()) <= 12 and not s.endswith((".", ",")):
            sections.append((s, []))
        else:
            sections[-1][1].append(s)
    kept = [(h, " ".join(" ".join(t).split())) for h, t in sections if h or t]
    return FullText(url, kind, kept, [_depth(h) if h else 0 for h, _ in kept])


def _pdf_pages(data: bytes, max_pages: int) -> list[str]:
    """The text of each of the first `max_pages` pages (the subprocess's work; pypdf also caps decompression)."""
    import pypdf  # only the subprocess parses PDFs

    reader = pypdf.PdfReader(io.BytesIO(data))
    return [(pg.extract_text() or "") for pg in reader.pages[:max_pages]]


if __name__ == "__main__":  # `python -I -m devkit.fulltext <max pages> <memory>` < pdf bytes: JSON page texts
    import resource  # POSIX only, like the rest of devkit's process handling

    resource.setrlimit(resource.RLIMIT_AS, (int(sys.argv[2]), int(sys.argv[2])))  # before pypdf allocates anything
    print(json.dumps(_pdf_pages(sys.stdin.buffer.read(), int(sys.argv[1]))))
