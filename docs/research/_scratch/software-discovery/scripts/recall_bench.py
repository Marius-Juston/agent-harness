"""Recall of library-discovery strategies on needs whose gold answers come from sources the lead did not write (#169).

Usage: uv run --with model2vec --with numpy python recall_bench.py <cache_dir> --mode warm|tune|test
           [--pypi-index <pypi_index.py output>] [--confirm-test-run]

Follows PROTOCOL.md: the split in data/split.json (make_split.py) is frozen before any tuning; `--mode tune` reports
TUNE only; `--mode test` refuses to run without --confirm-test-run and logs every run in data/TEST_RUNS.log;
`--mode warm` fetches and caches every API answer for every need and prints no metric (the snapshot).

Strata (data files next to this script, in data/):
- A, text needs: 120 awesome-python subcategories (vinta/awesome-python @ ba49ec7, drawn by build_gold.py, seed 169);
  gold = every GitHub repo the list files there. q0 is the label ("fuzzy matching text processing"); the two
  paraphrases per need in paraphrases.json were written once by the round-1 researcher from the label alone (no
  library names), before any result for that need was seen, and are never edited.
- P, the lead's 16 pilot needs and paraphrases (pilot_needs.json): TUNE only.
- M, migration needs: 40 PyMigBench sources (ualberta-smr/PyMigBench @ d8ffb98, seed 169); gold = the libraries real
  projects migrated to; the only query is the source's own PyPI summary. TEST only.
- C, code needs: 10 hand-written helpers in real repos (pinned commits); gold named by an outside source where one
  exists (pip's NEWS, ruff's rule docs, an awesome-python subcategory), else by the researcher (see gold_by). TEST only.

Sources: GitHub repository search via `gh api` (30/min), packages.ecosyste.ms keyword pages, context7's public v1
search endpoint (keyless, undocumented), an offline PyPI summary index (pypi_index.py), PyPI JSON. Every answer is
cached as a JSON file in <cache_dir>; transient failures are not cached. Metrics: hit@3 (primary), hit@1, hit@10,
MRR@10, pool hit; 95% percentile bootstrap over needs (10,000 resamples, seed 169) and paired differences.
"""

import ast
import hashlib
import json
import math
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
CACHE = Path(sys.argv[1])
CACHE.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "agent-harness-recall-bench/0.1 (research; github.com/Marius-Juston/agent-harness)"}
STOP = {"a", "an", "the", "of", "to", "in", "for", "with", "and", "or", "between", "two", "into", "on", "from",
        "without", "like", "by", "as", "at", "is", "be", "it", "its", "that", "this", "your", "using", "use", "&", "-"}
NOT_LIBS = ("/websites/", "/llmstxt/", "/context7/")


FAIL = object()


def _key(*parts: str) -> Path:
    return CACHE / f"{hashlib.sha1('|'.join(parts).encode(), usedforsecurity=False).hexdigest()}.json"


def cached(kind: str, arg: str, fetch):
    f = _key(kind, arg)
    if f.exists():
        return json.loads(f.read_text())
    val = fetch()
    if val is FAIL:  # a transient failure is not cached, so a re-run retries it
        return None
    f.write_text(json.dumps(val))
    return val


QUOTA: set[str] = set()  # hosts whose quota ran out in this run


def http_json(url: str, pause: float = 0.3):
    def fetch():
        if urllib.parse.urlsplit(url).netloc in QUOTA:
            return FAIL
        for attempt in range(4):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:  # noqa: S310
                    time.sleep(pause)
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    return None
                if e.code == 429 and float(e.headers.get("retry-after") or 0) > 300:
                    QUOTA.add(urllib.parse.urlsplit(url).netloc)  # a monthly quota, not a burst limit: give up now
                    return FAIL
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(10 * (attempt + 1))
                    continue
                return FAIL
            except Exception:
                time.sleep(5 * (attempt + 1))
        print(f"transient failure, not cached: {url}", file=sys.stderr)
        return FAIL
    return cached("http", url, fetch)


# ---------------------------------------------------------------- sources

def gh_search(q: str, sort: str = "", n: int = 30) -> list[dict]:
    def fetch():
        args = ["gh", "api", "-X", "GET", "search/repositories", "-f", f"q={q}", "-f", f"per_page={n}"]
        if sort:
            args += ["-f", f"sort={sort}"]
        for attempt in range(8):
            r = subprocess.run(args, capture_output=True, text=True)
            if r.returncode == 0:
                time.sleep(2.1)  # stay under 30 searches/min
                return [{"name": i["full_name"].lower(), "stars": i["stargazers_count"], "desc": i["description"] or "",
                         "topics": i.get("topics", []), "src": "gh"} for i in json.loads(r.stdout)["items"]]
            if "422" in r.stderr or "Validation Failed" in r.stderr:
                return []
            time.sleep(15 * (attempt + 1))
        raise RuntimeError(r.stderr)
    return cached("gh", f"{q}|{sort}|{n}", fetch)


