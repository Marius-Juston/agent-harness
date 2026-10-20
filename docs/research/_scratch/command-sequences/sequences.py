"""Mine recurring multi-step command sequences from Claude Code transcripts (#321).

Usage:
    python3 sequences.py extract --cache CACHE.jsonl.gz NAME=PROJECT_DIR [NAME=PROJECT_DIR ...]
    python3 sequences.py report --cache CACHE.jsonl.gz > report.json

`extract` reads every main transcript (`<dir>/*.jsonl`, role "lead") and subagent transcript
(`<dir>/*/subagents/*.jsonl`, role from the `.meta.json` agentType) and keeps one record per tool call: the turn it
was issued in, the context size of that turn, the Bash command, its output size and whether it failed. The cache holds
command text, so keep it outside the repository. `report` prints only normalised labels and counts (no paths, no
command text), so its output can be committed.

Cost model (Opus 5.5 list prices per M tokens, as in harness-efficiency.md section 2; every model is priced at them):
an extra turn re-reads its context (cache read) and writes the command plus 233 hidden output tokens; a tool output is
written to the cache once and re-read on every later turn until the next compaction; 4 characters per token for tool
output, 0.443 tokens per character for the model's own text.
"""

import argparse
import glob
import gzip
import json
import os
import re
import shlex
import sys
from collections import Counter, defaultdict
from itertools import pairwise
from multiprocessing import Pool
from urllib.parse import urlsplit

PRICE = {"out": 20.0, "cr": 0.20, "cw": 8.0}  # $ per M tokens
HIDDEN_OUT = 233  # output tokens per turn not visible in the transcript (harness-efficiency.md section 2)
OUT_TOK_PER_CHAR = 0.443
RESULT_CHARS_PER_TOK = 4.0

# ---------------------------------------------------------------- extraction


def role_of(agent_type):
    if agent_type is None:
        return "lead"
    t = agent_type.split(":")[-1].lower()
    if t.endswith("builder"):
        return "builder"
    if t.endswith("researcher"):
        return "researcher"
    if t.endswith("reviewer") or "auditor" in t:
        return "reviewer"
    if "test" in t:
        return "tester"
    return "other"


def result_text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def result_chars(c):
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        n = 0
        for b in c:
            if not isinstance(b, dict):
                continue
            n += len(b.get("text", "")) if b.get("type") == "text" else (6000 if b.get("type") == "image" else 200)
        return n
    return 0


def error_kind(text):
    t = text[:600]
    if "Refusing to run" in t or "isolated in the worktree" in t:
        return "isolation"
    if "hook" in t.lower() and ("block" in t.lower() or "denied" in t.lower()):
        return "hook"
    if "doesn't want to proceed" in t or "Permission" in t or "permission" in t[:200]:
        return "permission"
    if t.startswith("Exit code"):
        return "exit"
    return "other"


def extract_file(job):
    path, project, agent_type = job
    calls, pending, turn_of, ctx_of, out_of = [], {}, {}, {}, {}
    usage = {"inp": 0, "cw": 0, "cr": 0}
    turns, compactions = 0, []
    with open(path, errors="ignore") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = d.get("type")
            if t == "system" and d.get("subtype") == "compact_boundary":
                compactions.append(turns)
            if t == "assistant":
                m = d.get("message") or {}
                mid = m.get("id")
                if mid not in turn_of:
                    turns += 1
                    turn_of[mid] = turns
                    u = m.get("usage") or {}
                    ctx_of[mid] = (
                        u.get("input_tokens", 0)
                        + u.get("cache_creation_input_tokens", 0)
                        + u.get("cache_read_input_tokens", 0)
                    )
                    for k, f in (
                        ("inp", "input_tokens"),
                        ("cw", "cache_creation_input_tokens"),
                        ("cr", "cache_read_input_tokens"),
                    ):
                        usage[k] += u.get(f, 0)
                out_of[mid] = max(out_of.get(mid, 0), (m.get("usage") or {}).get("output_tokens", 0))
                for b in m.get("content") or []:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    inp = b.get("input") or {}
                    rec = {
                        "turn": turn_of[mid],
                        "ctx": ctx_of[mid],
                        "tool": b.get("name"),
                        "ts": d.get("timestamp", "")[:10],
                        "out": 0,
                        "err": None,
                    }
                    if b.get("name") == "Bash":
                        rec["cmd"] = inp.get("command", "")[:4000]
                        rec["cmd_chars"] = len(inp.get("command", ""))
                    elif b.get("name") == "Read":
                        rec["path"] = inp.get("file_path", "")
                        rec["cmd_chars"] = len(json.dumps(inp))
                    else:
                        rec["cmd_chars"] = len(json.dumps(inp))
                    pending[b.get("id")] = rec
                    calls.append(rec)
            if t == "user":
                c = (d.get("message") or {}).get("content")
                if not isinstance(c, list):
                    continue
                for b in c:
                    if not isinstance(b, dict) or b.get("type") != "tool_result":
                        continue
                    rec = pending.pop(b.get("tool_use_id"), None)
                    if rec is None:
                        continue
                    rec["out"] = result_chars(b.get("content"))
                    txt = result_text(b.get("content"))
                    if b.get("is_error"):
                        rec["err"] = error_kind(txt)
                    if rec["tool"] == "Bash":
                        rec["persisted"] = "<persisted-output>" in txt[:200]
                        rec["head"], rec["tail"] = txt[:400], txt[-400:]
    return {
        "project": project,
        "role": role_of(agent_type),
        "file": os.path.basename(path),
        "turns": turns,
        "compactions": compactions,
        "usage": usage | {"out": sum(out_of.values())},
        "calls": calls,
    }


