"""Reuse before build: `uv run dev reuse check <name>...` verifies the packages an agent names; `uv run dev reuse
"<need>"` searches for maintained libraries and repositories when it knows no good candidate (#29).

#169 (docs/research/software-discovery.md, TEST, n = 112) measured both: the agent's own names, verified by gates
G1-G6, hit the benchmark's answer in the top 3 for 0.83 of needs; search alone (with those names added to its pool)
for 0.71. So check mode is the default step and search an optional recall aid. check: per name, the registry record
(ecosyste.ms, else the registry itself), enrichment, G1-G6 and the metadata line; a name no source knows is
NOT FOUND (the package-hallucination guard). search:

- queries: the need, each `--also` paraphrase the agent writes, or `--from <file>[:<symbol>]`, which reads a
  hand-written helper's name, docstring, comments and imports (devkit.code.source) so it gets library suggestions.
  Each query is stopword-relaxed to at most QUERY_TERMS words;
- recall: GitHub repository search per query (is:public, so a token never brings private repositories in) (an OR query when the AND one returns under OR_FALLBACK), the
  packages.ecosyste.ms keyword lists of the query words (filtered to the ecosystem: the endpoint ignores it), and
  topic expansion (topics shared by TOPIC_MIN of the TOPIC_TOP first repositories). Candidates are deduplicated by
  repository;
- relevance R (0-1): cosine to the mean query vector with model2vec potion-base-8M (MIT; the optional extra
  `agent-harness[semantic]`), else BM25 scaled to the best candidate. The output names the re-rank that ran;
- shortlist (union): the n best by R plus the n most adopted (dependents, then monthly downloads) among candidates
  within R_FLOOR of the best R. All are enriched, gated and scored before the cut to n;
- gates (#169 note, F2), shown per candidate; a FAIL is dropped with a note, a HOLD is shown with its reason:
  G1 the name exists on its registry (PyPI, npm, crates.io) or GitHub, the package-hallucination guard (arXiv
  2406.10279), else FAIL, asked again after REGISTRY_MAX_AGE; G2 no malicious-package advisory (an OSV MAL- id on
  deps.dev, or a GitHub MALWARE advisory on ecosyste.ms), else FAIL, and HOLD when neither answered; G3 first release
  at least NEW_DAYS ago, else HOLD; G4 not archived, deprecated or wholly yanked (else FAIL) and released within
  DORMANT_DAYS (else HOLD); G5 licence by Blue Oak Council tier: permissive (Model to
  Bronze) passes, weak copyleft passes with a flag, strong, network or maximal copyleft FAILs unless
  `--allow-copyleft`, unknown or Lead HOLDs; G6 advisories open on the default version are flagged, never failed.
  A licence that is not OSI-approved (spdx.org) or missing is flagged too;
- quality Q (0-1, `quality()`): the sum of Q_WEIGHTS (developer survey shares, arXiv 2005.12574) over maturity
  (months since the first release, release count), documentation (a docs URL, else README size), maintenance (the larger of Scorecard
  Maintained / 10 and release recency: 1 up to FRESH_DAYS, 0 at DORMANT_DAYS), security (Scorecard / 10, 0.5 when
  unknown), popularity (monthly downloads, stars) and community (contributors, else forks); each raw signal s is
  normalised as log(1+s) / log(1+max(s, T)) with its cap T (OpenSSF criticality_score). Sources: ecosyste.ms,
  deps.dev v3, GitHub;
- order (#169's R4): every PASS before every HOLD (PASS_BEFORE_HOLD), each group by S = R + LAMBDA * Q. On #169's
  TEST split R4 beat PR #171's first order (prior shortlist, PASS and HOLD mixed, LAMBDA 0.15) by +0.09 hit@3
  [+0.02, +0.16], and the HOLD share of the top 3 fell from 0.45 to 0.06. Every weight and threshold is a named
  constant.

Every request goes through dev lit's client: the same machine-wide token buckets, cache and backoff
(devkit.lit.Client with this module's RATES). A source that is down is a note, never fatal.
"""

import ast
import contextlib
import dataclasses
import datetime as dt
import importlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import tokenize
import urllib.parse
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from devkit import code, lit
from devkit.paths import state_dir

# Requests per second and burst per source. GitHub's search API allows 10 requests a minute without a token and 30
# with one; ecosyste.ms, deps.dev, spdx.org, Blue Oak and the registries state no lower limit for this volume;
# crates.io asks for at most one request a second.
RATES: dict[str, tuple[float, float]] = {
    "github": (0.15, 2.0),
    "githubcore": (1.0, 2.0),  # repos/<o>/<r>/readme: GitHub's core API, 5000 an hour with a token, 60 without
    "ecosystems": (2.0, 4.0),
    "depsdev": (5.0, 5.0),
    "spdx": (1.0, 1.0),
    "blueoak": (1.0, 2.0),
    "pypi": (5.0, 5.0),
    "npm": (5.0, 5.0),
    "cargo": (1.0, 1.0),
}
KEYS = {"github": "GITHUB_TOKEN", "githubcore": "GITHUB_TOKEN"}  # GH_TOKEN or `gh auth token` fill it (client())

