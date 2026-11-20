#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["networkx==3.5"]
# ///
"""Score the candidate metrics against this repository's history (#496). Reads ``.scratch/arch_history.json`` from
history.py and prints four tables:

1. workflow.py timeline: its value and its rank among devkit modules for each metric, at selected commits;
2. lead time: the first commit from which each metric ranks workflow.py first (and keeps it first until the
   review that split it, commit index 144) and the first commit each candidate absolute threshold fires;
3. precision at the two reviews: each metric's top 5 modules, marked with the modules the reviews refactored;
4. forward validity: Spearman correlation, across modules, between each metric at a snapshot and the bug-fix
   commits touching the module in the next 30 first-parent commits, raw and with size partialled out.

Run from the worktree root: ``uv run docs/research/_scratch/architecture-grading/scripts/analyse.py``.
"""

import ast
import json
import math
import subprocess
from pathlib import Path

import networkx as nx

D = json.loads(Path(".scratch/arch_history.json").read_text())
R, B = D["rows"], D["blobs"]
W = 30  # churn window and outcome window, first-parent commits
TARGET = "devkit/workflow.py"
REVIEW2 = 144  # b8e2e49, the commit consolidation review 2 (#441) measured
REVIEW1 = 37  # 3725af6, the merge of review 1 (#138), measured on its branch at 839c572
LABELS = {  # modules each review turned into refactor-only step issues
    REVIEW1: {"devkit/docs.py", "devkit/citecheck.py", "devkit/lit.py"},  # #140 cycle, #139 shared helpers
    REVIEW2: {"devkit/workflow.py", "devkit/checks.py", "devkit/stats.py"},  # #442-#448 split, #449 cycle, #450 readers
}


class Blobs:
    def __init__(self) -> None:
        self.p = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.cache: dict[str, str] = {}

    def text(self, sha: str) -> str:
        if sha not in self.cache:
            self.p.stdin.write(sha.encode() + b"\n")
            self.p.stdin.flush()
            size = int(self.p.stdout.readline().split()[2])
            self.cache[sha] = self.p.stdout.read(size).decode("utf-8", "replace")
            self.p.stdout.read(1)
        return self.cache[sha]


BLOBS = Blobs()
_comm: dict[str, tuple[int, float]] = {}


def communities(blob: str) -> tuple[int, float]:
    """Louvain communities of the top-level definition graph (reference edges, shared module-level names, a hub
    definition's edges down-weighted by its degree): (number of communities of at least 100 LOC, modularity)."""
    if blob in _comm:
        return _comm[blob]
    src = BLOBS.text(blob)
    tree = ast.parse(src)
    lines = src.splitlines()
    defs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    glob = {
        n.id
        for st in tree.body
        if isinstance(st, (ast.Assign, ast.AnnAssign))
        for t in (st.targets if isinstance(st, ast.Assign) else [st.target])
        for n in ast.walk(t)
        if isinstance(n, ast.Name)
    }
    g = nx.Graph()
    size = {}
    for name, node in defs.items():
        body = lines[node.lineno - 1 : node.end_lineno]
        size[name] = sum(1 for ln in body if ln.strip() and not ln.strip().startswith("#"))
        g.add_node(name)
    users: dict[str, set[str]] = {}
    for name, node in defs.items():
        refs = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        refs |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        for r in refs:
            if r in defs and r != name:
                g.add_edge(name, r)
            elif r in glob:
                users.setdefault(r, set()).add(name)
    for names in users.values():
        names = sorted(names)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                w = g.get_edge_data(a, b, {}).get("weight", 0)
                g.add_edge(a, b, weight=w + 1 / len(names))
    for a, b in g.edges:
        g[a][b].setdefault("weight", 1.0)
        g[a][b]["weight"] /= math.sqrt(max(g.degree(a), 1) * max(g.degree(b), 1))
    if g.number_of_edges() == 0:
        _comm[blob] = (sum(1 for s in size.values() if s >= 100), 0.0)
        return _comm[blob]
    parts = nx.community.louvain_communities(g, weight="weight", seed=7)
    big = sum(1 for p in parts if sum(size[n] for n in p) >= 100)
    q = nx.community.modularity(g, parts, weight="weight")
    _comm[blob] = (big, round(q, 3))
    return _comm[blob]


