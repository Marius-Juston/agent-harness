"""#282 r1: replay every checkable prose claim over a repository's first-parent history. Read-only (git plumbing only).

usage: python3 -I r1_claims_replay.py REPO REF LABEL OUT.json [--base DIR ...]

A *claim* is a backticked token in a prose file (or in a code comment / docstring) that names something the code can
confirm mechanically:
  PATH  a repo path (has '/'), FILE a bare file name with a known extension, SYM an identifier (CamelCase, snake_case,
  UPPER_SNAKE, `f()`, dotted module), FLAG a `--option` inside a command span, SUB the subcommand word after
  `dev` in a `... dev X` span.
Validity at a commit (lenient on purpose, so a break is a lower bound on drift):
  PATH  exists as a file or directory, relative to the root, the doc's directory, or a --base / top-level directory;
  FILE  some tracked file has that basename;  SYM / SUB / FLAG  the token occurs in code (comments and docstrings
  removed), SUB as a quoted string.
Prose = *.md outside records (board/, docs/log/, generated/, research _scratch/, HANDOFF, CHANGELOG, agent memory,
template/ of a template repository). Doc type from the path.
Events per (doc, kind, token) key over first-parent commits:
  BREAK   valid -> invalid while the doc keeps the claim;  FIX the doc drops a broken claim (EDIT, or DOCDEL when the whole
  doc goes); RESTORE the code makes it valid again; COFIX the code change and the doc edit land in the same commit;
  BORN_STALE a claim added while invalid although it was valid earlier; NEVER claims never valid while present.
A doc moved in one commit carries its open intervals with it (same kind+token added in another doc that commit).
"""

import argparse
import collections
import io
import json
import os
import re
import subprocess
import sys
import tokenize

ap = argparse.ArgumentParser()
ap.add_argument("repo")
ap.add_argument("ref")
ap.add_argument("label")
ap.add_argument("out")
ap.add_argument("--base", action="append", default=[])
ap.add_argument("--pin-repo", help="template repository whose code (at the tag pinned in pyproject.toml) also counts")
A = ap.parse_args()


def git(*args):
    return subprocess.run(["git", "-C", A.repo, *args], capture_output=True, text=True, check=True).stdout


CODE_EXT = {
    ".py",
    ".swift",
    ".sh",
    ".bash",
    ".sql",
    ".toml",
    ".yml",
    ".yaml",
    ".json",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".cfg",
    ".ini",
    ".tex",
    ".jinja",
    ".m",
    ".h",
    ".c",
    ".cpp",
    ".rs",
    ".go",
    ".kt",
    ".java",
    ".plist",
    ".xcconfig",
}
CODE_NAMES = {"Makefile", "Justfile", "Dockerfile", "Brewfile", "Procfile"}
COMMENT_EXT = {".py", ".swift", ".sh", ".js", ".ts", ".rs", ".go", ".c", ".cpp", ".h", ".kt", ".java"}
FILE_EXT = "md|py|sh|swift|toml|json|ya?ml|sql|txt|tex|cfg|js|ts|tsx|jsonl|jinja|plist|xcconfig|csv|ini|lock|html|css"
EXCL = re.compile(
    r"(^|/)(board|log|logs|generated|_scratch|agent-memory|node_modules|\.scratch|fragments)(/|$)"
    r"|(^|/)(HANDOFF|CHANGELOG|INDEX)\.md$|^template/"
)


def doc_type(p):
    b = os.path.basename(p)
    if re.search(r"(^|/)(vendor|upstream[\w-]*)/", p):
        return "vendored"
    if re.search(r"(^|/)plans?/", p) or re.match(r"\d{4}-\d\d-\d\d", b):
        return "plan"
    if b in ("CLAUDE.md", "AGENTS.md", "HARNESS.md") or "/rules/" in "/" + p or p.startswith(".claude/rules/"):
        return "instruction"
    if "/agents/" in "/" + p:
        return "agent"
    if "/skills/" in "/" + p:
        return "skill"
    if "/research/" in "/" + p:
        return "research"
    if re.search(r"(^|/)(specs?|adr|design|architecture)(/|\b)", p, re.I) or re.search(
        r"SPEC|ARCHITECTURE|IMPLEMENTATION", b
    ):
        return "spec"
    if b.lower().startswith("readme") or b in ("CONTRIBUTING.md", "SECURITY.md"):
        return "readme"
    return "otherdoc"


def is_doc(p):
    return p.endswith(".md") and not EXCL.search(p)


def is_code(p):
    ext = os.path.splitext(p)[1]
    return (ext in CODE_EXT or os.path.basename(p) in CODE_NAMES) and not EXCL.search(p)


