# Fit-check evaluation protocol (#196; pre-registered 2026-10-13, before any arm run)

Question: after an agent has chosen a library (and `dev reuse check` has passed it), what is the cheapest reliable
free way to check that the library actually does the need? This follows the #169 protocol
([../PROTOCOL.md](../PROTOCOL.md)): frozen cases, fixed metrics and decision rule, TEST run once per arm.

## Cases
1. **TEST** = [data/cases_test.json](data/cases_test.json), 44 (package, need) pairs written by the researcher
   (claude-opus-5-5) for this eval: 23 FIT and 21 NOT_FIT. The NOT_FIT pairs are plausible wrong-capability picks in
   six classes: read-only or write-only (tomli, toml, pyyaml, xlrd, reportlab for extraction), wrong direction (markdown
   for HTML to Markdown, pyzbar for making QR codes), wrong format (orjson, ujson, simplejson, lz4), adjacent but not it
   (schedule, python-dateutil twice, pydantic, requests, html5lib, markupsafe), version drift (xlrd 2 dropped .xlsx,
   aioredis does not import on 3.13), and a run-time download the need rules out (nltk punkt, tiktoken encodings).
   Needs are testable offline; packages are installed as `uv add <package>`, no extras, CPython 3.13, Linux x86_64.
2. **Gold** is VERIFIED per case by a script written by the researcher before any arm ran ([gold/](gold/), `.txt`
   because they are sandbox inputs, not project code) and run in the same sandbox: [data/gold_results.json](data/gold_results.json)
   from [scripts/gold_run.py](scripts/gold_run.py). All 44 agree with their labels.
3. **Exclusion, decided before any arm run:** t03 (toml) is excluded. toml 0.10.2's `TomlPreserveCommentDecoder` keeps
   inline comments but raises `KeyError` on a top-level comment line, so neither label is clean. Scored: 43 cases,
   23 FIT and 20 NOT_FIT.
4. **PILOT** = [data/cases_pilot.json](data/cases_pilot.json), 6 cases, used only to shake down the harness. Prompts and
   scripts may change after the pilot only to fix a harness bug, and every change is logged below with new hashes.

## Arms, in cost order (one fresh tool-less `claude -p --model claude-opus-5-5` call per case, empty directory)
- **F0 memory:** the shared header ([prompts/common.md](prompts/common.md)) plus [prompts/f0.md](prompts/f0.md).
  JSON verdict.
- **F1 PyPI text:** F0's question plus the package's PyPI name, version, summary, requires_python and the first
  12,000 characters of its description, from a frozen snapshot ([prompts/f1.md](prompts/f1.md),
  [scripts/snapshot.py](scripts/snapshot.py)). The snapshot holds third-party README text, so it stays outside git;
  SHA-256 `a7c6639d7ed29578927a2cbf8d82219f57eda8d9b4403d8e94e8074b259f72fc`. Its versions match the installed ones
  for all 47 packages ([data/versions.json](data/versions.json)).
- **F2 context7 docs:** declared but **not runnable**. The keyless quota is spent until 2026-11-01: the REST API and
  the MCP tool both answered `Monthly quota exceeded` on 2026-10-13. It is not replaced by a stand-in.
- **F3 sandbox run:** the agent writes a script of at most 40 lines ([prompts/f3.md](prompts/f3.md)); the harness
  installs the package with `uv sync --no-build` into a throwaway environment (wheels only, so no package code runs
  at install) and runs the script under bubblewrap with no network, a read-only file system except a tmpfs `/tmp`,
  a cleared environment and a 60 s limit ([scripts/sandbox.py](scripts/sandbox.py)). FIT if and only if it exits 0
  and its last stdout line is `FIT_OK`.
- **F3r sandbox run with one repair:** F3, plus one more call after a failure that shows the script and its output
  ([prompts/f3r.md](prompts/f3r.md)); the agent answers `NOT_FIT: reason` or a corrected script, which runs once.
- **Exploratory, never used in the decision:** OR-combinations (NOT_FIT if either arm says so), computed from the
  same outputs by [scripts/score.py](scripts/score.py).

## Metrics and decision rule (fixed now)
5. Primary: accuracy over the 43 scored cases. Secondary: catch rate (NOT_FIT gold judged NOT_FIT), false-alarm rate
   (FIT gold judged NOT_FIT), UNPARSED count, calls, tokens and seconds per case, and install time and disk per
   package ([data/installs.json](data/installs.json), [scripts/install_all.py](scripts/install_all.py)).
