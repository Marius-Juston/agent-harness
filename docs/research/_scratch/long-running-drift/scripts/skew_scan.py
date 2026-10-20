"""Which harness-ops version each Claude Code session and subagent actually ran (#282 r1, platform and skew).

Usage: python skew_scan.py --harness <agent-harness clone> --out <csv> [--exclude <session-id>] CODE=<transcript dir> ...

Evidence per transcript file (main session or subagent):
- plugin cache paths `plugins/cache/agent-harness/harness-ops/<ver>/` inside skill loads (invoked_skills attachments and
  "Base directory for this skill:" user turns) and hook stdout/stderr; never free text, which quotes paths;
- a harness-ops subagent's system prompt (api-request-blob, role system) matched against every distinct body that agent
  file ever had in the harness history; each body maps to the first release tag that contains it;
- the harness-ops agent types listed to the session (agent_listing_delta), so a session without architect and
  suite-auditor ran the <= v0.1.4 agent set;
- Claude Code `version` and assistant `model` fields, first and last timestamps;
- guard-hook block messages ("PreToolUse:Bash hook error: [<expanded plugin root>/scripts/...");
- tool results "Agent type '...' not found".
Writes one CSV row per transcript file. Private names stay out: rows carry the CODE given on the command line.
"""

import argparse
import contextlib
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

CACHE = re.compile(r"plugins/cache/agent-harness/harness-ops/(\d+\.\d+\.\d+)\\?\"?/(\w+)")
NOT_FOUND = re.compile(r"Agent type '([^']+)' not found")
FM = re.compile(r"\A---\n.*?\n---\n", re.S)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def agent_variants(repo: Path) -> dict[str, list[tuple[str, str, str]]]:
    """agent name -> [(body, first commit date, release tags that shipped it)] for every distinct body on main."""
    tags = sorted(
        (t for t in git(repo, "tag", "-l", "v*").split()),
        key=lambda t: tuple(int(x) for x in t[1:].split(".")),
    )
    out: dict[str, list[tuple[str, str, str]]] = {}
    for path in git(repo, "log", "--all", "--format=", "--name-only", "--", "plugins/harness-ops/agents/").split():
        name = Path(path).stem
        if name in out or not path.endswith(".md"):
            continue
        seen: dict[str, tuple[str, str]] = {}
        for line in reversed(git(repo, "log", "--first-parent", "main", "--format=%H %cI", "--", path).splitlines()):
            sha, date = line.split()
            try:
                body = FM.sub("", git(repo, "show", f"{sha}:{path}")).strip()
            except subprocess.CalledProcessError:
                continue
            if body and body not in seen:
                seen[body] = (sha, date)
        # label each body with the release tags that shipped it; a body no tag shipped was main-only
        at_tag = {}
        for t in tags:
            with contextlib.suppress(subprocess.CalledProcessError):
                at_tag[t] = FM.sub("", git(repo, "show", f"{t}:{path}")).strip()
        labelled = []
        for body, (_sha, date) in seen.items():
            shipped = [t for t in tags if at_tag.get(t) == body]
            label = (
                (shipped[0] if len(shipped) == 1 else f"{shipped[0]}..{shipped[-1]}")
                if shipped
                else f"main@{date[:10]}"
            )
            labelled.append((body, date, label))
        out[name] = labelled
    return out


def texts(record: dict) -> list[str]:
    """Strings in a record where a cache path means the plugin was loaded, not quoted."""
    att = record.get("attachment") if isinstance(record.get("attachment"), dict) else {}
    out = []
    if att.get("type") == "invoked_skills":
        out.append(json.dumps(att.get("skills")))
    if str(att.get("type", "")).startswith("hook"):
        out += [str(att.get("stdout", "")), str(att.get("stderr", "")), str(att.get("content", ""))]
    msg = record.get("message") if isinstance(record.get("message"), dict) else {}
    if record.get("type") == "user":
        content = msg.get("content")
        parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
        for p in parts:
            if (
                isinstance(p, dict)
                and p.get("type") == "text"
                and str(p.get("text", "")).startswith("Base directory for this skill:")
            ):
                out.append(p["text"][:300])
    return out