# ---- claim extraction -------------------------------------------------------------------------------------------
TICK = re.compile(r"(?<!`)`([^`\n]{1,200})`(?!`)")
RUNNER = re.compile(r"^(uv run|python3?|\./|scripts/|bash |sh |make |npm run|just |[\w.-]+ dev |dev )")
DEVSUB = re.compile(r"(?:^|\s)dev\s+([a-z][a-z0-9-]+)")
FLAGRE = re.compile(r"(?<![\w-])(--[a-zA-Z][\w-]+)")
PLACEHOLDER = re.compile(r"[<>{}*$~\[\]|]|NNN|YYYY|XXX|\.\.\.|^/|^[a-z]+://|@")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CAMEL = re.compile(r"^[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+$|^[a-z]+(?:[A-Z][a-z0-9]*)+$")
FILENAME = re.compile(rf"^[\w.-]+\.(?:{FILE_EXT})$")


def claims_in(text, is_md=True):
    out = set()
    if is_md:
        lines, fence = [], False
        for ln in text.splitlines():
            if ln.lstrip().startswith(("```", "~~~")):
                fence = not fence
                continue
            if fence:
                s = ln.strip()
                if RUNNER.match(s):
                    out |= _command(s)
                continue
            lines.append(ln)
        text = "\n".join(lines)
    text = text.replace("``", "`")
    for m in TICK.finditer(text):
        s = m.group(1).strip()
        if not s:
            continue
        if " " in s or RUNNER.match(s):
            if RUNNER.match(s) or s.startswith("--"):
                out |= _command(s)
            continue
        out |= _single(s)
    return out


def _command(s):
    out = set()
    for f in FLAGRE.findall(s):
        out.add(("FLAG", f))
    m = DEVSUB.search(s)
    if m:
        out.add(("SUB", m.group(1)))
    for w in s.split():
        if "/" in w and not PLACEHOLDER.search(w) and re.match(r"^[\w./-]+$", w) and not w.startswith("-"):
            out.add(("PATH", _normp(w)))
    return out


def _normp(w):
    w = re.sub(r":\d+.*$", "", w).split("#")[0]
    if w.startswith("./"):
        w = w[2:]
    return w.rstrip("/.,;")


def _single(s):
    s = s.rstrip(".,;:")
    if s.startswith("--") and re.fullmatch(r"--[a-zA-Z][\w-]+", s):
        return {("FLAG", s)}
    if PLACEHOLDER.search(s) or re.search(r"T-\d+|^#\d+", s):
        return set()
    if "/" in s:
        if re.match(r"^[\w./-]+(:\d+)?$", s) and re.search(r"[A-Za-z]", s):
            p = _normp(s)
            return {("PATH", p)} if p and p not in (".", "..") else set()
        return set()
    if FILENAME.match(s):
        return {("FILE", s)}
    if s.endswith("()"):
        s = s[:-2]
    if "." in s and all(IDENT.match(x) for x in s.split(".")):
        parts = s.split(".")
        last = parts[-1]
        if len(parts) >= 3 and all(x.islower() or "_" in x for x in parts):
            return {("PATH", "/".join(parts))}  # dotted module path
        return {("SYM", last)} if _symlike(last) else set()
    return {("SYM", s)} if IDENT.match(s) and _symlike(s) else set()


def _symlike(s):
    return len(s) >= 4 and (CAMEL.match(s) or ("_" in s.strip("_") and re.search(r"[a-z]", s, re.I)))


# ---- code tokens --------------------------------------------------------------------------------------------------
WORD = re.compile(r"--?[A-Za-z][\w-]*|[A-Za-z_][A-Za-z0-9_]*")
QUOTED = re.compile(r"[\"']([a-z][a-z0-9-]*)[\"']")
CMT_LINE = re.compile(r"^\s*(#|//|/\*|\*)")


def code_scan(path, text):
    """(tokens of code with comments/docstrings removed, comment+docstring text)."""
    ext = os.path.splitext(path)[1]
    comments = []
    code = text
    if ext == ".py":
        try:
            keep, prev = [], tokenize.INDENT
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.COMMENT or (
                    tok.type == tokenize.STRING
                    and prev in (tokenize.INDENT, tokenize.NEWLINE, tokenize.DEDENT)
                    and tok.string.lstrip("rbuRBU").startswith(('"""', "'''"))
                ):
                    comments.append(tok.string)
                else:
                    keep.append(tok.string)
                if tok.type not in (tokenize.NL, tokenize.COMMENT):
                    prev = tok.type
            code = " ".join(keep)
        except (tokenize.TokenError, IndentationError, SyntaxError):
            pass
    elif ext in COMMENT_EXT or os.path.basename(path) in CODE_NAMES or ext in (".toml", ".yml", ".yaml", ".sql"):
        keep = []
        for ln in text.splitlines():
            (comments if CMT_LINE.match(ln) else keep).append(ln)
        code = "\n".join(keep)
    toks = set(WORD.findall(code)) | {"q:" + q for q in QUOTED.findall(code)}
    cm = "\n".join(comments) if ext in COMMENT_EXT else ""
    return toks, cm