def eco_keyword(kw: str) -> list[dict]:
    body = http_json(f"https://packages.ecosyste.ms/api/v1/keywords/{urllib.parse.quote(kw)}?per_page=50")
    out = []
    for p in (body or {}).get("packages", []):
        if p.get("ecosystem") != "pypi":
            continue
        m = re.match(r"https://github.com/([^/]+/[^/#?]+)", (p.get("repository_url") or "").lower())
        if m:
            out.append({"name": m.group(1).removesuffix(".git"), "desc": p.get("description") or "",
                        "stars": (p.get("repo_metadata") or {}).get("stargazers_count") or 0,
                        "topics": p.get("keywords_array") or [], "src": "eco"})
    return out


C7_MISSING: set[str] = set()  # context7 queries with no cached answer (keyless quota: 200 requests a month per IP)


def c7_search(q: str) -> list[dict]:
    url = f"https://context7.com/api/v1/search?query={urllib.parse.quote(q)}"
    body = http_json(url, pause=1.0)
    if not _key("http", url).exists():
        C7_MISSING.add(q)
    out = []
    for r in (body or {}).get("results", []):
        i = r.get("id", "")
        if i.startswith(NOT_LIBS) or i.count("/") != 2:
            continue
        out.append({"name": i.strip("/").lower(), "desc": f"{r.get('title', '')} {r.get('description', '')}",
                    "stars": max(r.get("stars") or 0, 0), "topics": [], "src": "c7"})
    return out


def gh_repo(full: str) -> dict | None:
    def fetch():
        r = subprocess.run(["gh", "api", f"repos/{full}"], capture_output=True, text=True)
        if r.returncode:
            return None
        j = json.loads(r.stdout)
        return {"name": j["full_name"].lower(), "language": j.get("language"), "stars": j["stargazers_count"],
                "archived": j["archived"], "topics": j.get("topics", []), "desc": j.get("description") or ""}
    return cached("repo", full.lower(), fetch)


def pypi(name: str) -> dict | None:
    return http_json(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json")


def pypi_repo(info: dict) -> str | None:
    urls = [*(info.get("project_urls") or {}).values(), info.get("home_page") or ""]
    for u in urls:
        m = re.match(r"https?://(?:www\.)?github\.com/([^/]+/[^/#?]+)", u or "")
        if m:
            return m.group(1).lower().removesuffix(".git")
    return None


# ---------------------------------------------------------------- pooling and ranking

def terms(q: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9+#]+(?:[-.][a-z0-9]+)*", q.lower()) if w not in STOP]


def retrieve(queries: list[str], *, relax: bool, topics: bool, lang: bool = True, c7: bool = False,
             gh: bool = True) -> tuple[list[list[dict]], dict[str, dict]]:
    """Per-query result lists (for round-robin order) and the merged pool."""
    lists: list[list[dict]] = []
    suffix = " language:python" if lang else " python"
    for q in queries:
        res: list[dict] = []
        if gh:
            qq = " ".join(terms(q)) if relax else q
            res = list(gh_search(qq + suffix))
            if relax and len(res) < 5 and len(terms(q)) > 1:
                res += gh_search(" OR ".join(terms(q)[:5]) + suffix)
            if relax:
                t = terms(q)
                for kw in {"-".join(t), *(["-".join(t[:2])] if len(t) > 2 else [])}:
                    res += eco_keyword(kw)
        if c7:
            res += c7_search(q)
        lists.append(res)
    pool: dict[str, dict] = {}
    for res in lists:
        for i in res:
            pool.setdefault(i["name"], i)
    if topics and gh:
        counts: dict[str, int] = {}
        for i in sorted(pool.values(), key=lambda x: -x["stars"])[:10]:
            for t in i["topics"]:
                counts[t] = counts.get(t, 0) + 1
        for t, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]:
            if c >= 2 and t != "python":
                extra = gh_search(f"topic:{t}{suffix if lang else ''}", sort="stars")
                lists.append(extra)
                for i in extra:
                    pool.setdefault(i["name"], i)
    return lists, pool


class PypiIndex:
    """Offline semantic index of PyPI summaries (pypi_index.py output), embedded once with the same model."""

    def __init__(self, path: Path, rk: "Ranker") -> None:
        import numpy as np
        self.rows = [json.loads(line) for line in path.read_text().splitlines()]
        t0 = time.time()
        texts = [f"{r['name']} {r['summary']} {' '.join(r['keywords'][:10])}" for r in self.rows]
        v = rk.m.encode(texts)
        self.embed_seconds = time.time() - t0
        self.v = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)
        self.np = np
        print(f"PyPI index: {len(self.rows)} packages, {sum(bool(r['repo']) for r in self.rows)} with a GitHub repo, "
              f"embedded in {self.embed_seconds:.1f}s")

    def search(self, qvec, k: int = 30) -> list[dict]:
        q = qvec / (self.np.linalg.norm(qvec) + 1e-9)
        sims = self.v @ q
        out, seen = [], set()
        for j in self.np.argsort(-sims):
            r = self.rows[int(j)]
            name = r["repo"] or f"pypi:{r['name']}"
            if name in seen:
                continue
            seen.add(name)
            out.append({"name": name, "desc": r["summary"], "stars": r["stars"], "topics": r["keywords"][:10],
                        "src": "pypi"})
            if len(out) == k:
                break
        return out


