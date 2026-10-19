# Question (#282)

In agent-run projects that live for months, what drifts, how fast, how late is it noticed, and which cheapest
mechanism keeps each class true? Extends decision-records-and-drift.md (#220), whose data spans 2-3 days.

Classes: (1) agent memory (auto-memory entries) no longer true, or contradicting repo rules or later owner decisions;
(2) prose describing behaviour the code no longer has (specs, template docs, agent bodies, skills, docstrings,
research notes); (3) upstream platform drift (Claude Code settings, frontmatter, docs; model changes);
(4) version skew between the template pin and the installed plugin.

Data: private longitudinal repositories, code-named in the note: L1 (6 months, ~2k commits, 20 CLAUDE.md revisions,
12 memory entries), L2 (9 months, ~540 commits, 14 revisions), L3 (4 months, ~270 commits, 64 revisions), plus
repo-E and this repository; Claude Code transcripts on this machine; outside long-lived repositories and literature.
This repository is public: no private names, paths, or content; counts and generic classes only.

Decision served: which drift mechanisms agent-harness ships (gate / generated / sweep / warning / none) and the
build order of #163, #226-#238 and new issues.
