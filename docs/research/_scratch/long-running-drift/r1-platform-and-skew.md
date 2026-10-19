# r1: platform drift and version skew (#282, round 1)

Search coverage: `uv run dev lit status` on 2026-10-19 showed openalex, crossref, europepmc and arxiv up and s2 DOWN
(HTTP 429). Claude Code docs were fetched live from code.claude.com on 2026-10-19 (WebFetch). Transcripts: every
`~/.claude/projects` directory of repo-E, H (this repository), L1 and repo-P on this machine, snapshot 2026-10-19
22:47 UTC, this researcher's own session excluded unless stated.

Code names: repo-E (the downstream project that pins H), H (agent-harness), L1, repo-P. Scripts are in
[scripts/](scripts/): [skew_scan.py](scripts/skew_scan.py), [skew_summary.py](scripts/skew_summary.py),
[skew_impact.py](scripts/skew_impact.py), [cc_versions.py](scripts/cc_versions.py). Their CSV output carries transcript
file ids and stays outside the repository.

## Answer in one paragraph
repo-E's agents ran harness-ops 0.1.4 for 162 h while its pin moved through v0.1.5 to v0.1.9, and nobody noticed. No
session in that window ran v0.1.9. A doctor warning that names exactly this case has existed since v0.1.1, but nothing
runs doctor after the first day, and it exits 0 with "all good". The cause is not #163, because repo-E's pin moved at
every release. The cause is in Claude Code's plugin model. There is one marketplace registry per user, keyed by name.
Re-adding a marketplace is a no-op. Third-party marketplaces have auto-update off by default. A changed `ref` in project
settings did not re-fetch here, and updates key on the manifest's `version` string. So no step of `copier update`,
`uv sync` or `dev setup` can move the installed plugin.

Two `/reload-plugins` calls on 2026-10-14 switched a repo-E session to a different, untagged build: H's main at a commit
after v0.1.7, still labelled "0.1.7". Its skills stayed at 0.1.4. A version-string comparison cannot see that case.
The cheapest exact check runs inside the plugin itself, from SessionStart and SubagentStart hooks. It compares the
loaded `${CLAUDE_PLUGIN_ROOT}` (version and a content digest) with the pin. Replayed on this history, it fires at 10 of
10 session starts in the window, the first 1.35 h after the pin moved.

Platform drift is fast: 18 Claude Code versions in 44 days. Its changelog changed at least 6 harness assumptions in 10
days, but none broke anything here. Version-dated re-check triggers plus a canary probe on upgrade fit this better than
pinning Claude Code.

## Findings: (a) version skew

**F1. Skew timeline in repo-E (VERIFIED, [skew_summary.py](scripts/skew_summary.py)).** The pin is the marketplace
`ref` in repo-E's `.claude/settings.json`, from `git log -L`. It moved 8 times, v0.1.1 to v0.1.9, between 2026-10-11
07:51 and 2026-10-15 07:10 UTC. The project-scope install record says harness-ops 0.1.4 (commit = the v0.1.4 tag), and
it has not changed since 2026-10-11 15:58 UTC.
- In sync for 37.2 h (pin v0.1.4).
- Then pinned ahead of the installed plugin for 162.4 h and counting: v0.1.5 1.5 h, v0.1.6 9.8 h, v0.1.7 39.5 h,
  v0.1.9 111.6 h.
