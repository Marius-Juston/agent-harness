"""R2b check 2: replay "an open issue's Owns path existed on main and is now gone" over every first-parent commit.

usage: python3 r2_owns_replay.py [--no-window]   (--no-window: count commits up to 15 min AFTER the close instead)
For each issue with an `## Owns` section, with its body as it was at each commit (GraphQL edit history from
r2_issue_edits.py; 9 issues changed Owns across edits), at each first-parent commit while the issue was open (created <= commit time < closed - 15 min, so
the issue's own merge-then-close window does not count), each Owns path is matched against the commit's tree.
A finding = (issue, path) that matched at an earlier commit and matches nothing now. Prints each finding with its
first and last commit index and how the issue ended, for hand classification.

The rule behind the note's "11 of 11": an issue is FLAGGED when at least one of its Owns paths is flagged while it
is open, excluding the 15 minutes before its own close (a stand-in for "skip the merging PR's own issue"). The
summary line counts flagged issues and splits them by how they ended: closed within 15 min of the first flagged
commit (closed by hand as moved), closed later (done but left open), or still OPEN. TRUE = every flagged issue needed
an action on its Owns (close, or edit the Owns); judged by hand from the printed rows.
"""

import bisect
import datetime
import json
import re
import sys

import r2_common as c

OWNS = re.compile(r"## Owns\s*\n(.*?)(\n## |\Z)", re.S)


def ts(s):
    return int(datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()) if s else None


def matcher(pat):
    p = pat.strip().rstrip("/")
    if any(ch in p for ch in "*?["):
        rx = re.escape(p).replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
        r = re.compile(rf"^{rx}(/.*)?$")
        return lambda files: any(r.match(f) for f in files)
    return lambda files: any(f == p or f.startswith(p + "/") for f in files)


for name, pre in (("AH", "ah"), ("PB", "pb")):
    hist = c.history(name)
    trees = [set(c.files(name, s)) for s, _, _ in hist]
    issues = json.load(open(c.DATA / f"{pre}_issues.json"))  # noqa: SIM115 - research script, kept as run
    edits = {int(k): v["edits"] for k, v in json.load(open(c.DATA / f"{pre}_edits.json")).items()}  # noqa: SIM115 - research script, kept as run

    def owns_at(iss, t):
        e = edits.get(iss["number"]) or []  # noqa: B023 - research script, kept as run
        body = e[max(bisect.bisect_right([ts(x[0]) for x in e], t) - 1, 0)][1] if e else iss["body"]
        m = OWNS.search(body or "")
        return set(re.findall(r"`([^`]+)`", m.group(1))) if m else set()

    n_iss = n_paths = n_checks = 0
    flagged_issues = {}
    for iss in issues:
        every = set().union(*(owns_at(iss, t) for _, t, _ in hist)) | owns_at(iss, 2**40)
        paths = sorted(every)
        if not paths:
            continue
        n_iss += 1
        t0, t1 = ts(iss["created_at"]), ts(iss["closed_at"])
        open_idx = [
            k
            for k, (_, t, _) in enumerate(hist)
            if t >= t0 - 0 and (t1 is None or (t <= t1 + 900 if "--no-window" in sys.argv else t < t1 - 900))
        ]
        for p in paths:
            n_paths += 1
            f = matcher(p)
            seen = False
            flagged = []
            for k in range(len(hist)):
                hit = f(trees[k])
                if k in open_idx and p in owns_at(iss, hist[k][1]):
                    n_checks += 1
                    if not hit and seen:
                        flagged.append(k)
                seen |= hit
            if flagged:
                first_t = hist[flagged[0]][1]
                ct = ts(iss["closed_at"])
                how = "open" if ct is None else ("closed <=15 min" if ct - first_t <= 900 else "closed later")
                flagged_issues[iss["number"]] = how
                end = f"closed {iss['closed_at'][:16]}" if iss["closed_at"] else "OPEN"
                print(
                    f"{name} #{iss['number']} `{p}` flagged at {flagged[0]}..{flagged[-1]} ({len(flagged)} commits; "
                    f"re-exists at head: {f(trees[-1])}) {end} | {iss['title'][:70]}"
                )
    print(f"RESULT {name}: {n_iss} issues with Owns, {n_paths} paths, {n_checks} (path, open commit) checks")
    split = {h: sum(v == h for v in flagged_issues.values()) for h in ("closed <=15 min", "closed later", "open")}
    print(f"RESULT {name}: {len(flagged_issues)} flagged issues; {split}")

    # Integrate-time rule (the proposed gate): at each merge, an issue that is open at the merge, is not closed by the
    # merged PR (Closes/Fixes/Resolves #n in its body), and owns a path that existed before the merge and is gone after
    # it. Unlike the replay above, this counts the issues the lead closed by hand within minutes of the merge.
    prs = {p["number"]: p for p in json.load(open(c.DATA / f"{pre}_prs.json"))}  # noqa: SIM115 - research script, kept as run
    at_merge = {}
    for k in range(1, len(hist)):
        sha, tk, subj = hist[k]
        m = re.search(r"#(\d+)", subj)
        pr = prs.get(int(m.group(1))) if m else None
        own = {int(x) for x in re.findall(r"(?:[Cc]loses|[Ff]ixes|[Rr]esolves) #(\d+)", (pr or {}).get("body") or "")}
        if pr and (b := re.match(r"(\d+)-", str(pr.get("head") or ""))):
            own.add(int(b.group(1)))  # the harness branch convention <issue>-<slug> names the PR's own issue
        for iss in issues:
            t0, t1 = ts(iss["created_at"]), ts(iss["closed_at"])
            if iss["number"] in own or t0 > tk or (t1 is not None and t1 < tk):
                continue
            for pth in owns_at(iss, tk):
                f = matcher(pth)
                if f(trees[k - 1]) and not f(trees[k]):
                    ct = t1
                    how = "open" if ct is None else ("closed <=15 min" if ct - tk <= 900 else "closed later")
                    at_merge[iss["number"]] = (k, how)
    split = {h: sum(v[1] == h for v in at_merge.values()) for h in ("closed <=15 min", "closed later", "open")}
    if "--list" in sys.argv:
        for n, (k, how) in sorted(at_merge.items()):
            print(f"  at-merge {name} #{n} merge {k} {how}")
    print(
        f"RESULT-AT-MERGE {name}: {len(at_merge)} flagged issues at {len({v[0] for v in at_merge.values()})} merge(s); {split}"
    )
