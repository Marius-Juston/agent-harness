"""Per-test outcomes across N JUnit reports of the same tree (different random seeds): flaky = mixed outcomes.
Also the duration tail. Usage: python rerun_outcomes.py run1.xml run2.xml ..."""

import collections
import statistics
import sys
from xml.etree import ElementTree

outcomes = collections.defaultdict(list)
times = collections.defaultdict(list)
for path in sys.argv[1:]:
    for c in ElementTree.parse(path).getroot().iter("testcase"):  # noqa: S314 (our own JUnit reports)
        tid = f"{c.get('classname')}::{c.get('name')}"
        kinds = {x.tag for x in c}
        o = "fail" if kinds & {"failure", "error"} else "skip" if "skipped" in kinds else "pass"
        outcomes[tid].append(o)
        times[tid].append(float(c.get("time") or 0))
runs = len(sys.argv) - 1
mixed = {t: o for t, o in outcomes.items() if len(set(o) - {"skip"}) > 1}
always_fail = [t for t, o in outcomes.items() if set(o) == {"fail"}]
skipped = [t for t, o in outcomes.items() if set(o) == {"skip"}]
missing = [t for t, o in outcomes.items() if len(o) != runs]
print(
    f"runs={runs} test ids={len(outcomes)} mixed(flaky)={len(mixed)} always-fail={len(always_fail)} "
    f"always-skip={len(skipped)} not-in-every-run={len(missing)}"
)
for t, o in sorted(mixed.items()):
    print("  FLAKY", t, o)
for t in always_fail:
    print("  FAIL ", t)
med = sorted(((statistics.median(v), t) for t, v in times.items()), reverse=True)
total = sum(m for m, _ in med)
top10 = sum(m for m, _ in med[:10])
print(
    f"summed median time {total:.1f}s; top-10 tests {top10:.1f}s = {100 * top10 / total:.1f}%; "
    f"top-1 {med[0][0]:.1f}s = {100 * med[0][0] / total:.1f}% ({med[0][1]})"
)
print("tests over 1s:", sum(1 for m, _ in med if m > 1), " over 5s:", sum(1 for m, _ in med if m > 5))
for m, t in med[:5]:
    print(f"  {m:6.2f}s {t}")
