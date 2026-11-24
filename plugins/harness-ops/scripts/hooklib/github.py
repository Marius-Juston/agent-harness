"""guard_bash's GitHub and credential rules (#528, #601, #603): the gh writes that bypass integrate or widen
access, the forged records devkit trusts, and the owner's credentials (gh's config directory, SSH keys, token
variables). Defense in depth, not a security boundary: see guard_bash's docstring. Stdlib only."""

import functools
import os
import re
from fnmatch import fnmatchcase

from hooklib import pycode
from hooklib.shell import ASSIGN, GREPS, PY, SHELLS, Simple, Word, base, short_flag, unwrap, within

# #601: gh's credential: its hosts file (under $GH_CONFIG_DIR, $XDG_CONFIG_HOME/gh or ~/.config/gh) and the token
# variables. gh reads them itself as a program; an agent reading one holds the owner's GitHub token outside every
# gh rule below (#528).
GH_CRED = (
    "the owner's credentials (gh's config directory ~/.config/gh or $GH_CONFIG_DIR, GH_TOKEN and GITHUB_TOKEN, the "
    "token `gh auth` or git's credential helpers print, SSH private keys) stay out of agent hands: gh, git and ssh "
    "read them themselves, so run those (`gh auth status` says whether a token is set). A whole-environment read "
    "(env, printenv, set, export -p, os.environ, process.env) prints them too: read one variable by name, e.g. "
    "`printenv HOME`."
)
SSH_PUBLIC = {"config", "known_hosts", "known_hosts.old", "authorized_keys", "environment"}  # not secrets in ~/.ssh
CRED_OK = {"ls", "stat", "test", "[", "[[", "cd", "pushd", "echo", "printf", "file", "ssh", "ssh-add", "ssh-keygen"}
CRED_OK |= {"dirname", "basename", "realpath", "readlink"}  # name a credential path without reading it
TREE_SEARCHERS = {"ag", "ack", "pt", "sift"}  # search a tree with no -r
GREP_ABOVE = (
    "this Grep path is a folder above a credential store (~/.config/gh, ~/.ssh), and Grep searches hidden files, so "
    "it would read the owner's token. Grep the project or a narrower folder."
)
ARCHIVERS = {"tar", "zip", "7z", "7za", "cpio", "rsync", "pax", "ar"}  # read a whole directory tree
CRED_TEXT = re.compile(r"gh/hosts\.yml|gh/config\.yml|GH_CONFIG_DIR|\.ssh/id_[\w-]+\b(?!\.pub)")
# Interpreters whose -e / program text this hook reads for environment and credential reads (python has its own path).
INTERPRETERS = {"node": "js", "nodejs": "js", "deno": "js", "bun": "js", "perl": "pl", "ruby": "rb", "php": "php"}
INTERPRETERS |= {"awk": "awk", "gawk": "awk", "mawk": "awk", "nawk": "awk"}
ENV_ANY = {
    "py": re.compile(r"\benviron\b|\bgetenv\b|\benvirons\b"),
    "js": re.compile(r"\bprocess\.env\b|\bDeno\.env\b|\bBun\.env\b"),
    "pl": re.compile(r"%ENV\b|\$ENV\b|\bENV\b"),
    "rb": re.compile(r"\bENV\b"),
    "awk": re.compile(r"\bENVIRON\b"),
    "php": re.compile(r"\bgetenv\b|\$_ENV\b|\$_SERVER\b"),
}
# One variable read by its literal name: allowed unless the name is a token's.
ENV_ONE = re.compile(
    r"""(?:\bos\.environ\.get\s*\(|\bos\.environ\s*\[|\bos\.getenv\s*\(|\bgetenv\s*\(|\benviron\.get\s*\(|\benviron\s*\["""
    r"""|\bprocess\.env\s*\[|\bENV\.fetch\s*\(|\bENV\s*\[|\$ENV\s*\{|\bENVIRON\s*\[|\$_ENV\s*\[|\$_SERVER\s*\[)"""
    r"""\s*(['"]?)([A-Za-z_]\w*)\1\s*[\])},]|\bprocess\.env\.([A-Za-z_]\w*)"""
)
PY_OS_IMPORT = re.compile(r"(?m)^\s*(?:from\s+os\s+import[^;\n]*|import\s+os\b[^;\n]*)")
# Code that runs text it decodes, which no check here can read (#603 pre-existing finding).
OPAQUE = (
    'decoded or rewritten text run as code (base64 -d | bash, eval "$(... | rev)", exec(b64decode(...))) cannot be '
    "checked: run the command itself."
)
TRANSFORMERS = {"base64", "base32", "basenc", "xxd", "openssl", "gunzip", "zcat", "gzip", "bzip2", "bzcat", "xz"}
TRANSFORMERS |= {"xzcat", "unxz", "zstd", "zstdcat", "unzstd", "lzcat", "uudecode", "rev", "tr", "sed", "awk", "gawk"}
TRANSFORMERS |= {"perl", "ruby", "node", "iconv", "jq", "cut", "paste"}
CODE_DECODE = re.compile(
    r"b64decode|b32decode|b16decode|b85decode|a85decode|codecs\.decode|fromhex|decompress|rot_?13|atob\s*\("
    r"|Buffer\.from\s*\([^)]*['\"](?:base64|hex)|\[::-1\]"
)
CODE_RUN = re.compile(r"\b(?:exec|eval|compile|system|popen|subprocess|spawn|execSync|child_process|Function)\b")
VAR_REF = re.compile(r"\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)")
DECLARERS = {"export", "declare", "typeset", "local", "readonly"}
TOKEN_NAMES = {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"}
DUMPERS = {"export", "declare", "typeset"}  # with no names (or only -p / -x) they print every exported variable

# #528: GitHub writes that bypass integrate or widen access. Every agent acts as the owner's gh user, so an injected
# instruction acts through exactly these. dev integrate calls gh from Python, which this hook never sees.
GH_MERGE = (
    "`gh pr merge` (and a merge through `gh api`) skips integrate's re-gate of the merged tree. Open the PR with "
    "`uv run dev pr <issue>`; the lead merges it with `uv run dev integrate <pr>`."
)
GH_ADMIN = (
    "repository settings, branch protection, rulesets, collaborators, webhooks, keys, secrets, variables, releases, "
    "gists, gh aliases and the gh token are the owner's: no agent changes or reads them. Push your branch and open the "
    'PR with `uv run dev pr <issue>`; for anything else, `uv run dev board block <issue> --reason "<what the owner '
    'must do>"`.'
)
REF_DELETE = (
    "deleting or moving a remote ref can drop another worker's branch. `uv run dev worktree prune` deletes only a "
    "branch whose merged PR head contains its tip."
)
ISSUE_WRITE = (
    "closing, editing, deleting or moving an issue is the lead's. Comment on it (`gh issue comment`) or block it "
    '(`uv run dev board block <issue> --reason "..."`) and say so in your hand-back.'
)
REVIEW = (
    "only harness-ops:reviewer, harness-ops:research-auditor and the agents DEVKIT_REVIEW_AGENTS names post PR "
    "reviews or `Verdict:` lines, each naming only itself: a verdict is what integrate trusts, and every agent posts "
    "as the owner. Hand back; the lead dispatches the reviewer. A project's own reviewer agent is added by the owner: "
    "DEVKIT_REVIEW_AGENTS=security-reviewer (comma-separated, bare or plugin:name) exported in the shell that starts "
    "Claude Code, never in a settings file's env block, which an agent can write (the guard ignores it there)."
)
OWNER_MARK = (
    "`weakening-ok:` lines are the owner's approval and `<!-- devkit:` markers are devkit's own records: an agent "
    'never writes them. Ask the owner with `uv run dev board block <issue> --reason "..."`.'
)
DEFAULT_REVIEWERS = ("harness-ops:reviewer", "harness-ops:research-auditor")  # always allowed to post verdicts
REVIEW_VAR = "DEVKIT_REVIEW_AGENTS"  # #662: the owner's extra reviewers, read through guard_bash.owner_env
VERDICT_LINE = re.compile(r"(?im)^[\s>*_#`-]*verdict:[\s*_]*(merge|send back|reconsider)")  # looser than board.VERDICT
WEAKENING_OK = re.compile(r"^\s*weakening-ok:\s*\S", re.M)  # devkit/weaken.py MARKER: must agree (a test checks)
DEVKIT_MARK = re.compile(r"<!--\s*devkit:")
AGENT_NAMED = re.compile(r"harness-ops:([\w-]+)")
GH_OWNER_CMDS = {
    "repo": {"edit", "delete", "archive", "unarchive", "rename", "create", "deploy-key", "sync", "fork"},
    "gist": {"create", "edit", "delete"},
    "release": {"create", "delete", "edit", "upload", "delete-asset"},
    "secret": {"set", "delete", "remove"},
    "variable": {"set", "delete"},
    "alias": {"set", "import"},  # an alias could name `pr merge`
    "config": {"set"},  # owner settings, aliases among them
    "auth": {"refresh", "login", "logout", "setup-git", "switch"},
    "ruleset": {"create", "edit", "delete"},
    "ssh-key": {"add", "delete"},
    "gpg-key": {"add", "delete"},
    "label": {"delete"},
    "workflow": {"enable", "disable"},
    "extension": {"install", "upgrade", "remove", "create"},
    "extensions": {"install", "upgrade", "remove", "create"},
    "ext": {"install", "upgrade", "remove", "create"},
}
# gh's own command groups (2.x) and its built-in `co` alias. A first word outside them is an alias or an extension,
# which can name any command: refused to subagents, whose aliases this hook cannot resolve.
GH_GROUPS = {"agent-task", "alias", "api", "attestation", "auth", "browse", "cache", "co", "codespace", "completion"}
GH_GROUPS |= {"config", "copilot", "extension", "extensions", "ext", "gist", "gpg-key", "help", "issue", "label"}
GH_GROUPS |= {"org", "pr", "preview", "project", "release", "repo", "ruleset", "rs", "run", "search", "secret"}
GH_GROUPS |= {"ssh-key", "status", "variable", "workflow", "version", "accessibility", "licenses"}
GH_GLOBAL_VALUE = {"-R", "--repo", "--hostname"}  # flags gh takes anywhere, before the command words too
GH_UNKNOWN = (
    "this gh command is not one of gh's own, so it is an alias or an extension, which can run any gh command. Run the "
    "gh command itself; extensions and aliases are the owner's."
)
COMMIT_API = (
    "a commit made through the GitHub API (contents, git data, createCommitOnBranch, merge-upstream) lands on a "
    "branch without integrate. Commit locally, push your issue branch and open the PR with `uv run dev pr <issue>`."
)
GH_DIRECT = (
    "a GitHub API write outside gh (curl, wget, httpie, requests, fetch) skips every gh rule. Use the gh command, "
    "whose writes this hook checks, or a `uv run dev` command; plain GET reads stay allowed."
)
GITHUB_API = re.compile(r"\b(?:api|uploads)\.github\.com\b|/api/v3/|/api/graphql\b")
CODE_WRITE = re.compile(
    r"\.(?:post|put|patch|delete)\s*\(|method\s*[=:]\s*['\"](?:POST|PUT|PATCH|DELETE)|\bdata\s*=|\bjson\s*=|\bbody\s*:",
    re.I,
)
CURL_DATA = {"--data", "--data-raw", "--data-binary", "--data-urlencode", "--data-ascii", "--form", "--form-string"}
CURL_DATA |= {"--upload-file", "--json", "-d", "-F", "-T"}
WGET_WRITE = ("--post-data", "--post-file", "--body-data", "--body-file")
HTTPIE = {"http", "https", "xh", "xhs"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
GH_ISSUE_OWN = {"close", "edit", "delete", "transfer"}  # gh issue <sub>: the lead's, refused to subagents
# REST endpoints no agent writes: the repository itself and its access, refs, merges, releases; gists; new repositories.
GH_OWNER_API = re.compile(
    r"^(?:repos/[^/]+/[^/]+/?$|repos/[^/]+/[^/]+/(?:branches/[^/]+/protection|rulesets|collaborators|hooks|keys|"
    r"actions/|environments|transfer|invitations|releases|dispatches|pages|forks)|gists|user/(?:repos|keys|gpg_keys)|"
    r"orgs/[^/]+/(?:repos|rulesets|hooks|members|secrets|variables))"
)
GH_REF_API = re.compile(r"^repos/[^/]+/[^/]+/(?:git/refs|branches/[^/]+/rename)")
GH_COMMIT_API = re.compile(r"^repos/[^/]+/[^/]+/(?:contents/|git/(?:commits|trees|blobs|tags)|merge-upstream)")
GH_MERGE_API = re.compile(r"^repos/[^/]+/[^/]+/(?:pulls/\d+/merge|merges)")
GH_REVIEW_API = re.compile(r"^repos/[^/]+/[^/]+/pulls/\d+/reviews")
GH_ISSUE_API = re.compile(r"^repos/[^/]+/[^/]+/issues/(?:\d+/?$|comments/\d+)")
GQL_MERGE = re.compile(r"\b(?:mergePullRequest|enablePullRequestAutoMerge|mergeBranch)\b")
GQL_COMMIT = re.compile(r"\bcreateCommitOnBranch\b")
GQL_REF = re.compile(r"\b(?:deleteRef|updateRef|updateRefs|createRef)\b")
GQL_OWNER = re.compile(
    r"\b(?:(?:create|update|delete)BranchProtectionRule|"
    r"(?:create|update|delete)RepositoryRuleset|updateRepository|archiveRepository|unarchiveRepository|"
    r"deleteRepository|createRepository|transferRepository|cloneTemplateRepository|addCollaborator)\b"
)
GQL_REVIEW = re.compile(r"\b(?:addPullRequestReview|submitPullRequestReview|dismissPullRequestReview)\b")
GQL_ISSUE = re.compile(r"\b(?:closeIssue|updateIssue|deleteIssue|transferIssue|updateIssueComment)\b")
GH_API_VALUE = {"-H", "--header", "-q", "--jq", "-t", "--template", "--hostname", "-p", "--preview", "--cache"}
GH_API_VALUE |= {"-R", "--repo"}
GH_API_FIELD = {"-f", "-F", "--field", "--raw-field", "--input"}
BODY_FILE = {"--body-file", "-F", "--input"}  # their value is a file the text is read from; "-" is stdin


@functools.lru_cache(maxsize=1)
def cred_dirs() -> tuple[str, ...]:
    """Credential stores: gh's config directories ($GH_CONFIG_DIR, $XDG_CONFIG_HOME/gh, ~/.config/gh) and ~/.ssh."""
    home = os.path.expanduser("~")
    dirs = [os.environ.get("GH_CONFIG_DIR", ""), os.path.join(os.environ.get("XDG_CONFIG_HOME") or "", "gh")]
    dirs += [os.path.join(home, ".config", "gh"), os.path.join(home, ".ssh")]
    return tuple(dict.fromkeys(os.path.normpath(d) for d in dirs if os.path.isabs(d)))


def cred_path(value: str, cwd: str, glob: bool = False) -> bool:
    """A path into a credential store (any file of gh's config directory, an SSH private key) or a glob reaching one.
    gh's config.yml counts: its aliases can rename `pr merge`."""
    if any(ch.isspace() for ch in value):  # prose, not a path
        return False
    if re.search(r"\$\{?(?:GH_CONFIG_DIR|XDG_CONFIG_HOME)\b", value):
        return True
    path = re.split(r"[=:]", value)[-1]
    dirs = cred_dirs()
    if not path or not (glob or "/" in path or path[0] in "~$." or any(within(cwd, d) for d in dirs)):
        return False  # a bare name outside a credential store: the common case, decided without touching the path
    full = os.path.normpath(os.path.join(cwd, os.path.expanduser(os.path.expandvars(path))))
    ssh = os.path.join(os.path.expanduser("~"), ".ssh")
    for d in dirs:
        if d == ssh:
            secret = within(full, d) and (full == d or not (base(full) in SSH_PUBLIC or full.endswith(".pub")))
            if secret or (glob and any(fnmatchcase(os.path.join(d, k), full) for k in ("id_rsa", "id_ed25519"))):
                return True
        elif within(full, d) or (glob and any(fnmatchcase(x, full) for x in (d, f"{d}/hosts.yml", f"{d}/config.yml"))):
            return True
    return full.endswith(("/gh/hosts.yml", "/gh/config.yml"))


def holds_cred(root: str, cwd: str, hidden: bool = True) -> bool:
    """`root` is a directory at or above a credential store (~, ~/.config, /home, /), so a recursive read of it reaches
    the store; with `hidden` False the store counts only when no hidden directory lies between them."""
    if not root or any(ch.isspace() for ch in root):
        return False
    full = os.path.normpath(os.path.join(cwd, os.path.expanduser(os.path.expandvars(root))))
    for d in cred_dirs():
        if within(d, full) and (hidden or not any(x.startswith(".") for x in os.path.relpath(d, full).split(os.sep))):
            return True
        if any(ch in root for ch in "*?["):  # ~/.config/* lists the store itself; ~/.c*/* a folder above it
            ups = [d]
            while os.path.dirname(ups[-1]) != ups[-1]:
                ups.append(os.path.dirname(ups[-1]))
            if any(fnmatchcase(u, full) for u in ups):
                return True
    return False


def shows_token(args: list[str]) -> bool:
    """`gh auth status -t` / `--show-token[=...]`: it prints the token."""
    return any(a.startswith("--show-token") or re.fullmatch(r"-[a-zA-Z]*t[a-zA-Z]*", a) for a in args)


def env_read(code: str, lang: str) -> bool:
    """Interpreter code that reads a token variable, or the environment other than one variable by literal name."""
    if not ENV_ANY[lang].search(code):
        return False
    if any((m.group(2) or m.group(3)) in TOKEN_NAMES for m in ENV_ONE.finditer(code)):
        return True
    rest = ENV_ONE.sub("", PY_OS_IMPORT.sub("", code) if lang == "py" else code)
    return bool(ENV_ANY[lang].search(rest))


def api_write(name: str, args: list[str]) -> bool:
    """A curl, wget or httpie call that writes to the GitHub API (any method but GET and HEAD)."""
    if name not in ("curl", "wget", *HTTPIE) or not any(GITHUB_API.search(a) for a in args):
        return False
    if name == "curl":
        method = ""
        for k, a in enumerate(args):
            if a in ("-X", "--request") and k + 1 < len(args):
                method = args[k + 1]
            elif a.startswith("--request="):
                method = a.split("=", 1)[1]
            elif a.startswith("-X") and len(a) > 2:
                method = a[2:]
        if method:
            return method.upper() not in ("GET", "HEAD")
        if "-G" in args or "--get" in args:
            return False
        short = [a for a in args if a.startswith("-") and not a.startswith("--")]
        return any(a.split("=", 1)[0] in CURL_DATA for a in args) or any(set(a[1:]) & {"d", "F", "T"} for a in short)
    if name == "wget":
        method = next((a.split("=", 1)[1] for a in args if a.startswith("--method=")), "")
        return method.upper() not in ("", "GET", "HEAD") or any(a.startswith(WGET_WRITE) for a in args)
    items = [a for a in args if not a.startswith("-") and not GITHUB_API.search(a)]
    return any(a.upper() in WRITE_METHODS for a in items) or any(re.match(r"^[\w.-]+(?::=|=|@)", a) for a in items)


def review_agents(extra: str) -> frozenset[str]:
    """#662: the agents that post verdict reviews: DEFAULT_REVIEWERS plus the comma-separated names in `extra`, the
    owner's DEVKIT_REVIEW_AGENTS. Nothing an agent can write extends it: no file, repository, git ref or settings
    `env` block (owner_env reads the variable as unset when one sets it)."""
    return frozenset(DEFAULT_REVIEWERS) | {n.strip() for n in extra.split(",") if n.strip()}


def kind(name: str) -> str:
    """An agent name as guard_bash holds the agent type: plugin prefix dropped."""
    return name.rsplit(":", 1)[-1]


def assigned(pipes: list[list[Simple]]) -> dict[str, str]:
    """NAME -> value for each literal assignment in the command (b=main, export M=-X): what `$b` expands to."""
    out: dict[str, str] = {}
    for pipe in pipes:
        for c in pipe:
            ws = [w.value for w in c.words]
            if ws[:1] and ws[0] in DECLARERS:
                ws = [w for w in ws[1:] if not w.startswith("-")]
            for w in ws:
                m = re.match(r"([A-Za-z_]\w*)=(.*)\Z", w, re.S)
                if not m:
                    break
                if not re.search(r"[$\0`]", m.group(2)):
                    out[m.group(1)] = m.group(2)
    return out


class GitHubRules:
    """guard_bash.Checker's GitHub and credential checks; the Checker sets these attributes."""

    agent: str | None  # the subagent's type, plugin prefix dropped; None in the main (lead) session
    cwd: str
    later: list[str]
    vars: dict[str, str]
    transforms: bool

    def review_env(self) -> str:
        """The owner's DEVKIT_REVIEW_AGENTS; Checker reads it through owner_env. "" (the defaults only) here."""
        return ""

    def feeds_cred_paths(self, c: Simple, earlier: list[Simple]) -> bool:
        """`du -a ~ | xargs grep x`: xargs runs a reader on paths listed from a directory holding a credential store."""
        inner = unwrap([w.value for w in c.words], [])
        if inner and base(inner[0]) in CRED_OK:
            return False
        roots = [w.value for p in earlier for w in p.words[1:] if not w.value.startswith("-")]
        return any(holds_cred(r, self.cwd) for r in roots)

    def gh_cred(self, c: Simple, argv: list[str], paths: list[Word], path_ok: bool) -> str | None:
        """#601: GH_CRED when the command reads or writes a credential store, reads a token variable, or dumps the
        environment."""
        if TOKEN_NAMES & set(c.expands):
            return GH_CRED
        if not path_ok and any(cred_path(w.value, self.cwd, w.glob) for w in paths):
            return GH_CRED
        if any(cred_path(r, self.cwd) or "/environ" in r for r in c.reads + c.writes):
            return GH_CRED
        if any("/environ" in w.value and "/proc/" in w.value for w in c.words):
            return GH_CRED
        name, args = (base(argv[0]), argv[1:]) if argv else ("", [])
        if TOKEN_NAMES & set(args) and name in ("printenv", "declare", "typeset", "export"):
            return GH_CRED
        if name == "printenv" and not [a for a in args if not a.startswith("-")]:
            return GH_CRED
        if name == "set" and not args:
            return GH_CRED
        if name in DUMPERS and all(re.fullmatch(r"-[px]+", a) for a in args):
            return GH_CRED
        words = [w.value for w in c.words if not ASSIGN.match(w.value)]
        if not argv and words and base(words[0]) == "env":  # bare `env [-0] [NAME=v]`: prints every variable
            return GH_CRED
        return GH_CRED if self.sweeps_creds(name, args) else None

    def sweeps_creds(self, name: str, args: list[str]) -> bool:
        """A recursive or archiving read (grep -r, rg, find -exec, cp -r, tar, rsync, ...) of a directory that holds a
        credential store, such as ~ or ~/.config."""
        if name in GREPS and name != "rg":
            if not short_flag(args, "rR", ("--recursive", "--dereference-recursive")):
                return False
        elif name in TREE_SEARCHERS:  # recursive by default, and some read hidden files: counted as reading them
            pass
        elif name == "find":
            runs = {"-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf"} & set(args)
            if not runs and "xargs" not in self.later:
                return False
        elif name in ("cp", "scp"):
            if not short_flag(args, "rRa", ("--recursive", "--archive")):
                return False
        elif name not in ARCHIVERS and name != "rg":
            return False
        hidden = name != "rg" or any(
            a in ("--hidden", "-uu", "-uuu") or re.fullmatch(r"-[a-zA-Z]*\.[a-zA-Z]*", a) for a in args
        )
        roots = [a for a in args if not a.startswith("-")] or ["."]
        return any(holds_cred(root, self.cwd, hidden) for root in roots)

    def reaches(self, c: Simple, argv: list[str], fed: list[str]) -> str | None:
        """What interpreter code and raw HTTP clients reach: the environment, credential files, GitHub API writes
        (subagents), and code decoded at run time."""
        name, args = (base(argv[0]), argv[1:]) if argv else ("", [])
        if (
            self.transforms
            and any("\0" in a for a in argv)
            and (name in SHELLS or PY.match(name) or name in ("eval", "source", ".", *INTERPRETERS) or "\0" in argv[0])
        ):
            return OPAQUE  # eval "$(... | base64 -d)", bash <(... | rev), $(... | tr ...)
        if name in INTERPRETERS:
            code = "\n".join(args + [b for b, _ in c.stdin] + fed)
            if why := self.code_reaches(code, INTERPRETERS[name]):
                return why
        if self.agent is not None and api_write(name, args):
            return GH_DIRECT
        return None

    def code_reaches(self, code: str, lang: str) -> str | None:
        if env_read(code, lang) or CRED_TEXT.search(code):
            return GH_CRED
        if self.agent is not None and GITHUB_API.search(code) and CODE_WRITE.search(code):
            return GH_DIRECT
        if lang == "py":  # #690: words only inside its string literals or comments are text it edits, not code
            return OPAQUE if pycode.runs_decoded(code, CODE_DECODE, CODE_RUN) else None
        if CODE_DECODE.search(code) and CODE_RUN.search(code):
            return OPAQUE
        return None

    def expanded(self, c: Simple) -> Simple:
        """c with the variables this command assigned literal values to expanded; unquoted ones split into words."""
        words: list[Word] = []
        for w in c.words:
            v = VAR_REF.sub(lambda m: self.vars.get(m.group(1) or m.group(2), m.group(0)), w.value)
            if v != w.value and not w.quoted:
                words += [Word(x) for x in v.split()]
            else:
                words.append(Word(v, w.glob, w.quoted, w.brace))
        new = Simple(words)
        new.stdin, new.reads, new.writes, new.expands = c.stdin, c.reads, c.writes, c.expands
        return new

    def gh(self, args: list[str], stdin: list[str]) -> str | None:
        """#528: refuse gh writes that bypass integrate, widen access or forge a record devkit trusts."""
        pos, k = [], 0  # the indexes of gh's two command words; global flags such as -R <repo> may come first
        while k < len(args) and len(pos) < 2:
            if args[k] in GH_GLOBAL_VALUE:
                k += 2
                continue
            if not args[k].startswith("-"):
                pos.append(k)
            k += 1
        group, sub = args[pos[0]] if pos else "", args[pos[1]] if len(pos) > 1 else ""
        rest = args[pos[0] + 1 :] if pos else []
        if group and group not in GH_GROUPS and self.agent is not None:
            return GH_UNKNOWN
        if group in ("extension", "extensions", "ext") and sub == "exec" and self.agent is not None:
            return GH_UNKNOWN
        if group == "pr" and sub == "merge":
            return GH_MERGE
        if group == "auth" and (sub in ("token", "git-credential") or (sub == "status" and shows_token(rest))):
            return GH_CRED
        if sub in GH_OWNER_CMDS.get(group, ()):
            return GH_ADMIN
        if group == "issue" and sub in GH_ISSUE_OWN and self.agent is not None:
            return ISSUE_WRITE
        texts = self.gh_texts(rest, stdin)
        if group == "api" and (why := self.gh_api(rest, texts)):
            return why
        if group == "pr" and sub == "review" and not self.reviewer():
            return REVIEW
        if group in ("pr", "issue", "api"):
            return self.forged(texts, verdicts=True)
        return None

    def gh_api(self, args: list[str], texts: list[str]) -> str | None:
        method, endpoint, fields, k = "", "", False, 0
        while k < len(args):
            a = args[k]
            if a in ("-X", "--method") and k + 1 < len(args):
                method, k = args[k + 1], k + 1
            elif a.startswith("--method=") or (a.startswith("-X") and len(a) > 2):
                method = a.split("=", 1)[1] if a.startswith("--") else a[2:]
            elif a in GH_API_FIELD:
                fields, k = True, k + 1
            elif a.split("=", 1)[0] in GH_API_FIELD:
                fields = True
            elif a in GH_API_VALUE:
                k += 1
            elif not a.startswith("-") and not endpoint:
                endpoint = a
            k += 1
        method = (method or ("POST" if fields else "GET")).upper()
        endpoint = re.sub(r"^(?:https?://[^/]+/(?:api/v3/)?)?/*", "", endpoint).split("?", 1)[0]
        if endpoint == "graphql":
            text = "\n".join(texts)
            if GQL_MERGE.search(text):
                return GH_MERGE
            if GQL_COMMIT.search(text):
                return COMMIT_API
            if GQL_REF.search(text):
                return REF_DELETE
            if GQL_OWNER.search(text):
                return GH_ADMIN
            if GQL_REVIEW.search(text) and not self.reviewer():
                return REVIEW
            return ISSUE_WRITE if GQL_ISSUE.search(text) and self.agent is not None else None
        if method in ("GET", "HEAD"):
            return None
        if GH_MERGE_API.match(endpoint):
            return GH_MERGE
        if GH_COMMIT_API.match(endpoint):
            return COMMIT_API
        if GH_REF_API.match(endpoint):
            return REF_DELETE
        if GH_OWNER_API.match(endpoint):
            return GH_ADMIN
        if GH_REVIEW_API.match(endpoint) and not self.reviewer():
            return REVIEW
        return ISSUE_WRITE if GH_ISSUE_API.match(endpoint) and self.agent is not None else None

    def gh_texts(self, args: list[str], stdin: list[str]) -> list[str]:
        """Every text a gh command may post: its arguments, and the files or stdin its body options read."""
        texts = list(args)
        for k, a in enumerate(args):
            path = None
            if a in BODY_FILE and k + 1 < len(args):
                path = args[k + 1]
            elif a.startswith(("--body-file=", "--input=")):
                path = a.split("=", 1)[1]
            elif "=@" in a:
                path = a.split("=@", 1)[1]
            if path == "-":
                texts += stdin
            elif path:
                try:
                    with open(os.path.join(self.cwd, os.path.expanduser(path)), encoding="utf-8") as f:
                        texts.append(f.read(1 << 20))
                except (OSError, UnicodeDecodeError):
                    pass
        return texts

    def forged(self, texts: list[str], verdicts: bool = False) -> str | None:
        """OWNER_MARK or REVIEW when the text carries a record devkit trusts because the owner's gh user posted it."""
        text = "\n".join(texts)
        if WEAKENING_OK.search(text) or DEVKIT_MARK.search(text):
            return OWNER_MARK
        if not (verdicts and VERDICT_LINE.search(text)):
            return None
        if not self.reviewer():
            return REVIEW
        listed = review_agents(self.review_env())
        named = set(AGENT_NAMED.findall(text))  # a listed name as written, not inside a longer one, counts too
        named |= {kind(n) for n in listed if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text)}
        others = named & {kind(n) for n in listed} - {self.agent}  # a reviewer passing as another listed one
        return REVIEW if others else None

    def reviewer(self) -> bool:
        """The agent is one review_agents (#662) lets post verdict reviews; the lead session never is."""
        return self.agent is not None and self.agent in {kind(n) for n in review_agents(self.review_env())}
