"""Measure the issue and PR bookkeeping the lead and workers still do by hand (#346, round 1, sub-question a).

Usage:
    python3 bookkeeping.py extract --cache CACHE.jsonl.gz NAME=PROJECT_DIR [NAME=PROJECT_DIR ...]
    python3 bookkeeping.py fetch --cache CACHE.jsonl.gz --out GH.json      # GitHub data, needs `gh` auth
    python3 bookkeeping.py report --cache CACHE.jsonl.gz --gh GH.json > report.json

Reuses command-sequences/sequences.py (#321): its transcript reader (`extract_file`), its step splitter and labels
(`annotate`), and its cost model (`turn_cost`, `carry_cost`, `occ_cost`). `extract` adds what that reader drops:
the full timestamp of each call, the recipient and length of SendMessage, the subagent type of Agent spawns, and
the file path of Edit/Write calls. The cache and GH.json hold command text, PR comments and titles: keep both outside
the repository. `report` prints counts and modelled dollars only, so its output can be committed.
"""

import argparse
import glob
import gzip
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from itertools import pairwise
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "command-sequences"))
sys.dont_write_bytecode = True
import sequences as S  # noqa: E402

# repo-E is private and named by code: set REPO_E to its bare GitHub repository name to rerun
OWNER = "Marius-Juston"
REPO_E = os.environ.get("REPO_E", "repo-E")

# ---------------------------------------------------------------- extraction (sequences.extract_file + extras)


def extras_file(path):
    """One dict per tool_use block, in the order sequences.extract_file appends its call records."""
    out = []
    with open(path, errors="ignore") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "assistant":
                continue
            for b in (d.get("message") or {}).get("content") or []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                inp = b.get("input") or {}
                e = {"tsf": d.get("timestamp", "")[:19]}
                name = b.get("name")
                if name == "SendMessage":
                    msg = inp.get("message")
                    msg = msg if isinstance(msg, str) else json.dumps(msg)
                    e |= {"to": inp.get("to", ""), "msg": msg[:3000], "msg_chars": len(msg)}
                elif name in ("Agent", "Task"):
                    p = inp.get("prompt", "")
                    e |= {"atype": inp.get("subagent_type", "general"), "msg": p[:3000], "msg_chars": len(p)}
                elif name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                    body = inp.get("content") or inp.get("new_string") or json.dumps(inp.get("edits", ""))
                    e |= {"path": inp.get("file_path", ""), "msg_chars": len(body)}
                out.append(e)
    return out


HANDBACK = re.compile(r'<agent-message from="(\w+)">')


def handbacks(path):
    """Subagent hand-backs that reach a main session as messages: (turns so far, sender id, characters)."""
    out, seen = [], set()
    with open(path, errors="ignore") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "assistant":
                seen.add((d.get("message") or {}).get("id"))
            elif d.get("type") == "user":
                c = (d.get("message") or {}).get("content")
                txt = c if isinstance(c, str) else S.result_text(c)
                if m := HANDBACK.search(txt[:300]):
                    out.append((len(seen), m.group(1), len(txt)))
    return out


def agent_ids(d):
    """subagent id -> agentType, from the .meta.json files next to each subagent transcript."""
    ids = {}
    for f in glob.glob(os.path.join(d, "*", "subagents", "*.meta.json")):
        try:
            with open(f) as fh:
                ids[os.path.basename(f)[len("agent-") : -len(".meta.json")]] = json.load(fh).get("agentType", "?")
        except (OSError, ValueError):
            continue
    return ids


def extract_job(job):
    rec = S.extract_file(job)
    ex = extras_file(job[0])
    if len(ex) != len(rec["calls"]):
        raise SystemExit(f"call order mismatch in {job[0]}")
    for c, e in zip(rec["calls"], ex, strict=True):
        c.update(e)
    rec["path"] = job[0]
    rec["handbacks"] = handbacks(job[0]) if job[2] is None else []
    return rec


def extract(args):
    jobs, ids = [], {}
    for spec in args.projects:
        name, d = spec.split("=", 1)
        ids |= agent_ids(d)
        for f in glob.glob(os.path.join(d, "*.jsonl")):
            if not any(x in f for x in args.exclude):
                jobs.append((f, name, None))
        for f in glob.glob(os.path.join(d, "*", "subagents", "*.jsonl")):
            if any(x in f for x in args.exclude):
                continue
            meta = f[:-6] + ".meta.json"
            at = "unknown"
            if os.path.exists(meta):
                with open(meta) as fh:
                    at = json.load(fh).get("agentType", "unknown")
            jobs.append((f, name, at))
    with Pool(max(1, (os.cpu_count() or 2) - 1)) as pool, gzip.open(args.cache, "wt") as out:
        out.write(json.dumps({"agent_ids": ids}) + "\n")
        for rec in pool.imap_unordered(extract_job, jobs, chunksize=4):
            out.write(json.dumps(rec) + "\n")
    print(f"{len(jobs)} transcripts -> {args.cache}", file=sys.stderr)


def load(cache):
    """(agent id -> type, iterator of annotated transcripts)."""
    with gzip.open(cache, "rt") as fh:
        ids = json.loads(fh.readline())["agent_ids"]

    def it():
        with gzip.open(cache, "rt") as fh:
            fh.readline()
            for line in fh:
                yield S.annotate(json.loads(line))

    return ids, it()


# ---------------------------------------------------------------- classification


def labels(c):
    return [lb for _, lb, _ in c.get("steps", [])] if c["tool"] == "Bash" else []


