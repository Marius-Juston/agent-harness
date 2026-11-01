# Changelog

Notable changes of each release (a `vX.Y.Z` tag), newest first. A project moves to a release with
`uvx copier update`; read the entries between its pinned tag and the new one first. v0.1.0 to v0.1.4 predate this
file: see [their GitHub releases](https://github.com/Marius-Juston/agent-harness/releases).

## v0.1.17 (2026-11-01)

Several gates can run on one machine at once (`gate_slots`), integrate can land several ready PRs behind one gate
(`dev integrate --train`), and a gate run in one worktree can no longer report another worktree's results or reuse
a pass recorded for a different test selection. `devkit/workflow.py` is split into focused modules, ending the
import cycle around `devkit.checks`.

### Upgrading a project
Run `uvx copier update`, then `uv sync && uv run dev setup && uv run dev docs && uv run dev check`. These can need
attention:
- **New opt-in `[tool.devkit] gate_slots`** (default 1, so nothing changes until a project sets it): the number of
  gates that may run at once on one machine, each holding one of the lock files `gate.lock`, `gate.lock.1`, ... in
  `<git-common-dir>/devkit/`. Integrate's gate still takes the next free slot ahead of queued worker gates, and the
  test workers are divided among the slots. A gate also honours slot files left by a larger setting, so **lowering
  `gate_slots` takes effect only once the extra `gate.lock.<n>` files are deleted while no gate runs** (`dev status`
  lists every holder). A value below 1, or a bool, string or float, is a config error. (#461)
- **The gate's first line names the worktree, the interpreter and the devkit directory it runs**, and a
  `gate: WARNING: ...` line follows when devkit or python comes from another worktree of the same repository (a
  sibling's venv or editable install). Fix that setup rather than ignoring the warning: such a gate runs the
  sibling's code. A venv outside the project or an editable harness checkout elsewhere is only named. (#463)
- **Cached passes now re-check the collected test selection.** A pass record stores a fingerprint of the node ids
  `pytest --collect-only` lists, and a cache hit is reused only when it matches, so a pass scoped on a feature
  branch is not reused on main at the same tree. Records written before this release carry no fingerprint and are
  never reused: every tree re-gates once after the upgrade, and each cache check costs one collect-only run. (#462)
- **`dev integrate --train N <pr>...`** is new: it gates up to N ready PRs merged together once, lands each as its
  own squash commit with one push of main, and on failure bisects to the first breaking member and sends back only
  that one. A protected main that refuses a direct push stops the train with nothing landed (#472). (#464)
- **`--harness-ok` now takes PR numbers.** In a train it clears only the members named and refuses a number that is
  not a member (and duplicate members); on a single integrate, a bare `--harness-ok` still clears that PR, and a
  number other than that PR is refused. Scripts that pass it with a value must pass PR numbers. (#464)
- **devkit internals moved out of `devkit.workflow`.** A project or script importing devkit internals from
  `devkit.workflow` should import them from the new modules: `devkit.quota` (quota cap and usage polling),
  `devkit.branches` (worktree listing, branch pruning), `devkit.prtext` (Reuse:, title and session-leak rules),
  `devkit.ci_status` (PR check polling, `--local-ci`), `devkit.merging` (merge with main, conflicts, stacked
  retargets, `regenerate`) and `devkit.fixloop` (fix loop, send-backs, scrubbing, outcome records, `--why`).
  `devkit.workflow` keeps re-exporting what the plugin scripts import (`_gate_passed`, `REUSE_FORMS`,
  `reuse_problem`) and what `devkit.train` reads through it, so a plugin older or newer than the project's devkit
  keeps working; anything else, `SESSION_URL` for one, is no longer there. Tests that monkeypatch `workflow.git` or
  `workflow.state_dir` to steer moved code must patch the new module too. (#442, #443, #444, #445, #446, #448)
- **`dev agent-stats` scorecard counts as `--cost`/`--reviews`/`--misses` do:** it drops runs with no real
  assistant message and skips `<synthetic>` messages, so the main-session row can show fewer runs and higher
  medians than before on the same transcripts. Compare scorecards across the upgrade with that in mind. (#450)

### Added
- `[tool.devkit] gate_slots`: N concurrent gate slots per machine, merges still first; `dev status` lists every
  slot holder. (#461)
- `dev integrate --train N <pr>...`: a merge train that gates several ready PRs together and bisects on failure; the
  multi-agent spec gains it as step 9. (#464)
- The gate's first line names its worktree, interpreter and devkit, with a warning for one from another worktree.
  (#463)
- `devkit/quota.py`, `devkit/branches.py`, `devkit/prtext.py`, `devkit/ci_status.py`, `devkit/merging.py` and
  `devkit/fixloop.py`, holding code moved verbatim out of `devkit/workflow.py` (1,279 -> 934 lines in the last
  step). (#442, #443, #444, #445, #446, #448)
- `devkit/diffparse.py`, a leaf module holding the diff parser moved from `devkit/weaken.py`. (#449)

### Changed
- `--harness-ok` takes PR numbers; a bare `--harness-ok` clears a single integrate only. (#464)
- A pass record stores the collected test selection's fingerprint, and a cache hit needs it to match. (#462)
- `devkit.checks` no longer imports `devkit.cli` or `devkit.weaken`: `gate.check` takes a parser factory, and the
  largest import cycle goes from 7 modules to none. (#449)
- `dev agent-stats` has one transcript reader for the scorecard and `--cost`/`--reviews`/`--misses`; the scorecard
  skips synthetic turns and runs without a real one. (#450)
- This repository's consolidation trigger excludes research scratch, eval fixtures, `tests/fixtures` and `template`
  (its own `[tool.devkit.consolidation]`), and decision D24 freezes `scripts/suite_metrics.py` as evidence. (#451)

### Fixed
- A queued `dev check` could report another worktree's test output as its own: two gates failing in the same second
  shared one log file, and concurrent writers of `test-history.jsonl` crashed or lost runs. Logs now have unique
  names and the history is written under a lock. (#463)
- A pass scoped on a feature branch was reused by `dev check` on main at the same tree. (#462)
- A gate passing through the wait queue to a free slot printed the waiting notice. (#461)
- A test of integrate wrote `pr-7.outcome.json` into the clone's real shared state dir on every run. (#448)

## v0.1.16 (2026-11-01)

Integrate needs the lead less: it regenerates declared hash lines and copied action pins when a PR's head is merged
with a moved main, refuses a PR whose body no longer links its branch's issue or that lacks a required review, and
`dev handoff` and `dev status --resume` rebuild the state a lead needs after a restart from the board and the
worktrees.

### Upgrading a project
Run `uvx copier update`, then `uv sync && uv run dev setup && uv run dev docs && uv run dev check`. These can need
attention:
- **PR bodies must link the branch's issue.** `uv run dev integrate` refuses a PR on an `<n>-slug` branch whose body
  has neither `Closes #n` (or another closing keyword) nor `Refs #n`, before the gate and whatever `--override`
  says, and sends it back through the fix loop. `dev pr` writes the line, so only PRs opened or edited by hand need
  it (`gh pr edit <n> --body ...`). Branches without an `<n>-` prefix (Dependabot, release-please) are never
  refused. (#434)
- **New opt-in `[tool.devkit]` keys**, all off by default, so nothing changes until a project sets them:
  - `regenerate`: a list of argv lists integrate runs after `dev docs` in the merged tree, so a PR whose only
    conflict with main is a generated line (a hash ledger, say) merges without a send-back. Mark each such file
    `merge=devkit-generated` in `.gitattributes`; a failing command stops integrate before the gate. The template's
    `pyproject.toml` has a commented example. (#386)
  - `required_reviews`: changed-path glob or prefix -> reviewer agent name. Integrate refuses a PR touching such a
    path until a trusted verdict review naming that reviewer says `merge` on the head or an ancestor of it;
    `--override` does not lift it. (#391)
  - `action_pins`: source workflow -> globs of its copies. `dev docs` moves a copy's action commit pins to the
    source's, and the gate's generated-docs stage fails when a copy lags. (#431)
- **Raised ratchet floors apply to this repository only** (`min_tests` 2246, `min_coverage` 91 in its own
  `.devkit/ratchets.toml`); a project's floors are its own. (#439)

### Added
- `[tool.devkit] regenerate`: integrate runs the declared commands after `dev docs` in the merged tree and commits
  what they change. (#386)
- `[tool.devkit] required_reviews` and integrate's required-review refusal. (#391)
- `[tool.devkit] action_pins` and pin syncing in `dev docs`; this repository syncs the template's workflows to
  `ci.yml`, and its CI runs `dev docs` before the gate on Dependabot PRs. (#431)
- `uv run dev handoff` rebuilds the handoff file's In flight, Just merged and Next sections from the board and the
  default branch's log, keeping every hand-written section byte for byte; integrate refreshes it after a merge unless
  the checkout is on the default branch. A missing handoff file is a no-op. (#394)
- `uv run dev status --resume` lists every agent worktree with commits ahead of main or uncommitted edits, with its
  issue, branch, open PR, last commit, first unresolved review thread and a one-line resume prompt (`--json`
  too). (#388)

### Changed
- `dev integrate` refuses a PR body without `Closes`/`Refs` for its branch's issue, and checks the body again after
  the gate. (#434)
- The lead skill resumes from `dev status --resume` (section 5) and names `dev handoff` (section 6). (#388, #394)
- This repository's ratchet floors: `min_tests` 1021 -> 2246, `min_coverage` 81 -> 91. (#439)

### Fixed
- A PR whose `<n>-slug` branch claims an issue could merge after its body lost the `Closes` line, leaving the issue
  open. (#434)
- Dependabot bumps of `ci.yml` left the template's `gate.yml` and `board.yml` on an older `astral-sh/setup-uv` pin;
  both now match `ci.yml` (10.3.3). (#431)
- The copier-update conflict test failed on every branch past v0.1.15, which no longer stores `harness_ref`. (#388)

## v0.1.15 (2026-11-01)

The lead relays less: `copier update` moves the pins on its own, integrate sends a failing PR straight back to its
worker, PR titles follow Conventional Commits, and durable rules get a decision table the gate watches. Four
hand-rolled matchers were replaced or hardened from the #416 reuse survey
([devkit-reuse-survey.md](docs/research/devkit-reuse-survey.md)).

### Upgrading a project
Run `uvx copier update` (a plain update now moves the devkit and plugin pins: `--data harness_ref=<tag>` is no
longer needed, and this update moves them even when `.copier-answers.yml` still holds an older stored answer), then
`uv sync && uv run dev setup && uv run dev docs && uv run dev check`. `uv run dev doctor` warns when a project's
pins lag its template (an update made with a plain `copier update` before this fix) and names the
`--data harness_ref=<tag>` command that repairs it. (#163) These can need attention:
- **Run `uv run dev docs` after the update.** It writes the new `docs/generated/decisions.md`, which the gate's
  generated-docs stage needs and the template's HARNESS.md links. `.devkit/decisions.toml` is project-owned: copier
  creates it once and never updates it, and the gate's new "decision table" stage warns when an active row's
  `enforced-by` literal leaves its path. (#226)
- **Retitle open PRs.** `uv run dev integrate` refuses a PR whose title is not Conventional Commits
  (`type(scope): outcome`, lowercase type, ` (#n)` optional), before the gate and whatever `--override` says. `dev pr`
  writes such titles from the issue, so only PRs opened by hand or before the update need it
  (`gh pr edit <n> --title ...`). The template's `dependabot.yml` gives Dependabot PRs `build(deps):` / `ci(deps):`
  titles. A project whose main does not use Conventional Commits opts out with `conventional_titles = false` under
  `[tool.devkit]`. (#100)
- **Raised dependency floors.** The devkit now requires `pytest>=9.2.0`, `hypothesis>=6.170.13`, `ruff>=0.17.3`,
  `zizmor>=1.31.1` and `diff-cover>=11.0.0` (and the template's dev group `copier>=9.19.1`). A project that pins
  any of them lower must raise its pin, or `uv sync` cannot resolve. pytest 9.2 makes `-c` with a missing path a
  usage error and the `indirect`, `ids` and `scope` arguments of `parametrize` keyword-only. (#426)
- **`pathspec` is a new runtime dependency** of the devkit (`pathspec>=1.1`, MPL-2.0, used unmodified), for rule
  `paths:` matching. (#421)
- **The subagent cap moved to `settings.local.json` (under `.claude/`).** The checked-in `settings.json` no longer
  pins `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` (12), because a checked-in `env` beats the owner's shell. The template
  renders the git-ignored `settings.local.json` once with a default of 24 and `copier update` never touches it again;
  change the cap there, per machine. An owner's own value in either file is kept. (#393, #401)
- **New gate stages.** "package installs in code and scripts" fails an added line of code or script that runs a
  package install (pip, apt, npm, conda, `curl | sh` and others), also through a subprocess or a `" ".join([...])`;
  list a file that legitimately does so in `.devkit/install-allow.txt` with its reason (`[tool.devkit]
  install_allowlist`). (#392, #423) "rules provenance and paths" checks rules-file bullets for provenance and real
  paths. (#78) Backticked `dev` command spans in docs with a bad flag or a leftover positional are warnings for now.
  (#229)

### Added
- `.devkit/decisions.toml`, a `decision table` gate stage and the generated `docs/generated/decisions.md`: durable
  prose-only rules with `enforced-by` markers the gate checks. (#226)
- `uv run dev gotcha <path> <lesson>` and `/harness-ops:gotcha`: a one-line lesson goes into the path-scoped Claude
  Code rules file with its issue and date, checked by the gate's rules stage. (#78)
- Integrate closes the fix loop: a conflict, failed gate or fixable refusal posts one PR comment with the reasons,
  the last 40 failing lines and the open feedback, moves the item back to in-progress with its claim kept, and names
  the worker to resume; `board brief` shows it as "Fix before merge". The 3rd failed cycle blocks it `needs owner`.
  Tokens and session links are cut from the comment. (#110)
- `dev pr` writes a Conventional Commits title from the issue, `dev pr-title` checks one, and integrate refuses a
  non-conforming title. (#100)
- The gate's package-install stage and `.devkit/install-allow.txt`; builder, tester and researcher must never wrap a
  denied or guarded command in a script, subprocess or alias. (#392)
- `dev doctor` checks the binaries configured MCP servers need (e.g. ripgrep for lean-lsp-mcp). (#390)
- The research note on libraries to replace hand-rolled matchers and parsers in devkit. (#416)

### Changed
- `harness_ref` is never asked: it is always the template's own release, and an update drops the stored
  answer. (#163)
- Rule `paths:` globs are matched as Claude Code matches them (node-ignore semantics, brace expansion, comma
  splitting) with pathspec's `GitIgnoreSpec`; patterns that do not compile are dropped with a warning. (#421)
- The install check matches whole Python statements (stdlib `ast` with folded string constants) and joined shell
  continuations, not single lines. (#423)
- `scrubbed()` also redacts gitleaks' vendor-prefix credentials (Slack, GCP, PyPI, npm, GitLab, Hugging Face,
  Stripe, private keys, JWTs, URL passwords); `Bearer` needs 20 or more characters, so "Bearer of news" stays. (#422)
- `check_dev_commands` parses each `uv run dev` span with devkit's own parser, so its options cannot drift from
  the CLI. (#229)
- The research skill defaults to 3 researchers and 2 rounds (5 and 3 under 'wide'), with one extra round only when
  the last changed the recommendation; researchers quote and locate each claim and tag single-source ones. (#84)
- A gate waiting for the machine lock behind a worker's check no longer uses up integrate's deadline: the deadline
  starts once the lock is held, the wait is bounded by `gate_lock_wait_s` (7200), and integrate's gate goes
  first. (#385)
- `dev pr` with several `Closes #N` moves every closed issue claimed on the branch to status:review. (#384)
- One `dev integrate --local-ci` merge was verified end to end with Actions not running. (#108)
- Dependency bumps: filelock 4.2.1, the python group (#426), `astral-sh/setup-uv` 10.3.3 and
  `actions/upload-artifact` 7.0.2 (also in the template's `gate.yml`). (#383, #424, #425)

### Fixed
- `copier update` reused the `harness_ref` answer stored in `.copier-answers.yml` over the new release's default, so
  template files moved while the dev-group, uv-source and plugin pins stayed on the old tag. (#163)
- `board new` printed ` (ready)` and exited 0 when label creation or `gh issue create` failed or returned no issue
  URL; it now exits non-zero with the cause. (#387)
- The template pinned the subagent cap in the checked-in `settings.json`, overriding the owner's shell. (#393)
- The gate lock-wait integrate test raced a timer under `dev test --affected` load; it now synchronises on
  events. (#407)
- The copier-update subagent-cap test assumed the previous release pinned the cap, and would fail once a release
  without the pin shipped. (#402)

## v0.1.14 (2026-10-21)

The harness runs in the environment the project declares, not the one the machine happens to have: plugin hooks work
on an older system `python3`, a sourced ROS (or any shell `PYTHONPATH`) no longer breaks the gate's tests, and the
owner's git config no longer changes the git output devkit parses.

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.14`, then `uv sync && uv run dev setup && uv run dev check`. These can
need attention:
- **The gate, `dev test` and `dev suite-audit` ignore `PYTHONPATH`, `PYTHONHOME`, `PYTHONOPTIMIZE`,
  `PYTHONWARNINGS`, `PYTEST_PLUGINS` and `PYTEST_ADDOPTS`** from the shell, as CI never sets them, and print which
  ones they dropped. If the tests relied on one of them, move it into pytest's `pythonpath` or `filterwarnings`
  option in `pyproject.toml`. `dev doctor` warns while one is set, because ad-hoc `uv run python` still sees it.
  docs/SETUP.md has a new *Host Python environment* section. (#372, #376)
- **Every `dev` command pins git's default output** for the git it starts, through `GIT_CONFIG_COUNT` entries
  (`color.*=never`, the `a/` `b/` diff prefixes, non-relative and quoted paths, no signatures in `git log`), and
  `git diff`/`log`/`show` run with `--no-ext-diff`. The pins reach every subprocess of `dev`, the project's test suite
  under `dev test` included. (#374)
- **Plugin hooks need `python3` 3.10 or later** on the machine. `dev doctor` checks it. (#370)
- **Test-only optional extras belong in the dev group.** `uv sync` and CI's `uv sync --locked` install no extras and
  remove any installed by hand. This is a project-side fix; docs/SETUP.md says how.

### Fixed
- `uv_add_guard.py` crashed on every Bash call under Python 3.10 (a module-level `import tomllib`), so the `uv add`
  reuse gates never ran. Without `tomllib` it now reads `pyproject.toml` with the project venv's Python. A test runs
  every plugin hook under Python 3.10, and ruff lints `plugins/**/*.py` at `py310`. (#370)
- A sourced ROS Humble put its Python 3.10 `launch_testing` pytest plugin into the venv's pytest, and the test stage
  died on `No module named 'lark'` before running a test. (#372)
- With `color.ui=always` (or a `color.grep`/`color.diff` setting, `diff.noprefix`, `diff.relative` or
  `diff.external`) in the git config, `dev test --affected` and the `dev stats` diff parsing read colour codes or
  wrong paths. (#374)

### Added
- An animated terminal demo of the full loop in the README, and the step after `claude` in the quickstart. (#368)

## v0.1.13 (2026-10-20)

Less issue and PR bookkeeping for the lead, from the #346 research ([issue-pr-automation.md](docs/research/issue-pr-automation.md)):
conflicts are caught at `dev pr` and right after each merge, reviewers post their own review, `board new` names
related issues, and `integrate --why` replaces diagnostic reruns.

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.13`, then `uv sync && uv run dev docs && uv run dev check`. These can
need attention:
- **`dev pr` needs git 2.40 or later** on every machine that hands back work: it now runs `git merge-tree
  --write-tree` against `origin/main` and refuses, before pushing, a branch that conflicts (or one it cannot check).
  Ubuntu 22.04 ships 2.34. (#349; a doctor check is #356)
- **Reviewers post, the lead points.** `harness-ops:reviewer` and `harness-ops:research-auditor` post one COMMENT review
  whose first line is `Verdict: merge|send back|reconsider, round <N>, <sha>` and hand back at most 5 lines; the
  research auditor's verdict words changed from accept/return to merge/send back. With no PR yet, the auditor posts
  on the issue. A project's own reviewer agents (its `.claude` agents directory) still hand back findings: give them the same
  posting step, or the lead relays text as before. (#347)
- **Only the `gh` user's verdict review shows in full** in `board feedback` and `board brief`; anyone else's is cut to
  200 characters as before. (#347)
- `board new` fetches every issue once more (about 3 s) to print related issues. (#350)

### Added
- `dev pr` refuses a branch that conflicts with main, with the conflicted paths; after each merge `integrate` sends
  back every other open PR the new main conflicts with (`sent_back` in RESULT-JSON). (#349)
- `board new` prints up to 3 related issues (stdlib BM25 over title, Context and DONE WHEN) and pre-fills Refs; it
  never refuses. The lead skill closes duplicates with `gh issue close <n> --duplicate-of <m>`. (#350)
- `dev integrate <pr> --why` prints the PR's last recorded outcome, its reasons and failing gate lines, and whether
  the head moved since, without running anything or writing to GitHub. The gate's output is also kept in a log under
  the integrate state directory. (#348)
- Reviewer and research-auditor verdicts as one GitHub review; `board feedback`/`board brief` show it first, marked
  stale when its SHA is not the PR head. (#347)
- Research note [issue-pr-automation.md](docs/research/issue-pr-automation.md). (#346)

## v0.1.12 (2026-10-20)

`dev integrate` deletes a merged PR's local branch, a new `dev worktree prune` cleans up the rest, and two board and
doctor fixes.

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.12`, then `uv sync && uv run dev docs && uv run dev check`. These can
need attention:
- **Merged branches are deleted.** After a merge, `integrate` deletes the PR's local branch when its tip is the merged
  head or an ancestor of it and no worktree has it checked out (rebases and bisects count); otherwise it keeps the
  branch and says why. Branches left from earlier merges: `uv run dev worktree prune --dry-run`, then without
  `--dry-run`. guard_bash's `git branch -D` refusal now points there. (#340)

### Added
- `dev worktree prune [--dry-run]`: deletes local branches whose merged PR (or a merged PR that replaced a closed one)
  contains every commit on them; skips main, branches with no PR or an open PR, and refuses when the PR list may be
  cut off. (#340)

### Fixed
- Issue keywords (`Closes`, `Refs`) inside inline code spans and fenced code blocks no longer link, close or move
  issues, in `board lint` and in integrate's board update. (#339)
- `dev doctor`'s "plugin installed" check counts only an install for this project (or user scope), not one for any
  other project on the machine. (#3)

## v0.1.11 (2026-10-20)

`dev integrate` waits for checks still running instead of refusing, a stacked-PR route auto mode allows, and setup
and doc-drift fixes (#321 follow-ups).

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.11`, then `uv sync && uv run dev docs && uv run dev check`. These can
need attention:
- **integrate waits.** A lead no longer waits for green checks by hand: `integrate` waits up to `checks_wait_s` and
  ends in `waiting` on timeout; a second `waiting` on the same head carries `"again": true` and points to
  `--local-ci` or the owner. (#322, #305)
- **Stacked PRs.** A PR retargeted to main after its parent merged gets a cherry-pick route to a `<branch>-v2`
  instead of a rebase plus force push. (#305)

### Added
- `dev board brief` lists the `dev board new` form for out-of-scope follow-ups in its Commands section. (#324)

### Fixed
- `dev setup` prints WARN, not FAIL, when user settings register the same marketplace repository at another
  project's ref, naming both refs; another repository under that name still fails. (#327)
- agent-harness tracks its own `.claude/settings.json`, so the generated agent surface no longer depends on whether
  this machine ran `dev setup`. (#328)

### Changed
- `dev integrate` waits (bounded by `checks_wait_s`) for checks still running on the PR's own head when they are its
  only unfinished feedback, then judges them; a failing check, requested change or open thread still refuses at
  once, and a timeout ends in `waiting`, which a rerun resumes. Under `--local-ci` the required checks it stands in
  for are neither waited for nor judged there; the others (e.g. CodeQL) are. Not with `--override` or `--dry-run`.
  The lead skill no longer asks the lead to wait for green checks by hand. (#321, #322)
- researcher checks a repository that is not a registry package with authenticated
  `gh api repos/<o>/<r> --jq '{...}'` instead of curling `api.github.com` into a Python filter (60 requests/hour
  unauthenticated; 7.7 % of those calls failed). (#323)
- A second `waiting` on the same head carries `"again": true`, and its `RESULT` line points to `--local-ci` or the
  owner instead of another rerun. (#305)
- A stacked PR retargeted to main gets a route auto mode allows instead of `git rebase --onto` plus a force push: a
  `<branch>-v2` from main, its own commits cherry-picked by sha, a new PR via `dev pr`, the old one closed. (#305)

## v0.1.10 (2026-10-19)

Research-driven harness changes (#254, #282): shorter always-loaded rules, one hand-back and review contract,
explicit effort per agent, plugin skew detection, squash-only integrate, and a hard limit on hung tests.

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.10`, then `uv sync && uv run dev docs && uv run dev check`. These can
need attention:
- **Plugin skew fails `doctor`.** `uv run dev doctor` exits 1 when Claude Code loaded a harness-ops build whose
  version or content digest differs from the pinned tag's (was a warning). `uv run dev setup` re-pins the plugin to
  the tag; `dev setup --repin-all` is the last resort and uninstalls the plugin from every other project. The
  SessionStart and SubagentStart hooks print the skew line. (#296, #310)
- **integrate squash-merges.** The squash commit is the PR title and body only, so put the trailers a commit needs
  in the PR body. Open PRs based on the merged branch are retargeted to main, with the `git rebase --onto` to run.
  In a public repository `dev pr` and `integrate` refuse a Claude session link or `Claude-Session:` trailer in the
  title, body or commit messages. The repository must allow squash merges; agent-harness itself is now
  squash-only. (#292, #295)
- **Compaction at 400k.** The template's `.claude/settings.json` sets `"autoCompactWindow": 400000`. (#258, #304)
- **Workers withhold tools.** builder sets `disallowedTools: Agent, Artifact`; tester and researcher also withhold
  `Skill`. The template leaves `enableArtifact` unset, so the lead keeps Artifacts. (#256, #311)
- **Effort per agent.** Every agent sets `effort` in its frontmatter (builder, reviewer, suite-auditor `medium`; the
  others `high`); the lead skill escalates per dispatch after a failure. (#259, #306)
- **One hand-back contract.** Every agent hands back at most 25 lines: Blocked on me, Changed, Found. (#263, #293)
- **Review verdict contract.** Findings are tagged Blocking, Nit, Pre-existing or Follow-up, and only Blocking
  returns a PR; from round 3 any Blocking finding means reconsider the approach. Each Blocking finding names a
  runnable probe that the builder commits as a test and reruns before each fix push. (#260, #309, #257, #315)
- **`dev lit search` coverage line.** Its first line is `discovery YYYY-MM-DD: OK|OK, partial|DEGRADED (...)` and it
  exits 1 on DEGRADED; the separate `dev lit status` step is gone from the research agents. (#266, #274)
- **Test timeout and gate deadline.** `pytest-timeout` is a dependency; `[tool.devkit] test_timeout_s = 300` fails a
  hung test (mark a slow one `@pytest.mark.timeout(n)`), and `gate_deadline_s = 3600` stops integrate's gate and
  kills its process group. (#43, #290)

### Added
- `dev agent-stats --cost`, `--reviews` and `--misses`: cost per merged issue reconciled with the cost records,
  review rounds per role, and candidate post-merge defects. (#255, #280)
- `dev eval --model/--effort/--agent/--label` arms with a check that the intended model ran, and a paired A/B
  report. (#82, #288)
- guard_bash denies `curl`, `wget` and WebFetch to the literature APIs `dev lit` wraps (`DEVKIT_LIT_RAW=1`
  overrides). (#227, #285)
- integrate refuses a merge that deletes a path another open issue owns; board lint reports deleted Owns as errors.
  (#230, #286)

### Changed
- HARNESS.md drops lines a hook enforces or another file repeats (543 -> 310 words, budget 320) and builder.md
  states that Edit and Write need a prior Read. (#267, #278)
- One-time owner-approved budget raise: lead `SKILL.md` 650 -> 720 words, `builder.md` 600 -> 650. (#267, #317)
- `dev pr` prints the PR URL and one status line; builders send the body with nothing piped after it. (#261, #273)
- read_guard windows Reads of persisted `tool-results/*.txt` outputs over 300 lines or 40 KB. (#265, #272)

## v0.1.9 (2026-10-15)

The owner can change the weekly quota cap on one machine without a PR: `! uv run dev quota-cap <0-100>`.

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.9`, then `uv sync && uv run dev docs && uv run dev check`.
- **Machine-local quota cap.** `uv run dev quota-cap` prints the effective cap and its source; `<pct>` writes an
  override to `<git-common-dir>/devkit/quota_cap` (never committed, shared by all worktrees), `--clear` drops it.
  The spawn guard, `orient` and `status` use it over `[tool.devkit] quota_cap`. The Bash and Edit guards deny agents
  setting it. (#247)

## v0.1.8 (2026-10-14)

Research tools share one rate limit per user: sessions in different repositories on one machine no longer overrun
an API key together. The `Reuse:` line asks whether each new dependency does the job, and through which API.

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.8`, then `uv sync && uv run dev docs && uv run dev check`. These can
need attention:
- **Rate limits move to a per-user directory.** `dev lit` and `dev reuse` keep their token buckets and shared
  cooldowns in `$XDG_STATE_HOME/devkit/lit` (default `~/.local/state/devkit/lit`), shared by every repository on the
  machine; the response cache stays in `<git-common-dir>/devkit/lit`. A project still on an older release keeps its
  own buckets, so update every project that searches with the same keys. SETUP.md lists every file devkit keeps
  outside a repository and how to remove it. (#241)
- **New fit clause in the `Reuse:` line.** Each new dependency needs `fit: <package> does <need> via <API>` (or
  `fit: unsure`) in a `Reuse:` line; `Reuse: none: harness adoption` on this update needs none. (#196)

### Added
- The `Reuse:` completion check refuses a new dependency without a fit clause, and the builder asks the fit question
  before `uv add`; the method is picked by a pre-registered eval in `software-discovery.md` §11. (#196)
- README cites The Semantic Scholar Open Data Platform (arXiv:2301.10140), as the Semantic Scholar API license asks.
  (#241)
- Research notes: recall of `dev lit search` against WebSearch and the arxiv MCP (#192), a pre-registered rerun of
  the discovery benchmark (#176), decision records and drift (#220), and builders' reuse trigger rate (#183).

### Changed
- Semantic Scholar is asked at 0.8 requests/s (was 1/s), below a key's 1/s across all endpoints. (#241)
- `dev reuse --from` builds the need from the symbol's name, its docstring's first sentence and its third-party
  imports only. (#175)

### Fixed
- `dev reuse check --json a b` and other option orders: bare words after an option join the need. (#175)
- The licence gate (G5) matches SPDX ids case-insensitively: `mit` and `apache-2.0` were HOLD. (#215)

## v0.1.7 (2026-10-13)

Reuse before build becomes a checked step: agents verify candidate libraries before they write code, `uv add` runs
the same gates, and the PR names what was weighed. The literature commands gain fields and citation chasing.

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.7`, then `uv sync && uv run dev docs && uv run dev check`. These can
need attention:
- **New `uv add` guard (PreToolUse hook).** Each package named in `uv add` goes through `uv run dev reuse check`
  first. A name PyPI does not know, a malicious-package advisory or strong or network copyleft is refused; other
  findings are warnings, and an unreachable registry fails open with a warning. To allow a copyleft package, the
  owner lists it in `DEVKIT_ALLOW_COPYLEFT` (comma-separated) in the environment Claude Code runs in; an assignment
  inside the command does not count. (#172)
- **New `Reuse:` completion check.** A branch that adds a dependency to pyproject.toml or a new non-test module
  needs one `Reuse:` line in its PR summary: candidates with their `reuse check` results, the research note that
  weighed them, or `none: <reason>` (`Reuse: none: harness adoption` for this update). (#173)
- **Optional extra `semantic`.** `uv add 'agent-harness[semantic]'` installs model2vec, which `dev reuse` search
  uses to re-rank by meaning; without it the re-rank is BM25, and the output says which ran. (#29)

### Added
- `uv run dev reuse "<need>"` searches, gates and ranks maintained libraries and repositories, and
  `uv run dev reuse check <name>...` verifies named candidates (existence, malicious advisories, age, maintenance,
  licence, open advisories). The builder and researcher prompts name candidates first, then check them. Design and
  evidence in the research note `software-discovery.md`. (#29, #169, #179)
- `uv run dev lit get` returns authors, the open-access link and the full abstract, takes several ids, and batches
  OpenAlex lookups. (#174, #30)
- `uv run dev lit refs` and `uv run dev lit cited-by` chase citations backward and forward through OpenAlex. (#193)
- `uv run dev lit search --also` searches paraphrases too and merges the results by rank. (#194)
- `uv run dev cite-check` flags DOIs that Crossref marks retracted, withdrawn or removed; an expression of concern
  is a note. (#195)
- Ten pre-registered reuse-before-build trigger scenarios with their grader under evals/. (#183)
- The lead skill documents dispatching a builder onto another repository's issue. (#170)

### Fixed
- The licence gate reads a dual-licensed package as either licence (OR) and prefers the registry's SPDX
  expression, and it flags an undeclared licence list that mixes in strong copyleft. (#180, #182)

## v0.1.6 (2026-10-13)

A fix found adopting v0.1.5.

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.6`, then `uv sync && uv run dev docs && uv run dev check`.

### Fixed
- `uv run dev cite-check` with no paths skips the research README.md, which the template ships since v0.1.5, as
  well as INDEX.md. Before, the first PR that brought the README in failed with `0 citations in 1 note(s)`. (#165)

## v0.1.5 (2026-10-13)

Round-2 hardening. The gate catches more ways to pass without doing the work, merging works without Actions
minutes, and the template now ships the generic files that projects had been writing by hand.

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.5`, then `uv sync && uv run dev docs && uv run dev check`. Without
the flag the update keeps the stored v0.1.4 answer, and the devkit and plugin stay pinned there. These can need
attention:
- **New gate checks can fail a tree that passed under v0.1.4.**
  - Agent and skill frontmatter: a key outside the documented tables fails the gate, and a skill's `version:` key
    is now an error. Values outside the documented options fail too (`effort`, `isolation`, `memory`, `color`,
    `maxTurns`, `context`, `shell`). (#153)
  - Skill bash injection: an inline ``!`cmd` `` or the last line of a ```` ```! ```` block must end in `|| true` or
    in a `| head`/`| tail` pipeline, and it needs an allow rule. An unguarded one fails. (#153)
  - Test weakening: the gate flags removed tests or asserts and new skip/xfail (#55). It now also flags loosened or
    trivial asserts, a swallowed `AssertionError`, dropped parametrize cases, conftest and pytest/coverage config
    edits, golden-file edits, and patches of a name an assert calls (#127). The issue must record owner approval
    (`weakening-ok:`) for any of these.
  - Test floors: `[tests] min_tests` in `.devkit/ratchets.toml` (template default 1) and an optional
    `min_coverage`. Lowering either needs an owner-approved raise entry (#58). `xfail_strict = true` is on, so an
    xfail test that starts passing fails the run (#136).
  - `guard_bash` blocks destructive git and filesystem commands (#52), and read-only agents may not pass `git -c`
    (#145). Reading `core.hooksPath` with `git config --get` is now allowed; writing it is still refused (#45).
- **Files the template now ships that a project may already have locally.** `copier update` can report a conflict
  for each one. Keep your version or take the template's, and remove any conflict markers or `.rej` files:
  .devkit/HARNESS.md, docs/PROMPTS.md, docs/specs/multi-agent.md, scripts/claude-memory.sh, evals/README.md and
  evals/scenarios/, CONTRIBUTING.md, SECURITY.md and .editorconfig (sources under [template/](template)). `CHANGELOG.md`,
  `CLAUDE.md` and `README.md` are project-owned, and the update never touches them.
- **CLAUDE.md split.** Replace the generic sections of your CLAUDE.md with the line `@.devkit/HARNESS.md`, once,
  by hand ("Updating a project" in [README.md](README.md)). (#22)
- **Dependency pin.** The dev group now requires `agent-harness @ git+…@<tag>`, a direct reference, so a
  `--no-sources` resolve cannot pick the unrelated `agent-harness` on PyPI. (#61)

### Gate and checks
- Test-weakening check, extended to 11 kinds (#55, #127). Test-count and coverage floors, with report-only diff
  coverage (#58). `xfail_strict` on and higher floors in this repository (#136). Per-test JUnit outcome and
  duration history for audits (#121).
- Frontmatter allowlists match the documented agent and skill fields, with value and injection checks (#153).
- The word budget counts the rules and commands files under a project's `.claude/` (#44).
- Free secret and code scanning: a gitleaks hook and ruff `S` rules (#15).
- cite-check no longer reads DOI fragments as arXiv ids and resolves DataCite (Zenodo) DOIs (#35).

### Board, integrate and CI
- Integrate blocks on failing checks, unresolved review threads and requested changes (#39). Merge hygiene:
  stale branches are updated automatically, conflicts go back to the builder, and union merge applies only to
  append-only files (#38). After a push, integrate waits for every check on the new tip (#72).
- Merging works with no Actions minutes: the local gate on the merged tree posts the required check contexts (#76).
- The board uses GitHub's native sub-issues and blocked-by relationships (#64). `board lint` checks issue
  conventions and fixes the mechanical ones with `--fix` (#74).
- Template `gate.yml`: one concurrency group per PR or SHA, and citations folded into check (#57).
  `maintenance.yml` no longer fails on an issue body over 65,536 characters (#120).
- `spawn_guard` covers workflow agents, and a jq error can no longer block every spawn (#85).

### Plugin
- Agents and skills use the documented frontmatter: `maxTurns` and `color` on every agent, `isolation: worktree`
  for architect and suite-auditor, `when_to_use` on skills, `$ARGUMENTS` for research, and a `dev status`
  preload for lead (#153).
- New suite-auditor agent for a size-tiered test-suite audit, held read-only by `guard_bash` (#114, #146).
- Builder and tester prompts forbid special-casing test inputs (#143). A fix-now rule for small finds, and
  follow-up triage before integrate (#154).
- agent-stats no longer undercounts output tokens (it was 4 to 13 times low) (#53).

### Template
- CLAUDE.md is split into a project-owned CLAUDE.md and the template-owned .devkit/HARNESS.md (#22).
- Standard repository files: CONTRIBUTING, SECURITY, `.editorconfig` and CHANGELOG (#81). Routine prompts, the
  memory transfer script, the multi-agent spec, agent-surface evals and more read-only allow rules (#115).

### Research and internals
- New notes: [test-suite design](docs/research/test-suite-design.md) (#112),
  [architecture consolidation](docs/research/architecture-consolidation.md) (#111, #138),
  [fix now or defer](docs/research/fix-now-vs-defer.md) (#149),
  [test floors and coverage](docs/research/test-floors-and-coverage.md) (#58) and
  [free security scanning](docs/research/free-security-scanning.md) (#15).
- devkit: shared HTTP-get and atomic-write helpers (#139), and the import cycle through `cli` is gone (#140).
