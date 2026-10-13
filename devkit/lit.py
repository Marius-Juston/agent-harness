"""Literature search shared by every agent on this machine (`uv run dev lit search|get`).

Parallel researchers on one IP got 429s from OpenAlex and Semantic Scholar and spent OpenAlex's daily budget, then
fell back to weaker sources. Every call here goes through one client:

- a token bucket per source under flock in <git-common-dir>/devkit/lit/, so all worktrees and processes together
  stay under each API's documented rate; a 429 sets a cooldown that every process honours;
- a response cache in the same place, shared by all worktrees, for CACHE_DAYS (only 200 answers are cached);
- free API keys (OPENALEX_API_KEY, S2_API_KEY) from the environment, else from a private secrets file
  (DEVKIT_SECRETS_FILE, default ~/.config/api-keys.env, mode 600), since subagents and Claude Code's Bash tool do not
  source the shell profile; a key never reaches the cache key, the output or a log (#23);
- backoff on 429/5xx through devkit.backoff (#25): Retry-After when given, else full jitter for a 5xx and, for a
  throttle, a shared cooldown that doubles on each consecutive throttle and resets on a 200; each waiter wakes at a
  random 0-20 % past a cooldown's end, so a shared cooldown does not release every process at once; a source whose
  wait exceeds MAX_WAIT is skipped, so the others still answer; a cooldown is recorded for the keyed or the keyless
  pool, and only holds callers of that pool off;
- a fallback chain (`--source auto`, the default): OpenAlex and S2, then Crossref, Europe PMC and arXiv in turn
  until n papers are in hand, with a note naming the fallbacks that answered;
- compact output: one record per paper (title, year, venue, citations, ids, abstract snippet), capped;
- `dev lit status`: each source's health from a live probe; OK while at least one default source answers (partial
  names the ones down), DEGRADED and exit 1 only when all are down, so a research round never starts on the weaker
  fallbacks alone without knowing it (#8, #23).

stdlib only: pyalex and semanticscholar each cover one API and retry in-process, which does not share a rate limit
or a cache across processes, the part that failed.
"""

import contextlib
import fcntl
import hashlib
import html
import json
import os
import random
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from devkit import backoff, netsafe
from devkit.paths import state_dir

UA = "agent-harness-devkit-lit/0.1 (research tooling; mailto:noreply@example.invalid)"
CACHE_DAYS = 7
MAX_N = 25
DEFAULT_N = 8
SNIPPET = 240  # characters of abstract per record
MAX_WAIT = 60.0  # seconds: a longer Retry-After (a spent daily budget) gives up on that source for this call
TRIES = 4
# Requests per second and burst, per source, for the whole machine. Below the documented limits on purpose: OpenAlex
# allows 10/s, Crossref 1/s for searches in its public pool since 2026-07-21 (community.crossref.org/t/
# refining-rest-api-limits-for-improved-stability-and-reliability/16137), arXiv asks for one request per 3 s, and
# Semantic Scholar's shared unauthenticated pool throttles hard (1/s is its rate with a key). Europe PMC's REST page
# (europepmc.org/RestfulWebService) states no number; its team confirmed the limit is per IP and a user quoted
# 10/s or 500/min (groups.google.com/a/ebi.ac.uk/g/epmc-webservices/c/cZLnV1JhCj8, 2024-12), and it was seen
# throttling after a few rapid requests with a 503 and no Retry-After (github.com/hherb/bmlibrarian_lite/issues/341),
# so 1/s, burst 1, and its 503 counts as a 429.
RATES: dict[str, tuple[float, float]] = {
    "openalex": (5.0, 5.0),
    "s2": (1.0, 1.0),
    "crossref": (0.5, 1.0),
    "europepmc": (1.0, 1.0),
    "arxiv": (1 / 3, 1.0),
}
SOURCES = tuple(RATES)
DEFAULT_SOURCES = ("openalex", "s2")
# Asked in order, after DEFAULT_SOURCES, only while fewer than n papers are in hand (`--source auto`, the CLI default).
FALLBACK_SOURCES = ("crossref", "europepmc", "arxiv")
THROTTLE_503 = {"europepmc"}  # sources whose 503 means "slow down"
KEYS = {"openalex": "OPENALEX_API_KEY", "s2": "S2_API_KEY"}  # free keys raise a source above its shared pool
SECRETS_FILE = "~/.config/api-keys.env"  # DEVKIT_SECRETS_FILE overrides

Get = Callable[[str, dict[str, str]], tuple[int, dict[str, str], str]]  # (url, headers) -> (status, headers, body)


class SourceError(Exception):
    pass