- Up to 5 releases behind.
- Technical-lag literature measures the same thing ("to which extent a deployed collection of components is
  outdated"; Zerouali et al., JSEP 2019, [A formal framework for measuring technical lag in component repositories — and its application to npm](https://doi.org/10.1002/smr.2157), SOURCED abstract).

**F2. What each subagent actually ran (VERIFIED, [skew_scan.py](scripts/skew_scan.py)).** A harness-ops subagent's
system prompt is its agent body. Claude Code records it in `prompt_snapshot` attachments, and from 2.1.300 also in
api-request records ([changelog 2.1.300](https://code.claude.com/docs/en/changelog): "Changed local session
transcripts to also record each API request's system prompt"). The script matched every body against all distinct
bodies of that agent file on H's main and labelled each with the release tags that shipped it. All 491 harness-ops
subagents in repo-E's transcripts matched one body.
- 305 of 491 (62%) ran a body older than the pin at their start. By pin: v0.1.9 181 + 24, v0.1.7 56 + 14, v0.1.6 23,
  v0.1.5 2, v0.1.3 5.
- After the pin passed v0.1.4 there were 378 such subagents:
  - 301 ran bodies only 0.1.4 could have supplied;
  - 32 ran bodies from the "0.1.7" cache (F4);
  - 45 were reviewers, whose body has not changed since v0.1.0;
  - 0 ran v0.1.9.
- Hook evidence is the expanded plugin root in guard-hook block messages, `PreToolUse:Bash hook error:
  ["<cache>/harness-ops/<ver>"/scripts/...`. It agrees with the body version in every subagent that has both signals.
- This researcher, spawned 2026-10-19 on Claude Code 2.1.303, also runs the v0.1.2..v0.1.4 researcher body. So do 33
  of the 33 harness-ops subagents of the current lead session.

**F3. Shipped fixes that never reached repo-E's agents (VERIFIED, git on H).**
- `v0.1.4..v0.1.9` touches `plugins/harness-ops` in 17 files (+1,013/-80) through 26 merged PRs. That includes 2
  agents (architect, suite-auditor), 2 hook scripts (`completion_gate.py`, `uv_add_guard.py`) and 3 hook wirings
  (`SubagentStart`, `SubagentStop: builder$`, and `Agent|Workflow` for the quota gate).
- Exposure in the window ([skew_impact.py](scripts/skew_impact.py)):
  - 29 `uv add` calls after the v0.1.7 pin. 27 of them ran without the reuse-gate hook (the other 2 ran during the
    0.1.7 reload window, F4).
  - Corrected after the PR #303 audit (`skew_impact.py --reloaded`, window to 2026-10-19 23:13 UTC): 112 builder spawns
    after the v0.1.5 pin, 108 of them without the completion gate; the 4 others ran after a reload. The original
    "92 of 96" was the count after the v0.1.7 pin.
  - 0 Workflow calls, so the workflow quota-gate bypass had no exposure.
- The v0.1.9 headline fix, a machine-local quota cap, still worked through 0.1.4. The v0.1.9 devkit rewrites
  `usage.json` with the effective cap, and the 0.1.4 hook reads that file. Today's reading carries `cap: 50`, equal to
  the local override. Forward compatibility here came through the data file, not through the plugin.

**F4. `/reload-plugins` switched a session to a build the project never installed (VERIFIED by transcript; mechanism
BELIEVED).**
- On 2026-10-14 04:04 UTC, H's own project installed harness-ops "0.1.7".
- That cache equals H's main at an untagged commit after v0.1.7, whose `plugin.json` still said 0.1.7. A tree diff
  matches only that commit, not the v0.1.7 tag.
- Two repo-E sessions then ran `/reload-plugins` (05:50 and 15:31 UTC). Both gained architect and suite-auditor at
  once, and their later subagents ran the "0.1.7"-cache bodies and hooks. The session's skills stayed at 0.1.4, because
  they had been loaded before the reload.
- The next fresh repo-E sessions, on 2026-10-15, loaded 0.1.4 again.
- Docs say a session loads what `installed_plugins.json` records at startup or at the last `/reload-plugins`
  ([plugin loading](https://code.claude.com/docs/en/plugins/loading), SOURCED). repo-E's record says 0.1.4, so the
  switch is unexplained: possibly a bug, or a resolution rule that crosses project scopes. Claude Code 2.1.299 fixed a
  related reload bug: "/reload-plugins not loading a plugin installed from another terminal".
- Separately, the current lead process holds in-use markers on both the 0.1.4 and "0.1.7" cache directories.

**F5. Root cause (SOURCED + VERIFIED). #163 is not it.**
- repo-E moved its pin at every release (8 settings commits). #163, copier keeping the stored `harness_ref`, did not
  block it.
- The plugin layer has no step that follows the pin. All documented, from the
  [plugins loading reference](https://code.claude.com/docs/en/plugins/loading),
  [host-marketplace](https://code.claude.com/docs/en/plugins/host-marketplace) and the
  [CLI reference](https://code.claude.com/docs/en/plugins/cli-reference):
  - "There is one `known_marketplaces.json` per user, so a marketplace you add in one project is available in every
    project". "Claude Code identifies a marketplace by its `name`, so a user can't have two marketplaces with the same
    name registered at once". So repo-E (pinned v0.1.9) and H (tracking main) share one checkout. Here it sits on main
    at the untagged commit, last updated 2026-10-14 04:04 UTC, and its registry entry carries no ref.
  - `claude plugin marketplace add` on an existing name prints "already on disk" and exits 0. So `dev setup`'s
    `marketplace add <repo>@<tag>` ([setup.py](../../../../devkit/setup.py) `plugin_steps`) re-pins nothing, and
    `plugin install` reports "already installed" with exit 0.
  - "Background auto-update is off for your marketplace by default". Updates happen only through
    `/plugin marketplace update` or `claude plugin update`, and "a user gets a new copy of a plugin only when its
    computed version changes". The computed version is the manifest `version` first.
  - Docs say a declared marketplace "whose source changed in settings" is re-fetched. repo-E changed the declared
    `ref` four times after 2026-10-13 (v0.1.5, v0.1.6, v0.1.7, v0.1.9; corrected after the PR #303 audit), but the checkout's `lastUpdated` stayed at 2026-10-14 04:04 UTC. A ref change did
    not trigger a re-fetch here (VERIFIED observation; BELIEVED that ref is not compared).
- **Detection failed for 162 h (VERIFIED).**
  - `staleness()` in [setup.py](../../../../devkit/setup.py) has warned "plugin X installed V, pinned P -> claude plugin
    update" since v0.1.1 (commit 6a87e12). Today it prints `installed 0.1.4, pinned v0.1.9`, but as a WARN, with exit 0
    and "all good".
  - Nothing calls it routinely. In repo-E's transcripts, `uv run dev doctor` ran 15 times, all on 2026-10-11, and
    0 times after the pin moved. `dev setup`, which ends in doctor, also ran 0 times after.
  - The warning text appears in no transcript before this round.

**F6. "Agent type not found" is not a skew symptom here (VERIFIED, [skew_scan.py](scripts/skew_scan.py)).**
- repo-E: 9 failed Agent calls in 3 events, all on 2026-10-11, the day of the move from local agents to the plugin.
  - 4 × `builder`: the project's first agents directory was created after the session started. Docs: "The watcher
    covers only directories that existed when the session started", [sub-agents](https://code.claude.com/docs/en/sub-agents)
    (SOURCED).
  - 4 × `harness-ops:researcher`: the local agents were deleted before the plugin was installed.
  - 1 × `harness-ops:builder`: before the install, cleared by `/reload-plugins`.
- L1: 2 calls in 1 event (2026-09-25), 28 s before the agent files were committed.
- None after 2026-10-11 in any repository. Skew fails silently: old bodies and hooks run without error.

**F7. L1 and repo-P have no pin-versus-install skew by construction (VERIFIED).** Their agents and hooks live in the
repository. L1's one project plugin comes from a `directory` marketplace, which loads in place ("loads its current
source files at every session start, whatever its version string says", plugin loading docs, SOURCED). A
directory-sourced marketplace that repo-E registered once points at a deleted temporary directory: a dangling,
disabled registry entry.

## Findings: (b) platform drift

**F8. Claude Code release rate (VERIFIED, [cc_versions.py](scripts/cc_versions.py)).** The transcripts show 18
versions from 2.1.260 (2026-09-06) to 2.1.304 (2026-10-19), about 0.4 per day. That includes 9 versions in the 9 days
of repo-E's harness era (2.1.294 to 2.1.304). H's own plugin validation is pinned to `@anthropic-ai/claude-code@2.1.296`
([pyproject.toml](../../../../pyproject.toml)), 8 releases behind the runtime. That is a validator/runtime skew of its
own. "Auto-update ... applies them on the next launch, never mid-session"
([prompt caching](https://code.claude.com/docs/en/prompt-caching), SOURCED). So a long-lived lead session runs one
version while a fresh worker session can run the next one: one repo-E lead session spanned 2.1.296 to 2.1.299 across a resume.

**F9. Changelog items in 10 days that touch harness assumptions (SOURCED,
[changelog](https://code.claude.com/docs/en/changelog), 2.1.295 to 2.1.304; 2.1.297 and 2.1.298 absent from the page).
None was seen to break anything; each is a re-check trigger (BELIEVED).**
- 2.1.299: "Changed actions a plugin hook can block to be refused, not let through, when the hook cannot be handed them
  or cannot hand back its answer". This makes transport failures fail closed, while spawn_guard's header says it
  "never blocks a spawn".
- 2.1.300: "`updatedInput` from PreToolUse ... hooks is now validated like Claude's own input: unknown keys are
  dropped". read_guard depends on `updatedInput`.
- 2.1.296: Read gained `allow_large`. read_guard's escape list names only `limit` and `# whole-file`.
- 2.1.295: `onFailure: "block"` for command hooks. H uses it twice in `hooks.json`.
- 2.1.302: "at most 12 of one event run at a time" for prompt and agent hooks.
- 2.1.299: "agent `tools:` lists ... take only the named MCP server's tools".
- 2.1.300: system prompts recorded in transcripts. This changes the record shapes that any transcript forensics, such as
  this round's, must parse (VERIFIED: body matching needed `prompt_snapshot` before 2026-10-15 and api-request records
  after).

**F10. Silent overrides and stale doc facts (SOURCED + VERIFIED).**
- Cache TTL precedence. Docs give: environment variable, then the `subagentPromptCacheTtl` setting, then
  "the `cacheTtl` value in the subagent's `experimental` frontmatter"
  ([prompt caching](https://code.claude.com/docs/en/prompt-caching)). The template sets
  `subagentPromptCacheTtl: "1h"`, so the frontmatter `cacheTtl: 1h` on 3 of 7 agents is dead config. It is harmless
  while both say 1h, but a per-agent `5m` would silently not apply (already noted in harness-efficiency.md).
- Import depth. [Memory docs](https://code.claude.com/docs/en/memory) now say "a maximum depth of four hops", and
  `IMPORT_HOPS = 5` is still in `devkit/checks.py` (#47). It was stale when written (decision-records-and-drift.md §5).
- HTML comments. "Block-level HTML comments ... in CLAUDE.md files are stripped before the content is injected"
  (memory docs). Exposure today is 0: no CLAUDE.md, HARNESS.md, skill or agent file in H, repo-E, L1 or repo-P
  contains `<!--` (VERIFIED, grep). Any future marker scheme must not rely on agents seeing comments.
- Read line truncation: not verified. Transcript tool descriptions were not inspectable before 2.1.300, so it is
  dropped.

**F11. Transcript retention bounds every longitudinal study (SOURCED + VERIFIED).** "Claude Code clients store
session transcripts locally ... for 30 days by default ... Adjust the period with `cleanupPeriodDays`"
([data usage](https://code.claude.com/docs/en/data-usage)). The earliest record on this machine is 2026-09-06, and L1's
transcripts start 2026-09-25, although L1 is about 6 months old. Platform and skew evidence older than about 4 to 6
weeks is gone unless `cleanupPeriodDays` is raised. Neither user nor project settings set it.

**F12. Model drift within the window (VERIFIED, [cc_versions.py](scripts/cc_versions.py); one cause BELIEVED).**
- Turns by model:
  - claude-opus-5: 2026-09-06 to 09-11;
  - claude-opus-5-5: 09-25 on;
  - claude-sonnet-5, then claude-sonnet-5-5 from 10-03;
  - claude-haiku-4-5, then claude-haiku-5-5 from 10-09.
- Agent frontmatter says `model: opus` (an alias), so the model behind an agent changed with no harness edit.
- One repo-E researcher, on 2026-10-14, switched mid-run from claude-opus-5-5 to claude-opus-4-8 for 94 of its 145
  turns. Its hand-back does not say so. This is consistent with documented automatic model fallback ("the session
  continues there", [prompt caching](https://code.claude.com/docs/en/prompt-caching)), but the trigger was not found
  in the record.
- Behaviour of "the same" hosted model can change within months (GPT-4 prime identification 84% to 51%, March to June
  2023; Chen, Zaharia and Zou 2023, [How is ChatGPT's behavior changing over time?](https://arxiv.org/abs/2307.09009), SOURCED abstract; 2023 models, not
  comparable to today's).

## Mechanisms evaluated

| Mechanism | Catches | Replay / cost | Verdict |
|---|---|---|---|
| **Loaded-root check in the plugin's own hooks**: SessionStart (`session_brief.sh`) and SubagentStart (`spawn_guard.sh`) compare `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json` `version` and a content digest of the root (excluding `.in_use`, `.orphaned_at`, `__pycache__`) with the pin in `[tool.devkit.plugins]` and a digest the pinned devkit release ships | startup skew (F1), reload switches to another build (F4), a "0.1.7" that is not v0.1.7 | 10 of 10 repo-E session starts in the window would print it, the first 1.35 h after the pin moved (VERIFIED from F1's session list); reload switches show at the next spawn; no network, no `claude` subprocess | **ship (warning banner; gate-level wording)**; BELIEVED design |
| `dev doctor`: installed < pin becomes FIX (exit 1) and adds the digest comparison | the same, on demand | `claude plugin list --json` takes 0.84 s and has no commit field (VERIFIED); doctor ran 0 times in the window | ship, but it cannot be the only detector |
| `dev setup` / after `copier update`: `claude plugin marketplace update <name>` then `claude plugin update <plugin> --scope project` | moves the install | installs the marketplace HEAD's version, which is main and not the tag, unless the marketplace was added with `#<tag>`, and one name per user cannot hold two refs (F5) | ship as the fix command; the digest check stays |
| Plugin shipped inside the pinned wheel and loaded with `--plugin-dir` / `CLAUDE_CODE_PLUGIN_DIRS` (inline plugins outrank marketplace installs: "replaced silently", loading docs) | makes skew impossible: devkit and plugin come from one `uv` pin, and two projects on one machine can hold different versions | unknown whether `CLAUDE_CODE_PLUGIN_DIRS` in settings `env` applies before plugin load | **probe first** (gap G1) |
| Pin Claude Code (`minimumVersion`, `autoUpdatesChannel`, `DISABLE_AUTOUPDATER`; names from the settings reference index) | platform changes | blocks about 0.4 fixes per day, some of which the harness needs (2.1.299 reload fix, 2.1.300 transcripts) | **no**; record the version instead |
| Canary on upgrade: orient records the last Claude Code version per machine; on change, run a short headless probe set (hooks fire for Agent, Workflow, SubagentStart and SubagentStop; `updatedInput` applied; agent listing contains every plugin agent; loaded root == pin) | F9-class semantic changes | at most one probe run per day at the observed release rate; extends #102 | ship after the loaded-root check |
| Version-dated assumptions: comments of the form "Claude Code 2.1.296: ..." (3 in H) plus, on a version change, list the changelog bullets that match harness keywords (hook, plugin, subagent, frontmatter, Read, transcript) | F9 and F10 | 6 or more relevant bullets in 10 days; a keyword list is a warning, not a gate | warning at orient |
| Quote-presence sweep (#235) | doc text changes (IMPORT_HOPS) | 0 doc-citing comments carry a quote today; the IMPORT_HOPS case is live and would fire on the memory page's "four hops" | ship as specified |
| Raise `cleanupPeriodDays` on harness machines | keeps the drift evidence that F1-F12 need | disk only | recommend 180 (ASSUMED value) |
| Model and date in hand-backs and notes | F12 | cheap; the transcript knows the model | already a memory rule; make `dev pr` or hand-back record the models a subagent used |

## Gaps seen, not closed
- **G1.** Does `CLAUDE_CODE_PLUGIN_DIRS` (or `--plugin-dir`) set from project settings `env` load a plugin from the
  uv-installed package before startup, and does it shadow the marketplace install's hooks cleanly? A probe on the
  current Claude Code decides whether skew can be removed rather than detected.
- **G2.** Why `/reload-plugins` in repo-E loaded the "0.1.7" cache although repo-E's install record says 0.1.4 (F4).
  Reproduce on a scratch pair of projects, then report it upstream if it reproduces.
- **G3.** Does a changed `ref` in a project's `extraKnownMarketplaces` ever re-fetch, and with which ref, when another
  project declared the same marketplace name without one (F5)?
- **G4.** The 45 reviewers (F2) and all main sessions' hook versions are known only where a guard blocked something;
  hook stdout carries no version. A loaded-root line in `session_brief` output would make this exact in future.
- **G5.** L1 and repo-P were scanned only for the agent-not-found errors. Their in-repo agents cannot skew against a
  pin, but mid-session edits of in-repo agents and hooks (the watcher semantics in F6) were not measured.
- Not done in this round (round mode): no issues filed. Candidates for the lead: the loaded-root check (design above), G1 probe, G2 repro, doctor FIX-level staleness, `cleanupPeriodDays`.

## Sources fetched
- Claude Code docs, fetched 2026-10-19:
  - [plugin loading reference](https://code.claude.com/docs/en/plugins/loading)
  - [host a marketplace](https://code.claude.com/docs/en/plugins/host-marketplace)
  - [create a marketplace](https://code.claude.com/docs/en/plugin-marketplaces)
  - [plugin commands reference](https://code.claude.com/docs/en/plugins/cli-reference)
  - [prompt caching](https://code.claude.com/docs/en/prompt-caching)
  - [sub-agents](https://code.claude.com/docs/en/sub-agents)
  - [memory](https://code.claude.com/docs/en/memory)
  - [data usage](https://code.claude.com/docs/en/data-usage)
  - [settings](https://code.claude.com/docs/en/settings)
  - [settings reference](https://code.claude.com/docs/en/settings-reference) (index rows only)
  - [changelog](https://code.claude.com/docs/en/changelog) (2.1.295 to 2.1.304)
- Zerouali, Mens, Gonzalez-Barahona, Decan, Constantinou, Robles. A formal framework for measuring technical lag in
  component repositories, and its application to npm. J. Softw. Evol. Process 2019.
  [doi:10.1002/smr.2157](https://doi.org/10.1002/smr.2157) (abstract via `uv run dev lit get`)
- Chinthanet, Kula, McIntosh, Ishio, Ihara, Matsumoto. Lags in the release, adoption, and propagation of npm
  vulnerability fixes. EMSE 2021. [doi:10.1007/s10664-021-09951-x](https://doi.org/10.1007/s10664-021-09951-x)
  (abstract). It reports that fixing releases bundle up to 85.72% unrelated commits and that stale clients need extra
  migration effort, the analogue of a plugin 5 releases behind.
- Chen, Zaharia, Zou. How is ChatGPT's behavior changing over time? arXiv 2023.
  [arXiv:2307.09009](https://arxiv.org/abs/2307.09009) (abstract)
- Local evidence: H git history (tags v0.1.0 to v0.1.9; `plugins/harness-ops`), repo-E's settings history, the plugin
  cache and registry files on this machine, and transcripts via the four scripts above.