# ---- blob reader --------------------------------------------------------------------------------------------------
class Cat:
    def __init__(self):
        self.p = subprocess.Popen(
            ["git", "-C", A.repo, "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )

    def get(self, spec):
        self.p.stdin.write((spec + "\n").encode())
        self.p.stdin.flush()
        hdr = self.p.stdout.readline().decode()
        if hdr.endswith("missing\n"):
            return None
        size = int(hdr.split()[2])
        data = self.p.stdout.read(size)
        self.p.stdout.read(1)
        if size > 1_500_000 or b"\0" in data[:8000]:
            return None
        return data.decode("utf-8", "replace")


cat = Cat()

hist = [
    ln.split("\t", 3)
    for ln in git("log", "--first-parent", "--reverse", "--format=%H\t%ct\t%an\t%s", A.ref).splitlines()
]
files, dirs, bases = set(), collections.Counter(), collections.Counter()
ftoks, tokcount = {}, collections.Counter()
doc_claims = {}  # path -> set((kind, token))
ever_valid = set()  # (kind, token, docdir) seen valid
first_seen = {}  # key -> (idx, ts)
state = {}  # key -> valid bool at previous commit
openv = {}  # key -> break record (open)
events = []
snapshots = []
tops = set()


def add_file(p, sha_spec):
    files.add(p)
    parts = p.split("/")
    for i in range(1, len(parts)):
        dirs["/".join(parts[:i])] += 1
    bases[parts[-1]] += 1
    if len(parts) > 1:
        tops.add(parts[0])
    text = None
    if is_code(p):
        text = cat.get(sha_spec)
        if text is not None:
            t, cm = code_scan(p, text)
            ftoks[p] = t
            for x in t:
                tokcount[x] += 1
            if cm:
                cl = {("c", k, v) for k, v in claims_in(cm, is_md=False)}
                if cl:
                    doc_claims[p] = cl
    if is_doc(p):
        text = cat.get(sha_spec) if text is None else text
        if text is not None:
            doc_claims[p] = {("d", k, v) for k, v in claims_in(text)}


def del_file(p):
    files.discard(p)
    parts = p.split("/")
    for i in range(1, len(parts)):
        d = "/".join(parts[:i])
        dirs[d] -= 1
        if dirs[d] <= 0:
            del dirs[d]
    bases[parts[-1]] -= 1
    for x in ftoks.pop(p, ()):
        tokcount[x] -= 1
    doc_claims.pop(p, None)


PIN = re.compile(r"agent-harness\s*@\s*git\+\S+?@(v[\d.]+)|agent-harness\s*=\s*\{[^}]*tag\s*=\s*\"(v[\d.]+)\"")
pin_cache, extra = {}, set()


def pin_tokens(tag):
    if tag not in pin_cache:
        toks = set()
        g = ["git", "-C", A.pin_repo]
        for p in subprocess.run(
            [*g, "ls-tree", "-r", "--name-only", tag], capture_output=True, text=True, check=True
        ).stdout.splitlines():
            if is_code(p) or p.startswith("template/"):
                t = subprocess.run([*g, "show", f"{tag}:{p}"], capture_output=True).stdout.decode("utf-8", "replace")
                toks |= code_scan(p, t)[0]
        pin_cache[tag] = toks
    return pin_cache[tag]


def valid(kind, tok, docpath):
    if kind == "PATH":
        cands = [tok, os.path.normpath(os.path.join(os.path.dirname(docpath), tok))]
        cands += [f"{b}/{tok}" for b in A.base] + [f"{t}/{tok}" for t in tops]
        return any(c in files or c in dirs for c in cands)
    if kind == "FILE":
        return bases.get(tok, 0) > 0
    if kind == "SUB":
        return tokcount.get("q:" + tok, 0) > 0 or "q:" + tok in extra
    return tokcount.get(tok, 0) > 0 or tok in extra


prev_sha = None
for idx, (sha, ts, an, subj) in enumerate(hist):
    ts = int(ts)
    if prev_sha is None:
        for ln in git("ls-tree", "-r", sha).splitlines():
            meta, p = ln.split("\t", 1)
            if meta.split()[1] == "blob":
                add_file(p, meta.split()[2])
        changed_docs = set(doc_claims)
    else:
        out = git("diff-tree", "-r", "--no-renames", "--name-status", prev_sha, sha)
        changed_docs = set()
        for ln in out.splitlines():
            st, p = ln.split("\t", 1)
            if p in files:
                del_file(p)
            if st != "D":
                add_file(p, f"{sha}:{p}")
            changed_docs.add(p)
    if A.pin_repo and (prev_sha is None or "pyproject.toml" in changed_docs):
        m = PIN.search(cat.get(f"{sha}:pyproject.toml") or "")
        extra = pin_tokens(m.group(1) or m.group(2)) if m else set()
    # evaluate
    cur = {}
    for p, cl in doc_claims.items():
        dt = "docstring" if cl and next(iter(cl))[0] == "c" else doc_type(p)
        for src, kind, tok in cl:  # noqa: B007 - research script
            cur[(p, kind, tok)] = (valid(kind, tok, p), dt)
    added = {k for k in cur if k not in state}
    removed = {k for k in state if k not in cur}
    added_by_ct = collections.defaultdict(list)
    for k in added:
        added_by_ct[(k[1], k[2])].append(k)
    # carry open intervals across doc moves
    for k in list(removed):
        moved = [n for n in added_by_ct.get((k[1], k[2]), ()) if os.path.basename(n[0]) == os.path.basename(k[0])]
        if k in openv and k[0] not in doc_claims and moved:
            nk = moved[0]
            added_by_ct[(k[1], k[2])].remove(nk)
            openv[nk] = openv.pop(k)
            first_seen[nk] = first_seen.get(k, (idx, ts))
            state[nk] = state[k]
            added.discard(nk)
            removed.discard(k)
    base = dict(repo=A.label, idx=idx, sha=sha[:10], ts=ts, author=an, subj=subj[:100])
    for k in removed:
        if k in openv:
            r = openv.pop(k)
            how = "DOCDEL" if k[0] not in doc_claims else "EDIT"
            events.append(
                dict(r, ev="FIX", how=how, fix_idx=idx, fix_sha=sha[:10], fix_ts=ts, fix_author=an, fix_subj=subj[:100])
            )
        elif state[k][0] and not valid(k[1], k[2], k[0]) and k[0] in doc_claims:
            events.append(dict(base, ev="COFIX", doc=k[0], kind=k[1], tok=k[2], dtype=state[k][1]))
        state.pop(k, None)
    for k in added:
        first_seen[k] = (idx, ts)
        v, dt = cur[k]
        if not v and (k[1], k[2]) in ever_valid:
            events.append(dict(base, ev="BORN_STALE", doc=k[0], kind=k[1], tok=k[2], dtype=dt))
    for k, (v, dt) in cur.items():
        if v:
            ever_valid.add((k[1], k[2]))
        if k in state and k not in added:
            was = state[k][0]
            if was and not v:
                openv[k] = dict(
                    base,
                    ev="BREAK",
                    doc=k[0],
                    kind=k[1],
                    tok=k[2],
                    dtype=dt,
                    born_idx=first_seen[k][0],
                    born_ts=first_seen[k][1],
                )
            elif not was and v and k in openv:
                r = openv.pop(k)
                events.append(
                    dict(
                        r,
                        ev="FIX",
                        how="RESTORE",
                        fix_idx=idx,
                        fix_sha=sha[:10],
                        fix_ts=ts,
                        fix_author=an,
                        fix_subj=subj[:100],
                    )
                )
        state[k] = (v, dt)
    if idx % max(1, len(hist) // 12) == 0 or idx == len(hist) - 1:
        snap = collections.Counter()
        for k, (v, dt) in cur.items():
            age = (ts - first_seen[k][1]) / 86400
            st = "valid" if v else ("stale" if (k[1], k[2]) in ever_valid else "never")
            snap[f"{dt}|{st}|{'old' if age > 30 else 'new'}"] += 1
        snapshots.append(dict(idx=idx, ts=ts, counts=dict(snap)))
    prev_sha = sha
    if idx % 50 == 0:
        print(A.label, idx, len(hist), len(cur), len(openv), file=sys.stderr)

for k, r in openv.items():  # noqa: B007 - research script
    events.append(dict(r, ev="OPEN"))
never = collections.Counter()
final = collections.Counter()
for k, (v, dt) in state.items():
    final[(dt, k[1], "valid" if v else ("stale" if (k[1], k[2]) in ever_valid else "never"))] += 1
json.dump(
    dict(
        label=A.label,
        n_commits=len(hist),
        first_ts=int(hist[0][1]),
        last_ts=int(hist[-1][1]),
        head=hist[-1][0][:10],
        events=events,
        snapshots=snapshots,
        final={"|".join(k): v for k, v in final.items()},
    ),
    open(A.out, "w"),  # noqa: SIM115 - research script
    indent=0,
)
print(A.label, "events", collections.Counter(e["ev"] for e in events), file=sys.stderr)
