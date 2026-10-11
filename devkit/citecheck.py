"""Verify the sources a research note cites (`uv run dev cite-check <note>...`).

A model can produce a fluent citation to a paper that does not exist, or a real arXiv id under the wrong title.
This resolves every link and, where an authoritative record exists, compares the cited title with it:

- arXiv ids (abs/, html/, pdf/ links): one batched query to the arXiv API; link text that looks like a title must
  match the record's title.
- DOIs (doi.org links): the Crossref record; same title comparison.
- GitHub repositories: the API record (exists, archived?).
- Any other http(s) link: answers with a status below 400.

Results are cached per URL in the shared git dir for CACHE_DAYS, so re-checks and other worktrees cost nothing. Only
definite answers are cached: a timeout, 5xx or 429 is retried next run. A 401/403 (paywall, bot wall) proves nothing
about the page, so it is reported as unverified, never as ok.
"""

import concurrent.futures
import contextlib
import difflib
import fcntl
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from devkit.paths import state_dir

CACHE_DAYS = 14
UA = "agent-harness-devkit-citecheck/0.1 (research verification; mailto:noreply@example.invalid)"
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
BARE = re.compile(r"(?<![(\[])\bhttps?://[^\s)>\]`'\"]+")
BARE_ARXIV = re.compile(r"\b(?:arXiv[: ]*)?(\d{4}\.\d{4,5})(?:v\d+)?\b")
CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
ARXIV = re.compile(r"arxiv\.org/(?:abs|html|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?")
DOI = re.compile(r"doi\.org/(10\.\d{4,9}/[^\s?#]+)")
GITHUB = re.compile(r"^https?://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
TITLE_MIN_WORDS = 4
MATCH_FLOOR = 0.55


def _get(url: str, timeout: int = 20) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(400_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, str(e)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower())


def similar(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def looks_like_title(text: str) -> bool:
    return len(text.split()) >= TITLE_MIN_WORDS and not text.lower().startswith(("http", "arxiv", "doi"))


def arxiv_titles(ids: list[str]) -> dict[str, str]:
    """id -> title for ids that exist (one API call per 50 ids, as the arXiv API terms ask)."""
    titles: dict[str, str] = {}
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for i in range(0, len(ids), 50):
        chunk = ids[i : i + 50]
        status, body = _get("https://export.arxiv.org/api/query?max_results=50&id_list=" + ",".join(chunk), timeout=40)
        if status != 200:
            raise RuntimeError(f"arXiv API answered {status or body}")
        for entry in ET.fromstring(body).findall("a:entry", ns):
            eid = (entry.findtext("a:id", "", ns) or "").rsplit("/", 1)[-1].split("v")[0]
            title = " ".join((entry.findtext("a:title", "", ns) or "").split())
            if eid and title and title.lower() != "error":
                titles[eid] = title
        if i + 50 < len(ids):
            time.sleep(3)
    return titles


RETRY_DELAY = 2.0  # seconds before the one retry of a transient answer


def _check_one(url: str) -> dict:
    """One check, retried once after RETRY_DELAY when the answer is transient (timeout, 5xx, 429)."""
    res = _check_once(url)
    if res.get("transient"):
        time.sleep(RETRY_DELAY)
        res = _check_once(url)
    return res


def _check_once(url: str) -> dict:
    if m := DOI.search(url):
        status, body = _get(f"https://api.crossref.org/works/{urllib.parse.quote(m.group(1))}")
        if status == 200:
            title = (json.loads(body)["message"].get("title") or [""])[0]
            return {"ok": True, "title": title}
        if transient(status):
            return {"ok": False, "transient": True, "why": f"Crossref {status or body[:80]}: not reachable now"}
        return {"ok": False, "why": f"DOI not found at Crossref ({status})"}
    if m := GITHUB.match(url):
        status, body = _get(f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}")
        if status == 200:
            data = json.loads(body)
            return {"ok": True, "title": data.get("full_name", ""), "note": "ARCHIVED" if data.get("archived") else ""}
        if status in (403, 429):  # GitHub's rate limit, not an answer about the repository
            return {"ok": False, "transient": True, "why": f"GitHub rate limit ({status}): unchecked"}
        if transient(status):
            return {"ok": False, "transient": True, "why": f"GitHub API {status or body[:80]}"}
        return {"ok": False, "why": f"GitHub repo not found ({status})"}
    status, body = _get(url)
    if status in (401, 403, 999):  # paywalls and bot walls: the page may or may not exist
        return {"ok": False, "unverified": True, "why": f"HTTP {status}: unverified (cite a DOI or an open copy)"}
    if transient(status):
        return {"ok": False, "transient": True, "why": f"HTTP {status or body[:80]}: not reachable now"}
    if status < 400:
        return {"ok": True}
    return {"ok": False, "why": f"HTTP {status}"}


def transient(status: int) -> bool:
    """No answer (timeout, DNS, reset), a server error or a rate limit: worth retrying, never worth caching."""
    return status == 0 or status == 429 or status >= 500


@contextlib.contextmanager
def _locked(path: Path):
    with open(path.with_suffix(".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


STALE_NOTES = ("content unverified", "rate limit: unchecked")  # older versions cached these as ok; re-check them


def _read_cache(path: Path) -> dict:
    try:
        cache = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in cache.items() if not any(s in str(v.get("note", "")) for s in STALE_NOTES)}


def save_cache(path: Path, new: dict) -> None:
    """Merge `new` into the cache file under a lock and replace the file atomically: a parallel reader sees the old
    file or the new one, never half of one, and parallel writers keep each other's entries."""
    with _locked(path):
        cache = _read_cache(path) | new
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(cache, f)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise


def check_notes(paths: list[Path], offline_ok: bool = False) -> tuple[int, list[str], list[str]]:
    """(links checked, problems, notes) for the given markdown files. Under CI or offline_ok, a link that stayed
    unreachable after its retry is a note, so one rate-limited site cannot fail the run."""
    lenient = offline_ok or bool(os.environ.get("CI"))
    cache_file = state_dir() / "cite-cache.json"
    cache = _read_cache(cache_file)
    new: dict[str, dict] = {}
    now = time.time()
    cites: list[tuple[Path, str, str]] = []
    for p in paths:
        text = CODE.sub("", p.read_text(encoding="utf-8"))  # code spans hold endpoints and examples, not citations
        found = LINK.findall(text)
        cites += [(p, label, url.rstrip(".,;")) for label, url in found]
        linked = {u for _, u in found}
        cites += [(p, "", u.rstrip(".,;")) for u in BARE.findall(text) if u not in linked]
        linked_ids = {m.group(1) for u in linked if (m := ARXIV.search(u))}
        cites += [
            (p, "", f"https://arxiv.org/abs/{i}")
            for i in sorted(set(BARE_ARXIV.findall(LINK.sub("", text))))
            if i not in linked_ids
        ]
    problems, notes = [], []
    ids = sorted({m.group(1) for _, _, u in cites if (m := ARXIV.search(u))})
    fresh_ids = [i for i in ids if now - cache.get(f"arxiv:{i}", {}).get("t", 0) > CACHE_DAYS * 86400]
    if fresh_ids:
        try:
            titles = arxiv_titles(fresh_ids)
            for i in fresh_ids:
                new[f"arxiv:{i}"] = {"t": now, "title": titles.get(i)}
        except (RuntimeError, ET.ParseError) as exc:
            (notes if offline_ok else problems).append(f"arXiv API unreachable ({exc}): arXiv ids unchecked")
    urls = sorted({u for _, _, u in cites if not ARXIV.search(u)})
    todo = [u for u in urls if now - cache.get(u, {}).get("t", 0) > CACHE_DAYS * 86400]
    fetched: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        for u, res in zip(todo, pool.map(_check_one, todo), strict=True):
            fetched[u] = {"t": now, **res}
            if not res.get("transient"):
                new[u] = fetched[u]
    if new:
        save_cache(cache_file, new)
    cache |= fetched | new
    for p, label, url in cites:
        where = f"{p.name}: [{label[:60]}]({url})" if label else f"{p.name}: {url}"
        if m := ARXIV.search(url):
            rec = cache.get(f"arxiv:{m.group(1)}")
            if rec is None:
                continue
            if not rec.get("title"):
                problems.append(f"{where}: arXiv id {m.group(1)} does not exist")
            elif label and looks_like_title(label) and similar(label, rec["title"]) < MATCH_FLOOR:
                problems.append(f"{where}: link text does not match arXiv title {rec['title']!r}")
            continue
        rec = cache.get(url, {})
        if rec.get("unverified") or (rec.get("transient") and lenient):
            notes.append(f"{where}: {rec['why']}")
        elif not rec.get("ok", False):
            problems.append(f"{where}: {rec.get('why', 'unchecked')}")
        elif (
            rec.get("title")
            and label
            and looks_like_title(label)
            and DOI.search(url)
            and similar(label, rec["title"]) < MATCH_FLOOR
        ):
            problems.append(f"{where}: link text does not match the DOI's title {rec['title']!r}")
        elif rec.get("note"):
            notes.append(f"{where}: {rec['note']}")
    return len(cites), problems, notes
