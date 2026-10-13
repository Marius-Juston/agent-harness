"""#183's pre-registered grader and report over `dev eval` run records (.devkit/state/evals/run-*.json).

A trial passes when some Bash command matches PASS_RX (a library check), or, in an arm run with the #172 guard
installed (--guarded), when some command is a `uv add`: the guard is a PreToolUse hook that checks every `uv add`, and
the run record keeps commands but not hook output. Reports the pass rate and pass^3 per scenario, and with --after the
paired difference over scenarios with a seeded bootstrap interval.

    uv run python evals/reuse/grade.py BASELINE.json... [--after AFTER.json... [--guarded]]
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path

PASS_RX = re.compile(r'dev reuse (check|"|[a-z])')
UV_ADD_RX = re.compile(r"\buv add\b")
SCENARIO_PREFIX = "reuse-"


def trial_passes(commands: list[str], guarded: bool) -> bool:
    if any(PASS_RX.search(c) for c in commands):
        return True
    return guarded and any(UV_ADD_RX.search(c) for c in commands)


def load_arm(paths: list[Path], guarded: bool) -> dict[str, list[bool]]:
    out: dict[str, list[bool]] = {}
    for p in paths:
        results = json.loads(p.read_text())["results"]
        for key, trials in results.items():
            if key.startswith(SCENARIO_PREFIX) and key.endswith(":trials"):
                name = key.removesuffix(":trials")
                out.setdefault(name, []).extend(trial_passes(t["commands"], guarded) for t in trials)
    return out


def rate(xs: list[bool]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def paired_bootstrap(before: list[float], after: list[float], n: int = 10_000, seed: int = 183) -> tuple[float, float]:
    """95% percentile interval of mean(after - before), resampling scenarios with replacement."""
    rng = random.Random(seed)  # noqa: S311 - a seeded statistical resample, not a secret
    diffs = [a - b for a, b in zip(after, before, strict=True)]
    means = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def table(arm: dict[str, list[bool]]) -> list[str]:
    rows = ["| scenario | passes | rate | pass^3 |", "|---|---|---|---|"]
    rows += [
        f"| {k} | {sum(v)}/{len(v)} | {rate(v):.2f} | {'yes' if all(v) else 'no'} |" for k, v in sorted(arm.items())
    ]
    allt = [x for v in arm.values() for x in v]
    rows.append(
        f"| **all** | {sum(allt)}/{len(allt)} | {rate(allt):.2f} | {sum(all(v) for v in arm.values())} of {len(arm)} |"
    )
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("baseline", nargs="+", type=Path)
    ap.add_argument("--after", nargs="+", type=Path, default=[])
    ap.add_argument("--guarded", action="store_true", help="the after arm ran with the #172 uv add guard installed")
    a = ap.parse_args(argv)
    base = load_arm(a.baseline, guarded=False)
    print("## Baseline\n", *table(base), sep="\n")
    if a.after:
        after = load_arm(a.after, guarded=a.guarded)
        if sorted(after) != sorted(base):
            print(f"scenario sets differ: {sorted(set(base) ^ set(after))}", file=sys.stderr)
            return 1
        names = sorted(base)
        b, f = [rate(base[k]) for k in names], [rate(after[k]) for k in names]
        lo, hi = paired_bootstrap(b, f)
        print("\n## After\n", *table(after), sep="\n")
        overall = rate([x for v in after.values() for x in v])
        print(
            f"\npaired difference {sum(f) / len(f) - sum(b) / len(b):+.2f} [{lo:+.2f}, {hi:+.2f}] (95%, 10000 resamples, seed 183)"
        )
        print(f"target >= 0.80 met: {'yes' if overall >= 0.8 else 'no'} ({overall:.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
