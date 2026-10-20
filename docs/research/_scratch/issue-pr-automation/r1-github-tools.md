# r1: GitHub-native features, hosted apps and local tools that can take over issue/PR bookkeeping without Actions

Round 1 sub-question (b) of #346. Search coverage: no `dev lit search` run (the question is about product docs, not
literature, so no discovery line was printed); sources are GitHub docs fetched from their source repository
([github/docs](https://github.com/github/docs), GitHub, 2026) and the rendered docs API, vendor pages via WebFetch,
context7 (Mergify, git-cliff), gh 2.102.0 `--help`, and read-only `gh api` probes. Probe script:
[github_env_probe.sh](scripts/github_env_probe.sh) (re-run it to refresh the VERIFIED rows).
Date of all checks: 2026-10-20.

## Findings

### F0. The constraint differs by repository: Actions are blocked only on the private repository.
- VERIFIED (probe): `Marius-Juston/agent-harness` is public and user-owned. Of its last 40 runs, `pull_request` went
  11/12 success, `push` 7/7, Dependabot (`dynamic`) 21/21. `repo-E` is private and user-owned:
  `pull_request` 0/10, `push` 0/3, `schedule` 0/20, but Dependabot 7/7. The failed `board` job (2026-10-20 17:30) has
  0 steps and the annotation "The job was not started because recent account payments have failed or your spending
  limit needs to be increased".
- SOURCED: Dependabot on Actions runners "**does not** count towards your included GitHub Actions minutes"
  ([About Dependabot on GitHub Actions runners](https://docs.github.com/en/code-security/concepts/supply-chain-security/dependabot-on-actions), GitHub Docs, 2026). That matches the 7/7 above.
- BELIEVED: so for a public harness repo (agent-harness itself) the Actions cron and Actions-based apps still work;
  every "works without Actions" verdict below matters for private harness-run projects (repo-E) and for the period
  until ~Nov 2026.

### F1. Native merge queue is out for user-owned repositories, public or private. No hosted tool removes the local gate runner.
- SOURCED: "Pull request merge queues are available in any public repository owned by an organization, or in private
  repositories owned by organizations using GitHub Enterprise Cloud"
  ([gated-features/merge-queue.md](https://github.com/github/docs/blob/main/data/reusables/gated-features/merge-queue.md), github/docs, 2026), reused by [Managing a merge queue](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue) (GitHub Docs, 2026).
  Third-party CI must run on pushes to `gh-readonly-queue/{base_branch}` branches; Actions must use `merge_group`.
- SOURCED: [Mergify pricing](https://mergify.com/pricing) (Mergify, 2026): "Open Source" $0 ("Zero cost for open
  source projects and private teams of up to 5"), "Free" $0 up to 5 users on private repositories; Merge Queue is in
  every plan. Personal-account eligibility is not stated (unconfirmed). Mergify checks a queued PR "directly on the
  original pull request, instead of creating temporary batch PRs" only when `batch_size = 1`,
  `max_parallel_checks = 1`, serial mode, `max_checks_retries = 0` and no two-step CI
  ([Mergify batches docs via context7](https://docs.mergify.com/merge-queue/batches/), Mergify, 2026).
- SOURCED: [Kodiak billing](https://kodiakhq.com/docs/billing) (Kodiak, 2026): "Kodiak is free for personal use and
  public repositories"; a subscription ($4.99 per active user per month) is needed only for private *organization*
  repositories. It merges PRs carrying an `automerge` label once required checks pass
  ([Kodiak quickstart](https://kodiakhq.com/docs/quickstart), 2026). Repo active: last push 2026-10-20, AGPL-3.0,
  1,116 stars (VERIFIED, `gh api repos/chdsbd/kodiak`).
- VERIFIED (devkit source): the required contexts on agent-harness (`check`, `template`, `plugin`) are statuses that
  `dev integrate` posts itself after a local gate (`devkit/workflow.py`, `repos/{repo}/statuses/{tip}`). repo-E's
  `main` protection has no required checks at all.
- BELIEVED: any queue (Mergify, Kodiak, native) waits for a check on the updated/queued head. With no Actions, that
  check is the local gate, so a local process must still notice the queued head, gate it and post the status. A
  hosted queue therefore adds a second state machine without removing the local one. Candidate 1 (a devkit-local
  queue) stays the right shape; hosted queues are prior art for its design (serial, batch 1, refusal per head).

### F2. Native auto-merge works on the public repo now; on the private repo it needs GitHub Pro.
- SOURCED: auto-merge "is available in public repositories with GitHub Free … and in public and private repositories
  with GitHub Pro, GitHub Team, GitHub Enterprise Cloud"
  ([gated-features/auto-merge.md](https://github.com/github/docs/blob/main/data/reusables/gated-features/auto-merge.md), github/docs, 2026). It "merges a pull request automatically after all required reviews and status checks pass"
  ([Automatically merging a pull request](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/automatically-merging-a-pull-request), GitHub Docs, 2026).
- VERIFIED: `allow_auto_merge` is `false` on both repos; `gh pr merge` offers `--auto` and `--match-head-commit`
  (gh 2.102.0), and the GraphQL schema has `enablePullRequestAutoMerge`. devkit already merges with
  `--match-head-commit … --delete-branch`.
- BELIEVED: repo-E's private `main` has branch protection, and protected branches on private repos need Pro
  ([gated-features/protected-branches.md](https://github.com/github/docs/blob/main/data/reusables/gated-features/protected-branches.md), github/docs, 2026), so the owner is probably on Pro (`gh api user` returns `plan: null`; unconfirmed).
- BELIEVED: the gain is narrow. Where `dev integrate` waits for Actions checks still running (v0.1.11, #322), it could
  instead post its local statuses, call `gh pr merge --auto --match-head-commit <sha>` and return, so the session does
  not block. That helps candidate 1 on public repos only; on repo-E nothing else posts checks, so auto-merge would
  fire at the moment the local gate posts, which `integrate` already does.

### F3. Rulesets, issue types, issue fields, sub-issues and dependencies.
- SOURCED: rulesets "are available in public repositories with GitHub Free … and in public and private repositories
  with GitHub Pro" ([gated-features/repo-rules.md](https://github.com/github/docs/blob/main/data/reusables/gated-features/repo-rules.md), github/docs, 2026), up to 75 per repository ([About rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets), GitHub Docs, 2026). Rules include "Require status checks to pass", "Require linear history", "Require a pull request before merging" ([Available rules for rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets), GitHub Docs, 2026). Rulesets enforce, they do not do bookkeeping; no candidate needs them. VERIFIED: agent-harness has 0 rulesets and uses classic protection.
- SOURCED: issue types are organization objects ("Organization owners can modify issue types", up to 25)
  ([Managing issue types in an organization](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/managing-issue-types-in-an-organization), GitHub Docs, 2026). Issue fields "are organization-level fields"
  ([About issue fields in projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/understanding-fields/about-issue-fields), GitHub Docs, 2026).
  VERIFIED: GraphQL `repository.issueTypes` is `null` on both user-owned repos. So `type:`/`P1` labels stay labels.
- SOURCED: up to 100 sub-issues per parent and eight nesting levels
  ([Adding sub-issues](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues), GitHub Docs, 2026). Issue dependencies "are available for users on GitHub Free, GitHub Pro, GitHub Team, and GitHub Enterprise Cloud" and `gh issue create --blocked-by/--blocking`, `gh issue edit --add-blocked-by`, `gh issue view --json blockedBy,blocking` exist ([Creating issue dependencies](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-issue-dependencies), GitHub Docs, 2026). No dependency-count limit is documented there.
- VERIFIED (devkit source): `devkit/board.py` already uses native `addBlockedBy`/`addSubIssue` as the source of truth,
  and `devkit/stats.py` reads `closingIssuesReferences`. Nothing to adopt here; already shipped.

### F4. `closingIssuesReferences` and closing keywords: already used; one limit matters.
- SOURCED: keywords "are interpreted only when the pull request targets the repository's _default_ branch"; a commit
  message keyword closes the issue but the PR "will not be listed as a linked pull request"
  ([Linking a pull request to an issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue), GitHub Docs, 2026).
- VERIFIED (devkit source): `dev pr` writes `Closes #N` (or `Refs #N` with `--partial`) as the body's first line.
  BELIEVED: a stacked PR (base not `main`) gets no link; candidate 6's generated body should keep the issue number in
  the title as well (as #100 plans) so it survives a stack.

### F5. Projects v2 built-in workflows cannot replace `board event`.
- SOURCED: built-in workflows "update the **Status** of items based on certain events" (item added, closed, merged);
  two are on by default (closed → Done, merged → Done); others auto-archive and auto-add
  ([Using the built-in automations](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-built-in-automations), GitHub Docs, 2026). Auto-add workflows per plan: Free 1, Pro 5
  ([Adding items automatically](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/adding-items-automatically), GitHub Docs, 2026).
- VERIFIED (`template/.github/workflows/board.yml`): the cron job's work is "blockers are re-synced, so items whose
  blockers closed move to ready, and claims with no comment for 6 h are labelled `stale`". BELIEVED: no built-in
  workflow triggers on a blocker closing or on comment age, and the harness board is labels, not a Project. So
  Projects v2 replaces none of candidate 3; it would add a second status store.

### F6. Local scheduling (candidate 3): systemd user timer with `Persistent=true`, plus a staleness warning; not webhooks.
- SOURCED: `Persistent=` stores the last trigger time on disk so a missed `OnCalendar=` run fires "immediately" on
  activation ([systemd.timer](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html), systemd, 2026). `loginctl enable-linger` spawns the user manager "at boot and kept around after logouts" ([loginctl](https://www.freedesktop.org/software/systemd/man/latest/loginctl.html), systemd, 2026).
- SOURCED: WSL shuts an idle distribution down: `instanceIdleTimeout` default 15000 ms ("Set to -1 to disable auto
  shutdown"), `vmIdleTimeout` default 60000 ms; systemd needs `[boot] systemd=true`
  ([Advanced settings configuration in WSL](https://learn.microsoft.com/en-us/windows/wsl/wsl-config), Microsoft Learn, updated 2026-10-16).
- VERIFIED (this machine, WSL2 kernel 6.18): PID 1 is systemd, `/etc/wsl.conf` has `systemd=true`, `systemctl --user`
  works (state `degraded`), `Linger=no`, cron is installed. BELIEVED: timers fire only while WSL runs; with
  `Persistent=true` a missed run catches up at the next start. Whether a lingering user manager keeps an idle distro
  alive is unconfirmed. Hence the orient warning in candidate 3 ("last board event N h ago") is required, not
  optional. Plain cron has no catch-up (BELIEVED; anacron does, but only daily granularity).
- SOURCED: `gh webhook forward` (extension `cli/gh-webhook`) "is only designed for use during testing and development.
  It is not supported for use in production"; "Only one person can use webhook forwarding at a time for each
  repository" ([Using the GitHub CLI to forward webhooks for testing](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/using-the-github-cli-to-forward-webhooks-for-testing), GitHub Docs, 2026). Not usable for multi-machine leads.
- SOURCED: events are "optimized for polling with the ETag header … 304 Not Modified … your current rate limit will be
  untouched", `X-Poll-Interval` must be obeyed, and the API "is not built to serve real-time use cases … event latency
  can be anywhere from 30s to 6h"; up to 300 events, 30 days ([REST API endpoints for events](https://docs.github.com/en/rest/activity/events), GitHub Docs, 2026). Notifications poll with `Last-Modified` the same way ([REST API endpoints for notifications](https://docs.github.com/en/rest/activity/notifications), GitHub Docs, 2026).
- VERIFIED (probe, both repos): `X-Poll-Interval: 60`; two conditional `GET /repos/{r}/events` returned `304` and
  `X-Ratelimit-Remaining` stayed unchanged (4833 → 4833; 4829 → 4829). So a 1-minute ETag poll is free in rate limit,
  but its latency bound (up to 6 h) makes it a cheap "anything changed?" pre-check before `board event`, not a trigger
  for the review relay.

### F7. Changelog (candidate 8): git-cliff locally, GitHub generated notes as the zero-install fallback.
- VERIFIED (`dev reuse check`, 2026-10-20): `git-cliff` (PyPI) PASS Q 0.90, Apache-2.0, last release 2026-09-18;
  `commitizen` PASS Q 0.92, MIT, last release 2026-10-07; `release-please` (npm) PASS Q 0.93, Apache-2.0, last release
  2026-08-24 (not on PyPI: G1 FAIL there).
- SOURCED: git-cliff runs offline on git history and can optionally pull PR titles, numbers and labels with
  `GITHUB_TOKEN=… git cliff --github-repo owner/repo`, and group commits by `github.pr_labels`
  ([git-cliff GitHub integration](https://git-cliff.org/docs/integration/github), git-cliff, 2026).
- SOURCED: `cz changelog --incremental` extends an existing changelog and "lets you edit the changelog manually
  without it being completely rewritten"; `cz bump --changelog` bumps and writes it
  ([commitizen changelog](https://commitizen-tools.github.io/commitizen/commands/changelog/), commitizen, 2026).
- SOURCED: release-please's CLI needs `--token` and `--repo-url` and opens release PRs through the API, so it runs
  without Actions ([release-please CLI](https://github.com/googleapis/release-please/blob/main/docs/cli.md), googleapis, 2026).
- SOURCED: GitHub's generated release notes list merged PRs and contributors, with label-based categories, through
  `POST /repos/{owner}/{repo}/releases/generate-notes` ([Automatically generated release notes](https://docs.github.com/en/repositories/releasing-projects-on-github/automatically-generated-release-notes), GitHub Docs, 2026; [REST releases](https://docs.github.com/en/rest/releases/releases), 2026). No commit convention needed.
- VERIFIED (`git log`): the last 15 subjects are not Conventional Commits (`integrate: …`, `release: …`), and
  CHANGELOG.md entries carry a hand-written "Upgrading a project" section. BELIEVED: any generator needs #100 first and
  can draft the Added/Fixed lists only; the upgrade narrative stays manual. git-cliff fits best (single binary/PyPI
  wheel, offline, template keeps the current file layout); release-please overlaps #60 and owns versioning, which the
  harness does by tag already.

### F8. Claim + worktree (candidate 9): `gh issue develop --worktree` now exists.
- SOURCED: gh v2.99.0 (2026-09-01): "`gh issue develop` can now create a linked branch and check it out in a new Git
  worktree" (`gh issue develop 123 --checkout --worktree /path`); v2.98.0 added `gh pr checkout --worktree`
  ([cli/cli v2.99.0 release](https://github.com/cli/cli/releases/tag/v2.99.0), GitHub, 2026; [cli/cli v2.98.0 release](https://github.com/cli/cli/releases/tag/v2.98.0), GitHub, 2026). VERIFIED: present in local gh 2.102.0 `--help` (`--name`, `--base`, `--worktree`).
- BELIEVED: it creates the branch on GitHub (a remote push) and links it in the issue's Development sidebar, which
  devkit's `board claim` does not do; it does not post the claim comment or set `status:in-progress`. So `board claim
  --worktree` could call it for the branch+worktree half (one gh call instead of `git worktree add`), but devkit's own
  worktree layout and branch name must still be passed explicitly. Unconfirmed: behaviour when the branch already
  exists.
- VERIFIED (repo health, `gh api`): [gh-poi](https://github.com/seachicken/gh-poi) (MIT, 1,022 stars, pushed
  2026-10-18) deletes merged local branches, including squash merges. Already covered by v0.1.12 (`integrate` deletes
  merged branches, `dev worktree prune`), so no gain.

### F9. Duplicate detection (candidate 4): two native pieces, neither replaces a local check.
- SOURCED: while typing a new issue in the web UI, "GitHub may suggest potential duplicate issues … once the title is
  filled out and the body reaches 100 characters, and up to three existing issues may be shown … non-blocking"
  ([Creating an issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-an-issue), GitHub Docs, 2026). Agents file through `gh`/GraphQL, so they never see it (BELIEVED).
- SOURCED: gh v2.98.0 added `gh search issues --search-type semantic|hybrid`; results are relevance-ranked, a single
  page, no `--sort` ([cli/cli v2.98.0 release](https://github.com/cli/cli/releases/tag/v2.98.0), GitHub, 2026).
- VERIFIED (small probe, agent-harness, 5 queries): one query ("merge queue") returned the same two issues in all
  three modes; three paraphrase queries of known closed issues (#340, #318, #324) returned nothing in lexical or
  semantic mode; "branch" returned 5 lexical hits and 0 semantic. The next semantic/hybrid calls got HTTP 403 "API
  rate limit exceeded", yet a `rate_limit` read moments later showed the search bucket at 21/30 (other agents share
  the token, so the cause is unconfirmed). So on this probe semantic search adds no
  recall on a small personal repo and has a tighter limit. N is tiny; the dedup sub-round should measure it on the
  held-out split before ruling it out.
- SOURCED: `gh issue close <n> --duplicate-of <m>` (gh 2.102.0 `--help`) and the "Duplicate of #N" comment create a
  "marked as duplicate" timeline event ([Marking issues or pull requests as a duplicate](https://docs.github.com/en/issues/tracking-your-work-with-issues/administering-issues/marking-issues-or-pull-requests-as-a-duplicate), GitHub Docs, 2026). BELIEVED: closing through this gives the dedup evaluation clean labels for the future.

### F10. Review relay (candidate 2): the native piece is one REST review with inline comments; hosted AI reviewers add cost or need Actions.
- SOURCED: `POST /repos/{o}/{r}/pulls/{n}/reviews` takes a `comments` array (path plus `line` or diff `position`),
  `event` APPROVE/REQUEST_CHANGES/COMMENT, or blank for PENDING; it "may result in secondary rate limiting"
  ([REST API endpoints for pull request reviews](https://docs.github.com/en/rest/pulls/reviews), GitHub Docs, 2026). "Pull request authors cannot approve their own pull requests" ([Approving a pull request with required reviews](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests/approving-a-pull-request-with-required-reviews), GitHub Docs, 2026).
- VERIFIED: GraphQL has `addPullRequestReview`, `resolveReviewThread`, `unresolveReviewThread`, and
  `pullRequest.reviewThreads { isResolved isOutdated path }` reads back per-thread state. BELIEVED: since agents act
  as the owner, who authors the PR, `dev review post` can only use `event: COMMENT`; "blocking" must live in the
  thread/body format and `isResolved`, not in REQUEST_CHANGES.
- SOURCED: [CodeRabbit pricing](https://www.coderabbit.ai/pricing) (CodeRabbit, 2026): "free reviews forever for public
  repositories"; private repos: paid from $24/user/month (annual) after a 14-day trial; no free private tier listed.
- SOURCED: Copilot code review "uses GitHub Actions to run the agentic capabilities"; if Actions are unavailable
  "reviews will still be generated" without those capabilities; it costs AI credits plus Actions minutes, and the
  Copilot Free plan does not include it ([Copilot code review](https://docs.github.com/en/copilot/concepts/agent-capabilities/agent-powered-workflows-and-operations/code-review), GitHub Docs, 2026).
- SOURCED: [PR-Agent](https://github.com/qodo-ai/pr-agent) (Qodo, "community-maintained legacy project", MIT per
  `gh api`, 13,499 stars, pushed 2026-10-20) runs as a local CLI (`pip install "pr-agent[github]"`) with one LLM call
  per tool and posts to the PR. BELIEVED: it would replace the harness's own reviewer, not the relay, and
  ADR-style model routing (repo-E routes model calls through one module) argues against a second LLM path. Not recommended.

### F11. Other hosted apps checked.
- SOURCED: the Mend-hosted Renovate app is free ("A generous free tier … unlimited number of public and private
  repositories"), runs on Mend's runners every 4 h (Community/OSS) and on webhooks
  ([Mend-hosted Renovate overview](https://docs.renovatebot.com/mend-hosted/overview/), Mend, 2026); it does not need
  Actions (BELIEVED: the page never mentions them). It is dependency bookkeeping only; Dependabot already runs on
  both repos (F0). No candidate.
- VERIFIED (`gh api`): Probot `stale` (archived 2023) and `no-response` (archived 2021) are dead; [probot/settings](https://github.com/probot/settings) (ISC, pushed 2026-10-20) has a hosted app that syncs `.github/settings.yml` to repository settings. BELIEVED: useful only to flip `allow_auto_merge`/`delete_branch_on_merge` as code; one-off `gh api -X PATCH` does the same.
- VERIFIED (repo health): [gh-dash](https://github.com/dlvhdr/gh-dash) (MIT, 12,651 stars, pushed 2026-10-17): a TUI
  with "User-defined, per-repo, PRs & issues sections" and "Custom actions". BELIEVED: an owner dashboard, not
  something an agent drives; it can show the HANDOFF view (candidate 7) live but does not write it.
- VERIFIED: GitHub's own `mergeable`/`mergeStateStatus` is computed per PR against its base, lazily: 3 of 4 open
  repo-E PRs read `UNKNOWN`. It cannot predict PR-vs-PR conflicts, so candidate 5 needs `git merge-tree` locally.

## Summary table

| Tool / feature | Runs where | Cost | Works on personal account without Actions | Candidate |
| --- | --- | --- | --- | --- |
| Native merge queue | GitHub | n/a | **No** (org-owned only; F1) | 1: rules out |
| Mergify queue + rules | Mergify cloud | $0 OSS / ≤5 users | Unconfirmed for user accounts; needs a check on the queued head, so local gate still runs (F1) | 1: prior art only |
| Kodiak | Kodiak cloud | $0 personal & public | Yes (BELIEVED), but same local-gate need (F1) | 1: prior art only |
| Native auto-merge (`gh pr merge --auto --match-head-commit`) | GitHub | free public; Pro private | Yes; off on both repos (F2) | 1: helps on public repo |
| Rulesets | GitHub | free public; Pro private | Yes (F3) | none |
| Issue types / issue fields | GitHub | n/a | **No** (org-level; F3) | none |
| Sub-issues, dependencies, `closingIssuesReferences` | GitHub | free | Yes; already used by devkit (F3, F4) | done |
| Projects v2 built-in workflows | GitHub | free (auto-add: Free 1, Pro 5) | Yes, but cannot re-sync blockers or sweep stale claims (F5) | 3: no |
| systemd user timer, `Persistent=true` (+linger) | owner's machine/WSL | free | Yes; fires only while WSL runs (F6) | 3: yes |
| cron | owner's machine | free | Yes; no catch-up (F6) | 3: fallback |
| `gh webhook forward` | owner's machine | free | Dev-only, one forwarder per repo (F6) | 3: no |
| Events/notifications polling with ETag | owner's machine | free (304 costs 0) | Yes; latency up to 6 h (F6) | 3: pre-check |
| git-cliff | local | free, Apache-2.0 | Yes (offline) (F7) | 8: chosen |
| commitizen | local | free, MIT | Yes (F7) | 8: alternative |
| release-please CLI | local (API) | free, Apache-2.0 | Yes (F7) | 8: overlaps #60 |
| GitHub generated release notes | GitHub API | free | Yes (F7) | 8: fallback |
| `gh issue develop --worktree` | local + GitHub | free | Yes (gh ≥ 2.99.0) (F8) | 9: helps |
| gh-poi | local | free, MIT | Yes; superseded by v0.1.12 (F8) | 9: no |
| Web duplicate suggestions; `gh search issues --search-type semantic` | GitHub | free | UI-only; semantic gave no recall on a 5-query probe (F9) | 4: measure, likely no |
| `gh issue close --duplicate-of` | GitHub | free | Yes (F9) | 4: label source |
| PR review REST with inline `comments`, `reviewThreads.isResolved` | GitHub | free | Yes; COMMENT only on own PRs (F10) | 2: yes |
| CodeRabbit | CodeRabbit cloud | free public; ≥$24/user/mo private | Yes, public only (F10) | 2: optional, public |
| Copilot code review | GitHub | paid Copilot + Actions minutes | Degraded without Actions (F10) | 2: no |
| PR-Agent CLI | local | own LLM key | Yes (F10) | 2: no |
| Mend Renovate app | Mend cloud | free | Yes (BELIEVED) (F11) | none |
| probot/settings app | Probot cloud | free | Yes (BELIEVED) (F11) | none |
| gh-dash | local TUI | free, MIT | Yes (F11) | 7: owner view only |
| `mergeable` / `mergeStateStatus` | GitHub | free | Yes; PR-vs-base only, lazy (F11) | 5: no |

## Gaps not closed
- G1. Mergify Open Source / Free plan eligibility for user-owned (non-organization) repositories: not stated on the
  pricing page; not tested (installing an app is an owner decision).
- G2. Whether a lingering systemd user manager keeps an idle WSL distro alive past `instanceIdleTimeout`; needs a
  one-night test on the owner's machine (enable linger, timer every 30 min, read `journalctl --user`).
- G3. Semantic issue search: 5 queries on one small repo, then a 403 on the semantic bucket. The dedup researcher
  should run it on the held-out split with paced calls before excluding it.
- G4. `gh issue develop` when the branch already exists remotely or locally (resume after a crash): not tested.
- G5. The owner's GitHub plan (`plan: null` from `gh api user`): Pro is inferred from private-repo branch protection.

## Sources fetched
- github/docs source files (2026): gated-features `merge-queue.md`, `auto-merge.md`, `repo-rules.md`,
  `protected-branches.md`; `managing-a-merge-queue.md`, `about-rulesets.md`, `available-rules-for-rulesets.md`,
  `using-the-built-in-automations.md`, `adding-items-automatically.md`, `about-issue-fields.md`,
  `managing-issue-types-in-an-organization.md`, `adding-sub-issues.md`, `creating-issue-dependencies.md`,
  `linking-a-pull-request-to-an-issue.md`, `creating-an-issue.md`, `marking-issues-or-pull-requests-as-a-duplicate.md`,
  `dependabot-on-actions.md`, Copilot `code-review.md`, `using-the-github-cli-to-forward-webhooks-for-testing.md`,
  `data/variables/projects.yml` (sub-issue limit 100, issue-type limit 25).
- Rendered GitHub Docs (docs.github.com article API): events, notifications, REST best practices, pull request reviews,
  releases, generated release notes, approving with required reviews.
- Vendor pages: Mergify pricing and docs (context7 `/websites/mergify`), Kodiak billing and quickstart, CodeRabbit
  pricing, Mend-hosted Renovate overview, git-cliff GitHub integration (context7), commitizen changelog, release-please
  `docs/cli.md`, cli/cli v2.98.0 and v2.99.0 release notes, systemd.timer and loginctl man pages, Microsoft Learn
  wsl-config.
- `gh api` repository health: chdsbd/kodiak, renovatebot/renovate, orhun/git-cliff, googleapis/release-please,
  commitizen-tools/commitizen, dlvhdr/gh-dash, seachicken/gh-poi, probot/settings, probot/stale, probot/no-response,
  cli/gh-webhook, qodo-ai/pr-agent.
- Not fetched: WebSearch for Kodiak pricing found nothing primary (the Kodiak docs page was used instead); a Mergify
  personal-account search returned only secondary sources, which are not cited.