def merged(pool: dict[str, dict], extra: list[dict]) -> dict[str, dict]:
    out = dict(pool)
    for i in extra:
        out.setdefault(i["name"], i)
    return out


def round_robin(lists: list[list[dict]]) -> list[dict]:
    seen, out = set(), []
    for k in range(max((len(x) for x in lists), default=0)):
        for x in lists:
            if k < len(x) and x[k]["name"] not in seen:
                seen.add(x[k]["name"])
                out.append(x[k])
    return out


class Ranker:
    def __init__(self) -> None:
        from model2vec import StaticModel
        self.m = StaticModel.from_pretrained("minishlab/potion-base-8M")

    def vec(self, texts: list[str]):
        return self.m.encode(texts).mean(axis=0)

    def embed(self, qvec, items: list[dict], star_w: float) -> list[dict]:
        if not items:
            return []
        vs = self.m.encode([f"{i['name'].split('/')[-1].removeprefix('pypi:')} {i['desc']} {' '.join(i['topics'])}" for i in items])
        qn = math.sqrt(float((qvec * qvec).sum())) + 1e-9
        sc = [float((v * qvec).sum()) / (math.sqrt(float((v * v).sum())) * qn + 1e-9)
              + star_w * math.log10(1 + i["stars"]) for v, i in zip(vs, items, strict=True)]
        return [i for _, i in sorted(zip(sc, items, strict=True), key=lambda t: -t[0])]


def bm25_rank(qtext: str, items: list[dict]) -> list[dict]:
    docs = [terms(f"{i['name'].split('/')[-1].removeprefix('pypi:').replace('-', ' ').replace('_', ' ')} {i['desc']} "
                  f"{' '.join(i['topics']).replace('-', ' ')}") for i in items]
    n = len(docs) or 1
    avg = sum(map(len, docs)) / n or 1
    df: dict[str, int] = {}
    for d in docs:
        for w in set(d):
            df[w] = df.get(w, 0) + 1
    qt = set(terms(qtext))

    def score(d):
        s = 0.0
        for w in qt:
            tf = d.count(w)
            if tf:
                s += math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5)) * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(d) / avg))
        return s
    sc = [score(d) + 0.3 * math.log10(1 + i["stars"]) for d, i in zip(docs, items, strict=True)]
    return [i for _, i in sorted(zip(sc, items, strict=True), key=lambda t: -t[0])]


# ---------------------------------------------------------------- the three sets

def canon(names) -> set[str]:
    out = set()
    for g in names:
        info = gh_repo(g)
        out.add(info["name"] if info else g.lower())
    return out


def load_text_needs() -> list[dict]:
    """Stratum A (awesome-python, 120) and stratum P (the lead's 16 pilot needs: TUNE only)."""
    needs = json.loads((DATA / "needs_raw.json").read_text())
    para = json.loads((DATA / "paraphrases.json").read_text())
    for n in needs:
        n["para"] = para[n["id"]]
        n["gold"] = canon(n["gold"])
        n["stratum"] = "A"
    for n in json.loads((DATA / "pilot_needs.json").read_text()):
        n["gold"] = canon(n["gold"])
        n["stratum"] = "P"
        needs.append(n)
    return needs


