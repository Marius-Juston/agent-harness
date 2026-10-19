"""Slowest single-test durations in gate history ledgers (devkit/gate.py `record_history` format), for #43's
per-test timeout. Usage: uv run python slowest_tests.py <git-common-dir>/devkit/test-history.jsonl ..."""

import json
import sys

for path in sys.argv[1:]:
    durations, runs = [], 0
    with open(path) as f:
        for line in f:
            runs += 1
            for test_id, (outcome, seconds) in json.loads(line)["tests"].items():
                durations.append((seconds, outcome, test_id))
    durations.sort(reverse=True)
    print(f"{runs} runs, {len(durations)} results; slowest 5:")
    for seconds, outcome, test_id in durations[:5]:
        print(f"  {seconds:8.1f} s  {outcome:7}  {test_id[:100]}")
