"""Paths and shared rules for the #192 literature-recall benchmark (PROTOCOL.md).

Units come from two places: this repository's merged notes (public, ids A1..A4) and a private consuming project's
merged notes (ids P01..P09). Everything derived from the private notes (question text, queries, gold titles, raw
results) is kept out of this public repository, under PRIV (gitignored); only code names, counts, hashes and
aggregate numbers are committed.

Environment: LITRECALL_PRIVATE (default <repo>/.scratch/lit-recall-private) and LITRECALL_P_DIR (the private
project's docs/research directory, read-only; needed only by build_inputs.py).
"""

import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
REPO = HERE.parents[4]
RESEARCH = REPO / "docs" / "research"
PRIV = Path(os.environ.get("LITRECALL_PRIVATE", REPO / ".scratch" / "lit-recall-private"))

# Public units: this repository's merged notes with a pre-research QUESTION.md and at least 3 gold papers.
A_UNITS = [
    {
        "id": "A1",
        "note": "architecture-consolidation.md",
        "question": "_scratch/architecture-consolidation/QUESTION.md",
    },
    {"id": "A2", "note": "fix-now-vs-defer.md", "question": "_scratch/fix-now-vs-defer/QUESTION.md"},
    {"id": "A3", "note": "software-discovery.md", "question": "_scratch/software-discovery/QUESTION.md"},
    {"id": "A4", "note": "test-suite-design.md", "question": "_scratch/test-suite-design/QUESTION.md"},
]
for _u in A_UNITS:
    _u["domain"] = "CS"

DOI = re.compile(r"\b(10\.\d{4,9}/[^\s<>\"'\]\[)(,;`|]+)", re.I)
ARXIV = re.compile(r"(?:arxiv\.org/(?:abs|pdf|html)/|arXiv:\s?)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
PMCID = re.compile(r"\b(PMC\d{5,9})\b")
PMID = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{5,9})")


def clean_doi(d: str) -> str:
    d = d.rstrip(".:")
    d = re.sub(r"(?:/full|/abstract|/pdf|/epdf|\.pdf|#.*|\?.*)$", "", d, flags=re.I)
    return d.lower()


def ids_in(text: str) -> dict[str, set[str]]:
    """Every DOI, arXiv id, PMID and PMCID a text names (in links or plain)."""
    out: dict[str, set[str]] = {"doi": set(), "arxiv": set(), "pmid": set(), "pmcid": set()}
    for m in DOI.finditer(text):
        d = clean_doi(m.group(1))
        if d.startswith("10.48550/arxiv."):
            out["arxiv"].add(d.split("arxiv.", 1)[1])
        else:
            out["doi"].add(d)
    out["arxiv"] |= {m.group(1) for m in ARXIV.finditer(text)}
    out["pmcid"] |= {m.group(1).upper() for m in PMCID.finditer(text)}
    out["pmid"] |= {m.group(1) for m in PMID.finditer(text)}
    return out


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def load(name: str, private: bool = False):
    return json.loads(((PRIV if private else DATA) / name).read_text())


def dump(obj, name: str, private: bool = False) -> Path:
    p = (PRIV if private else DATA) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")
    return p


def patient_fetch(c, source: str, url: str, waits: int = 40) -> str:
    """Client.fetch that waits out a shared cooldown (arXiv throttles this IP for minutes at a time)."""
    import time

    from devkit import lit

    for _ in range(waits):
        try:
            return c.fetch(source, url)
        except lit.SourceError as exc:
            m = re.search(r"rate limited for (?:another )?(\d+)s", str(exc))
            if not m:
                raise
            time.sleep(int(m.group(1)) + 5)
    return c.fetch(source, url)
