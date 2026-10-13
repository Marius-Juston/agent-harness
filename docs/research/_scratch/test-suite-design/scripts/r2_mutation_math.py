"""Arithmetic behind r2-mutation-diff.md (inputs copied from the run logs of 2026-10-12)."""

# mutmut 3.8.0 whole devkit/ (H at bd9abb9): exit-code tallies from mutants/devkit/*.meta
killed, survived, no_tests, timeout = 7756, 4437, 2617, 13
total = killed + survived + no_tests + timeout
print(
    "mutmut total",
    total,
    "score killed/(killed+survived) %.3f" % (killed / (killed + survived)),
    "score over all %.3f" % (killed / total),
    "no-tests share %.3f" % (no_tests / total),
)
print("mutants per LOC %.2f" % (total / 5943))
# cosmic-ray 8.7.0 on #107 with cr-filter-git
cr_total, cr_kept, cr_killed, cr_surv, cr_wall = 4108, 402, 303, 99, 272.18
print(
    "cr kept share %.3f" % (cr_kept / cr_total),
    "survival %.3f" % (cr_surv / cr_kept),
    "s/mutant %.2f" % (cr_wall / cr_kept),
    "mutants per added line %.2f" % (cr_kept / 253),
    "lines mutated 81 -> mutants/line %.2f" % (cr_kept / 81),
)
# replay of 12 merged PRs
tests, pass_base, assert_fail = 159, 12, 49
missing_api = (
    56 + 14 + 9 + 2 + 10 + 2 + 3 + 1 + 1
)  # AttributeError TypeError collect ValueError JSON FNF StopIt BoardError ConnErr
print(
    "replay pass-before %.3f" % (pass_base / tests),
    "assert-fail %.3f" % (assert_fail / tests),
    f"non-assert fail {missing_api} = {missing_api / tests:.3f}",
    "sum",
    pass_base + assert_fail + missing_api,
)