def repo_of(cmd, project):
    """The repository a command acts on: an explicit -R or a cd into the checkout, else the transcript's project."""
    if re.search(r"-R\s+\S*agent-harness|cd\s+[^;&|\n]*agent-harness", cmd):
        return "H"
    if re.search(rf"-R\s+\S*{re.escape(REPO_E)}|cd\s+[^;&|\n]*{re.escape(REPO_E)}", cmd):
        return "repo-E"
    return project


REVIEWER_TYPES = ("reviewer", "auditor")
REVIEW_TXT = re.compile(r"(?i)review|audit|finding|blocking|\bnits?\b|verdict")
CONFLICT_TXT = re.compile(
    r"(?i)conflict|merge origin/main|git merge origin|rebase|retarget|stacked|your base|dead base"
)
COMMENT_BODY = re.compile(r"--body(?:-file)?\s+(?:-\s*<<-?\s*'?\w+'?\s*\n)?['\"]?(.{0,300})", re.S)
BRANCH_DELETE = re.compile(r"git branch -[dD]\b")


def body_kind(cmd):
    m = COMMENT_BODY.search(cmd)
    b = (m.group(1) if m else "").lower()
    if "review" in b[:150] or "audit" in b[:150]:
        return "review"
    if b.startswith("owner"):
        return "owner decision"
    return "other"


def send_kind(c, ids):
    """A lead SendMessage's purpose: by recipient first (anything sent to a reviewer or auditor is a re-review
    request), then by the message's opening text."""
    to = ids.get(c.get("to", ""), "?")
    msg = c.get("msg", "")
    if any(t in to for t in REVIEWER_TYPES):
        return "re-review request"
    if REVIEW_TXT.search(msg[:200]):
        return "review findings to author"
    if CONFLICT_TXT.search(msg[:600]):
        return "conflict/base relay"
    if re.match(r"(?i)\s*owner", msg):
        return "owner decision relay"
    return "other"


def writes_file(c, name):
    """An Edit/Write of `name`, or a Bash command that writes it (redirect, tee, sed -i, python write)."""
    if c["tool"] in ("Edit", "Write", "MultiEdit"):
        return c.get("path", "").endswith(name)
    if c["tool"] == "Bash" and name in c.get("cmd", ""):
        n = re.escape(name)
        return bool(re.search(rf"(>|tee|sed -i|open\([^)]*['\"]w)[^;&|\n]*{n}|{n}[^\n]*write_text", c["cmd"]))
    return False


SEND_CAT = {
    "conflict/base relay": "5 conflict relay",
    "owner decision relay": "x owner-decision relay",
    "re-review request": "2 review relay",
    "review findings to author": "2 review relay",
    "other": "x other SendMessage",
}
UPKEEP = {
    "dev board sync",
    "dev board sweep",
    "dev board event",
    "dev board list",
    "dev board next",
    "dev board wave",
    "dev board lint",
    "dev usage-poll",
    "dev quota-cap",
}
FILING = {
    "dev board new",
    "gh issue create",
    "gh issue edit",
    "gh issue close",
    "gh issue reopen",
    "dev board link",
    "dev board block",
    "dev board release",
    "dev board beat",
}


def categories(c, ids):
    """The bookkeeping categories a call belongs to (a call can be in several)."""
    lb, cmd, out, t = set(labels(c)), c.get("cmd", ""), set(), c["tool"]
    if {"dev integrate", "gh pr checks", "gh pr merge", "gh run rerun", "gh pr update-branch"} & lb or (
        "gh pr view" in lb and re.search(r"statusCheckRollup|mergeable|mergeStateStatus", cmd)
    ):
        out.add("1 integrate loop")
    if t == "SendMessage":
        out.add(SEND_CAT[send_kind(c, ids)])
    if t in ("Agent", "Task") and any(x in c.get("atype", "") for x in REVIEWER_TYPES):
        out.add("2 review relay: reviewer spawn")
    if {"gh pr comment", "gh pr review", "gh issue comment"} & lb:
        k = body_kind(cmd)
        if k == "review":
            out.add("2 review relay")
        elif k == "owner decision":
            out.add("x owner-decision record")
        elif "gh issue comment" in lb:
            out.add("4 filing/upkeep of issues")
        else:
            out.add("x other PR comment")
    if "dev board feedback" in lb or re.search(r"gh api [^|]*pulls/\d+/(comments|reviews)", cmd):
        out.add("2 review relay")
    if UPKEEP & lb:
        out.add("3 board upkeep runs")
    if FILING & lb:
        out.add("4 filing/upkeep of issues")
    if t == "Bash" and re.search(r"CONFLICT \(|Merge conflict in", c.get("head", "") + c.get("tail", "")):
        out.add("5 conflict seen in output")
    if "gh pr edit" in lb and "--base" in cmd:
        out.add("5 conflict relay")
    if {"dev pr", "gh pr create"} & lb or ("gh pr edit" in lb and re.search(r"--body|--title", cmd)):
        out.add("6 PR body/open")
    if writes_file(c, "HANDOFF.md") or writes_file(c, "SESSION.md"):
        out.add("7 HANDOFF/SESSION write")
    if writes_file(c, "CHANGELOG.md") or {"git tag", "gh release"} & lb:
        out.add("8 changelog/release")
    if {"git worktree add", "dev board claim", "git worktree remove", "git worktree prune"} & lb or (
        "git branch" in lb and BRANCH_DELETE.search(cmd)
    ):
        out.add("9 worktree+claim/cleanup")
    return out


