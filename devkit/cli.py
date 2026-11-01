"""`uv run dev <command>`: the single entry point for agents and humans. `uv run dev --help` is the command list."""

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

from devkit import (
    board,
    branches,
    checks,
    citecheck,
    code,
    config,
    consolidation,
    docs,
    evals,
    gate,
    handoff,
    lit,
    quota,
    reuse,
    rules,
    setup,
    stats,
    train,
    workflow,
)
from devkit.paths import pin_git_output, root
from devkit.suite_audit import due, file_report, last_snapshot, render, report

NOTE_TEMPLATE = """# Research: {title}

Status: v0 ({date}). Issue: #{issue}
Labels: SOURCED = cited and fetched; VERIFIED = measured in this repo (script named); BELIEVED = judgment; ASSUMED = unchecked premise.
Search coverage: (the `discovery` line `uv run dev lit search` printed first, with any sources down and the date)
Companions: (closest notes from INDEX.md)

## 0. Findings in one paragraph
(the answer first: what to do and why, with the key numbers)

## 1. Question and scope

## 2. Findings
(each finding: claim, label, source link)

## 3. Recommendation for this project

## Sources
(full citation + the URL you fetched; `uv run dev cite-check` verifies them)

## Follow-ups
- [ ] (next step) DONE WHEN (measurable condition)
"""