GITHUB = "https://api.github.com/search/repositories?"
GITHUB_REPOS = "https://api.github.com/repos/"
ECOSYSTEMS = "https://packages.ecosyste.ms/api/v1/"
DEPSDEV = "https://api.deps.dev/v3/"
SPDX = "https://spdx.org/licenses/licenses.json"
BLUEOAK = ("https://blueoakcouncil.org/list.json", "https://blueoakcouncil.org/copyleft.json")
# ecosystem -> (ecosyste.ms and deps.dev name, GitHub language qualifier, registry url for a name)
ECOS: dict[str, tuple[str, str, Callable[[str], str]]] = {
    "pypi": ("pypi", "python", lambda n: f"https://pypi.org/pypi/{urllib.parse.quote(n, safe='')}/json"),
    "npm": ("npm", "", lambda n: f"https://registry.npmjs.org/{urllib.parse.quote(n, safe='@')}/latest"),
    "cargo": ("cargo", "rust", lambda n: f"https://crates.io/api/v1/crates/{urllib.parse.quote(n, safe='')}"),
}
REGISTRY_HOST = {"pypi": "pypi.org", "npm": "npmjs.org", "cargo": "crates.io"}  # ecosyste.ms registry names
LANG_ECO = {"python": "pypi", "javascript": "npm", "typescript": "npm", "rust": "cargo"}

DEFAULT_N = 10  # #169 measured every order at n = 10
MAX_N = 25
QUERY_TERMS = 6  # GitHub allows at most five AND/OR operators per query
OR_FALLBACK = 5  # an AND query with fewer results is asked again with OR
GH_PER_PAGE = 20
KEYWORD_CAP = 4  # ecosyste.ms keyword lists asked per run
KEYWORD_PER_PAGE = 50  # each record is ~13 kB; 50 stays under the client's 2 MB read cap
TOPIC_TOP, TOPIC_MIN, TOPIC_CAP = 10, 2, 2
TOPIC_STOP = {"python", "python3", "javascript", "typescript", "nodejs", "rust", "hacktoberfest", "library"}
MODEL = "minishlab/potion-base-8M"
BM25_K1, BM25_B = 1.5, 0.75
NEED_WORDS = 40  # words of a --from need

# Search order R4 of docs/research/software-discovery.md (#169), chosen on TUNE, confirmed on TEST.
PASS_BEFORE_HOLD = True  # every PASS above every HOLD: the largest single effect (#169 TUNE: +0.09 hit@3)
R_FLOOR = 0.20  # union shortlist: the adoption half draws from candidates with R >= best R - R_FLOOR (fixed, untuned)
LAMBDA = 1.0  # S = R + LAMBDA * Q; fitted on #169's TUNE split (the cited prior was 0.15). No tier rule.
Q_WEIGHTS = {
    "maturity": 0.242,
    "documentation": 0.199,
    "maintenance": 0.172,
    "security": 0.152,
    "popularity": 0.117,
    "community": 0.117,
}
T_MONTHS, T_RELEASES, T_DOWNLOADS, T_STARS, T_CONTRIBUTORS, T_FORKS = 60, 50, 1e6, 1e4, 50, 50  # caps T
T_README = 10_000  # bytes: the documentation factor without a docs URL
REGISTRY_MAX_AGE = 86400  # seconds: G1 answers are re-asked daily, so a pulled package stops verifying soon
SECURITY_UNKNOWN = 0.5
FRESH_DAYS = 180  # release recency 1 up to here, falling linearly to 0 at DORMANT_DAYS
NEW_DAYS = 90  # G3: a first release more recent than this holds
DORMANT_DAYS = 730  # G4: no release for longer than this holds
PERMISSIVE = {"Model", "Gold", "Silver", "Bronze"}  # Blue Oak tiers that pass G5; Lead holds
WEAK, COPYLEFT_FAIL = "weak", {"strong", "network", "maximal"}

STOP = set(
    """a an and are as at be by can do for from has have how i if in into is it its of on or that the this to was
    with without we you your use using used get set make need needs want library libraries package packages tool
    tools python implement implementation simple small fast easy way some any all each every not no yes then than
    when which what who will until between before after would should could may might also just via per my our self cls none true false return
    def class import args kwargs value values thing things one two new""".split()  # noqa: SIM905
)

PASS, HOLD, FAIL, FLAG = "pass", "HOLD", "FAIL", "flag"
SEVERITY = {PASS: 0, FLAG: 0, "n/a": 0, HOLD: 1, FAIL: 2}


@dataclass
class Candidate:
    name: str  # package name, else the repository's owner/name
    repo: str = ""  # github.com/owner/name, lower case: the deduplication key
    ecosystem: str = ""  # "" for a repository without a package
    description: str = ""
    topics: list[str] = field(default_factory=list)
    stars: int | None = None
    forks: int | None = None
    license: str = ""
    first_release: str = ""  # ISO dates
    last_release: str = ""
    last_push: str = ""
    releases: int | None = None
    releases_last_year: int | None = None
    downloads_month: int | None = None
    contributors: int | None = None
    dependents: int | None = None
    scorecard: float | None = None
    maintained: float | None = None  # the Scorecard Maintained check, 0-10
    docs: str = ""
    readme_bytes: int | None = None
    advisories: int | None = None  # open on the default version
    malicious: list[str] = field(default_factory=list)
    malware_checked: bool = False  # deps.dev or an ecosyste.ms record answered with its advisories (G2)
    archived: bool = False
    deprecated: bool = False
    yanked: bool = False  # every release yanked (G4)
    verified: str = ""  # who answered for the name: "github" or the registry
    relevance: float = 0.0
    quality: float = 0.0
    score: float = 0.0
    status: str = PASS
    gates: dict[str, str] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    found_by: list[str] = field(default_factory=list)
    record: dict | None = field(default=None, repr=False)  # the ecosyste.ms package record, not printed

    def key(self) -> str:
        return self.repo or f"{self.ecosystem}:{self.name}"

    def text(self) -> str:
        return " ".join([self.name.replace("-", " ").replace("_", " "), self.description, *self.topics])


# ---- queries ----