class Cat:
    """Calls of one category: count, distinct turns, modelled $ (each turn once plus every call's carry), errors."""

    def __init__(self):
        self.calls = self.err = self.chars = 0
        self.turns = set()
        self.usd = 0.0
        self.files = set()

    def add(self, tr, c):
        self.calls += 1
        self.err += bool(c["err"])
        self.chars += c.get("cmd_chars", 0)
        key = (tr["file"], c["turn"])
        if key not in self.turns:
            self.turns.add(key)
            self.usd += c["turn_usd"]
        self.usd += c["carry_usd"]
        self.files.add(tr["file"])

    def row(self):
        return {
            "calls": self.calls,
            "turns": len(self.turns),
            "transcripts": len(self.files),
            "err_rate": round(self.err / self.calls, 3) if self.calls else 0,
            "usd": round(self.usd, 2),
            "usd_per_turn": round(self.usd / len(self.turns), 3) if self.turns else 0,
            "written_chars": self.chars,
        }


def spend(tr):
    """Modelled $ of a whole transcript, as sequences.report computes it."""
    u = tr["usage"]
    return (
        u["inp"] * 4.0
        + u["cw"] * S.PRICE["cw"]
        + u["cr"] * S.PRICE["cr"]
        + (u["out"] + S.HIDDEN_OUT * tr["turns"]) * S.PRICE["out"]
    ) / 1e6


def out_usd(chars):
    """Output-token $ of `chars` characters the model wrote (0.443 tokens per character, Opus 5.5 output price)."""
    return chars * S.OUT_TOK_PER_CHAR * S.PRICE["out"] / 1e6


def ts(x):
    return datetime.fromisoformat(x.replace("Z", "+00:00")).replace(tzinfo=None) if x else None


# ---------------------------------------------------------------- report

PR_IN_TEXT = re.compile(r"\bPR\s*(?:[\w.-]+/[\w.-]+)?#(\d+)|\bpr (?:diff|view|checks) (\d+)")
ISSUE_URL = re.compile(rf"github\.com/{OWNER}/({re.escape(REPO_E)}|agent-harness)/issues/(\d+) \((?:ready|blocked)\)")
SUMMARY_HEREDOC = re.compile(r"dev pr\b[^\n]*--summary\s+-[^\n]*<<-?\s*'?(\w+)'?\n(.*?)\n\1\b", re.S)


def out_text(c):
    """A call's kept output: its first 400 characters, plus its last 400 when the output is longer (no overlap)."""
    return c.get("head", "") + ("\n" + c.get("tail", "") if c.get("out", 0) > 400 else "")


def file_class(path):
    """A conflicted file's class (repo-E is private: its paths stay out of the report)."""
    for k, rx in (
        ("lock file", r"(uv\.lock|package-lock\.json|bun\.lockb?|pnpm-lock\.yaml)$"),
        ("generated doc (docs/generated/)", r"docs/generated/"),
        ("research INDEX.md", r"INDEX\.md$"),
        ("CHANGELOG.md", r"CHANGELOG\.md$"),
        ("pyproject.toml", r"pyproject\.toml$"),
        ("agent/skill prompt (.md under plugins/ or .claude/)", r"(plugins|\.claude)/.*\.md$"),
        ("test file", r"(^|/)tests?/|\.test\.|\.spec\."),
        ("stylesheet", r"\.css$"),
        ("other source or doc", r"."),
    ):
        if re.search(rx, path):
            return k
    return "other"


def first_pr(text):
    m = PR_IN_TEXT.search(text or "")
    return int(m.group(1) or m.group(2)) if m else None


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


FLAGS = ("--override", "--harness-ok", "--local-ci")


def refusal_kind(text):
    t = text.lower()
    for k, rx in (
        ("pending check", r"pending|in_progress|queued|still running"),
        ("failing check", r"fail(ed|ing|ure)"),
        ("review thread / requested changes", r"thread|changes_requested|requested changes"),
        ("--override needs a reason", r"override needs"),
        ("--local-ci", r"local-ci"),
        ("deletes owned paths", r"deletes what open issues own"),
        ("feedback (unspecified)", r"feedback"),
    ):
        if re.search(rx, t):
            return k
    return "other"


def integrate_calls(tr):
    """(repo, pr, time, outcome, head sha or None, call) for each `dev integrate` in a transcript."""
    out = []
    for c in tr["calls"]:
        if c["tool"] != "Bash" or "dev integrate" not in labels(c):
            continue
        for m in re.finditer(r"dev integrate\s+(\d+)", c["cmd"]):
            text = c.get("head", "") + c.get("tail", "")
            sha = re.findall(r'"head": "([0-9a-f]{40})"', text)
            out.append(
                (
                    repo_of(c["cmd"], tr["project"]),
                    int(m.group(1)),
                    c["tsf"],
                    S.integrate_outcome(c),
                    sha[-1] if sha else None,
                    text,
                    c,
                )
            )
    return out


def between(gh, repo, pr, a, b):
    """What changed on PR `pr` between two integrate tries at times a < b: commits, force pushes, comments, reviews."""
    p = gh.get(repo, {}).get(pr)
    if not p:
        return None
    ta, tb = ts(a + "Z"), ts(b + "Z")

    def inside(x):
        return x is not None and ta < ts(x) <= tb

    commits = [n["commit"]["committedDate"] for n in p["commits"]["nodes"]]
    pushes = [n["createdAt"] for n in p["timelineItems"]["nodes"] if n["__typename"] == "HeadRefForcePushedEvent"]
    comments = [n["createdAt"] for n in p["comments"]["nodes"]] + [n["submittedAt"] for n in p["reviews"]["nodes"]]
    return {
        "new_commit": any(inside(x) for x in commits) or any(inside(x) for x in pushes),
        "new_feedback": any(inside(x) for x in comments),
    }


