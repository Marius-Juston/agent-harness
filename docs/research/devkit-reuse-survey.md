# Research: established libraries and algorithms for devkit's hand-rolled matchers and parsers

Status: v1.1 (2026-11-01; research-auditor fixes from PR #427: held-out glob probes, loader versions, self-contained scrub arm, measurement commit). Issue: #416. Verdicts for (d) and (f) were posted early on [#229](https://github.com/Marius-Juston/agent-harness/issues/229#issuecomment-6552913444) and [#100](https://github.com/Marius-Juston/agent-harness/issues/100#issuecomment-6552913689), where builders were already working.
Labels: SOURCED = cited and fetched; VERIFIED = measured in this repo (script named, under [_scratch/devkit-reuse-survey/scripts/](_scratch/devkit-reuse-survey/scripts/)); BELIEVED = judgment; ASSUMED = unchecked premise.
Search coverage: `uv run dev lit status` on 2026-11-01 showed openalex, crossref, europepmc and arxiv up and s2 DOWN (HTTP 429). The later `dev lit search` call got answers from openalex and s2. The arXiv MCP timed out, so the one paper used was read through `dev lit get`. Also used: `uv run dev reuse check` (PyPI, 2026-11-01), the GitHub REST API, PyPI JSON, context7 (time-machine), raw upstream source files on GitHub, the Claude Code docs, and the Claude Code 2.1.316 binary installed on this machine.
Companions: [software-discovery.md](software-discovery.md) (the reuse gates G1-G6 used here) and [decision-records-and-drift.md](decision-records-and-drift.md) §5 (the `uv run dev` span replay that item (d) builds on). Item (c) overlaps with [free-security-scanning.md](free-security-scanning.md) (gitleaks in pre-commit).

## 0. Findings in one paragraph
Of the six hand-rolled pieces, only one should take a new runtime dependency. **(b) rule globs: adopt pathspec.** Claude Code 2.1.316 does not match a rule's `paths:` with picomatch. It uses the **node-ignore** library, which has gitignore semantics. Before matching, it splits comma-separated strings, expands braces and cuts a trailing `/**`. On 20 probe globs run through the node-ignore module extracted from the binary, devkit's `PurePosixPath.full_match` disagrees **10 times**. One example: `*.md` loads for `docs/a.md`, although the docs table says root only. pathspec's `GitIgnoreSpec` with case folding, behind the same preprocessing, disagrees **once** (a negation under an excluded parent). On 35 held-out probes written by the research-auditor, it disagrees **3 times**, against 16 for devkit. Those misses fall into two known classes: negation under an excluded parent, which a parent-directory check closes, and `[!a]` classes. The installed 2.1.310 and 2.1.314 have the same loader. pathspec passes the reuse gates with a G5 flag (MPL-2.0, weak copyleft, used unmodified) and has no required dependencies. **(a) install lint: use stdlib `ast`.** A statement-level pass (whole `ast.Call` segments plus constant folding, and joined shell continuations) gets **11 of 12** probes right where today's per-line regex gets 5. That is in-sample, a regression floor. It adds 1 hit over the whole tree (a test fixture). bashlex is GPLv3+ and dormant (1,383 days since its last release). Neither bashlex nor tree-sitter-bash can resolve `"$PM" install`, the one probe left. **(c) scrubbing: port gitleaks' prefix rules, adopt no tool.** `scrubbed()` redacts 7 of 18 synthetic credential forms. gitleaks catches 15. Adding nine prefix patterns, URL userinfo and a 20-character Bearer minimum brings `scrubbed()` to 17 with 0 of 10 benign lines flagged (in-sample; it covers those nine vendor formats only). The old `scrubbed()` flagged "Bearer of bad news". #422 shipped this as PR #428. detect-secrets is on HOLD (dormant 909 days), and the gitleaks binary is not on PATH in a rendered project. **(d) dev spans: no library.** devkit's own parser with no rules rejects **149 of 367** spans in the tree at `7f2ba80` (41 %). The loosened parser plus the five rules of #220 flags 12, all under `docs/research/`, and catches both planted bad spans. **(e) lock test: skip time-machine.** time-machine 3.0 stopped mocking `time.monotonic`, and it never reaches a subprocess, so it cannot drive `bounded_run`. **(f) PR titles: skip commitizen.** Its check *is* one regex, and it would bring 13 direct requirements. A ten-line regex agrees with it on 13 of 14 probes, and on the one disagreement the regex is stricter.

## 1. Question and scope
The owner asked, before v0.1.15, whether established libraries or algorithms would substantially help six pieces of devkit that reviewers found gaps in (#416). For each candidate, this note asks whether it closes the measured gap, what it costs every downstream project (a runtime dependency of `agent-harness` reaches every pinned project), and what its licence and maintenance look like. Each candidate gets a verdict to adopt or skip it. Where the reviewers gave probe cases, I ran the candidates on them. Where they did not, I wrote probe cases from the gap description. Every probe set is small and was written by me, so each rate below is in-sample.

## 2. Findings

### 2.1 (a) `check_installs`: per-line regex vs a statement-level pass (VERIFIED, [a_installs.py](_scratch/devkit-reuse-survey/scripts/a_installs.py))
- **The gap.** The PR #399 author recorded it under Unverified: "a list split across lines, or a command built from string pieces, gets past it" ([guards: catch blocked package installs run through a script or subprocess, PR #399](https://github.com/Marius-Juston/agent-harness/pull/399), 2026). SOURCED.
- **Measured on 12 probes:** 9 should flag, 1 is a reviewed hint string that both arms flag, and 2 are benign. The per-line `INSTALL` regex gets **5 of 12** right. It misses a list split over lines, `"pip " + "install x"`, implicit concatenation, a name bound to a list, an f-string with a name, and a shell `\` continuation. The stdlib prototype gets **11 of 12** right. It matches each `ast.Call` that touches an added line as one collapsed segment, and again with its strings folded (`+`, implicit concatenation, f-strings, names bound once to a str or list constant). For `.sh` files it joins backslash continuations first. VERIFIED. The 11 of 12 is **in-sample**: I wrote the prototype after writing the probes. Read it as a regression floor for the build, not as an estimate of recall on bypasses nobody has tried.
- **Residual:** `PM=pip; "$PM" install x`. Catching it needs shell evaluation, not parsing. A parser returns a command node whose name is an expansion. BELIEVED, from how bashlex and tree-sitter-bash represent expansions; neither was run here.
- **False-positive cost:** with every tracked `.py`/`.sh` line treated as added and the allowlist applied, the line regex has 0 hits and the prototype has 1. The extra hit is `tests/test_checks.py:768`, a fixture that hides `install` behind a constant on purpose. VERIFIED.
- **Candidates:**
  - **bashlex**: `reuse check` HOLD. GPLv3+, which is not Blue Oak rated, and dormant 1,383 days. Importing it into an MIT package that every project installs is a licence problem. VERIFIED (`uv run dev reuse check bashlex`, 2026-11-01).
  - **tree-sitter + tree-sitter-bash**: both PASS (MIT). That would be two compiled-wheel dependencies, and on these probes they close nothing that the 5-line continuation join does not. BELIEVED (not run).
- **Verdict: adopt the stdlib `ast` pass and skip the shell parsers.** It needs the new file's full text, not only `-U0` lines: read `root()/path` for each changed `.py` file and keep the calls whose line range intersects the added lines.

### 2.2 (b) rule `paths:` matching: what Claude Code actually does (VERIFIED from the 2.1.316 binary; [b_extract_ignore.py](_scratch/devkit-reuse-survey/scripts/b_extract_ignore.py), [b_globs.py](_scratch/devkit-reuse-survey/scripts/b_globs.py))
- **The documented behaviour.** The docs give the `paths:` examples, say brace expansion is bounded by a budget of 1,000 patterns and 4 MiB, say that `[` starts a bracket expression, and say `paths` "Accepts a YAML list or a comma-separated string". They describe `*.md` as "Markdown files in the project root". They do not name the matcher ([How Claude remembers your project](https://code.claude.com/docs/en/memory), Anthropic, Claude Code docs, fetched 2026-11-01). SOURCED. The PR #405 author believed the matcher was "picomatch-style" ([/gotcha: capture a one-line lesson into a path-scoped rules file, PR #405](https://github.com/Marius-Juston/agent-harness/pull/405), 2026). picomatch appears in the binary only in the `claudeMdExcludes` setting's description.
- **What the bundle does.** I read the bundled JavaScript of Claude Code 2.1.316 (`~/.local/share/claude/versions/2.1.316`).
  - The rule loader keeps a conditional rule when `<ignore>.default().add(<globs>).ignores(<relative path>)` is true.
  - The pattern validator's message reads "skipped by the ignore library (blank, comment, or trailing backslash)".
  - The module defines `ls="node-ignore"`, the marker of [node-ignore](https://github.com/kaelzhang/node-ignore) (kaelzhang, pushed 2026-10-23).
  - Before matching, the frontmatter parser splits strings at top-level commas, expands `{a,b}` within the documented budget, cuts a trailing `/**` (`src/**` becomes `src`), and treats a list of only `**` as unconditional.
  - At match time, the loader's `xst()` drops each pattern node-ignore cannot compile (its validator runs `add([p]).test("probe")` and logs the error) before the rest are added. So one bad pattern costs only itself, which matches the docs' "the rule's other patterns keep working". The reference runner applies the same filter ([b_ignore_run.js](_scratch/devkit-reuse-survey/scripts/b_ignore_run.js)).
  - VERIFIED. This is minified code. The installed 2.1.310 and 2.1.314 carry the same loader: all five markers are present in each binary, namely the `.ignores()` call on the rule globs, the `/**` cut, the validator message, the node-ignore module and the 1,000 / 4 MiB budget constants (VERIFIED, [b_versions.py](_scratch/devkit-reuse-survey/scripts/b_versions.py)). A later version may differ. `paths:` loading has regressed upstream before: rules with `paths:` were not loaded at all in 2.1.27 ([paths field in ~/.claude/rules/ frontmatter not working - rules with paths are not loaded, anthropics/claude-code#22170](https://github.com/anthropics/claude-code/issues/22170), 2026-01-31, closed). SOURCED.
- **Measured on 20 probes, with the extracted node-ignore module as ground truth:**

  | matcher | disagrees with Claude Code |
  |---|---|
  | devkit `rules.matches` (`full_match` + braces) | **10 / 20** |
  | pathspec `"gitignore"` spec + Claude Code's preprocessing | 3 / 20 |
  | pathspec `GitIgnoreSpec`, pattern and path casefolded, + preprocessing | **1 / 20** |

  VERIFIED.
- **devkit's 10 misses:**
  - `*.md` on `docs/a.md`: in gitignore semantics a pattern with no slash matches at any depth. The docs table is wrong on this point.
  - `"src/**, lib/**"` as a string: devkit reads it as one glob.
  - `*.{ts,tsx}` on `web/a.tsx`.
  - `/README.md` and `docs/` (anchors).
  - `photos \[2024/**` (the escape the docs recommend).
  - `.env*` at depth.
  - a bare directory `src`.
  - `src/*` on `src/a/b.ts`: a matched directory carries its contents.
  - `*.MD` on `a.md`: node-ignore ignores case by default.
- **The one pathspec miss:** `tests/**` plus `!tests/fixtures/**`. In node-ignore a file under an excluded parent cannot be re-included, so Claude Code loads the rule. pathspec re-includes the file.
- **Held out.** After reading v1 of this note, the research-auditor wrote 35 more probes (PR #427 audit). No arm was tuned on them, and they now sit in [b_globs.py](_scratch/devkit-reuse-survey/scripts/b_globs.py) as `HELDOUT`. Disagreements with Claude Code:

  | matcher | in-sample (20) | held-out (35) |
  |---|---|---|
  | devkit `rules.matches` | 10 | 16 |
  | pathspec `"gitignore"` + preprocessing | 3 | 6 |
  | `GitIgnoreSpec` + casefold + preprocessing | **1** | **3** |
  | the same + a parent-directory check | 0 | 1 |

  VERIFIED.
  - Two known classes account for all three held-out `GitIgnoreSpec` misses:
    1. **Negation under an excluded parent** (2 misses: `docs/**` + `!docs/keep.md`, and `src/` + `!src/a.py`). Treating a path as matched when any of its parent directories matches closes this class, which is git's own rule.
    2. **`[!a]` classes** (`src/[!a]*.ts` on `src/b.ts`, 1 miss). node-ignore does not match it, while pathspec reads `[!a]` as a negated class.
  - The parent-directory check was written after the auditor named the negation class. Its held-out 1 of 35 is therefore not a blind number.
  - The bound to carry forward is **1 of 20 in-sample and 3 of 35 held-out** for `GitIgnoreSpec` + casefold + preprocessing, with the two known classes above.
- **Alternatives considered and set aside:**
  - **A vendored Python port of node-ignore** (MIT on npm, `ignore` 7.0.14; the bundled module is about 4.4 kB minified). It would be exact by construction, but it is a hand-translated regex engine that devkit would own: the same maintenance trap this survey is meant to remove, with no upstream to follow. BELIEVED.
  - **`git check-ignore --no-index`** with the rule's patterns in a temporary exclude file. It adds no dependency and git's wildmatch has the same gitignore family of semantics. But it costs one subprocess per rule and check, its case handling follows `core.ignorecase` instead of node-ignore's always-ignore-case, and the patterns would have to go through a temporary file. BELIEVED (not run).
- **Why it matters.** `check_rules` fails a glob that "matches nothing" and `gotcha` picks the rules file by `matches()`. A glob devkit reads too narrowly is failed or skipped although Claude Code loads it. A glob devkit reads too widely passes although it never loads, which is the silent-rule drift #78 exists to catch. BELIEVED (consequence of the verified mismatch).
- **pathspec cost:** PASS with a G5 flag (MPL-2.0 is weak copyleft; used unmodified), Q 0.87, 70,767 dependents, last release 2026-04-27, MPL-2.0 (weak copyleft, fine when used unmodified), and no required dependencies (PyPI `requires_dist` lists only extras). VERIFIED (`reuse check`, PyPI JSON). It is in `uv.lock` today only through copier in the `dev` group, so it would be a **new runtime dependency** for every project. The issue's "already a transitive dep" holds for this repository's dev environment only. VERIFIED (`uv tree --invert --package pathspec`).
- **wcmatch** is PASS (MIT, depends on bracex) but has bash-glob semantics. Its `MATCHBASE` flag mimics basename matching, but it has no gitignore directory-contents rule. BELIEVED (not run).
- **Verdict: adopt pathspec `GitIgnoreSpec`** behind a 15-line copy of Claude Code's preprocessing: comma split, brace expansion with the 1,000-pattern budget, `/**` cut, dropping patterns that do not compile, and casefold. Add the parent-directory check for negations. Keep both probe tables as regression tests. Re-extract and re-run them when Claude Code's version moves, since `paths:` loading has regressed upstream before (#22170; Follow-ups).

### 2.3 (c) `scrubbed()`: secret redaction before posting fix-loop comments (VERIFIED, [c_scrub.py](_scratch/devkit-reuse-survey/scripts/c_scrub.py); SOURCED literature)
- **The gap.** No reviewer probe was recorded on [integrate: close the fix loop, PR #408](https://github.com/Marius-Juston/agent-harness/pull/408) (its tests cover session links and five token forms). So I built 18 synthetic credential forms from a seeded RNG (the script holds no secret-shaped literal, and gitleaks finds nothing in it) and 10 ordinary log lines.

  | | credential forms caught (18) | benign lines flagged (10) |
  |---|---|---|
  | `SECRET` at 7f2ba80 (when this note was written) | 7 | 1 |
  | proposed: that `SECRET` with a 20-character Bearer minimum, + 9 prefix patterns + URL userinfo | 17 | **0** |
  | `scrubbed()` at HEAD, after #422 shipped the proposal (PR #428) | 17 | 0 |
  | gitleaks (prek-built from v8.30.1, `gitleaks stdin`, default rules) | 15 | 0 |

  VERIFIED (re-run 2026-11-01 after rebasing on #428; the proposed arm is self-contained in the script, so its benign count is its own).
- **What 17/18 covers.** It measures coverage of the nine vendor formats chosen here (plus GitHub, `sk-`, AWS key ids, URL userinfo and Bearer), and nothing beyond them. Tokens from Discord, Twilio, Azure, DigitalOcean and the other vendors in gitleaks' 222 rules were not probed and are not covered.
- **What `scrubbed()` misses today:** Slack, Google API, PyPI, npm, GitLab, Hugging Face and Stripe tokens, PEM private keys, JWTs, `scheme://user:pass@` URLs, and an AWS secret key. The AWS secret key needs entropy (gitleaks `generic-api-key`) and the prefix set misses it too.
- **What gitleaks misses:** the `sk-proj-` key, the URL password, and the bearer header.
- **The benign false positive:** the 7f2ba80 pattern `(?<=[Bb]earer )[A-Za-z0-9._~+/=-]+` redacts "of" in "Bearer of bad news". With a 20-character minimum, as on the `token` alternative, the proposed arm flags 0 of 10 benign lines and still redacts the 40-character bearer probe.
- **The prefix patterns are fitted to these probes,** so 17/18 is an in-sample number.
- **Literature.** Basak et al. compared 9 tools on a benchmark. gitleaks had the best recall (88 %) at 46 % precision. False positives came from "generic regular expressions and ineffective entropy calculation", and false negatives from "faulty regular expressions ... and insufficient rulesets" ([A Comparative Study of Software Secrets Reporting by Secret Detection Tools](https://arxiv.org/abs/2307.00714), Basak, Cox, Reaves, Williams, 2023, ESEM). SOURCED. This supports porting gitleaks' vendor-prefix rules and leaving out its generic and entropy rules.
- **Candidates:**
  - **gitleaks** (MIT, pushed 2026-10-28, 30.5k stars) already runs in pre-commit. In a rendered project, prek builds the binary into its own cache, not onto PATH, so devkit cannot count on calling it. VERIFIED (binary found only under `~/.cache/prek/hooks/`).
  - **detect-secrets**: HOLD, dormant 909 days (Apache-2.0). VERIFIED (`reuse check`).
- **Verdict: skip both tools as dependencies; port the vendor-prefix rules.** Port the regexes of gitleaks' `slack-bot-token`, `gcp-api-key`, `pypi-upload-token`, `npm-access-token`, `gitlab-pat`, `huggingface-access-token`, `stripe-access-token`, `private-key` and `jwt` rules from its `config/gitleaks.toml` (MIT, attribution in a comment), add the URL-userinfo pattern, and set the Bearer minimum.

### 2.4 (d) #229 `uv run dev` spans: own parser alone vs loosened parser plus rules (VERIFIED, [d_dev_spans.py](_scratch/devkit-reuse-survey/scripts/d_dev_spans.py))
- **Measured** on 2026-11-01 at commit `7f2ba80` (main at the time; the working tree also held this note's untracked v0 skeleton, whose two spans are valid and sit under `docs/research/`). The input was the 367 backticked `uv run dev` spans in 300 tracked md/sh/yml/json/jinja files (generated dir skipped), plus 4 planted probes:

  | parser | spans flagged in the tree | outside `docs/research/` | planted bad positional / bad flag | planted good / placeholder |
  |---|---|---|---|---|
  | `cli.build()` as built, no rules | 149 / 367 (41 %) | not counted | flagged | **placeholder flagged** |
  | loosened only (rule 1) | 43 / 367 | 21 | flagged | silent |
  | loosened + five rules ([r2_cli_worker.py](_scratch/adr-drift/scripts/r2_cli_worker.py)) | 12 | **0** | flagged | silent |

  VERIFIED.
- **Where the strict parser's rejections come from.** It produces 85 "the following arguments are required" errors and 39 "invalid int value" errors from `<n>`/`N` placeholders. That is prose notation, not drift, so the syntax rules are what make the parse usable. No library parses usage notation (`<x>`, `a|b`, `...`) against an argparse tree. BELIEVED.
- **Two bugs in the r2 worker that #229 should not copy:**
  1. It scans for flags before it cuts the span at shell operators. So `uv run dev doctor && uv run dev test --affected` reports `--affected`, and `uv run dev check | grep -A1` reports `-A1`.
  2. `uv run dev board --help` comes out as a K5 error.

  VERIFIED (both appear in the 12 flags).
- **A simplification:** on Python 3.13.11, `ArgumentParser(exit_on_error=False)` raises `argparse.ArgumentError` for an invalid int, a missing required option, an invalid choice and an option missing its value. That can replace the stderr-redirect/`SystemExit` catch, and devkit requires Python >= 3.13. VERIFIED (a one-off run, quoted on #229).
- **Verdict: no library.** Keep the #220 design.

### 2.5 (e) #407 timing-sensitive lock test: injected clock vs time-machine (SOURCED; VERIFIED code read)
- **What time-machine can and cannot patch.** time-machine 3.0.0 (2025-11-18) "Remove[d] mocking of `time.monotonic()` and `time.monotonic_ns()`". It patches only the current interpreter's wall-clock functions (`time.time`, `datetime.now`, `clock_gettime(CLOCK_REALTIME)`, ...). The maintainers recommend `unittest.mock` on a directly imported `monotonic` instead ([time-machine changelog](https://github.com/adamchainz/time-machine/blob/main/docs/changelog.rst), Adam Johnson, via context7, 2026-11-01). SOURCED.
- **What the test exercises.** devkit's `bounded_run` times the gate with `time.monotonic()` and waits on a real subprocess holding a real lock (`devkit/workflow.py` lines 867-871). VERIFIED (code read).
- **Consequence.** time-machine cannot shorten or order that wait, and freezegun would not reach the subprocess either. BELIEVED.
- **Status.** [PR #420](https://github.com/Marius-Juston/agent-harness/pull/420) (open) already replaces the race with event synchronisation and a monotonic stamp written by the subprocess. Its author reports 50/50 full-suite runs. SOURCED (PR body; not re-run here).
- **Verdict: skip time-machine.** Inject a clock or synchronise on events, as PR #420 does.

### 2.6 (f) #100 Conventional Commits title check: commitizen vs a regex (VERIFIED, [f_cc_title.py](_scratch/devkit-reuse-survey/scripts/f_cc_title.py); SOURCED)
- **What commitizen checks.** commitizen's conventional check is `ConventionalCommitsCz.schema_pattern()`, a single regex over 12 types ([conventional_commits.py](https://github.com/commitizen-tools/commitizen/blob/master/commitizen/cz/conventional_commits/conventional_commits.py), commitizen-tools, master, fetched 2026-11-01). commitizen 4.19.3 has 13 direct requirements on Python 3.13 (questionary, prompt-toolkit, jinja2, tomlkit, argcomplete, decli, ...). It passes `reuse check` (MIT, Q 0.92). VERIFIED (PyPI JSON).
- **Measured.** Copied verbatim, that pattern and the proposed `(TYPES)(\([\w./-]+\))?!?: \S[^\n\r]*` (fullmatch) decide 13 of 14 probe titles alike. The probes cover the spec examples, a missing space, a space before the colon, a capitalised type, an unknown type, an empty scope and a scope with a space. They also decide all 133 first-parent titles of main alike (both accept 0, as expected before #100). On `fix:  (#12)`, commitizen accepts an empty subject and the regex refuses it. VERIFIED.
- **Spec and release-please facts that bound the design:**
  - SOURCED: Conventional Commits 1.0.0 says types "MUST NOT be treated as case-sensitive by implementors" (rule 15) ([Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/), conventional-commits, fetched 2026-11-01). Since `dev pr` writes the title, refusing `Fix:` is safe.
  - SOURCED: release-please's default strategy bumps major on breaking, minor on `feat`/`feature` and **patch on every other type** ([default.ts](https://github.com/googleapis/release-please/blob/main/src/versioning-strategies/default.ts), googleapis, main, fetched 2026-11-01). So `type:build` must map to `feat`, not to the Conventional Commits `build` type.
- **Verdict: skip commitizen; use a ten-line regex.**

## 3. Recommendation for this project
In build order for v0.1.15:

1. **(b) Adopt pathspec ≥ 1.1 as a runtime dependency** and match rules the way Claude Code 2.1.316 does: top-level comma split, brace expansion capped at 1,000 patterns, a trailing `/**` cut, dropping patterns that do not compile, `GitIgnoreSpec` with case folding, and a parent-directory check for negations. Bounds, measured without the parent check: at most 1 of 20 disagreements with the extracted node-ignore in-sample and 3 of 35 held-out. The known classes are negation under an excluded parent (closed by the parent check) and `[!a]` classes. Today's tree and a fresh render must raise 0 new rules-stage problems. This is the one item where the hand-rolled code is measurably wrong (10 of 20 probes), and the behaviour of the file it guards is undocumented. **P1, [#421](https://github.com/Marius-Juston/agent-harness/issues/421).**
2. **(c) Extend `workflow.SECRET`** with the nine gitleaks vendor-prefix rules and URL userinfo, and set a 20-character minimum after `Bearer `. Bounds: at least 17 of the 18 probes redacted and 0 of the 10 benign lines. No new dependency. **P1:** small, and a leak into a public PR comment cannot be taken back; [#422](https://github.com/Marius-Juston/agent-harness/issues/422), after #100 merges.
3. **(a) Statement-level install lint with stdlib `ast`** for `.py` files and continuation joining for shell. Bounds: at least 11 of 12 probes, which is in-sample and serves as a regression floor; at most 1 new hit over today's tree (allowlist the fixture or keep the obfuscation). No new dependency. **P2:** the agent rule already covers intent, and this closes a bypass that nobody has been seen using; [#423](https://github.com/Marius-Juston/agent-harness/issues/423), after #229 merges.
4. **(d)** No issue: #229 proceeds as specified, with the two worker bugs avoided (comment posted).
5. **(e)** No issue: PR #420.
6. **(f)** No issue: #100 proceeds with a regex and the `type:build → feat` mapping (comment posted).

No candidate here needs more than one new runtime dependency. pathspec is the only one, and it has no required dependencies of its own.

## 4. Limits
- Every probe set was written by me from the gap description and is small (12, 20, 18 + 10, 4, 14). The pattern sets proposed in (a) and (c) were written after seeing these probes, so their rates are in-sample.
- (b) rests on one Claude Code version's minified bundle. The bundle was read, not documented, and the docs table contradicts it on `*.md`.
- tree-sitter-bash and wcmatch were judged from their documented semantics, not run.

## Sources
- Anthropic. How Claude remembers your project (Claude Code docs, path-specific rules). Fetched 2026-11-01. https://code.claude.com/docs/en/memory
- Claude Code 2.1.316 binary (bundled JavaScript: rule loader, `paths:` parser, node-ignore module). Local install, read 2026-11-01 with [b_extract_ignore.py](_scratch/devkit-reuse-survey/scripts/b_extract_ignore.py).
- kaelzhang. node-ignore (npm `ignore` 7.0.14, MIT). GitHub repository, pushed 2026-10-23. https://github.com/kaelzhang/node-ignore
- anthropics/claude-code#22170. paths field in ~/.claude/rules/ frontmatter not working - rules with paths are not loaded. 2026-01-31, closed. https://github.com/anthropics/claude-code/issues/22170
- cpburnz. python-pathspec. GitHub repository. https://github.com/cpburnz/python-pathspec
- Basak, Cox, Reaves, Williams. A Comparative Study of Software Secrets Reporting by Secret Detection Tools. ESEM 2023. https://arxiv.org/abs/2307.00714 (abstract)
- gitleaks. GitHub repository, MIT, pushed 2026-10-28. https://github.com/gitleaks/gitleaks
- Yelp. detect-secrets. GitHub repository, Apache-2.0. https://github.com/Yelp/detect-secrets
- Adam Johnson. time-machine changelog (3.0.0). https://github.com/adamchainz/time-machine/blob/main/docs/changelog.rst
- commitizen-tools. commitizen, conventional_commits.py. https://github.com/commitizen-tools/commitizen/blob/master/commitizen/cz/conventional_commits/conventional_commits.py
- Conventional Commits 1.0.0 specification. https://www.conventionalcommits.org/en/v1.0.0/
- googleapis. release-please, src/versioning-strategies/default.ts. https://github.com/googleapis/release-please/blob/main/src/versioning-strategies/default.ts
- Marius-Juston/agent-harness PRs [#399](https://github.com/Marius-Juston/agent-harness/pull/399), [#405](https://github.com/Marius-Juston/agent-harness/pull/405), [#408](https://github.com/Marius-Juston/agent-harness/pull/408), [#420](https://github.com/Marius-Juston/agent-harness/pull/420).

## Follow-ups
- [ ] (b) rules match `paths:` with pathspec GitIgnoreSpec behind Claude Code's preprocessing ([#421](https://github.com/Marius-Juston/agent-harness/issues/421)) DONE WHEN the 20 in-sample and 35 held-out probes are tests with at most 1 and 3 disagreements (fewer with the parent-directory check), and a comma-string `paths:` and `*.md` at depth match.
- [ ] (c) SECRET gains gitleaks' vendor-prefix rules and a Bearer minimum ([#422](https://github.com/Marius-Juston/agent-harness/issues/422)) DONE WHEN at least 17 of the 18 synthetic forms are redacted and 0 of the 10 benign lines are touched (test).
- [ ] (a) check_installs matches whole `ast.Call` statements with folded constants and joined shell continuations ([#423](https://github.com/Marius-Juston/agent-harness/issues/423)) DONE WHEN at least 11 of the 12 probes are right and today's tree adds at most 1 hit.
- [ ] (b) re-run [b_versions.py](_scratch/devkit-reuse-survey/scripts/b_versions.py) and [b_globs.py](_scratch/devkit-reuse-survey/scripts/b_globs.py) (both probe sets) against each new Claude Code minor version, because `paths:` loading has regressed upstream before ([anthropics/claude-code#22170](https://github.com/anthropics/claude-code/issues/22170)) DONE WHEN the marker check and both tables are regenerated and any change in disagreement is filed.
