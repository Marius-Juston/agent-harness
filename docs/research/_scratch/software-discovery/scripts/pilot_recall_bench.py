"""Recall of library-discovery strategies on needs with a known-good answer.

Usage: uv run --with model2vec python recall_bench.py <cache_dir>
GitHub search goes through `gh api` (authenticated, 30 searches/min); answers are cached as JSON files.
"""

import hashlib
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

CACHE = Path(sys.argv[1])
CACHE.mkdir(parents=True, exist_ok=True)

# need, paraphrases an agent would write (no library names), accepted answers (owner/repo, lower case)
NEEDS = [
    (
        "spaced repetition scheduler",
        ["flashcard review interval algorithm", "memory model for scheduling reviews"],
        {"open-spaced-repetition/py-fsrs"},
    ),
    (
        "computerized adaptive testing with item response theory",
        ["IRT item selection simulation", "adaptive test engine"],
        {"douglasrizzo/catsim"},
    ),
    (
        "detect and anonymize personally identifiable information in text",
        ["PII redaction", "de-identification of names emails phone numbers"],
        {"microsoft/presidio", "data-privacy-stack/presidio"},
    ),
    (
        "retry a function call with exponential backoff",
        ["retrying decorator", "retry transient failures with jitter"],
        {"jd/tenacity", "litl/backoff", "hynek/stamina"},
    ),
    (
        "fuzzy string matching",
        ["string similarity levenshtein", "approximate text match ratio"],
        {"rapidfuzz/rapidfuzz", "seatgeek/thefuzz"},
    ),
    (
        "validate JSON against a schema",
        ["JSON Schema validator", "check json documents follow a schema"],
        {"python-jsonschema/jsonschema", "horejsek/python-fastjsonschema"},
    ),
    (
        "property-based testing",
        ["generate random test inputs and shrink failures", "quickcheck for python"],
        {"hypothesisworks/hypothesis"},
    ),
    (
        "convert HTML to Markdown",
        ["html to markdown converter", "turn web page html into markdown text"],
        {"matthewwithanm/python-markdownify", "alir3z4/html2text"},
    ),
    (
        "detect the character encoding of bytes",
        ["charset detection", "guess text encoding of a file"],
        {"jawah/charset_normalizer", "chardet/chardet"},
    ),
    (
        "parse cron expressions and compute next run time",
        ["cron schedule iterator", "next occurrence of a crontab line"],
        {"kiorky/croniter", "pallets-eco/croniter"},
    ),
    (
        "parse SPDX license expressions",
        ["license expression parser", "normalize open source license identifiers"],
        {"aboutcode-org/license-expression", "nexb/license-expression"},
    ),
    (
        "deep difference between two python objects",
        ["compare nested dicts and lists", "diff of data structures"],
        {"seperman/deepdiff"},
    ),
    (
        "rate limiting with a token bucket",
        ["rate limiter", "throttle calls per second leaky bucket"],
        {"vutran1710/pyratelimiter", "alisaifee/limits"},
    ),
    (
        "parse and compare semantic version strings",
        ["semver", "version number comparison"],
        {"python-semver/python-semver", "pypa/packaging"},
    ),
    (
        "cross-process file lock",
        ["file locking", "lock a file between processes"],
        {"tox-dev/filelock", "tox-dev/py-filelock", "harlowja/fasteners"},
    ),
    (
        "small fast static text embeddings",
        ["sentence embeddings without a gpu", "distilled embedding model"],
        {"minishlab/model2vec"},
    ),
]


def gh_search(q: str, sort: str = "", n: int = 30) -> list[dict]:
    key = hashlib.sha1(f"{q}|{sort}|{n}".encode()).hexdigest()  # noqa: S324 - research script, kept as run
    f = CACHE / f"{key}.json"
    if f.exists():
        return json.loads(f.read_text())
    args = ["gh", "api", "-X", "GET", "search/repositories", "-f", f"q={q}", "-f", f"per_page={n}"]
    if sort:
        args += ["-f", f"sort={sort}"]
    for attempt in range(4):
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode == 0:
            break
        time.sleep(20 * (attempt + 1))
    else:
        raise RuntimeError(r.stderr)
    items = [
        {
            "name": i["full_name"].lower(),
            "stars": i["stargazers_count"],
            "desc": i["description"] or "",
            "topics": i.get("topics", []),
        }
        for i in json.loads(r.stdout)["items"]
    ]
    f.write_text(json.dumps(items))
    time.sleep(2.2)  # stay under 30 searches/min
    return items


STOP = {
    "a",
    "an",
    "the",
    "of",
    "to",
    "in",
    "for",
    "with",
    "and",
    "or",
    "between",
    "two",
    "into",
    "on",
    "from",
    "without",
}


def terms(q: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9+#.-]+", q.lower()) if w not in STOP]


def eco_keyword(kw: str) -> list[dict]:
    key = hashlib.sha1(f"eco|{kw}".encode()).hexdigest()  # noqa: S324 - research script, kept as run
    f = CACHE / f"{key}.json"
    if f.exists():
        return json.loads(f.read_text())
    import urllib.request

    url = f"https://packages.ecosyste.ms/api/v1/keywords/{kw}?per_page=50"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "recall-bench"}), timeout=30
        ) as r:
            pk = json.loads(r.read())["packages"]
    except Exception:
        pk = []
    items = []
    for p in pk:
        if p.get("ecosystem") != "pypi":
            continue
        ru = (p.get("repository_url") or "").lower()
        m = re.match(r"https://github.com/([^/]+/[^/#?]+)", ru)
        if m:
            items.append(
                {
                    "name": m.group(1).removesuffix(".git"),
                    "stars": (p.get("repo_metadata") or {}).get("stargazers_count") or 0,
                    "desc": p.get("description") or "",
                    "topics": p.get("keywords_array") or [],
                }
            )
    f.write_text(json.dumps(items))
    return items