def report(args):
    ids, trs = load(args.cache)
    with open(args.gh) as fh:
        ghraw = json.load(fh)
    gh = {p: {x["number"]: x for x in v["prs"]} for p, v in ghraw.items()}
    issues = {p: {x["number"]: x for x in v["issues"]} for p, v in ghraw.items()}
    R = {}
    cats = defaultdict(lambda: defaultdict(Cat))
    overview, spend_role, days = defaultdict(Counter), Counter(), set()
    sends = defaultdict(lambda: [0, 0])  # kind -> [n, chars]
    known = Counter()  # SendMessage recipients whose agent type is known
    hbacks = defaultdict(lambda: [0, 0, 0.0, 0.0])  # sender role -> [n, chars, carry $, carry $ above 300 chars]
    bursts = []  # sets of lead turns, one per burst of integrate-loop calls
    loop_split, loop_split_usd = Counter(), Counter()  # lead integrate-loop turns: with an integrate call, or not
    relay = defaultdict(lambda: defaultdict(list))  # (file, pr) -> action -> [call]
    tries = defaultdict(list)
    board_new = []  # (role, repo, number, call)
    closes = Counter()
    claim_chain = Counter()
    pr_summaries = defaultdict(list)
    handoff = defaultdict(list)
    changelog = defaultdict(list)
    dispatch = [0, 0]
    reviewer_wt = Counter()
    conflict_files = Counter()
    conflict_roles = Counter()
    posts = Counter()  # comment and review posts by role, command and body kind
    close_days = defaultdict(set)
    owner = Counter()
    dedupe_search = Counter()
    sumparts = defaultdict(Counter)
    issue_reads = Counter()
    for tr in trs:
        role = tr["role"]
        overview[role]["transcripts"] += 1
        overview[role]["turns"] += tr["turns"]
        spend_role[role] += spend(tr)
        for i, c in enumerate(tr["calls"]):
            if c.get("tsf"):
                days.add(c["tsf"][:10])
            ks = categories(c, ids)
            for k in ks:
                cats[role][k].add(tr, c)
                cats["all"][k].add(tr, c)
            if ks:
                cats[role]["ANY bookkeeping category"].add(tr, c)
                cats["all"]["ANY bookkeeping category"].add(tr, c)
            if {k for k in ks if k[0].isdigit() and "spawn" not in k}:
                cats[role]["ANY of 1-9 (no reviewer spawn, no x-rows)"].add(tr, c)
                cats["all"]["ANY of 1-9 (no reviewer spawn, no x-rows)"].add(tr, c)
            lb, cmd = labels(c), c.get("cmd", "")
            if c["tool"] == "SendMessage" and role == "lead":
                k = send_kind(c, ids)
                known[c.get("to", "") in ids] += 1
                sends[k][0] += 1
                sends[k][1] += c.get("msg_chars", 0)
            if role == "lead":
                pr = None
                if c["tool"] == "SendMessage" and send_kind(c, ids) in (
                    "re-review request",
                    "review findings to author",
                ):
                    pr, act = first_pr(c.get("msg")), send_kind(c, ids)
                elif c["tool"] in ("Agent", "Task") and any(x in c.get("atype", "") for x in REVIEWER_TYPES):
                    pr, act = first_pr(c.get("msg")), "reviewer spawn"
                elif {"gh pr comment", "gh pr review"} & set(lb) and body_kind(cmd) == "review":
                    m = re.search(r"gh pr (?:comment|review)\s+(\d+)", cmd)
                    pr, act = (int(m.group(1)) if m else None), "review posted by lead"
                if pr:
                    relay[(tr["file"], pr)][act].append(c)
                if c["tool"] == "AskUserQuestion":
                    owner["AskUserQuestion calls"] += 1
                    nxt = tr["calls"][i + 1 : i + 8]
                    if any(
                        "x owner-decision record" in categories(n, ids)
                        or "x owner-decision relay" in categories(n, ids)
                        for n in nxt
                    ):
                        owner["... followed by an owner-decision comment or relay within 7 calls"] += 1
                if c["tool"] in ("Agent", "Task"):
                    dispatch[0] += 1
                    dispatch[1] += c.get("msg_chars", 0)
                if "gh issue close" in lb:
                    for seg in re.split(r"gh issue close", cmd)[1:]:
                        s2 = seg[:400].lower()
                        if re.search(
                            r"duplicate|superseded|covered by|already|folded|tracked (upstream|in|as)|same as|"
                            r"merged into|subsumed|replaced by|moved to|obsolete",
                            s2,
                        ):
                            closes["duplicate/superseded/covered"] += 1
                            close_days["duplicate/superseded/covered"].add(c["tsf"][:10])
                        elif "not planned" in s2 or "not_planned" in s2:
                            closes["not planned (other)"] += 1
                        elif re.search(r"merged|done in|done:|pr #|shipped|released|met by", s2):
                            closes["done by a merged PR or release"] += 1
                            close_days["done by a merged PR or release"].add(c["tsf"][:10])
                        else:
                            closes["other"] += 1
            if c["tool"] == "Bash":
                if "dev board new" in lb:
                    for m in dict.fromkeys(ISSUE_URL.findall(out_text(c))):
                        m = (None, *m)
                        board_new.append((role, "H" if m[1] == "agent-harness" else "repo-E", int(m[2])))
                    if not ISSUE_URL.search(out_text(c)):
                        board_new.append((role, None, None))
                    prev = [p for p in tr["calls"][max(0, i - 3) : i] if p["tool"] == "Bash"]
                    if any(
                        {"gh issue list", "gh search issues", "gh api search/issues"} & set(labels(p))
                        or ("gh issue list" in labels(p))
                        for p in prev
                    ) or {"gh issue list"} & set(lb):
                        dedupe_search[role] += 1
                if "git worktree add" in lb and role == "lead":
                    claim_chain["worktree add (lead)"] += 1
                    if "dev board claim" in lb:
                        claim_chain["... with board claim in the same call"] += 1
                        claim_chain["... same call, error"] += bool(c["err"])
                if "dev board claim" in lb and role == "lead":
                    claim_chain["board claim (lead)"] += 1
                    claim_chain["board claim error"] += bool(c["err"])
                if role == "reviewer":
                    reviewer_wt["worktree add"] += "git worktree add" in lb
                    reviewer_wt["worktree remove"] += "git worktree remove" in lb
                    reviewer_wt["reviewer transcripts"] += 0
                if "dev pr" in lb:
                    m = SUMMARY_HEREDOC.search(cmd)
                    pr_summaries[role].append((len(m.group(2)) if m else None, bool(c["err"]), c))
                    if m:
                        body = m.group(2).splitlines()
                        sumparts[role]["all"] += len(m.group(2))
                        sumparts[role]["checkbox lines"] += sum(
                            len(x) for x in body if re.match(r"\s*[-*] \[[ xX]\]", x)
                        )
                        sumparts[role]["gate/test result lines"] += sum(
                            len(x) for x in body if re.search(r"RESULT:|dev check|passed|cite-check|\d+ passed", x)
                        )
                if "gh issue view" in lb:
                    issue_reads[role] += 1
                for x in ("gh pr comment", "gh pr review", "gh issue comment"):
                    if x in lb:
                        posts[f"{role}: {x}: {body_kind(cmd)}"] += 1
                hit = set(re.findall(r"Merge conflict in (\S+)", out_text(c)))
                conflict_roles[role] += bool(hit)
                for f in hit:
                    conflict_files[file_class(f)] += 1
                for f in re.findall(r'"conflicts": \[([^\]]*)\]', out_text(c))[-1:]:
                    for x in re.findall(r'"([^"]+)"', f):
                        conflict_files[file_class(x)] += 1
            if writes_file(c, "HANDOFF.md") or writes_file(c, "SESSION.md"):
                handoff[role].append(c)
            if writes_file(c, "CHANGELOG.md"):
                changelog[role].append(c)
            if c["tool"] == "Read" and c.get("path", "").endswith("HANDOFF.md"):
                handoff[role + " (Read)"].append(c)
        if role == "lead":
            for x in integrate_calls(tr):
                tries[(x[0], x[1])].append(x)
            bounds = [*sorted(tr["compactions"]), tr["turns"]]
            for turn, sender, chars in tr.get("handbacks", []):
                r = S.role_of(ids.get(sender))
                r = "unknown" if sender not in ids else r
                remaining = max(0, next((b for b in bounds if b >= turn), tr["turns"]) - turn)
                hb = hbacks[r]
                hb[0] += 1
                hb[1] += chars
                hb[2] += S.carry_cost(chars, remaining)
                hb[3] += S.carry_cost(max(0, chars - 300), remaining)
            # integrate-loop calls in bursts (a gap over 20 minutes starts a new burst)
            loop = sorted((c for c in tr["calls"] if "1 integrate loop" in categories(c, ids)), key=lambda c: c["tsf"])
            seen_turns = set()
            for c in loop:
                if c["turn"] in seen_turns:
                    continue
                seen_turns.add(c["turn"])
                k = "integrate call" if "dev integrate" in labels(c) else "checks/status read or wait only"
                loop_split[k] += 1
                loop_split_usd[k] += c["turn_usd"] + c["carry_usd"]
            last = None
            for c in loop:
                t = ts(c["tsf"] + "Z")
                if last is None or (t - last).total_seconds() > 1200:
                    bursts.append(set())
                bursts[-1].add(c["turn"])
                last = t
    R["window"] = [min(days), max(days)]
    R["overview"] = {r: dict(v) | {"usd": round(spend_role[r], 1)} for r, v in overview.items()}
    R["overview_usd_all"] = round(sum(spend_role.values()), 1)
    R["categories"] = {
        r: {k: v.row() for k, v in sorted(d.items())}
        for r, d in cats.items()
        if r in ("lead", "all", "builder", "researcher", "reviewer")
    }
    R["lead_sendmessage_kinds"] = {
        k: {"n": n, "chars": ch, "mean_chars": round(ch / n), "out_usd": round(out_usd(ch), 2)}
        for k, (n, ch) in sorted(sends.items(), key=lambda kv: -kv[1][0])
    }
    R["lead_sendmessage_recipient_type_known"] = {str(k): v for k, v in known.items()}
    R["dispatch_prompts"] = {
        "spawns": dispatch[0],
        "prompt_chars": dispatch[1],
        "mean_chars": round(dispatch[1] / max(1, dispatch[0])),
        "out_usd": round(out_usd(dispatch[1]), 2),
    }

    # (2) review relay per PR
    per_pr = []
    for acts in relay.values():
        rounds = len(acts.get("reviewer spawn", [])) + len(acts.get("re-review request", []))
        relay_calls = (
            acts.get("review posted by lead", [])
            + acts.get("review findings to author", [])
            + acts.get("re-review request", [])
        )
        turns = {}
        for c in relay_calls:
            turns.setdefault(c["turn"], c["turn_usd"])
        usd = sum(turns.values()) + sum(c["carry_usd"] for c in relay_calls)
        chars = sum(
            c.get("msg_chars", 0) for c in acts.get("review findings to author", []) + acts.get("re-review request", [])
        )
        posted = acts.get("review posted by lead", [])
        kept = {c["turn"] for c in relay_calls if c not in posted}
        # the lead stops posting: a posting turn goes (its whole turn) unless a kept message shares it (then only
        # the comment's own output tokens go); every findings message shrinks to a 200-character pointer
        save = sum(
            (c["turn_usd"] if c["turn"] not in kept else out_usd(c.get("cmd_chars", 0))) + c["carry_usd"]
            for c in posted
        ) + out_usd(sum(max(0, c.get("msg_chars", 0) - 200) for c in acts.get("review findings to author", [])))
        per_pr.append(
            {
                "rounds": rounds,
                "relay_turns": len(turns),
                "usd": usd,
                "chars": chars,
                "save": save,
                "posted": len(posted),
                "posted_chars": sum(c.get("cmd_chars", 0) for c in posted),
                "to_author": len(acts.get("review findings to author", [])),
            }
        )
    rr = [x for x in per_pr if x["rounds"] or x["relay_turns"]]
    R["review_relay_per_pr"] = {
        "prs": len(rr),
        "rounds_total": sum(x["rounds"] for x in rr),
        "rounds_dist": dict(sorted(Counter(min(x["rounds"], 5) for x in rr).items())),
        "relay_turns_total": sum(x["relay_turns"] for x in rr),
        "relay_turns_per_pr_mean": round(sum(x["relay_turns"] for x in rr) / max(1, len(rr)), 2),
        "relay_turns_per_round": round(sum(x["relay_turns"] for x in rr) / max(1, sum(x["rounds"] for x in rr)), 2),
        "lead_posted_review_comments": sum(x["posted"] for x in rr),
        "lead_posted_review_chars": sum(x["posted_chars"] for x in rr),
        "findings_messages_to_author": sum(x["to_author"] for x in rr),
        "relay_usd": round(sum(x["usd"] for x in rr), 2),
        "relay_message_chars": sum(x["chars"] for x in rr),
        "save_upper_usd (lead stops posting; findings message becomes a 200-char pointer)": round(
            sum(x["save"] for x in rr), 2
        ),
    }

    # (1) integrate retries: what changed between consecutive tries on the same PR
    trans = Counter()
    outcome_before = Counter()
    nothing_after = Counter()
    per_pr_tries = Counter()
    sha_known = Counter()  # retries where both tries printed the head SHA (RESULT-JSON)
    gaps = []
    for (repo, pr), xs in tries.items():
        xs.sort(key=lambda x: x[2])
        per_pr_tries[min(len(xs), 6)] += 1
        for a, b in pairwise(xs):
            outcome_before[a[3]] += 1
            same = a[4] == b[4] if a[4] and b[4] else None
            w = between(gh, repo, pr, a[2], b[2])
            gaps.append((ts(b[2] + "Z") - ts(a[2] + "Z")).total_seconds() / 60)
            fa = {f for f in FLAGS if f in a[6]["cmd"]}
            fb = {f for f in FLAGS if f in b[6]["cmd"]}
            if w is None:
                k = "PR not found"
            elif w["new_commit"] or same is False:
                k = "new head commit"
            elif w["new_feedback"]:
                k = "new comment/review only"
            elif fb - fa:
                k = "same head, lead added a flag (" + ",".join(sorted(fb - fa)) + ")"
            else:
                k = "nothing changed (same head, same flags)"
                nothing_after[f"{a[3]}: {refusal_kind(a[5])}" if a[3] == "refused" else a[3]] += 1
            if same is not None:
                sha_known[k] += 1
            trans[k] += 1
    calls = sum(len(v) for v in tries.values())
    lead_int = cats["lead"]["1 integrate loop"]
    R["integrate"] = {
        "integrate_invocations": calls,
        "prs": len(tries),
        "tries_per_pr_dist (6 = 6+)": dict(sorted(per_pr_tries.items())),
        "retries": sum(trans.values()),
        "retry_what_changed": dict(trans.most_common()),
        "outcome_of_try_before_a_retry": dict(outcome_before.most_common()),
        "outcome_before_a_retry_where_nothing_changed": dict(nothing_after.most_common()),
        "retries_with_both_head_shas_printed": dict(sha_known.most_common()),
        "minutes_between_tries_median": round(median(gaps) or 0, 1),
        "outcomes_all": dict(Counter(x[3] for v in tries.values() for x in v).most_common()),
        "flags": {
            f: sum(1 for v in tries.values() for x in v if f in x[6]["cmd"])
            for f in ("--override", "--harness-ok", "--local-ci", "--dry-run")
        },
        "lead_integrate_loop_usd": round(lead_int.usd, 2),
        "lead_integrate_loop_turns": len(lead_int.turns),
    }
    merged = sum(1 for v in tries.values() if any(x[3] in ("merged",) or "merged" in x[3] for x in v))
    R["integrate"]["prs_merged_in_these_tries"] = merged
    R["integrate"]["lead_loop_turns_per_merged_pr"] = round(len(lead_int.turns) / max(1, merged), 2)

    # (4) board new -> what became of the item
    fate = Counter()
    fate_role = defaultdict(Counter)
    for role, repo, n in board_new:
        if repo is None:
            fate["no issue URL in the output"] += 1
            continue
        i = issues.get(repo, {}).get(n)
        if not i:
            k = "not found"
        elif i["state"] == "OPEN":
            k = "open"
        else:
            k = f"closed {i['stateReason']}"
            dup = any(x["__typename"] == "MarkedAsDuplicateEvent" for x in i["timelineItems"]["nodes"])
            close_note = [
                x.get("bodyText", "") for x in i["timelineItems"]["nodes"] if x["__typename"] == "IssueComment"
            ]
            if dup or (
                close_note
                and re.search(
                    r"(?i)duplicate|superseded|covered by|folded into|same as|tracked "
                    r"(upstream|in|as)|subsumed|replaced by",
                    close_note[-1][:400],
                )
            ):
                k += " (duplicate/superseded)"
        fate[k] += 1
        fate_role[role][k] += 1
    R["board_new"] = {
        "calls_with_issue": len([x for x in board_new if x[1]]),
        "fate": dict(fate.most_common()),
        "fate_by_role": {r: dict(v.most_common()) for r, v in fate_role.items()},
        "lead_manual_closes": dict(closes.most_common()),
    }
    all_closed = Counter()
    for _p, v in issues.items():
        for i in v.values():
            if i["state"] != "CLOSED":
                all_closed["open"] += 1
                continue
            notes = [x.get("bodyText", "") for x in i["timelineItems"]["nodes"] if x["__typename"] == "IssueComment"]
            dup = any(x["__typename"] == "MarkedAsDuplicateEvent" for x in i["timelineItems"]["nodes"])
            if dup or (
                notes
                and re.search(
                    r"(?i)duplicate|superseded|covered by|folded into|same as|tracked "
                    r"(upstream|in|as)|subsumed|replaced by",
                    notes[-1][:400],
                )
            ):
                all_closed[f"closed {i['stateReason']} as duplicate/superseded"] += 1
            else:
                all_closed[f"closed {i['stateReason']}"] += 1
    R["all_issues_fate"] = dict(all_closed.most_common())

    # (3) board staleness: in-progress claims idle over 6 h, per window (cron alive / dead)
    R["staleness"] = staleness(issues, args.cron_dead_from)

    # (5) conflicts
    R["conflicts"] = {
        "files_top": conflict_files.most_common(12),
        "file_conflicts": sum(conflict_files.values()),
        "calls_showing_a_conflict_by_role": dict(conflict_roles),
    }
    R["comment_posts"] = dict(sorted(posts.items()))
    R["lead_close_days"] = {k: sorted(v) for k, v in close_days.items()}
    R["owner_decisions"] = dict(owner)
    R["board_new_preceded_by_issue_search"] = dict(dedupe_search)
    R["pr_summary_composition_chars"] = {r: dict(v) for r, v in sumparts.items()}

    # (6) PR summaries in dev pr
    rows = {}
    for role, xs in pr_summaries.items():
        lens = [n for n, _, _ in xs if n is not None]
        rows[role] = {
            "dev_pr_calls": len(xs),
            "with_heredoc_summary": len(lens),
            "summary_chars_median": median(lens),
            "summary_chars_total": sum(lens),
            "summary_out_usd": round(out_usd(sum(lens)), 2),
            "err_rate": round(sum(e for _, e, _ in xs) / max(1, len(xs)), 3),
        }
    R["pr_summaries"] = rows

    # (7) HANDOFF / SESSION writes and (8) CHANGELOG writes
    def files_rows(d):
        return {
            r: {
                "calls": len(xs),
                "turns": len({(c["tsf"], c["turn"]) for c in xs}),
                "chars": sum(c.get("cmd_chars", 0) for c in xs),
                "usd": round(sum(c["turn_usd"] + c["carry_usd"] for c in xs), 2),
                "days": len({c["tsf"][:10] for c in xs}),
            }
            for r, xs in d.items()
        }

    R["handbacks_to_lead"] = {
        r: {"n": n, "chars_mean": round(ch / n), "carry_usd": round(cu, 2), "carry_usd_above_300_chars": round(sv, 2)}
        for r, (n, ch, cu, sv) in sorted(hbacks.items(), key=lambda kv: -kv[1][0])
    }
    R["integrate_loop_turn_split"] = {
        k: {"turns": n, "usd": round(loop_split_usd[k], 2)} for k, n in loop_split.items()
    }
    R["integrate_bursts"] = {
        "bursts": len(bursts),
        "turns": sum(len(b) for b in bursts),
        "turns_per_burst_median": median([len(b) for b in bursts]),
        "turns_beyond_one_per_burst": sum(len(b) - 1 for b in bursts),
    }
    R["handoff"] = files_rows(handoff)
    R["changelog"] = files_rows(changelog)
    R["worktree_claim"] = dict(claim_chain)
    R["reviewer_worktrees"] = dict(reviewer_wt)
    R["issue_view_reads"] = dict(issue_reads)
    json.dump(R, sys.stdout, indent=1, default=str)