6. Start with F0 as the choice. Take the costlier arms in order F1, F3, F3r; each replaces the current choice only if
   its accuracy minus the choice's has a 95% paired bootstrap interval above 0 (resampling cases, 10,000 draws,
   seed 196) and its false-alarm rate is not higher. The arm left is the method. It must also catch at least one
   NOT_FIT case (the #196 DONE WHEN).
7. Audit, reported whatever the result: every F3 or F3r FIT verdict on NOT_FIT gold is read and classed as a cheat
   (capability written by hand, unconditional FIT_OK), a gold error or a real capability; every F3 or F3r NOT_FIT on
   FIT gold is classed as a script bug, an environment limit or a real gap.
8. TEST runs once per arm through [scripts/run_arms.py](scripts/run_arms.py) with `--confirm-test-run`, which logs
   to [data/TEST_RUNS.log](data/TEST_RUNS.log). A re-run after any change is counted and reported.

## Known bounds (stated before any result)
- One author wrote the cases, the gold scripts and (as the same model) runs the arms; the packages are well-known,
  so memory is expected to do well. A case set from an outside source would be better (follow-up).
- One model, one date, one call per case: sampling variance is not measured.
- Offline needs only. A need that needs a server, a GPU or credentials cannot be checked in this sandbox.

## Commitment
- `data/cases_test.json` SHA-256 `66a1114585b5a6cf32505971201a596a8395135320d2bb07402645b89b8ade40`, committed with this file before any arm
  run, pilot included.

## TEST result on the 43 cases, and why a second stratum (2026-10-13, after the TEST run)
F0, F1 and F3r all scored 43/43; F3 scored 40/43 (three script errors). The cases are ceiling-easy for memory: every
package is well known to the model. Rule 6 picks F0 here. That says nothing about packages the model cannot know,
which is where `dev reuse` search (the recall aid) sends an agent. Amendment 1 adds a stratum for that, with new
cases; the 43 TEST cases are not re-run.

## Amendment 1 (2026-10-13, before any stratum-N package was looked at): stratum N, post-cutoff packages
9. **Population.** PyPI projects whose first upload in ClickHouse's public `pypi.projects` mirror is on or after
   2026-07-15 (after the model's stated June 2026 knowledge cutoff), whose newest version has a `py3-none-any` wheel,
   whose description is at least 1,500 characters, and with at least 2,000 downloads summed over 2026-07-15 to
   2026-10-12 (`pypi.pypi_downloads_per_day`). Query: [scripts/n_population.sql](scripts/n_population.sql).
10. **Order and acceptance.** Walk the population by `cityHash64(concat(name, '196'))` ascending. Accept a package if
   (a) the capability of its README's first usage example runs offline in the sandbox, with no credentials, server,
   GPU or model download; (b) the README is in English; (c) it is not a plugin that needs a host application; (d)
   `uv run dev reuse check` does not FAIL it on G1 or G2. Log every skipped package with its reason in
   `data/n_selection.md`. Stop at 12 accepted packages.
11. **Two cases per accepted package, written from the README alone, before any arm runs on N:**
   - **claim:** the capability of the README's first usage example, phrased as a need. Gold: that example, changed
     only to run offline with inputs made in the script, run in the sandbox. Works: FIT. Fails: NOT_FIT (a
     documented claim that does not hold).
   - **adjacent:** the nearest capability a user could expect that the README does not claim (the other direction,
     another format, or a missing option). Gold: NOT_FIT if a gold attempt fails and a search of the installed
     source finds no API for it; FIT if the package does it after all.
12. **Scoring.** Stratum N (24 cases) is scored on its own with rules 5-7; each arm runs once on it
   (`cases_n.json`, logged). Rule 6 is applied within N, and the note reports both strata. The combined decision:
   if rule 6 picks a costlier arm on N, the method is F0 for packages the agent named from memory and that arm for
   packages it did not know before search; otherwise F0 for both.

## Amendment 1a (2026-10-13, after walking the 80-package population, before looking at any package outside it)
13. The rule-9 population has 80 packages and yields 7 accepted ones, short of 12 (`data/n_selection.md`). Two
   readings applied while walking, now written down: a "usage example" is a Python API example (the arms call the
   public Python API; CLI-only packages are skipped under 10a), and "runs offline" means from inputs a short script
   can make (a package whose example needs an artefact built by its own CLI or another service is skipped under 10a).
14. Extension: the same query with the download floor lowered from 2,000 to 500
   ([scripts/n_population_ext.sql](scripts/n_population_ext.sql)), minus the 80 already walked, in the same hash
   order, walked under the same rules until 12 packages are accepted in total or the list ends.

## Amendment 2 (2026-10-13, after the stratum-N gold runs, before any arm runs on N)
15. **Sandbox fix.** The N gold run showed pygit2 failing at import in the sandbox (`OpenSSL error: failed to load
   certificates`): `/etc` was not mounted. `sandbox.py` now binds `/etc/ssl`, `/etc/ca-certificates`, `/etc/pki` and
   `/etc/localtime` read-only when present. The 43-case TEST arms ran before this fix. Their only sandbox failures
   were F3's three false alarms (t04, t30, t32), and all three are assertion errors in the agent's own script, not
   import or certificate errors (`data/test_f3.json`), so the fix changes no TEST number. All 44 TEST gold scripts
   and all 24 N gold scripts agree with their labels under the fixed sandbox.
16. **N gold** ([gold_n/](gold_n/), [data/gold_results_n.json](data/gold_results_n.json)): 24 cases, 10 FIT and
   14 NOT_FIT. Ten claims hold. Two do not: ezgb's writes shell out to the `git bug` CLI, which is not installed (n09),
   and satusehat-integration's wheel cannot be imported as documented, because its modules import a `src` package
   that the wheel does not ship (n21). All 12 adjacent needs are NOT_FIT. The selection log, including the deviation
   that 4 of the 12 packages were first released before 2026-07-15 according to PyPI, is
   [data/n_selection.md](data/n_selection.md).
17. **F1 input for N:** a frozen PyPI snapshot of the 12 packages, SHA-256
   `17433bf8b59c57e19947d9895d6136ee50f229daea09c42f5773fe960e1b6c4a`, versions matching the installed ones.
- `data/cases_n.json` SHA-256 `a320437d90bc65549bf8f6b1e2f84efa0ea6d64e9f46841672520f74f746bdb8`, committed with this amendment before any arm runs on N.

## Amendment 3 (2026-10-13, during and after the N runs): harness fixes and the public-repository scrub
18. **Timeouts.** The first F3 run on N crashed at its first 900 s `claude -p` timeout after 9 of 24 cases, because
   `run_arms.py` did not catch `TimeoutExpired`; the F3r run that followed stopped at n10. `run_arms.py` now records a
   timed-out or non-JSON call (`error` field) instead of crashing. The 15 remaining F3 cases ran in a second invocation
   and F3r resumed (logged in `data/TEST_RUNS.log`). The F3 calls that were in flight when the first run crashed
   produced no recorded result and were re-run once; no recorded result was replaced.
19. **Licence scrub (lead's instruction for this public MIT repository).** Committed result files keep verdicts, the
   model's one-sentence reason (cut at 300 characters), timings, token counts, exit codes and exception class names
   only. The raw files (sandbox stdout and stderr, which quote third-party source in tracebacks, and full model
   replies) stay outside git, in `<git-common-dir>/devkit/bench/fit-196-2026-10-13/` with the PyPI snapshots (never
   pushed); their SHA-256 values are in [data/raw_manifest.json](data/raw_manifest.json)
   ([scripts/scrub.py](scripts/scrub.py)). The PyPI snapshots are rebuilt from version-pinned URLs and checked against
   their hashes by [scripts/refetch_pypi.py](scripts/refetch_pypi.py) (both reproduced on 2026-10-13). The exact wheels
   installed are pinned with URL and SHA-256 in [data/wheels.json](data/wheels.json)
   ([scripts/pin_wheels.py](scripts/pin_wheels.py)). Four N gold scripts that followed README examples closely (n05,
   n07, n13, n19) were rewritten as own code with other inputs before commit (n07 and n13 a second time, after the audit, to remove shared call sequences;
   `scripts/readme_overlap.py`); all four still give their labels. The
   F4 and F4r check scripts were written with README text in the prompt, so they stay outside git too (hashes in the
   manifest); the F3 and F3r scripts, written without it, are committed.
   Audit fix: the hedge count (rule 21) was first run on reasons already cut to 300 characters and gave 13/24 hedged
   and a 21/24 cascade; `hedge.py flag` now stores the flag computed on the full reason (15/24, cascade 22/24, as the
   raw files give), and `scrub.py` stores it before cutting.
   The branch was then squashed into one commit on origin/main at the lead's instruction, so the pre-registration
   commits named in this file's history (local `ba1cc9e` protocol and TEST cases, `c3ab2e6` amendment 1, `9044179`
   N cases and amendment 2) are not on the remote. The SHA-256 values of `cases_test.json` and `cases_n.json` above,
   which still match, are the record that the cases were not changed after the arms ran.

## Amendment 4 (2026-10-13, after the N scores, before running it): exploratory arm F4, never used in a decision
20. Rule 6 picks F0 on both strata (see the note). On N the sandbox arms fail mostly because the agent does not know
   the unfamiliar package's API (8 of 10 FIT claims judged NOT_FIT by F3). F4 tests the obvious remedy: F3, and F4r
   = F3r, each with the F1 PyPI text in front ([prompts/f4.md](prompts/f4.md)). Run once on N only, reported as
   exploratory, to inform the follow-up.
21. Exploratory analysis declared now, before computing it: the share of F0 reasons on each stratum that hedge
   ("can't confirm", "not verified", "no reliable knowledge", "limited recall", "uncertain", "I don't have"), as a
   possible router between "memory is enough" and "check further".