def pool(queries: list[str], topics: bool, relax: bool = False) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for q in queries:
        qq = " ".join(terms(q)) if relax else q
        res = gh_search(f"{qq} language:python")
        if relax and len(res) < 5 and len(terms(q)) > 1:
            res += gh_search(" OR ".join(terms(q)[:5]) + " language:python")
        if relax:
            for kw in {"-".join(terms(q)), *(["-".join(terms(q)[:2])] if len(terms(q)) > 2 else [])}:
                res += eco_keyword(kw)
        for i in res:
            out.setdefault(i["name"], i)
    if topics:
        counts: dict[str, int] = {}
        for i in sorted(out.values(), key=lambda x: -x["stars"])[:10]:
            for t in i["topics"]:
                counts[t] = counts.get(t, 0) + 1
        for t, c in sorted(counts.items(), key=lambda kv: -kv[1])[:3]:
            if c >= 2 and t != "python":
                for i in gh_search(f"topic:{t} language:python", sort="stars"):
                    out.setdefault(i["name"], i)
    return out


def main() -> None:
    from model2vec import StaticModel

    model = StaticModel.from_pretrained("minishlab/potion-base-8M")

    def cos(a, b):
        return float((a * b).sum() / (math.sqrt((a * a).sum() * (b * b).sum()) + 1e-9))

    strategies = {
        "S0 one query, GitHub order": (False, False, "gh"),
        "S1 3 queries, GitHub order": (True, False, "gh"),
        "S2 3 queries + topics, stars": (True, True, "stars"),
        "S3 3 queries + topics, embed": (True, True, "embed"),
        "S4 3 queries + topics, embed+stars": (True, True, "blend"),
        "S5 S4 + relaxed queries + ecosyste.ms keywords": (True, True, "blend+relax"),
        "S6 S5, query vector = mean of the 3 queries": (True, True, "blend+mq+relax"),
        "S7 S5 pool, lexical BM25 over 3 queries + stars (no embeddings)": (True, True, "lex+relax"),
    }
    hits = {s: 0 for s in strategies}
    rows = []
    for need, para, gold in NEEDS:
        qv = model.encode([need])[0]
        row = [need]
        for s, (multi, topics, order) in strategies.items():
            qs = [need, *para] if multi else [need]
            p = pool(qs, topics, relax=order.endswith("relax"))
            items = list(p.values())
            if order == "gh":
                ranked = items  # insertion order = GitHub best match, query by query
            elif order.startswith("lex"):
                docs = [
                    terms(
                        f"{i['name'].split('/')[1].replace('-', ' ').replace('_', ' ')} {i['desc']} {' '.join(i['topics']).replace('-', ' ')}"
                    )
                    for i in items
                ]
                n_ = len(docs) or 1
                avg = sum(map(len, docs)) / n_ or 1
                df: dict[str, int] = {}
                for d in docs:
                    for w in set(d):
                        df[w] = df.get(w, 0) + 1
                qt = set(terms(" ".join([need, *para])))

                def bm25(d):
                    sc = 0.0
                    for w in qt:  # noqa: B023 - research script, kept as run
                        tf = d.count(w)
                        if tf:
                            idf = math.log(1 + (n_ - df[w] + 0.5) / (df[w] + 0.5))  # noqa: B023 - research script, kept as run
                            sc += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(d) / avg))  # noqa: B023 - research script, kept as run
                    return sc

                score = [bm25(d) + 0.3 * math.log10(1 + i["stars"]) for d, i in zip(docs, items, strict=True)]
                ranked = [i for _, i in sorted(zip(score, items, strict=True), key=lambda t: -t[0])]
            elif order == "stars":
                ranked = sorted(items, key=lambda i: -i["stars"])
            else:
                texts = [f"{i['name'].split('/')[1]} {i['desc']} {' '.join(i['topics'])}" for i in items]
                vs = model.encode(texts) if texts else []
                qvec = model.encode([need, *para]).mean(axis=0) if "+mq" in order else qv
                sims = [cos(qvec, v) for v in vs]
                if order == "embed":
                    score = sims
                else:
                    score = [s_ + 0.03 * math.log10(1 + i["stars"]) for s_, i in zip(sims, items, strict=True)]
                ranked = [i for _, i in sorted(zip(score, items, strict=True), key=lambda t: -t[0])]
            top = [i["name"] for i in ranked[:10]]
            hit = bool(gold & set(top))
            hits[s] += hit
            inpool = bool(gold & set(p))
            row.append(f"{'Y' if hit else ('p' if inpool else '-')}{len(items)}")
        rows.append(row)
    print("need | " + " | ".join(strategies))
    for r in rows:
        print(" | ".join(r))
    print(f"\nhit@10 over {len(NEEDS)} needs:")
    for s, h in hits.items():
        print(f"  {s}: {h}/{len(NEEDS)}")


if __name__ == "__main__":
    main()
