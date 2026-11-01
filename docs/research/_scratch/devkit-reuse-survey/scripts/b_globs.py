"""(b) #78: where does devkit.rules' `paths:` matcher disagree with Claude Code's, and does pathspec close it?

usage (repository root):
  python3 -I docs/research/_scratch/devkit-reuse-survey/scripts/b_extract_ignore.py "$(readlink -f "$(which claude)")" \
      <scratch>/ignore.js
  uv run python docs/research/_scratch/devkit-reuse-survey/scripts/b_globs.py <scratch>/ignore.js

CLAUDE = Claude Code 2.1.316's own pipeline, re-implemented from its bundle: the `paths:` value is split at top-level
commas (string or list), braces are expanded, a trailing `/**` is cut, then node-ignore's `ignores(relpath)` decides
(run on the extracted module, so the matcher itself is Claude Code's). DEVKIT = devkit.rules (globs_of + matches).
PATHSPEC = the same preprocessing, then pathspec's gitignore spec (pathspec 1.1.1, a dev-only transitive dependency).
"""

import json
import subprocess
import sys
from pathlib import Path

import pathspec

from devkit import rules

HERE = Path(__file__).parent

# (paths: value as written in frontmatter, relative file path)
PROBES = [
    (["*.md"], "a.md"),
    (["*.md"], "docs/a.md"),  # docs table says "Markdown files in the project root"
    (["src/**"], "src/a/b.ts"),
    (["src/**/*.ts"], "src/a.ts"),
    (["src/*.ts"], "src/a/b.ts"),
    (["**/*.ts"], "a.ts"),
    ("src/**, lib/**", "lib/x.py"),  # comma-separated string, which the docs allow
    (["*.{ts,tsx}"], "web/a.tsx"),
    (["src/*.{ts,tsx}"], "src/a.tsx"),
    (["/README.md"], "README.md"),
    (["docs/"], "docs/a.md"),
    (["tests/**", "!tests/fixtures/**"], "tests/fixtures/a.py"),
    (["photos \\[2024/**"], "photos [2024/a.jpg"),
    ([".env*"], "config/.env.local"),
    (["src"], "src/a.py"),
    (["a/**/b"], "a/b"),
    (["src/*"], "src/a/b.ts"),
    (["*.MD"], "a.md"),
    (["devkit/*.py"], "devkit/rules.py"),
    (["plugins/**/agents/*.md"], "plugins/harness-ops/agents/builder.md"),
]


def split_and_expand(value):
    """Claude Code's ja()+Gm(): flatten lists, split strings at top-level commas, expand {a,b} (no budget here)."""
    items = value if isinstance(value, list) else [value]
    out = []
    for it in items:
        parts, cur, depth = [], "", 0
        for ch in str(it):
            if ch == "," and depth == 0:
                parts.append(cur.strip())
                cur = ""
                continue
            depth += ch == "{"
            depth -= ch == "}"
            cur += ch
        parts.append(cur.strip())
        for p in filter(None, parts):
            out += rules.expand_braces(p)
    return [g[:-3] if g.endswith("/**") else g for g in out if g]


def devkit(value, rel):
    return any(rules.matches(rel, g) for g in rules.globs_of({"paths": value}))


def pathspec_match(value, rel):
    return pathspec.PathSpec.from_lines("gitignore", split_and_expand(value)).match_file(rel)


def gitignorespec(value, rel):
    """pathspec.GitIgnoreSpec (git's edge cases), patterns and path casefolded: node-ignore's default ignorecase."""
    globs = [g.casefold() for g in split_and_expand(value)]
    return pathspec.GitIgnoreSpec.from_lines(globs).match_file(rel.casefold())


def with_parents(value, rel):
    """gitignorespec, plus git's rule that a file under an excluded parent directory cannot be re-included."""
    globs = [g.casefold() for g in split_and_expand(value)]
    spec = pathspec.GitIgnoreSpec.from_lines(globs)
    parts = rel.casefold().split("/")
    parents = ["/".join(parts[:i]) + "/" for i in range(1, len(parts))]
    return any(spec.match_file(p) for p in parents) or spec.match_file(rel.casefold())


ARMS = {"devkit": devkit, "pathspec": pathspec_match, "GitIgnoreSpec+casefold": gitignorespec, "+parents": with_parents}


def run(name, probes, ignore_js):
    cases = [[split_and_expand(v), rel] for v, rel in probes]
    ref = json.loads(
        subprocess.run(
            ["node", str(HERE / "b_ignore_run.js"), ignore_js],
            input=json.dumps(cases),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    miss = dict.fromkeys(ARMS, 0)
    print(f"\n[{name}] paths | file | claude | " + " | ".join(ARMS))
    for (v, rel), c in zip(probes, ref, strict=True):
        got = {k: f(v, rel) for k, f in ARMS.items()}
        for k, g in got.items():
            miss[k] += g != c
        mark = " <-" if any(g != c for g in got.values()) else ""
        print(f"{v!r} | {rel} | {c} | " + " | ".join(str(g) for g in got.values()) + mark)
    print(f"[{name}] probes {len(probes)}: disagree with Claude Code: {miss}")


# Held out: written by the research-auditor after reading v1 of the note (PR #427 audit, 2026-11-01), so no arm was
# tuned on them.
HELDOUT = [
    (["**/test_*.py"], "tests/test_a.py"),
    (["docs/**/*.md"], "docs/a.md"),
    (["*.py"], "a/b/c.py"),
    (["foo/*/bar"], "foo/x/y/bar"),
    (["?.md"], "docs/a.md"),
    (["[abc].md"], "b.md"),
    (["a/b"], "x/a/b"),
    (["**/node_modules"], "web/node_modules/x.js"),
    (["src/**/*.test.ts"], "src/a/b.test.ts"),
    (["!*.md"], "a.md"),
    (["#foo"], "#foo"),
    (["\\#foo"], "#foo"),
    ("*.md, !README.md", "README.md"),
    (["docs/*"], "docs/sub/a.md"),
    (["src/**/*"], "src/a.py"),
    (["**/*.{md,mdx}"], "a/b.mdx"),
    (["{src,lib}/**"], "lib/a.py"),
    (["Makefile"], "sub/Makefile"),
    (["*.Py"], "a.PY"),
    ([".github/workflows/*.yml"], ".github/workflows/ci.yml"),
    (["a/**/b/**"], "a/x/b/c"),
    (["*/a.md"], "x/y/a.md"),
    (["src/a.ts/"], "src/a.ts"),
    (["tests/"], "tests/a.py"),
    (["src/[!a]*.ts"], "src/b.ts"),
    (["*.py", "!keep.py"], "keep.py"),
    (["docs/**", "!docs/keep.md"], "docs/keep.md"),
    (["docs/*.md", "!docs/keep.md"], "docs/keep.md"),
    (["src/", "!src/a.py"], "src/a.py"),
    (["**/*.md"], "a/b/c/d.md"),
    (["src/**/**/x"], "src/x"),
    (["a/*.md"], "A/b.md"),
    (["*"], "a/b"),
    (["src/**/test/**"], "src/test/a.py"),
    (["src/{a,b}/{c,d}.ts"], "src/b/d.ts"),
]

if __name__ == "__main__":
    run("in-sample", PROBES, sys.argv[1])
    run("held-out", HELDOUT, sys.argv[1])
