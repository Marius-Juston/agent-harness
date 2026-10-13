"""Build the external gold set for recall_bench.py from awesome-python (vinta/awesome-python, pinned commit).

Usage: uv run python build_gold.py <awesome-python README.md> <out.json> [k]
A need is one awesome-python subcategory (`- Fuzzy Matching` under `### Text Processing`), or a whole section when it has
no subcategories. Its gold is every GitHub repo the list puts there. Subcategories named General/Others/Miscellaneous are
dropped (no specific need). k needs (default 60; round 1 used 120) are sampled with seed 169; batch 1 is the first
60 drawn, batch 2 the next 60.
"""

import json
import random
import re
import sys

SKIP_SECTIONS = {"Sponsors", "Categories", "Newsletters", "Podcasts", "Websites", "Contributing"}
SKIP_SUBS = {"general", "others", "other", "miscellaneous"}
GH = re.compile(r"\]\(https://github\.com/([^/)\s]+/[^/)#\s]+)")


def parse(md: str) -> list[dict]:
    needs, sec, desc, sub, cur = [], None, "", None, None
    in_projects = False
    for line in md.splitlines():
        if line.startswith("## "):
            in_projects = line.strip() == "## Projects"
            continue
        if not in_projects:
            continue
        if line.startswith("### "):
            sec, desc, sub, cur = line[4:].strip(), "", None, None
            continue
        if sec is None or sec in SKIP_SECTIONS:
            continue
        if line.startswith("_") and line.rstrip().endswith("_") and not desc:
            desc = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line.strip("_ \n"))
            continue
        m_sub = re.match(r"^- ([^\[].*)$", line)
        if m_sub:  # a subcategory heading
            sub = re.sub(r"\s*-?\s*\[[^\]]*\]\([^)]*\)", "", m_sub.group(1)).strip()
            cur = {"section": sec, "sub": sub, "desc": desc, "gold": []}
            needs.append(cur)
            continue
        if line.startswith("- ["):  # an item directly under the section
            if not needs or needs[-1]["section"] != sec or needs[-1]["sub"] is not None:
                cur = {"section": sec, "sub": None, "desc": desc, "gold": []}
                needs.append(cur)
        m = GH.search(line)
        if m and cur is not None and line.lstrip().startswith("- ["):
            cur["gold"].append(m.group(1).lower().removesuffix(".git"))
    out = []
    for n in needs:
        if n["sub"] and n["sub"].lower() in SKIP_SUBS:
            continue
        if not n["gold"]:
            continue
        n["id"] = f"{n['section']} / {n['sub']}" if n["sub"] else n["section"]
        n["q0"] = f"{n['sub']} {n['section']}".lower() if n["sub"] else n["section"].lower()
        out.append(n)
    return out


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as fh:
        needs = parse(fh.read())
    print(f"eligible needs: {len(needs)}", file=sys.stderr)
    k = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    # random.sample draws by index, so the first 60 of a 120-draw equal the 60-draw: the batches nest.
    drawn = random.Random(169).sample(needs, k)  # noqa: S311 (sampling)
    for i, n in enumerate(drawn):
        n["batch"] = 1 + i // 60
    sample = sorted(drawn, key=lambda n: n["id"])
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(sample, fh, indent=1)
    for n in sample:
        print(f"{n['id']} || {n['q0']} || {n['desc']}")
