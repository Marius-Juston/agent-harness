"""Amendment 3: alternative gold titles = the text of each markdown link in the merged note whose URL names one of the
work's ids (OpenAlex sometimes stores a truncated title, e.g. a subtitle dropped). Used by score.py's title rule.

Usage: LITRECALL_P_DIR=<private project>/docs/research uv run --no-project python alt_titles.py
Writes data/alt_titles_A.json and PRIV/alt_titles_P.json: {unit: {gold key: [titles]}}.
"""

import os
import re
from pathlib import Path

from common import A_UNITS, RESEARCH, dump, ids_in, load

LINK = re.compile(r"\[([^\]]{12,300})\]\((https?://[^)\s]+)\)")


def alt(note: str, gold: list[dict]) -> dict[str, list[str]]:
    by_id = {(k, v.lower() if k == "doi" else v): g["key"] for g in gold for k, v in g["ids"].items() if v}
    out: dict[str, list[str]] = {}
    for m in LINK.finditer(note):
        text, url = m.group(1).strip(), m.group(2)
        if len(text.split()) < 2 or re.fullmatch(r"(?i)(doi|arxiv|pmc|pubmed|abstract|full text|pdf)[\s:\d./-]*", text):
            continue
        for k, vs in ids_in(url).items():
            for v in vs:
                if key := by_id.get((k, v)):
                    out.setdefault(key, [])
                    if text not in out[key]:
                        out[key].append(text)
    return out


def main() -> None:
    p_dir = Path(os.environ["LITRECALL_P_DIR"])
    ga, gp = load("gold_A.json"), load("gold_P.json", private=True)
    a = {u["id"]: alt((RESEARCH / u["note"]).read_text(), ga[u["id"]]) for u in A_UNITS}
    p = {
        u["id"]: alt((p_dir / u["note"]).read_text(), gp[u["id"]]) for u in load("p_units.json", private=True)["units"]
    }
    dump(a, "alt_titles_A.json")
    dump(p, "alt_titles_P.json", private=True)
    for name, d, g in (("A", a, ga), ("P", p, gp)):
        print(name, sum(len(v) for v in d.values()), "works with a link title of", sum(len(v) for v in g.values()))


if __name__ == "__main__":
    main()