# Hand-written helpers. gold_by says who named the answer: an outside source, or the researcher (BELIEVED mapping).
CODE_CASES = [
    {"id": "pip retry decorator", "repo": "pypa/pip", "sha": "fb1d16609222a4a44c64bd8b12805a5e10d65a06",
     "path": "src/pip/_internal/utils/retry.py", "symbols": ["retry"], "gold": ["jd/tenacity"],
     "gold_by": "pip NEWS.rst: 'Remove vendored tenacity' when this helper replaced it",
     "need": ["retry a function call on error until a deadline", "retry decorator with fixed wait", "retrying library"]},
    {"id": "devkit backoff", "repo": "Marius-Juston/agent-harness", "sha": "3b49fdcfb2f63dc2c2526bb1a6abc31f27ec39f8",
     "path": "devkit/backoff.py", "symbols": ["full_jitter", "hinted", "retry"],
     "gold": ["jd/tenacity", "litl/backoff", "hynek/stamina"], "gold_by": "researcher (tenacity is in best-of-python)",
     "need": ["retry with exponential backoff and jitter", "honour Retry-After when retrying http calls", "retrying decorator"]},
    {"id": "devkit token bucket", "repo": "Marius-Juston/agent-harness", "sha": "3b49fdcfb2f63dc2c2526bb1a6abc31f27ec39f8",
     "path": "devkit/lit.py", "symbols": ["take"],
     "gold": ["tomasbasham/ratelimit", "vutran1710/pyratelimiter", "alisaifee/limits"],
     "gold_by": "researcher (ratelimit is in best-of-python)",
     "need": ["token bucket rate limiter", "limit calls per second across processes", "throttle api requests"]},
    {"id": "devkit .env parser", "repo": "Marius-Juston/agent-harness", "sha": "3b49fdcfb2f63dc2c2526bb1a6abc31f27ec39f8",
     "path": "devkit/lit.py", "symbols": ["parse_secrets"], "gold": ["theskumar/python-dotenv"],
     "gold_by": "awesome-python Configuration Files entry ('Reads key-value pairs from a .env file')",
     "need": ["read KEY=value pairs from a .env file", "load environment variables from dotenv file", "parse shell style env file"]},
    {"id": "devkit safe XML parse", "repo": "Marius-Juston/agent-harness", "sha": "3b49fdcfb2f63dc2c2526bb1a6abc31f27ec39f8",
     "path": "devkit/netsafe.py", "symbols": ["parse_xml"], "gold": ["tiran/defusedxml"],
     "gold_by": "ruff rule S314 docs recommend defusedxml",
     "need": ["parse untrusted XML safely without entity expansion", "XML parser that blocks DTD and XXE attacks", "secure xml parsing"]},
    {"id": "devkit frontmatter split", "repo": "Marius-Juston/agent-harness", "sha": "3b49fdcfb2f63dc2c2526bb1a6abc31f27ec39f8",
     "path": "devkit/frontmatter.py", "symbols": ["split"], "gold": ["eyeseast/python-frontmatter"],
     "gold_by": "researcher",
     "need": ["parse YAML frontmatter from markdown files", "split markdown into metadata header and body", "frontmatter parser"]},
    {"id": "cpython levenshtein", "repo": "python/cpython", "sha": "60403a5409ff2c3f3b07dd2ca91a7a3e096839c7",
     "path": "Lib/traceback.py", "symbols": ["_levenshtein_distance"],
     "gold": ["rapidfuzz/rapidfuzz", "rapidfuzz/levenshtein", "seatgeek/thefuzz", "jamesturk/jellyfish"],
     "gold_by": "awesome-python Fuzzy Matching (rapidfuzz); researcher added the other three",
     "need": ["levenshtein edit distance between two strings", "fast string distance metrics", "fuzzy string matching"]},
    {"id": "django slugify", "repo": "django/django", "sha": "f1d11117c6a3e227ce7cdd263cf0743f0eb73070",
     "path": "django/utils/text.py", "symbols": ["slugify"], "gold": ["un33k/python-slugify", "avian2/unidecode"],
     "gold_by": "awesome-python Transliteration and Slugs",
     "need": ["convert a title to a url slug", "transliterate unicode to ascii for slugs", "slugify strings"]},
    {"id": "tqdm format_sizeof", "repo": "tqdm/tqdm", "sha": "9cf5a12b1f955468a17f0ba3c59092b23e4258ac",
     "path": "tqdm/std.py", "symbols": ["tqdm.format_sizeof", "tqdm.format_interval"], "gold": ["python-humanize/humanize"],
     "gold_by": "best-of-python entry humanize (jmoiron/humanize, now python-humanize/humanize)",
     "need": ["human readable file sizes and durations", "format numbers with SI prefixes like 1.2k", "humanize numbers and times"]},
    {"id": "home-assistant parse_datetime", "repo": "home-assistant/core", "sha": "dfafd830cffc8fab94210b7182a49008b4dcf165",
     "path": "homeassistant/util/dt.py", "symbols": ["parse_datetime"],
     "gold": ["dateutil/dateutil", "scrapinghub/dateparser", "python-pendulum/pendulum", "ariebovenberg/whenever"],
     "gold_by": "awesome-python Date and Time",
     "need": ["parse ISO 8601 datetime strings with timezone", "datetime parsing library", "timezone aware date handling"]},
]

