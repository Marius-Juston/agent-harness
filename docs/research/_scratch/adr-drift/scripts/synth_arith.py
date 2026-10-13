"""Arithmetic behind the #220 note's derived numbers (inputs are the round files' measured counts)."""

print("floor loss passing today:", 1518 - 1021, f"{(1518 - 1021) / 1518:.1%}")
print(
    "floor after 0.95 raise:",
    int(0.95 * 1518),
    "loss caught above",
    1518 - int(0.95 * 1518),
    f"{(1518 - int(0.95 * 1518)) / 1518:.1%}",
)
print("comment-only decisions:", f"{5 / 23:.0%}", "held by test/hook:", f"{10 / 23:.0%}")
print("decision index imported:", 23 * 15, f"{23 * 15 / 600:.1%} of 600")
print("HANDOFF precision at write:", f"{2 / 10:.0%}", f"{2 / 5:.0%} with said-done rule")
print("memory recall:", f"{4 / 16:.0%}")
print("URL sweep possibly dead:", f"{26 / 539:.1%}")
print("READU false alerts per 100 commits:", 81 / 6000 * 100)
print("CLI drift lived:", "20 merges; Owns stale 58 h")
print("cold bash writes:", f"{118 / 619:.0%}", "triggering reads:", f"{2006 / 6211:.0%}")
print("drift classes missed today:", f"{17 / 32:.0%}")
