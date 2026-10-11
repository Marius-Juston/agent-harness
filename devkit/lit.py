"""Literature search shared by every agent on this machine (`uv run dev lit search|get`).

Parallel researchers on one IP got 429s from OpenAlex and Semantic Scholar and spent OpenAlex's daily budget, then
fell back to weaker sources. Every call here goes through one client:

- a token bucket per source under flock in <git-common-dir>/devkit/lit/, so all worktrees and processes together
  stay under each API's documented rate; a 429 sets a cooldown that every process honours;
- a response cache in the same place, shared by all worktrees, for CACHE_DAYS (only 200 answers are cached);
- free API keys from the environment when present (OPENALEX_API_KEY, S2_API_KEY); they never reach the cache key
  or the output;
- backoff on 429/5xx (Retry-After when given), giving up on a source whose wait exceeds MAX_WAIT, so the others
  still answer;
- compact output: one record per paper (title, year, venue, citations, ids, abstract snippet), capped.

stdlib only: pyalex and semanticscholar each cover one API and retry in-process, which does not share a rate limit
or a cache across processes, the part that failed.
"""

import contextlib
import fcntl
import hashlib
import html
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from devkit.paths import state_dir

UA = "agent-harness-devkit-lit/0.1 (research tooling; mailto:noreply@example.invalid)"
CACHE_DAYS = 7
MAX_N = 25
DEFAULT_N = 8
SNIPPET = 240  # characters of abstract per record
MAX_WAIT = 60.0  # seconds: a longer Retry-After (a spent daily budget) gives up on that source for this call
TRIES = 4
# Requests per second and burst, per source, for the whole machine. Below the documented limits on purpose: OpenAlex
# allows 10/s, Crossref's polite pool 50/s (we use far less), arXiv asks for one request per 3 s, and Semantic
# Scholar's shared unauthenticated pool throttles hard (1/s is its rate with a key).
RATES: dict[str, tuple[float, float]] = {
    "openalex": (5.0, 5.0),
    "s2": (1.0, 1.0),
    "crossref": (5.0, 5.0),
    "arxiv": (1 / 3, 1.0),
}
SOURCES = tuple(RATES)
DEFAULT_SOURCES = ("openalex", "s2")

Get = Callable[[str, dict[str, str]], tuple[int, dict[str, str], str]]  # (url, headers) -> (status, headers, body)


