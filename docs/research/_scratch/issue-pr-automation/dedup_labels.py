"""Labelled duplicate/related issue pairs for the #346 dedup study, and the frozen DEV/TEST split.

Run from the worktree root:
    python3 docs/research/_scratch/issue-pr-automation/dedup_labels.py fetch   # read-only gh issue list
    uv run python docs/research/_scratch/issue-pr-automation/dedup_labels.py build   # writes dedup_labels.json

Raw issue text stays in .scratch/dedup/ (gitignored): repo-E is a private repository, so no repo-E title or
body may land in this public repo. dedup_labels.json holds issue numbers only.

Tiers (a pair is (query, target): the query is the later-filed issue, the target an earlier one):
  A  duplicate / superseded / folded-in, curated by hand from closure comments (CURATED below, each with the
     comment it rests on). Never used for tuning: it is held out whole.
  B  related: the query's body names the target as #N (same repo, an issue, filed earlier). Split DEV/TEST by
     a hash of the query, frozen before any method ran.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
RAW = ROOT / ".scratch" / "dedup"
OUT = Path(__file__).with_name("dedup_labels.json")
# repo-E is private and named by code here: to rerun (and to reproduce the frozen split, which hashes the real name),
# set REPO_E to its bare GitHub repository name, as in harness-efficiency/review_convergence.py
OWNER = "Marius-Juston"
REPO_E = os.environ.get("REPO_E", "repo-E")
SLUG = {"agent-harness": f"{OWNER}/agent-harness", "repo-E": f"{OWNER}/{REPO_E}"}
REPOS = list(SLUG)
FIELDS = "number,title,body,state,stateReason,createdAt,closedAt,author,labels,comments"

# (repo, duplicate-or-folded issue, kept issue, evidence: where the relation is stated)
CURATED = [
    ("agent-harness", 37, 240, "#37 closing comment: 'Duplicate of #240'"),
    ("agent-harness", 10, 104, "#10 closing comment: 'Duplicate of #104'"),
    ("agent-harness", 10, 74, "PR for #74: 'Supersedes #10 except its cron-comment and board new parts'"),
    ("agent-harness", 308, 338, "#308 closing comment: 'Folded into #338'"),
    ("agent-harness", 275, 338, "#275 closing comment: 'Folded into #338'"),
    ("agent-harness", 66, 255, "#66 comment: 'Implementation is folded into #255'"),
    ("repo-E", 661, 647, "#661 closing comment: 'Duplicate of #647'"),
    ("repo-E", 658, 647, "#658 closing comment: 'Duplicate of #647'"),
    ("repo-E", 658, 661, "same bucket: both duplicates of #647"),
    ("repo-E", 349, 341, "#349 closing comment: 'Duplicate of #341 (same test)'"),
    ("repo-E", 535, 565, "PR for #565: 'Closes #535 (folded in ...)'"),
    ("repo-E", 57, 36, "#57 closing comment: 'Covered by #36'"),
]

SECTION = re.compile(r"^## (.+?)\s*$", re.M)
# #N not preceded by a word char, '/', or '-' (excludes owner/repo#N cross-repo refs and anchors)
REF = re.compile(r"(?<![\w/#-])#(\d+)\b")


def sections(body: str) -> dict[str, str]:
    body = body or ""
    out: dict[str, str] = {}
    marks = list(SECTION.finditer(body))
    for k, m in enumerate(marks):
        end = marks[k + 1].start() if k + 1 < len(marks) else len(body)
        out[m.group(1).strip()] = body[m.end() : end].strip()
    return out


def load(repo: str) -> dict[int, dict]:
    return {i["number"]: i for i in json.loads((RAW / f"{repo}.json").read_text())}


def fetch() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for repo in REPOS:
        out = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "-R",
                SLUG[repo],
                "--state",
                "all",
                "--limit",
                "3000",
                "--json",
                FIELDS,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        (RAW / f"{repo}.json").write_text(out)
        print(repo, len(json.loads(out)), "issues")


def is_test(repo: str, q: int) -> bool:
    # keyed on the repository's own name, as frozen on 2026-10-20 before any method ran
    return hashlib.sha256(f"{SLUG[repo].split('/')[1]}#{q}".encode()).digest()[0] % 10 < 3


def human_refs(body: str | None) -> str:
    """The body less the Refs line `board new` pre-fills (#350): that line is the ranker's own guess, not a label."""
    from devkit.board import RELATED_NOTE

    out, section = [], ""
    for line in (body or "").splitlines():
        if m := SECTION.match(line):
            section = m.group(1).strip()
        if not (section == "Refs" and RELATED_NOTE in line):
            out.append(line)
    return "\n".join(out)


def build() -> None:
    labels: dict = {"split_rule": "TEST iff sha256('<repo name>#<query>')[0] % 10 < 3", "repos": {}}
    for repo in REPOS:
        iss = load(repo)
        created = {n: i["createdAt"] for n, i in iss.items()}
        tier_a = []
        for r, x, y, ev in CURATED:
            if r != repo:
                continue
            q, t = (x, y) if created[x] > created[y] else (y, x)
            tier_a.append({"query": q, "target": t, "evidence": ev})
        tier_b = []
        for n, i in iss.items():
            for t in sorted({int(m) for m in REF.findall(human_refs(i["body"]))}):
                if t in iss and t != n and created[t] < created[n]:
                    tier_b.append({"query": n, "target": t, "split": "TEST" if is_test(repo, n) else "DEV"})
        labels["repos"][repo] = {
            "n_issues": len(iss),
            "tier_a": tier_a,
            "tier_b": sorted(tier_b, key=lambda p: (p["query"], p["target"])),
        }
        qs = {p["query"] for p in tier_b}
        print(
            repo,
            "issues",
            len(iss),
            "tierA pairs",
            len(tier_a),
            "tierB pairs",
            len(tier_b),
            "tierB queries",
            len(qs),
            "TEST queries",
            sum(is_test(repo, q) for q in qs),
        )
    text = json.dumps(labels, indent=1, sort_keys=True)
    OUT.write_text(text + "\n")
    print("wrote", OUT.name, "sha256", hashlib.sha256((text + "\n").encode()).hexdigest()[:16])


if __name__ == "__main__":
    {"fetch": fetch, "build": build}[sys.argv[1]]()
