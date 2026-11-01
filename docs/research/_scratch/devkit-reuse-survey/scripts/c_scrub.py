"""(c) #110: which credential forms does workflow.scrubbed() miss that gitleaks' rules catch, and what does each
flag on ordinary log lines?

usage (repository root): uv run python docs/research/_scratch/devkit-reuse-survey/scripts/c_scrub.py GITLEAKS_BIN
Probe tokens are built at runtime from a seeded RNG and split literals, so this file holds no secret-shaped string
(the gitleaks pre-commit hook scans it). gitleaks runs as `gitleaks stdin` with its default rules, one probe at a
time. EXTRA is the prefix set proposed for scrubbed(): the deterministic-prefix forms gitleaks caught and SECRET
did not.
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


SECRETS = {
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


def main():
    binary = sys.argv[1]
    cur = ext = gl = 0
    print("probe | scrubbed() | scrubbed()+EXTRA | gitleaks rules")
    for name, tok in SECRETS.items():
        line = f"step 3: {tok} done"
        c = workflow.scrubbed(line) != line
        e = c or bool(EXTRA.search(line))
        g = gitleaks(binary, line)
        cur, ext, gl = cur + c, ext + e, gl + bool(g)
        print(f"{name} | {c} | {e} | {','.join(g) or '-'}")
    print(f"secrets {len(SECRETS)}: scrubbed() {cur}, +EXTRA {ext}, gitleaks {gl}")
    fc = fe = fg = 0
    for line in BENIGN:
        c = workflow.scrubbed(line) != line
        e = c or bool(EXTRA.search(line))
        g = gitleaks(binary, line)
        fc, fe, fg = fc + c, fe + e, fg + bool(g)
        if c or e or g:
            print(f"FP? {line!r}: scrubbed={c} extra={e} gitleaks={g}")
    print(f"benign {len(BENIGN)}: flagged by scrubbed() {fc}, +EXTRA {fe}, gitleaks {fg}")


main()
