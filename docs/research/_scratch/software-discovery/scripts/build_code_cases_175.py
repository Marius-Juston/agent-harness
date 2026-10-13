"""Build #175's held-out code set D: hand-written helpers whose replacement library a commit message names.

Usage: uv run python build_code_cases_175.py <out.json>   (needs `gh` with a token: GitHub REST API)

Rule (PROTOCOL.md amendment 6, written before this script ran): a case is a public GitHub commit, found with the
commit search ("replace custom ... with <library>" and similar, .scratch queries listed in the amendment), whose
message names the PyPI library that replaced a hand-written Python helper; the helper is the function or class the
commit deletes or rewrites to call that library, read at the commit's first parent. Gold is that library only (its
PyPI name, and its GitHub repository from PyPI's project URLs). The case author picked the helper; the commit's
author named the gold. Exclusions, applied here mechanically: the symbol is missing at the parent; the helper's
text (the symbol plus the file's imports, as `dev reuse --from` reads it) names the gold (leakage); the gold is not
on PyPI. Caps: at most 3 cases per gold library, at most 2 per repository. None of #169's set C.

Per case the output keeps everything both arms read, so the TEST run needs no GitHub call for its inputs: `text`
(what from_spec passes to need_from) and `files` (the repository's .py paths at the parent, for own_modules).
"""

import ast
import hashlib
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request

# (repository, migration commit, path at the parent, symbol, gold PyPI name)
PICKS = [
    (
        "thiswillbeyourgithub/MediaMetadataExtractor",
        "5d37a3af6880",
        "MediaMetadataExtractor.py",
        "natural_sort_key",
        "natsort",
    ),
    ("warnes/lucas-game", "cfc3e7e74850", "random_color_screen.py", "get_config_dir", "platformdirs"),
    (
        "vroonhof/opensteuerauszug",
        "d50ac8599f81",
        "src/opensteuerauszug/config/paths.py",
        "get_xdg_config_home",
        "platformdirs",
    ),
    ("pmrv/fleche", "466abf5b9068", "src/fleche/storage/file.py", "file_write_lock", "filelock"),
    (
        "panoseti/panoseti",
        "e70689fa86bd",
        "control/src/control/transfer/daemon.py",
        "_acquire_transfer_lock",
        "filelock",
    ),
    ("turgutino/Mazu", "a902bc22e119", "mazu/checkpoint/lock.py", "ReentrantFileLock", "filelock"),
    ("int-brain-lab/bpod-core", "9b1e95b531eb", "bpod_core/misc.py", "LRUCache", "cachetools"),
    ("artibyrd/powercord", "6023c731fbca", "app/extensions/utilities/widget.py", "EvaluationCache", "cachetools"),
    ("TEAMSchools/teamster", "6e903f0d706f", "scripts/dbt-bq-audit.py", "fmt_md_table", "tabulate"),
    (
        "aloktripathi1/GyanKosh",
        "012111553326",
        "backend/app/agents/grounding.py",
        "_best_word_overlap_match",
        "rapidfuzz",
    ),
    ("TT-Wang/memem", "fc5b20f33d7e", "memem/obsidian_store.py", "_find_best_match", "rapidfuzz"),
    ("dunderlab/deer-agent-framework", "bc0ef22464e9", "deer/__init__.py", "load_env_file", "python-dotenv"),
    (
        "ultra-trace-systems/mascope",
        "af01ee724f08",
        "libraries/sdk/src/mascope_sdk/client.py",
        "_parse_env_file",
        "python-dotenv",
    ),
    ("SunYanbox/code-gather", "3eafdfbe26d5", "main.py", "load_env", "python-dotenv"),
    ("Taiiwo/TaiiCMS", "60f2ad95f42d", "plugins/payment/payment.py", "dict2xml", "xmltodict"),
    ("alberto3433/openbot", "16e70afd70d5", "orderbot/cache/base.py", "singularize", "inflect"),
    ("narumiruna/telegram-bot", "de9feb029eda", "src/bot/retry.py", "retry_on_failure", "tenacity"),
    ("PrimeIntellect-ai/verifiers", "bf322c44a301", "verifiers/utils/retry_utils.py", "with_retry", "tenacity"),
    (
        "azezpz1/messy-weather-sports-bot",
        "75d6f96b9b41",
        "src/messy_weather_nfl_bot/retry.py",
        "request_with_retry",
        "tenacity",
    ),
    ("franklinbaldo/causaganha", "7eaa103d3a55", "src/djen_backup/rate_limit.py", "TokenBucket", "aiolimiter"),
    ("sebvannistel/PolyglotSubs-Kodi", "9d19b2096707", "a4kSubtitles/lib/utils.py", "DictAsObject", "python-box"),
    (
        "sebvannistel/PolyglotSubs-Kodi",
        "9d19b2096707",
        "a4kSubtitles/lib/utils.py",
        "strip_non_ascii_and_unprintable",
        "ftfy",
    ),
    ("shepherdpp/qteasy", "7761fd5d082e", "qteasy/utilfuncs.py", "progress_bar", "tqdm"),
    ("half-orm/half-orm-dev", "68db28caf0dd", "half_orm_dev/release_manager.py", "Version", "packaging"),
    ("DataLab-Platform/Sigima", "dbcd64f2a7a8", "sigima/client/remote.py", "is_version_at_least", "packaging"),
    ("opendecree/decree-python", "9c3739cd9548", "sdk/src/opendecree/_compat.py", "_parse_version", "packaging"),
    ("mstsirkin/tokenbudget", "d488ea121c27", "whenparse.py", "parse_when", "dateparser"),
    (
        "AnhAnh18/KnowledgeNexus",
        "1288418eb89d",
        "src/knowledgenexus/shared/contracts/foundation/schema_validator.py",
        "_is_rfc3339_date_time",
        "jsonschema",
    ),
    ("terrateamio/terratunnel", "5180272f8c51", "terratunnel/client/app.py", "_is_binary_content", "python-magic"),
    (
        "ericmjl/agent-autolearn",
        "a392649f765e",
        "skills/autolearn-reviewer/scripts/autolearn.py",
        "_slugify",
        "python-slugify",
    ),
    ("abhijeetmamidwar/better", "383b76f4adba", "alarm_clock/utils.py", "parse_time", "python-dateutil"),
]
CAP_GOLD, CAP_REPO = 3, 2
GH_REPO = re.compile(r"github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?(?:[/#?].*)?$", re.I)