def words(text: str) -> list[str]:
    """Lower-case words, snake_case and camelCase split."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w]


def relax(query: str) -> list[str]:
    """The query's distinct non-stopword words, at most QUERY_TERMS."""
    out: list[str] = []
    for w in words(query):
        if len(w) > 1 and w not in STOP and not w.isdigit() and w not in out:
            out.append(w)
    return out[:QUERY_TERMS]


def need_from(text: str, name: str = "") -> str:
    """A need written from code: its definitions' names, docstrings, comments and imported modules."""
    parts = [name.split(".")[-1]] if name else []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                parts.append(node.name)
            if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and (
                doc := ast.get_docstring(node)
            ):
                parts.append(doc.split("\n\n")[0])
    with contextlib.suppress(tokenize.TokenError, IndentationError, SyntaxError):  # comments up to an error
        for t in tokenize.generate_tokens(io.StringIO(text).readline):
            if t.type == tokenize.COMMENT:
                parts.append(t.string.lstrip("#"))
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                parts += [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                parts.append(node.module.split(".")[0])
    seen: list[str] = []
    for w in words(" ".join(parts)):
        if w not in seen and w not in STOP and len(w) > 1:
            seen.append(w)
    return " ".join(seen[:NEED_WORDS])


def from_spec(spec: str) -> str:
    """`<file>[:<symbol>]` to a need (LookupError when the file or symbol is missing)."""
    path, _, symbol = spec.partition(":")
    text = code.source(path)
    if not symbol:
        return need_from(text)
    try:  # a symbol's own text, plus the file's imports: what the helper leans on
        imports = [ast.unparse(n) for n in ast.parse(text).body if isinstance(n, ast.Import | ast.ImportFrom)]
    except SyntaxError:
        imports = []
    return need_from("\n".join([code.source(path, symbol), *imports]), symbol)


# ---- recall ----


def _json(client: lit.Client, source: str, url: str, notes: list[str], missing_ok: bool = False):
    """The parsed answer, or None with a note: a source being down is reported, never fatal. A 404 is no note when
    `missing_ok` (deps.dev knows no package or project of that name: the field stays unknown)."""
    try:
        return json.loads(client.fetch(source, url))
    except lit.SourceError as exc:
        if not (missing_ok and str(exc).endswith(": not found")):
            notes.append(str(exc))
    except ValueError as exc:
        notes.append(f"{source}: unreadable answer ({type(exc).__name__})")
    return None


def repo_key(url: str | None) -> str:
    m = re.match(r"(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/([^/\s#?]+)", (url or "").strip())
    return f"github.com/{m[1]}/{m[2].removesuffix('.git')}".lower() if m else ""


def github(client: lit.Client, q: str, notes: list[str]) -> list[Candidate] | None:
    data = _json(client, "github", GITHUB + urllib.parse.urlencode({"q": q, "per_page": GH_PER_PAGE}), notes)
    if not isinstance(data, dict):
        return None
    out = []
    for r in data.get("items") or []:
        out.append(
            Candidate(
                name=r.get("full_name", ""),
                repo=repo_key(r.get("html_url")),
                description=r.get("description") or "",
                topics=list(r.get("topics") or []),
                stars=r.get("stargazers_count"),
                forks=r.get("forks_count"),
                license=((r.get("license") or {}).get("spdx_id") or "").replace("NOASSERTION", ""),
                last_push=(r.get("pushed_at") or "")[:10],
                archived=bool(r.get("archived")),
                ecosystem=LANG_ECO.get((r.get("language") or "").lower(), ""),
                verified="github",
                found_by=["github"],
            )
        )
    return [c for c in out if c.repo]


def _checks(card: dict) -> dict[str, float]:
    return {c["name"]: c.get("score", -1) for c in card.get("checks") or [] if isinstance(c, dict) and "name" in c}


def from_record(p: dict) -> Candidate:
    """A candidate from an ecosyste.ms package record (keywords or lookup)."""
    rm = p.get("repo_metadata") or {}
    card = (rm.get("scorecard") or {}).get("data") or {}
    maintained = _checks(card).get("Maintained")
    deps = [p.get("dependent_repos_count"), p.get("dependent_packages_count")]
    monthly = "month" in (p.get("downloads_period") or "")
    mal = [
        i
        for a in p.get("advisories") or []
        if not a.get("withdrawn_at")
        for i in a.get("identifiers") or []
        if i.startswith("MAL-") or (a.get("classification") or "").upper() == "MALWARE"
    ]
    return Candidate(
        name=p.get("name", ""),
        repo=repo_key(p.get("repository_url")),
        ecosystem=p.get("ecosystem", ""),
        description=p.get("description") or rm.get("description") or "",
        topics=list(rm.get("topics") or p.get("keywords_array") or []),
        stars=rm.get("stargazers_count"),
        forks=rm.get("forks_count"),
        license=" AND ".join(p.get("normalized_licenses") or []),
        first_release=(p.get("first_release_published_at") or "")[:10],
        last_release=(p.get("latest_release_published_at") or "")[:10],
        last_push=(rm.get("pushed_at") or "")[:10],
        releases=p.get("versions_count"),
        downloads_month=p.get("downloads") if monthly else None,
        contributors=(rm.get("commit_stats") or {}).get("total_committers"),
        dependents=sum(d for d in deps if d) if any(d is not None for d in deps) else None,
        scorecard=card.get("score"),
        maintained=maintained if maintained is not None and maintained >= 0 else None,
        docs=p.get("documentation_url") or "",
        malicious=list(dict.fromkeys(mal)),
        malware_checked=isinstance(p.get("advisories"), list),
        archived=bool(rm.get("archived")),
        deprecated=(p.get("status") or "") in {"deprecated", "removed"},
        found_by=["ecosyste.ms"],
        record=p,
    )


def keyword(client: lit.Client, term: str, ecos: tuple[str, ...], notes: list[str]) -> list[Candidate]:
    url = f"{ECOSYSTEMS}keywords/{urllib.parse.quote(term, safe='')}?per_page={KEYWORD_PER_PAGE}"
    data = _json(client, "ecosystems", url, notes)
    pkgs = data.get("packages") if isinstance(data, dict) else None
    return [from_record(p) for p in pkgs or [] if p.get("ecosystem") in ecos and p.get("name")]


MERGED = ("found_by", "topics", "malicious")
BOOLS = (
    "archived",
    "deprecated",
    "yanked",
    "malware_checked",
)  # unions; every other empty field takes the other source's value


def merge(into: dict[str, Candidate], c: Candidate) -> None:
    """Same repository (or package) twice: one candidate with the union of what each source knew. A package record
    names the candidate and gives its licence, whichever source came first."""
    k = c.key()
    if k not in into:
        into[k] = c
        return
    a = into[k]
    if c.record is not None and a.record is None:
        a.record, a.name, a.ecosystem = c.record, c.name, c.ecosystem
        a.license = c.license or a.license  # what is installed is the package, under the registry's licence
    for f in dataclasses.fields(Candidate):
        mine, theirs = getattr(a, f.name), getattr(c, f.name)
        if f.name in MERGED:
            setattr(a, f.name, list(dict.fromkeys(mine + theirs)))
        elif f.name in BOOLS:
            setattr(a, f.name, mine or theirs)
        elif mine is None or mine == "":
            setattr(a, f.name, theirs)


def recall(client: lit.Client, queries: list[str], ecosystem: str, notes: list[str]) -> dict[str, Candidate]:
    ecos = tuple(ECOS) if ecosystem == "any" else (ecosystem,)
    lang = "" if ecosystem == "any" else ECOS[ecosystem][1]
    # is:public: a gh token also finds the user's private repositories, which must never reach output or the cache
    qual = " is:public" + (f" language:{lang}" if lang else "")
    found: dict[str, Candidate] = {}
    repos: list[Candidate] = []
    asked: set[str] = set()

    def ask(q: str) -> list[Candidate] | None:
        if q in asked:
            return []
        asked.add(q)
        got = github(client, q + qual, notes)
        repos.extend(got or [])
        return got

    for query in queries:
        terms = relax(query)
        if not terms:
            continue
        got = ask(" ".join(terms))
        if got is not None and len(got) < OR_FALLBACK and len(terms) > 1:
            ask(" OR ".join(terms))
    shared = Counter(t for c in repos[:TOPIC_TOP] for t in set(c.topics) if t not in TOPIC_STOP)
    for topic, n in shared.most_common(TOPIC_CAP):
        if n >= TOPIC_MIN:
            ask(f"topic:{topic}")
    for c in repos:
        merge(found, c)
    terms = list(dict.fromkeys(t for q in queries for t in relax(q)))
    for term in terms[:KEYWORD_CAP]:
        for c in keyword(client, term, ecos, notes):
            merge(found, c)
    return found


# ---- relevance ----


def cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=False)) / (na * nb) if na and nb else 0.0