def churn(i: int, path: str, w: int = W) -> int:
    return sum(1 for r in R[max(0, i - w + 1) : i + 1] if path in r["changed"])


def churn_all(i: int, path: str) -> int:
    return sum(1 for r in R[: i + 1] if path in r["changed"])


def future_bugs(i: int, path: str, w: int = W) -> int:
    return sum(1 for r in R[i + 1 : i + 1 + w] if r["bug"] and path in r["changed"])


def dispersion(i: int, path: str, k: int = 6) -> float:
    """Share of pairs among the last k commits touching ``path`` whose changed top-level definitions are disjoint,
    ignoring definitions that more than half of those commits touched (orchestration hubs)."""
    sets = [set(r["touched_defs"].get(path, [])) for r in R[: i + 1] if path in r["changed"]][-k:]
    sets = [s for s in sets if s]
    if len(sets) < 3:
        return 0.0
    count: dict[str, int] = {}
    for s in sets:
        for d in s:
            count[d] = count.get(d, 0) + 1
    hubs = {d for d, c in count.items() if c > len(sets) / 2}
    sets = [s - hubs for s in sets]
    pairs = [(a, b) for x, a in enumerate(sets) for b in sets[x + 1 :]]
    return round(sum(1 for a, b in pairs if not (a & b)) / len(pairs), 2)


def metrics(i: int) -> dict[str, dict[str, float]]:
    files = R[i]["files"]
    fan_in: dict[str, int] = {}
    for blob in files.values():
        for t in set(B[blob].get("imports", [])) | set(B[blob].get("fn_imports", [])):
            tp = t.replace(".", "/") + ".py"
            fan_in[tp] = fan_in.get(tp, 0) + 1
    out = {}
    for p, blob in files.items():
        m = B[blob]
        ce = len(m["imports"]) + len(m["fn_imports"])
        ca = fan_in.get(p, 0)
        ch = churn(i, p)
        big, q = communities(blob)
        out[p] = {
            "loc": m["loc"],
            "cc_sum": m["cc_sum"],
            "cc_max": m["cc_max"],
            "cog_sum": m["cog_sum"],
            "cog_max": m["cog_max"],
            "cog_over15": m["cog_over15"],
            "fan_out": ce,
            "fan_in": ca,
            "instability": round(ce / (ca + ce), 2) if ca + ce else 0.0,
            "fn_imports": len(m["fn_imports"]),
            "churn30": ch,
            "churn_all": churn_all(i, p),
            "hotspot_cog": ch * m["cog_sum"],
            "hotspot_loc": ch * m["loc"],
            "communities": big,
            "modularity": q,
            "dispersion": dispersion(i, p),
        }
    return out


NAMES = [
    "loc",
    "cc_sum",
    "cc_max",
    "cog_sum",
    "cog_max",
    "cog_over15",
    "fan_out",
    "fan_in",
    "instability",
    "fn_imports",
    "churn30",
    "churn_all",
    "hotspot_cog",
    "hotspot_loc",
    "communities",
    "modularity",
    "dispersion",
]


def rank(ms: dict, name: str, path: str) -> int:
    v = ms[path][name]
    return 1 + sum(1 for q in ms.values() if q[name] > v)


def spearman(x: list[float], y: list[float]) -> float:
    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda k: v[k])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r

    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return sxy / (sx * sy) if sx and sy else float("nan")


def partial(rxy: float, rxz: float, ryz: float) -> float:
    d = math.sqrt((1 - rxz**2) * (1 - ryz**2))
    return (rxy - rxz * ryz) / d if d else float("nan")


