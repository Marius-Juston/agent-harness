"""Wall time of each gate stage but the test run, outside the gate lock (#520).

Runs the gate's own stage list (devkit.gate.stages, as `dev check` builds it) and times each callable; the last
stage (pytest) is skipped unless --tests is given, so this never competes with the builders' gates for long.
Also times the gate's fixed costs around the stages: tree_key and the test-selection fingerprint (collect-only).

Usage (from the repository root): uv run python docs/research/_scratch/gate-speed/scripts/time_stages.py [--tests]
"""

import sys
import time

from devkit import checks, cli, gate


def main() -> None:
    base = checks.merge_base()
    t = time.perf_counter()
    gate.tree_key(base)
    print(f"{'(tree_key)':40s} {time.perf_counter() - t:6.2f} s")
    t = time.perf_counter()
    gate.selection(False)
    print(f"{'(selection: pytest --collect-only)':40s} {time.perf_counter() - t:6.2f} s")
    stages = gate.stages(False, cli.command_map(), base, cli.command_help(), cli.build)
    run = stages if "--tests" in sys.argv else stages[:-1]
    total = 0.0
    for stage in run:
        t = time.perf_counter()
        ran = stage()
        took = time.perf_counter() - t
        total += took
        print(f"{ran.name[:40]:40s} {took:6.2f} s  ok={ran.ok}")
    print(f"{'total of the stages run':40s} {total:6.2f} s")


if __name__ == "__main__":
    main()
