"""Build the benchmark inputs (PROTOCOL.md rules 1-2): the question text of each unit and the raw paper ids its merged
note cites. Run once, before the split and before any query is written.

Usage: LITRECALL_P_DIR=<private project>/docs/research uv run --no-project python build_inputs.py

Writes data/questions_A.json, data/gold_raw_A.json, data/units.json (public) and PRIV/questions_P.json,
PRIV/gold_raw_P.json (private). Question text = the unit's QUESTION.md (or, for one private unit with none, its
issue's title and Context, saved in PRIV by hand), verbatim.
"""

import os
from pathlib import Path

from common import A_UNITS, PRIV, RESEARCH, dump, ids_in, load


def gold_raw(note_text: str) -> dict[str, list[str]]:
    return {k: sorted(v) for k, v in ids_in(note_text).items()}


def main() -> None:
    p_dir = Path(os.environ["LITRECALL_P_DIR"])
    p_units = load("p_units.json", private=True)["units"]
    qa, ga, qp, gp, units = [], {}, [], {}, []
    for u in A_UNITS:
        qa.append({"id": u["id"], "question": (RESEARCH / u["question"]).read_text().strip()})
        ga[u["id"]] = gold_raw((RESEARCH / u["note"]).read_text())
        units.append({"id": u["id"], "domain": u["domain"], "source": "public"})
    for u in p_units:
        q = u["question"]
        text = (PRIV / q[5:]).read_text() if q.startswith("PRIV:") else (p_dir / q).read_text()
        qp.append({"id": u["id"], "question": text.strip()})
        gp[u["id"]] = gold_raw((p_dir / u["note"]).read_text())
        units.append({"id": u["id"], "domain": u["domain"], "source": "private"})
    for g in (ga, gp):
        for uid, ids in g.items():
            n = sum(len(v) for v in ids.values())
            next(x for x in units if x["id"] == uid)["raw_ids"] = n
    dump(qa, "questions_A.json")
    dump(ga, "gold_raw_A.json")
    dump(qp, "questions_P.json", private=True)
    dump(gp, "gold_raw_P.json", private=True)
    dump(units, "units.json")
    for x in units:
        print(x)


if __name__ == "__main__":
    main()
