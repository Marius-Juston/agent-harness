"""R2b check 6: replay "HANDOFF claims open work that is already closed" over every project-B HANDOFF version.

usage: python3 r2_handoff_replay.py
For each version of docs/HANDOFF.md on project B's first-parent history, every #n / agent-harness#n reference is
looked up (issue closed_at, PR merged_at/closed_at; gh api for numbers missing from the dumps) and flagged when it is
closed at the moment the version is committed (stale on arrival) or before the next version replaces it (went stale).
Flags are split by section: claims-open sections (In flight, Next, Waiting) vs the rest (State, history, prose).
Also flags a version whose State names a harness version different from the pin in pyproject.toml at that commit.
"""

import datetime
import json
import re
import subprocess

import r2_common as c


def ts(s):
    return int(datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()) if s else None


CLOSED = {}
for pre, repo, full in (("pb", "PB", c.PB_NAME), ("ah", "AH", "agent-harness")):  # noqa: B007 - research script, kept as run
    for i in json.load(open(c.DATA / f"{pre}_issues.json")):  # noqa: SIM115 - research script, kept as run
        CLOSED[(repo, i["number"])] = ts(i["closed_at"])
    for p in json.load(open(c.DATA / f"{pre}_prs.json")):  # noqa: SIM115 - research script, kept as run
        CLOSED[(repo, p["number"])] = ts(p["merged_at"])


def closed_at(repo, n):
    if (repo, n) not in CLOSED:
        slug = {"PB": c.PB_SLUG, "AH": c.AH_SLUG}[repo]
        p = subprocess.run(
            ["gh", "api", f"repos/{slug}/issues/{n}", "--jq", ".closed_at"], capture_output=True, text=True
        )
        CLOSED[(repo, n)] = ts(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip() not in ("", "null") else None
    return CLOSED[(repo, n)]


CLAIMS_OPEN = re.compile(r"in flight|next|waiting|running|open|paused|blocked", re.I)
DONE_WORD = re.compile(r"\b(merged|done|landed|closed|shipped|released)\b", re.I)


def items(txt):
    """(section, item text) with continuation lines joined: a list item starts with '- ' or 'N. '."""
    section, cur, out = "", None, []
    for line in txt.splitlines():
        if line.startswith("## "):
            if cur:
                out.append((section, cur))
            section, cur = line[3:].strip(), None
            continue
        if re.match(r"\s*(- |\d+\. )", line) or not line.strip():
            if cur:
                out.append((section, cur))
            cur = line.strip() or None
        else:
            cur = (cur + " " + line.strip()) if cur else line.strip()
    if cur:
        out.append((section, cur))
    return out


hist = c.history("PB")
versions = []
prev = None
for sha, t, subj in hist:  # noqa: B007 - research script, kept as run
    txt = subprocess.run(
        ["git", "-C", str(c.PB), "show", f"{sha}:docs/HANDOFF.md"], capture_output=True, text=True
    ).stdout
    if txt and txt != prev:
        versions.append((sha, t, txt))
        prev = txt
now = int(datetime.datetime.now(datetime.UTC).timestamp())
tot = {"open-sec": [0, 0, 0], "other": [0, 0, 0]}  # refs, closed at write, closed before replaced
for vi, (sha, t, txt) in enumerate(versions):
    t_next = versions[vi + 1][1] if vi + 1 < len(versions) else now
    pin = re.search(
        r"agent-harness@(v[\d.]+)",
        subprocess.run(["git", "-C", str(c.PB), "show", f"{sha}:pyproject.toml"], capture_output=True, text=True).stdout
        or "",
    )
    print(
        f"== HANDOFF {sha[:9]} {datetime.datetime.fromtimestamp(t):%m-%d %H:%M} (valid {(t_next - t) / 3600:.1f} h), pin {pin.group(1) if pin else 'none'}"
    )
    for section, line in items(txt):
        kind = "open-sec" if CLAIMS_OPEN.search(section) else "other"
        if section.lower().startswith("state"):
            vers = set(re.findall(r"\bv0\.\d+\.\d+\b", line))
            if pin and vers and pin.group(1) not in vers and re.search(r"adopt|on v|uses|pinned", line, re.I):
                print(f"   VERSION [{section}] pin {pin.group(1)} vs {sorted(vers)} | {line.strip()[:120]}")
        for m in re.finditer(r"(agent-harness)?#(\d+)", line):
            before = line[: m.start()]
            repo = "AH" if m.group(1) or before.rfind("agent-harness") > before.rfind(c.PB_NAME) else "PB"
            n = int(m.group(2))
            ca = closed_at(repo, n)
            clause = re.split(r"[;.]\s|\(|\)", line[: m.end()])[-1] + line[m.end() :].split(";")[0].split(". ")[0]
            hist_word = bool(DONE_WORD.search(clause))
            tot[kind][0] += 1
            tag = "said-done" if hist_word else "claim"
            if ca is not None and ca <= t:
                tot[kind][1] += 1
                if kind == "open-sec":
                    print(f"   STALE-AT-WRITE {tag} [{section}] {repo}#{n} | {line.strip()[:150]}")
            elif ca is not None and ca <= t_next:
                tot[kind][2] += 1
                if kind == "open-sec":
                    print(f"   WENT-STALE +{(ca - t) / 3600:.1f}h {tag} [{section}] {repo}#{n} | {line.strip()[:150]}")
print("totals (refs, closed at write, closed before replaced):", tot)
print("versions:", len(versions))
