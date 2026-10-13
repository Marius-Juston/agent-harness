"""Worker for r2_cli_replay.py: checks `dev` command spans in a batch of texts against ONE devkit version's parser.

usage: <venv python> r2_cli_worker.py DEVKIT_ROOT IN.json OUT.json
IN.json = [[source, text], ...]. DEVKIT_ROOT holds the devkit/ package of the version under test; it goes first on
sys.path so the commit's own parser is used (asserted). Kinds:
  K1 unknown command, K2 unknown subcommand          (today's gate checks these for `uv run dev` in md/sh/yml/json/jinja)
  K3 unknown option flag (r1 proposal)               K4 extra positional argument (argparse leftovers)
  K5 argparse error left after loosening (bad choice, option missing its value)
`prefix` is "uv" for `uv run dev X`, "bare" for a backticked span that starts with `dev X`.
"""

import argparse
import contextlib
import io
import json
import re
import shlex
import sys

root, inp, outp = sys.argv[1:4]
PROSE_K4 = "--prose" in sys.argv  # also full-parse spans outside backticks (cut at shell/sentence punctuation)
sys.path.insert(0, root)
import devkit.cli as cli  # noqa: E402 - research script, kept as run

assert cli.__file__.startswith(root), cli.__file__  # noqa: S101 - research script, kept as run
P = cli.build()


def opts(p):
    own, subs = set(), {}
    for a in p._actions:
        if isinstance(a, argparse._SubParsersAction):
            subs.update(a.choices)
        else:
            own.update(a.option_strings)
    return own, subs


def loosen(p):
    """Parse for leftovers only: no required options, optional positionals, no type conversion. Prose often names
    a command without its arguments or with placeholders (N, <n>); those are not drift."""
    for a in p._actions:
        if isinstance(a, argparse._SubParsersAction):
            a.required = False
            for sp in a.choices.values():
                loosen(sp)
            continue
        a.required = False
        a.type = None
        if not a.option_strings:
            if a.nargs is None:
                a.nargs = "?"
            elif a.nargs == "+":
                a.nargs = "*"


_, TOP = opts(P)
LOOSE = cli.build()
loosen(LOOSE)
SHELL = re.compile(r"&&|\|\||\s\|\s|;|\s>|2>|<<|\s&|,\s|(?<!\.)\.(?:\s|$)")
HIT = re.compile(r"(?<![\w/.$-])(uv run )?dev ([a-z][a-z-]*)")
FLAG = re.compile(r"(?<![\w<\[/=-])(--?[a-zA-Z][\w-]*)")
CUT = re.compile(r"\s(?:\||;|&&|#)|\)|`")


def norm(tokens):
    out = []
    for t in tokens:
        if t in ("...", "…") or t.endswith(("...", "..")) or any(ch in t for ch in "|{}()*"):
            return None  # elided or alternatives: not a complete command
        opt = t.startswith("[") and t.endswith("]")
        t = t.strip("[]")
        if re.fullmatch(r"<[^>]*>", t):
            if opt:
                continue
            t = "1"
        t = re.sub(r"<[^>]*>", "x", t)
        out.append(t)
    return out


def check(src, text):
    res = []
    for ln, line in enumerate(text.splitlines(), 1):
        for m in HIT.finditer(line):
            prefix = "uv" if m.group(1) else "bare"
            before = line[: m.start()]
            in_tick = before.count("`") % 2 == 1
            if prefix == "bare" and not before.endswith("`"):
                continue  # bare `dev X` only when a code span starts with it
            cmd = m.group(2)
            rest = line[m.end() :]
            if in_tick:
                rest = rest.split("`")[0]
                complete = True
            else:
                c = CUT.search(rest)
                rest = rest[: c.start()] if c else rest
                complete = False
            span = f"{'uv run ' if m.group(1) else ''}dev {cmd}{rest}".strip()
            if cmd not in TOP:
                res.append(
                    dict(src=src, line=ln, text=line.strip()[:200], kind="K1", prefix=prefix, span=span, detail=cmd)
                )
                continue
            own, subs = opts(TOP[cmd])
            words = rest.split()
            sub = None
            if words:
                w0 = words[0].rstrip("*")
                alts = [a for a in re.split(r"[|/]", w0) if a]
                if words[0].endswith("*") and len(alts) <= 1:
                    alts = [x for x in subs if x.startswith(w0)][:1] or alts  # settings.json permission glob
                if len(alts) > 1 or w0 != words[0]:
                    if subs and alts and all(a in subs for a in alts):
                        continue  # `code outline|show`, `board list*`: every alternative is a subcommand
                    if not alts:
                        continue  # `dev code *`
            if subs:
                if words and words[0] in subs:
                    sub = words[0]
                    own = own | opts(subs[sub])[0]
                elif words and not words[0].startswith(("-", "<", "[")):
                    res.append(
                        dict(
                            src=src,
                            line=ln,
                            text=line.strip()[:200],
                            kind="K2",
                            prefix=prefix,
                            span=span,
                            detail=words[0],
                        )
                    )
                    continue
            for fl in FLAG.findall(rest):
                if fl not in own and fl not in ("-h", "--help"):
                    res.append(
                        dict(src=src, line=ln, text=line.strip()[:200], kind="K3", prefix=prefix, span=span, detail=fl)
                    )
            if not complete and not PROSE_K4:
                continue
            sh = SHELL.search(rest)
            rest = rest[: sh.start()] if sh else rest
            try:
                toks = shlex.split(rest)
            except ValueError:
                continue
            toks = norm(toks)
            if toks is None:
                continue
            err = io.StringIO()
            try:
                with contextlib.redirect_stderr(err):
                    _, extra = LOOSE.parse_known_args([cmd, *toks])
            except SystemExit:
                msg = err.getvalue().strip().splitlines()[-1:] or [""]
                if "expected one argument" in msg[0]:
                    continue  # prose names an option without its value
                res.append(
                    dict(
                        src=src,
                        line=ln,
                        text=line.strip()[:200],
                        kind="K5",
                        prefix=prefix,
                        span=span,
                        detail=msg[0][:120],
                    )
                )
                continue
            pos = [e for e in extra if not e.startswith("-")]
            if pos:
                res.append(
                    dict(
                        src=src,
                        line=ln,
                        text=line.strip()[:200],
                        kind="K4",
                        prefix=prefix,
                        span=span,
                        detail=" ".join(pos)[:80],
                    )
                )
    return res


docs = json.load(open(inp))  # noqa: SIM115 - research script, kept as run
out = []
spans = 0
for src, text in docs:
    spans += sum(1 for ln in text.splitlines() for _ in HIT.finditer(ln))
    out += check(src, text)
json.dump({"findings": out, "hits": spans, "commands": sorted(TOP)}, open(outp, "w"))  # noqa: SIM115 - research script, kept as run
