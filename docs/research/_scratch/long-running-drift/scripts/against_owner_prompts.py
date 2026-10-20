"""Extract human-typed prompts from top-level Claude Code transcripts and flag drift-correction candidates.

Usage: python3 against_owner_prompts.py <project_transcript_dir> [<dir> ...] [--dump OUT.jsonl]
Prints per-directory counts: sessions, human prompts, keyword hits. Prompt text goes only to --dump (private).
"""

import json
import pathlib
import re
import sys

KW = re.compile(
    r"\b(stale|outdated|out of date|out-of-date|no longer|not anymore|anymore|obsolete|"
    r"we (don't|do not|stopped|removed|changed|switched)|memory|that'?s old|old (rule|way|version)|"
    r"we already (decided|changed|removed)|i (told|said)|again\b|still (says|using|uses|refers))",
    re.I,
)


def human_text(o):
    if o.get("type") != "user" or o.get("isSidechain") or o.get("isMeta"):
        return None
    m = o.get("message", {})
    c = m.get("content")
    if isinstance(c, list):
        if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
            return None
        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    if not isinstance(c, str):
        return None
    s = c.strip()
    if (
        not s
        or s.startswith("<")
        or s.startswith("Caveat:")
        or "<task-notification>" in s
        or s.startswith("[Request interrupted")
    ):
        return None
    if o.get("origin") not in (None, "user", "human") and o.get("promptSource") not in (None, "user", "human", "typed"):
        pass
    return s


def main(argv):
    dump = None
    if "--dump" in argv:
        i = argv.index("--dump")
        dump = open(argv[i + 1], "w")  # noqa: SIM115
        argv = argv[:i] + argv[i + 2 :]
    for d in argv:
        p = pathlib.Path(d).expanduser()
        files = sorted(p.glob("*.jsonl"))
        n = hits = 0
        for f in files:
            for line in f.open(errors="replace"):
                try:
                    o = json.loads(line)
                except Exception:  # noqa: S112
                    continue
                s = human_text(o)
                if s is None:
                    continue
                n += 1
                k = bool(KW.search(s))
                hits += k
                if dump:
                    dump.write(
                        json.dumps({"dir": p.name, "file": f.name, "ts": o.get("timestamp"), "kw": k, "text": s[:1500]})
                        + "\n"
                    )
        print(f"{p.name}\tsessions={len(files)}\tprompts={n}\tkw_hits={hits}")


if __name__ == "__main__":
    main(sys.argv[1:])
