"""Add named PyPI packages (an agent's B0 picks, or an index's top hits) to a `dev reuse` snapshot (run_reuse_snapshot.py)
and enrich, verify and gate them with PR #171's own functions, so arms A2 and P1-P3 replay offline (PROTOCOL.md step 6).

Usage (PR #171's devkit first on the path, as for run_reuse_snapshot.py):
  PYTHONPATH=<dir with devkit @ 4d61af0> uv run --with model2vec python add_named.py <in_snap> <out_snap> \
      --names <json {need id: [pypi names] | [{name, repo, desc}]}> --tag agent|p1 [--exclude data/m_sources.json]

Per need: every named package becomes a candidate, unless the pool already holds it (same PyPI name, or the same
GitHub repository); its found_by gets the tag. Named candidates get R and the prior from PR #171's encoder, exactly as
search() computes them (cosine to the mean query vector, independent of the other candidates). Every candidate any
config could show at n=10 (the 20 best by prior, the 10 best by R, all within 0.20 of the best R) plus every named
candidate is enriched, verified and gated; the rest stay NOT_ENRICHED. A name neither ecosyste.ms nor PyPI knows is
kept with status NOT_FOUND (harm: the name does not exist).

Index hits ({name, repo, desc} rows from p_index.py) are added without a lookup and are not forced; only those the
shortlist rules reach are looked up (ecosyste.ms by name) and enriched. --exclude (amendment 5 item 22): an M need's
source package is left out when the enrich set is computed, as the scorer leaves it out before ranking.
"""

import dataclasses
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

from devkit import reuse  # PR #171's module (PYTHONPATH)

IN, OUT = Path(sys.argv[1]), Path(sys.argv[2])
NAMES = json.loads(Path(sys.argv[sys.argv.index("--names") + 1]).read_text())
TAG = sys.argv[sys.argv.index("--tag") + 1]
EXCL = json.loads(Path(sys.argv[sys.argv.index("--exclude") + 1]).read_text()) if "--exclude" in sys.argv else {}
N, FLOOR = 10, 0.20
FIELDS = {f.name for f in dataclasses.fields(reuse.Candidate)}
OUT.mkdir(parents=True, exist_ok=True)


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def to_cand(d: dict) -> reuse.Candidate:
    return reuse.Candidate(**{k: v for k, v in d.items() if k in FIELDS})


def named(client, name: str, notes: list[str]) -> reuse.Candidate | None:
    """A candidate for a PyPI name: the ecosyste.ms package record, else the PyPI JSON answer; None if neither knows it."""
    url = f"{reuse.ECOSYSTEMS}registries/pypi.org/packages/{urllib.parse.quote(name, safe='')}"
    rec = reuse._json(client, "ecosystems", url, notes, missing_ok=True)
    if isinstance(rec, dict) and rec.get("name"):
        return reuse.from_record(rec)
    body = reuse._json(client, "pypi", reuse.ECOS["pypi"][2](name), notes, missing_ok=True)
    if not isinstance(body, dict) or "info" not in body:
        return None
    info = body["info"]
    repo = ""
    for u in [*(info.get("project_urls") or {}).values(), info.get("home_page") or ""]:
        if m := re.match(r"https?://(?:www\.)?github\.com/([^/]+/[^/#?]+)", u or ""):
            repo = "github.com/" + m.group(1).lower().removesuffix(".git")
            break
    return reuse.Candidate(
        name=info.get("name") or name, repo=repo, ecosystem="pypi", description=info.get("summary") or ""
    )


