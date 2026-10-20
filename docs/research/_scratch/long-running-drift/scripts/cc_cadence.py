"""Claude Code platform churn, 2.1.116 (week of 2026-04-19) to the newest version in CHANGELOG.md (fetched 2026-10-19).
Counts versions and bullets whose wording signals a behaviour/config change an agent harness could depend on."""

import re
import sys

t = open(sys.argv[1]).read()  # noqa: SIM115
secs = re.split(r"^## ", t, flags=re.M)[1:]
vers = [s.split("\n", 1)[0].strip() for s in secs]
cut = vers.index("2.1.116")
win = secs[:cut]
kw = re.compile(r"^- (\[\w+\] )?(Removed|Changed|Deprecated|Renamed|Breaking)\b")
cfg = re.compile(
    r"setting|frontmatter|hook|CLAUDE\.md|AGENTS\.md|memory|skill|subagent|agent|permission|plugin|env|\.claude/", re.I
)
bul = [l for s in win for l in s.splitlines() if l.startswith("- ")]  # noqa: E741
hits = [l for l in bul if kw.search(l)]  # noqa: E741
both = [l for l in hits if cfg.search(l)]  # noqa: E741
print(
    f"versions in window: {len(win)} ({vers[cut - 1]}..{vers[0]}); bullets {len(bul)}; change-wording {len(hits)}; "
    f"of which touch harness surfaces {len(both)} ({len(both) / len(win):.2f} per version)"
)
for l in both[:12]:  # noqa: E741
    print("  ", l[:160])