NOW = datetime(2026, 10, 20, 17, 49)  # when GH.json was fetched (UTC), so the report is reproducible from GH.json


def staleness(issues, dead_from):
    """Episodes where a claimed in-progress item went over 6 h with no comment (what `board sweep` labels), split
    at `dead_from` (the last successful scheduled board run in repo-E; H has no board workflow)."""
    cut = ts(dead_from)
    out = {}
    for p, v in issues.items():
        eps, claims, ended = Counter(), Counter(), Counter()
        hours_over = defaultdict(float)
        for i in v.values():
            nodes = sorted(i["timelineItems"]["nodes"], key=lambda x: x.get("createdAt") or "")
            start, last = None, None
            end_all = ts(i["closedAt"]) if i["closedAt"] else NOW
            events = []
            for x in nodes:
                t = x["__typename"]
                if t == "LabeledEvent" and x["label"]["name"] == "status:in-progress":
                    events.append((ts(x["createdAt"]), "start"))
                elif (t == "UnlabeledEvent" and x["label"]["name"] == "status:in-progress") or t == "ClosedEvent":
                    events.append((ts(x["createdAt"]), "stop"))
                elif t == "IssueComment":
                    events.append((ts(x["createdAt"]), "comment"))
            events.append((end_all, "end"))
            for when, kind in sorted(events, key=lambda e: e[0]):
                if start is not None and last is not None:
                    idle = (when - last).total_seconds() / 3600
                    if idle > 6:
                        w = "dead" if last + timedelta(hours=6) >= cut else "alive"
                        eps[w] += 1
                        hours_over[w] += idle - 6
                        how = {"comment": "a comment", "stop": "close or status change", "end": "close"}[kind]
                        how = "nothing (still idle now)" if kind == "end" and not i["closedAt"] else how
                        ended[f"{w}: ended by {how}"] += 1
                if kind == "start":
                    start, last = when, when
                    claims["dead" if when >= cut else "alive"] += 1
                elif kind == "comment" and start is not None:
                    last = when
                elif kind in ("stop", "end"):
                    start = last = None
        actors = Counter(
            "github-actions" if (x.get("actor") or {}).get("login") == "github-actions" else "a person or local devkit"
            for i in v.values()
            for x in i["timelineItems"]["nodes"]
            if x["__typename"] in ("LabeledEvent", "UnlabeledEvent")
            and (x["label"]["name"].startswith("status:") or x["label"]["name"] == "stale")
        )
        stale_labels = sum(
            1
            for i in v.values()
            for x in i["timelineItems"]["nodes"]
            if x["__typename"] == "LabeledEvent" and x["label"]["name"] == "stale"
        )
        out[p] = {
            "status_or_stale_label_changes_by_actor": dict(actors),
            "stale_labels_ever_applied": stale_labels,
            "in_progress_starts": dict(claims),
            "idle_over_6h_episodes": dict(eps),
            "episode_end": dict(ended),
            "hours_beyond_6h": {k: round(x, 1) for k, x in hours_over.items()},
        }
    return out


