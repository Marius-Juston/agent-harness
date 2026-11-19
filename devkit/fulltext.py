"""A paper's full text as an outline of sections, and verbatim quote matching against one of them (#512).

`dev cite-check` checks a recommendation-driving claim's quote against the section its locator names, and
`dev lit read` prints a paper's outline or one section, so an agent reads the part a claim rests on rather than the
abstract or the whole paper. Formats: arXiv's HTML rendering (LaTeXML) or any HTML page, Europe PMC's JATS XML, and
PDF (pypdf). Pure: lit.Client.full_text does the fetching and caching.
"""

import io
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser

MIN_WORDS = 1000  # fewer words is a landing page or an abstract, not a paper
MAX_PAGES = 120  # pypdf cost on a hostile or huge PDF stays bounded

# A locator as notes write it: "abstract", "§3.2", "§Results", "Section 4", "Appendix B", "Table 2", "Fig. 3", "p. 7".
LOCATOR = re.compile(
    r"\babstract\b|§\s*[A-Za-z0-9][\w.]*(?:[ \t]+[A-Z][a-z]+)*|\b(?:Section|Sec\.)[ \t]+[\w.]+"
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
        num = _number(heads[start])
        end = start + 1
        while end < len(heads) and num:
            h = heads[end]
            n = _number(h)
            if (n and not n.startswith(num + ".")) or (not n and BACK_MATTER.search(h)):
                break
            end += 1
        return heads[start], "\n".join(f"{h}\n{t}" for h, t in self.sections[start:end])


def _number(heading: str) -> str:
    """'3.2' of '3.2 Results', 'A.1' of 'A.1 Proofs' or of 'Appendix A.1 Proofs'; '' when unnumbered."""
    m = NUMBER.match(re.sub(r"(?i)^\s*appendix\s+", "", heading))
    return m.group(1) if m else ""


def _fold(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.casefold()).strip()


# ---- verbatim matching ----

TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "­": None}) | {ord(c): "-" for c in "‐‑‒–—−"}
GAP = re.compile(r"\s*(?:\.\.\.|…|\[[^\]]*\])\s*")  # an ellipsis or an editorial [insertion] in a quote


def normalise(s: str) -> str:
    """Compatibility forms (ligatures), one quote and dash style, line-break hyphenation joined, case folded and all
    whitespace dropped: PDF and HTML text of the same sentence then compare equal."""
    s = unicodedata.normalize("NFKC", s).translate(TRANSLATE)
    s = re.sub(r"(?<=\w)-\s*(?=\w)", "", s)
    return re.sub(r"\s+", "", s.casefold())


def contains(text: str, quote: str) -> bool:
    """The quote's pieces (split at ellipses and [insertions]) appear in `text` in order, after normalise()."""
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
        self.stack: list[str] = []
        self.skip = 0
        self.heading: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "img", "hr", "meta", "link", "input"):
            self._text(" ")
            return
        parent = self.stack[-1] if self.stack else ""
        self.stack.append(tag)
        if tag in SKIP:
            self.skip += 1
        elif re.fullmatch(r"h[1-6]", tag) or (tag == "title" and parent in JATS_SECTIONS):
            self.heading = []
        elif tag == "abstract":
            self.sections.append(("Abstract", []))
        if tag in BLOCK:
            self._text(" ")

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        while self.stack:
            top = self.stack.pop()
            if top in SKIP:
                self.skip -= 1
            if (re.fullmatch(r"h[1-6]", top) or top == "title") and self.heading is not None:
                head = " ".join("".join(self.heading).split())
                self.heading = None
                last, body = self.sections[-1]
                if head and not (last.lower() == head.lower() and not "".join(body).strip()):
                    self.sections.append((head, []))
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
    sections = [(h, " ".join("".join(t).split())) for h, t in p.sections]
    return FullText(url, kind, [(h, t) for h, t in sections if h or t])


PDF_HEAD = re.compile(
    r"^(?:(?:\d{1,2}(?:\.\d{1,2})*|[A-H](?:\.\d{1,2})*)\.?[ \t]+[A-Z][^\n]{1,70}"
    r"|(?:Abstract|ABSTRACT|Introduction|INTRODUCTION|References|REFERENCES|Acknowledge?ments?|Conclusions?)"
    r"|Appendix[ \t]+[A-Z][^\n]{0,70})$"
)


def from_pdf(data: bytes, url: str) -> FullText | None:
    """Sections of a PDF by heading-looking lines (numbered titles, Abstract, References ...): approximate, so
    cite-check reports a quote outside the located section of a PDF as a note, not a problem."""
    import pypdf  # only `cite-check` and `lit read` parse PDFs: other commands skip its import

    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        pages = [(pg.extract_text() or "") for pg in reader.pages[:MAX_PAGES]]
    except Exception:  # pypdf raises many types on a broken file; the paper is then unreadable
        return None
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in "\n".join(pages).splitlines():
        s = line.strip()
        if PDF_HEAD.match(s) and len(s.split()) <= 12 and not s.endswith((".", ",")):
            sections.append((s, []))
        else:
            sections[-1][1].append(s)
    return FullText(url, "pdf", [(h, " ".join(" ".join(t).split())) for h, t in sections if h or t])