def bm25(docs: list[list[str]], query_terms: list[list[str]]) -> list[float]:
    """Mean BM25 of each doc over the queries, scaled so the best doc scores 1."""
    n = len(docs)
    if not n:
        return []
    avg = sum(map(len, docs)) / n or 1.0
    df = Counter(t for d in docs for t in set(d))
    scores = []
    for d in docs:
        tf, total = Counter(d), 0.0
        for q in query_terms:
            for t in set(q):
                if f := tf.get(t):
                    idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                    total += idf * f * (BM25_K1 + 1) / (f + BM25_K1 * (1 - BM25_B + BM25_B * len(d) / avg))
        scores.append(total / max(1, len(query_terms)))
    top = max(scores) or 1.0
    return [s / top for s in scores]


Encoder = Callable[[list[str]], list[list[float]]]


def load_encoder() -> tuple[Encoder | None, str]:
    """model2vec when the `semantic` extra is installed and the model loads, else None and why."""
    try:
        m2v = importlib.import_module("model2vec")
    except ImportError:
        return None, "model2vec not installed (uv add 'agent-harness[semantic]')"
    try:
        model = m2v.StaticModel.from_pretrained(MODEL)
    except Exception as exc:  # a download or load failure falls back, it does not stop the search
        return None, f"model2vec could not load {MODEL} ({type(exc).__name__})"
    return (lambda texts: model.encode(texts).tolist()), ""


def rank_relevance(cands: list[Candidate], queries: list[str], encoder: Encoder | None, why: str = "") -> str:
    """Set each candidate's relevance R (0-1); return which re-rank ran."""
    if not cands:
        return "none"
    if encoder is not None:
        qv = encoder(queries)
        mean = [sum(col) / len(qv) for col in zip(*qv, strict=True)]
        sims = [cosine(list(v), mean) for v in encoder([c.text() for c in cands])]
        label = f"semantic (model2vec {MODEL})"
    else:
        sims = bm25([words(c.text()) for c in cands], [words(q) for q in queries])
        label = "bm25" + (f" ({why})" if why else "")
    for c, s in zip(cands, sims, strict=True):
        c.relevance = round(max(0.0, min(1.0, s)), 4)
    return label


# ---- enrichment and verification ----


def _days(iso: str, now: float) -> float | None:
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.UTC)
    return (now - t.timestamp()) / 86400