# ---------------------------------------------------------------- GitHub data (fetch)

REPOS = {"repo-E": f"{OWNER}/{REPO_E}", "H": f"{OWNER}/agent-harness"}
PR_QUERY = """query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(first: 40, after: $endCursor, orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number createdAt mergedAt closedAt state headRefName
        commits(last: 100) { nodes { commit { oid committedDate } } }
        comments(first: 100) { nodes { createdAt author { login } } }
        reviews(first: 50) { nodes { submittedAt state } }
        timelineItems(first: 50, itemTypes: [HEAD_REF_FORCE_PUSHED_EVENT, BASE_REF_CHANGED_EVENT]) {
          nodes { __typename ... on HeadRefForcePushedEvent { createdAt } ... on BaseRefChangedEvent { createdAt } }
        }
      }
    }
  }
}"""
ISSUE_QUERY = """query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 40, after: $endCursor, orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title createdAt closedAt state stateReason author { login }
        labels(first: 20) { nodes { name } }
        timelineItems(first: 100, itemTypes: [LABELED_EVENT, UNLABELED_EVENT, ISSUE_COMMENT, CLOSED_EVENT,
                                              REOPENED_EVENT, MARKED_AS_DUPLICATE_EVENT]) {
          nodes {
            __typename
            ... on LabeledEvent { createdAt label { name } actor { login } }
            ... on UnlabeledEvent { createdAt label { name } actor { login } }
            ... on IssueComment { createdAt author { login } bodyText }
            ... on ClosedEvent { createdAt stateReason actor { login } }
            ... on ReopenedEvent { createdAt }
            ... on MarkedAsDuplicateEvent { createdAt }
          }
        }
      }
    }
  }
}"""


