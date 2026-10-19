"""R1 memory: inventory auto-memory entries and replay their life in Claude Code transcripts (machine-local, read-only).

usage: python3 -I r1_memory_scan.py --projects-dir ~/.claude/projects --out OUT.json CODE=DIRNAME [CODE=DIRNAME ...]

DIRNAME is the project folder under --projects-dir (e.g. the mangled repository path). Every folder whose name starts with
DIRNAME (worktree folders) is scanned for transcripts too; the memory directory is DIRNAME/memory. Output is JSON keyed by
CODE with real entry names, so keep OUT outside the public repository and publish only counts and code names.

Per entry: frontmatter (type, originSessionId, modified), birth/mtime, body dates, wikilinks and dangling ones, whether the
index lists it, and from transcripts: write/edit events, Read events, index loads (main vs subagent), and entry files that
were written or indexed once but no longer exist (deleted entries).
"""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path

INDEX_MARK = "auto-memory, persists across conversations"
DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
LINK = re.compile(r"\[\[([^\]]+)\]\]")
IDX_FILE = re.compile(r"\]\(([^)]+?\.md)\)")


def birth(p: Path) -> float:
    st = os.stat(p)
    if hasattr(st, "st_birthtime"):
        return st.st_birthtime
    out = subprocess.run(["stat", "-c", "%W", "--", str(p)], capture_output=True, text=True).stdout.strip()
    return float(out) if out and out != "0" else st.st_mtime


def iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.UTC).isoformat(timespec="seconds")


def frontmatter(text: str) -> dict:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    fm = {}
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.strip().partition(":")
            if v.strip():
                fm[k.strip()] = v.strip().strip('"')
    return fm


def inventory(mem: Path) -> dict:
    idx = (mem / "MEMORY.md").read_text(errors="replace") if (mem / "MEMORY.md").exists() else ""
    indexed = set(IDX_FILE.findall(idx))
    names = {p.stem for p in mem.glob("*.md")}
    out = {}
    for p in sorted(mem.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        text = p.read_text(errors="replace")
        fm = frontmatter(text)
        body = text.split("---", 2)[-1]
        links = LINK.findall(body)
        out[p.name] = {
            "type": fm.get("type"),
            "origin": fm.get("originSessionId"),
            "fm_modified": fm.get("modified"),
            "birth": iso(birth(p)),
            "mtime": iso(p.stat().st_mtime),
            "words": len(text.split()),
            "body_dates": sorted(set(DATE.findall(body))),
            "links": links,
            "dangling": [x for x in links if x not in names],
            "indexed": p.name in indexed,
            "has_why": "**Why:**" in body,
            "has_how": "**How to apply:**" in body,
            "issue_refs": len(re.findall(r"(?<![\w/])#\d+\b", body)),
        }
    return {"entries": out, "index_lines": len(idx.splitlines()), "index_files": sorted(indexed)}


def walk(obj):
    """Yield every dict nested in obj."""
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            yield o
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)


