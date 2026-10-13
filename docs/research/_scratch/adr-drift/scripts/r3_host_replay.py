"""Audit fix for #227: replay the proposed literature-API deny rule over local session transcripts (read-only).

usage: python3 -I r3_host_replay.py [TRANSCRIPT_DIR] [--show]
Rule replayed: deny a Bash command that runs curl or wget, or a WebFetch, whose URL starts with one of DENY
(the endpoints `dev lit` wraps, from devkit/lit.py). For contrast it also counts fetches of nearby scholarly hosts
that must stay allowed (ALLOW_NEAR). Every denied call is printed with --show for a hand TRUE/FP call.
"""

import json
import os
import pathlib
import re
import sys
from collections import Counter

DENY = (
    "api.openalex.org/",
    "api.semanticscholar.org/",
    "api.crossref.org/",
    "www.ebi.ac.uk/europepmc/webservices/rest/search",
    "export.arxiv.org/api/",
)
# A broader Europe PMC prefix would also deny full-text downloads, which `dev lit` does not offer (counted below).
EPMC_FULLTEXT = "www.ebi.ac.uk/europepmc/webservices/rest/PMC"
ALLOW_NEAR = (
    "arxiv.org/abs/",
    "arxiv.org/pdf/",
    "doi.org/",
    "api.datacite.org/",
    "europepmc.org/article/",
    "api.github.com/",
    "openalex.org/W",
    EPMC_FULLTEXT,
)
URL = re.compile(r"https?://([^\s'\"<>)`]+)")
FETCHER = re.compile(r"(?:^|[\s;&|(`$])(?:curl|wget)\b")


def same_segment(cmd: str) -> bool:
    """True when some shell segment (split at newlines, ;, &&, ||, |) runs curl/wget on a deny URL, or a variable
    or loop feeds one (a deny URL anywhere before a curl/wget in a script also counts)."""
    segs = re.split(r"\n|;|&&|\|\||\|", cmd)
    for s in segs:
        if FETCHER.search(s) and any(d in s for d in DENY):
            return True
    first_deny = min((cmd.find(d) for d in DENY if d in cmd), default=-1)
    later_fetch = FETCHER.search(cmd, first_deny) if first_deny >= 0 else None
    return bool(later_fetch and "$" in cmd)


def kind(url_tail: str) -> str | None:
    for d in DENY:
        if url_tail.startswith(d) or url_tail.startswith(d.rstrip("/") + "?"):
            return "deny:" + d
    for a in ALLOW_NEAR:
        if a in url_tail[: len(a) + 12]:
            return "allow:" + a
    return None


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = pathlib.Path(
        args[0] if args else os.environ.get("PROJECT_B_TRANSCRIPTS", "~/.claude/projects/project-b")
    ).expanduser()
    c, shown, loose, files = Counter(), [], [], 0
    for f in sorted(root.rglob("*.jsonl")):
        files += 1
        who = "subagent" if "subagents" in f.parts else "main"
        for line in f.open(errors="replace"):
            try:
                msg = json.loads(line).get("message") or {}
            except (json.JSONDecodeError, AttributeError):
                continue
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                name, inp = b.get("name"), b.get("input") or {}
                if name == "WebFetch":
                    urls, tool = [str(inp.get("url", ""))], "WebFetch"
                elif name == "Bash":
                    cmd = str(inp.get("command", ""))
                    if not FETCHER.search(cmd):
                        # a mention without curl/wget is never denied; count deny-host mentions to show what the
                        # tool-name condition spares (grep, heredocs, python scripts, dev lit itself)
                        if any(d in cmd for d in DENY):
                            c[who, "Bash mention without curl/wget (not denied)"] += 1
                        continue
                    urls, tool = [m.group(0) for m in URL.finditer(cmd)], "Bash curl/wget"
                else:
                    continue
                kinds = {k for u in urls if (k := kind(re.sub(r"^https?://", "", u)))}
                for k in kinds:
                    c[who, f"{tool} {k}"] += 1  # tool calls, not URLs
                if any(k.startswith("deny") for k in kinds):
                    c[who, "CALLS DENIED"] += 1
                    if tool.startswith("Bash") and not same_segment(cmd):
                        c[who, "  of which the deny URL is not in a curl/wget segment (hand check)"] += 1
                        loose.append(f"{f.name[:12]} {who}: {cmd[:300]!r}")
                    if "--show" in sys.argv:
                        src = inp.get("command") or inp.get("url")
                        shown.append(f"{f.name[:12]} {who} {tool}: {str(src)[:220]!r}")
    print(f"transcript files: {files}")
    for (who, what), n in sorted(c.items()):
        print(f"  {who:8} {what}: {n}")
    for s in shown:
        print(s)
    print("# denied Bash calls whose deny URL sits outside any curl/wget segment")
    for s in loose:
        print(s)


if __name__ == "__main__":
    main()