def graphql_all(query, repo, key):
    owner, name = repo.split("/")
    out = subprocess.run(
        [
            "gh",
            "api",
            "graphql",
            "--paginate",
            "-f",
            f"query={query}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "--jq",
            f".data.repository.{key}.nodes[]",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [json.loads(x) for x in out.splitlines() if x.strip()]


def fetch(args):
    data = {}
    for proj, repo in REPOS.items():
        data[proj] = {
            "repo": repo,
            "prs": graphql_all(PR_QUERY, repo, "pullRequests"),
            "issues": graphql_all(ISSUE_QUERY, repo, "issues"),
        }
        print(proj, len(data[proj]["prs"]), "PRs", len(data[proj]["issues"]), "issues", file=sys.stderr)
    with open(args.out, "w") as fh:
        json.dump(data, fh)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--cache", required=True)
    e.add_argument("projects", nargs="+")
    e.add_argument("--exclude", action="append", default=[], help="skip transcripts whose path holds this (repeat)")
    f = sub.add_parser("fetch")
    f.add_argument("--out", required=True)
    r = sub.add_parser("report")
    r.add_argument("--cache", required=True)
    r.add_argument("--gh", required=True)
    r.add_argument("--cron-dead-from", default="2026-10-15T17:19:30Z", help="last successful scheduled board run")
    a = ap.parse_args()
    if a.cmd == "extract":
        extract(a)
    elif a.cmd == "fetch":
        fetch(a)
    else:
        report(a)


if __name__ == "__main__":
    main()
