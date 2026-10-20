"""Agent tool calls that failed on a missing path or an unknown CLI argument whose text a prose file on main named at
that time (the agent may have taken it from stale prose). Read-only.

usage: python3 -I r1_tx_errors.py REPO REF TRANSCRIPT_DIR [TRANSCRIPT_DIR ...]
Failures: Read/Edit "does not exist", Bash "No such file or directory", argparse "invalid choice" / "unrecognized
arguments". The failing repo-relative path (or the rejected word) is looked up with `git grep -F` in *.md files of the
newest first-parent commit at or before the call (records excluded as in r1_claims_replay.py). Prints counts and, per
match, time, tool, failure kind and the doc type that named it (no transcript text).
"""

import bisect
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime

repo, ref, *dirs = sys.argv[1:]
root = os.path.realpath(repo)
hist = [
    ln.split()
    for ln in subprocess.run(
        ["git", "-C", repo, "log", "--first-parent", "--reverse", "--format=%ct %H", ref],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
]
times = [int(t) for t, _ in hist]
EXCL = re.compile(r"(^|/)(board|log|logs|generated|_scratch|agent-memory|fragments)/|(HANDOFF|CHANGELOG|INDEX)\.md$")


def named_in_docs(word, t):
    k = bisect.bisect_right(times, t) - 1
    if k < 0:
        return []
    out = subprocess.run(
        ["git", "-C", repo, "grep", "-l", "-F", word, hist[k][1], "--", "*.md"], capture_output=True, text=True
    ).stdout
    return [ln.split(":", 1)[1] for ln in out.splitlines() if not EXCL.search(ln.split(":", 1)[1])]


def rel(p):
    p = os.path.realpath(p) if p.startswith("/") else p
    m = re.search(r"worktrees?/[^/]+/(.*)$", p)
    if m:
        return m.group(1)
    return p[len(root) + 1 :] if p.startswith(root + "/") else None


ARG = re.compile(r"invalid choice: '([^']+)'|unrecognized arguments: (\S+)")
NOFILE = re.compile(r"(?:cannot access|can't open|No such file or directory)[^'\"]*?['\"]?(/?[\w./-]+\.\w+)")
fails, matched = 0, []
for d in dirs:
    for path in glob.glob(os.path.join(d, "**", "*.jsonl"), recursive=True):
        pend = {}
        for ln in open(path, errors="replace"):  # noqa: SIM115 - research script
            if '"tool_use"' not in ln and '"tool_result"' not in ln:
                continue
            try:
                o = json.loads(ln)
            except json.JSONDecodeError:
                continue
            content = (o.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    pend[c["id"]] = (c.get("name"), c.get("input", {}), o.get("timestamp"))
                elif c.get("type") == "tool_result" and c.get("tool_use_id") in pend:
                    name, inp, ts = pend.pop(c["tool_use_id"])
                    body = c.get("content")
                    text = body if isinstance(body, str) else json.dumps(body)
                    words, kind = [], None
                    if name in ("Read", "Edit") and "does not exist" in text[:300]:
                        r = rel(inp.get("file_path", ""))
                        words, kind = ([r] if r else []), "missing-file"
                    elif name == "Bash" and (m := ARG.search(text[:3000])):
                        words, kind = [m.group(1) or m.group(2)], "cli-arg"
                    elif name == "Bash" and (m := NOFILE.search(text[:3000])):
                        r = rel(m.group(1)) if m.group(1).startswith("/") else m.group(1)
                        words, kind = ([r] if r and "/" in r else []), "missing-file"
                    if not kind:
                        continue
                    fails += 1
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() if ts else None
                    for w in words:
                        if t and len(w) >= 6:
                            docs = named_in_docs(w, t)
                            if docs:
                                matched.append(
                                    (
                                        datetime.fromtimestamp(t).isoformat()[:16],
                                        os.path.basename(path)[:8],
                                        name,
                                        kind,
                                        w,
                                        docs[:3],
                                    )
                                )
print(f"failures={fails} named-in-prose={len(matched)}")
for m in matched:
    print("  ", m[0], m[1], m[2], m[3], len(m[5]), "docs")
json.dump(matched, open(os.environ.get("TX_OUT", "/dev/null"), "w"))  # noqa: SIM115 - research script
