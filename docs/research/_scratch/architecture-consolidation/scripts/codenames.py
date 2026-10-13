"""Code names of the four measured repositories (#111). Paths come from the environment, never from this file.

Set REPO_L, REPO_R, REPO_E and REPO_H to the checkouts, and APP_PKG to repo-L's application package (default "app").
`repo_metrics.py` keys its JSON by the checkout's directory name, so `entry()` and `replay_rows()` look that name up.
Run every script from the worktree root; measured data lives in the gitignored `.scratch/`.
"""

import json
import os
from pathlib import Path

ENV = {"repo-L": "REPO_L", "repo-R": "REPO_R", "repo-E": "REPO_E", "H": "REPO_H"}
APP = os.environ.get("APP_PKG", "app")


def path(code: str) -> Path:
    return Path(os.environ[ENV[code]]).expanduser()


def entry(data: dict, code: str) -> dict:
    """The record for one repository in a repo_metrics.py result keyed by directory name."""
    name = path(code).name
    return data[name] if name in data else data[code]


def replay_rows(code: str) -> list[dict]:
    """Rows of `.scratch/replay_<dir>.json`, written by `repo_metrics.py --replay 1 --replay-tools 10 <repo>`."""
    name = path(code).name
    with open(f".scratch/replay_{name}.json") as fh:
        return json.load(fh)[name]["rows"]