# request headers that carry a key (authorization: dev reuse's GitHub token)
KEY_HEADERS = {"x-api-key", "authorization"}


class KeepKeysOnHost(netsafe.HttpOnlyRedirects):
    """A redirect to another host does not take the key header with it (urllib copies every header by default)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            for store in (new.headers, new.unredirected_hdrs):
                for name in [h for h in store if h.lower() in KEY_HEADERS]:
                    del store[name]
        return new


OPENER = urllib.request.build_opener(KeepKeysOnHost)


def http_get(url: str, headers: dict[str, str], timeout: int = 30) -> tuple[int, dict[str, str], str]:
    try:
        return netsafe.http_get(url, ua=UA, headers=headers, timeout=timeout, cap=2_000_000, opener=OPENER)
    except (ValueError, UnicodeEncodeError):  # a header or url urllib cannot send; its message may quote the key
        raise SourceError("key contains non-ASCII characters (or a control character); fix the secrets file") from None


# ---- rate limit: token bucket shared through a locked file ----


def hold_field(keyed: bool) -> str:
    """Where a cooldown is kept: a 429 to the keyless shared pool says nothing about a key's own limit, and back."""
    return "until_key" if keyed else "until"


def streak_fields(keyed: bool) -> tuple[str, str]:
    """(consecutive throttles, time of the last one) for the keyed or keyless pool."""
    f = hold_field(keyed)
    return f + "_streak", f + "_last"


def take(state: dict, now: float, rate: float, burst: float, keyed: bool = False) -> tuple[dict, float]:
    """One token from a bucket: (new state, seconds to wait first; 0 = granted now). Pure.

    state = {"tokens": float, "t": last refill time, "until"/"until_key": cooldown end of the keyless/keyed pool}. A
    refused call does not consume. Only the caller's own pool's cooldown holds it off."""
    tokens = min(burst, state.get("tokens", burst) + max(0.0, now - state.get("t", now)) * rate)
    new = {**state, "tokens": tokens, "t": now}
    cooldown = state.get(hold_field(keyed), 0.0) - now
    if cooldown > 0:
        return new, cooldown
    # A sleep of exactly the wait can leave 0.999... tokens; a wait below the clock's resolution would never pass.
    if tokens >= 1 or now + (1 - tokens) / rate == now:
        new["tokens"] = max(0.0, tokens - 1)
        return new, 0.0
    return new, (1 - tokens) / rate


@dataclass
class Bucket:
    """Machine-wide bucket per source in <dir>/buckets.json, read and written under flock."""

    dir: Path
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    rng: random.Random = field(default_factory=random.Random)
    rates: dict[str, tuple[float, float]] = field(default_factory=lambda: RATES)  # dev reuse passes its own sources

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
                netsafe.atomic_write(path, json.dumps(data))
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def acquire(self, source: str, keyed: bool = False) -> None:
        rate, burst = self.rates[source]
        while True:
            with self._state() as data:
                now = self.clock()
                data[source], wait = take(data.get(source, {}), now, rate, burst, keyed)
                held = data[source].get(hold_field(keyed), 0.0) > now
            if wait <= 0:
                return
            if wait > MAX_WAIT:  # a cooldown from a spent budget: skip the source instead of asking again
                raise SourceError(f"{source}: rate limited for another {wait:.0f}s (daily budget spent?); skipped")
            self.sleep(backoff.spread(wait, self.rng) if held else wait)  # waiters on one cooldown wake apart

    def held_off(self, source: str, keyed: bool = False) -> float:
        """Seconds of shared cooldown left on `source` for the keyed or keyless pool (0 = none). Takes no token."""
        with self._state() as data:
            return max(0.0, data.get(source, {}).get(hold_field(keyed), 0.0) - self.clock())

    def cooldown(self, source: str, seconds: float, keyed: bool = False) -> None:
        """Every process in the same pool (keyed or not) holds off `source` for `seconds` (a 429 told one to)."""
        with self._state() as data:
            st = data.setdefault(source, {})
            f = hold_field(keyed)
            st[f] = max(st.get(f, 0.0), self.clock() + seconds)

    def throttled(self, source: str, hint: float, keyed: bool = False) -> float:
        """A throttle answer: extend the pool's shared streak (a new one after backoff.WINDOW quiet) and hold every
        caller of the pool off for the server's hint, else for the escalated cooldown. Returns the hold's seconds."""
        n, last = streak_fields(keyed)
        with self._state() as data:
            st, now = data.setdefault(source, {}), self.clock()
            st[n] = (st.get(n, 0) if now - st.get(last, now) <= backoff.WINDOW else 0) + 1
            st[last] = now
            seconds = hint or backoff.escalated(st[n])
            f = hold_field(keyed)
            st[f] = max(st.get(f, 0.0), now + seconds)
            return seconds

    def succeeded(self, source: str, keyed: bool = False) -> None:
        """A 200 ends the pool's throttle streak: the next throttle starts again at backoff.BASE."""
        n, last = streak_fields(keyed)
        with self._state() as data:
            if source in data:
                data[source].pop(n, None)
                data[source].pop(last, None)


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
    for e in netsafe.parse_xml(body).findall("a:entry", ns):
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