def extract(args):
    jobs = []
    for spec in args.projects:
        name, d = spec.split("=", 1)
        for f in glob.glob(os.path.join(d, "*.jsonl")):
            jobs.append((f, name, None))
        for f in glob.glob(os.path.join(d, "*", "subagents", "*.jsonl")):
            meta = f[:-6] + ".meta.json"
            at = "unknown"
            if os.path.exists(meta):
                with open(meta) as fh:
                    at = json.load(fh).get("agentType", "unknown")
            jobs.append((f, name, at))
    with Pool(max(1, (os.cpu_count() or 2) - 1)) as pool, gzip.open(args.cache, "wt") as out:
        for rec in pool.imap_unordered(extract_file, jobs, chunksize=4):
            out.write(json.dumps(rec) + "\n")
    print(f"{len(jobs)} transcripts -> {args.cache}", file=sys.stderr)


# ---------------------------------------------------------------- normalisation

HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")
KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "time", "{", "(", "exec"}
NOISE = {"done", "fi", "}", ")", "true", "false", ":", "esac"}
DEV_GROUPS = {"board", "code", "lit", "research", "reuse", "consolidation", "suite-audit", "eval"}
UV_ARG_OPTS = {"--directory", "--project", "--with", "--package", "--extra", "--group", "--python", "-p"}


def strip_heredocs(cmd):
    out, delims = [], []
    for line in cmd.split("\n"):
        if delims:
            if line.strip() == delims[0]:
                delims.pop(0)
            continue
        out.append(line)
        delims += HEREDOC.findall(line)
    return "\n".join(out)