def main() -> None:
    all_m = {i: metrics(i) for i in range(len(R))}

    print("## 1. workflow.py timeline (value / rank among devkit modules)")
    cols = ["loc", "cog_sum", "churn30", "hotspot_cog", "fan_out", "instability", "communities", "dispersion"]
    print("idx date       n_mod " + " ".join(f"{c:>15}" for c in cols))
    for i in [1, 22, 37, 60, 84, 101, 112, 131, 144, 164]:
        ms = all_m[i]
        if TARGET not in ms:
            continue
        cells = [f"{ms[TARGET][c]}/{rank(ms, c, TARGET)}" for c in cols]
        print(f"{i:>3} {R[i]['date'][:10]} {len(ms):>5} " + " ".join(f"{c:>15}" for c in cells))

    print("\n## 2. lead time: first index from which workflow.py ranks 1st without a break up to index 144")
    for name in NAMES:
        first = None
        for i in range(REVIEW2, -1, -1):
            if TARGET in all_m[i] and rank(all_m[i], name, TARGET) == 1:
                first = i
            else:
                break
        when = (
            f"{first} ({R[first]['date'][:10]}), {REVIEW2 - first} commits before review 2"
            if first is not None
            else "never"
        )
        print(f"  {name:12} {when}")
    thresholds = {
        "loc > 1000": lambda m: m["loc"] > 1000,
        "loc > 500": lambda m: m["loc"] > 500,
        "cog_sum > 300": lambda m: m["cog_sum"] > 300,
        "cog_max > 25": lambda m: m["cog_max"] > 25,
        "churny (loc>1000, 30+ fp commits)": lambda m: m["loc"] > 1000 and m["churn_all"] >= 30,
        "churny-fp (loc>1000, 15+ fp commits)": lambda m: m["loc"] > 1000 and m["churn_all"] >= 15,
        "communities >= 3": lambda m: m["communities"] >= 3,
        "dispersion >= 0.8": lambda m: m["dispersion"] >= 0.8,
        "fan_out >= 8": lambda m: m["fan_out"] >= 8,
    }
    print(
        "\n   first index each absolute threshold fires on workflow.py, and how many other modules it flags then / at 144"
    )
    for label, f in thresholds.items():
        first = next((i for i in range(len(R)) if TARGET in all_m[i] and f(all_m[i][TARGET])), None)
        if first is None:
            print(f"  {label:38} never")
            continue
        others_then = sorted(p for p, m in all_m[first].items() if p != TARGET and f(m))
        others_144 = sorted(p for p, m in all_m[REVIEW2].items() if p != TARGET and f(m))
        print(
            f"  {label:38} {first} ({R[first]['date'][:10]}); others then {len(others_then)}; "
            f"others at 144: {len(others_144)} {[p.split('/')[-1] for p in others_144]}"
        )

    print("\n## 3. top 5 per metric at the reviews (* = a module the review refactored)")
    for rv in (REVIEW1, REVIEW2):
        ms = all_m[rv]
        print(f" review at index {rv} ({R[rv]['date'][:10]}, {len(ms)} modules)")
        for name in NAMES:
            top = sorted(ms, key=lambda p: -ms[p][name])[:5]
            hits = sum(p in LABELS[rv] for p in top)
            cells = [("*" if p in LABELS[rv] else "") + p.split("/")[-1][:-3] + f"={ms[p][name]}" for p in top]
            print(f"  {name:12} hits {hits}/3  " + ", ".join(cells))

    print(f"\n## 4. forward validity: Spearman(metric at snapshot, bug-fix commits touching module in next {W})")
    snaps = [40, 60, 80, 100, 120]
    print("  snapshot: modules, modules with >=1 future bug fix, total bug-fix touches")
    for s in snaps:
        ys = [future_bugs(s, p) for p in all_m[s]]
        print(f"   {s}: {len(ys)}, {sum(1 for y in ys if y)}, {sum(ys)}")
    print(f"  {'metric':12} " + " ".join(f"{s:>12}" for s in snaps) + "   median raw / median size-partialled")
    for name in NAMES:
        raw, par = [], []
        for s in snaps:
            ms = all_m[s]
            ps = sorted(ms)
            x = [ms[p][name] for p in ps]
            y = [future_bugs(s, p) for p in ps]
            z = [ms[p]["loc"] for p in ps]
            rxy = spearman(x, y)
            raw.append(rxy)
            par.append(partial(rxy, spearman(x, z), spearman(y, z)) if name != "loc" else float("nan"))
        cells = [f"{a:+.2f}/{b:+.2f}" for a, b in zip(raw, par, strict=True)]
        med = sorted(raw)[len(raw) // 2]
        pm = sorted(par)[len(par) // 2]
        print(f"  {name:12} " + " ".join(f"{c:>12}" for c in cells) + f"   {med:+.2f} / {pm:+.2f}")


if __name__ == "__main__":
    main()