def http_get(url: str, headers: dict[str, str], timeout: int = 30) -> tuple[int, dict[str, str], str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(2_000_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), ""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, {}, str(e)


class SourceError(Exception):
    pass


# ---- rate limit: token bucket shared through a locked file ----


def take(state: dict, now: float, rate: float, burst: float) -> tuple[dict, float]:
    """One token from a bucket: (new state, seconds to wait first; 0 = granted now). Pure.

    state = {"tokens": float, "t": last refill time, "until": cooldown end}. A refused call does not consume."""
    tokens = min(burst, state.get("tokens", burst) + max(0.0, now - state.get("t", now)) * rate)
    new = {**state, "tokens": tokens, "t": now}
    cooldown = state.get("until", 0.0) - now
    if cooldown > 0:
        return new, cooldown
    if tokens >= 1:
        new["tokens"] = tokens - 1
        return new, 0.0
    return new, (1 - tokens) / rate


@dataclass
class Bucket:
    """Machine-wide bucket per source in <dir>/buckets.json, read and written under flock."""

    dir: Path
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep

    @contextlib.contextmanager
    def _state(self):
        path = self.dir / "buckets.json"
        with open(self.dir / "buckets.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                try:
                    data = json.loads(path.read_text())
                except (FileNotFoundError, json.JSONDecodeError):
                    data = {}
                yield data
                _atomic_write(path, json.dumps(data))
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def acquire(self, source: str) -> None:
        rate, burst = RATES[source]
        while True:
            with self._state() as data:
                data[source], wait = take(data.get(source, {}), self.clock(), rate, burst)
            if wait <= 0:
                return
            if wait > MAX_WAIT:  # a cooldown from a spent budget: skip the source instead of asking again
                raise SourceError(f"{source}: rate limited for another {wait:.0f}s (daily budget spent?); skipped")
            self.sleep(wait)

    def cooldown(self, source: str, seconds: float) -> None:
        """Every process holds off `source` for `seconds` (a 429 told one of them to)."""
        with self._state() as data:
            st = data.setdefault(source, {})
            st["until"] = max(st.get("until", 0.0), self.clock() + seconds)


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


# ---- records ----


@dataclass
class Paper:
    title: str
    year: int | None = None
    venue: str = ""
    cites: int | None = None
    ids: dict[str, str] = field(default_factory=dict)  # doi, arxiv, openalex, s2
    abstract: str = ""
    source: str = ""

    def key(self) -> str:
        if doi := self.ids.get("doi"):
            return "doi:" + doi.lower()
        if ax := self.ids.get("arxiv"):
            return "arxiv:" + ax
        return "t:" + re.sub(r"[^a-z0-9]", "", self.title.lower())

    def line(self, snippet: int = SNIPPET) -> str:
        meta = ", ".join(
            x
            for x in (
                str(self.year or "n.d."),
                self.venue[:60],
                f"{self.cites} cites" if self.cites is not None else "",
            )
            if x
        )
        ids = " ".join(f"{k}:{v}" for k, v in self.ids.items() if v)
        out = f"- {self.title[:200]} ({meta}) [{ids}]"
        if self.abstract and snippet:
            text = " ".join(self.abstract.split())
            out += "\n  " + (text if len(text) <= snippet else text[: snippet - 1].rstrip() + "…")
        return out


def _strip_doi(doi: str | None) -> str:
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi or "", flags=re.I)


def inverted_to_text(index: dict[str, list[int]] | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}; rebuild the text."""
    if not index:
        return ""
    words = sorted((pos, w) for w, positions in index.items() for pos in positions)
    return " ".join(w for _, w in words)


def _arxiv_from_doi(doi: str) -> str:
    m = re.match(r"10\.48550/arxiv\.(.+)$", doi, flags=re.I)
    return m.group(1) if m else ""


def parse_openalex(body: str) -> list[Paper]:
    data = json.loads(body)
    out = []
    for w in data.get("results", [data] if "id" in data else []):
        loc = w.get("primary_location") or {}
        doi = _strip_doi(w.get("doi") or (w.get("ids") or {}).get("doi"))
        ids = {"doi": doi, "arxiv": _arxiv_from_doi(doi), "openalex": (w.get("id") or "").rsplit("/", 1)[-1]}
        out.append(
            Paper(
                title=w.get("title") or w.get("display_name") or "",
                year=w.get("publication_year"),
                venue=((loc.get("source") or {}).get("display_name")) or "",
                cites=w.get("cited_by_count"),
                ids=ids,
                abstract=inverted_to_text(w.get("abstract_inverted_index")),
                source="openalex",
            )
        )
    return out


def parse_s2(body: str) -> list[Paper]:
    data = json.loads(body)
    out = []
    for p in data.get("data", [data] if "paperId" in data else []):
        ext = p.get("externalIds") or {}
        out.append(
            Paper(
                title=p.get("title") or "",
                year=p.get("year"),
                venue=p.get("venue") or "",
                cites=p.get("citationCount"),
                ids={"doi": ext.get("DOI") or "", "arxiv": ext.get("ArXiv") or "", "s2": p.get("paperId") or ""},
                abstract=p.get("abstract") or "",
                source="s2",
            )
        )
    return out


def parse_crossref(body: str) -> list[Paper]:
    msg = json.loads(body).get("message", {})
    out = []
    for it in msg.get("items", [msg] if "DOI" in msg else []):
        parts = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
        abstract = html.unescape(re.sub(r"<[^>]+>", " ", it.get("abstract") or ""))
        out.append(
            Paper(
                title=" ".join(it.get("title") or [""]),
                year=parts[0] if parts else None,
                venue=" ".join(it.get("container-title") or []),
                cites=it.get("is-referenced-by-count"),
                ids={"doi": it.get("DOI") or "", "arxiv": _arxiv_from_doi(it.get("DOI") or "")},
                abstract=abstract,
                source="crossref",
            )
        )
    return out


def parse_arxiv(body: str) -> list[Paper]:
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    out = []
    for e in ET.fromstring(body).findall("a:entry", ns):
        aid = re.sub(r"v\d+$", "", (e.findtext("a:id", "", ns) or "").rsplit("/abs/", 1)[-1])
        title = " ".join((e.findtext("a:title", "", ns) or "").split())
        if not aid or not title or title.lower() == "error":
            continue
        out.append(
            Paper(
                title=title,
                year=int(y) if (y := (e.findtext("a:published", "", ns) or "")[:4]).isdigit() else None,
                venue=e.findtext("x:journal_ref", "", ns) or "arXiv",
                ids={"doi": e.findtext("x:doi", "", ns) or "", "arxiv": aid},
                abstract=e.findtext("a:summary", "", ns) or "",
                source="arxiv",
            )
        )
    return out


PARSE = {"openalex": parse_openalex, "s2": parse_s2, "crossref": parse_crossref, "arxiv": parse_arxiv}


# ---- requests ----


def _years(years: str) -> tuple[str, str]:
    """'2015-', '-2020', '2015-2020' or '2019' -> (from, to), either may be ''."""
    if not re.fullmatch(r"\d{4}|\d{4}-|-\d{4}|\d{4}-\d{4}", years):
        raise ValueError(f"--years wants 2019, 2015-, -2020 or 2015-2020, not {years!r}")
    lo, _, hi = years.partition("-") if "-" in years else (years, "", years)
    return lo, hi


def search_url(source: str, query: str, n: int, years: str = "") -> str:
    q = urllib.parse.quote(query)
    lo, hi = _years(years) if years else ("", "")
    match source:
        case "openalex":
            url = (
                f"https://api.openalex.org/works?search={q}&per_page={n}"
                "&select=id,doi,title,publication_year,primary_location,cited_by_count,abstract_inverted_index"
            )
            if lo or hi:
                url += "&filter=publication_year:" + (f"{lo}-{hi}" if lo != hi else lo)
            return url
        case "s2":
            url = (
                f"https://api.semanticscholar.org/graph/v1/paper/search?query={q}&limit={n}"
                "&fields=title,year,venue,citationCount,externalIds,abstract"
            )
            return url + (f"&year={lo}-{hi}" if lo or hi else "")
        case "crossref":
            url = (
                f"https://api.crossref.org/works?query={q}&rows={n}"
                "&select=DOI,title,issued,container-title,is-referenced-by-count,abstract"
            )
            filt = [f"from-pub-date:{lo}" if lo else "", f"until-pub-date:{hi}" if hi else ""]
            return url + ("&filter=" + ",".join(f for f in filt if f) if lo or hi else "")
        case "arxiv":
            return f"https://export.arxiv.org/api/query?search_query=all:{q}&max_results={n}"
    raise ValueError(f"unknown source {source!r}; one of {', '.join(SOURCES)}")


ARXIV_ID = re.compile(r"^(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?$", re.I)
DOI_ID = re.compile(r"^(?:doi:|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/\S+)$", re.I)


def get_urls(ident: str) -> list[tuple[str, str]]:
    """(source, url) candidates for one paper id, best first: DOI, arXiv id, OpenAlex W-id or S2 paper id."""
    ident = ident.strip()
    s2_fields = "fields=title,year,venue,citationCount,externalIds,abstract"
    oa_sel = "select=id,doi,title,publication_year,primary_location,cited_by_count,abstract_inverted_index"
    if m := ARXIV_ID.match(ident):
        aid = m.group(1)
        return [
            ("s2", f"https://api.semanticscholar.org/graph/v1/paper/ARXIV:{aid}?{s2_fields}"),
            ("arxiv", f"https://export.arxiv.org/api/query?id_list={aid}"),
        ]
    if m := DOI_ID.match(ident):
        doi = urllib.parse.quote(m.group(1), safe="/")
        return [
            ("openalex", f"https://api.openalex.org/works/doi:{doi}?{oa_sel}"),
            ("s2", f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?{s2_fields}"),
            ("crossref", f"https://api.crossref.org/works/{doi}"),
        ]
    if re.fullmatch(r"W\d+", ident, flags=re.I):
        return [("openalex", f"https://api.openalex.org/works/{ident.upper()}?{oa_sel}")]
    if re.fullmatch(r"[0-9a-f]{40}", ident):
        return [("s2", f"https://api.semanticscholar.org/graph/v1/paper/{ident}?{s2_fields}")]
    raise ValueError(f"not a DOI, arXiv id, OpenAlex W-id or S2 paper id: {ident!r}")


def with_key(source: str, url: str, env: dict[str, str]) -> tuple[str, dict[str, str]]:
    """The url and headers to send: API keys from env, added here so they never reach the cache key."""
    if source == "openalex" and (k := env.get("OPENALEX_API_KEY")):
        return url + ("&" if "?" in url else "?") + "api_key=" + urllib.parse.quote(k), {}
    if source == "s2" and (k := env.get("S2_API_KEY")):
        return url, {"x-api-key": k}
    return url, {}


@dataclass
class Client:
    dir: Path
    get: Get = http_get
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))

    def __post_init__(self) -> None:
        (self.dir / "cache").mkdir(parents=True, exist_ok=True)
        self.bucket = Bucket(self.dir, self.clock, self.sleep)

    def _cache_path(self, url: str) -> Path:
        return self.dir / "cache" / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".json")

    def fetch(self, source: str, url: str) -> str:
        """Body of a 200 answer for `url`, from the shared cache when fresh. Raises SourceError otherwise."""
        path = self._cache_path(url)
        with contextlib.suppress(FileNotFoundError, json.JSONDecodeError, KeyError):
            rec = json.loads(path.read_text())
            if self.clock() - rec["t"] < CACHE_DAYS * 86400:
                return rec["body"]
        send, headers = with_key(source, url, self.env)
        delay = 2.0
        status, body = 0, ""
        for attempt in range(TRIES):
            self.bucket.acquire(source)
            status, resp_headers, body = self.get(send, headers)
            if status == 200:
                _atomic_write(path, json.dumps({"t": self.clock(), "url": url, "body": body}))
                return body
            if status == 404:
                raise SourceError(f"{source}: not found")
            if status not in (0, 429) and status < 500:
                raise SourceError(f"{source}: HTTP {status}")
            wait = _retry_after(resp_headers) or delay
            if status == 429:
                self.bucket.cooldown(source, wait)
            if wait > MAX_WAIT:
                raise SourceError(f"{source}: rate limited for {wait:.0f}s (daily budget spent?); skipped")
            if status != 429 and attempt + 1 < TRIES:  # a 429's wait is the cooldown, which acquire() honours
                self.sleep(wait)
            delay *= 2
        raise SourceError(f"{source}: {'HTTP ' + str(status) if status else 'unreachable'} after {TRIES} tries")

    def search(self, query: str, sources: tuple[str, ...] = DEFAULT_SOURCES, n: int = DEFAULT_N, years: str = ""):
        """(papers merged across sources in rank order, alternating sources, at most n; notes on failed sources)."""
        n = max(1, min(n, MAX_N))
        merged: dict[str, Paper] = {}
        notes = []
        ranked: list[list[Paper]] = []
        for src in sources:
            try:
                papers = PARSE[src](self.fetch(src, search_url(src, query, n, years)))
            except SourceError as exc:
                notes.append(str(exc))
                continue
            except (ValueError, ET.ParseError) as exc:  # json.JSONDecodeError is a ValueError
                notes.append(f"{src}: unreadable answer ({type(exc).__name__})")
                continue
            ranked.append([p for p in papers if p.title])
        for i in range(max(map(len, ranked), default=0)):
            for papers in ranked:
                if i < len(papers):
                    _merge(merged, papers[i])
        return list(merged.values())[:n], notes

    def lookup(self, ident: str) -> tuple[Paper | None, list[str]]:
        notes = []
        for src, url in get_urls(ident):
            try:
                papers = PARSE[src](self.fetch(src, url))
            except SourceError as exc:
                notes.append(str(exc))
                continue
            except (ValueError, ET.ParseError) as exc:
                notes.append(f"{src}: unreadable answer ({type(exc).__name__})")
                continue
            if papers and papers[0].title:
                return papers[0], notes
        return None, notes


def _merge(merged: dict[str, Paper], p: Paper) -> None:
    """Same paper from two sources: keep one record with the union of ids, the higher citation count, any abstract."""
    k = p.key()
    alt = next((key for key, q in merged.items() if q.key() == k or _same_title(q, p)), None)
    if alt is None:
        merged[k] = p
        return
    q = merged[alt]
    q.ids = {**{a: b for a, b in p.ids.items() if b}, **{a: b for a, b in q.ids.items() if b}}
    q.cites = max(q.cites or 0, p.cites or 0) if q.cites is not None or p.cites is not None else None
    q.abstract = q.abstract or p.abstract
    q.venue = q.venue or p.venue
    q.year = q.year or p.year


def _same_title(a: Paper, b: Paper) -> bool:
    ta, tb = (re.sub(r"[^a-z0-9]", "", x.title.lower()) for x in (a, b))
    return bool(ta) and ta == tb and (a.year is None or b.year is None or abs(a.year - b.year) <= 1)


def _retry_after(headers: dict[str, str]) -> float:
    for k, v in headers.items():
        if k.lower() == "retry-after":
            with contextlib.suppress(ValueError):
                return max(0.0, float(v))
    return 0.0


def client() -> Client:
    return Client(state_dir("lit"))


def render(papers: list[Paper], notes: list[str], snippet: int = SNIPPET) -> str:
    lines = [p.line(snippet) for p in papers] or ["no results"]
    lines += [f"note: {n}" for n in notes]
    return "\n".join(lines)


def parse_sources(spec: str) -> tuple[str, ...]:
    if spec == "all":
        return SOURCES
    out = tuple(s.strip() for s in spec.split(",") if s.strip())
    if bad := [s for s in out if s not in RATES]:
        raise ValueError(f"unknown source(s) {', '.join(bad)}; use {', '.join(SOURCES)} or all")
    return out or DEFAULT_SOURCES
