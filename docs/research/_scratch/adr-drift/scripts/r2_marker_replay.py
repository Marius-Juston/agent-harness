"""R2b check 7: replay an `enforced-by: <path>: "<literal>"` presence check over every first-parent commit.

usage: python3 r2_marker_replay.py
Marker sets (each tracked from the first commit where it is present in its path):
  A  the 18 recorded decisions of r1_adr_decisions.py, with the short marker r1 chose (chosen at HEAD, so a marker
     can only show a delete-and-restore; this set is biased towards zero false alarms)
  B  the same decisions, marker = the whole sentence holding A's marker at the commit where it first appeared
     (what an author writing the rule sentence as the marker would have had; rewording shows up here)
  C  the C1 reversal: researcher.md's rule ("never call those APIs through WebFetch") and its reason
     ("shared, cached, rate-limited client"), tracked from their first appearance
  T  project B: every sentence that links docs/adr/ in a tracked .md, from its first appearance
An event = present at commit k-1, absent at k. Printed with: whether the literal is elsewhere in the tree at k
(moved), and whether and when it comes back. Classify by hand: TRUE (the rule or reason was really removed) or FP.
"""

import functools
import re
import subprocess

import r2_common as c
from r1_adr_decisions import D


@functools.cache
def show(repo, sha, path):
    p = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{path}"], capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def elsewhere(repo, sha, lit):
    p = subprocess.run(["git", "-C", str(repo), "grep", "-l", "-F", lit, sha], capture_output=True, text=True)
    return [x.split(":", 1)[1] for x in p.stdout.split()]


def norm(s):
    return re.sub(r"\s+", " ", s)


def sentence(text, lit, path=""):
    if path.endswith(".py"):  # code: the source line holding the literal
        return next(norm(ln).strip() for ln in text.splitlines() if lit in norm(ln))
    t = norm(text)
    i = t.find(lit)
    a = max(t.rfind(". ", 0, i), t.rfind("\n", 0, i), t.rfind("- ", 0, i))
    b = t.find(". ", i + len(lit))
    return t[a + 2 if a >= 0 else 0 : b + 1 if b >= 0 else len(t)].strip()


def track(name, path, lit, label):
    repo = c.REPOS[name][0]
    hist = c.history(name)
    state, first, events = None, None, []
    for k, (sha, _, subj) in enumerate(hist):
        txt = show(repo, sha, path)
        here = txt is not None and lit in norm(txt)
        if here and first is None:
            first = k
        if first is not None and state and not here:
            events.append([k, sha[:9], subj[:60], elsewhere(repo, sha, lit[:80]) if lit else [], None])
        if first is not None and here and state is False and events:
            events[-1][4] = k
        state = here if first is not None else None
    return first, events, len(hist)


def report(set_name, rows):
    n_ev = 0
    print(f"== set {set_name}: {len(rows)} markers")
    for name, path, lit, label in rows:
        first, events, n = track(name, path, lit, label)  # noqa: RUF059 - research script, kept as run
        if first is None:
            print(f"   {label}: never present in {path}")
            continue
        n_ev += len(events)
        for k, sha, subj, moved, back in events:
            print(
                f"   {label} {path}: GONE at {k} {sha} (back at {back}) moved-to={moved[:2]} | {subj}\n      was: «{lit[:230]}»"
            )
            txt = show(c.REPOS[name][0], sha, path)
            if txt:
                import difflib

                cands = re.split(r"(?<=[.!?])\s+|\n", txt)
                best = max(cands, key=lambda x: difflib.SequenceMatcher(None, norm(x), lit).ratio())
                print(f"      now: «{norm(best)[:230]}»")
            else:
                print("      now: file gone")
    print(f"   set {set_name}: {n_ev} disappearance events")


A, B = [], []
for d in D:
    if d[5] is None:
        continue
    path, lit = d[5]
    A.append(("AH", path, lit, d[0]))
    first, _, _ = track("AH", path, lit, d[0])
    sha = c.history("AH")[first][0]
    B.append(("AH", path, sentence(show(c.AH, sha, path), lit, path), d[0] + "s"))
report("A", A)
report("B", B)
report(
    "C",
    [
        ("AH", "plugins/harness-ops/agents/researcher.md", "never call those APIs through WebFetch", "C1-rule"),
        ("AH", "plugins/harness-ops/agents/researcher.md", "shared, cached, rate-limited client", "C1-reason"),
    ],
)
T, seen = [], set()
for sha, _, _ in c.history("PB"):
    for f in c.files("PB", sha):
        if not f.endswith(".md") or f.startswith("docs/adr/") or f.startswith("docs/generated/") or "/_scratch/" in f:
            continue
        txt = show(c.PB, sha, f)
        if not txt or "adr/0" not in txt:
            continue
        for m in re.finditer(r"adr/0\d{3}", norm(txt)):
            s = sentence(txt, norm(txt)[m.start() : m.end()])
            if (f, s) not in seen and len(s) > 20:
                seen.add((f, s))
                T.append(("PB", f, s, f"T{len(T) + 1}"))
# keep only the first-appearing version of each (file, ADR number) pair, so rewrites count as events, not new markers
firsts = {}
for row in T:
    key = (row[1], tuple(sorted(set(re.findall(r"adr/(0\d{3})", row[2])))))
    firsts.setdefault(key, row)
report("T", list(firsts.values()))
