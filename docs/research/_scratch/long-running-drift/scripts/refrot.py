"""Referential rot in root context files (DOCER-like, paths only).
For each repo's primary file (>=6 months old, >5 lines): extract backticked path-like tokens from the HEAD
version; a token is STALE if it resolves (exactly, or as a path suffix) in the tree at the file's first commit
but not in the HEAD tree; UNRESOLVED if it resolves in neither (examples, globs, generated or later-added-then-
removed paths). Precision of STALE is checked by hand on a sample (see stale_sample.json)."""

import base64
import collections
import json
import random
import re
import subprocess
import sys
from datetime import UTC, datetime


def gh(path):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None


EXT = r"\.(py|ts|tsx|js|jsx|mjs|cjs|rs|go|java|kt|rb|md|json|toml|yaml|yml|sh|c|h|cc|cpp|hpp|cs|swift|txt|cfg|ini|lock|sql|proto|vue|svelte|css|scss|html|mdx|zig|ex|exs|dart|php|gradle|xml)$"
TOK = re.compile(r"`([^`\s]+)`")


def tokens(txt):
    out = set()
    for t in TOK.findall(txt):
        t = t.strip().rstrip(":,.;)").lstrip("(")
        t = re.sub(r":\d+(-\d+)?$", "", t)
        if t.startswith(("http", "-", "$", "~", "@", "<", "/")) or any(c in t for c in "*{}<>|=?[]\"'%"):
            continue
        t = t[2:] if t.startswith("./") else t
        t = t.rstrip("/")
        if ("/" in t and re.match(r"^[\w.\-/]+$", t)) or re.search(EXT, t):
            if "::" in t or t.count(".") > 3:
                continue
            out.add(t)
    return out


def tree(repo, sha):
    d = gh(f"repos/{repo}/git/trees/{sha}?recursive=1")
    if not d:
        return None, True
    return {e["path"] for e in d["tree"]}, d.get("truncated", False)


def resolves(t, paths):
    if t in paths:
        return True
    suf = "/" + t
    return any(p.endswith(suf) for p in paths)


h = json.load(open(sys.argv[1]))  # noqa: SIM115
now = datetime(2026, 10, 19, tzinfo=UTC)
by = collections.defaultdict(list)
for r in h:
    if (r["lines_head"] or 0) > 5:
        by[r["repo"]].append(r)
prim = [max(v, key=lambda r: r["commits"]) for v in by.values()]
prim = [r for r in prim if (now - datetime.fromisoformat(r["first_date"].replace("Z", "+00:00"))).days >= 183]
res, stale_items = [], []
for r in prim:
    c = gh(f"repos/{r['repo']}/contents/{r['file']}")
    if not c or "content" not in c:
        continue
    txt = base64.b64decode(c["content"]).decode("utf-8", "replace")
    toks = tokens(txt)
    head_sha = gh(f"repos/{r['repo']}/commits?per_page=1")[0]["sha"]
    th, trh = tree(r["repo"], head_sha)
    t0, tr0 = tree(r["repo"], r["first_sha"])
    if th is None or t0 is None:
        continue
    ok = [t for t in toks if resolves(t, th)]
    stale = [t for t in toks if not resolves(t, th) and resolves(t, t0)]
    unres = [t for t in toks if not resolves(t, th) and not resolves(t, t0)]
    res.append(
        {
            "repo": r["repo"],
            "file": r["file"],
            "commits": r["commits"],
            "first_date": r["first_date"][:10],
            "tokens": len(toks),
            "ok": len(ok),
            "stale": stale,
            "unresolved": unres,
            "truncated": trh or tr0,
        }
    )
    stale_items += [{"repo": r["repo"], "file": r["file"], "token": t} for t in stale]
    sys.stderr.write(".")
json.dump(res, open(sys.argv[2], "w"), indent=1)  # noqa: SIM115
random.seed(282)
sample_out = sys.argv[3] if len(sys.argv) > 3 else "stale_sample.json"
with open(sample_out, "w") as fh:
    json.dump(random.sample(stale_items, min(30, len(stale_items))), fh, indent=1)
n = len(res)
ns = sum(1 for x in res if x["stale"])
print(
    f"files {n}; with >=1 stale path ref {ns} ({ns / n:.1%}); tokens {sum(x['tokens'] for x in res)}; "
    f"stale refs {sum(len(x['stale']) for x in res)}; unresolved {sum(len(x['unresolved']) for x in res)}; truncated trees {sum(x['truncated'] for x in res)}"
)