def parse_europepmc(body: str) -> list[Paper]:
    """Europe PMC search, resultType=core: PubMed (source MED), PMC and preprints (source PPR)."""
    out = []
    for r in (json.loads(body).get("resultList") or {}).get("result", []):
        journal = ((r.get("journalInfo") or {}).get("journal") or {}).get("title") or r.get("journalTitle") or ""
        year = str(r.get("pubYear") or "")
        out.append(
            Paper(
                title=" ".join((r.get("title") or "").split()),
                year=int(year) if year.isdigit() else None,
                venue=journal or ("preprint" if r.get("source") == "PPR" else ""),
                cites=r.get("citedByCount"),
                ids={"doi": r.get("doi") or "", "pmid": r.get("pmid") or "", "pmcid": r.get("pmcid") or ""},
                abstract=html.unescape(re.sub(r"<[^>]+>", " ", r.get("abstractText") or "")),
                source="europepmc",
            )
        )
    return out


PARSE = {
    "openalex": parse_openalex,
    "s2": parse_s2,
    "crossref": parse_crossref,
    "europepmc": parse_europepmc,
    "arxiv": parse_arxiv,
}


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
        case "europepmc":
            if lo or hi:
                q = urllib.parse.quote(f"{query} AND PUB_YEAR:[{lo or '*'} TO {hi or '*'}]", safe="")
            return (
                f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={q}"
                f"&format=json&resultType=core&pageSize={n}"
            )
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


def parse_secrets(text: str) -> dict[str, str]:
    """KEYS values from `KEY=value` or `export KEY=value` lines, as a shell reads them: a quoted value ends at its
    closing quote, an unquoted one at ` #` (a comment). Every other line is ignored."""
    out = {}
    for line in text.splitlines():
        line = re.sub(r"^export\s+", "", line.strip())
        name, eq, value = line.partition("=")
        value = value.strip()
        if value[:1] in ("'", '"'):
            end = value.find(value[0], 1)
            value = value[1:end] if end > 0 else ""
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if eq and name.strip() in KEYS.values() and value:
            out[name.strip()] = value
    return out


def secrets_path(env: dict[str, str]) -> Path:
    return Path(env.get("DEVKIT_SECRETS_FILE") or SECRETS_FILE).expanduser()


def read_secrets(path: Path) -> tuple[dict[str, str], str]:
    """(keys in the file, problem). A missing file is no problem. Read only when it is a regular file (not a symlink
    or a FIFO), owned by this user, and nobody else may access it; the checks are made on the open handle, so the file
    cannot be swapped between check and read."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}, ""
    except OSError as exc:
        return {}, f"{path}: not read ({exc.strerror}; a symlink is refused)"
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        mode = st.st_mode & 0o777
        if not stat.S_ISREG(st.st_mode):
            return {}, f"{path} is not a regular file, so it is not read"
        if st.st_uid != os.getuid():
            return {}, f"{path} is owned by another user (uid {st.st_uid}), so it is not read"
        if mode & 0o077:
            return {}, f"{path} is readable by group or others (mode {mode:o}), so it is not read: chmod 600 {path}"
        try:
            return parse_secrets(f.read(65536).decode("utf-8-sig")), ""
        except (OSError, UnicodeDecodeError) as exc:
            return {}, f"{path}: unreadable ({type(exc).__name__})"


def with_secrets(env: dict[str, str]) -> tuple[dict[str, str], str]:
    """`env` with each unset key filled from the secrets file (read only when a key is unset), and its problem."""
    if all(env.get(k) for k in KEYS.values()):
        return dict(env), ""
    found, problem = read_secrets(secrets_path(env))
    return {**env, **{k: v for k, v in found.items() if not env.get(k)}}, problem


def key_report(env: dict[str, str]) -> tuple[dict[str, str], str]:
    """For `dev doctor`: each key's origin ("environment", "secrets file" or "missing"), never its value; a problem."""
    found, problem = ({}, "") if all(env.get(k) for k in KEYS.values()) else read_secrets(secrets_path(env))
    where = {k: "environment" if env.get(k) else "secrets file" if found.get(k) else "missing" for k in KEYS.values()}
    return where, problem


