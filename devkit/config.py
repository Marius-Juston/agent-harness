"""What differs between projects: `[tool.devkit]` in the project's pyproject.toml. Everything has a default that fits
the Copier template's layout, so a project only states what it changes. An unknown key is an error, never ignored:
a typo must not silently fall back to a default."""

import dataclasses
import functools
import tomllib
from dataclasses import dataclass, field

from devkit.paths import root


@dataclass(frozen=True)
class Config:
    # Directories holding `agents/*.md` and `skills/*/SKILL.md` (a project's `.claude`, or a plugin's root).
    surface: list[str] = field(default_factory=lambda: [".claude"])
    # True when the project has no agents or skills of its own (the harness plugin provides them): an empty surface
    # then passes. Declared, never inferred, so a moved directory still fails the gate.
    surface_optional: bool = False
    ratchets: str = ".devkit/ratchets.toml"
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
    checks_wait_s: int = 900  # how long integrate waits for the checks on a PR head it pushed before it merges
    # `dev integrate --local-ci` (#76): a required check that has not queued after this long means Actions cannot run.
    local_ci_wait_s: int = 600
    # `dev integrate --local-ci`: required check context -> the commands (argument lists, run in the merged tree after
    # the gate passed; "{tmpdir}" becomes a fresh empty directory) that stand in for it. [] = the gate alone. A required
    # context without an entry is never posted: integrate refuses instead.
    local_ci: dict[str, list[list[str]]] = field(default_factory=lambda: {"check": []})
    # Claude Code plugins `dev setup` installs at project scope: "plugin@marketplace" -> marketplace source.
    plugins: dict[str, str] = field(
        default_factory=lambda: {
            "harness-ops@agent-harness": "Marius-Juston/agent-harness",
            "astral@astral-sh": "astral-sh/claude-code-plugins",
        }
    )


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
    return Config(**data)


@functools.cache
def get() -> Config:
    path = root() / "pyproject.toml"
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    return parse(data.get("tool", {}).get("devkit", {}))
