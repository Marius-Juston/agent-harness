"""Failure signatures that recur across agent runs (#497): the same mistake made by several runs is a gap that a
doc, a docstring, a helper or a guard could close once.

Usage: python3 recurring.py <transcript dir or name suffix> [...] [--min-runs N] [--show]

A signature is the failure's key line (the last `XError: ...` line of a traceback, a CLI `error:` line, or the
first line of a refusal), with paths, numbers, quoted values and hex ids replaced by placeholders. Prints, per
directory: failing calls, distinct signatures, and the signatures seen in at least N runs (default 3) with their
run count, calls, the calls and minutes until the next call that does not fail, and the class from tx_docs.
Without --show the signature text is withheld (private repositories); --show prints it truncated.
"""

import hashlib
import re
import sys
from collections import defaultdict

import tx_docs as t

KEY = [
    re.compile(r"^\s*(\w+(Error|Exception)): .*$", re.M),
    re.compile(r"^.*\berror: .*$", re.M),
    re.compile(r"^.*(No such file or directory|File does not exist|cannot access).*$", re.M),
]


def signature(text, fc):
    if fc == "guard":
        line = text.strip().splitlines()[0] if text.strip() else ""
    else:
        line = ""
        for rx in KEY:
            hits = rx.findall(text) if rx.groups == 0 else [m.group(0) for m in rx.finditer(text)]
            if hits:
                line = hits[-1]
                break
        line = line or (text.strip().splitlines() or [""])[0]
    s = re.sub(r"(/|~)[\w.@+-]+(/[\w.@+-]*)+", "<path>", line)
    s = re.sub(r"'[^']*'|\"[^\"]*\"|`[^`]*`", "<v>", s)
    s = re.sub(r"\b[0-9a-f]{7,}\b", "<id>", s)
    s = re.sub(r"\d+", "N", s)
    return re.sub(r"\s+", " ", s).strip()[:160]


def main(argv):
    show = "--show" in argv
    argv = [a for a in argv if a != "--show"]
    min_runs = 3
    if "--min-runs" in argv:
        i = argv.index("--min-runs")
        min_runs = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2 :]
    import pathlib

    for d in argv:
        p = pathlib.Path(d).expanduser()
        if not p.exists():
            p = next(q for q in (pathlib.Path.home() / ".claude" / "projects").iterdir() if q.name.endswith(d))
        runs_of = defaultdict(set)
        calls_of = defaultdict(int)
        rec_calls = defaultdict(int)
        rec_min = defaultdict(float)
        cls_of = {}
        nfail = 0
        nruns = 0
        for f in sorted(p.rglob("*.jsonl")):
            evs, _ = t.events(f)
            if not evs:
                continue
            nruns += 1
            for k, e in enumerate(evs):
                fc = t.classify(e)
                if not fc or fc == "gate":
                    continue
                nfail += 1
                sig = signature(e["result"], fc)
                runs_of[sig].add(str(f))
                calls_of[sig] += 1
                cls_of[sig] = fc
                j = next((j for j in range(k + 1, len(evs)) if not t.classify(evs[j])), None)
                if j is not None:
                    rec_calls[sig] += j - k
                    m = t.minutes(e.get("rts") or e["ts"], evs[j].get("rts") or evs[j]["ts"])
                    rec_min[sig] += m or 0
        rec = sorted((s for s in runs_of if len(runs_of[s]) >= min_runs), key=lambda s: -len(runs_of[s]))
        print(
            f"== {p.name[-12:]}: runs={nruns} failing calls={nfail} signatures={len(runs_of)} "
            f"recurring (>= {min_runs} runs)={len(rec)} covering {sum(calls_of[s] for s in rec)} calls"
        )
        for s in rec:
            sid = hashlib.sha256(s.encode()).hexdigest()[:6]
            text = f"{sid} {s[:110]}" if show else sid
            print(
                f"  runs={len(runs_of[s]):4d} calls={calls_of[s]:4d} recovery calls={rec_calls[s]:4d} "
                f"min={rec_min[s]:6.1f} class={cls_of[s]:12s} {text}"
            )


if __name__ == "__main__":
    main(sys.argv[1:])