class Parser(argparse.ArgumentParser):
    """No prefix matching (`--fi` is not `--file`): guard_bash allowlists read-only agents' arguments, so it must see
    the ones argparse acts on. Subparsers are built with the parent's class, so every level inherits this."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)


def build() -> argparse.ArgumentParser:
    p = Parser(prog="dev", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    sub.add_parser("orient", help="session start: handoff, board, worktrees, gate, memory, quota (~60 lines)")
    sub.add_parser("handoff", help="rewrite the handoff file's in flight, just merged and next sections from the board")
    s = sub.add_parser("status", help="this machine: gate, agent worktrees, free memory, quota")
    s.add_argument("--json", action="store_true")
    s.add_argument(
        "--resume", action="store_true", help="per worktree with commits or edits: issue, PR, review, prompt"
    )
    s = sub.add_parser("worktree", help="local branches: prune those of merged or superseded PRs")
    ws = s.add_subparsers(dest="sub", required=True, metavar="<sub>")
    x = ws.add_parser("prune", help="delete local branches whose merged or closed PR's head contains their tip")
    x.add_argument("--dry-run", action="store_true", help="only list what would be deleted and what is kept, and why")
    s = sub.add_parser("check", help="the gate: drift checks, budgets, lint, tests (one per machine; cached per tree)")
    s.add_argument("--full", action="store_true", help="include slow tests")
    s.add_argument("--no-cache", action="store_true")
    s = sub.add_parser("test", help="pytest with workers scaled to free memory; --affected runs the change's tests")
    s.add_argument("paths", nargs="*")
    s.add_argument("--affected", action="store_true")
    s.add_argument("--slow", action="store_true")
    s.add_argument("-n", type=int, default=None)
    sub.add_parser("docs", help="regenerate the generated references and the research INDEX.md")

    c = sub.add_parser("code", help="navigate by symbol: outline | show | refs | find (capped output)")
    cs = c.add_subparsers(dest="sub", required=True)
    x = cs.add_parser("outline", help="signatures / headings of a file")
    x.add_argument("path")
    x = cs.add_parser("show", help="body of a function, class or Class.method")
    x.add_argument("symbol")
    x.add_argument("path", nargs="?")
    x.add_argument("--full", action="store_true")
    x = cs.add_parser("refs", help="who mentions a symbol, grouped by file, tests last")
    x.add_argument("symbol")
    x = cs.add_parser("find", help="regex search, capped per file")
    x.add_argument("pattern")
    x.add_argument("--notes", action="store_true", help="search docs/ only")
    x.add_argument("-i", action="store_true", dest="ignore_case")

    b = sub.add_parser(
        "board",
        help="work items (GitHub issues): list, next, new, claim, beat, block, release, sync, sweep, event, brief",
    )
    bs = b.add_subparsers(dest="sub", required=True)
    bs.add_parser("init", help="create the status/priority/type/area/stale labels in the GitHub repo")
    x = bs.add_parser("list", help="open items, one line each (STALE = in progress, no update for 6 h)")
    x.add_argument("--status", choices=board.STATUSES)
    x.add_argument("--type", dest="kind", choices=board.TYPES)
    x.add_argument("--json", action="store_true")
    x = bs.add_parser(
        "next", help="startable items: ready, blockers closed; by inherited priority, then how much they unblock"
    )
    x.add_argument("--type", dest="kind", choices=board.TYPES)
    x.add_argument("-n", type=int, default=5)
    x = bs.add_parser("wave", help="up to N startable items whose Owns overlap neither each other nor work in progress")
    x.add_argument("n", type=int)
    x = bs.add_parser("lint", help="conventions every open item meets (labels, title, DONE WHEN, Owns, links)")
    x.add_argument("--fix", action="store_true", help="make the label edits that follow from each item")
    x = bs.add_parser("new", help="create an item with a measurable DONE WHEN")
    x.add_argument("--title", required=True)
    x.add_argument("--type", dest="kind", choices=board.TYPES, required=True)
    x.add_argument("--prio", choices=board.PRIORITIES, default="P2")
    x.add_argument("--context", default="")
    x.add_argument("--done", action="append", default=[], help="a measurable completion condition (repeat)")
    x.add_argument("--owns", action="append", default=[], help="path or glob this item may change (repeat)")
    ref = "12, #12, owner/repo#12 or an issue URL"
    x.add_argument("--blocked-by", action="append", default=[], help=f"a native blocked-by link (repeat): {ref}")
    x.add_argument("--refs", action="append", default=[], help="a mention, not a link (relates-to)")
    x.add_argument("--parent", help=f"makes the item a native sub-issue of: {ref}")
    x = bs.add_parser("link", help="add native links to an item; a blocked-by link that closes a cycle is refused")
    x.add_argument("number", type=int)
    x.add_argument("--blocked-by", action="append", default=[], help=f"(repeat) {ref}")
    x.add_argument("--parent", help=f"replaces any parent: {ref}")
    x = bs.add_parser("claim", help="claim an item for an agent (race-safe across machines)")
    x.add_argument("number", type=int)
    x.add_argument("--agent", required=True)
    x.add_argument("--branch", default="")
    x.add_argument("--force", action="store_true", help="take over a stale claim")
    x = bs.add_parser("beat", help="heartbeat / milestone comment on an item")
    x.add_argument("number", type=int)
    x.add_argument("message")
    x = bs.add_parser("block", help="mark blocked with a reason")
    x.add_argument("number", type=int)
    x.add_argument("--reason", required=True)
    x = bs.add_parser("release", help="drop a claim, back to ready")
    x.add_argument("number", type=int)
    x.add_argument("--reason", default="released")
    bs.add_parser("sync", help="move items between blocked and ready from their declared blockers")
    x = bs.add_parser("sweep", help="label and comment on claimed in-progress items with no comment for --hours")
    x.add_argument("--hours", type=float, default=board.STALE_H)
    x.add_argument("--issue", type=int, action="append", help="sweep only this item (repeat)")
    x = bs.add_parser(
        "event", help="CI, scheduled: sync blockers, then sweep stale claims (GITHUB_EVENT_NAME, GITHUB_EVENT_PATH)"
    )
    x.add_argument("--name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    x.add_argument("--payload", type=Path, default=os.environ.get("GITHUB_EVENT_PATH"))
    x = bs.add_parser("brief", help="everything an agent needs to start an item (spawn prompts stay one line)")
    x.add_argument("number", type=int)
    x = bs.add_parser("feedback", help="a PR's comments and reviews since its last commit, and its unresolved threads")
    x.add_argument("pr", type=int)

    x = sub.add_parser("pr", help="worker: push the gated branch, open the PR, move the item to review")
    x.add_argument("number", type=int)
    x.add_argument(
        "--summary",
        help="markdown file, or - for stdin (heredoc); without a 'Closes #N' or 'Refs #N' first line it gets 'Closes #N'",
    )
    x.add_argument("--draft", action="store_true")
    x.add_argument("--partial", action="store_true", help="some DONE WHEN item is not met: 'Refs #N', not 'Closes #N'")
    x = sub.add_parser("pr-title", help="CI: fail unless the PR title is Conventional Commits (title from PR_TITLE)")
    x.add_argument("--title", default=os.environ.get("PR_TITLE"), help="default: the PR_TITLE environment variable")
    x = sub.add_parser("integrate", help="lead: re-gate a PR merged onto main in a throwaway worktree, then merge it")
    x.add_argument("pr", type=int)
    x.add_argument("more", type=int, nargs="*", metavar="PR", help="with --train: the other members, in priority order")
    x.add_argument(
        "--train",
        type=int,
        metavar="N",
        help="merge N ready PRs (pr, then the others, in priority order) with one gate; bisect on failure",
    )
    x.add_argument(
        "--harness-ok",
        type=int,
        nargs="*",
        metavar="PR",
        help="you read the diff of harness files it touches; with --train, name each PR whose diff you read",
    )
    x.add_argument("--dry-run", action="store_true")
    x.add_argument(
        "--override", metavar="REASON", help="merge despite unfinished PR feedback; the reason is logged on the PR"
    )
    x.add_argument(
        "--local-ci",
        action="store_true",
        help="no Actions minutes: required checks Actions does not run are run locally and posted as statuses",
    )
    x.add_argument(
        "--consolidation-review",
        type=int,
        metavar="EPIC",
        help="the merge commit records a consolidation review (Consolidation-Review: #EPIC), resetting its trigger",
    )
    x.add_argument(
        "--why",
        action="store_true",
        help="only print the PR's last integrate outcome and whether its head moved since: no gate, no merge, no write",
    )

    x = sub.add_parser(
        "cite-check", help="resolve every source in research notes; flag dead links and title mismatches"
    )
    x.add_argument("paths", nargs="*", help="notes (default: notes changed vs main)")
    x.add_argument("--offline-ok", action="store_true")
    lt = sub.add_parser(
        "lit",
        help="literature search shared by all agents: rate-limited, cached, compact (OpenAlex, S2, Crossref, Europe PMC, arXiv)",
    )
    ls = lt.add_subparsers(dest="sub", required=True)
    x = ls.add_parser("search", help="papers for a query: title, year, venue, cites, ids, abstract snippet")
    x.add_argument("query")
    x.add_argument(
        "--also", action="append", default=[], help="a paraphrase of the query (repeatable): results merged, deduped"
    )
    x.add_argument(
        "--source",
        default="auto",
        help=f"comma list of {', '.join(lit.SOURCES)}, or all; auto (default) asks {', '.join(lit.DEFAULT_SOURCES)}, "
        f"then {', '.join(lit.FALLBACK_SOURCES)} in turn until it has -n papers",
    )
    x.add_argument("-n", type=int, default=lit.DEFAULT_N, help=f"results (max {lit.MAX_N})")
    x.add_argument("--years", default="", help="2019, 2015-, -2020 or 2015-2020")
    x.add_argument("--snippet", type=int, default=lit.SNIPPET, help="abstract characters per paper (0 = none)")
    ls.add_parser(
        "status",
        help="each source's health now (live probe); exits 1 only when every default source is down: "
        "run before a research round",
    )
    x = ls.add_parser(
        "get",
        help="papers by DOI, arXiv id, OpenAlex W-id or S2 id: authors, open-access link, full abstract; "
        "several ids batch into one OpenAlex call",
    )
    x.add_argument("id", nargs="+")
    x.add_argument("--snippet", type=int, default=None, help="abstract characters (default: all; 0 = none)")
    for name, what in (("refs", "the works it cites (backward)"), ("cited-by", "the works citing it (forward)")):
        x = ls.add_parser(
            name, help=f"citation chasing through OpenAlex: {what}, most cited first, compact lines like search"
        )
        x.add_argument("id", help="a DOI, arXiv id or OpenAlex W-id")
        x.add_argument("-n", type=int, default=lit.DEFAULT_N, help=f"results (max {lit.MAX_N})")
        x.add_argument("--years", default="", help="2019, 2015-, -2020 or 2015-2020")
        x.add_argument("--snippet", type=int, default=lit.SNIPPET, help="abstract characters per paper (0 = none)")
    x = sub.add_parser(
        "reuse",
        help="reuse before build: `check NAME...` verifies packages you name (exists, gates G1-G6, maintenance); "
        '`"<need>"` searches for more (GitHub, ecosyste.ms, deps.dev; shares dev lit\'s cache and rate limits)',
        # one parser, two forms: `check` is read from the first word, so the usage names both (#179)
        usage=f"%(prog)s check NAME... [--ecosystem {{{','.join(reuse.ECOS)}}}] [--json] [--allow-copyleft]\n"
        f'       %(prog)s "<need>" [--also NEED] [--from FILE[:SYMBOL]] [--ecosystem {{{",".join([*reuse.ECOS, "any"])}}}]'
        " [-n N] [--json] [--allow-copyleft]",
        description="check NAME...: verify the packages you name first; exits 1 unless every name passes or holds. "
        '"<need>": search for more options only when you know no good candidate.',
    )
    x.add_argument(
        "need",
        nargs="*",
        default=[],
        help="what the code must do, in a few words (one quoted argument); or `check` and package names",
    )
    x.add_argument("--also", action="append", default=[], help="a paraphrase of the need (repeatable): better recall")
    x.add_argument(
        "--from",
        dest="from_",
        metavar="FILE[:SYMBOL]",
        help="build the need from code: name, first docstring sentence, third-party imports",
    )
    x.add_argument("--ecosystem", default="pypi", choices=[*reuse.ECOS, "any"])
    x.add_argument("-n", type=int, default=reuse.DEFAULT_N, help=f"candidates (max {reuse.MAX_N})")
    x.add_argument("--json", action="store_true", help="JSON to paste into a research note")
    x.add_argument("--allow-copyleft", action="store_true", help="the owner allows strong copyleft (gate G5)")
    r = sub.add_parser("research", help="research notes: new")
    rs = r.add_subparsers(dest="sub", required=True)
    x = rs.add_parser("new", help="create <research_dir>/<slug>.md from the house template")
    x.add_argument("slug")
    x.add_argument("--title", required=True)
    x.add_argument("--issue", default="?")
    x = sub.add_parser("gotcha", help="append a one-line lesson to the .claude/rules file scoped to a path")
    x.add_argument("path", help="the file or directory the lesson is about")
    x.add_argument("lesson", nargs="+")
    x.add_argument("--issue", type=int, help="provenance issue (default: the branch's <n>-)")
    x.add_argument("--checkable", action="store_true", help="a test or hook can catch it: print the issue to file")

    s = sub.add_parser("setup", help="once per machine/clone: uv sync, git config, plugin install, then doctor")
    s.add_argument(
        "--repin-all",
        action="store_true",
        help="re-pin a skewed plugin by removing its marketplace in every scope: uninstalls it from every other "
        "project and the user scope on this machine and deletes its data, options and secrets",
    )
    sub.add_parser("doctor", help="read-only: what this machine still needs")
    x = sub.add_parser(
        "usage-poll", help="record the weekly usage percent every 10 min for the spawn guard (detach it)"
    )
    x.add_argument("--once", action="store_true", help="one reading, then exit")
    x = sub.add_parser(
        "quota-cap",
        help="the spawn guard's weekly cap and its source; owner only: `! uv run dev quota-cap <0-100>` sets a "
        "machine-local override (never committed), --clear drops it",
    )
    x.add_argument("pct", nargs="?", help="new cap percent, 0-100")
    x.add_argument("--clear", action="store_true", help="drop the override: back to [tool.devkit] quota_cap")
    x = sub.add_parser("eval", help="micro-scenario evals of the agent surface via claude -p in a remote-less clone")
    x.add_argument("names", nargs="*", help="scenario names (default: all in the evals dir)")
    x.add_argument("--trials", type=int, default=3)
    x.add_argument(
        "--model", default="", help="run every trial on this model; a trial whose transcript shows another fails"
    )
    x.add_argument(
        "--effort", default="", choices=["", *evals.EFFORTS], help="effort for every trial (recorded, not verified)"
    )
    x.add_argument("--agent", default="", help="run every scenario as this agent instead of its own")
    x.add_argument("--label", default="", help="name this run's record so --compare can find it")
    x.add_argument("--compare", nargs=2, metavar=("A", "B"), help="paired B-A deltas of two run records, no trials run")
    x.add_argument("--margin", type=float, default=0.05, help="non-inferiority margin of --compare (pass rate)")
    x = sub.add_parser(
        "consolidation", help="architecture review: is one due (orient shows it), and the report the architect reads"
    )
    xs = x.add_subparsers(dest="sub", required=True)
    y = xs.add_parser("status", help="is a review due since the last Consolidation-Review: trailer, and why (fast)")
    y.add_argument("--json", action="store_true")
    y = xs.add_parser(
        "report", help="read-only measurements: smells, growth by directory, command overlap, parse errors"
    )
    y.add_argument("--json", action="store_true")
    y.add_argument("--tools", action="store_true", help="also run pinned pyscn and vulture on a git-archive copy")
    x = sub.add_parser("agent-stats", help="scorecard from transcripts: tokens, calls, big-result share per agent type")
    x.add_argument("--json", action="store_true")
    g = x.add_mutually_exclusive_group()
    g.add_argument("--cost", dest="mode", action="store_const", const="cost", help="$ by token kind per type and issue")
    g.add_argument("--reviews", dest="mode", action="store_const", const="reviews", help="review rounds per role")
    g.add_argument(
        "--misses", dest="mode", action="store_const", const="misses", help="candidate post-merge defect pairs"
    )
    x.add_argument("--since", default="", help="YYYY-MM-DD: transcripts started and PRs merged on or after")
    x.add_argument("--until", default="", help="YYYY-MM-DD: transcripts started and PRs merged on or before")
    x = sub.add_parser("suite-audit", help="size-tiered audit of the whole test suite: is one due, and the nine checks")
    xs = x.add_subparsers(dest="sub", required=True)
    y = xs.add_parser("status", help="is an audit due since the last one (what orient shows), and why")
    y.add_argument("--json", action="store_true")
    y = xs.add_parser("report", help="tier, the nine checks and the judgement items for harness-ops:suite-auditor")
    y.add_argument("--json", action="store_true")
    y.add_argument("--file", action="store_true", help="keep one issue per failing check; record the audit")
    y.add_argument("--quick", action="store_true", help="no suite runs: history, git and gh only")
    y.add_argument(
        "--reruns",
        type=int,
        metavar="K",
        help="seeds for fresh-clone reruns: whole suite at tier S, history suspects above (default: 5 or 10)",
    )
    return p


def command_map() -> dict[str, set[str]]:
    parser = build()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    out: dict[str, set[str]] = {}
    for name, sp in action.choices.items():
        subs = [a for a in sp._actions if isinstance(a, argparse._SubParsersAction)]
        out[name] = set(subs[0].choices) if subs else set()
    return out


def command_help() -> list[tuple[str, str]]:
    parser = build()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    rows = []
    for choice in action._choices_actions:
        rows.append((choice.dest, choice.help or ""))
        sp = action.choices[choice.dest]
        for sub in [a for a in sp._actions if isinstance(a, argparse._SubParsersAction)]:
            rows += [(f"{choice.dest} {c.dest}", c.help or "") for c in sub._choices_actions]
    return rows


_NOT_NOTES = {"INDEX.md", "README.md"}


def _changed_notes() -> list[Path]:
    base = checks.merge_base()
    notes = f"{config.get().research_dir}/*.md"
    out = checks.git("diff", "--name-only", base or "HEAD", "--", notes, check=False).splitlines()
    out += checks.git("ls-files", "--others", "--exclude-standard", "--", notes).splitlines()
    # INDEX.md is generated and README.md ships with the template: neither is a note with citations to check.
    return [root() / f for f in sorted(set(out)) if (root() / f).exists() and Path(f).name not in _NOT_NOTES]


def run_gotcha(a: argparse.Namespace) -> int:
    lesson = " ".join(a.lesson)
    issue = a.issue or rules.issue_from_branch(checks.git("branch", "--show-current", check=False).strip())
    if issue is None:
        print("no issue number in the branch name: pass --issue N")
        return 1
    if a.checkable:
        print("a check holds where a rule only advises; file this instead of a rule:")
        print(rules.checkable_issue(a.path, lesson, issue))
        return 0
    try:
        path, wrote = rules.add(a.path, lesson, issue, date.today())
    except ValueError as exc:
        print(f"not written: {exc}")
        return 1
    print(f"{'added to' if wrote else 'already in'} {path.relative_to(root())}")
    return 0


def run(a: argparse.Namespace) -> int:
    match a.cmd:
        case "orient":
            print(workflow.orient())
        case "handoff":
            print(handoff.refresh())
        case "status":
            print(workflow.status(a.json, a.resume))
        case "worktree":
            print("\n".join(branches.prune_branches(a.dry_run)) or "no local branches of merged or closed PRs")
        case "check":
            return gate.check(command_map(), command_help(), full=a.full, use_cache=not a.no_cache, parser=build)
        case "test":
            return gate.test(a.paths, a.affected, a.slow, a.n)
        case "docs":
            changed = docs.write(command_help())
            print("updated: " + ", ".join(changed) if changed else "generated docs already current")
        case "code":
            print(
                {
                    "outline": lambda: code.outline(a.path),
                    "show": lambda: code.show(a.symbol, a.path, a.full),
                    "refs": lambda: code.refs(a.symbol),
                    "find": lambda: code.find(a.pattern, a.notes, a.ignore_case),
                }[a.sub]()
            )
        case "board":
            return run_board(a)
        case "pr":
            print(workflow.pr(a.number, a.summary, a.draft, a.partial))
        case "pr-title":
            return workflow.check_title(a.title)
        case "integrate" if a.why:  # read-only, whatever other flags came with it
            return workflow.why(a.pr)
        case "integrate" if a.train is not None or a.more:
            members = [a.pr, *a.more]
            if a.train != len(members) or a.override or a.consolidation_review:
                print(
                    f"integrate --train N takes exactly N PRs (got --train {a.train} and {len(members)}), "
                    "and neither --override nor --consolidation-review"
                )
                return 2
            if a.harness_ok == []:
                print("integrate --train: name the PRs whose harness diff you read: --harness-ok 12 15")
                return 2
            return train.train(members, set(a.harness_ok or []), a.dry_run, a.local_ci)
        case "integrate":
            harness_ok = a.harness_ok is not None and (not a.harness_ok or a.pr in a.harness_ok)
            return workflow.integrate(a.pr, harness_ok, a.dry_run, a.override, a.local_ci, a.consolidation_review)
        case "cite-check":
            paths = [Path(p) for p in a.paths] or _changed_notes()
            if not paths:
                print("no research notes changed (pass paths to check others)")
                return 0
            n, problems, notes = citecheck.check_notes(paths, a.offline_ok)
            for line in problems:
                print(f"FAIL {line}")
            for line in notes:
                print(f"note {line}")
            print(f"RESULT: {n} citations in {len(paths)} note(s), {len(problems)} problem(s)")
            return 1 if problems or n == 0 else 0
        case "lit":
            return run_lit(a)
        case "reuse":
            return run_reuse(a)
        case "research":
            path = root() / config.get().research_dir / f"{a.slug}.md"
            if path.exists():
                print(f"{path} exists: extend it instead")
                return 1
            path.write_text(NOTE_TEMPLATE.format(title=a.title, date=time.strftime("%Y-%m-%d"), issue=a.issue))
            print(f"created {path.relative_to(root())}")
        case "gotcha":
            return run_gotcha(a)
        case "setup":
            return setup.setup(repin_all=a.repin_all)
        case "doctor":
            return setup.doctor()
        case "usage-poll":
            return quota.usage_poll(once=a.once)
        case "quota-cap":
            return run_quota_cap(a)
        case "suite-audit":
            return run_suite_audit(a)
        case "eval":
            if a.compare:
                return evals.run_compare(*a.compare, a.margin)
            return evals.run(a.names, a.trials, a.model, a.effort, a.agent, a.label)
        case "agent-stats":
            print(stats.run(a.mode, a.since, a.until, a.json) if a.mode else stats.report(a.json))
        case "consolidation":
            return run_consolidation(a)
    return 0


def run_quota_cap(a: argparse.Namespace) -> int:
    if a.pct is not None and a.clear:
        print("error: give a percent or --clear, not both", file=sys.stderr)
        return 1
    if a.pct is not None:
        try:
            quota.set_quota_cap(config.parse_cap(a.pct))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    elif a.clear:
        quota.set_quota_cap(None)
    cap, source = quota.quota_cap()
    print(f"quota cap {cap}% ({quota.CAP_SOURCES[source]})")
    return 0


def run_consolidation(a: argparse.Namespace) -> int:
    if a.sub == "status":
        r = consolidation.due(root())
        for k in ("head", "at_base"):
            r[k].pop("loc")
        why = "; ".join(r["why"])
        print(json.dumps(r, indent=1) if a.json else f"review due: {'yes: ' + why if r['review_due'] else 'no'}")
        return 0
    names = {name for top, subs in command_map().items() for name in (top, *subs)}
    r = consolidation.report(root(), names, tools=a.tools)
    if n := r["parse_errors"]["count"]:
        print(f"warning: {n} Python file(s) failed to parse; see the report's parse errors", file=sys.stderr)
    print(json.dumps(r, indent=1) if a.json else consolidation.render(r))
    return 0


def run_lit(a: argparse.Namespace) -> int:
    if a.sub == "status":
        health = lit.client().status()
        print(lit.render_status(health))
        return 1 if lit.degraded(health) else 0
    try:
        if a.sub == "search":
            sources, fallback = lit.plan(a.source)
            c = lit.client()
            papers, notes, health = c.search_coverage(
                a.query, sources, a.n, a.years, fallback=fallback, also=tuple(a.also)
            )
            print(lit.coverage_line(health, c.clock()))
            print(lit.render(papers, notes, a.snippet))
            if lit.degraded(health) or not any(h.ok for h in health):
                return 1
            return 0 if papers or not notes else 1
        elif a.sub in lit.CHASE:
            papers, notes = lit.client().chase(a.id, a.sub, a.n, a.years)
        else:
            found, notes = lit.client().lookup_many(a.id)
            print(lit.render_full([p for p in found if p], notes, a.snippet))
            return 0 if all(found) else 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(lit.render(papers, notes, a.snippet))
    return 0 if papers or not notes else 1


def run_reuse(a: argparse.Namespace) -> int:
    if a.need[:1] == ["check"]:
        if not a.need[1:] or a.ecosystem == "any":
            print("error: dev reuse check takes package names and --ecosystem pypi, npm or cargo", file=sys.stderr)
            return 2
        report = reuse.check(reuse.client(), a.need[1:], a.ecosystem, a.allow_copyleft)
        print(reuse.to_json(report) if a.json else reuse.render(report))
        return 0 if all(c.status in (reuse.PASS, reuse.HOLD) for c in report["candidates"]) else 1
    if len(a.need) > 1:
        print('error: quote the need as one argument: dev reuse "<need>"', file=sys.stderr)
        return 2
    queries = [q for q in (*a.need, *a.also) if q.strip()]
    if a.from_:
        try:
            queries.append(reuse.from_spec(a.from_))
        except LookupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if not queries:
        print("error: give a need, --also or --from", file=sys.stderr)
        return 2
    encoder, why = reuse.load_encoder()
    report = reuse.search(reuse.client(), queries, a.ecosystem, a.n, encoder, why, a.allow_copyleft)
    print(reuse.to_json(report) if a.json else reuse.render(report))
    return 0 if report["candidates"] or not report["notes"] else 1


def run_board(a: argparse.Namespace) -> int:
    match a.sub:
        case "init":
            made = board.ensure_labels()
            print(f"created labels: {', '.join(made)}" if made else "labels already present")
        case "list":
            print(board.listing(a.status, a.kind, a.json))
        case "next":
            print(board.next_items(a.kind, a.n))
        case "wave":
            print(board.wave_items(a.n))
        case "lint":
            out, code = board.lint_board(a.fix)
            print(out)
            return code
        case "new":
            print(board.new(a.title, a.kind, a.prio, a.context, a.done, a.owns, a.blocked_by, a.refs, a.parent))
        case "link":
            print("\n".join(board.link(a.number, a.blocked_by, a.parent)) or "already linked")
        case "claim":
            print(board.claim(a.number, a.agent, a.branch, a.force))
        case "beat":
            workflow.heartbeat(a.number, a.message)
            print(f"#{a.number}: heartbeat posted")
        case "block":
            board.block(a.number, a.reason)
            print(f"#{a.number} -> blocked")
        case "release":
            board.release(a.number, a.reason)
            print(f"#{a.number} -> ready")
        case "sync":
            print("\n".join(board.sync()) or "nothing to move")
        case "sweep":
            print("\n".join(board.sweep(a.hours, only=a.issue)) or "nothing stale")
        case "event":
            if not a.name or not a.payload:
                raise board.BoardError("board event needs --name and --payload (set by GitHub Actions)")
            print("\n".join(board.event(a.name, json.loads(Path(a.payload).read_text()))) or "nothing to move")
        case "brief":
            print(board.brief(a.number))
        case "feedback":
            print(
                "\n".join(board.pr_feedback_lines(board.pr_feedback(a.pr))) or "no new comments, no unresolved threads"
            )
    return 0


def run_suite_audit(a: argparse.Namespace) -> int:
    if a.sub == "status":
        d = due(root(), last_snapshot()[1])
        print(
            json.dumps(d, indent=1) if a.json else f"audit due: {'yes: ' + '; '.join(d['why']) if d['due'] else 'no'}"
        )
        return 0
    r = report(root(), reruns=a.reruns, quick=a.quick)
    print(json.dumps(r, indent=1) if a.json else render(r))
    if a.file:
        print("\n".join(file_report(r)))
    return 0


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    """build()'s arguments. `dev reuse check --json a b`: argparse ends the `need` list at the first option, so the
    names after it come back unrecognised; they are names too (#175). Any other leftover is still an error."""
    p = build()
    args, extra = p.parse_known_args(argv)
    if extra and args.cmd == "reuse" and not any(e.startswith("-") for e in extra):
        args.need += extra
    elif extra:
        p.error(f"unrecognized arguments: {' '.join(extra)}")
    return args


def main() -> None:
    os.environ.update(pin_git_output(os.environ))  # every git devkit starts prints what it parses (paths.GIT_PINNED)
    args = parse()
    try:
        sys.exit(run(args))
    except (board.BoardError, config.ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except BrokenPipeError:
        sys.exit(0)