def gh(path: str) -> dict | list:
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def raw(repo: str, sha: str, path: str) -> str:
    url = f"https://raw.githubusercontent.com/{repo}/{sha}/{urllib.parse.quote(path)}"
    with urllib.request.urlopen(url, timeout=40) as r:
        return r.read().decode("utf-8", errors="replace")


def helper_text(src: str, symbol: str) -> str | None:
    """What reuse.from_spec passes to need_from: the symbol's source (decorators included) plus the file's
    module-level imports; None when the symbol is not defined in the file."""
    tree = ast.parse(src)
    node = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and n.name == symbol
        ),
        None,
    )
    if node is None:
        return None
    start = min([node.lineno, *[d.lineno for d in node.decorator_list]])
    code = "\n".join(src.splitlines()[start - 1 : node.end_lineno])
    imports = [ast.unparse(n) for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)]
    return "\n".join([code, *imports])


def gold_repo(pypi: str) -> str:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{pypi}/json", timeout=40) as r:
        info = json.loads(r.read())["info"]
    urls = [*(info.get("project_urls") or {}).values(), info.get("home_page") or ""]
    for u in urls:
        if m := GH_REPO.search(u or ""):
            return gh(f"repos/{m[1]}")["full_name"].lower()  # GitHub's current name (tox-dev/py-filelock moved)
    return ""


def leak_terms(pypi: str) -> set[str]:
    base = pypi.lower().removeprefix("python-")
    return {pypi.lower(), base, base.replace("-", "_"), base.replace("-", "")}


def leaks(term: str, text: str) -> bool:
    """A term of 5 or more letters anywhere in the text (ReentrantFileLock names filelock); a shorter one as a
    whole word only (box, not checkbox)."""
    if len(term) >= 5:
        return term in text.lower()
    return re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", text, re.I) is not None


def main() -> None:
    out, per_gold, per_repo, dropped = [], {}, {}, []
    for repo, sha, path, symbol, gold in PICKS:
        cid = f"{repo.split('/')[1]}:{symbol}"
        c = gh(f"repos/{repo}/commits/{sha}")
        parent = c["parents"][0]["sha"]
        msg = c["commit"]["message"].strip()
        text = helper_text(raw(repo, parent, path), symbol)
        if text is None:
            dropped.append(f"{cid}: symbol missing at the parent")
            continue
        if hits := [t for t in leak_terms(gold) if leaks(t, text)]:
            dropped.append(f"{cid}: helper names the gold ({', '.join(sorted(hits))})")
            continue
        if per_gold.get(gold, 0) >= CAP_GOLD or per_repo.get(repo, 0) >= CAP_REPO:
            dropped.append(f"{cid}: cap")
            continue
        grepo = gold_repo(gold)
        tree = gh(f"repos/{repo}/git/trees/{parent}?recursive=1")
        files = sorted(t["path"] for t in tree["tree"] if t["type"] == "blob" and t["path"].endswith(".py"))
        per_gold[gold] = per_gold.get(gold, 0) + 1
        per_repo[repo] = per_repo.get(repo, 0) + 1
        out.append(
            {
                "id": cid,
                "repo": repo,
                "commit": c["sha"],
                "parent": parent,
                "path": path,
                "symbol": symbol,
                "gold_pypi": gold,
                "gold_repo": grepo,
                "gold_by": f"commit message: {msg.splitlines()[0]}",
                "files_truncated": bool(tree.get("truncated")),
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "files": files,
            }
        )
        print(f"kept {cid} -> {gold} ({grepo})", flush=True)
    for d in dropped:
        print("dropped", d)
    with open(sys.argv[1], "w") as f:
        json.dump({"cases": out, "dropped": dropped}, f, indent=1)
    print(f"{len(out)} cases kept, {len(dropped)} dropped")


if __name__ == "__main__":
    main()