def scan(dirs: list[Path], memdir: str) -> dict:
    ev = {"writes": [], "reads": [], "bash": [], "index_loads": [], "sessions": {}}
    seen_idx = {}
    for d in dirs:
        for f in d.rglob("*.jsonl"):
            sub = "subagents" in f.parts
            sid = f.stem
            first = None
            loaded = False
            with open(f, errors="replace") as fh:
                for line in fh:
                    if first is None:
                        m = re.search(r'"timestamp":"([^"]+)"', line)
                        if m:
                            first = m.group(1)
                    hit_idx = INDEX_MARK in line and not loaded
                    hit_mem = memdir in line
                    if not (hit_idx or hit_mem):
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = rec.get("timestamp", first)
                    if hit_idx:
                        m = re.search(re.escape(INDEX_MARK) + r"\):\\n\\n(.*?)(?:\\n\\n[A-Z#<]|</system)", line)
                        files = sorted(set(IDX_FILE.findall(m.group(1).replace("\\n", "\n")))) if m else []
                        ev["index_loads"].append({"session": sid, "sub": sub, "ts": ts, "files": files})
                        for x in files:
                            seen_idx.setdefault(x, ts)
                        loaded = True
                    if hit_mem and rec.get("type") == "assistant":
                        for blk in walk(rec.get("message", {})):
                            if blk.get("type") != "tool_use":
                                continue
                            inp = blk.get("input", {})
                            fp = str(inp.get("file_path", ""))
                            if memdir in fp and fp.endswith(".md"):
                                kind = "reads" if blk.get("name") == "Read" else "writes"
                                ev[kind].append(
                                    {
                                        "session": sid,
                                        "sub": sub,
                                        "ts": ts,
                                        "tool": blk.get("name"),
                                        "file": Path(fp).name,
                                    }
                                )
                            cmd = str(inp.get("command", ""))
                            if blk.get("name") == "Bash" and memdir in cmd:
                                ev["bash"].append(
                                    {
                                        "session": sid,
                                        "sub": sub,
                                        "ts": ts,
                                        "rm": bool(re.search(r"\b(rm|mv|git rm)\b", cmd)),
                                        "removed": sorted(set(re.findall(r"\brm\s+(?:-\w+\s+)*([\w./-]+\.md)", cmd))),
                                        "files": sorted(set(re.findall(r"([\w.-]+\.md)", cmd))),
                                    }
                                )
            ev["sessions"][sid] = {"first": first, "sub": sub}
    ev["seen_in_index_first"] = seen_idx
    return ev


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("projects", nargs="+", help="CODE=DIRNAME")
    a = ap.parse_args()
    root = Path(a.projects_dir).expanduser()
    res = {}
    for spec in a.projects:
        code, _, name = spec.partition("=")
        mem = root / name / "memory"
        dirs = [p for p in root.iterdir() if p.is_dir() and (p.name == name or p.name.startswith(name + "-"))]
        inv = inventory(mem) if mem.exists() else {"entries": {}}
        ev = scan(dirs, f"/{name}/memory")
        existing = set(inv["entries"])
        written = {w["file"] for w in ev["writes"]} - {"MEMORY.md"}
        indexed_ever = set(ev["seen_in_index_first"])
        removed = {Path(x).name for b in ev["bash"] for x in b.get("removed", [])}
        inv["deleted"] = sorted((written | indexed_ever | removed) - existing - {"MEMORY.md"})
        inv["rm_events"] = [b for b in ev["bash"] if b.get("removed")]
        for e, row in inv["entries"].items():
            row["writes"] = [w for w in ev["writes"] if w["file"] == e]
            row["reads"] = [r for r in ev["reads"] if r["file"] == e]
            row["origin_first_ts"] = ev["sessions"].get(row["origin"] or "", {}).get("first")
            row["index_loads_main"] = sum(1 for x in ev["index_loads"] if e in x["files"] and not x["sub"])
            row["index_loads_sub"] = sum(1 for x in ev["index_loads"] if e in x["files"] and x["sub"])
        res[code] = {
            "inventory": inv,
            "n_transcripts": len(ev["sessions"]),
            "n_sub_transcripts": sum(1 for s in ev["sessions"].values() if s["sub"]),
            "first_transcript": min((s["first"] for s in ev["sessions"].values() if s["first"]), default=None),
            "index_loads_main": sum(1 for x in ev["index_loads"] if not x["sub"]),
            "index_loads_sub": sum(1 for x in ev["index_loads"] if x["sub"]),
            "sub_with_index": len({x["session"] for x in ev["index_loads"] if x["sub"]}),
            "events": ev,
        }
        print(
            code,
            "entries",
            len(inv["entries"]),
            "deleted",
            len(inv["deleted"]),
            "transcripts",
            res[code]["n_transcripts"],
            "sub",
            res[code]["n_sub_transcripts"],
            "sub_with_index",
            res[code]["sub_with_index"],
            "writes",
            len(ev["writes"]),
            "reads",
            len(ev["reads"]),
            "bash",
            len(ev["bash"]),
        )
    Path(a.out).write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
