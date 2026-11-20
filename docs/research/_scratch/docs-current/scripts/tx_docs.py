"""Where agents lose time to missing or stale docs, from Claude Code transcripts (#497).

Usage: python3 tx_docs.py <project_transcript_dir> [...] [--dump OUT.jsonl] [--since YYYY-MM-DD]

Per directory it prints:
  A. orientation: for builder/tester runs, tool calls and minutes before the first Edit/Write, by call class;
  B. failure signatures in tool results (is_error or a known failure string in the output), by class, with the
     minutes and calls until the next call that does not fail (the recovery cost);
  C. `--help` calls and `dev code` navigation calls;
  D. assistant sentences naming a doc and a missing/stale word (candidates for hand coding).
Text of every candidate goes only to --dump (one JSON object per line), never to stdout.
"""

import json
import pathlib
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}

GUARD = re.compile(
    r"This agent is isolated in the worktree|PreToolUse:\w+ hook error|Blocked by project rule|"
    r"Permission for this action was denied|auto mode classifier|doesn't want to proceed"
)
CLI = re.compile(
    r"unrecognized arguments|invalid choice|error: the following arguments are required|"
    r"Unknown JSON field|unknown flag|unknown command|unknown shorthand flag|No such option|"
    r"error: unexpected argument|usage: dev "
)
PATH = re.compile(r"No such file or directory|File does not exist|does not exist\b|cannot access|not found in")
API = re.compile(
    r"ImportError|ModuleNotFoundError|AttributeError|has no attribute|NameError|"
    r"TypeError: .*(argument|keyword)|cannot import name"
)
GATE = re.compile(
    r"FAILED|AssertionError|ruff check|RESULT: failed|Found \d+ error|F821|SIM\d+|"
    r"would reformat|invalid-syntax|E\d{3} "
)
TOOLERR = re.compile(
    r"<tool_use_error>|InputValidationError|File has been modified since read|"
    r"must be read|String to replace not found|Found \d+ matches of the string"
)

SUBJ = re.compile(
    r"\b(CLAUDE\.md|AGENTS\.md|HARNESS|README|docstring|the (doc|docs|spec|note|brief|help|--help)|"
    r"agent-surface|module map|documentation|documented|undocumented)\b",
    re.I,
)
MISS = re.compile(
    r"\b(stale|outdated|out of date|no longer|undocumented|not documented|isn't documented|is not documented|"
    r"doesn't (say|mention|document|exist|match)|does not (say|mention|document|exist|match)|"
    r"(is|are|was) (wrong|incorrect|obsolete|missing)|contradict\w*|misleading|nowhere|"
    r"(says|claims|states) .{0,80}\bbut\b)",
    re.I,
)
SENT = re.compile(r"(?<=[.!?\n])\s+")


def ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def text_of(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


def call_class(name, inp):
    """Coarse class of a pre-edit call."""
    cmd = inp.get("command", "") if isinstance(inp, dict) else ""
    path = (inp.get("file_path") or inp.get("path") or "") if isinstance(inp, dict) else ""
    if name == "Bash":
        if re.search(r"dev board brief|dev orient|gh issue view", cmd):
            return "brief"
        if re.search(r"--help\b|\s-h\b", cmd):
            return "help"
        if re.search(r"dev code (outline|show|refs|find)", cmd):
            return "code-nav"
        if re.search(r"\b(cat|sed -n|head|tail|less)\b[^|;&]*\.(md|txt)\b", cmd):
            return "doc-read"
        if re.search(r"\b(grep|rg|git grep|find|ls|tree)\b", cmd):
            return "search"
        if re.search(r"\b(cat|sed -n|head|tail)\b[^|;&]*\.(py|toml|json|ya?ml|sh|jinja)\b", cmd):
            return "code-read"
        if re.search(r"\bgit (log|show|diff|status|blame)\b", cmd):
            return "git-history"
        if re.search(r"dev (test|check)|pytest", cmd):
            return "run-tests"
        return "bash-other"
    if name == "Read":
        return "doc-read" if re.search(r"\.(md|txt)$", path) else "code-read"
    if name in ("Grep", "Glob", "LSP"):
        return "search"
    return "other"


def failure_class(text, is_error):
    if GUARD.search(text[:400]):
        return "guard"
    if TOOLERR.search(text[:400]):
        return "tool-misuse"
    if CLI.search(text):
        return "cli-usage"
    if API.search(text):
        return "api"
    if PATH.search(text):
        return "path"
    if is_error and GATE.search(text):
        return "gate"
    if is_error:
        return "other-error"
    return None


READS = re.compile(
    r"^\s*(cd [^;&]*(&&|;)\s*)?(grep|rg|git grep|cat|sed|head|tail|awk|uv run dev code|gh (issue|pr) view)\b"
)


def classify(e):
    """failure_class, except that a --help call is never a CLI failure and the output of a read or search
    (grep, cat, a Read) counts only when the call itself errored: file text quotes failure strings."""
    cmd = e["input"].get("command", "") if e["name"] == "Bash" else ""
    if not e["is_error"] and (e["name"] in ("Read", "Grep", "Glob") or READS.search(cmd) or "--help" in cmd):
        return None
    fc = failure_class(e["result"], e["is_error"])
    if fc == "cli-usage" and "--help" in cmd:
        return None
    return fc


def events(path):
    """Ordered tool calls of one transcript: dict(ts, name, input, result, is_error)."""
    pending = {}
    out = []
    texts = []
    for line in path.open(errors="replace"):
        try:
            o = json.loads(line)
        except Exception:  # noqa: S112
            continue
        if not isinstance(o, dict):
            continue
        m = o.get("message") or {}
        cont = m.get("content") if isinstance(m, dict) else None
        if o.get("type") == "assistant" and isinstance(cont, list):
            for x in cont:
                if not isinstance(x, dict):
                    continue
                if x.get("type") == "tool_use":
                    e = {
                        "ts": o.get("timestamp"),
                        "name": x.get("name"),
                        "input": x.get("input") or {},
                        "result": "",
                        "is_error": False,
                    }
                    pending[x.get("id")] = e
                    out.append(e)
                elif x.get("type") == "text":
                    texts.append((o.get("timestamp"), x.get("text", "")))
        elif o.get("type") == "user" and isinstance(cont, list):
            for x in cont:
                if isinstance(x, dict) and x.get("type") == "tool_result" and x.get("tool_use_id") in pending:
                    e = pending.pop(x["tool_use_id"])
                    e["result"] = text_of(x.get("content"))
                    e["is_error"] = bool(x.get("is_error"))
                    e["rts"] = o.get("timestamp")
    return out, texts


def agent_type(f):
    meta = f.with_suffix(".meta.json")
    if meta.exists():
        try:
            return json.loads(meta.read_text()).get("agentType", "?")
        except Exception:
            return "?"
    return "main session"


def short(e):
    i = e["input"]
    if e["name"] == "Bash":
        return i.get("command", "")[:300]
    return json.dumps({k: v for k, v in i.items() if k in ("file_path", "pattern", "path", "query", "url")})[:300]


def minutes(a, b):
    ta, tb = ts(a or ""), ts(b or "")
    return (tb - ta).total_seconds() / 60 if ta and tb else None


def main(argv):
    dump = None
    since = ""
    if "--dump" in argv:
        i = argv.index("--dump")
        dump = open(argv[i + 1], "w")  # noqa: SIM115
        argv = argv[:i] + argv[i + 2 :]
    if "--since" in argv:
        i = argv.index("--since")
        since = argv[i + 1]
        argv = argv[:i] + argv[i + 2 :]
    for d in argv:
        p = pathlib.Path(d).expanduser()
        if not p.exists():  # a suffix of a directory name under ~/.claude/projects
            p = next(q for q in (pathlib.Path.home() / ".claude" / "projects").iterdir() if q.name.endswith(d))
        label = p.name[-12:]
        files = sorted(p.rglob("*.jsonl"))
        runs = Counter()
        pre_calls, pre_min = defaultdict(list), defaultdict(list)
        pre_cls = Counter()
        no_edit = Counter()
        fails = Counter()
        rec_calls, rec_min = defaultdict(list), defaultdict(list)
        help_calls = nav_calls = total_calls = 0
        remarks = 0
        for f in files:
            evs, texts = events(f)
            if not evs or (evs[0]["ts"] or "")[:10] < since:
                continue
            kind = agent_type(f)
            runs[kind] += 1
            total_calls += len(evs)
            # A. orientation
            if kind in ("harness-ops:builder", "harness-ops:tester"):
                first = next((k for k, e in enumerate(evs) if e["name"] in EDIT_TOOLS), None)
                if first is None:
                    no_edit[kind] += 1
                else:
                    pre_calls[kind].append(first)
                    mm = minutes(evs[0]["ts"], evs[first]["ts"])
                    if mm is not None:
                        pre_min[kind].append(mm)
                    pre_cls.update(call_class(e["name"], e["input"]) for e in evs[:first])
            # C. help and nav
            for e in evs:
                c = e["input"].get("command", "") if e["name"] == "Bash" else ""
                help_calls += bool(re.search(r"--help\b", c))
                nav_calls += bool(re.search(r"dev code (outline|show|refs|find)", c))
            # B. failures and recovery
            for k, e in enumerate(evs):
                fc = classify(e)
                if not fc:
                    continue
                fails[fc] += 1
                j = next((j for j in range(k + 1, len(evs)) if not classify(evs[j])), None)
                if j is not None:
                    rec_calls[fc].append(j - k)
                    mm = minutes(e.get("rts") or e["ts"], evs[j].get("rts") or evs[j]["ts"])
                    if mm is not None:
                        rec_min[fc].append(mm)
                if dump and fc in ("cli-usage", "path", "api", "other-error"):
                    dump.write(
                        json.dumps(
                            {
                                "dir": label,
                                "file": str(f.relative_to(p)),
                                "kind": kind,
                                "ts": e["ts"],
                                "class": fc,
                                "call": short(e),
                                "result": e["result"][:500],
                                "next": [short(x) for x in evs[k + 1 : k + 4]],
                                "recovery_calls": (j - k) if j is not None else None,
                            }
                        )
                        + "\n"
                    )
            # D. remarks
            for t_ts, t in texts:
                for s in SENT.split(t):
                    if SUBJ.search(s) and MISS.search(s):
                        remarks += 1
                        if dump:
                            dump.write(
                                json.dumps(
                                    {
                                        "dir": label,
                                        "file": str(f.relative_to(p)),
                                        "kind": kind,
                                        "ts": t_ts,
                                        "class": "remark",
                                        "s": s[:600],
                                    }
                                )
                                + "\n"
                            )
                        break

        def med(xs):
            xs = sorted(xs)
            return xs[len(xs) // 2] if xs else None

        print(f"== {label}: transcripts={len(files)} runs={sum(runs.values())} calls={total_calls}")
        print("runs by kind:", dict(runs.most_common()))
        for kind in pre_calls:
            print(
                f"A {kind}: runs with an edit={len(pre_calls[kind])} without={no_edit[kind]} "
                f"median calls before first edit={med(pre_calls[kind])} "
                f"median minutes={med(pre_min[kind]):.1f} total pre-edit calls={sum(pre_calls[kind])}"
            )
        tot = sum(pre_cls.values())
        print(
            "A pre-edit call classes:", ", ".join(f"{k} {v} ({100 * v / tot:.0f}%)" for k, v in pre_cls.most_common())
        )
        print(f"B failures: {dict(fails.most_common())}")
        for fc in fails:
            if rec_calls[fc]:
                print(
                    f"B {fc}: n={len(rec_calls[fc])} median recovery calls={med(rec_calls[fc])} "
                    f"median minutes={med(rec_min[fc]):.2f} total minutes={sum(rec_min[fc]):.1f}"
                )
        print(f"C --help calls={help_calls} dev-code nav calls={nav_calls}")
        print(f"D doc remarks (candidates)={remarks}")


if __name__ == "__main__":
    main(sys.argv[1:])
