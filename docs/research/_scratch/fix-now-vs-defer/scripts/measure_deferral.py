"""Measure deferral: work items agents filed during other work, and what finishing them later cost (#149).

Inputs, never committed: `.scratch/repos.md` maps the code names (repo-L, repo-R, repo-E, H) to local checkouts;
Claude Code transcripts under ~/.claude/projects (main sessions and subagents/). GitHub data is read with `gh`
(read-only) and cached under `.scratch/fix-now-vs-defer/`; the file-board repository is read from its git history.

Outputs go to `.scratch/fix-now-vs-defer/` (gitignored): items.csv (one row per filed item), quotes.txt (the
wording around each filing) and the summary printed on stdout, which uses code names only.

Usage (stdlib only, run from the repository root):
    python3 docs/research/_scratch/fix-now-vs-defer/scripts/measure_deferral.py [--refresh] [--max-lines 30]
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import fnmatch
import glob
import json
import re
import statistics
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
OUT = ROOT / ".scratch" / "fix-now-vs-defer"
PROJECTS = Path.home() / ".claude" / "projects"
NOW = dt.datetime.now(dt.UTC)

# Files whose churn is bookkeeping, not the fix: generated docs, locks, handoffs, logs, the file board, stats.
BOOKKEEPING = re.compile(
    r"^(docs/generated/|docs/research/INDEX\.md$|uv\.lock$|.*\.lock$|HANDOFF\.md$|docs/HANDOFF\.md$|docs/log/|"
    r"board/|\.devkit/.*\.jsonl?$|docs/research/_scratch/)"
)
# A created issue is printed alone on its line (gh issue create), optionally with its board status (board new).
ISSUE_URL = re.compile(r"^\s*https://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)(?:\s+\([\w -]+\))?\s*$", re.M)
TICKET_CREATED = re.compile(r"Created board/\w+/(T-\d{4})-")
GH_API_ISSUE = r"gh api\b[^\n;|&]*?repos/[^\s/]+/[^\s/]+/issues(?=[\s'\"]|$)"
FILING = re.compile(r"board new\b|gh issue create\b|" + GH_API_ISSUE)
TICKET = re.compile(r"\bT-\d{4}\b")
WT_ISSUE = re.compile(r"-worktrees/(\d+)-[\w.-]+")
ORIGIN_IN_TEXT = re.compile(
    r"(?:from|in|reviewing|out of|left out of|after|during|while|of|by)\s+(?:PR\s+)?#(\d+)|^#(\d+)\b", re.I
)
DECISION = re.compile(
    r"owner (?:decision|decides|must|to decide|approval|call|choice)|needs? (?:an |the )?owner|ask the owner|"
    r"OPEN_QUESTIONS|decision needed|owner's call",
    re.I,
)
SMALL_HINT = re.compile(
    r"one[- ]line|a few lines|one[- ]word|\btypo\b|wording|\brename\b|\bflag\b|listing|single line|trivial|"
    r"small fix|constant|one (?:rule|entry|step)|about \d{1,2} (?:LOC|lines)",
    re.I,
)
MARKER = re.compile(
    r"found (?:while|during|reviewing|in|by)|follow[- ]?up|left out of|out of scope|spun off|split (?:out )?(?:of|from)"
    r"|surfaced (?:by|in|during)|noticed (?:while|during|in)|^(?:##\s*Context\s*)?From #\d+|deferred|hand-?back",
    re.I | re.M,
)
REASONS = {
    "scope/Owns/lane": r"\bOwns\b|\blane\b|out(?:side)? (?:of )?(?:my |the |this )?(?:scope|issue|ticket)"
    r"|not in (?:my |the |this )?(?:scope|issue|ticket)|beyond (?:the |this )?(?:scope|issue|ticket)|\bscope\b",
    "parallel work/open PR/hotspot": r"hotspot|conflict|while #?\d+ (?:is )?(?:open|in flight)|in flight"
    r"|avoid(?:ed)? (?:editing|touching)|another (?:worker|agent|PR)|once (?:it|#\d+) merges|after #\d+ merges"
    r"|\bblocked\b",
    "owner decision": r"\bowner\b|\bdecision\b|\bdecide\b",
    "size/budget/later": r"too (?:big|large|broad)|budget|time-?box|separate (?:PR|change|issue|ticket)"
    r"|its own (?:PR|issue|ticket)|\blater\b|left out|not (?:done|covered)|did(?:n't| not)",
    "research gap/protocol follow-up": r"\bgaps?\b|follow[- ]?up|unverified|not closed|file each",
    "found incidentally": r"found (?:while|during|reviewing|in)|noticed|surfaced|discovered|pre-?existing|unrelated",
}


def run(cmd: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True).stdout


def ts(s: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def days(a: dt.datetime | None, b: dt.datetime | None) -> float | None:
    return round((b - a).total_seconds() / 86400, 2) if a and b else None


# --------------------------------------------------------------------------------------------- repositories


def load_repos() -> dict[str, dict]:
    text = (ROOT / ".scratch" / "repos.md").read_text()
    repos = {}
    for code, path in re.findall(r"\b(repo-[A-Z]|H)\s*=\s*[^/=]*?(/[^\s,)]+)", text):
        p = Path(path)
        url = run(["git", "remote", "get-url", "origin"], cwd=p).strip()
        slug = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url).group(1)
        # a transferred repository answers under its new owner; transcripts may use either name
        now = run(["gh", "repo", "view", slug, "--json", "nameWithOwner", "-q", ".nameWithOwner"]).strip()
        repos[code] = {
            "code": code,
            "path": p,
            "slug": slug,
            "aliases": {slug.lower(), now.lower()},
            "fileboard": (p / "board" / "epics.toml").exists(),
        }
    return repos


def sanitize(text: str, repos: dict[str, dict]) -> str:
    text = text.replace(str(Path.home()), "~")
    for r in repos.values():
        for alias in sorted(r["aliases"], key=len, reverse=True):
            text = re.sub(re.escape(alias), r["code"], text, flags=re.I)
        for name in {r["path"].name, *(a.split("/")[-1] for a in r["aliases"])}:
            text = re.sub(re.escape(name), r["code"], text, flags=re.I)
        for a in r["aliases"]:
            text = re.sub(re.escape(a.split("/")[0]), "owner", text, flags=re.I)
    return text


def code_for_path(path: str, repos: dict[str, dict]) -> str | None:
    best = None
    for r in repos.values():
        base = str(r["path"])
        inside = path.startswith(base + "/") or path == base or path.startswith(base + "-worktrees")
        if inside and (best is None or len(base) > len(str(repos[best]["path"]))):
            best = r["code"]
    return best


# --------------------------------------------------------------------------------------------- transcripts


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def split_filings(cmd: str) -> list[dict]:
    """One dict per `board new` / `gh issue create` / `gh api .../issues` POST in a Bash command, in order."""
    parts = re.split(r"(?=board new\b|gh issue create\b|" + GH_API_ISSUE + ")", cmd)[1:]
    out = []
    for seg in parts:
        if re.match(r"(board new|gh issue create)\s+(--help|-h)\b", seg):
            continue
        api = re.match(r"gh api\b[^\n;|&]*?repos/([^\s/]+/[^\s/]+)/issues(?=[\s'\"]|$)", seg)
        if api:
            t = re.search(
                r"(?:-f|-F|--raw-field|--field)\s+(?:title=([\"'])(.*?)(?<!\\)\1|([\"'])title=(.*?)(?<!\\)\3)",
                seg,
                re.S,
            )
            if not t:  # a read, a PATCH of an existing issue, or a comment
                continue
            b = re.search(
                r"(?:-f|-F|--raw-field|--field)\s+(?:body=([\"'])(.*?)(?<!\\)\1|([\"'])body=(.*?)(?<!\\)\3)", seg, re.S
            )
            out.append(
                {
                    "title": t.group(2) or t.group(4) or "",
                    "context": (b.group(2) or b.group(4) or "") if b else "",
                    "type": "",
                    "slug": api.group(1),
                }
            )
            continue

        def get(flag: str, seg: str = seg) -> str:
            m = re.search(flag + r"\s+([\"'])(.*?)(?<!\\)\1", seg, re.S)
            return m.group(2) if m else ""

        typ = re.search(r"--type\s+(\w+)", seg)
        out.append(
            {
                "title": get("--title"),
                "context": get("--context") or get("--body") or get("--accept"),
                "type": typ.group(1) if typ else "",
            }
        )
    return out


def scan_transcripts(repos: dict[str, dict]) -> tuple[list[dict], dict]:
    events, stats = [], collections.Counter()
    slug2code = {a: r["code"] for r in repos.values() for a in r["aliases"]}
    fileboard = next((r["code"] for r in repos.values() if r["fileboard"]), None)
    for f in [
        *glob.glob(str(PROJECTS / "*" / "*.jsonl")),
        *glob.glob(str(PROJECTS / "*" / "*" / "subagents" / "*.jsonl")),
    ]:
        is_sub = "/subagents/" in f
        stats["files_sub" if is_sub else "files_main"] += 1
        meta = {}
        mp = Path(f).with_suffix(".meta.json")
        if is_sub and mp.exists():
            try:
                meta = json.loads(mp.read_text())
            except ValueError:
                meta = {}
        first_prompt, texts, pending, results, handback, last_text = None, collections.deque(maxlen=3), {}, {}, "", ""
        found = []
        with open(f, errors="ignore") as fh:
            for line in fh:
                if first_prompt is None and '"type":"user"' in line:
                    try:
                        d = json.loads(line)
                        first_prompt = text_of((d.get("message") or {}).get("content"))
                    except ValueError:
                        pass
                if pending and '"tool_result"' in line:
                    for tid in [t for t in pending if t in line]:
                        d = json.loads(line)
                        for c in (d.get("message") or {}).get("content") or []:
                            if isinstance(c, dict) and c.get("tool_use_id") == tid:
                                cc = c.get("content")
                                results[tid] = cc if isinstance(cc, str) else text_of(cc) or json.dumps(cc)
                        pending.pop(tid, None)
                if '"assistant"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") != "assistant":
                    continue
                for c in (d.get("message") or {}).get("content") or []:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "text" and c.get("text", "").strip():
                        texts.append(c["text"])
                        last_text = c["text"]
                    if c.get("type") != "tool_use":
                        continue
                    inp = c.get("input") or {}
                    if c.get("name") == "SubagentHandback":
                        handback = inp.get("message", "")
                    cmd = inp.get("command", "") if c.get("name") == "Bash" else ""
                    if cmd and FILING.search(cmd) and split_filings(cmd):
                        pending[c["id"]] = True
                        found.append((c["id"], d, cmd, list(texts)))
        handback = handback or last_text
        for tid, d, cmd, before in found:
            res = results.get(tid, "")
            created = [(slug2code.get(s.lower()), n) for s, n in ISSUE_URL.findall(res)]
            segs = split_filings(cmd)
            if not created:  # `gh api` prints a number or JSON: resolve by title and time against GitHub later
                for sg in segs:
                    code = slug2code.get(sg.get("slug", "").lower()) or next(
                        (code_for_path(m.group(1), repos) for m in re.finditer(r"cd\s+(\S+)", cmd)), None
                    )
                    if sg.get("slug") and code and sg["title"]:
                        created.append((code, "title:" + sg["title"]))
            created += [(fileboard, t) for t in TICKET_CREATED.findall(res)]
            created = [(c, i) for c, i in created if c]
            if not created:
                stats["filing_calls_without_created_item"] += 1
                continue
            role = "lead" if not is_sub else (meta.get("agentType") or d.get("attributionAgent") or "subagent")
            # origin: the work item the filer was on
            cds = [m.group(1) for m in re.finditer(r"cd\s+(\S+)", cmd)]
            places = [*cds, d.get("cwd") or "", meta.get("worktreePath") or ""]
            origin_code = next((code_for_path(p, repos) for p in places if p and code_for_path(p, repos)), None)
            origin = None
            for p in [*places, d.get("gitBranch") or ""]:
                if m := WT_ISSUE.search(p) or re.match(r"(\d+)-", Path(p).name if p else ""):
                    origin = m.group(1)
                    break
                if m := TICKET.search(Path(p).name if p else ""):
                    origin = m.group(0)
                    break
            if origin is None and is_sub:
                for src in (meta.get("description") or "", first_prompt or ""):
                    if m := (TICKET.search(src) or re.search(r"#(\d+)\b|\bissue (\d+)\b", src)):
                        origin = m.group(0) if m.group(0).startswith("T-") else (m.group(1) or m.group(2))
                        break
            for k, (code, item) in enumerate(created):
                seg = segs[k] if k < len(segs) else {"title": "", "context": "", "type": ""}
                orig = origin
                how = "worker" if (is_sub and origin) else "none"
                if orig is None:
                    ctx_ticket = TICKET.search(seg["context"]) if code == fileboard else None
                    m = ORIGIN_IN_TEXT.search(seg["context"])
                    if ctx_ticket:
                        orig, how = ctx_ticket.group(0), "context"
                    elif m:
                        orig, how = m.group(1) or m.group(2), "context"
                stats["created"] += 1
                events.append(
                    {
                        "repo": code,
                        "item": item,
                        "role": role,
                        "is_sub": is_sub,
                        "filed_at": d.get("timestamp"),
                        "session": Path(f).parts[-3 if is_sub else -1][:8],
                        "origin": orig,
                        "origin_how": how,
                        "origin_repo": origin_code,
                        "type": seg["type"],
                        "title": seg["title"],
                        "context": seg["context"],
                        "before": before,
                        "handback": handback,
                    }
                )
    # one row per item: keep the earliest filing
    first = {}
    for e in sorted(events, key=lambda e: e["filed_at"] or ""):
        first.setdefault((e["repo"], e["item"]), e)
    stats["unique_items"] = len(first)
    return list(first.values()), stats


# --------------------------------------------------------------------------------------------- GitHub outcomes


def gh_data(r: dict, refresh: bool) -> tuple[list, list]:
    out = []
    for kind, fields in (
        ("issues", "number,title,body,createdAt,closedAt,state,stateReason,labels,closedByPullRequestsReferences"),
        (
            "prs",
            "number,title,body,createdAt,mergedAt,closedAt,state,additions,deletions,files,headRefName,"
            "closingIssuesReferences",
        ),
    ):
        cache = OUT / "gh" / f"{r['code']}.{kind}.json"
        if refresh or not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            sub = "issue" if kind == "issues" else "pr"
            cache.write_text(
                run(["gh", sub, "list", "-R", r["slug"], "--state", "all", "-L", "5000", "--json", fields])
            )
        out.append(json.loads(cache.read_text()))
    return out[0], out[1]


def owns_of(body: str) -> list[str]:
    m = re.search(r"^## Owns\s*\n(.*?)(?=^## |\Z)", body or "", re.S | re.M)
    return re.findall(r"`([^`]+)`", m.group(1)) if m else []


def github_outcomes(r: dict, refresh: bool) -> dict:
    issues, prs = gh_data(r, refresh)
    by_issue = {i["number"]: i for i in issues}
    by_pr = {p["number"]: p for p in prs}
    pr_issues = collections.defaultdict(set)
    for p in prs:
        ns = {c["number"] for c in p.get("closingIssuesReferences") or []}
        ns |= {
            int(n) for n in re.findall(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", p.get("body") or "", re.I)
        }
        if m := re.match(r"(\d+)-", p.get("headRefName") or ""):
            ns.add(int(m.group(1)))
        pr_issues[p["number"]] = {n for n in ns if n in by_issue}
    issue_prs = collections.defaultdict(set)
    for pn, ns in pr_issues.items():
        for n in ns:
            issue_prs[n].add(pn)
    for i in issues:
        for c in i.get("closedByPullRequestsReferences") or []:
            if c["number"] in by_pr:
                issue_prs[i["number"]].add(c["number"])
                pr_issues[c["number"]].add(i["number"])

    def diff(pns: set[int]) -> dict:
        files, total, net, truncated = set(), 0, 0, False
        for pn in pns:
            p = by_pr[pn]
            fl = p.get("files") or []
            truncated |= len(fl) >= 100
            for x in fl:
                ch = x["additions"] + x["deletions"]
                total += ch
                if not BOOKKEEPING.match(x["path"]):
                    net += ch
                    files.add(x["path"])
        return {"total": total, "net": net, "files": files, "truncated": truncated}

    def item(n: str) -> dict | None:
        n = int(n)
        if n in by_pr and n not in by_issue:  # an origin given as a PR number
            p = by_pr[n]
            return {
                "created": ts(p["createdAt"]),
                "closed": ts(p["mergedAt"]),
                "state": p["state"],
                "reason": "",
                "labels": [],
                "body": p.get("body") or "",
                "title": p["title"],
                "owns": [],
                "merged_prs": {n},
                "shared": False,
                **{"diff": diff({n})},
                "merged_at": ts(p["mergedAt"]),
            }
        i = by_issue.get(n)
        if not i:
            return None
        merged = {pn for pn in issue_prs.get(n, set()) if by_pr[pn].get("mergedAt")}
        return {
            "created": ts(i["createdAt"]),
            "closed": ts(i["closedAt"]),
            "state": i["state"],
            "reason": i.get("stateReason") or "",
            "labels": [x["name"] for x in i.get("labels") or []],
            "body": i.get("body") or "",
            "title": i["title"],
            "owns": owns_of(i.get("body") or ""),
            "merged_prs": merged,
            "shared": any(len(pr_issues[pn]) > 1 for pn in merged),
            "diff": diff(merged),
            "merged_at": max((ts(by_pr[pn]["mergedAt"]) for pn in merged), default=None),
        }

    baseline = [diff({p["number"]})["net"] for p in prs if p.get("mergedAt")]
    return {"item": item, "all": issues, "baseline": baseline}


# --------------------------------------------------------------------------------------------- file-board outcomes


def fileboard_outcomes(r: dict) -> dict:
    repo = r["path"]
    created, done = {}, {}
    cur = None
    log = run(
        [
            "git",
            "log",
            "--reverse",
            "--no-renames",
            "--diff-merges=first-parent",
            "--format=@@%H|%aI|%s",
            "--name-status",
            "--",
            "board/",
        ],
        repo,
    )
    for line in log.splitlines():
        if line.startswith("@@"):
            cur = ts(line[2:].split("|")[1])
        elif m := re.match(r"([AMD])\tboard/(\w+)/(T-\d{4})-", line):
            st, folder, t = m.groups()
            if st == "A":
                created.setdefault(t, cur)
                if folder == "done":
                    done.setdefault(t, cur)
    merges = collections.defaultdict(list)
    for line in run(["git", "log", "--first-parent", "--format=%H|%P|%aI|%s"], repo).splitlines():
        h, _parents, when, subj = line.split("|", 3)
        head = subj.split(":")[0]
        if subj.startswith("Merge T-"):  # "Merge T-0405, T-0406, T-0409 ...": one merge may close several
            for t in TICKET.findall(head):
                merges[t].append(("merge", h, ts(when), len(TICKET.findall(head))))
        elif re.match(r"T-\d{4}\b", subj):
            for t in TICKET.findall(head):
                merges[t].append(("direct", h, ts(when), len(TICKET.findall(head))))
    lanes = {}
    epics = (repo / "board" / "epics.toml").read_text()
    for block in epics.split("[[lanes]]")[1:]:
        if (i := re.search(r'id\s*=\s*"([^"]+)"', block)) and (o := re.search(r"owns\s*=\s*\[(.*?)\]", block, re.S)):
            lanes[i.group(1)] = re.findall(r'"([^"]+)"', o.group(1))
    files_now = {m.group(1): p for p in (repo / "board").glob("*/T-*.md") if (m := re.match(r"(T-\d{4})", p.name))}
    cache: dict[str, dict] = {}

    def numstat(kind: str, h: str) -> list[tuple[int, str]]:
        cmd = (
            ["git", "diff", "--numstat", f"{h}^1", h]
            if kind == "merge"
            else ["git", "show", "--numstat", "--format=", h]
        )
        rows = []
        for line in run(cmd, repo).splitlines():
            a, b, path = line.split("\t", 2)
            rows.append(((int(a) if a != "-" else 0) + (int(b) if b != "-" else 0), path))
        return rows

    def item(t: str) -> dict | None:
        if t in cache:
            return cache[t]
        p = files_now.get(t)
        if not p:
            return None
        body = p.read_text(errors="ignore")
        lane = m.group(1) if (m := re.search(r"^lane:\s*(\S+)", body, re.M)) else ""
        status = p.parent.name
        commits = merges.get(t, [])
        use = [c for c in commits if c[0] == "merge"] or commits
        files, total, net = set(), 0, 0
        for kind, h, _, _ in use:
            for ch, path in numstat(kind, h):
                total += ch
                if not BOOKKEEPING.match(path):
                    net += ch
                    files.add(path)
        merged_at = max((c[2] for c in use), default=None)
        # review = merged to master, waiting for an acceptance run; done = accepted
        closed = done.get(t) if status == "done" else (merged_at if status == "review" else None)
        cache[t] = {
            "created": created.get(t),
            "closed": closed,
            "state": "CLOSED" if status in ("done", "review") else "OPEN",
            "reason": {"done": "COMPLETED", "review": "MERGED_IN_REVIEW"}.get(status, status),
            "labels": [lane],
            "body": body,
            "title": (m.group(1) if (m := re.search(r"^title:\s*(.*)", body, re.M)) else ""),
            "owns": lanes.get(lane, []),
            "merged_prs": {c[1] for c in use},
            "shared": any(c[3] > 1 for c in use),
            "diff": {"total": total, "net": net, "files": files, "truncated": False},
            "merged_at": merged_at,
        }
        return cache[t]

    seen_merges = {c[1]: c for cs in merges.values() for c in cs if c[0] == "merge"}
    baseline = [sum(ch for ch, path in numstat("merge", h) if not BOOKKEEPING.match(path)) for h in seen_merges]
    return {
        "item": item,
        "all": [{"body": p.read_text(errors="ignore"), "number": t} for t, p in files_now.items()],
        "baseline": baseline,
    }


# --------------------------------------------------------------------------------------------- analysis


def matches_owns(path: str, owns: list[str]) -> bool:
    return any(path == o or path.startswith(o.rstrip("/") + "/") or fnmatch.fnmatch(path, o) for o in owns)


def role_class(role: str) -> str:
    for k in ("builder", "tester", "researcher", "reviewer", "architect", "auditor"):
        if k in role:
            return k
    return "lead" if role == "lead" else "other-subagent"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-fetch GitHub data instead of using the cache")
    ap.add_argument("--max-lines", type=int, default=30, help="the 'small enough to fix in place' line bound")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    repos = load_repos()
    items, tstats = scan_transcripts(repos)
    outcomes = {
        code: (fileboard_outcomes(r) if r["fileboard"] else github_outcomes(r, args.refresh))
        for code, r in repos.items()
    }
    resolved, seen = [], set()
    for e in items:
        if str(e["item"]).startswith("title:"):
            want, filed = e["item"][6:].strip(), ts(e["filed_at"])
            hit = [
                i
                for i in outcomes[e["repo"]]["all"]
                if (i.get("title") or "").strip() == want
                and filed
                and abs((ts(i["createdAt"]) - filed).total_seconds()) <= 900
            ]
            if not hit:
                tstats["dropped_title_unmatched"] += 1
                continue
            e["item"] = str(hit[0]["number"])
        if (e["repo"], str(e["item"])) not in seen:
            seen.add((e["repo"], str(e["item"])))
            resolved.append(e)
    items = resolved
    rows = []
    for e in items:
        oc = outcomes[e["repo"]]
        it = oc["item"](e["item"])
        filed = ts(e["filed_at"])
        if it is None or filed is None:
            tstats["dropped_unknown_item"] += 1
            continue
        if not repos[e["repo"]]["fileboard"] and abs((it["created"] - filed).total_seconds()) > 900:
            tstats["dropped_created_mismatch"] += 1  # a URL in the output that this call did not create
            continue
        same_repo = e["origin_repo"] in (None, e["repo"])
        org = oc["item"](e["origin"]) if (e["origin"] and same_repo) else None
        d = it["diff"]
        # overlap counts code and config only: shared notes, specs and READMEs would make every pair overlap
        mine = {f for f in d["files"] if not f.startswith("docs/") and not f.endswith(".md")}
        org_files = (
            {f for f in org["diff"]["files"] if not f.startswith("docs/") and not f.endswith(".md")} if org else set()
        )
        touch_pr = bool(mine & org_files)
        touch_owns = bool(org and any(matches_owns(f, org["owns"]) for f in mine))
        text = " ".join([e["title"], e["context"], it["title"], it["body"][:2000]])
        typ = e["type"] or next((lb.split(":")[1] for lb in it["labels"] if lb.startswith("type:")), "")
        decision = bool(DECISION.search(text)) or any(re.search(r"owner|decision|question", lb) for lb in it["labels"])
        closed_ok = (
            it["state"] == "CLOSED" and it["reason"] in ("COMPLETED", "MERGED_IN_REVIEW", "") and bool(it["merged_prs"])
        )
        rows.append(
            {
                "repo": e["repo"],
                "item": e["item"],
                "session": e["session"],
                "role": role_class(e["role"]),
                "origin": e["origin"] or "",
                "origin_how": e["origin_how"],
                "cross_repo": not same_repo,
                "type": typ,
                "state": it["state"],
                "reason": it["reason"],
                "filed_at": e["filed_at"],
                "days_to_close": days(filed, it["closed"]) if it["closed"] else "",
                "days_to_merged_change": days(filed, it["merged_at"]) if it["merged_at"] else "",
                "filed_days_ago": days(filed, NOW),
                "age_open_days": days(filed, NOW) if it["state"] != "CLOSED" else "",
                "closing_changes": len(it["merged_prs"]),
                "shared_closing_pr": it["shared"],
                "lines_total": d["total"] if it["merged_prs"] else "",
                "lines_net": d["net"] if it["merged_prs"] else "",
                "truncated": d["truncated"],
                "touches_origin_pr_files": touch_pr if org else "",
                "touches_origin_owns": touch_owns if org else "",
                "filed_after_origin_merged": (filed > org["merged_at"]) if (org and org["merged_at"] and filed) else "",
                "decision": decision,
                "done_when_n": len(re.findall(r"^- \[ \]", it["body"], re.M))
                or len(re.findall(r"^- ", it["body"].split("acceptance:")[-1].split("verify:")[0], re.M)),
                "owns_n": len(it["owns"]) if not repos[e["repo"]]["fileboard"] else "",
                "small_hint": bool(
                    SMALL_HINT.search(" ".join([e["title"], e["context"], it["title"], it["body"][:1500]]))
                ),
                "closed_completed_with_diff": closed_ok,
                "small": closed_ok and d["net"] <= args.max_lines,
                "fixable_in_place": closed_ok
                and d["net"] <= args.max_lines
                and not decision
                and typ not in ("research", "epic")
                and e["origin"] is not None,
                "reasons": ";".join(
                    k for k, rx in REASONS.items() if re.search(rx, " ".join([e["context"], *e["before"][-2:]]), re.I)
                ),
            }
        )
        e["_row"] = rows[-1]
        e["title"] = e["title"] if e["title"] and "$" not in e["title"] else it["title"]
        if not e["context"] or "$" in e["context"][:3]:
            m = re.search(r"## Context\s*\n(.*?)(?=^## |\Z)", it["body"], re.S | re.M)
            e["context"] = (m.group(1) if m else it["body"]).strip()
    write_outputs(rows, items, repos, outcomes, tstats, args.max_lines)


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else f"{a}/0"


def q(xs: list[float]) -> str:
    xs = sorted(x for x in xs if x != "")
    if not xs:
        return "n=0"
    qs = statistics.quantiles(xs, n=4) if len(xs) > 1 else [xs[0]] * 3
    return f"n={len(xs)} median={statistics.median(xs):.1f} IQR={qs[0]:.1f}-{qs[2]:.1f} max={max(xs):.1f}"


def write_outputs(rows, items, repos, outcomes, tstats, max_lines) -> None:
    with open(OUT / "items.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(OUT / "quotes.txt", "w") as fh:
        for e in items:
            if "_row" not in e:
                continue
            r = e["_row"]
            fh.write(
                f"=== {r['repo']} {r['item']} role={r['role']} origin={r['origin']} net={r['lines_net']} "
                f"state={r['state']} reasons={r['reasons']}\n"
            )
            fh.write("TITLE: " + sanitize(e["title"], repos)[:200] + "\n")
            fh.write("CONTEXT: " + sanitize(e["context"], repos)[:500].replace("\n", " ") + "\n")
            for b in e["before"][-2:]:
                fh.write("BEFORE: " + sanitize(b, repos)[:400].replace("\n", " ") + "\n")
            hb = [
                s
                for s in re.split(r"(?<=[.;\n])\s+", e["handback"])
                if str(e["item"]).lstrip("T-") in s or e["item"] in s
            ]
            for s in hb[:2]:
                fh.write("HANDBACK: " + sanitize(s, repos)[:300].replace("\n", " ") + "\n")
    P = print
    P(
        f"transcripts: {tstats['files_main']} main, {tstats['files_sub']} subagent files; "
        f"{tstats['created']} filings -> {tstats['unique_items']} unique items "
        f"({tstats['filing_calls_without_created_item']} filing calls created nothing)"
    )
    P(
        f"rows with a known outcome: {len(rows)}; dropped: {tstats['dropped_unknown_item']} unknown item, "
        f"{tstats['dropped_created_mismatch']} URL not created by the call, "
        f"{tstats['dropped_title_unmatched']} gh-api filings whose title matched no issue"
    )
    by = collections.Counter((r["repo"], r["role"]) for r in rows)
    P("filed by repo x role: " + ", ".join(f"{k[0]}/{k[1]}={v}" for k, v in sorted(by.items())))
    groups = {
        "all": rows,
        "during other work (origin known)": [r for r in rows if r["origin"]],
        "workers (subagent with origin)": [r for r in rows if r["origin"] and r["origin_how"] == "worker"],
        "builder/tester/reviewer/auditor": [
            r for r in rows if r["role"] in ("builder", "tester", "reviewer", "auditor") and r["origin"]
        ],
        "researcher": [r for r in rows if r["role"] == "researcher" and r["origin"]],
        "lead, from other work": [r for r in rows if r["role"] == "lead" and r["origin"]],
        **{f"during other work, {c}": [r for r in rows if r["origin"] and r["repo"] == c] for c in sorted(repos)},
    }
    for name, g in groups.items():
        if not g:
            continue
        closed = [r for r in g if r["state"] == "CLOSED"]
        withdiff = [r for r in g if r["closed_completed_with_diff"]]
        P(f"\n## {name}: {len(g)} items")
        P("  by repo: " + ", ".join(f"{k}={v}" for k, v in sorted(collections.Counter(r["repo"] for r in g).items())))
        P(
            f"  closed {pct(len(closed), len(g))}; still open {pct(len(g) - len(closed), len(g))}; "
            f"closed with a merged change {pct(len(withdiff), len(g))}"
        )
        P(f"  closed as not planned/duplicate: {sum(r['reason'] in ('NOT_PLANNED', 'DUPLICATE') for r in closed)}")
        P(
            f"  days filed->closed: {q([r['days_to_close'] for r in closed])}; "
            f"filed->closing change merged, hours: {q([r['days_to_merged_change'] * 24 for r in withdiff])}"
        )
        old = [r for r in g if r["filed_days_ago"] >= 7]
        P(
            f"  filed >= 7 days ago: {len(old)}; closed within 7 days of filing: "
            f"{pct(sum(r['days_to_close'] != '' and r['days_to_close'] <= 7 for r in old), len(old))}; still open now: "
            f"{pct(sum(r['state'] != 'CLOSED' for r in old), len(old))}"
        )
        P(f"  open age days: {q([r['age_open_days'] for r in g if r['state'] != 'CLOSED'])}")
        P(f"  closing diff, net lines: {q([r['lines_net'] for r in withdiff])}")
        P(f"  closing diff, all lines: {q([r['lines_total'] for r in withdiff])}")
        for b in (10, 30, 50, 100):
            P(f"    net <= {b}: {pct(sum(r['lines_net'] <= b for r in withdiff), len(withdiff))} of closed-with-change")
        tp = [r for r in withdiff if r["touches_origin_pr_files"] != ""]
        P(
            f"  closing change touched the origin change's files: {pct(sum(r['touches_origin_pr_files'] is True for r in tp), len(tp))}; "
            f"origin Owns: {pct(sum(r['touches_origin_owns'] is True for r in tp), len(tp))}"
        )
        fa = [r for r in g if r["filed_after_origin_merged"] != ""]
        P(
            f"  filed after the origin change merged: {pct(sum(r['filed_after_origin_merged'] is True for r in fa), len(fa))}"
        )
        P(
            f"  owner-decision marker: {pct(sum(r['decision'] for r in g), len(g))}; shared closing PR: "
            f"{sum(r['shared_closing_pr'] for r in withdiff)}"
        )
        op = [r for r in g if r["state"] != "CLOSED"]
        P(
            f"  open items, size proxies: small-hint wording {pct(sum(r['small_hint'] for r in op), len(op))}; "
            f"one DONE WHEN {pct(sum(r['done_when_n'] == 1 for r in op), len(op))}; "
            f"one DONE WHEN and <= 2 Owns {pct(sum(r['done_when_n'] == 1 and r['owns_n'] != '' and r['owns_n'] <= 2 for r in op), len(op))}"
        )
        fx = [r for r in g if r["fixable_in_place"]]
        P(
            f"  FIXABLE IN PLACE (completed, net <= {max_lines}, no decision, not research/epic): "
            f"{pct(len(fx), len(g))} of filed; {pct(len(fx), len(withdiff))} of closed-with-change"
        )
        fxo = [r for r in fx if r["touches_origin_pr_files"] is True or r["touches_origin_owns"] is True]
        P(f"    ... and touching the origin's files or Owns: {pct(len(fxo), len(g))}")
        rc = collections.Counter(x for r in g for x in r["reasons"].split(";") if x)
        P(f"  no stated reason (no wording category matched): {pct(sum(not r['reasons'] for r in g), len(g))}")
        P(
            "  wording categories (crude regex, multi-label): "
            + ", ".join(f"{k}={pct(v, len(g))}" for k, v in rc.most_common())
        )
    with open(OUT / "small.txt", "w") as fh:
        for e in items:
            r = e.get("_row")
            if r and r["closed_completed_with_diff"] and r["lines_net"] <= 100:
                fh.write(
                    f"{r['repo']} {r['item']} net={r['lines_net']} total={r['lines_total']} role={r['role']} "
                    f"origin={r['origin']}({r['origin_how']}) type={r['type']} decision={r['decision']} "
                    f"shared={r['shared_closing_pr']} touchPR={r['touches_origin_pr_files']} "
                    f"touchOwns={r['touches_origin_owns']} days={r['days_to_merged_change']}\n   "
                    + sanitize(e["title"], repos)[:160]
                    + "\n   "
                    + sanitize(e["context"], repos)[:300].replace("\n", " ")
                    + "\n"
                )
    P("\n## sessions with the most items filed during other work (session id prefix, repo: workers + lead)")
    ses = collections.Counter((r["session"], r["repo"]) for r in rows if r["origin"])
    for (sid, code), n in ses.most_common(8):
        sub = [r for r in rows if r["session"] == sid and r["repo"] == code and r["origin"]]
        days_ = sorted(r["filed_at"][:10] for r in sub)
        P(
            f"  {sid} {code}: {n} ({days_[0]}..{days_[-1]}); open now {sum(r['state'] != 'CLOSED' for r in sub)}; "
            f"roles {dict(collections.Counter(r['role'] for r in sub))}"
        )
    P("\n## baseline: every merged change, net lines (bookkeeping excluded)")
    for code, oc in outcomes.items():
        b = oc["baseline"]
        P(f"  {code}: {q(b)}; <= {max_lines}: {pct(sum(x <= max_lines for x in b), len(b))}")
    # text markers across all GitHub issues / tickets, for coverage beyond the transcripts
    P("\n## text markers in item bodies (all items, any filer)")
    seen = {(r["repo"], str(r["item"])) for r in rows}
    for code, oc in outcomes.items():
        allm = [i for i in oc["all"] if MARKER.search((i.get("body") or "")[:1500])]
        insc = sum((code, str(i["number"])) in seen for i in allm)
        P(
            f"  {code}: {pct(len(allm), len(oc['all']))} items carry a follow-up marker; {insc} of those are in the transcript set"
        )


if __name__ == "__main__":
    main()
