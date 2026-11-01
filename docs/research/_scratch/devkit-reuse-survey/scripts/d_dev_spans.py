"""(d) #229: how many backticked `uv run dev` spans in today's tree does devkit's own argparse parser reject,
with no syntax rules (A, strict) and with r2's loosened parser plus its five rules (B, the #220 worker)?

usage: uv run python docs/research/_scratch/devkit-reuse-survey/scripts/d_dev_spans.py
Reads the same file types check_dev_commands reads (md/sh/yml/json/jinja, generated dir skipped), plus planted
probes. Prints counts by error class and every B flag. Run from the repository root.
"""

import contextlib
import io
import json
import re
import shlex
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
import devkit.cli as cli  # noqa: E402


def _loosen(p):
    import argparse

    for a in p._actions:
        if isinstance(a, argparse._SubParsersAction):
            a.required = False
            for sp in a.choices.values():
                _loosen(sp)
            continue
        a.required, a.type = False, None
        if not a.option_strings:
            a.nargs = "?" if a.nargs is None else ("*" if a.nargs == "+" else a.nargs)


LOOSE = cli.build()
_loosen(LOOSE)
WORKER = ROOT / "docs/research/_scratch/adr-drift/scripts/r2_cli_worker.py"
SPAN = re.compile(r"`(uv run dev [^`\n]+)`")
PROBES = [
    ("probe:bad-positional", '`uv run dev board block 5 "the reason"`'),  # #229 DONE WHEN: must be reported
    ("probe:bad-flag", "`uv run dev board block 5 --why x`"),
    ("probe:good", '`uv run dev board block 5 --reason "x"`'),
    ("probe:placeholder", '`uv run dev board block <n> --reason "<why>"`'),
]


def files():
    out = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "*.md",
            "*.sh",
            "*.yml",
            "*.json",
            "*.jinja",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [p for p in out if not p.startswith("docs/generated/") and (ROOT / p).is_file()]


def strict(span):
    """A: shlex + devkit's parser as built, nothing loosened, no rules."""
    try:
        toks = shlex.split(span)[3:]
    except ValueError as e:
        return f"shlex: {e}"
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            _, extra = cli.build().parse_known_args(toks)
    except SystemExit:
        last = (err.getvalue().strip().splitlines() or ["help/exit"])[-1]
        last = re.sub(r"argument [^:]+:", "argument X:", last)
        return re.sub(r"'[^']*'|\S+$", "_", last.split(": error: ")[-1])[:60]
    return f"leftover {extra}" if extra else None


def loose_only(span):
    """C: rule 1 alone (r2 worker's loosen(): nothing required, no type conversion), no rules 2-5."""
    try:
        toks = shlex.split(span)[3:]
    except ValueError:
        return "shlex"
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            _, extra = LOOSE.parse_known_args(toks)
    except SystemExit:
        return "error"
    return "leftover" if extra else None


def main():
    docs, spans = [], []
    for p in files():
        text = (ROOT / p).read_text(encoding="utf-8", errors="replace")
        docs.append([p, text])
        spans += [(p, m.group(1)) for m in SPAN.finditer(text)]
    docs += [list(x) for x in PROBES]
    spans += [(s, SPAN.search(t).group(1)) for s, t in PROBES]

    tree = spans[: len(spans) - len(PROBES)]
    a_tree = sum(1 for _s, sp in tree if strict(sp))
    c_tree = Counter(loose_only(sp) for _s, sp in tree)
    c_out = sum(1 for s, sp in tree if loose_only(sp) and not s.startswith("docs/research/"))
    print(f"A strict, tree only: {a_tree} of {len(tree)}")
    print(f"C rule 1 only, tree: {dict(c_tree)}; outside docs/research: {c_out}")
    print("C on probes:", [(s, loose_only(sp)) for s, sp in spans[len(tree) :]])
    a = Counter()
    a_fail = 0
    for _src, sp in spans:
        r = strict(sp)
        if r:
            a_fail += 1
            a[r if not r.startswith("leftover") else "leftover positional/flag"] += 1
    n_tree = len(spans) - len(PROBES)
    print(f"tree spans: {n_tree} in {len(docs) - len(PROBES)} files; probes: {len(PROBES)}")
    print(f"A strict own-parser rejects: {a_fail} of {len(spans)} (incl. probes)")
    for k, v in a.most_common(12):
        print(f"   {v:4d}  {k}")

    with tempfile.TemporaryDirectory() as d:
        inp, outp = Path(d, "in.json"), Path(d, "out.json")
        inp.write_text(json.dumps(docs))
        subprocess.run([sys.executable, str(WORKER), str(ROOT), str(inp), str(outp)], check=True)
        res = json.loads(outp.read_text())
    flags = [f for f in res["findings"] if f["prefix"] == "uv" and "`" in f["text"]]
    print(f"B loosened parser + five rules (#220 worker): {len(flags)} flags")
    outside = sum(1 for f in flags if not f["src"].startswith(("docs/research/", "probe:")))
    print(f"   outside docs/research and probes: {outside}")
    for f in flags:
        print(f"   {f['kind']} {f['src']}:{f['line']}  {f['span'][:70]}  -> {f['detail']}")


main()
