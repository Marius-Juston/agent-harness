"""Hide a question's gold in a trial clone (PROTOCOL.md §2) and print the trial prompt.

Run: `python3 -I scripts/prepare_trial.py <questions.yaml> <clone dir> <Qnn>`; then commit the deletion in the clone.
Deletes the question's `hide` paths and `common_hide`, and the INDEX.md rows that link a hidden note. File operations
only; it never touches the checkout it is run from.
"""

import re
import shutil
import sys
from pathlib import Path


def load(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text())


def main() -> None:
    spec, clone, qid = load(Path(sys.argv[1])), Path(sys.argv[2]).resolve(), sys.argv[3]
    q = {**spec["questions"], **spec["spares"]}[qid]
    hidden = [*q["hide"], *spec["common_hide"]]
    for rel in hidden:
        p = clone / rel
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
        else:
            sys.exit(f"hide path missing in clone: {rel}")
    index = clone / "docs/research/INDEX.md"
    notes = {Path(rel).name for rel in hidden if rel.endswith(".md") and rel.count("/") == 2}
    rows = index.read_text().splitlines(keepends=True)
    kept = [r for r in rows if not any(re.search(rf"\]\({re.escape(n)}\)", r) for n in notes)]
    index.write_text("".join(kept))
    print(f"hid {len(hidden)} paths, {len(rows) - len(kept)} INDEX rows", file=sys.stderr)
    print(spec["template"].format(prompt=" ".join(q["prompt"].split())))


if __name__ == "__main__":
    main()
