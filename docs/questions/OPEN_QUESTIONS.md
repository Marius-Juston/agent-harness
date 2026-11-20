# Open questions

One section per question: the question, who asked, the default the asker proceeds on, and the answer once the
owner gives it. Append; never rewrite another agent's lines (this file merges with the union driver).

## Q1 (2026-11-20, lead): approve the test edits in the board.py split (#625, PR #640)?
The split retargets about 250 test patches to the module that defines each name; the gate flags 10 of them as
weakening. Reviewer verdict: merge, round 2. Default: PR stays open until the owner posts `weakening-ok: <reason>`
on #625.
Answer (2026-11-20, owner): `weakening-ok` posted on #625; PR #640 merged.

## Q2 (2026-11-20, lead): approve deleting the lit_bypass test with its script (#636)?
The owner chose deletion (option A) on 2026-11-19; the gate also needs `weakening-ok: <reason>` on #636.
Default: the branch waits, unpushed as a PR.
Answer (2026-11-20, owner): `weakening-ok` posted on #636; PR #653 merged.

## Q3 (2026-11-19, lead): a working Anthropic API key for the paid experiments (#262 pilot, #590 baseline)?
The stored key answers HTTP 401 (format checked, an API key, not an admin key). Budget the owner set: $25 total.
Default: PR #610 stays open; researcher effort stays high.
Answer (2026-11-20, owner): key replaced. Next: the owner runs the #262 pilot (`run_pilot.sh`, $13 cap).

## Q4 (2026-11-20, lead): run the #619 live recording?
`uv run dev eval research --claims 0 --label 619-held-out` (no API spend) from the main checkout; workers are refused
(#647). Default: #619 stays open with its code merged (PR #645).
Answer (2026-11-20, owner): run. S2 failed 34 of 56 calls, so #619 stays open for a rerun (results on #619).

## Q5 (2026-11-19, lead): the library's answers to the TDM questions (#587)?
Q1-Q3 in docs/research/tdm-compliance.md §5 decide whether opt-in institutional access is allowed. After the
2026-11-20 re-measure (PR #646), 21 claims still lack an open-access full text. Default: open access only.

## Q6 (2026-11-19, lead): scratch repository and tokens for least-privilege credentials (#608)?
Default: guards stay the only line of defence; #644 restates the injection envelope as a static-attack filter.
