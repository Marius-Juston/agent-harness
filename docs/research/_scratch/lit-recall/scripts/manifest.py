"""SHA-256 of every raw answer file under PRIV/runs (PROTOCOL.md rule 9, amendment 5), written to
data/runs_manifest.json so a rerun of score.py can be checked against the inputs it was scored on.

Usage: uv run --no-project python manifest.py
"""

import hashlib

from common import PRIV, dump


def main() -> None:
    files = sorted((PRIV / "runs").glob("*/*.json"))
    dump({str(f.relative_to(PRIV)): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}, "runs_manifest.json")
    print(len(files), "files")


if __name__ == "__main__":
    main()
