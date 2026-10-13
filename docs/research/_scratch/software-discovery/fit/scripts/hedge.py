"""Exploratory (PROTOCOL amendment 4, rule 21): does F0 hedge, and how would a hedge-routed cascade score (#196).

A reason "hedges" if it contains one of the phrases fixed in rule 21 before counting: "can't confirm", "not verified",
"no reliable knowledge", "limited recall", "uncertain", "I don't have" (case-insensitive). The committed result files
keep reasons cut at 300 characters, so the flag is computed on the full reason and stored as "hedged".

Usage:
  uv run --no-project python hedge.py flag <raw f0.json> <committed f0.json>   # store "hedged" from the full reasons
  uv run --no-project python hedge.py report <cases.json> <f0.json> <f4r.json|-> [<cases.json> <f0.json> <f4r.json|->]
report prints, per stratum: hedged share, F0 accuracy when hedged and when not, and the cascade "F0, then F4r only
when F0 hedges". Pass "-" when F4r did not run on a stratum; the cascade is then printed only if nothing hedged.
"""

import json
import re
import sys
from pathlib import Path

HEDGE = re.compile(r"can't confirm|not verified|no reliable knowledge|limited recall|uncertain|i don't have", re.I)


def flag(raw: Path, committed: Path) -> None:
    full = json.loads(raw.read_text())
    out = json.loads(committed.read_text())
    for k, v in out.items():
        if not k.startswith("_"):
            v["hedged"] = bool(HEDGE.search(str(full[k].get("reason", ""))))
    committed.write_text(json.dumps(out, indent=1))
    print(committed.name, sum(v["hedged"] for k, v in out.items() if not k.startswith("_")), "hedged")


def report(args: list[str]) -> None:
    for cf, ff, gf in zip(args[::3], args[1::3], args[2::3], strict=True):
        cases = {c["id"]: c for c in json.loads(Path(cf).read_text()) if "exclude" not in c}
        f0 = json.loads(Path(ff).read_text())
        f4 = json.loads(Path(gf).read_text()) if gf != "-" else None
        rows = [(f0[i]["hedged"], f0[i]["verdict"] == c["gold"], i) for i, c in cases.items()]
        h = [ok for hd, ok, _ in rows if hd]
        n = [ok for hd, ok, _ in rows if not hd]
        line = (f"{Path(cf).name}: hedged {len(h)}/{len(rows)}; F0 right when hedged {sum(h)}/{len(h)}, "
                f"when not {sum(n)}/{len(n)}")  # fmt: skip
        if f4 is not None or not h:
            casc = [
                (f4[i]["verdict"] if hd and f4 is not None else f0[i]["verdict"]) == cases[i]["gold"]
                for hd, _, i in rows
            ]
            line += f"; cascade F0 -> F4r when hedged {sum(casc)}/{len(casc)}"
        print(line)


if __name__ == "__main__":
    if sys.argv[1] == "flag":
        flag(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        report(sys.argv[2:])