def split_steps(cmd):
    """[(op, text)]: op is the operator before the step ('' first, '|' pipe, '&&', '||', ';')."""
    s = strip_heredocs(cmd)
    steps, buf, op = [], [], ""
    quote, depth, i = None, 0, 0
    while i < len(s):
        ch = s[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(s):
                buf.append(s[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < len(s):
            buf.append(s[i : i + 2])
            i += 2
            continue
        if ch == "$" and s[i + 1 : i + 2] == "(":
            depth += 1
            buf.append("$(")
            i += 2
            continue
        if ch == ")" and depth:
            depth -= 1
            buf.append(ch)
            i += 1
            continue
        if depth == 0:
            two = s[i : i + 2]
            if two in ("&&", "||"):
                steps.append((op, "".join(buf)))
                buf, op = [], two
                i += 2
                continue
            if ch == "|" and s[i - 1 : i] != ">":
                steps.append((op, "".join(buf)))
                buf, op = [], "|"
                i += 1
                continue
            if ch in ";\n":
                steps.append((op, "".join(buf)))
                buf, op = [], ";"
                i += 1
                continue
        buf.append(ch)
        i += 1
    steps.append((op, "".join(buf)))
    return [(o, t.strip()) for o, t in steps if t.strip()]


def words_of(text):
    try:
        return shlex.split(text, comments=False)
    except ValueError:
        return text.split()


def gh_api_label(words):
    ep = next((w for w in words[2:] if (not w.startswith("-") and "/" in w) or w == "graphql"), "")
    if ep == "graphql":
        return "gh api graphql"
    ep = re.sub(r"^/?repos/[^/]+/[^/]+/?", "", ep.split("?")[0])
    segs = [x for x in ep.split("/") if x and not re.fullmatch(r"[0-9a-f]{7,40}|\d+|\{[^}]*\}|:\w+", x)]
    return "gh api " + "/".join(segs[:2]) if segs else "gh api"


def _label(text):
    """A step's normalised label: command + subcommand words, no arguments (unfiltered; use `label`)."""
    w = words_of(text)
    while w and (w[0] in KEYWORDS or re.fullmatch(r"\w+=\S*", w[0])):
        w = w[1:]
    if not w:
        return ""
    w0 = os.path.basename(w[0])
    if w0 in NOISE:
        return ""
    if w0 in ("timeout", "env", "nice", "xargs", "command"):
        j = 1  # drop the wrapper's own options and duration, keep the wrapped command intact
        while j < len(w) and (w[j].startswith("-") or re.fullmatch(r"[\d.]+[smh]?|\w+=\S*|\{\}", w[j])):
            j += 2 if w[j] in ("-I", "-n", "-P", "-L", "-s", "-k") else 1
        inner = label(shlex.join(w[j:])) if j < len(w) else ""
        return f"xargs {inner}".strip() if w0 == "xargs" else (inner or w0)
    if w0 == "uv" and len(w) > 1:
        if w[1] != "run":
            return "uv " + w[1]
        j = 2
        while j < len(w) and w[j].startswith("-"):
            j += 2 if w[j] in UV_ARG_OPTS else 1
        return label(shlex.join(w[j:])) if j < len(w) else "uv run"
    if w0 == "dev" or w0.endswith("/dev"):
        parts = ["dev"]
        if len(w) > 1 and re.fullmatch(r"[a-z][a-z-]*", w[1]):
            parts.append(w[1])
            if w[1] in DEV_GROUPS and len(w) > 2 and re.fullmatch(r"[a-z][a-z-]*", w[2]):
                parts.append(w[2])
        return " ".join(parts)
    if re.fullmatch(r"python[\d.]*", w0):
        if "-c" in w:
            body = text
            if "sys.stdin" in body and "json" in body:
                return "python -c json<stdin"
            return "python -c json" if "json" in body else "python -c"
        if "-m" in w:
            i = w.index("-m")
            return "python -m " + (w[i + 1] if i + 1 < len(w) else "")
        if len(w) > 1 and w[1] == "-":
            return "python heredoc"
        return "python script" if len(w) > 1 else "python"
    if w0 == "git":
        j = 1
        while j < len(w) and w[j].startswith("-"):
            j += 2 if w[j] in ("-C", "-c") else 1
        if j >= len(w):
            return "git"
        v = w[j]
        if v in ("worktree", "stash", "remote", "submodule") and j + 1 < len(w) and not w[j + 1].startswith("-"):
            return f"git {v} {w[j + 1]}"
        return f"git {v}"
    if w0 == "gh":
        rest = []
        skip = False
        for x in w[1:]:
            if skip:
                skip = False
                continue
            if x in ("-R", "--repo"):
                skip = True
                continue
            rest.append(x)
        if rest and rest[0] == "api":
            return gh_api_label(["gh", *rest])
        return "gh " + " ".join(x for x in rest[:2] if not x.startswith("-"))
    if w0 == "sed":
        return "sed -n" if "-n" in w else ("sed -i" if any(x.startswith("-i") for x in w) else "sed")
    if w0 in ("bash", "sh") and "-c" in w:
        return "bash -c"
    if w0 in ("npm", "npx", "pnpm", "cargo", "docker", "claude"):
        return w0 + (" " + w[1] if len(w) > 1 and not w[1].startswith("-") and "/" not in w[1] else "")
    if w0 == "printf":
        return "echo"
    return w0 if re.fullmatch(r"[\w.+-]+", w0) else "?"


# Labels that reach the report: public command names only, so a private tool or script name never prints.
PUBLIC = set(
    [
        "grep",
        "head",
        "cd",
        "sed",
        "git",
        "python",
        "tail",
        "cat",
        "dev",
        "echo",
        "ls",
        "gh",
        "pytest",
        "cut",
        "ruff",
        "for",
        "curl",
        "wc",
        "mkdir",
        "sleep",
        "sort",
        "awk",
        "cp",
        "find",
        "rm",
        "tr",
        "pgrep",
        "npx",
        "uniq",
        "file",
        "uv",
        "bash",
        "uvx",
        "base64",
        "claude",
        "diff",
        "tee",
        "npm",
        "date",
        "ps",
        "node",
        "break",
        "which",
        "export",
        "nvidia-smi",
        "pdftotext",
        "mv",
        "kill",
        "du",
        "xargs",
        "tar",
        "pkill",
        "jq",
        "free",
        "sha256sum",
        "uptime",
        "chmod",
        "set",
        "time",
        "read",
        "paste",
        "nproc",
        "psql",
        "ln",
        "nohup",
        "ty",
        "mutool",
        "df",
        "sqlite3",
        "stat",
        "ss",
        "strings",
        "wait",
        "fold",
        "unzip",
        "cmp",
        "env",
        "test",
        "wget",
        "pip",
        "pnpm",
        "cargo",
        "docker",
        "make",
        "rg",
        "fd",
        "tree",
        "touch",
        "source",
        "less",
        "basename",
        "dirname",
        "realpath",
        "readlink",
        "md5sum",
        "gzip",
        "zcat",
        "column",
        "comm",
        "nl",
        "od",
        "xxd",
        "timeout",
        "watch",
        "whoami",
        "hostname",
        "uname",
        "lsof",
        "shellcheck",
        "mypy",
        "go",
        "ssh",
        "scp",
        "rsync",
        "eval",
        "exit",
        "continue",
        "local",
        "declare",
        "type",
        "command",
        "jobs",
        "ulimit",
        "printf",
        "[",
        "[[",
    ]
)
PY_MODULES = {"pytest", "json", "json.tool", "http.server", "pip", "venv", "ruff", "mypy", "devkit", "py_compile"}
GH_API_WORDS = set(
    [
        "pulls",
        "issues",
        "comments",
        "reviews",
        "commits",
        "check-runs",
        "check-suites",
        "actions",
        "runs",
        "jobs",
        "logs",
        "branches",
        "protection",
        "required_status_checks",
        "contents",
        "releases",
        "search",
        "user",
        "repos",
        "git",
        "refs",
        "heads",
        "statuses",
        "status",
        "labels",
        "milestones",
        "rate_limit",
        "notifications",
        "compare",
        "timeline",
        "events",
        "sub_issues",
        "dependencies",
        "blocked_by",
        "collaborators",
        "rulesets",
        "hooks",
        "tags",
        "forks",
        "topics",
        "files",
        "merge",
        "requested_reviewers",
        "assignees",
        "workflows",
        "artifacts",
        "caches",
        "environments",
        "deployments",
        "pages",
        "code-scanning",
        "alerts",
        "dependabot",
        "secret-scanning",
        "projects",
        "billing",
        "usage",
    ]
)


def label(text):
    """`_label` with every name that is not public replaced: 'script' (a .sh/.py path), else 'other'."""
    lb = _label(text)
    if not lb:
        return lb
    head = lb.split()[0]
    if head not in PUBLIC:
        return "script" if re.search(r"\.(sh|py)$", head) else "other"
    if lb.startswith("python -m ") and lb.split()[2] not in PY_MODULES:
        return "python -m other"
    if lb.startswith("gh api ") and lb != "gh api graphql":
        segs = lb[7:].split("/")
        return "gh api " + "/".join(x if x in GH_API_WORDS else "x" for x in segs)
    if head in ("npm", "npx", "pnpm", "cargo", "docker", "claude"):
        return head
    return lb


SETUP = {"cd", "export", "source", "set", "echo", ""}


def call_shape(cmd):
    steps = [(op, label(t), t) for op, t in split_steps(cmd)]
    core = next((lb for _, lb, _ in steps if lb not in SETUP), "echo" if steps else "")
    pipes = []
    for (_, a, _), (op, b, t) in pairwise(steps):
        if op == "|":
            pipes.append((a, b, t))
    loop = bool(re.search(r"\b(until|while)\b", cmd)) and "sleep" in cmd
    return steps, core, pipes, loop


# ---------------------------------------------------------------- cost model


def turn_cost(ctx, cmd_chars):
    return (ctx * PRICE["cr"] + (HIDDEN_OUT + cmd_chars * OUT_TOK_PER_CHAR) * PRICE["out"]) / 1e6


def carry_cost(out_chars, remaining):
    return out_chars / RESULT_CHARS_PER_TOK * (PRICE["cw"] + PRICE["cr"] * remaining) / 1e6


# ---------------------------------------------------------------- report


def load(cache):
    with gzip.open(cache, "rt") as fh:
        for line in fh:
            yield json.loads(line)


def annotate(tr):
    """Per call: label token, remaining turns before the next compaction, $ of its turn and of its output."""
    bounds = [*sorted(tr["compactions"]), tr["turns"]]
    for c in tr["calls"]:
        nxt = next((b for b in bounds if b >= c["turn"]), tr["turns"])
        c["remaining"] = max(0, nxt - c["turn"])
        c["turn_usd"] = turn_cost(c["ctx"], c.get("cmd_chars", 0))
        c["carry_usd"] = carry_cost(c["out"], c["remaining"])
        if c["tool"] == "Bash":
            steps, core, pipes, loop = call_shape(c.get("cmd", ""))
            c["steps"], c["core"], c["pipes"], c["loop"] = steps, core, pipes, loop
            c["tok"] = ("poll " if loop else "") + core
        elif c["tool"] and c["tool"].startswith("mcp__"):
            c["tok"] = "mcp " + c["tool"].split("__")[1]
        else:
            c["tok"] = c["tool"] or "?"
    return tr


def occ_cost(calls):
    """Modelled $ of a run of calls (each distinct turn once) and the saving if one call printing the last call's
    output replaced it (an upper bound: it assumes the intermediate outputs were not needed)."""
    turns = {}
    for c in calls:
        turns.setdefault(c["turn"], c["turn_usd"])
    cost = sum(turns.values()) + sum(c["carry_usd"] for c in calls)
    keep = calls[-1]["turn_usd"] + calls[-1]["carry_usd"]
    return cost, max(0.0, cost - keep)


class Tally:
    def __init__(self):
        self.n = 0
        self.files = set()
        self.roles = Counter()
        self.projects = Counter()
        self.calls = 0
        self.err = 0
        self.usd = 0.0
        self.save = 0.0
        self.out = 0

    def add(self, tr, calls, save=None):
        self.n += 1
        self.files.add(tr["file"])
        self.roles[tr["role"]] += 1
        self.projects[tr["project"]] += 1
        self.calls += len(calls)
        self.err += any(c["err"] for c in calls)
        cost, sv = occ_cost(calls)
        self.usd += cost
        self.save += sv if save is None else save
        self.out += sum(c["out"] for c in calls)

    def merge(self, o):
        self.n += o.n
        self.files |= o.files
        self.roles.update(o.roles)
        self.projects.update(o.projects)
        self.calls += o.calls
        self.err += o.err
        self.usd += o.usd
        self.save += o.save
        self.out += o.out

    def row(self):
        return {
            "n": self.n,
            "transcripts": len(self.files),
            "roles": dict(self.roles.most_common()),
            "projects": dict(self.projects.most_common()),
            "calls_per": round(self.calls / self.n, 2) if self.n else 0,
            "err_rate": round(self.err / self.n, 3) if self.n else 0,
            "usd": round(self.usd, 2),
            "save_usd_upper": round(self.save, 2),
            "out_chars_per": round(self.out / self.n) if self.n else 0,
        }


def bash_calls(tr):
    return [c for c in tr["calls"] if c["tool"] == "Bash"]


def paths_in(text):
    return set(re.findall(r"[\w./-]+\.(?:py|md|toml|json|ts|tsx|js|yml|yaml|txt|sh|cfg|ini|jinja)\b", text))


def labs(c):
    return [lb for _, lb, _ in c.get("steps", [])]


CURL_KIND = [  # (kind, registrable domains): a URL's host matches a domain or one of its subdomains
    ("GitHub REST API", ("api.github.com",)),
    (
        "package registry (PyPI, npm, crates)",
        ("pypi.org", "registry.npmjs.org", "crates.io", "ecosyste.ms", "deps.dev"),
    ),
    (
        "literature API (OpenAlex, Crossref, Europe PMC, S2, arXiv)",
        ("openalex.org", "crossref.org", "ebi.ac.uk", "semanticscholar.org", "arxiv.org"),
    ),
    ("Hugging Face API", ("huggingface.co",)),
    ("raw GitHub file", ("raw.githubusercontent.com",)),
]
URL = re.compile(r"https?://[^\s'\"<>`|;)]+")


def url_hosts(cmd):
    """The hostnames of the http(s) URLs written in a command, parsed rather than matched as substrings."""
    out = set()
    for u in URL.findall(cmd):
        try:
            host = urlsplit(u).hostname
        except ValueError:
            continue
        if host:
            out.add(host.lower())
    return out


def on_domain(host, domain):
    return host == domain or host.endswith("." + domain)


def curl_kind(cmd):
    hosts = url_hosts(cmd)
    for name, domains in CURL_KIND:
        if any(on_domain(h, d) for h in hosts for d in domains):
            return name
    return "other host"


def integrate_outcome(c):
    """integrate's own outcome: the last RESULT-JSON, else the last `RESULT:` line that is not the gate's."""
    t = c.get("head", "") + c.get("tail", "")
    if js := re.findall(r'"outcome": "([\w-]+)"', t):
        return js[-1]
    rs = re.findall(r"RESULT: ([a-z][\w-]*)", t)
    own = [x for x in rs if x not in ("passed", "failed")]
    return own[-1] if own else (f"gate {rs[-1]} only" if rs else "?")


def sed_window(cmd):
    m = re.search(r"sed -n ['\"]?(\d+),(\d+)p", cmd)
    return (int(m.group(1)), int(m.group(2))) if m else None


def grep_hits(c):
    """Line numbers grep -n printed for a single file ('N:' at line start) or several ('path:N:')."""
    return {int(x) for x in re.findall(r"(?m)^(?:[^\s:]+:)?(\d+)[:-]", c.get("head", ""))}


def detectors(tr, D):
    """The sequences #321 names, counted exactly; each D[name] is a Tally. `save` passed explicitly is the modelled
    saving of the replacement the note proposes; otherwise Tally uses occ_cost's upper bound."""
    calls = tr["calls"]
    bc = bash_calls(tr)
    seen_integrate = Counter()
    for i, c in enumerate(calls):
        if c["tool"] != "Bash":
            continue
        cmd, lb = c.get("cmd", ""), labs(c)
        # A. ad-hoc JSON filters over a command's output: counted once per call, by first producer and consumer
        fl = [(a, b) for a, b, _ in c["pipes"] if b in ("python -c json<stdin", "jq")]
        if fl:
            a, b = fl[0]
            prod = f"curl ({curl_kind(cmd)})" if a == "curl" else a
            D[f"A json filter ({b}) <- {prod}"].add(tr, [c])
            D[f"A json filter ({b}): all calls"].add(tr, [c])
            D["A json filter: all calls"].add(tr, [c])
        if "curl" in lb and "api.github.com" in url_hosts(cmd):
            D["A curl to the GitHub REST API: all calls"].add(tr, [c])
            if re.search(r"rate limit|API rate|\b403\b", c.get("head", "") + c.get("tail", "")):
                D["A curl to the GitHub REST API: rate-limit message or 403 in output"].add(tr, [c])
        if re.search(r"\bgh\b.*--json", cmd):
            D["A gh --json: all"].add(tr, [c])
            if re.search(r"(-q|--jq)\s", cmd):
                D["A gh --json with built-in --jq/-q"].add(tr, [c])
        # B. dev code outline X, then a window of X within the next 3 calls
        if "dev code outline" in lb:
            ps = paths_in(cmd)
            D["B outline: all"].add(tr, [c])
            for n in calls[i + 1 : i + 4]:
                if n["tool"] == "Bash" and "dev code show" in labs(n):
                    D["B outline -> dev code show"].add(tr, [c, n], save=0.0)
                    break
                if n["tool"] == "Bash" and "sed -n" in labs(n) and paths_in(n["cmd"]) & ps:
                    D["B outline -> sed -n window, same file"].add(tr, [c, n], save=0.0)
                    break
                if n["tool"] == "Read" and any(n.get("path", "").endswith(p.lstrip("./")) for p in ps):
                    D["B outline -> Read, same file"].add(tr, [c, n], save=0.0)
                    break
        # C. grep -n FILE (hits are parsed from the first 400 output characters only: a lower bound), then sed -n a,bp on the same file around a hit, in the next call
        if (
            "grep" in lb
            and i + 1 < len(calls)
            and calls[i + 1]["tool"] == "Bash"
            and re.search(r"grep\b[^|]*-\w*n", cmd)
        ):
            n = calls[i + 1]
            win = sed_window(n.get("cmd", ""))
            if win and paths_in(cmd) & paths_in(n["cmd"]):
                if c["out"] > 800:
                    D["C grep -n -> sed -n, same file, grep output over 800 chars"].add(tr, [c, n], save=0.0)
                D["C grep -n -> sed -n, same file"].add(tr, [c, n], save=n["turn_usd"])
                if any(win[0] <= h <= win[1] for h in grep_hits(c)):
                    D["C grep -n -> sed -n, same file, window holds a hit"].add(tr, [c, n], save=n["turn_usd"])
                    # the one-call alternative (grep -n -C k) prints a window of that size around every hit
                    head = c.get("head", "")
                    line_len = max(20, len(head) / max(1, head.count("\n")))
                    hits = max(1, round(c["out"] / line_len))
                    net = n["turn_usd"] + c["carry_usd"] + n["carry_usd"] - carry_cost(hits * n["out"], c["remaining"])
                    D[f"C one-call grep -C instead: net saving {'>' if net > 0 else '<='} 0"].add(tr, [c, n], save=net)
        # D. gate and tool output trimmed by a pipe (isolation refusals and masked exit codes)
        for name in ("dev check", "dev test", "dev pr", "dev integrate", "dev cite-check"):
            if name in lb:
                filt = [b for a, b, _ in c["pipes"] if a == name]
                D[f"D {name}: all"].add(tr, [c])
                if filt:
                    D[f"D {name} | {filt[0]}"].add(tr, [c])
        if "pytest" in lb:
            D["D pytest direct (not dev test)"].add(tr, [c])
        # E. persisted output, then read back
        if c.get("persisted"):
            D["E persisted output: all"].add(tr, [c])
            for n in calls[i + 1 : i + 4]:
                if (n["tool"] == "Read" and "tool-results" in n.get("path", "")) or (
                    n["tool"] == "Bash" and "tool-results" in n.get("cmd", "")
                ):
                    how = "Read" if n["tool"] == "Read" else n["core"]
                    D[f"E persisted -> read back by {how}"].add(tr, [c, n], save=0.0)
                    D[f"E persisted, producer {c['core']}"].add(tr, [c, n], save=0.0)
                    break
        # F. waiting: polling loops and bare sleeps
        if c["loop"]:
            polled = next((x for x in lb if x not in SETUP | {"sleep", "grep", "head", "tail", "[", "[[", "test"}), "?")
            D[f"F poll loop on {polled}"].add(tr, [c], save=0.0)
        elif "sleep" in lb:
            D["F bare sleep"].add(tr, [c], save=0.0)
        # G. integrate: outcome and reruns on the same PR
        if "dev integrate" in lb:
            pr = re.search(r"dev integrate\s+(\d+)", cmd)
            key = pr.group(1) if pr else "?"
            seen_integrate[key] += 1
            D[f"G integrate outcome {integrate_outcome(c)}"].add(tr, [c], save=0.0)
            if seen_integrate[key] > 1:
                D["G integrate rerun on a PR already tried this session"].add(tr, [c], save=0.0)
            text = c.get("head", "") + c.get("tail", "")
            if "unfinished feedback" in text:
                why = "pending check" if "pending" in text else ("review thread" if "thread" in text else "other")
                D[f"G integrate refused for unfinished feedback: {why}"].add(tr, [c], save=0.0)
            if re.search(r"backgrounded|running in background|notified when it completes", text):
                D["G integrate run in the background"].add(tr, [c], save=0.0)
            if re.search(r"gh pr checks[^;&|]*--watch|until[^;]*gh pr (checks|view)", cmd):
                D["G checks wait + integrate in one call"].add(tr, [c], save=0.0)
    for j, c in enumerate(bc):
        lb = labs(c)
        # K. a dev command's --help, then that command within 3 Bash calls
        for _, hl, t in c["steps"]:
            if hl.startswith("dev") and re.search(r"(?<!\S)(--help|-h)(?!\S)", t):
                D[f"K {hl} --help: all"].add(tr, [c], save=c["turn_usd"])
                nxt = next((n for n in bc[j + 1 : j + 4] if hl in labs(n)), None)
                if nxt:
                    D["K dev --help -> same command (<=3 calls)"].add(tr, [c, nxt], save=c["turn_usd"])
                break
        # H. lead dispatch prep: worktree add + board claim, in one call or within 3 Bash calls
        if "git worktree add" in lb:
            D["H git worktree add: all"].add(tr, [c], save=0.0)
            if "dev board claim" in lb:
                D["H worktree add + board claim in one call"].add(tr, [c], save=0.0)
            else:
                win = bc[max(0, j - 3) : j + 4]
                part = [w for w in win if w is not c and "dev board claim" in labs(w)]
                if part:
                    D["H worktree add + board claim in separate calls (<=3)"].add(tr, [c, part[0]])
        # G2. reading checks, then integrate in a later call
        if "gh pr checks" in lb or ("gh pr view" in lb and "statusCheckRollup" in c["cmd"]):
            D["G pr checks/status read: all"].add(tr, [c], save=0.0)
            if "dev integrate" not in lb:
                hit = next((n for n in bc[j + 1 : j + 4] if "dev integrate" in labs(n)), None)
                if hit:
                    D["G checks read -> integrate in a later call (<=3)"].add(tr, [c, hit])
        # I. reviewer: PR metadata then diff
        if "gh pr diff" in lb:
            D["I gh pr diff: all"].add(tr, [c], save=0.0)
            if j and "gh pr view" in labs(bc[j - 1]):
                D["I gh pr view -> gh pr diff"].add(tr, [bc[j - 1], c])
        # J. body edit loops: read a PR/issue body, edit it, write it back
        if re.search(r"gh (pr|issue) edit", c["cmd"]) and re.search(r"--body", c["cmd"]):
            D["J gh pr/issue edit --body: all"].add(tr, [c], save=0.0)
            rd = [p for p in bc[max(0, j - 4) : j] if re.search(r"gh (pr|issue) view", p["cmd"]) and "body" in p["cmd"]]
            if rd:
                D["J body read -> edit -> body write (<=4 calls)"].add(tr, bc[bc.index(rd[0]) : j + 1])


def ngrams(tr, N, roles_n):
    toks = []
    for c in tr["calls"]:
        if toks and toks[-1][0] == c["tok"]:
            toks[-1][1].append(c)
            continue
        toks.append((c["tok"], [c]))
    for n in (2, 3, 4):
        for k in range(len(toks) - n + 1):
            win = toks[k : k + n]
            nb = sum(1 for t, cs in win if cs[0]["tool"] == "Bash")
            if nb < 2:
                continue
            key = " > ".join(t + ("*" if len(cs) > 1 else "") for t, cs in win)
            flat = [c for _, cs in win for c in cs]
            N[n][key].add(tr, flat)
            roles_n[tr["role"]][n][key].add(tr, flat)


def report(args):
    overview = defaultdict(Counter)
    lead_sessions = []
    turn_usd = defaultdict(list)
    spend = defaultdict(Counter)
    days = Counter()
    pipes = defaultdict(lambda: defaultdict(Tally))
    sigs = defaultdict(lambda: defaultdict(Tally))
    errs = defaultdict(Counter)
    D = defaultdict(Tally)
    N = {n: defaultdict(Tally) for n in (2, 3, 4)}
    roles_n = defaultdict(lambda: {n: defaultdict(Tally) for n in (2, 3, 4)})
    retries = defaultdict(Counter)
    flags = Counter()
    for tr in load(args.cache):
        if args.projects and tr["project"] not in args.projects.split(","):
            continue
        tr = annotate(tr)
        r = tr["role"]
        overview[tr["project"]][r] += 1
        overview["_bash"][r] += len(bash_calls(tr))
        overview["_calls"][r] += len(tr["calls"])
        overview["_turns"][r] += tr["turns"]
        u = tr["usage"]
        usd = (
            u["inp"] * 4.0
            + u["cw"] * PRICE["cw"]
            + u["cr"] * PRICE["cr"]
            + (u["out"] + HIDDEN_OUT * tr["turns"]) * PRICE["out"]
        ) / 1e6
        spend[tr["project"]][r] += usd
        spend["all"][r] += usd
        spend["all"]["all"] += usd
        bc = bash_calls(tr)
        for c in tr["calls"]:
            if c["ts"]:
                days[c["ts"]] += 1
        for c in bc:
            turn_usd[r].append(c["turn_usd"])
        for j, c in enumerate(bc):
            for a, b, _ in c["pipes"]:
                pipes[r][f"{a} | {b}"].add(tr, [c])
                pipes["all"][f"{a} | {b}"].add(tr, [c])
            sig = " ".join((op + " " if op else "") + lb for op, lb, _ in c["steps"] if lb not in ("cd", ""))
            sigs[r][sig].add(tr, [c])
            if c["err"]:
                errs[r][c["err"]] += 1
                if any(n["core"] == c["core"] for n in bc[j + 1 : j + 3]):
                    retries[r][c["core"]] += 1
            for _, lb, t in c["steps"]:
                if lb.startswith("dev "):
                    for f in re.findall(r"(?<!\S)(--[a-z][\w-]*)", t):
                        flags[f"{lb} {f}"] += 1
        dd = defaultdict(Tally)
        detectors(tr, dd)
        for k, v in dd.items():
            D[k].merge(v)
        if r == "lead" and dd:
            ds = sorted({c["ts"] for c in tr["calls"] if c["ts"]})
            lead_sessions.append(
                {"project": tr["project"], "first": ds[0], "last": ds[-1], "turns": tr["turns"]}
                | {k: v.n for k, v in sorted(dd.items()) if k.startswith(("G ", "H ", "D dev integrate: all"))}
            )
        ngrams(tr, N, roles_n)

    def top(d, k, key="usd"):
        rows = sorted(d.items(), key=lambda kv: -getattr(kv[1], key) if key != "n" else -kv[1].n)
        return [{"seq": s, **t.row()} for s, t in rows[:k]]

    out = {
        "window": [min(days), max(days)] if days else None,
        "transcripts": {k: dict(v) for k, v in overview.items()},
        "modelled_usd": {k: {r: round(x, 1) for r, x in v.items()} for k, v in spend.items()},
        "errors_by_kind": {k: dict(v) for k, v in errs.items()},
        "retries_by_core": {k: v.most_common(15) for k, v in retries.items()},
        "bash_turn_usd_mean_median": {
            r: [round(sum(v) / len(v), 4), round(sorted(v)[len(v) // 2], 4)] for r, v in turn_usd.items()
        },
        "lead_sessions": sorted(lead_sessions, key=lambda x: x["first"]),
        "detectors": {k: v.row() for k, v in sorted(D.items(), key=lambda kv: -kv[1].usd)},
        "pipes_top_by_n": {r: top(v, 15, "n") for r, v in pipes.items()},
        "call_shapes_top_by_usd": {r: top(v, 10) for r, v in sigs.items()},
        "ngrams_top_by_save": {n: top(v, 15, "save") for n, v in N.items()},
        "ngrams_top_by_score_per_role": {
            r: sorted(
                (
                    {"seq": k, "score": round(t.usd * (1 + t.err / t.n), 2), **t.row()}
                    for x in d.values()
                    for k, t in x.items()
                ),
                key=lambda row: -row["score"],
            )[:8]
            for r, d in roles_n.items()
        },
        "ngrams_top_by_n_per_role": {r: {n: top(v, 8, "n") for n, v in d.items()} for r, d in roles_n.items()},
        "dev_flags": flags.most_common(40),
    }
    json.dump(out, sys.stdout, indent=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--cache", required=True)
    e.add_argument("projects", nargs="+", help="NAME=DIR, e.g. repo-E=<path to its Claude Code project folder>")
    r = sub.add_parser("report")
    r.add_argument("--cache", required=True)
    r.add_argument("--projects", help="comma-separated NAMEs to keep (default: all)")
    a = ap.parse_args()
    extract(a) if a.cmd == "extract" else report(a)


if __name__ == "__main__":
    main()