def main() -> None:
    encoder, why = reuse.load_encoder()
    client = reuse.client()
    for k, f in enumerate(sorted(IN.glob("*.json"))):
        out = OUT / f.name
        if out.exists():
            continue
        d = json.loads(f.read_text())
        t0 = time.time()
        notes: list[str] = []
        rows = d["candidates"]
        by_name = {norm(c["name"]): c for c in rows if c.get("ecosystem") == "pypi"}
        by_repo = {c["repo"]: c for c in rows if c.get("repo")}
        new: list[reuse.Candidate] = []
        forced: set[str] = set()
        rows_in = [e for e in NAMES.get(d["id"], []) if isinstance(e, dict)]
        for e in rows_in:  # index hits: no lookup now, not forced
            repo = f"github.com/{e['repo']}" if e.get("repo") else ""
            hit = by_name.get(norm(e["name"])) or (by_repo.get(repo) if repo else None)
            if hit is not None:
                hit["found_by"] = list(dict.fromkeys([*hit.get("found_by", []), TAG]))
                continue
            c = reuse.Candidate(
                name=e["name"], repo=repo, ecosystem="pypi", description=e.get("desc", ""), found_by=[TAG]
            )
            new.append(c)
            by_name[norm(c.name)] = {"name": c.name}
            if repo:
                by_repo[repo] = {"name": c.name}
        for nm in [e for e in NAMES.get(d["id"], []) if isinstance(e, str)]:
            hit = by_name.get(norm(nm))
            if hit is None:
                c = named(client, nm, notes)
                if c is None:
                    rows.append(
                        {
                            "name": nm,
                            "ecosystem": "pypi",
                            "status": "NOT_FOUND",
                            "found_by": [TAG],
                            "relevance": 0.0,
                            "prior": 0.0,
                            "repo": "",
                        }
                    )
                    continue
                hit = by_repo.get(c.repo) if c.repo else None
                if hit is None:
                    c.found_by = [TAG]
                    new.append(c)
                    forced.add(c.key())
                    continue
            hit["found_by"] = list(dict.fromkeys([*hit.get("found_by", []), TAG]))
            forced.add(hit["repo"] or f"{hit.get('ecosystem', '')}:{hit['name']}")
        if new:
            reuse.rank_relevance(new, d["queries"], encoder, why)
        rows += [
            {**{key: v for key, v in dataclasses.asdict(c).items() if key != "record"}, "status": "NOT_ENRICHED"}
            for c in new
        ]
        live = {id(c): c for c in new}
        src = EXCL.get(d["id"])

        def is_src(r: dict, src: list | None = src) -> bool:
            return bool(src) and (
                norm(r["name"]) in (norm(src[0]), norm(src[1] or "-"))
                or (bool(src[1]) and r.get("repo", "").removeprefix("github.com/") == src[1].lower())
            )

        found = [r for r in rows if r["status"] != "NOT_FOUND" and not is_src(r)]
        best = max((r["relevance"] for r in found), default=0.0)

        def key(r: dict) -> str:
            return r["repo"] or f"{r.get('ecosystem', '')}:{r['name']}"

        want = (
            {key(r) for r in sorted(found, key=lambda r: -r["prior"])[: 2 * N]}
            | {key(r) for r in sorted(found, key=lambda r: -r["relevance"])[:N]}
            | {key(r) for r in found if r["relevance"] >= best - FLOOR}
            | forced
        )
        newc = {c.key(): c for c in live.values()}
        tiers, osi = reuse.licence_lists(client, notes)
        for i, r in enumerate(rows):
            if r["status"] != "NOT_ENRICHED" or key(r) not in want:
                continue
            c = newc.get(key(r)) or to_cand(r)
            if TAG in c.found_by and c.record is None and isinstance(NAMES.get(d["id"], [None])[0], dict):
                rec = named(client, c.name, notes)  # an index hit reached the shortlist: look it up now
                if rec is not None:
                    rec.repo = rec.repo or c.repo
                    rec.found_by, rec.relevance, rec.prior = c.found_by, c.relevance, c.prior
                    c = rec
            reuse.enrich(client, c, "pypi", notes)
            ok = reuse.verify(client, c, notes)
            now = client.clock()
            c.gates = reuse.gates(c, now, tiers, False)
            c.status = reuse.status(c.gates) if ok else "UNVERIFIED"
            c.quality = reuse.quality(c, now)
            c.score = round(c.relevance + reuse.LAMBDA * c.quality, 4)
            if flag := reuse.osi_flag(c.license, osi):
                c.flags.append(flag)
            rows[i] = {key2: v for key2, v in dataclasses.asdict(c).items() if key2 != "record"}
        d["candidates"] = rows
        d.setdefault("added", {})[TAG] = {
            "names": NAMES.get(d["id"], []),
            "date": time.strftime("%Y-%m-%d"),
            "notes": list(dict.fromkeys(notes)),
        }
        out.write_text(json.dumps(d, default=str))
        print(f"{k + 1} {d['id']}: +{len(new)} new, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