def with_key(source: str, url: str, env: dict[str, str]) -> tuple[str, dict[str, str]]:
    """The url and headers to send: API keys from env, added here so they never reach the cache key."""
    if source == "openalex" and (k := env.get("OPENALEX_API_KEY")):
        return url + ("&" if "?" in url else "?") + "api_key=" + urllib.parse.quote(k), {}
    if source == "s2" and (k := env.get("S2_API_KEY")):
        return url, {"x-api-key": k}
    return url, {}


def limit_403(status: int, headers: dict[str, str]) -> bool:
    """GitHub answers a spent rate limit with 403 and x-ratelimit-remaining 0, and a secondary limit with 403 and
    Retry-After: both are throttles, not refusals."""
    h = {k.lower(): v.strip() for k, v in headers.items()}
    return status == 403 and (h.get("x-ratelimit-remaining") == "0" or "retry-after" in h)


@dataclass
class Client:
    dir: Path
    get: Get = http_get
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    rng: random.Random = field(default_factory=random.Random)
    # Another tool's sources (dev reuse) share this cache, buckets and backoff with their own rates, keys and auth.
    rates: dict[str, tuple[float, float]] = field(default_factory=lambda: RATES)
    keys: dict[str, str] = field(default_factory=lambda: KEYS)
    auth: Callable[[str, str, dict[str, str]], tuple[str, dict[str, str]]] = with_key

    def __post_init__(self) -> None:
        (self.dir / "cache").mkdir(parents=True, exist_ok=True)
        self.bucket = Bucket(self.dir, self.clock, self.sleep, self.rng, self.rates)

    def _cache_path(self, url: str) -> Path:
        return self.dir / "cache" / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".json")

    def fetch(
        self, source: str, url: str, *, live: bool = False, tries: int = TRIES, max_age: float = CACHE_DAYS * 86400
    ) -> str:
        """Body of a 200 answer for `url`, from the shared cache when younger than `max_age` seconds (never when
        `live`). Raises SourceError otherwise."""
        path = self._cache_path(url)
        with contextlib.suppress(FileNotFoundError, json.JSONDecodeError, KeyError):
            rec = json.loads(path.read_text())
            if not live and self.clock() - rec["t"] < max_age:
                return rec["body"]
        send, headers = self.auth(source, url, self.env)
        keyed = self.keyed(source)
        status, body = 0, ""
        for attempt in range(tries):
            self.bucket.acquire(source, keyed)
            try:
                status, resp_headers, body = self.get(send, headers)
            except SourceError as exc:
                raise SourceError(f"{source}: {exc}") from None
            if status == 200:
                self.bucket.succeeded(source, keyed)
                netsafe.atomic_write(path, json.dumps({"t": self.clock(), "url": url, "body": body}))
                return body
            if status == 404:
                raise SourceError(f"{source}: not found")
            throttled = status == 429 or (status == 503 and source in THROTTLE_503) or limit_403(status, resp_headers)
            if not throttled and status != 0 and status < 500:
                raise SourceError(f"{source}: HTTP {status}")
            hint = backoff.hinted(resp_headers, self.clock())
            held = self.bucket.throttled(source, hint, keyed) if throttled else hint
            if held > MAX_WAIT:
                raise SourceError(f"{source}: rate limited for {held:.0f}s (daily budget spent?); skipped")
            if not throttled and attempt + 1 < tries:  # a throttle's wait is the cooldown, which acquire() honours
                self.sleep(backoff.wait(attempt, hint, self.rng, cap=MAX_WAIT))
        raise SourceError(f"{source}: {'HTTP ' + str(status) if status else 'unreachable'} after {tries} tries")

    def keyed(self, source: str) -> bool:
        return bool(self.env.get(self.keys.get(source, "")))

    def status(self, sources: tuple[str, ...] = SOURCES) -> list["Health"]:
        """Each source's health now: a shared cooldown, else one live, uncached probe search (one try, through the
        shared bucket, so a 429 here also holds every other process off)."""
        out = []
        for src in sources:
            keyed = "" if src not in KEYS else (" (key)" if self.keyed(src) else " (no key: shared pool)")
            if (left := self.bucket.held_off(src, self.keyed(src))) > 0:
                out.append(Health(src, False, f"rate limited for another {left:.0f}s{keyed}"))
                continue
            try:
                PARSE[src](self.fetch(src, search_url(src, PROBE, 1), live=True, tries=1))
            except SourceError as exc:
                out.append(Health(src, False, str(exc).removeprefix(f"{src}: ") + keyed))
            except (ValueError, ET.ParseError) as exc:
                out.append(Health(src, False, f"unreadable answer ({type(exc).__name__}){keyed}"))
            else:
                out.append(Health(src, True, "ok" + keyed))
        return out

    def search(
        self,
        query: str,
        sources: tuple[str, ...] = DEFAULT_SOURCES,
        n: int = DEFAULT_N,
        years: str = "",
        *,
        fallback: tuple[str, ...] = (),
    ):
        """(papers merged across sources in rank order, alternating sources, at most n; notes on failed sources).

        While fewer than n papers are in hand, each `fallback` source is asked in turn and its papers appended; a
        note says which of them answered."""
        n = max(1, min(n, MAX_N))
        merged: dict[str, Paper] = {}
        notes: list[str] = []
        ranked = [r for src in sources if (r := self._ranked(src, query, n, years, notes)) is not None]
        for i in range(max(map(len, ranked), default=0)):
            for papers in ranked:
                if i < len(papers):
                    _merge(merged, papers[i])
        answered = []
        for src in fallback:
            if len(merged) >= n:
                break
            before = len(merged)
            for p in self._ranked(src, query, n, years, notes) or []:
                if len(merged) >= n:
                    break
                _merge(merged, p)
            if len(merged) > before:
                answered.append(f"{src} ({len(merged) - before})")
        if answered:
            notes.append("fallback: answered by " + ", ".join(answered))
        return list(merged.values())[:n], notes

    def _ranked(self, src: str, query: str, n: int, years: str, notes: list[str]) -> list[Paper] | None:
        """One source's papers in its rank order, or None (with a note) when it failed."""
        try:
            papers = PARSE[src](self.fetch(src, search_url(src, query, n, years)))
        except SourceError as exc:
            notes.append(str(exc))
            return None
        except (ValueError, ET.ParseError) as exc:  # json.JSONDecodeError is a ValueError
            notes.append(f"{src}: unreadable answer ({type(exc).__name__})")
            return None
        return [p for p in papers if p.title]

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


