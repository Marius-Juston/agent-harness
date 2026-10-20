"""Replay merged PR history with `git merge-tree` to measure conflicts among concurrent PRs (#346, sub-question d).

Usage (from the worktree root, so `devkit` imports):
    uv run python docs/research/_scratch/issue-pr-automation/conflicts_replay.py DATA.json REPO.git [--no-driver]

DATA.json is conflicts_fetch.py output; REPO.git a scratch bare clone with main and refs/pull/*/head fetched. The script
writes only into REPO.git (merge-tree trees and commit-tree commits for "B brought up to main") and prints counts.

Definitions (all on merged PRs into main):
- S_A: A's merge commit on main (squash or merge); P_A = S_A^1, main just before A landed; t_A = A.mergedAt.
- h_B: B's head when it was handed back: the latest PR commit with committedDate <= B.createdAt (`dev pr` time).
- At risk: an ordered pair (A, B) with t_A < t_B and S_A not an ancestor of h_B, i.e. A landed on main while B was
  being built or reviewed and B's hand-back did not contain it. Integrate has to merge B over A.
- Truth: B brought up to P_A (merge h_B with P_A; a conflict there is an earlier PR's, and the pair is set aside),
  then merged with S_A. A conflict in that second merge is A's and B's. Merge attributes come from the main commit
  (`--attr-source`) and the generated-file driver is the one integrate defines (keep one side), unless --no-driver.
- B-level truth: h_B merged with P_B (main just before B landed) conflicts: what integrate met on B's hand-back head.
Predictors, each judged against the pairwise truth:
- owns: the Owns of A's and B's issues overlap (devkit.board.overlap), as `board wave` uses; known at claim time.
- files: A's and B's net changed files intersect, leaving out union and generated paths (as `board brief` hotspots).
- pr-check: a merge-tree pre-check at the later of the two `dev pr` calls. When A was already on main at B's
  `dev pr`, it is h_B against main then; else both heads at that time, each brought up to main then, merged.
- earliest: the first time (any commit of A or B, after both had one, before t_A) when the two heads, each brought up
  to main then, conflict: a continuous pre-check over local worktrees and PR heads.
"""

import bisect
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from types import SimpleNamespace

from devkit.board import overlap, owns_of

