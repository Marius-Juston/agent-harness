"""What differs between projects: `[tool.devkit]` in the project's pyproject.toml. Everything has a default that fits
the Copier template's layout, so a project only states what it changes. An unknown key is an error, never ignored:
a typo must not silently fall back to a default."""

import dataclasses
import functools
import re
import tomllib
from dataclasses import dataclass, field

from devkit.paths import root

# `[tool.devkit.consolidation]`: the review trigger `dev orient` shows (devkit/consolidation.py). Defaults from
# docs/research/architecture-consolidation.md §6 and §8, calibrated on four repositories; a table states only changes.
CONSOLIDATION: dict[str, float | int | str | list[str]] = {
    "growth": 2.0,  # code grew to this many times its size at the last review ...
    "growth_min_loc": 5000,  # ... and by at least this many LOC
    "fp_commits": 100,  # first-parent commits since the last review
    "dormant_k": 30,  # a unit no commit touched in this many first-parent commits is dormant
    "dormant_share": 0.05,  # orphaned dormant units (dormant, imported by no active unit): share of code LOC
    "churny_loc": 1000,  # a non-test file over this many LOC ...
    "churny_commits": 30,  # ... changed by at least this many non-merge commits
    "lazy_cycle": 10,  # an import cycle of this many modules
    "lazy_share": 0.10,  # share of import statements inside functions ...
    "lazy_min_imports": 50,  # ... once there are this many imports
    "exclude": ["migrations/*", "*/migrations/*"],  # globs of dynamically loaded trees, never orphaned
    "since": "",  # a commit to count from instead of the newest `Consolidation-Review:` trailer
}

# `[tool.devkit.prices."<model id>"]`: list $ per million tokens that `dev agent-stats --cost` prices with
# (devkit/stats.py). A model is priced only by its exact id (a -YYYYMMDD snapshot suffix ignored), so an older model
# never takes a newer one's prices; unpriced models are listed. Defaults: the pricing page as fetched for
# docs/research/harness-efficiency.md §2 (2026-10-19). A table merges over the defaults, key by key.
PRICE_KEYS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
PRICES: dict[str, dict[str, float]] = {
    model: dict(zip(PRICE_KEYS, p, strict=True))
    for model, p in {
        "claude-opus-5-5": (4.0, 5.0, 8.0, 0.20, 20.0),
        "claude-sonnet-5-5": (2.0, 2.5, 4.0, 0.10, 10.0),
        "claude-haiku-5-5": (0.10, 0.125, 0.20, 0.01, 0.50),
    }.items()
}


@dataclass(frozen=True)
class Config:
    # Directories holding `agents/*.md` and `skills/*/SKILL.md` (a project's `.claude`, or a plugin's root).
    surface: list[str] = field(default_factory=lambda: [".claude"])
    # True when the project has no agents or skills of its own (the harness plugin provides them): an empty surface
    # then passes. Declared, never inferred, so a moved directory still fails the gate.
    surface_optional: bool = False
    ratchets: str = ".devkit/ratchets.toml"
    # Reviewed files that may add a package install command (`<path glob>  # reason` per line); a missing file is empty.
    install_allowlist: str = ".devkit/install-allow.txt"
    decisions: str = ".devkit/decisions.toml"  # durable rules with enforced-by literals (#226); a missing file is none
    research_dir: str = "docs/research"
    generated_dir: str = "docs/generated"
    handoff: str = "docs/HANDOFF.md"
    evals_dir: str = "evals/scenarios"
    area_map: str = ".github/labeler.yml"  # path -> area label map, in actions/labeler format
    # Plain content: a PR touching only these (with CONTENT_SUFFIXES) is not harness and needs no lead read.
    content: list[str] = field(default_factory=lambda: ["docs/research/", "docs/log/", "docs/questions/"])
    # Backticked repository paths in prose under these prefixes are not checked (their markdown links still are).
    path_check_skip: list[str] = field(default_factory=list)
    quota_cap: int = 95  # weekly all-models percent at which the spawn guard refuses new agents
    # #100: integrate refuses a PR title that is not Conventional Commits (`dev pr` writes one); false turns that
    # refusal and `dev pr-title` off, for a project whose main does not use Conventional Commits subjects.
    conventional_titles: bool = True
    checks_wait_s: int = 900  # how long integrate waits for the checks on a PR head it pushed before it merges
    # #43: one test's limit (pytest-timeout; 0 = none; a test needing more says `@pytest.mark.timeout(n)`), and the
    # deadline of integrate's gate run, past which its whole process group is killed so the gate lock is freed.
    # docs/research/test-suite-design.md §6: the slowest passing test seen was 75 s.
    test_timeout_s: int = 300
    gate_deadline_s: int = 3600
    # #385: gate_deadline_s counts from when integrate's gate holds the machine's gate lock; the wait for it (behind
    # at most the running gate, since integrate's goes ahead of queued worker gates) has this bound of its own.
    gate_lock_wait_s: int = 7200
    # `dev integrate --local-ci` (#76): a required check that has not queued after this long means Actions cannot run.
    local_ci_wait_s: int = 600
    # `dev integrate --local-ci`: required check context -> the commands (argument lists, run in the merged tree after
    # the gate passed; "{tmpdir}" becomes a fresh empty directory) that stand in for it. [] = the gate alone. A required
    # context without an entry is never posted: integrate refuses instead.
    local_ci: dict[str, list[list[str]]] = field(default_factory=lambda: {"check": []})
    # #386: commands (argument lists, run in order in the merged tree after `dev docs`) that rewrite files a merge
    # cannot, e.g. a ledger line hashing other files: `regenerate = [["uv", "run", "python", "scripts/rehash.py"]]`.
    # Mark each such file `merge=devkit-generated` in `.gitattributes` so merging main keeps one side; integrate then
    # runs these and commits what they changed, as it does for generated docs.
    regenerate: list[list[str]] = field(default_factory=list)
    # Claude Code plugins `dev setup` installs at project scope: "plugin@marketplace" -> marketplace source.
    plugins: dict[str, str] = field(
        default_factory=lambda: {
            "harness-ops@agent-harness": "Marius-Juston/agent-harness",
            "astral@astral-sh": "astral-sh/claude-code-plugins",
        }
    )
    # `dev doctor` (#390): MCP server match (a substring of a configured server's name, command or args) -> the real
    # binaries it needs on PATH, e.g. `mcp_prereqs = { "lean-lsp" = ["rg"] }`. A shell function of that name does not
    # count: the server starts without the shell.
    mcp_prereqs: dict[str, list[str]] = field(default_factory=dict)
    # #431: workflow -> globs of its copies whose action pins follow it (`dev docs` rewrites them; the gate fails when
    # they lag), e.g. `{ ".github/workflows/ci.yml" = ["template/.github/workflows/*.yml"] }`: Dependabot bumps only
    # the workflows it reads.
    action_pins: dict[str, list[str]] = field(default_factory=dict)
    consolidation: dict[str, object] = field(default_factory=lambda: dict(CONSOLIDATION))
    prices: dict[str, dict[str, float]] = field(default_factory=lambda: {k: dict(v) for k, v in PRICES.items()})