def scan(code: str, path: Path, variants: dict, exclude: str | None) -> dict | None:
    meta_file = path.with_suffix(".meta.json")
    meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
    agent_type = meta.get("agentType", "")
    row = dict(
        project=code,
        file=path.name,
        kind="sub" if "subagents" in path.parts else "main",
        agent=agent_type,
        start="",
        end="",
        cc=set(),
        models=set(),
        cache=set(),
        listed=set(),
        body_tag="",
        not_found=[],
    )
    session = None
    want = agent_type.removeprefix("harness-ops:") if agent_type.startswith("harness-ops:") else None
    body_done = False
    for line in path.open(errors="replace"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        session = session or r.get("sessionId")
        if exclude and session == exclude:
            return None
        ts = r.get("timestamp")
        if ts:
            row["start"] = row["start"] or ts
            row["end"] = ts
        if r.get("version"):
            row["cc"].add(r["version"])
        msg = r.get("message") if isinstance(r.get("message"), dict) else {}
        if r.get("type") == "assistant" and msg.get("model") and not str(msg["model"]).startswith("<"):
            row["models"].add(msg["model"])
        for t in texts(r):
            row["cache"].update(f"{v}/{kind}" for v, kind in CACHE.findall(t))
        att = r.get("attachment") if isinstance(r.get("attachment"), dict) else {}
        if att.get("type") == "agent_listing_delta":
            added = att.get("addedTypes") or []
            added = added if isinstance(added, list) else re.findall(r"'([^']+)'", str(added))
            removed = att.get("removedTypes") or []
            removed = removed if isinstance(removed, list) else re.findall(r"'([^']+)'", str(removed))
            row["listed"] |= {a for a in added if a.startswith("harness-ops:")}
            row["listed"] -= set(removed)
        if r.get("type") == "user" and isinstance(msg.get("content"), list):
            for p in msg["content"]:
                if isinstance(p, dict) and p.get("type") == "tool_result":
                    c = p.get("content")
                    s = (
                        c
                        if isinstance(c, str)
                        else (
                            " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                            if isinstance(c, list)
                            else str(c)
                        )
                    )
                    # a guard hook's block message names the expanded plugin root: "PreToolUse:Bash hook error: [...]"
                    if re.match(r"\s*(Pre|Post)ToolUse:\w+ hook error", s):
                        row["cache"].update(f"{v}/hook" for v, _ in CACHE.findall(s[:400]))
                    if not p.get("is_error"):
                        continue
                    row["not_found"] += [f"{ts}|{m}" for m in NOT_FOUND.findall(s)[:1]]
        system = None
        if r.get("type") == "api-request-blob" and msg.get("role") == "system":
            system = msg.get("content")
        elif r.get("type") == "api-request-shape" and isinstance(r.get("shape"), dict):
            system = r["shape"].get("system")
        elif isinstance(r.get("attachment"), dict) and r["attachment"].get("type") == "prompt_snapshot":
            system = [{"text": t} if isinstance(t, str) else t for t in r["attachment"].get("systemPrompt") or []]
        if want and not body_done and system:
            content = system
            text = (
                content
                if isinstance(content, str)
                else " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            )
            hits = [tag for body, _, tag in variants.get(want, []) if body in text]
            if hits:
                row["body_tag"] = "|".join(sorted(set(hits)))
                body_done = True
    if want and not body_done:
        row["body_tag"] = "nomatch"
    for k in ("cc", "models", "cache", "listed"):
        row[k] = " ".join(sorted(row[k]))
    row["not_found"] = " ".join(row["not_found"])
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--exclude")
    ap.add_argument("dirs", nargs="+")
    a = ap.parse_args()
    variants = agent_variants(a.harness)
    print({k: [(d[:10], t) for _, d, t in v] for k, v in variants.items()}, file=sys.stderr)
    rows = []
    for spec in a.dirs:
        code, _, d = spec.partition("=")
        for path in sorted(Path(d).rglob("*.jsonl")):
            if (row := scan(code, path, variants, a.exclude)) is not None:
                rows.append(row)
    with a.out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows -> {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