PY_KW = {"self", "cls", "return", "none", "true", "false", "if", "else", "elif", "for", "while", "in", "not", "and", "or",
         "def", "is", "str", "int", "float", "dict", "list", "len", "range", "raise", "try", "except", "with", "as",
         "import", "from", "lambda", "yield", "pass", "break", "continue", "isinstance", "tuple", "set", "bool", "any",
         "type", "value", "args", "kwargs", "max", "min", "get", "append", "strip", "split", "join", "items", "keys",
         "the", "of", "a", "to", "t", "n", "s", "i", "x", "re", "e", "k", "v", "f", "d", "b", "c"}


def snippet(case: dict) -> tuple[str, str, list[str]]:
    """(source of the symbols, docstrings+comments, identifier words ranked by count)."""
    src = cached("raw", f"{case['repo']}@{case['sha']}:{case['path']}", lambda: urllib.request.urlopen(
        urllib.request.Request(f"https://raw.githubusercontent.com/{case['repo']}/{case['sha']}/{case['path']}",
                               headers=UA), timeout=40).read().decode())
    tree = ast.parse(src)
    parts = []
    for sym in case["symbols"]:
        owner, _, name = sym.rpartition(".")
        scope = tree.body
        if owner:
            scope = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner).body
        nodes = [n for n in scope if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
        parts += [ast.get_source_segment(src, n) for n in nodes]
    code = "\n\n".join(parts)
    docs = []
    for n in ast.walk(ast.parse(code)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and (d := ast.get_docstring(n)):
            docs.append(d.split("\n\n")[0])
    docs += [c.strip("# ").strip() for c in re.findall(r"#[^\n]*", code)]
    words: dict[str, int] = {}
    for ident in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", re.sub(r'""".*?"""|#[^\n]*', " ", code, flags=re.S)):
        for w in re.sub(r"([a-z])([A-Z])", r"\1_\2", ident).lower().split("_"):
            if len(w) > 2 and w not in PY_KW:
                words[w] = words.get(w, 0) + 1
    ranked = sorted(words, key=lambda w: (-words[w], w))
    return code, " ".join(docs), ranked


def load_migration_needs(sample: bool = True) -> list[dict]:
    """Eligible PyMigBench sources (summary on PyPI, a target repo on GitHub); 40 drawn with seed 169 when sample."""
    files = (DATA / "pymigbench_files@d8ffb98.txt").read_text().split()
    pairs: dict[str, set[str]] = {}
    for f in files:
        s, t, _ = f.split("__", 2)
        pairs.setdefault(s, set()).add(t)
    needs = []
    for s, ts in sorted(pairs.items()):
        sj = pypi(s)
        if not sj or not (summary := (sj["info"].get("summary") or "").strip()):
            continue
        srepo = pypi_repo(sj["info"])
        srepo = gh_repo(srepo)["name"] if srepo and gh_repo(srepo) else srepo
        gold = set()
        for t in ts:
            tj = pypi(t)
            r = pypi_repo(tj["info"]) if tj else None
            info = gh_repo(r) if r else None
            if info and info["name"] != srepo:
                gold.add(info["name"])
        if gold:
            needs.append({"id": f"migration / {s}", "pkg": s, "stratum": "M", "summary": summary, "src_repo": srepo, "gold": gold,
                          "keywords": (sj["info"].get("keywords") or "")})
    if not sample:
        return needs
    rng = random.Random(169)  # noqa: S311 (sampling, not security)
    return sorted(rng.sample(needs, min(40, len(needs))), key=lambda n: n["id"])


# ---------------------------------------------------------------- arms

def text_arms(n: dict, rk: Ranker, px: "PypiIndex | None") -> dict[str, tuple[list[dict], dict]]:
    q0, para = n["q0"], n["para"]
    qs = [q0, *para]
    mq, v0 = rk.vec(qs), rk.vec([q0])
    a: dict[str, tuple[list[dict], dict]] = {}
    lists, pool = retrieve([q0], relax=False, topics=False)
    a["A0 one query, GitHub order"] = (round_robin(lists), pool)
    lists, pool = retrieve(qs, relax=False, topics=False)
    a["A1 3 queries, round-robin"] = (round_robin(lists), pool)
    lists, pool = retrieve(qs, relax=False, topics=True)
    items = list(pool.values())
    a["A2 3q + topics, stars"] = (sorted(items, key=lambda i: -i["stars"]), pool)
    a["A3 3q + topics, embed"] = (rk.embed(v0, items, 0.0), pool)
    a["A4 3q + topics, embed+stars"] = (rk.embed(v0, items, 0.03), pool)
    _, pool = retrieve(qs, relax=True, topics=True)
    items = list(pool.values())
    a["A5 A4 + relaxed + ecosyste.ms"] = (rk.embed(v0, items, 0.03), pool)
    a["A6 A5, mean query vector (pilot S6)"] = (rk.embed(mq, items, 0.03), pool)
    a["A7 A5 pool, BM25+stars"] = (bm25_rank(" ".join(qs), items), pool)
    _, pool7 = retrieve(qs, relax=True, topics=True, c7=True)
    a["A8 A6 + context7"] = (rk.embed(mq, list(pool7.values()), 0.03), pool7)
    lists, poolc = retrieve(qs, relax=False, topics=False, c7=True, gh=False)
    a["A9 context7 only, round-robin"] = (round_robin(lists), poolc)
    a["A10 context7 only, embed+stars"] = (rk.embed(mq, list(poolc.values()), 0.03), poolc)
    _, pooln = retrieve(qs, relax=True, topics=True, lang=False)
    a["A11 A6 without language:python"] = (rk.embed(mq, list(pooln.values()), 0.03), pooln)
    ext = [q0, n["desc"]] if n.get("desc") else [q0]
    _, poole = retrieve(ext, relax=True, topics=True)
    a["A12 A6 on outside text only (label + section blurb)"] = (rk.embed(rk.vec(ext), list(poole.values()), 0.03), poole)
    if px:
        hp = px.search(mq)
        a["A13 PyPI summary index only (top 30k), embed+stars"] = (rk.embed(mq, hp, 0.03), {i["name"]: i for i in hp})
        pp = merged(pool, hp)
        a["A14 A6 + PyPI summary index"] = (rk.embed(mq, list(pp.values()), 0.03), pp)
        pa = merged(pool7, hp)
        a["A15 A6 + context7 + PyPI index"] = (rk.embed(mq, list(pa.values()), 0.03), pa)
    return a


def code_arms(c: dict, rk: Ranker, px: "PypiIndex | None") -> dict[str, tuple[list[dict], dict]]:
    _, docs, words = snippet(c)
    idq = " ".join(words[:6])
    docq = " ".join(terms(docs)[:8]) or idq
    nv = rk.vec(c["need"])
    a: dict[str, tuple[list[dict], dict]] = {}
    lists, pool = retrieve([idq], relax=True, topics=False)
    a["C0 identifiers as query, GitHub order"] = (round_robin(lists), pool)
    lists, pool = retrieve([docq], relax=True, topics=False)
    a["C1 docstring/comments as query, GitHub order"] = (round_robin(lists), pool)
    _, pool = retrieve([idq, docq], relax=True, topics=True)
    a["C2 C0+C1 pooled, embed(code text)+stars"] = (
        rk.embed(rk.vec([docs + " " + " ".join(words[:20])]), list(pool.values()), 0.03), pool)
    _, pool = retrieve(c["need"], relax=True, topics=True)
    a["C3 agent-written need (3 lines) -> A6 pipeline"] = (rk.embed(nv, list(pool.values()), 0.03), pool)
    _, pool7 = retrieve(c["need"], relax=True, topics=True, c7=True)
    a["C4 C3 + context7"] = (rk.embed(nv, list(pool7.values()), 0.03), pool7)
    if px:
        pp = merged(pool, px.search(nv))
        a["C5 C3 + PyPI summary index"] = (rk.embed(nv, list(pp.values()), 0.03), pp)
    return a


def mig_arms(n: dict, rk: Ranker, px: "PypiIndex | None") -> dict[str, tuple[list[dict], dict]]:
    sq = n["summary"]
    sv = rk.vec([sq])
    a: dict[str, tuple[list[dict], dict]] = {}
    lists, pool = retrieve([f"alternative to {n['pkg']}"], relax=True, topics=False)
    a["M0 'alternative to <name>', GitHub order"] = (round_robin(lists), pool)
    lists, pool = retrieve([sq], relax=True, topics=False)
    a["M1 source summary as query, GitHub order"] = (round_robin(lists), pool)
    qs = [sq] + ([n["keywords"].replace(",", " ")] if n["keywords"].strip() else [])
    _, pool = retrieve(qs, relax=True, topics=True)
    a["M2 summary+keywords + topics, embed+stars"] = (rk.embed(sv, list(pool.values()), 0.03), pool)
    _, pool7 = retrieve(qs, relax=True, topics=True, c7=True)
    a["M3 M2 + context7"] = (rk.embed(sv, list(pool7.values()), 0.03), pool7)
    lists, poolc = retrieve([sq], relax=False, topics=False, c7=True, gh=False)
    a["M4 context7 only"] = (round_robin(lists), poolc)
    if px:
        pp = merged(pool, px.search(sv, k=31))
        a["M5 M2 + PyPI summary index"] = (rk.embed(sv, list(pp.values()), 0.03), pp)
    return a


# ---------------------------------------------------------------- metrics

def score(ranked: list[dict], pool: dict, gold: set[str], exclude: str | None = None) -> dict:
    top = [i["name"] for i in ranked if i["name"] != exclude][:10]
    rank = next((k + 1 for k, nm in enumerate(top) if nm in gold), 0)
    return {"hit1": float(rank == 1), "hit3": float(0 < rank <= 3), "hit10": float(rank > 0),
            "mrr": 1 / rank if rank else 0.0, "recall10": len(gold & set(top)) / min(len(gold), 10),
            "pool": float(bool(gold & set(pool) - {exclude})), "pool_size": len(pool)}


def boot(vals: list[float], reps: int = 10000) -> tuple[float, float, float]:
    rng = random.Random(169)  # noqa: S311 (bootstrap, not security)
    n = len(vals)
    means = sorted(sum(vals[rng.randrange(n)] for _ in range(n)) / n for _ in range(reps))
    return sum(vals) / n, means[int(0.025 * reps)], means[int(0.975 * reps) - 1]


def report(title: str, rows: dict[str, list[dict]], pairs: list[tuple[str, str]]) -> dict:
    n = len(next(iter(rows.values())))
    print(f"\n## {title} (n = {n})\n")
    print("| strategy | hit@3 [95% CI] (primary) | hit@1 | hit@10 [95% CI] | MRR@10 | pool hit | median pool |")
    print("|---|---|---|---|---|---|---|")
    out = {}
    for s, rs in rows.items():
        h3, h10 = boot([r["hit3"] for r in rs]), boot([r["hit10"] for r in rs])
        h1 = sum(r["hit1"] for r in rs) / n
        mrr = sum(r["mrr"] for r in rs) / n
        ph = sum(r["pool"] for r in rs) / n
        sizes = sorted(r["pool_size"] for r in rs)
        print(f"| {s} | {h3[0]:.2f} [{h3[1]:.2f}, {h3[2]:.2f}] | {h1:.2f} | {h10[0]:.2f} [{h10[1]:.2f}, {h10[2]:.2f}] | "
              f"{mrr:.2f} | {ph:.2f} | {sizes[len(sizes) // 2]} |")
        out[s] = {"hit3": h3, "hit10": h10, "hit1": h1, "mrr": mrr, "pool": ph}
    if pairs:
        print("\npaired differences, B minus A, hit@3 and hit@10 [95% CI]:")
    for a, b in pairs:
        if a not in rows or b not in rows:
            continue
        d3 = boot([rb["hit3"] - ra["hit3"] for ra, rb in zip(rows[a], rows[b], strict=True)])
        d10 = boot([rb["hit10"] - ra["hit10"] for ra, rb in zip(rows[a], rows[b], strict=True)])
        print(f"- {b} vs {a}: hit@3 {d3[0]:+.2f} [{d3[1]:+.2f}, {d3[2]:+.2f}]; hit@10 {d10[0]:+.2f} [{d10[1]:+.2f}, {d10[2]:+.2f}]")
        out[f"diff {b} vs {a}"] = {"hit3": d3, "hit10": d10}
    return out


S6 = "A6 A5, mean query vector (pilot S6)"
TEXT_PAIRS = [("A0 one query, GitHub order", "A1 3 queries, round-robin"), ("A0 one query, GitHub order", S6),
              (S6, "A7 A5 pool, BM25+stars"), (S6, "A8 A6 + context7"), (S6, "A11 A6 without language:python"),
              (S6, "A12 A6 on outside text only (label + section blurb)"),
              (S6, "A13 PyPI summary index only (top 30k), embed+stars"), (S6, "A14 A6 + PyPI summary index")]


def run(needs: list[dict], arms, rk: Ranker, px) -> tuple[dict[str, list[dict]], dict]:
    rows: dict[str, list[dict]] = {}
    per: dict[str, dict] = {}
    for n in needs:
        C7_MISSING.clear()
        computed = arms(n, rk, px)
        n["c7_complete"] = not C7_MISSING
        for s, (ranked, pool) in computed.items():
            sc = score(ranked, pool, n["gold"], exclude=n.get("src_repo"))
            rows.setdefault(s, []).append(sc)
            per.setdefault(n["id"], {})[s] = {**sc, "top3": [i["name"] for i in ranked if i["name"] != n.get("src_repo")][:3]}
    return rows, per


def main() -> None:
    mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "tune"
    rk = Ranker()
    px = PypiIndex(Path(sys.argv[sys.argv.index("--pypi-index") + 1]), rk) if "--pypi-index" in sys.argv else None
    split = json.loads((DATA / "split.json").read_text())
    text = {n["id"]: n for n in load_text_needs()}
    mig = {n["id"]: n for n in load_migration_needs(sample=False)}  # the split names which 40
    code = {}
    for c in CODE_CASES:
        code[c["id"]] = {**c, "gold": canon(c["gold"])}

    if mode == "warm":  # fetch and cache every answer for every need; prints no metric
        for ids in (split["tune"], split["test"]):
            for i in ids:
                (text_arms(text[i], rk, px) if i in text else mig_arms(mig[i], rk, px))
        for i in split["code_test"]:
            code_arms(code[i], rk, px)
        print(f"warm: {len(split['tune'])} TUNE, {len(split['test'])} TEST, {len(split['code_test'])} code needs cached")
        return
    if mode == "tune":
        needs = [text[i] for i in split["tune"]]
        out = {}
        for label, sub in [("TUNE, all", needs), ("TUNE, stratum A (awesome-python)", [n for n in needs if n["stratum"] == "A"]),
                           ("TUNE, stratum P (lead pilot)", [n for n in needs if n["stratum"] == "P"])]:
            rows, _ = run(sub, text_arms, rk, px)
            no_c7 = {k: v for k, v in rows.items() if "context7" not in k}
            out[label] = report(label, no_c7, TEXT_PAIRS if label == "TUNE, all" else [])
            ok = [k for k, n in enumerate(sub) if n["c7_complete"]]
            print(f"\ncontext7 answers cached for {len(ok)}/{len(sub)} needs (keyless quota); context7 arms on those only:")
            if ok:
                c7 = {k: [v[j] for j in ok] for k, v in rows.items() if "context7" in k or k == S6}
                out[label + ", context7 subset"] = report(label + ", context7 subset", c7,
                                                          [(S6, "A8 A6 + context7")] if label == "TUNE, all" else [])
        (CACHE / "results-tune.json").write_text(json.dumps({"results": out, "per_need": run(needs, text_arms, rk, px)[1]},
                                                            indent=1, default=list))
        print(f"\nper-need detail: {CACHE / 'results-tune.json'}")
        return
    if mode == "test":
        if "--confirm-test-run" not in sys.argv:
            sys.exit("TEST is run once, by the protocol (PROTOCOL.md step 5): pass --confirm-test-run to run it.")
        with (DATA / "TEST_RUNS.log").open("a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} test run, argv={sys.argv[2:]}\n")
        out = {}
        tneeds = [text[i] for i in split["test"] if i in text]
        mneeds = [mig[i] for i in split["test"] if i in mig]
        rows, per_t = run(tneeds, text_arms, rk, px)
        out["TEST A"] = report("TEST, stratum A (awesome-python)", rows, TEXT_PAIRS)
        rows, per_m = run(mneeds, mig_arms, rk, px)
        out["TEST M"] = report("TEST, stratum M (PyMigBench)", rows, [
            ("M1 source summary as query, GitHub order", "M2 summary+keywords + topics, embed+stars"),
            ("M2 summary+keywords + topics, embed+stars", "M3 M2 + context7"),
            ("M2 summary+keywords + topics, embed+stars", "M5 M2 + PyPI summary index")])
        rows, per_c = run([code[i] for i in split["code_test"]], code_arms, rk, px)
        out["TEST C"] = report("TEST, code snippets", rows, [
            ("C0 identifiers as query, GitHub order", "C3 agent-written need (3 lines) -> A6 pipeline"),
            ("C3 agent-written need (3 lines) -> A6 pipeline", "C5 C3 + PyPI summary index")])
        (CACHE / "results-test.json").write_text(json.dumps({"results": out, "per_need": {**per_t, **per_m, **per_c}},
                                                            indent=1, default=list))
        return
    sys.exit(f"unknown --mode {mode}")


if __name__ == "__main__":
    main()