ENV = {**os.environ, **{k: "replay" for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME")}}
ENV |= {"GIT_AUTHOR_EMAIL": "r@x", "GIT_COMMITTER_EMAIL": "r@x", "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z"}
ENV |= {"GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"}
DRIVER = ["-c", "merge.devkit-generated.driver=true", "-c", "merge.devkit-generated.name=keep"]


def ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


class Repo:
    def __init__(self, path: str, driver: bool):
        self.path, self.driver = path, driver
        self.merges: dict[tuple, tuple] = {}
        self.ups: dict[tuple, str | None] = {}

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run(["git", "-C", self.path, *args], capture_output=True, text=True, env=ENV, check=False)
        if check and r.returncode not in (0, 1):
            raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
        return r

    def merge(self, x: str, y: str, attr: str) -> tuple[str, list[str]]:
        """(tree, conflicted paths) of merging commits x and y."""
        key = (x, y, attr)
        if key not in self.merges:
            pre = [f"--attr-source={attr}", *(DRIVER if self.driver else [])]
            r = subprocess.run(
                ["git", "-C", self.path, *pre, "merge-tree", "--write-tree", "--name-only", "--no-messages", x, y],
                capture_output=True, text=True, env=ENV, check=False,
            )  # fmt: skip
            if r.returncode not in (0, 1):
                raise RuntimeError(f"merge-tree {x} {y}: {r.stderr.strip()}")
            lines = r.stdout.splitlines()
            self.merges[key] = (lines[0], sorted(set(lines[1:])) if r.returncode == 1 else [])
        return self.merges[key]

    def up(self, head: str, main: str) -> str | None:
        """`head` brought up to `main` as a commit (head itself when it contains main), or None on a conflict."""
        key = (head, main)
        if key not in self.ups:
            if self.ancestor(main, head):
                self.ups[key] = head
            elif self.ancestor(head, main):
                self.ups[key] = main
            else:
                tree, paths = self.merge(head, main, main)
                self.ups[key] = (
                    None if paths else self.git("commit-tree", tree, "-p", head, "-p", main, "-m", "up").stdout.strip()
                )
        return self.ups[key]

    def ancestor(self, a: str, b: str) -> bool:
        return self.git("merge-base", "--is-ancestor", a, b, check=False).returncode == 0

    def names(self, a: str, b: str) -> list[str]:
        return self.git("diff", "--name-only", "--no-renames", a, b).stdout.split()

    def special(self, paths: list[str], attr: str) -> set[str]:
        """Paths whose merge attribute is union or the generated driver: they never conflict."""
        if not paths:
            return set()
        r = subprocess.run(
            ["git", "-C", self.path, "check-attr", f"--source={attr}", "--stdin", "merge"],
            input="\n".join(paths), capture_output=True, text=True, check=True,
        )  # fmt: skip
        out = set()
        for line in r.stdout.splitlines():
            path, _, value = line.rsplit(": ", 2)
            if value in ("union", "devkit-generated"):
                out.add(path)
        return out


def load(data_path: str, repo: Repo):
    with open(data_path) as f:
        d = json.load(f)
    issues = {i["number"]: i for i in d["issues"]}
    main_log = [
        line.split() for line in repo.git("log", "--first-parent", "--format=%H %cI", "main").stdout.splitlines()
    ][::-1]
    main_t = [ts(t) for _, t in main_log]
    main_h = [h for h, _ in main_log]
    on_main = set(main_h)
    prs = []
    for p in d["prs"]:
        if not p["mergedAt"] or p["baseRefName"] != "main" or not p["mergeCommit"]:
            continue
        s = p["mergeCommit"]["oid"]
        if s not in on_main:
            continue
        commits = sorted(
            ((ts(c["commit"]["committedDate"]), c["commit"]["oid"]) for c in p["commits"]["nodes"]), key=lambda x: x[0]
        )
        nums = [n["number"] for n in p["closingIssuesReferences"]["nodes"]]
        if m := re.match(r"(\d+)-", p["headRefName"]):
            nums.append(int(m[1]))
        owns = [o for n in dict.fromkeys(nums) if n in issues for o in owns_of(issue_ns(issues[n]))]
        claims = [ts(c) for n in nums if n in issues for c in issues[n]["claims"]]
        created = ts(p["createdAt"])
        opened = [c for c in commits if c[0] <= created + 60] or commits[:1]
        parent = repo.git("rev-parse", s + "^1").stdout.strip()
        prs.append(
            SimpleNamespace(
                n=p["number"],
                created=created,
                merged=ts(p["mergedAt"]),
                s=s,
                p=parent,
                commits=commits,
                h=opened[-1][1],
                owns=owns,
                has_issue=bool(nums),
                claim=min(claims) if claims else None,
                comments=p["comments"]["nodes"],
            )
        )
    prs.sort(key=lambda x: x.merged)
    return prs, main_t, main_h


def issue_ns(i: dict) -> SimpleNamespace:
    from devkit.board import sections

    return SimpleNamespace(parts=sections(i["body"] or ""))


def head_at(pr, t: float) -> str | None:
    i = bisect.bisect_right([c[0] for c in pr.commits], t)
    return pr.commits[i - 1][1] if i else None


def main_at(main_t, main_h, t: float) -> str:
    return main_h[max(0, bisect.bisect_right(main_t, t) - 1)]


def prf(rows: list[tuple[bool, bool]]) -> dict:
    tp = sum(1 for t, p in rows if t and p)
    fp = sum(1 for t, p in rows if not t and p)
    fn = sum(1 for t, p in rows if t and not p)
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": len(rows) - tp - fp - fn,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "recall": round(tp / (tp + fn), 3) if tp + fn else None,
    }  # fmt: skip


def run(data_path: str, repo_path: str, driver: bool) -> dict:
    repo = Repo(repo_path, driver)
    prs, main_t, main_h = load(data_path, repo)
    out: dict = {"merged_prs": len(prs)}
    pairs, pre_conflicted = [], 0
    for bi, b in enumerate(prs):
        for a in prs[:bi]:
            if repo.ancestor(a.s, b.h):
                continue
            bm = repo.up(b.h, a.p)
            if bm is None:
                pre_conflicted += 1
                continue
            paths = repo.merge(bm, a.s, a.s)[1]
            pairs.append(SimpleNamespace(a=a, b=b, paths=paths))
    out["at_risk_pairs"] = len(pairs) + pre_conflicted
    out["pairs_set_aside_b_conflicted_before_a"] = pre_conflicted
    out["pairs_judged"] = len(pairs)
    out["conflicting_pairs"] = sum(1 for x in pairs if x.paths)
    both_open = [x for x in pairs if x.b.created < x.a.merged]
    out["pairs_both_prs_open"] = len(both_open)
    out["conflicting_pairs_both_prs_open"] = sum(1 for x in both_open if x.paths)
    out["prs_with_a_conflicting_pair"] = len({x.b.n for x in pairs if x.paths})

    # B-level truth and integrate's own send-back comments
    sent = {b.n for b in prs if any("conflicts with main" in (c["body"] or "") for c in b.comments)}
    b_truth = {b.n for b in prs if not repo.ancestor(b.p, b.h) and repo.merge(b.h, b.p, b.p)[1]}
    out["prs_whose_handback_head_conflicts_with_main_at_merge"] = len(b_truth)
    out["prs_sent_back_by_integrate"] = len(sent)
    out["sent_back_and_conflicting_handback"] = len(sent & b_truth)
    out["sent_back_with_a_conflicting_pair"] = len(sent & {x.b.n for x in pairs if x.paths})

    # predictors
    rows = {"owns": [], "owns_both_declared": [], "files": [], "pr_check": [], "earliest": []}
    lead_pr, lead_early, lead_claim, before_handback = [], [], [], 0
    owns_declared = 0
    path_counts: Counter = Counter()
    detail = []
    for x in pairs:
        a, b, truth = x.a, x.b, bool(x.paths)
        path_counts.update(x.paths)
        rows["owns"].append((truth, overlap(a.owns, b.owns)))
        if a.owns and b.owns:
            owns_declared += 1
            rows["owns_both_declared"].append((truth, overlap(a.owns, b.owns)))
        fa, fb = repo.names(a.p, a.s), repo.names(b.p, b.s)
        skip = repo.special(sorted(set(fa) | set(fb)), a.s)
        rows["files"].append((truth, bool((set(fa) & set(fb)) - skip)))
        # pr-check at the later dev pr
        t0 = max(a.created, b.created)
        if t0 >= a.merged:  # A on main at B's dev pr: B's head against main then
            m0 = main_at(main_t, main_h, b.created)
            flag = repo.up(b.h, m0) is None
        else:
            m0 = main_at(main_t, main_h, t0)
            ha, hb = head_at(a, t0) or a.commits[0][1], head_at(b, t0) or b.h
            ua, ub = repo.up(ha, m0), repo.up(hb, m0)
            flag = ua is not None and ub is not None and bool(repo.merge(ua, ub, m0)[1])
        rows["pr_check"].append((truth, flag))
        if flag and truth:
            lead_pr.append((a.merged - t0) / 3600 if t0 < a.merged else 0.0)
        # earliest continuous flag, while both in flight
        start = max(a.commits[0][0], b.commits[0][0])
        times = sorted({t for t, _ in a.commits + b.commits if start <= t < a.merged})
        first = None
        for t in times:
            ha, hb, m = head_at(a, t), head_at(b, t), main_at(main_t, main_h, t)
            ua, ub = repo.up(ha, m), repo.up(hb, m)
            if ua and ub and repo.merge(ua, ub, m)[1]:
                first = t
                break
        if first is None and a.merged <= b.created:  # B still building when A landed: the dev pr check covers it
            first = b.created if flag else None
        rows["earliest"].append((truth, first is not None))
        if first is not None and truth:
            lead_early.append((a.merged - first) / 3600 if first < a.merged else 0.0)
            before_handback += first < b.created
        if truth or flag or first is not None:
            h = 3600
            detail.append(
                {
                    "a": a.n,
                    "b": b.n,
                    "conflict": truth,
                    "owns": overlap(a.owns, b.owns),
                    "pr_check": flag,
                    "earliest_flag_h_before_a_merged": None if first is None else round((a.merged - first) / h, 2),
                    "b_opened_h_before_a_merged": round((a.merged - b.created) / h, 2),
                    "b_merged_h_after_a_merged": round((b.merged - a.merged) / h, 2),
                    "b_sent_back": b.n in sent,
                    "send_back_min_after_a_merged": [
                        round((ts(c["createdAt"]) - a.merged) / 60)
                        for c in b.comments
                        if "conflicts with main" in (c["body"] or "")
                    ],
                    "paths": x.paths,
                }
            )
        if truth and overlap(a.owns, b.owns) and a.claim and b.claim:
            lead_claim.append((a.merged - max(a.claim, b.claim)) / 3600)
    out["pairs_with_both_owns_declared"] = owns_declared
    # PR level: B is flagged when any of its at-risk pairs is; truth is any conflicting pair
    truth_b = {x.b.n for x in pairs if x.paths}
    judged_b = {x.b.n for x in pairs}
    out["pr_level"] = {
        k: prf([(n in truth_b, n in {x.b.n for x, (_, f) in zip(pairs, v, strict=True) if f}) for n in judged_b])
        for k, v in rows.items()
        if k != "owns_both_declared"
    }
    out["prs_judged"] = len(judged_b)
    out["predictors"] = {k: prf(v) for k, v in rows.items()}

    def med(v):
        v = sorted(v)
        return round(v[len(v) // 2], 2) if v else None

    out["lead_hours_median"] = {"pr_check": med(lead_pr), "earliest": med(lead_early), "owns_at_claim": med(lead_claim)}
    out["earliest_flags_before_b_handback"] = before_handback
    out["conflicted_path_top_dirs"] = Counter(p.split("/")[0] for p in path_counts.elements()).most_common(8)
    out["conflicted_paths"] = path_counts.most_common(10)
    cycle = {k: sorted((b.merged - b.created) / 3600 for b in prs if (b.n in b_truth) == k) for k in (True, False)}
    out["b_open_to_merged_hours_median"] = {"conflicting": med(cycle[True]), "clean": med(cycle[False])}
    lock = {"uv.lock", "pyproject.toml"}
    out["conflicting_pairs_only_uv_lock"] = sum(1 for x in pairs if x.paths and set(x.paths) <= {"uv.lock"})
    out["conflicting_pairs_only_lock_or_pyproject"] = sum(1 for x in pairs if x.paths and set(x.paths) <= lock)
    out["conflicting_pairs_touching_uv_lock"] = sum(1 for x in pairs if "uv.lock" in x.paths)
    out["pairs"] = detail
    return out


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], sys.argv[2], "--no-driver" not in sys.argv), indent=1))
