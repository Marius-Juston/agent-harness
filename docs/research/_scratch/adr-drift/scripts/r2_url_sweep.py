"""R2b check 8a: precision of the weekly dead-link sweep that already ships (lychee in the template's maintenance.yml),
from project B's only run (2026-10-12, log saved by `gh run view --log`).

usage: python3 r2_url_sweep.py MAINT_LOG [N_DOI_SAMPLE]
Every lychee error URL is re-checked today (2026-10-13): a DOI through the DOI registry's handle API (registered =
the link resolves, so a 403 is a publisher bot block), a github.com blob URL through `gh api` contents (exists =
GitHub's anonymous 503 throttling), anything else by GET with a browser User-Agent. Dead = 404/410, DNS/connection
failure, or an unregistered DOI / missing GitHub path. Prints counts per lychee status, split by research scratch.
"""

import collections
import json
import random
import re
import subprocess
import sys
import urllib.parse
import urllib.request


def rows(log):
    cur, out = None, []
    for l in open(log, errors="replace"):  # noqa: E741, SIM115 - research script, kept as run
        l = l.split("Z ", 1)[-1].rstrip()  # noqa: E741 - research script, kept as run
        m = re.match(r"### Errors in (.*)", l)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r"\* \[([^\]]+)\] <([^>]+)>", l)
        if m and cur:
            out.append((cur, m.group(1), m.group(2)))
    return out


def on_host(u, host):
    """True when the URL's hostname is `host` or a subdomain of it (exact parse, not a substring)."""
    h = (urllib.parse.urlsplit(u).hostname or "").lower()
    return h == host or h.endswith("." + host)


def doi_ok(u):
    doi = urllib.parse.unquote(u.split("doi.org/", 1)[1])
    try:
        with urllib.request.urlopen(f"https://doi.org/api/handles/{urllib.parse.quote(doi)}", timeout=30) as r:
            return json.load(r).get("responseCode") == 1
    except urllib.error.HTTPError as e:
        return False if e.code == 404 else None
    except Exception:
        return None


def gh_ok(u):
    m = re.match(r"https://github.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)", u)
    p = subprocess.run(
        ["gh", "api", f"repos/{m[1]}/{m[2]}/contents/{m[4]}?ref={m[3]}", "--silent"], capture_output=True
    )
    return p.returncode == 0


def get_ok(u):
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"})  # noqa: S310 - research script, kept as run
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - research script, kept as run
            return True, r.status
    except urllib.error.HTTPError as e:
        return (False if e.code in (404, 410) else None), e.code
    except Exception as e:
        return False, type(e).__name__


log = sys.argv[1]
n_doi = int(sys.argv[2]) if len(sys.argv) > 2 else 60
uniq = {}
for f, code, u in rows(log):
    uniq.setdefault(u, [code, set()])[1].add("scratch" if "/_scratch/" in f else "note")
print("lychee error URLs (unique):", len(uniq), dict(collections.Counter(v[0] for v in uniq.values())))
random.seed(220)
dois = [u for u, v in uniq.items() if on_host(u, "doi.org") and v[0] == "403"]
sample = set(random.sample(dois, min(n_doi, len(dois))))
res = collections.Counter()
for u, (code, where) in sorted(uniq.items()):
    if on_host(u, "doi.org") and code == "403" and u not in sample:
        continue
    if on_host(u, "doi.org"):
        ok = doi_ok(u)
        how = "doi-handle"
    elif on_host(u, "github.com") and "/blob/" in u:
        ok = gh_ok(u)
        how = "gh-contents"
    else:
        ok, how = get_ok(u)
    verdict = "alive" if ok else ("DEAD" if ok is False else "unknown")
    res[(code, verdict)] += 1
    if verdict != "alive" or code not in ("403", "503"):
        print(f"  [{code}] {verdict:7} {how} {'/'.join(sorted(where))} {u[:110]}")
print("re-check (lychee status, verdict):", dict(sorted(res.items())))
print(f"403 doi.org sampled {len(sample)} of {len(dois)}")