class ConfigError(ValueError):
    pass


def parse(data: dict) -> Config:
    known = {f.name: f for f in dataclasses.fields(Config)}
    if unknown := sorted(set(data) - set(known)):
        raise ConfigError(f"[tool.devkit]: unknown key(s) {unknown}; known: {sorted(known)}")
    for key, value in data.items():
        want = Config.__annotations__[key]
        kind = getattr(want, "__origin__", want)
        if (isinstance(kind, type) and not isinstance(value, kind)) or (kind is int and isinstance(value, bool)):
            raise ConfigError(f"[tool.devkit] {key} = {value!r}: expected {want}")
    for match, tools in data.get("mcp_prereqs", {}).items():
        if not (isinstance(tools, list) and all(isinstance(t, str) and t for t in tools)):
            raise ConfigError(f"[tool.devkit.mcp_prereqs] {match} = {tools!r}: expected a list of binary names")
    for source, globs in data.get("action_pins", {}).items():
        if not (isinstance(globs, list) and globs and all(isinstance(g, str) and g for g in globs)):
            raise ConfigError(f"[tool.devkit.action_pins] {source} = {globs!r}: expected a list of path globs")
    for argv in data.get("regenerate", []):
        if not (isinstance(argv, list) and argv and all(isinstance(a, str) and a for a in argv)):
            raise ConfigError(f"[tool.devkit] regenerate entry {argv!r}: expected a command as a list of arguments")
    if "consolidation" in data:
        data = {**data, "consolidation": _consolidation(data["consolidation"])}
    if "prices" in data:
        data = {**data, "prices": _prices(data["prices"])}
    return Config(**data)


def _prices(table: dict) -> dict[str, dict[str, float]]:
    out = {k: dict(v) for k, v in PRICES.items()}
    for model, p in table.items():
        if not isinstance(p, dict) or set(p) - set(PRICE_KEYS):
            raise ConfigError(f"[tool.devkit.prices.{model}]: expected a table of {list(PRICE_KEYS)}")
        out[model] = {**out.get(model, {}), **p}
        ok = set(out[model]) == set(PRICE_KEYS)
        if not ok or not all(isinstance(v, int | float) and not isinstance(v, bool) and v >= 0 for v in p.values()):
            raise ConfigError(f"[tool.devkit.prices.{model}]: expected numbers for {list(PRICE_KEYS)}")
    return out


def _consolidation(table: dict) -> dict:
    if unknown := sorted(set(table) - set(CONSOLIDATION)):
        raise ConfigError(f"[tool.devkit.consolidation]: unknown key(s) {unknown}; known: {sorted(CONSOLIDATION)}")
    for key, value in table.items():
        default = CONSOLIDATION[key]
        if isinstance(default, list):
            ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        elif isinstance(default, float):
            ok = isinstance(value, int | float) and not isinstance(value, bool)
        else:
            ok = isinstance(value, type(default)) and not isinstance(value, bool)
        if not ok:
            raise ConfigError(f"[tool.devkit.consolidation] {key} = {value!r}: expected {type(default).__name__}")
    return {**CONSOLIDATION, **table}


def parse_cap(text: str) -> int:
    """A quota cap percent: a decimal integer 0-100 (surrounding whitespace allowed), else ValueError."""
    t = text.strip()
    if not re.fullmatch(r"[0-9]{1,3}", t) or int(t) > 100:
        raise ValueError(f"quota cap {text!r}: expected an integer percent 0-100")
    return int(t)


@functools.cache
def get() -> Config:
    path = root() / "pyproject.toml"
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    return parse(data.get("tool", {}).get("devkit", {}))
