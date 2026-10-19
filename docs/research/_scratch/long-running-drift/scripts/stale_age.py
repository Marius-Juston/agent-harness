"""Hand-coded classification of the stale references from refrot.py, and how long each genuine one has been stale:
date of the last commit touching the referenced path (its deletion or move) to 2026-10-19."""

import json
import subprocess
from datetime import UTC, datetime

GENUINE = [
    ("avelino/awesome-go", ".github/scripts/check-quality.js"),
    ("langflow-ai/langflow", "src/backend/base/langflow/components"),
    ("oven-sh/bun", "src/shell"),
    ("oven-sh/bun", "src/bake"),
    ("infiniflow/ragflow", "api/db/db_models.py"),
    ("microsoft/ML-For-Beginners", ".github/workflows/co-op-translator.yml"),
    ("elastic/elasticsearch", "x-pack/plugin/src/yamlRestTest/resources/rest-api-spec/test/esql"),
]
OMNI = ("diegosouzapw/OmniRoute", "localDb.ts")  # bare name: find its old path via first-commit tree
# token counts (27 STALE tokens in rot.json; ragflow and career-ops each emit 2 tokens for one reference)
GENUINE_TOKENS = 9
FP = {
    "intentional removed/do-not-reintroduce note": 9,
    "install-generated, gitignored or user data": 6,
    "outside repo (installed pkg, private repo)": 2,
    "import specifier": 1,
}
now = datetime(2026, 10, 19, tzinfo=UTC)
rows = []
for repo, p in GENUINE:
    d = json.loads(
        subprocess.run(
            ["gh", "api", f"repos/{repo}/commits?path={p}&per_page=1"], capture_output=True, text=True
        ).stdout
    )
    when = d[0]["commit"]["committer"]["date"] if d else None
    rows.append((repo, p, when, (now - datetime.fromisoformat(when.replace("Z", "+00:00"))).days if when else None))
for r in rows:
    print(r)
days = sorted(r[3] for r in rows if r[3] is not None)
print(
    "tokens genuine:",
    GENUINE_TOKENS,
    "FP:",
    sum(FP.values()),
    FP,
    "token precision:",
    round(GENUINE_TOKENS / (GENUINE_TOKENS + sum(FP.values())), 3),
)
print("repos with >=1 genuine stale ref:", len({r for r, _ in GENUINE} | {OMNI[0]}), "of 106; flagged repos 13")
print("days stale (path's last commit -> 2026-10-19):", days)
