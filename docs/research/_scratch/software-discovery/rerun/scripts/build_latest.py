"""Stratum L (#176 rerun PROTOCOL.md, rule L1-L5): needs whose gold is a Python library first released on PyPI after
the model's training cutoff. Gold is chosen by outside signals only (GitHub creation date and stars, PyPI upload
times); the need text is the library's own PyPI summary with its names masked. Nothing here is written by hand.

Usage: uv run --no-project python -I build_latest.py <new_repos.json from fetch_new_repos.py> <corpus.jsonl> <cache_dir>
           <out.json>

L1 universe: public, non-fork, non-archived GitHub repositories with language Python, created 2026-07-01..2026-10-12,
   with at least 100 stars when fetched (fetch_new_repos.py, 2026-10-13).
L2 identity (repository -> package, never the reverse, software-discovery.md section 7): a PyPI project is the
   repository's package if its PyPI JSON lists github.com/<owner>/<name> in project_urls or home_page. Names tried:
   the repository's name, and every P1-corpus project whose parsed repository is this one.
L3 post-cutoff: the earliest file upload of every accepted package of the repository is on or after 2026-07-01.
L4 need text: the primary package's PyPI summary (the package whose normalised name equals the repository name, else
   the shortest name), with its names masked (mask(): keys are the package names, the repository name and the owner,
   lower case without separators; hyphen parts are not keys, so common words such as "agent" survive).
   also_described_as: its PyPI keywords, masked alike.
   A need left with fewer than 4 words is dropped.
L5 gold: the repository (canonical owner/name) and every accepted package name.
L6 not a placeholder: some accepted package has at least 2 releases with files (16 of 121 had one, several of them
   name reservations).
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UNIVERSE, CORPUS, CACHE, OUT = (Path(a) for a in sys.argv[1:5])
CACHE.mkdir(parents=True, exist_ok=True)
CUTOFF = "2026-07-01"
MIN_WORDS = 4
UA = {"User-Agent": "agent-harness-rerun-176/0.1 (research; github.com/Marius-Juston/agent-harness)"}


def norm(s: str) -> str:
    return re.sub(r"[-_.]+", "-", s).lower()


def pypi(name: str) -> dict | None:
    f = CACHE / f"pypi-{norm(name)}.json"
    if f.exists():
        return json.loads(f.read_text()) or None
    for attempt in range(3):
        try:
            req = urllib.request.Request(f"https://pypi.org/pypi/{name}/json", headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (fixed host)
                body = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                body = {}
                break
            time.sleep(2 + 3 * attempt)
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2 + 3 * attempt)
    else:
        return None  # transient: not cached
    time.sleep(0.4)  # PyPI asks for 1-2 requests a second from serial clients
    f.write_text(json.dumps(body))
    return body or None


def links_to(info: dict, repo: str) -> bool:
    urls = [*(info.get("project_urls") or {}).values(), info.get("home_page") or ""]
    for u in urls:
        m = re.match(r"https?://(?:www\.)?github\.com/([^/\s]+)/([^/#?\s]+)", u or "")
        if m and f"{m.group(1)}/{m.group(2)}".lower().removesuffix(".git") == repo:
            return True
    return False


def first_upload(body: dict) -> str:
    times = [f["upload_time_iso_8601"] for files in (body.get("releases") or {}).values() for f in files]
    return min(times) if times else ""


def mask(text: str, keys: set[str]) -> str:
    """Drop every word equal to a key, every word containing a key of 4+ characters, and every run of 2-3 words whose
    letters joined equal a key ("Gray Box" for graybox)."""
    words = (text or "").split()
    bare = [re.sub(r"[^a-z0-9]", "", w.lower()) for w in words]
    drop = {j for j, b in enumerate(bare) if b and (b in keys or any(len(k) >= 4 and k in b for k in keys))}
    for size in (2, 3):
        for j in range(len(words) - size + 1):
            if "".join(bare[j : j + size]) in keys:
                drop |= set(range(j, j + size))
    return " ".join(w for j, w in enumerate(words) if j not in drop).strip(" ,;:-")


def main() -> None:
    universe = json.loads(UNIVERSE.read_text())
    corpus_names: dict[str, list[str]] = {}
    with CORPUS.open() as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("repo") in universe:
                corpus_names.setdefault(r["repo"], []).append(r["name"])
    wanted = {nm for repo in universe for nm in [repo.split("/", 1)[1], *corpus_names.get(repo, [])]}
    with ThreadPoolExecutor(6) as pool:  # prefetch into the cache: 6 parallel GETs to PyPI's CDN-served JSON API
        list(pool.map(pypi, sorted(wanted)))
    rows, dropped = [], {"no package": 0, "pre-cutoff release": 0, "short need": 0, "one release": 0}
    for k, (repo, meta) in enumerate(sorted(universe.items())):
        if meta.get("fork") or meta.get("archived") or meta["created_at"][:10] < CUTOFF:
            continue
        owner, rname = repo.split("/", 1)
        tried = dict.fromkeys([rname, *corpus_names.get(repo, [])])
        pkgs = []
        for nm in tried:
            body = pypi(nm)
            if body and links_to(body["info"], repo):
                releases = sum(1 for files in (body.get("releases") or {}).values() if files)
                pkgs.append((body["info"]["name"], first_upload(body), body["info"], releases))
        pkgs = list({norm(p[0]): p for p in pkgs}.values())
        if not pkgs:
            dropped["no package"] += 1
            continue
        if any(not p[1] or p[1][:10] < CUTOFF for p in pkgs):
            dropped["pre-cutoff release"] += 1
            continue
        if max(p[3] for p in pkgs) < 2:
            dropped["one release"] += 1
            continue
        primary = next((p for p in pkgs if norm(p[0]) == norm(rname)), min(pkgs, key=lambda p: len(p[0])))
        names = [*(x[0] for x in pkgs), rname, owner]
        keys = {norm(nm).replace("-", "") for nm in names} - {""}
        need = mask(primary[2].get("summary") or "", keys)
        if len(need.split()) < MIN_WORDS:
            dropped["short need"] += 1
            continue
        kw = mask((primary[2].get("keywords") or "").replace(",", " "), keys)
        rows.append(
            {
                "repo": repo,
                "packages": sorted(p[0] for p in pkgs),
                "first_upload": min(p[1] for p in pkgs),
                "repo_created": meta["created_at"],
                "stars_at_fetch": meta["stargazers_count"],
                "summary": primary[2].get("summary") or "",
                "need": need,
                "also_described_as": [kw] if kw else [],
            }
        )
        if k % 200 == 0:
            print(f"{k}/{len(universe)} repos, {len(rows)} L needs", flush=True)
    OUT.write_text(json.dumps(sorted(rows, key=lambda r: r["repo"]), indent=1, ensure_ascii=False) + "\n")
    print(f"L needs: {len(rows)}; dropped {dropped}")


if __name__ == "__main__":
    main()
