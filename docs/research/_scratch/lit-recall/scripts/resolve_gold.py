"""Resolve each unit's raw cited ids to one record per work (PROTOCOL.md rule 1): OpenAlex OR-filter batches through
the shared `devkit.lit` client (cache, buckets), then DataCite for arXiv ids OpenAlex lacks (amendment 1).

Usage: uv run python resolve_gold.py      (from the repository's environment: it imports devkit)

Writes data/gold_A.json and PRIV/gold_P.json: {unit: [{key, title, year, first_author, type, domain, ids}]}, and
data/gold_counts.json (raw ids, resolved, dropped by cause, per unit: public numbers only).
"""

import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from common import PRIV, dump, load, norm_title, patient_fetch

from devkit import lit

SELECT = "select=id,doi,title,publication_year,type,ids,authorships,primary_topic,locations"
DROP_TYPES = {"dataset", "software", "erratum", "paratext", "retraction", "supplementary-materials"}
BATCH = 40


def family(name: str) -> str:
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    parts = [p for p in re.split(r"[^a-z]+", name) if p]
    return parts[-1] if parts else ""


def oa_record(w: dict) -> dict:
    ids = w.get("ids") or {}
    arx = ""
    for loc in w.get("locations") or []:
        m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", loc.get("landing_page_url") or "")
        if m:
            arx = m.group(1)
            break
    doi = lit._strip_doi(w.get("doi") or ids.get("doi")).lower()
    if doi.startswith("10.48550/arxiv."):
        arx = arx or doi.split("arxiv.", 1)[1]
    auth = w.get("authorships") or []
    topic = w.get("primary_topic") or {}
    return {
        "title": w.get("title") or "",
        "year": w.get("publication_year"),
        "first_author": family((auth[0].get("author") or {}).get("display_name", "")) if auth else "",
        "type": w.get("type") or "",
        "domain": ((topic.get("domain") or {}).get("display_name")) or "",
        "ids": {
            "doi": doi,
            "arxiv": arx,
            "pmid": (ids.get("pmid") or "").rsplit("/", 1)[-1],
            "pmcid": ("PMC" + (ids.get("pmcid") or "").rstrip("/").rsplit("/", 1)[-1].removeprefix("PMC"))
            if ids.get("pmcid")
            else "",
            "openalex": (w.get("id") or "").rsplit("/", 1)[-1],
        },
    }


def openalex_many(c: lit.Client, field: str, values: list[str]) -> list[dict]:
    out = []
    for s in range(0, len(values), BATCH):
        chunk = values[s : s + BATCH]
        q = urllib.parse.quote("|".join(chunk), safe="/")
        url = f"https://api.openalex.org/works?filter={field}:{q}&per_page={len(chunk)}&{SELECT}"
        out += [oa_record(w) for w in json.loads(patient_fetch(c, "openalex", url)).get("results", [])]
    return out


def arxiv_many(c: lit.Client, ids: list[str]) -> list[dict]:
    """arXiv ids OpenAlex lacks, from DataCite's record of the arXiv DOI (10.48550/arXiv.<id>). Amendment 1: the arXiv
    API throttled this IP for the whole resolution run (2026-10-13), and DataCite holds the same title, first
    author and year. Answers are cached in PRIV/datacite_cache.json."""
    cache_path = PRIV / "datacite_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    out = []
    for aid in ids:
        if aid not in cache:
            url = f"https://api.datacite.org/dois/10.48550/arxiv.{aid}"
            req = urllib.request.Request(url, headers={"User-Agent": lit.UA})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (fixed https host)
                    cache[aid] = json.loads(r.read())
            except urllib.error.HTTPError as exc:
                cache[aid] = {"error": exc.code}
            time.sleep(0.5)
            cache_path.write_text(json.dumps(cache))
        a = (cache[aid].get("data") or {}).get("attributes") or {}
        if not a.get("titles"):
            continue
        creators = a.get("creators") or [{}]
        out.append(
            {
                "title": a["titles"][0].get("title", ""),
                "year": a.get("publicationYear"),
                "first_author": family(creators[0].get("familyName") or creators[0].get("name", "")),
                "type": "preprint",
                "domain": "",
                "ids": {"doi": "", "arxiv": aid},
            }
        )
    return out


def resolve(c: lit.Client, raw: dict[str, list[str]]) -> tuple[list[dict], dict]:
    recs: list[dict] = []
    recs += openalex_many(c, "doi", raw["doi"])
    recs += openalex_many(c, "doi", [f"10.48550/arxiv.{a}" for a in raw["arxiv"]])
    recs += openalex_many(c, "pmid", raw["pmid"])
    recs += openalex_many(c, "pmcid", raw["pmcid"])
    got_arxiv = {r["ids"].get("arxiv") for r in recs}
    recs += arxiv_many(c, [a for a in raw["arxiv"] if a not in got_arxiv])
    # one record per work: same OpenAlex id, DOI, arXiv id or normalised title
    works: list[dict] = []
    for r in recs:
        same = next(
            (
                w
                for w in works
                if any(r["ids"].get(k) and r["ids"].get(k) == w["ids"].get(k) for k in ("openalex", "doi", "arxiv"))
                or (norm_title(r["title"]) and norm_title(r["title"]) == norm_title(w["title"]))
            ),
            None,
        )
        if same is None:
            works.append(r)
        else:
            same["ids"] = {k: same["ids"].get(k) or r["ids"].get(k, "") for k in {*same["ids"], *r["ids"]}}
    found = {
        *{("doi", r["ids"]["doi"]) for r in recs if r["ids"].get("doi")},
        *{("arxiv", r["ids"]["arxiv"]) for r in recs if r["ids"].get("arxiv")},
    }
    unresolved = [f"doi:{d}" for d in raw["doi"] if ("doi", d) not in found] + [
        f"arxiv:{a}" for a in raw["arxiv"] if ("arxiv", a) not in found
    ]
    kept = [w for w in works if w["type"] not in DROP_TYPES and w["title"]]
    for i, w in enumerate(kept):
        w["key"] = f"g{i:03d}"
    counts = {
        "raw_ids": sum(len(v) for v in raw.values()),
        "records": len(recs),
        "works": len(works),
        "dropped_type": len(works) - len(kept),
        "unresolved_ids": len(unresolved),
        "gold": len(kept),
    }
    return kept, {**counts, "unresolved": unresolved}


def main() -> None:
    c = lit.client()
    counts = {}
    for raw_name, out_name, private in (
        ("gold_raw_A.json", "gold_A.json", False),
        ("gold_raw_P.json", "gold_P.json", True),
    ):
        out = {}
        for uid, raw in load(raw_name, private).items():
            out[uid], cnt = resolve(c, raw)
            counts[uid] = {k: v for k, v in cnt.items() if k != "unresolved"}
            if not private:
                counts[uid]["unresolved"] = cnt["unresolved"]
            print(uid, counts[uid])
        dump(out, out_name, private)
    dump(counts, "gold_counts.json")


if __name__ == "__main__":
    main()