def _int(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def enrich(client: lit.Client, c: Candidate, ecosystem: str, notes: list[str]) -> None:
    """Find the package behind a repository (ecosyste.ms), then release dates, deprecation, advisories and Scorecard
    from deps.dev."""
    eco = c.ecosystem if ecosystem == "any" else ecosystem
    if c.record is None and c.repo and eco in ECOS:
        url = f"{ECOSYSTEMS}packages/lookup?" + urllib.parse.urlencode(
            {"repository_url": "https://" + c.repo, "ecosystem": ECOS[eco][0]}
        )
        recs = _json(client, "ecosystems", url, notes, missing_ok=True)
        if isinstance(recs, list) and (recs := [r for r in recs if isinstance(r, dict) and r.get("name")]):
            pkg = from_record(max(recs, key=lambda r: (r.get("dependent_repos_count") or 0, r.get("downloads") or 0)))
            pkg.repo = c.repo  # the repository GitHub answered for
            merge({c.key(): c}, pkg)
    if c.record is None:
        c.ecosystem = ""
        c.name = c.repo.removeprefix("github.com/") if c.repo else c.name
    if c.ecosystem in ECOS:
        base = f"{DEPSDEV}systems/{ECOS[c.ecosystem][0]}/packages/{urllib.parse.quote(c.name, safe='')}"
        pkg = _json(client, "depsdev", base, notes, missing_ok=True)
        versions = pkg.get("versions") if isinstance(pkg, dict) else None
        if versions:
            now = client.clock()
            dates = sorted(d for v in versions if (d := v.get("publishedAt")))
            c.releases_last_year = sum(1 for d in dates if (_days(d, now) or 1e9) <= 365)
            c.releases = c.releases or len(versions)
            if dates:
                c.first_release = c.first_release or dates[0][:10]
                c.last_release = c.last_release or dates[-1][:10]
            default = next((v for v in versions if v.get("isDefault")), versions[-1])
            ver = (default.get("versionKey") or {}).get("version", "")
            if ver:
                v = _json(client, "depsdev", f"{base}/versions/{urllib.parse.quote(ver, safe='')}", notes, True)
                if isinstance(v, dict):
                    c.malware_checked = True
                    keys = v.get("advisoryKeys") or []
                    ids = [k.get("id", "") if isinstance(k, dict) else str(k) for k in keys]
                    c.malicious = list(dict.fromkeys(c.malicious + [i for i in ids if i.startswith("MAL-")]))
                    c.advisories = len([i for i in ids if not i.startswith("MAL-")])
                    c.deprecated = c.deprecated or bool(v.get("isDeprecated"))
                    c.license = c.license or " AND ".join(v.get("licenses") or [])
                    links = {x.get("label"): x.get("url") for x in v.get("links") or [] if isinstance(x, dict)}
                    c.docs = c.docs or links.get("DOCUMENTATION") or ""
    if not c.docs and c.repo:
        readme = _json(client, "githubcore", f"{GITHUB_REPOS}{c.repo.removeprefix('github.com/')}/readme", notes, True)
        if isinstance(readme, dict):
            c.readme_bytes = _int(readme.get("size"))
    if (c.scorecard is None or c.maintained is None) and c.repo:
        proj = _json(client, "depsdev", f"{DEPSDEV}projects/{urllib.parse.quote(c.repo, safe='')}", notes, True)
        if isinstance(proj, dict):
            card = proj.get("scorecard") or {}
            c.scorecard = c.scorecard if c.scorecard is not None else card.get("overallScore")
            m = _checks(card).get("Maintained")
            c.maintained = c.maintained if c.maintained is not None else (m if m is not None and m >= 0 else None)
            c.stars = c.stars if c.stars is not None else _int(proj.get("starsCount"))
            c.forks = c.forks if c.forks is not None else _int(proj.get("forksCount"))
            c.license = c.license or proj.get("license") or ""


def verify(client: lit.Client, c: Candidate, notes: list[str]) -> bool:
    """G1: True when the printed name exists, the package on its registry, else the repository on GitHub."""
    if c.ecosystem in ECOS:
        try:
            body = client.fetch(c.ecosystem, ECOS[c.ecosystem][2](c.name), max_age=REGISTRY_MAX_AGE)
        except lit.SourceError as exc:
            if c.verified == "github" and c.repo:  # the package is gone or unverifiable; the repository stands
                notes.append(f"{c.repo}: package {c.name} not verified on {c.ecosystem} ({exc}); shown as a repository")
                c.ecosystem, c.name = "", c.repo.removeprefix("github.com/")
                return True
            notes.append(f"dropped {c.ecosystem}:{c.name}: G1 FAIL, not verified on its registry ({exc})")
            return False
        c.verified = c.ecosystem
        registry_state(c, body)
        return True
    return c.verified == "github"


def registry_state(c: Candidate, body: str) -> None:
    """From the registry's own answer: every release yanked (PyPI files, crates.io versions), or npm's deprecation."""
    try:
        data = json.loads(body)
    except ValueError:  # a long PyPI answer is cut at the read cap: the state stays unknown
        return
    if not isinstance(data, dict):
        return
    if c.ecosystem == "pypi":
        files = [f for fs in (data.get("releases") or {}).values() for f in fs or []]
        c.yanked = bool(files) and all(f.get("yanked") for f in files)
    elif c.ecosystem == "cargo":
        versions = data.get("versions") or []
        c.yanked = bool(versions) and all(v.get("yanked") for v in versions)
    elif c.ecosystem == "npm" and data.get("deprecated"):
        c.deprecated = True


# ---- quality and gates ----


def norm(s: float | None, cap: float) -> float:
    """log(1+s) / log(1+max(s, cap)): bounded, saturating at the cap (OpenSSF criticality_score)."""
    if not s or s <= 0:
        return 0.0
    return math.log1p(s) / math.log1p(max(s, cap))


def recency(iso: str, now: float) -> float:
    """1 for a release within FRESH_DAYS, falling linearly to 0 at DORMANT_DAYS; 0 when unknown."""
    age = _days(iso, now) if iso else None
    return 0.0 if age is None else max(0.0, min(1.0, (DORMANT_DAYS - age) / (DORMANT_DAYS - FRESH_DAYS)))


def factors(c: Candidate, now: float) -> dict[str, float]:
    """Each quality factor of Q_WEIGHTS in 0-1 (module docstring)."""
    months = (_days(c.first_release, now) or 0) / 30.44 if c.first_release else None
    return {
        "maturity": (norm(months, T_MONTHS) + norm(c.releases, T_RELEASES)) / 2,
        "documentation": 1.0 if c.docs else norm(c.readme_bytes, T_README),
        "maintenance": max((c.maintained or 0) / 10, recency(c.last_release or c.last_push, now)),
        "security": c.scorecard / 10 if c.scorecard is not None else SECURITY_UNKNOWN,
        "popularity": (norm(c.downloads_month, T_DOWNLOADS) + norm(c.stars, T_STARS)) / 2,
        "community": norm(c.contributors, T_CONTRIBUTORS) if c.contributors is not None else norm(c.forks, T_FORKS),
    }


def quality(c: Candidate, now: float) -> float:
    return round(sum(Q_WEIGHTS[k] * v for k, v in factors(c, now).items()), 4)


def licence_lists(client: lit.Client, notes: list[str]) -> tuple[dict[str, str] | None, dict[str, bool] | None]:
    """(SPDX id -> Blue Oak tier or copyleft family, SPDX id -> OSI-approved), each None when its list is down."""
    tiers: dict[str, str] | None = None
    ratings, copyleft = (_json(client, "blueoak", u, notes) for u in BLUEOAK)
    if isinstance(ratings, dict) and isinstance(copyleft, dict):
        tiers = {
            lic["id"]: r["name"] for r in ratings.get("ratings", []) for lic in r.get("licenses", []) if "id" in lic
        }
        for kind, fams in (copyleft.get("families") or {}).items():
            tiers.update({v["id"]: kind for f in fams for v in f.get("versions", []) if "id" in v})
    spdx = _json(client, "spdx", SPDX, notes)
    osi = None
    if isinstance(spdx, dict):
        osi = {x["licenseId"]: bool(x.get("isOsiApproved")) for x in spdx.get("licenses", []) if "licenseId" in x}
    return tiers, osi


def _ids(alt: str) -> list[str]:
    return [t for t in re.findall(r"[A-Za-z0-9.+-]+", alt) if t not in {"AND", "WITH"}]


def _lookup(table: dict, lic: str):
    """An SPDX id's entry, also under its current name (GPL-3.0 is GPL-3.0-only, GPL-3.0+ GPL-3.0-or-later)."""
    for k in (lic, lic + "-only", lic.removesuffix("+") + "-or-later"):
        if k in table:
            return table[k]
    return None


def licence_gate(lic: str, tiers: dict[str, str] | None, allow_copyleft: bool) -> tuple[str, str]:
    """G5: (outcome, why). The best alternative of an OR wins; within one, the worst licence of an AND counts."""
    if not lic:
        return HOLD, "no licence found"
    if tiers is None:
        return HOLD, "licence not checked (Blue Oak lists unavailable)"
    rank = {**dict.fromkeys(PERMISSIVE, 0), WEAK: 1, "Lead": 2, None: 2, **dict.fromkeys(COPYLEFT_FAIL, 3)}
    best: tuple[int, str] | None = None
    for alt in lic.split(" OR "):
        worst = max(
            ((rank.get(_lookup(tiers, i), 2), _lookup(tiers, i) or "unknown") for i in _ids(alt)),
            default=(2, "unknown"),
        )
        best = worst if best is None or worst[0] < best[0] else best
    level, tier = best or (2, "unknown")
    if level == 0:
        return PASS, f"{lic}: Blue Oak {tier}"
    if level == 1:
        return FLAG, f"{lic}: weak copyleft (use unmodified)"
    if level == 3:
        return (
            (FLAG, f"{lic}: {tier} copyleft, allowed by --allow-copyleft")
            if allow_copyleft
            else (
                FAIL,
                f"{lic}: {tier} copyleft (--allow-copyleft overrides)",
            )
        )
    return HOLD, f"{lic}: {'Blue Oak Lead' if tier == 'Lead' else 'not rated by Blue Oak'}"


def osi_flag(lic: str, osi: dict[str, bool] | None) -> str:
    """'' when an alternative of the expression is all OSI-approved, else why not."""
    if not lic:
        return "no licence found"
    if osi is None:
        return "OSI approval not checked (spdx.org list unavailable)"
    if any((ids := _ids(alt)) and all(_lookup(osi, i) for i in ids) for alt in lic.split(" OR ")):
        return ""
    return f"licence {lic} is not OSI-approved"


def gates(c: Candidate, now: float, tiers: dict[str, str] | None, allow_copyleft: bool) -> dict[str, str]:
    """G1-G6 outcomes, each 'pass', 'flag', 'n/a', 'HOLD' or 'FAIL' with its reason (module docstring)."""
    out = {"G1": f"{PASS} ({c.verified})"}
    if c.malicious:
        out["G2"] = f"{FAIL} (malicious: {', '.join(c.malicious)})"
    elif not c.ecosystem:
        out["G2"] = "n/a"  # a repository without a package: no registry advisories to check
    else:  # silence is not a pass: a squatted name passes G1
        out["G2"] = PASS if c.malware_checked else f"{HOLD} (malware not checked: no advisory source answered)"
    first = _days(c.first_release, now) if c.first_release else None
    out["G3"] = "n/a" if first is None else f"{HOLD} (first release {first:.0f} days ago)" if first < NEW_DAYS else PASS
    last = c.last_release or c.last_push
    idle = _days(last, now) if last else None
    if c.archived or c.deprecated or c.yanked:
        out["G4"] = f"{FAIL} ({'archived' if c.archived else 'deprecated' if c.deprecated else 'all releases yanked'})"
    elif idle is not None and idle > DORMANT_DAYS:
        out["G4"] = f"{HOLD} (dormant: {idle:.0f} days since {'release' if c.last_release else 'push'})"
    else:
        out["G4"] = PASS if idle is not None else "n/a"
    outcome, why = licence_gate(c.license, tiers, allow_copyleft)
    out["G5"] = f"{outcome} ({why})"
    out["G6"] = "n/a" if c.advisories is None else f"{FLAG} ({c.advisories} open advisories)" if c.advisories else PASS
    return out


def status(g: dict[str, str]) -> str:
    worst = max(SEVERITY[v.split(" ")[0]] for v in g.values())
    return {0: PASS, 1: HOLD, 2: FAIL}[worst]


def adoption(c: Candidate) -> tuple[int, int]:
    return (
        c.dependents if c.dependents is not None else -1,
        c.downloads_month if c.downloads_month is not None else -1,
    )


def judge(client: lit.Client, c: Candidate, tiers, osi, allow_copyleft: bool, notes: list[str]) -> bool:
    """G1 (verify), then G2-G6, Q, S and flags on an enriched candidate; False when G1 fails."""
    if not verify(client, c, notes):
        return False
    now = client.clock()
    c.gates = gates(c, now, tiers, allow_copyleft)
    c.status = status(c.gates)
    c.quality = quality(c, now)
    c.score = round(c.relevance + LAMBDA * c.quality, 4)
    if flag := osi_flag(c.license, osi):
        c.flags.append(flag)
    return True


def order(kept: list[Candidate]) -> list[Candidate]:
    """By S, every PASS before every HOLD when PASS_BEFORE_HOLD (R4)."""
    kept = sorted(kept, key=lambda c: -c.score)
    if PASS_BEFORE_HOLD:
        kept = [c for c in kept if c.status != HOLD] + [c for c in kept if c.status == HOLD]
    return kept


def search(
    client: lit.Client,
    queries: list[str],
    ecosystem: str = "pypi",
    n: int = DEFAULT_N,
    encoder: Encoder | None = None,
    why: str = "",
    allow_copyleft: bool = False,
) -> dict:
    """The report: candidates in final order (at most n), the re-rank that ran, recall counts and notes."""
    if ecosystem != "any" and ecosystem not in ECOS:
        raise ValueError(f"unknown ecosystem {ecosystem!r}: pypi, npm, cargo or any")
    n = max(1, min(n, MAX_N))
    notes: list[str] = []
    found = list(recall(client, queries, ecosystem, notes).values())
    rerank = rank_relevance(found, queries, encoder, why)
    found.sort(key=lambda c: -c.relevance)
    best = found[0].relevance if found else 0.0
    near = [c for c in found if c.relevance >= best - R_FLOOR]
    for c in {c.key(): c for c in found[:n] + near}.values():  # adoption needs the enriched record
        enrich(client, c, ecosystem, notes)
    shortlist = list({c.key(): c for c in found[:n] + sorted(near, key=adoption, reverse=True)[:n]}.values())
    tiers, osi = licence_lists(client, notes) if found else (None, None)
    kept: list[Candidate] = []
    for c in shortlist:  # every shortlisted candidate is scored before the cut to n
        if not judge(client, c, tiers, osi, allow_copyleft, notes):
            continue
        if c.status == FAIL:
            failed = "; ".join(f"{k} {v}" for k, v in c.gates.items() if v.startswith(FAIL))
            notes.append(f"dropped {c.name}: {failed}")
            continue
        kept.append(c)
    scored, kept = len(kept), order(kept)[:n]
    return {
        "scored": scored,
        "queries": queries,
        "ecosystem": ecosystem,
        "rerank": rerank,
        "recalled": len(found),
        "date": dt.datetime.fromtimestamp(client.clock(), dt.UTC).date().isoformat(),
        "score": f"PASS before HOLD, then S = R + {LAMBDA} * Q, after gates G1-G6 (devkit/reuse.py docstring)",
        "candidates": kept,
        "notes": list(dict.fromkeys(notes)),
    }


# ---- check: verify named packages ----

NOT_FOUND, UNVERIFIED = "NOT FOUND", "UNVERIFIED"
GITHUB_URL = re.compile(r"https?://(?:www\.)?github\.com/([\w.-]+/[\w.-]+)")


def _repo_in(urls) -> str:
    for u in urls:
        if isinstance(u, str) and (m := GITHUB_URL.search(u)):
            return "github.com/" + m.group(1).lower().removesuffix(".git")
    return ""


def from_registry(name: str, ecosystem: str, body: dict) -> Candidate:
    """A candidate from the registry's own answer, when ecosyste.ms does not know the name."""
    if ecosystem == "pypi":
        info = body.get("info") or {}
        urls, desc = [*(info.get("project_urls") or {}).values(), info.get("home_page")], info.get("summary")
        name = info.get("name") or name
    elif ecosystem == "npm":
        r = body.get("repository")
        urls, desc = [r.get("url") if isinstance(r, dict) else r, body.get("homepage")], body.get("description")
    else:
        crate = body.get("crate") or {}
        urls, desc = [crate.get("repository"), crate.get("homepage")], crate.get("description")
    return Candidate(name=name, repo=_repo_in(urls), ecosystem=ecosystem, description=desc or "", record=body)


def named(client: lit.Client, name: str, ecosystem: str, notes: list[str]) -> Candidate | None:
    """The package an agent named: its ecosyste.ms record, else its registry answer; None when neither knows it."""
    url = f"{ECOSYSTEMS}registries/{REGISTRY_HOST[ecosystem]}/packages/{urllib.parse.quote(name, safe='@')}"
    rec = _json(client, "ecosystems", url, notes, missing_ok=True)
    if isinstance(rec, dict) and rec.get("name"):
        c = from_record(rec)
        c.ecosystem = ecosystem
        return c
    body = _json(client, ecosystem, ECOS[ecosystem][2](name), notes, missing_ok=True)
    return from_registry(name, ecosystem, body) if isinstance(body, dict) and body else None


def check(client: lit.Client, names: list[str], ecosystem: str = "pypi", allow_copyleft: bool = False) -> dict:
    """The report for named packages, in the order given: existence, gates G1-G6, metadata. Nothing is dropped."""
    if ecosystem not in ECOS:
        raise ValueError(f"unknown ecosystem {ecosystem!r} for check: pypi, npm or cargo")
    notes: list[str] = []
    tiers, osi = licence_lists(client, notes) if names else (None, None)
    out: list[Candidate] = []
    for name in dict.fromkeys(n.strip() for n in names if n.strip()):
        c = named(client, name, ecosystem, notes)
        if c is not None:
            enrich(client, c, ecosystem, notes)
        if c is None:
            c = Candidate(name=name, ecosystem=ecosystem, status=NOT_FOUND)
            c.gates = {"G1": f"{FAIL} (no {ecosystem} package of this name: do not install it)"}
        elif not judge(client, c, tiers, osi, allow_copyleft, notes):
            c.status, c.gates = UNVERIFIED, {"G1": f"{FAIL} (not verified on {ecosystem}: see the notes)"}
        out.append(c)
    return {
        "mode": "check",
        "ecosystem": ecosystem,
        "date": dt.datetime.fromtimestamp(client.clock(), dt.UTC).date().isoformat(),
        "candidates": out,
        "notes": list(dict.fromkeys(notes)),
    }


# ---- output and edges ----


def to_json(report: dict) -> str:
    cands = [{k: v for k, v in dataclasses.asdict(c).items() if k != "record"} for c in report["candidates"]]
    return json.dumps({**report, "candidates": cands}, indent=1)


def _v(x, unit: str) -> str:
    return f"{x if x is not None else '?'} {unit}"


def meta(c: Candidate) -> str:
    """The metadata line: licence, release dates and maintenance signals."""
    return " | ".join(
        [
            c.license or "no licence",
            f"first {c.first_release or '?'}, last release {c.last_release or '?'}",
            _v(c.releases_last_year, "releases/yr"),
            _v(c.contributors, "contributors"),
            _v(c.dependents, "dependents"),
            f"scorecard {c.scorecard if c.scorecard is not None else '?'}",
            _v(c.advisories, "open advisories"),
            _v(c.stars, "stars"),
        ]
    )


def block(i: int, c: Candidate, head: str) -> list[str]:
    where = f"[{c.ecosystem}]" if c.ecosystem else "[repo]"
    out = [f"{i:>2}. " + " ".join(x for x in (c.name, where, c.repo) if x) + head]
    if c.status not in (NOT_FOUND, UNVERIFIED):
        out.append("    " + meta(c))
    out.append("    gates: " + ", ".join(f"{k} {v}" for k, v in c.gates.items()))
    if c.description:
        out.append(f"    {c.description[:160]}")
    return out + [f"    FLAG: {f}" for f in c.flags]


def render(report: dict) -> str:
    if report.get("mode") == "check":
        out = [f"check: {len(report['candidates'])} names ({report['ecosystem']}, {report['date']}); gates G1-G6"]
        for i, c in enumerate(report["candidates"], 1):
            q = f" Q {c.quality:.2f}" if c.status in (PASS, HOLD, FAIL) else ""
            out += block(i, c, f"  {c.status.upper()}{q}")
    else:
        out = [
            f"re-rank: {report['rerank']}; {len(report['queries'])} queries, {report['recalled']} recalled, "
            f"{report.get('scored', len(report['candidates']))} scored, {len(report['candidates'])} shown "
            f"({report['ecosystem']}); {report['score']}"
        ]
        for i, c in enumerate(report["candidates"], 1):
            hold = " HOLD" if c.status == HOLD else ""
            out += block(i, c, f"{hold}  S {c.score:.2f} (R {c.relevance:.2f}, Q {c.quality:.2f})")
        if not report["candidates"]:
            out.append("no candidates")
    out += [f"note: {x}" for x in report["notes"]]
    return "\n".join(out)


def auth(source: str, url: str, env: dict[str, str]) -> tuple[str, dict[str, str]]:
    if source in ("github", "githubcore") and (tok := env.get("GITHUB_TOKEN")):
        return url, {"authorization": f"Bearer {tok}", "accept": "application/vnd.github+json"}
    return url, {}


def github_token(env: dict[str, str]) -> str:
    """GITHUB_TOKEN, else GH_TOKEN, else the gh CLI's token: 30 searches a minute instead of 10."""
    if tok := env.get("GITHUB_TOKEN") or env.get("GH_TOKEN"):
        return tok
    if shutil.which("gh"):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10, check=False)
            return r.stdout.strip() if r.returncode == 0 else ""
    return ""


def client() -> lit.Client:
    """dev lit's client and state directory, so both share one cache, one set of buckets and one backoff."""
    env = dict(os.environ)
    if tok := github_token(env):
        env["GITHUB_TOKEN"] = tok
    return lit.Client(state_dir("lit"), env=env, rates=RATES, keys=KEYS, auth=auth)