def client() -> Client:
    env, problem = with_secrets(dict(os.environ))
    if problem:
        print(f"dev lit: {problem}", file=sys.stderr)
    return Client(state_dir("lit"), env=env)


def render(papers: list[Paper], notes: list[str], snippet: int = SNIPPET) -> str:
    lines = [p.line(snippet) for p in papers] or ["no results"]
    lines += [f"note: {n}" for n in notes]
    return "\n".join(lines)


PROBE = "memory"  # the query `status` searches for; any common word answers on every source


@dataclass
class Health:
    source: str
    ok: bool
    detail: str  # "ok" or why not (a cooldown, an HTTP status, unreachable), then "(key)" or "(no key: shared pool)"


def down(health: list[Health]) -> list[str]:
    """The default sources that are down."""
    return [h.source for h in health if not h.ok and h.source in DEFAULT_SOURCES]


def degraded(health: list[Health]) -> bool:
    """No default source answers: discovery leans on them, and the fallbacks alone are weaker (#8). One working
    major index is enough (#23)."""
    defaults = [h for h in health if h.source in DEFAULT_SOURCES]
    return bool(defaults) and not any(h.ok for h in defaults)


def render_status(health: list[Health]) -> str:
    lines = [f"{h.source:<10} {'ok  ' if h.ok else 'DOWN'}  {h.detail}" for h in health]
    answered = ", ".join(h.source for h in health if h.ok) or "none"
    if degraded(health):
        lines.append(
            f"discovery: DEGRADED ({', '.join(down(health))} down): do not start a research round; wait and re-run"
        )
    elif bad := down(health):
        lines.append(f"discovery: OK, partial ({', '.join(bad)} down; {answered} answered)")
    else:
        lines.append(f"discovery: OK ({answered} answered)")
    return "\n".join(lines)


def plan(spec: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """--source to (sources, fallback): 'auto' (or empty) is DEFAULT_SOURCES, then FALLBACK_SOURCES until n papers;
    an explicit list or 'all' asks exactly those."""
    if spec.strip() in ("", "auto"):
        return DEFAULT_SOURCES, FALLBACK_SOURCES
    return parse_sources(spec), ()


def parse_sources(spec: str) -> tuple[str, ...]:
    if spec == "all":
        return SOURCES
    out = tuple(s.strip() for s in spec.split(",") if s.strip())
    if bad := [s for s in out if s not in RATES]:
        raise ValueError(f"unknown source(s) {', '.join(bad)}; use {', '.join(SOURCES)}, all or auto")
    return out or DEFAULT_SOURCES
