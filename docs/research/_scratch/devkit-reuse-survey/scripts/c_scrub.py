"""(c) #110: which credential forms does workflow.scrubbed() miss that gitleaks' rules catch, and what does each
flag on ordinary log lines?

usage (repository root): uv run python docs/research/_scratch/devkit-reuse-survey/scripts/c_scrub.py GITLEAKS_BIN
Probe tokens are built at runtime from a seeded RNG and split literals, so this file holds no secret-shaped string
(the gitleaks pre-commit hook scans it). gitleaks runs as `gitleaks stdin` with its default rules, one probe at a
time. Arms:
  V1       SECRET as it was when this note was written (devkit/workflow.py at 7f2ba80, copied below);
  PROPOSED V1 with a 20-character minimum after `Bearer `, plus EXTRA (the nine vendor-prefix forms and URL
           userinfo that gitleaks caught and V1 did not), self-contained so its benign count is its own;
  HEAD     workflow.scrubbed() of the checkout this runs in (after #422 merged as PR #428, the shipped version);
  gitleaks default rules.
"""

import json
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from devkit import workflow

R = random.Random(416)  # noqa: S311 - fake probe tokens, not secrets
AN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def rnd(n, alphabet=AN):
    return "".join(R.choice(alphabet) for _ in range(n))


def j(*parts):
    return "".join(parts)


FORMS = {  # probe label -> generated credential-shaped value (never printed)
    "github classic": j("gh", "p_", rnd(36)),
    "github fine-grained": j("github", "_pat_", rnd(22), "_", rnd(59)),
    "github app in url": j("https://x-access-token:", "gh", "s_", rnd(36), "@github.com/o/r.git"),
    "anthropic": j("sk", "-ant-api03-", rnd(93, AN + "-_"), "AA"),
    "openai project": j("sk", "-proj-", rnd(48)),
    "aws key id": j("AK", "IA", rnd(16, "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")),
    "aws secret": j("aws_secret", "_access_key = ", rnd(40, AN + "+/")),
    "slack bot": j("xo", "xb-", rnd(12, "0123456789"), "-", rnd(13, "0123456789"), "-", rnd(24)),
    "google api": j("AI", "za", rnd(35, AN + "-_")),
    "pypi": j("py", "pi-AgEIcHlwaS5vcmc", rnd(70, AN + "-_")),
    "npm": j("np", "m_", rnd(36)),
    "gitlab": j("gl", "pat-", rnd(20, AN + "-_")),
    "huggingface": j("h", "f_", rnd(34, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")),
    "stripe live": j("sk", "_live_", rnd(24)),
    "private key": j("-----BEGIN RSA ", "PRIVATE KEY-----\\n", rnd(64, AN + "+/"), "\\n-----END RSA PRIVATE KEY-----"),
    "jwt": j("ey", "JhbGciOiJIUzI1NiJ9.", "ey", "J", rnd(40, AN + "-_"), ".", rnd(43, AN + "-_")),
    "url password": j("postgres://admin:", rnd(16), "@db.example.com:5432/app"),
    "bearer header": j("Authorization: ", "Bearer ", rnd(40, AN + "._-")),
}
BENIGN = [
    "FAILED tests/test_auth.py::test_token_refresh_rejects_expired - AssertionError: assert 401 == 200",
    "commit 3f9a1c0e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a39 (HEAD -> 416-reuse-survey)",
    "RESULT: failed (2 stage(s), 31 s)",
    "Bearer of bad news: the gate failed",
    "the token budget is 4000 words; tokenizer_config.json missing",
    "uuid 8f14e45f-ceea-467f-a0e6-1a4f3c3b8f21 not found",
    "sk-learn is not a package; use scikit-learn",
    "E   ModuleNotFoundError: No module named 'detect_secrets'",
    "##[error]Process completed with exit code 1.",
    "base64 payload aGVsbG8gd29ybGQgdGhpcyBpcyBub3QgYSBzZWNyZXQ= decoded",
]
EXTRA = re.compile(
    r"\b(?:xox[abposr]-[A-Za-z0-9-]{10,}|AIza[A-Za-z0-9_-]{35}|glpat-[A-Za-z0-9_-]{20,}|npm_[A-Za-z0-9]{36}"
    r"|pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}|hf_[A-Za-z]{34}|[sr]k_live_[A-Za-z0-9]{24,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"
)


def gitleaks(binary, text):
    with tempfile.TemporaryDirectory() as d:
        rep = Path(d, "r.json")
        subprocess.run(
            [binary, "stdin", "--no-banner", "--report-format", "json", "--report-path", str(rep), "--exit-code", "0"],
            input=text,
            capture_output=True,
            text=True,
            check=True,
        )
        return [f["RuleID"] for f in json.loads(rep.read_text() or "[]")]


V1 = re.compile(  # devkit/workflow.py SECRET at 7f2ba80
    r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{8,}|AKIA[A-Z0-9]{12,})"
    r"|(?<=[Bb]earer )[A-Za-z0-9._~+/=-]+|(?<=[Tt]oken )[A-Za-z0-9_]{20,}"
)
V1_BEARER20 = re.compile(V1.pattern.replace("[A-Za-z0-9._~+/=-]+|", "[A-Za-z0-9._~+/=-]{20,}|"))
assert V1_BEARER20.pattern != V1.pattern  # noqa: S101 - research script


def arms(binary, line):
    return {
        "V1": bool(V1.search(line)),
        "PROPOSED": bool(V1_BEARER20.search(line) or EXTRA.search(line)),
        "HEAD": workflow.scrubbed(line) != line,
        "gitleaks": bool(gitleaks(binary, line)),
    }


def main():
    binary = sys.argv[1]
    hit = dict.fromkeys(["V1", "PROPOSED", "HEAD", "gitleaks"], 0)
    # Output names the probe form and each arm's verdict only: never a generated value or anything derived from it
    # (CodeQL py/clear-text-logging-sensitive-data on PR #427).
    print("probe | " + " | ".join(hit))
    for label in list(FORMS):
        verdicts = arms(binary, f"step 3: {FORMS[label]} done")
        for k, v in verdicts.items():
            hit[k] += v
        print(f"{label} | " + " | ".join("caught" if v else "missed" for v in verdicts.values()))
    print(f"forms {len(FORMS)} caught: {hit}")
    fp = dict.fromkeys(hit, 0)
    for line in BENIGN:
        a = arms(binary, line)
        for k, v in a.items():
            fp[k] += v
        if any(a.values()):
            print(f"FP? {line!r}: {a}")
    print(f"benign {len(BENIGN)} flagged: {fp}")


main()
