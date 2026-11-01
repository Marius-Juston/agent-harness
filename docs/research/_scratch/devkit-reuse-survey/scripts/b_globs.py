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


def main():
    cases = [[split_and_expand(v), rel] for v, rel in PROBES]
    ref = json.loads(
        subprocess.run(
            ["node", str(HERE / "b_ignore_run.js"), sys.argv[1]],
            input=json.dumps(cases),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    dk_miss = ps_miss = gs_miss = 0
    print("paths | file | claude | devkit | pathspec | gitignorespec+casefold")
    for (v, rel), c in zip(PROBES, ref, strict=True):
        d, p, g = devkit(v, rel), pathspec_match(v, rel), gitignorespec(v, rel)
        dk_miss += d != c
        ps_miss += p != c
        gs_miss += g != c
        mark = " <-" if c != d or c != p or c != g else ""
        print(f"{v!r} | {rel} | {c} | {d} | {p} | {g}{mark}")
    print(
        f"probes {len(PROBES)}: disagree with Claude Code: devkit {dk_miss}, pathspec {ps_miss},"
        f" GitIgnoreSpec+casefold {gs_miss}"
    )


main()
