#!/usr/bin/env python3
"""PreToolUse(Bash): refuse commands that break project rules, each with the fix in the message (exit 2).
PreToolUse(Read|Grep): refuse the same secrets (.env, the research keys file, gh's credential) to the file tools.

The command is tokenized the way bash reads it: quoted strings and heredoc bodies are data (commit messages, issue
text, file contents), while every simple command is checked, including those inside $(...), `...`, <(...),
`bash -c`, `eval`, `find -exec`, wrappers (sudo, env, time, timeout, xargs, uv run, python -m) and text fed on stdin
to an interpreter (bash, sh, python) by a heredoc, a here-string or a pipe. Stdlib only. Settings deny rules are
prefix-only and this hook is best effort; server-side branch protection is the only complete guard for main.

GitHub writes that bypass integrate or widen access (gh pr merge, repository settings, refs, releases, gists) are
refused to every session; issue housekeeping is the lead's, and PR reviews and `Verdict:` lines belong to the reviewer
agents (#528), told apart by the input's `agent_type`.

A command allowed through that can wait forever (a poll loop, `tail -f`, `watch`, `wait`, ...) is rewritten
(updatedInput) to run under `timeout <[tool.devkit] wait_bound_s>`; see bound_waits.

Defense in depth, not a security boundary. Every agent runs as the owner, holding the owner's GitHub token and SSH
keys, so the GitHub and credential rules (#528, #601) close the common and the obvious paths only: code in a script
file, an encoding this lexer does not undo, or a tool it does not know still reaches what the owner can. The real
boundary is credentials scoped per agent, which no hook provides.
"""

import functools
import itertools
import json
import os
import re
import shlex
import sys
from fnmatch import fnmatchcase

# Hooks run as `#!/usr/bin/env python3`, the host's interpreter, not the project's: every script must run on this
# (Ubuntu 22.04 LTS ships 3.10). tests/test_plugin.py runs each one under it; devkit.setup.HOOK_PYTHON must agree.
MIN_PYTHON = (3, 10)

PIP = "Python is managed with uv only: `uv add <pkg>` (or `uv add --dev`), `uv run ...`. Never pip."
PKILL = '`pkill -f` matches the shell running it. Collect PIDs with `pgrep -f "[p]attern"` in one call, kill them in another.'
ENV = "Never read secrets: .env stays unread. Commands that need it load it themselves."
SECRETS = (
    "Never read the research API keys file (~/.config/api-keys.env or $DEVKIT_SECRETS_FILE): `uv run dev lit` "
    "loads it itself, and `uv run dev doctor` says whether each key is found, never its value."
)
SECRETS_NAME = "api-keys.env"
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
EXPANDS = re.compile(r"\$\{?[#!]?([A-Za-z_]\w*)")  # a variable a word expands (single-quoted text removed first)
SINGLE_QUOTED = re.compile(r"\$?'[^']*'")
DUMPERS = {"export", "declare", "typeset"}  # with no names (or only -p / -x) they print every exported variable
MAIN = "Never push to main: push your issue branch and open a PR (`uv run dev pr <issue>`); the lead integrates."
FORCE = "Force-push rewrites shared history. Use --force-with-lease on your own issue branch only, never on main."
HOOKS = "--no-verify, -n, SKIP= and core.hooksPath skip the checks every agent is held to. Fix what the hook reports instead."
DEEP = "the command nests too deeply to check. Split it into simpler commands."
# Destructive local commands: each loses uncommitted work, and sibling worktrees share one repository.
RESET = (
    "`git reset --hard` / `--merge` throw away uncommitted work. Commit it first, then `git reset --keep <ref>`, "
    "which refuses to lose changes."
)
DISCARD = (
    "this discards uncommitted changes across the tree. Commit what you want to keep (a WIP commit is fine), revert one "
    "file with `git restore <file>`, or unstage with `git restore --staged .`."
)
CLEAN = "`git clean -f` deletes untracked files for good. Preview with `git clean -n`, then `rm` the files it lists."
RM = (
    "recursive rm outside your worktree (or of the worktree or its .git) can delete another worker's files. "
    "`rm -r` paths inside your worktree, and remove a worktree with `git worktree remove <path>`."
)
STASH = (
    "the stash is shared by every worktree of the repository, so `git stash drop` / `clear` can delete another "
    "worker's changes. Keep work in a WIP commit and leave stash entries to the owner."
)
WORKTREE = (
    "`git worktree remove --force` deletes unsaved work. Commit and push its branch, then `git worktree remove <path>`."
)
BRANCH_D = (
    "`git branch -D` deletes an unmerged branch, maybe another worker's. Use `git branch -d`; for a squash-merged "
    "branch, run `uv run dev worktree prune` (integrate already does for the PR it merges): it "
    "deletes only a branch whose merged PR head contains its tip."
)
QUOTA = (
    "the quota cap is the owner's: agents never change it. `uv run dev quota-cap` prints it; ask the owner to run "
    "`! uv run dev quota-cap <pct>` (or to raise [tool.devkit] quota_cap). Stop spawning; commit and hand off."
)
LIT = (
    "call the literature APIs through `uv run dev lit` (search, get, refs, cited-by, status): it shares one rate "
    "limit, cache and key set across every worktree. Working on devkit/lit.py itself? Prefix the fetch with "
    "DEVKIT_LIT_RAW=1."
)
# The endpoints devkit/lit.py wraps (#227; docs/research/decision-records-and-drift.md check 9). Europe PMC stops
# at rest/search: its full-text downloads (rest/PMC.../fullTextXML) are not in dev lit and stay allowed.
LIT_API = re.compile(
    r"(?<![\w.-])(?:https?://)?(?:(?:api\.openalex\.org|api\.semanticscholar\.org|api\.crossref\.org)[/?]"
    r"|www\.ebi\.ac\.uk/europepmc/webservices/rest/search|export\.arxiv\.org/api/)",
    re.I,
)
LIT_VAR = re.compile(r"\$\{?(\w+)")  # a fetched argument that starts with a variable: $u, ${u}...
LIT_RAW = "DEVKIT_LIT_RAW"
# Environment the guards read (#191). The first two switch a guard off, so they count only from the environment
# Claude Code started in (owner_env); edit_guard refuses agent edits that put any of them in a settings file.
OWNER_ENV = ("DEVKIT_ALLOW_COPYLEFT", LIT_RAW, "DEVKIT_SECRETS_FILE")
# Names that move where settings files are found: a settings `env` setting one could hide the file that sets an
# override, so owner_env ignores every override while one is set, and edit_guard refuses adding them as well.
LOCATORS = ("HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR")
FETCHERS = {"curl", "wget"}
SLOTS = (
    "the gate slot count is the owner's: agents never change it. `uv run dev gate-slots` prints it; ask the owner to "
    "run `! uv run dev gate-slots <n>` (or to change [tool.devkit] gate_slots)."
)
# The owner's machine-local overrides under the git common dir: the `dev` command that sets each, its file, the denial.
OWNER_SETTINGS = (("quota-cap", "devkit/quota_cap", QUOTA), ("gate-slots", "devkit/gate_slots", SLOTS))
CAP_READERS = {"cat", "head", "tail", "ls", "stat", "wc", "test", "[", "[[", "file"}
WHOLE_TREE = {".", "./", ":", ":/", ":/*", "*"}  # pathspecs that name every file
HOOK_VAR = re.compile(r"^(SKIP|PRE_COMMIT\w*|GIT_CONFIG\w*)(=|$)")
C_RUNNERS = {"flock", "su", "runuser", "script"}  # their -c / --command value is a shell command
GREPS = {"grep", "egrep", "fgrep", "rg"}
GREP_VALUE = {"-m", "-A", "-B", "-C", "-d", "-D", "--max-count", "--context", "-g", "--glob", "-t", "--type"}

OPS = sorted(
    [
        "&&",
        "||",
        ";;&",
        ";;",
        ";&",
        ";",
        "|&",
        "|",
        "&>>",
        "&>",
        "&",
        "<<<",
        "<<-",
        "<<",
        "<>",
        "<&",
        "<",
        ">>",
        ">&",
        ">|",
        ">",
    ],
    key=len,
    reverse=True,
)
BREAK = set(" \t\n;&|<>()")
ASSIGN = re.compile(r"^[A-Za-z_]\w*\+?=")
PY = re.compile(r"^python[0-9.]*$")
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "mksh", "busybox"}
KEYWORDS = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "!", "{", "}", "time", "case", "esac"}
ENV_SAFE = {".env.example", ".env.sample", ".env.template", ".env.dist"}
ENV_OK_CMDS = {"ls", "stat", "test", "[", "[[", "echo", "printf", "touch", "rm"}
MESSAGE_OPTS = {"-m", "--message", "--title", "--body", "-b", "-t", "--subject"}  # their values are text, not paths
MAX_DEPTH = 8
# `git config` options that only read; any other flag (--add, --unset, -e, ...) makes the call a possible write
CONFIG_READ_ACTIONS = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list"}
CONFIG_READ_FLAGS = {"--global", "--system", "--local", "--worktree", "--show-origin", "--show-scope", "--includes"}
CONFIG_READ_FLAGS |= {"--no-includes", "-z", "--null", "--name-only", "--all", "--regexp", "--fixed-value"}
CONFIG_READ_FLAGS |= {"--bool", "--int", "--bool-or-int", "--path", "--expiry-date", "--no-type"}
CONFIG_VALUE_OPTS = {"-f", "--file", "--blob", "--type", "--default", "--url", "--value"}  # their value is not a name

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
    "only harness-ops:reviewer and harness-ops:research-auditor post PR reviews or `Verdict:` lines, each naming only "
    "itself: a verdict is what integrate trusts, and every agent posts as the owner. Hand back; the lead dispatches "
    "the reviewer."
)
OWNER_MARK = (
    "`weakening-ok:` lines are the owner's approval and `<!-- devkit:` markers are devkit's own records: an agent "
    'never writes them. Ask the owner with `uv run dev board block <issue> --reason "..."`.'
)
REVIEWERS = {"reviewer", "research-auditor"}  # agent types (plugin prefix dropped) that post verdict reviews
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


class Word:  # plain classes, not dataclasses: importing dataclasses costs every Bash call 5 ms
    __slots__ = ("brace", "glob", "quoted", "value")

    def __init__(self, value: str, glob: bool = False, quoted: bool = False, brace: bool = False):
        self.value = value
        self.glob = glob  # an unquoted * ? or [ that bash would expand
        self.quoted = quoted
        self.brace = brace  # an unquoted {, so maybe a brace expansion


class Simple:
    __slots__ = ("expands", "reads", "stdin", "words", "writes")

    def __init__(self, words: list[Word] | None = None):
        self.words: list[Word] = words if words is not None else []
        self.stdin: list[tuple[str, bool]] = []  # heredoc / here-string text, and "is a heredoc"
        self.reads: list[str] = []  # input redirection targets
        self.writes: list[str] = []  # output redirection targets
        self.expands: list[str] = []  # the variables its words and unquoted heredocs expand


class Lexer:
    """A small bash lexer: pipelines of simple commands; substitutions become pipelines of their own."""

    def __init__(self, s: str, out: list[list[Simple]]):
        self.s, self.n, self.out = s, len(s), out
        self.pipe: list[Simple] = [Simple()]
        self.out.append(self.pipe)
        self.pending: list[tuple[str, bool, bool, Simple]] = []

    def end_pipeline(self) -> None:
        if any(c.words or c.stdin or c.reads or c.writes for c in self.pipe):
            self.pipe = [Simple()]
            self.out.append(self.pipe)

    def sub(self, i: int, stop: str) -> int:
        """Lex a substitution starting at i into its own pipelines; return the index after its end."""
        return Lexer(self.s, self.out).run(i, stop)

    def run(self, i: int = 0, stop: str = "") -> int:
        s, depth = self.s, 0
        while i < self.n:
            c = s[i]
            if c in " \t":
                i += 1
            elif c == "\\" and s.startswith("\n", i + 1):
                i += 2
            elif c == "\n":
                i = self.heredocs(i + 1)
                self.end_pipeline()
            elif c == "#":
                j = s.find("\n", i)
                i = self.n if j < 0 else j
            elif c == ")":
                i += 1
                if stop == ")" and depth == 0:
                    return i
                depth = max(depth - 1, 0)
                self.end_pipeline()
            elif c == "(":
                depth += 1
                i += 1
                self.end_pipeline()
            elif c in "<>" and s.startswith("(", i + 1):
                i = self.word_into(i)
            else:
                op = next((o for o in OPS if s.startswith(o, i)), None)
                i = self.word_into(i) if op is None else self.operator(op, i + len(op))
        return i

    def word_into(self, i: int) -> int:
        w, i = self.word(i)
        cur = self.pipe[-1]
        if w.value.isdigit() and not w.quoted and i < self.n and self.s[i] in "<>":
            return i  # a file descriptor number in front of a redirection
        if w.brace:  # p{i,}p {pip,x}: check every word bash would produce
            cur.words += [Word(v, w.glob, w.quoted) for v in expand_braces(w.value)]
        else:
            cur.words.append(w)
        return i

    def operator(self, op: str, i: int) -> int:
        if op in ("|", "|&"):
            self.pipe.append(Simple())
            return i
        if op in ("&&", "||", ";;&", ";;", ";&", ";", "&"):
            self.end_pipeline()
            return i
        while i < self.n and self.s[i] in " \t":
            i += 1
        target, i = self.word(i)
        cur = self.pipe[-1]
        if op in ("<<", "<<-"):
            self.pending.append((target.value, op == "<<-", target.quoted, cur))
        elif op == "<<<":
            cur.stdin.append((target.value, False))
        elif op in ("<", "<>", "<&"):
            cur.reads.append(target.value)
        if op in (">", ">>", ">|", ">&", "&>", "&>>", "<>"):
            cur.writes.append(target.value)
        return i

    def heredocs(self, i: int) -> int:
        """At a newline: read the bodies of the heredocs opened on the line just ended."""
        for delim, _tabs, quoted, owner in self.pending:
            lines, s = [], self.s
            while i < self.n:
                j = s.find("\n", i)
                line = s[i : self.n if j < 0 else j]
                i = self.n if j < 0 else j + 1
                if line.strip() == delim:  # looser than bash, so a body never swallows the commands after it
                    break
                lines.append(line)
            body = "\n".join(lines)
            owner.stdin.append((body, True))
            if not quoted:
                owner.expands += EXPANDS.findall(body)
                scan_substitutions(body, self.out)  # an unquoted heredoc still runs $(...) and `...`
        self.pending = []
        return i

    def word(self, i: int) -> tuple[Word, int]:
        w, j = self.word_at(i)
        if "$" in self.s[i:j]:
            self.pipe[-1].expands += EXPANDS.findall(SINGLE_QUOTED.sub("", self.s[i:j]))
        return w, j

    def word_at(self, i: int) -> tuple[Word, int]:
        s, buf, glob, quoted, brace = self.s, [], False, False, False
        while i < self.n:
            c = s[i]
            if c in BREAK and not (c in "<>" and s.startswith("(", i + 1) and not buf):
                break
            if c in "<>":  # <(...) or >(...) process substitution
                i = self.sub(i + 2, ")")
                buf.append("\0")
            elif c == "\\":
                if s.startswith("\n", i + 1):
                    i += 2
                    continue
                buf.append(s[i + 1 : i + 2])
                quoted, i = True, i + 2
            elif c == "'":
                j = s.find("'", i + 1)
                j = self.n if j < 0 else j
                buf.append(s[i + 1 : j])
                quoted, i = True, j + 1
            elif c == "$" and s.startswith("'", i + 1):
                j = i + 2
                while j < self.n and s[j] != "'":
                    j += 2 if s[j] == "\\" else 1
                buf.append(ansi_c(s[i + 2 : j]))
                quoted, i = True, j + 1
            elif c == '"' or (c == "$" and s.startswith('"', i + 1)):
                i = i + 1 if c == '"' else i + 2
                while i < self.n and s[i] != '"':
                    if s[i] == "\\" and i + 1 < self.n:
                        buf.append(s[i + 1] if s[i + 1] in '$`"\\\n' else s[i : i + 2])
                        i += 2
                    elif s.startswith("$(", i):
                        i = self.sub(i + 2, ")")
                        buf.append("\0")
                    elif s[i] == "`":
                        i = self.backtick(i)
                        buf.append("\0")
                    else:
                        buf.append(s[i])
                        i += 1
                quoted, i = True, i + 1
            elif s.startswith("$(", i):
                i = self.sub(i + 2, ")")
                buf.append("\0")
            elif c == "`":
                i = self.backtick(i)
                buf.append("\0")
            elif s.startswith("${", i):
                j = s.find("}", i)
                j = self.n if j < 0 else j + 1
                buf.append(s[i:j])
                i = j
            else:
                glob = glob or c in "*?["
                brace = brace or c == "{"
                buf.append(c)
                i += 1
        return Word("".join(buf), glob, quoted, brace), i

    def backtick(self, i: int) -> int:
        j = i + 1
        while j < self.n and self.s[j] != "`":
            j += 2 if self.s[j] == "\\" else 1
        inner = re.sub(r"\\([`$\\])", r"\1", self.s[i + 1 : j])
        Lexer(inner, self.out).run()
        return j + 1


def expand_braces(word: str, cap: int = 256) -> list[str]:
    """Bash brace expansion of {a,b,...} (nested too; no {1..3} ranges), at most cap words."""
    depth, start, commas = 0, -1, []
    for i, ch in enumerate(word):
        if ch == "{":
            depth += 1
            if depth == 1:
                start, commas = i, []
        elif ch == "," and depth == 1:
            commas.append(i)
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and commas:
                cuts = [start, *commas, i]
                pre, post = word[:start], word[i + 1 :]
                out: list[str] = []
                for a, b in itertools.pairwise(cuts):
                    for tail in expand_braces(post, cap):
                        out += expand_braces(pre + word[a + 1 : b] + tail, cap)
                        if len(out) >= cap:
                            return out[:cap]
                return out
    return [word]


ANSI_C = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "f": "\f", "v": "\v"}


def ansi_c(s: str) -> str:
    """Decode the body of a $'...' string: \\n, \\t, \\xHH, \\NNN (octal), \\uHHHH; anything else stands for itself."""

    def one(m: re.Match) -> str:
        e = m.group(1)
        if e[0] in "xuU":
            return chr(min(int(e[1:], 16), 0x10FFFF)) if len(e) > 1 else e
        if e[0] in "01234567":
            return chr(int(e, 8))
        return ANSI_C.get(e, e)

    return re.sub(r"\\(x[0-9a-fA-F]{0,2}|u[0-9a-fA-F]{0,4}|U[0-9a-fA-F]{0,8}|[0-7]{1,3}|.)", one, s, flags=re.S)


def scan_substitutions(text: str, out: list[list[Simple]]) -> None:
    lx, i = Lexer(text, out), 0
    while i < len(text):
        if text[i] == "\\":
            i += 2
        elif text.startswith("$(", i):
            i = lx.sub(i + 2, ")")
        elif text[i] == "`":
            i = lx.backtick(i)
        else:
            i += 1


def lex(text: str) -> list[list[Simple]]:
    out: list[list[Simple]] = []
    lx = Lexer(text, out)
    lx.run()
    lx.heredocs(lx.n)
    return out


def settings_files(cwd: str) -> list[str]:
    """Every Claude Code settings file that could feed this session's `env`: .claude/settings{,.local}.json in each
    directory from the cwd, the git checkout (and its main checkout), the user's home and the project directory up
    to /, and in $CLAUDE_CONFIG_DIR. Home and checkout are found without the environment as well, since a settings
    `env` block could point HOME or CLAUDE_PROJECT_DIR elsewhere."""
    starts = [cwd, os.getcwd(), os.environ.get("CLAUDE_PROJECT_DIR", ""), os.path.expanduser("~")]
    try:
        import pwd  # POSIX only

        starts.append(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, AttributeError):
        pass
    import subprocess  # here, not at the top: most calls never spawn anything, and the import costs every call

    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        lines = out.stdout.splitlines() if out.returncode == 0 else []
        starts += lines[:1] + [os.path.dirname(x) for x in lines[1:2]]
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    dirs: list[str] = []
    for start in starts:
        d = os.path.abspath(start) if start else ""
        while d and d not in dirs:
            dirs.append(d)
            d = os.path.dirname(d) if os.path.dirname(d) != d else ""
    files = [os.path.join(d, ".claude", f) for d in dirs for f in ("settings.json", "settings.local.json")]
    if config := os.environ.get("CLAUDE_CONFIG_DIR"):
        files += [os.path.join(config, f) for f in ("settings.json", "settings.local.json")]
    return files


JSON_KEY = re.compile(r'"((?:[^"\\]|\\.)*)"\s*:')


def unescape(raw: str) -> str:
    """A JSON string body decoded, so `DEVKIT_LIT_RAW` reads as the name it is; undecodable text as written."""
    try:
        return json.loads(f'"{raw}"')
    except ValueError:
        return raw


def env_keys(text: str) -> set[str]:
    """The names a settings file's `env` block sets. Text that is not a JSON object fails closed: every key-like
    string in it counts, since Claude Code's reader may be more lenient than json."""
    try:
        data = json.loads(text)
    except ValueError:
        return {unescape(m.group(1)) for m in JSON_KEY.finditer(text)}
    env = data.get("env") if isinstance(data, dict) else None
    return {k for k in env if isinstance(k, str)} if isinstance(env, dict) else set()


def owner_env(name: str, cwd: str) -> str:
    """An override that switches a guard off, read only where an agent's tools cannot set it (#191).

    Claude Code hands a settings file's `env` block to hooks, ahead of the shell it started in (VERIFIED,
    docs/research/agent-harness.md §12c), and an agent can write those files. So the name reads as unset when any
    settings `env` sets it, or sets one of LOCATORS (which could hide the file that does): the owner sets it in the
    shell that starts Claude Code. Unset in the environment, nothing is read, so the usual call costs nothing."""
    value = os.environ.get(name, "")
    if not value:
        return ""
    for path in settings_files(cwd):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        if env_keys(text) & {name, *LOCATORS}:
            return ""
    return value


def base(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def lit_vars(pipes: list[list[Simple]]) -> set[str]:
    """Names assigned a literature-API URL by a command: `u=<url>`, `export u=<url>`, `for u in ... <url> ...`."""
    names = set()
    for c in itertools.chain.from_iterable(pipes):
        words = [w.value for w in c.words]
        while words and words[0] in KEYWORDS:
            words.pop(0)
        if words[:1] == ["for"] and words[2:3] == ["in"] and any(LIT_API.match(v) for v in words[3:]):
            names.add(words[1])
        if words[:1] and words[0] in ("export", "declare", "typeset", "local", "readonly"):
            words.pop(0)
        for w in itertools.takewhile(lambda w: ASSIGN.match(w) or w.startswith("-"), words):
            name, _, value = w.partition("=")
            if value and LIT_API.match(value):
                names.add(name.rstrip("+"))
    return names


def long_opt(arg: str, name: str, shortest: int) -> bool:
    """git accepts any unambiguous prefix of a long option."""
    arg = arg.split("=", 1)[0]
    return len(arg) >= shortest and name.startswith(arg)


def is_env(w: Word, code: bool = False) -> bool:
    """`code`: the word is from a Python string literal, where `.*` and `(.*?)` are regexes far more often than globs,
    so a glob counts only if it spells the start of the name (`.e*`, `.env*`)."""
    if any(ch.isspace() for ch in w.value):  # prose (a commit or issue body), not a path
        return False
    name = base(re.split(r"[=:]", w.value)[-1])
    if w.glob:  # a glob reaches a dotfile only if its own name starts with "." (no dotglob)
        if code and name[1:2] in ("", "*", "?", "["):
            return False
        return name.startswith(".") and any(fnmatchcase(f, name) for f in (".env", ".env.local"))
    return name not in ENV_SAFE and (name == ".env" or name.startswith(".env."))


def is_secrets(w: Word) -> bool:
    """The research API keys file: its default name, $DEVKIT_SECRETS_FILE, or the path that variable names here."""
    if any(ch.isspace() for ch in w.value):  # prose, not a path
        return False
    if "DEVKIT_SECRETS_FILE" in w.value and "$" in w.value:
        return True
    path = re.split(r"[=:]", w.value)[-1]
    name = base(path)
    if w.glob:  # `api-*`, `*.env`, or anything in ~/.config that would expand to it
        literal = re.sub(r"[*?\[\]]", "", name)
        near = path.rsplit("/", 1)[0].endswith(".config") if "/" in path else False
        return fnmatchcase(SECRETS_NAME, name) and (len(literal) >= 3 or near)
    custom = os.environ.get("DEVKIT_SECRETS_FILE", "")
    return name == SECRETS_NAME or (bool(custom) and ((name == base(custom) and name != "") or path == custom))


SKIP_VALUE = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "--user", "--group"},
    "doas": {"-u", "-C"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "-t"},
    "timeout": {"-k", "-s", "--kill-after", "--signal"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-a", "-E", "-s", "-i", "--max-args", "--max-procs", "--delimiter"},
    "stdbuf": {"-i", "-o", "-e"},
    "flock": {"-w", "-E", "--timeout"},
    "env": {"-u", "--unset", "-C", "--chdir"},  # strip_starters only: unwrap handles env itself
}
WRAPPERS = {"sudo", "doas", "nice", "ionice", "timeout", "xargs", "stdbuf", "nohup", "exec", "command", "builtin"}
WRAPPERS |= {"setsid", "chronic", "unbuffer", "flock", "time"}
UV_RUN_VALUE = {"--with", "-w", "--python", "-p", "--package", "--env-file", "--directory", "--project", "--extra"}
UV_RUN_VALUE |= {"--group", "--index", "--with-requirements", "--with-editable", "--only-group", "--no-group"}


def unwrap(argv: list[str], assigns: list[str]) -> list[str]:
    """Strip assignments and wrappers (sudo, env, time, timeout, xargs, uv run, python -m ...) to the real command."""
    argv = list(argv)
    while argv:
        while argv and ASSIGN.match(argv[0]):
            assigns.append(argv.pop(0))
        if not argv:
            break
        name = base(argv[0])
        if name in KEYWORDS:
            argv.pop(0)
        elif name == "env":
            argv.pop(0)
            while argv and (argv[0].startswith("-") or ASSIGN.match(argv[0])):
                a = argv.pop(0)
                if ASSIGN.match(a):
                    assigns.append(a)
                elif a in ("-S", "--split-string") and argv:
                    argv = argv.pop(0).split() + argv
                elif a in ("-u", "--unset", "-C", "--chdir") and argv:
                    argv.pop(0)
        elif name in WRAPPERS:
            argv.pop(0)
            while argv and argv[0].startswith("-"):
                a = argv.pop(0)
                if a == "--":
                    break
                if a in SKIP_VALUE.get(name, ()) and argv:
                    argv.pop(0)
            if name == "timeout" and argv:
                argv.pop(0)  # the duration
            if name == "flock" and argv:
                argv.pop(0)  # the lock file
        elif name == "uv" and "run" in argv[1:3]:
            argv = argv[argv.index("run") + 1 :]
            while argv and argv[0].startswith("-"):
                a = argv.pop(0)
                if a == "--":
                    break
                if a in UV_RUN_VALUE and argv:
                    argv.pop(0)
        elif PY.match(name):
            i = 1
            while i < len(argv) and argv[i].startswith("-") and argv[i] not in ("-", "-c") and argv[i][:2] != "-m":
                i += 2 if argv[i] in ("-X", "-W") else 1
            if i < len(argv) and argv[i] == "-m" and i + 1 < len(argv):
                argv = argv[i + 1 :]
            elif i < len(argv) and argv[i].startswith("-m") and len(argv[i]) > 2:
                argv = [argv[i][2:], *argv[i + 1 :]]
            else:
                break
        else:
            break
    return argv


class Checker:
    def __init__(self, cwd: str, read_only: str = "", agent: str | None = None):
        # #528: the subagent's type, plugin prefix dropped; None in the main (lead) session. VERIFIED on a real
        # PreToolUse input: a subagent's carries agent_type "<plugin>:<name>", the main session's has none.
        self.agent = agent
        self.fed: list[str] = []  # the text earlier stages of the current pipeline write to this command's stdin
        self.later: list[str] = []  # the command names of the current pipeline's later stages
        self.vars: dict[str, str] = {}  # literal values the command assigns, expanded where words use them
        self.transforms = False  # some stage rewrites text (base64 -d, rev, sed, ...): code built from it is opaque
        self.read_only = read_only  # a read-only agent's own `dev` report (READ_ONLY_AGENTS); "": not read-only
        self.cwd = cwd
        self.start = cwd  # the session's worktree; a `cd ..` inside the command does not move it
        self.in_python = 0  # > 0 while checking a Python string literal as a shell command
        self.lit_vars: set[str] = set()  # variables the command sets to a literature-API URL
        self.lit_raw = owner_env(LIT_RAW, cwd) == "1"

    def text(self, text: str, depth: int = 0) -> str | None:
        """Check a shell command string; return the reason it is blocked, or None."""
        if depth > MAX_DEPTH:
            return DEEP
        pipes = lex(text)
        self.lit_vars |= lit_vars(pipes)
        self.vars.update(assigned(pipes))
        if TOKEN_NAMES & set(self.vars.values()):  # x=GH_TOKEN; echo ${!x}
            return GH_CRED
        names = [[base(a[0]) for a in [unwrap([w.value for w in c.words], [])] if a] for p in pipes for c in p]
        self.transforms = self.transforms or any(n and n[0] in TRANSFORMERS for n in names)
        for pipe in pipes:
            cmds = [c for c in pipe if c.words or c.stdin or c.reads or c.writes]
            for k, c in enumerate(cmds):
                self.fed = [t for p in cmds[:k] for t in [b for b, _ in p.stdin] + [w.value for w in p.words]]
                self.later = [base(p.words[0].value) for p in cmds[k + 1 :] if p.words]
                if k and c.words and base(c.words[0].value) == "xargs" and self.feeds_cred_paths(c, cmds[:k]):
                    return GH_CRED
                if why := self.simple(c, depth):
                    return why
                if (lang := self.stdin_lang(c)) and k:  # earlier stages of the pipeline feed this interpreter
                    if any(base(w.value) in TRANSFORMERS for prev in cmds[:k] for w in prev.words[:1]):
                        return OPAQUE
                    for prev in cmds[:k]:
                        for chunk in [b for b, _ in prev.stdin] + [w.value for w in prev.words[1:]]:
                            if why := self.code(chunk, lang, depth):
                                return why
        return None

    def feeds_cred_paths(self, c: Simple, earlier: list[Simple]) -> bool:
        """`du -a ~ | xargs grep x`: xargs runs a reader on paths listed from a directory holding a credential store."""
        inner = unwrap([w.value for w in c.words], [])
        if inner and base(inner[0]) in CRED_OK:
            return False
        roots = [w.value for p in earlier for w in p.words[1:] if not w.value.startswith("-")]
        return any(holds_cred(r, self.cwd) for r in roots)

    def code(self, chunk: str, lang: str, depth: int) -> str | None:
        if lang == "sh":
            return self.text(chunk, depth + 1)
        return self.python(chunk, depth)

    def python(self, src: str, depth: int) -> str | None:
        """Python source fed to an interpreter: its string literals may be shell commands or paths."""
        literals = re.findall(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"", src)
        strings = [a or b for a, b in literals]
        for group in re.findall(r"\[([^\]]*)\]", src):  # list-form argv: ["pip", "install", "x"]
            parts = re.findall(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"", group)
            strings.append(" ".join(a or b for a, b in parts))
        if why := self.code_reaches(src, "py"):
            return why
        self.in_python += 1
        try:
            for s in strings:
                if is_env(Word(s)):
                    return ENV
                if is_secrets(Word(s)):
                    return SECRETS
                if s in TOKEN_NAMES or cred_path(s, self.cwd):  # os.environ["GH_TOKEN"], open(".../hosts.yml")
                    return GH_CRED
                if why := self.text(s, depth + 1):
                    return why
            return None
        finally:
            self.in_python -= 1

    def stdin_lang(self, c: Simple) -> str | None:
        """'sh' or 'py' when this command runs code read from stdin."""
        argv = unwrap([w.value for w in c.words], [])
        if not argv:
            return None
        name, args = base(argv[0]), argv[1:]
        if name in SHELLS:
            i = 0
            while i < len(args) and args[i].startswith(("-", "+")):
                if "c" in args[i].lstrip("-+") and not args[i].startswith("--"):
                    return None
                i += 2 if args[i] in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file") else 1
            return "sh" if i == len(args) or args[i] == "-s" else None
        if PY.match(name):
            rest = [a for a in args if not a.startswith("-")]
            return "py" if (not rest or args[-1] == "-") and "-c" not in args else None
        if name in ("source", ".") and args and args[0] in ("/dev/stdin", "-"):
            return "sh"
        return None

    def simple(self, c: Simple, depth: int) -> str | None:
        fed, self.fed = self.fed, []
        if self.vars and any("$" in w.value for w in c.words):  # b=main; git push origin $b
            c = self.expanded(c)
        assigns: list[str] = []
        argv = unwrap([w.value for w in c.words], assigns)
        name = base(argv[0]) if argv else ""
        text_opts = MESSAGE_OPTS if name in ("git", "gh") else set()  # grep -b is a flag, not a message
        paths = [w for k, w in enumerate(c.words) if not (k and c.words[k - 1].value in text_opts)]
        if name in GREPS and (k := grep_pattern(argv)) is not None:
            pattern = len(c.words) - len(argv) + k  # unwrap only strips words from the front
            paths = [w for j, w in enumerate(c.words) if j != pattern]
        env_ok = name in ENV_OK_CMDS or (name == "git" and "check-ignore" in argv[1:4])
        in_py = self.in_python > 0
        if (any(is_env(w, in_py) for w in paths) and not env_ok) or any(is_env(Word(r), in_py) for r in c.reads):
            return ENV
        if (any(is_secrets(w) for w in paths) and not env_ok) or any(is_secrets(Word(r)) for r in c.reads):
            return SECRETS
        if why := self.gh_cred(c, argv, paths, name in CRED_OK) or self.reaches(c, argv, fed):
            return why
        exported = argv[1:] if name in ("export", "declare", "typeset", "local", "readonly") else []
        if any(HOOK_VAR.match(a) or "hookspath" in a.lower() for a in exported + (assigns if not argv else [])):
            return HOOKS
        raw = [w.value for w in c.words]
        for k, v in enumerate(raw):
            if base(v) in C_RUNNERS:
                for j in range(k + 1, len(raw)):
                    code = raw[j].split("=", 1)[1] if raw[j].startswith("--command=") else None
                    if code is None and raw[j] in ("-c", "--command") and j + 1 < len(raw):
                        code = raw[j + 1]
                    if code is not None and (why := self.text(code, depth + 1)):
                        return why
        if lang := self.stdin_lang(c):
            for body, _ in c.stdin:
                if why := self.code(body, lang, depth):
                    return why
        if why := self.owner_setting(argv, c.writes, paths):
            return why
        if self.read_only and (why := read_only(argv, c.writes, self.read_only, tuple(assigns))):
            return why
        if not argv:
            return None
        args = argv[1:]
        if name in ("cd", "pushd") and args:
            self.cwd = os.path.join(self.cwd, os.path.expanduser(args[-1]))
        if name == "trap" and args:
            code = next((a for a in args if not a.startswith("-")), "")
            if why := self.text(code, depth + 1):
                return why
        if name in SHELLS:
            for i, a in enumerate(args):
                if a.startswith("-") and not a.startswith("--") and "c" in a and i + 1 < len(args):
                    return self.text(args[i + 1], depth + 1)
        if name == "eval":
            return self.text(" ".join(args), depth + 1)
        if name == "watch":
            return self.text(" ".join(a for a in args if not a.startswith("-")), depth + 1)
        if PY.match(name) and "-c" in args and args.index("-c") + 1 < len(args):
            return self.python(args[args.index("-c") + 1], depth)
        if name == "find":
            for i, a in enumerate(args):
                if a in ("-exec", "-execdir", "-ok", "-okdir"):
                    inner = []
                    for b in args[i + 1 :]:
                        if b in (";", "+"):
                            break
                        inner.append(Word(b))
                    if why := self.simple(Simple(inner), depth + 1):
                        return why
        if re.fullmatch(r"pip[0-9.]*", name) and {"install", "uninstall"} & set(args):
            return PIP
        if name == "uv":
            rest = [a for a in args if not a.startswith("-")]
            if rest[:1] == ["pip"] and {"install", "uninstall", "sync"} & set(rest):
                return PIP
        if name in FETCHERS and f"{LIT_RAW}=1" not in assigns and not self.lit_raw:
            for a in args:
                a = a.split("=", 1)[1] if a.startswith("--") and "=" in a else a  # --url=<url>
                if LIT_API.match(a) or ((m := LIT_VAR.match(a)) and m.group(1) in self.lit_vars):
                    return LIT
        if name == "pkill" and any(a == "--full" or (re.match(r"-[^-]", a) and "f" in a) for a in args):
            return PKILL
        if name == "gh":
            return self.gh(args, [b for b, _ in c.stdin] + fed)
        if name in ("dev", "devkit.cli") and args[:1] in (["board"], ["pr"]):
            return self.forged(args + [b for b, _ in c.stdin] + fed)
        if name == "git":
            return self.git(args, assigns)
        if name == "rm":
            return self.rm(args)
        return None

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
        if group == "pr" and sub == "review" and self.agent not in REVIEWERS:
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
            if GQL_REVIEW.search(text) and self.agent not in REVIEWERS:
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
        if GH_REVIEW_API.match(endpoint) and self.agent not in REVIEWERS:
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
        others = set(AGENT_NAMED.findall(text)) & REVIEWERS - {self.agent}  # a reviewer passing as the other one
        return REVIEW if self.agent not in REVIEWERS or others else None

    def owner_setting(self, argv: list[str], writes: list[str], paths: list[Word]) -> str | None:
        """The denial when the command sets one of OWNER_SETTINGS: `dev quota-cap <pct>|--clear` (or gate-slots), or
        touches its file other than to read it (a `cd` earlier in the command counts, so `cd .git/devkit && echo 99 >
        quota_cap` too)."""
        name, args = (base(argv[0]), argv[1:]) if argv else ("", [])
        for command, path, why in OWNER_SETTINGS:
            if name in ("dev", "devkit.cli") and args[:1] == [command] and not set(args[1:]) <= {"-h", "--help"}:
                return why

            def owned(v: str, path: str = path) -> bool:
                return path in v or os.path.normpath(os.path.join(self.cwd, v)).endswith("/" + path)

            if any(owned(w) for w in writes):
                return why
            if name not in CAP_READERS and any(owned(w.value) for w in paths):
                return why
        return None

    def git(self, args: list[str], assigns: list[str]) -> str | None:
        configs, where, i = [*assigns], self.cwd, 0
        while i < len(args) and args[i].startswith("-"):
            a = args[i]
            if a in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env") and i + 1 < len(args):
                if a == "-C":
                    where = os.path.join(where, args[i + 1])
                configs.append(args[i + 1])
                i += 2
            else:
                configs.append(a)
                i += 1
        if any("hookspath" in x.lower() for x in configs) or any(HOOK_VAR.match(x) for x in assigns):
            return HOOKS
        if i >= len(args):
            return None
        sub, rest = args[i], args[i + 1 :]
        if sub == "credential" or sub.startswith("credential-"):  # `git credential fill` prints the stored token
            return GH_CRED
        no_index = sub == "grep" and "--no-index" in rest  # searches any directory, hidden files included
        if no_index and any(holds_cred(a, where) for a in rest if not a.startswith("-")):
            return GH_CRED
        if sub == "config" and any("hookspath" in a.lower() for a in rest) and not config_reads_only(rest):
            return HOOKS
        if any(long_opt(a, "--no-verify", 9) for a in rest):
            return HOOKS
        if sub == "commit" and commit_skips_hooks(rest):
            return HOOKS
        if sub == "rebase":  # -x / --exec run a shell command after each commit
            for k, a in enumerate(rest):
                code = a.split("=", 1)[1] if a.startswith("--exec=") else a[2:] if a.startswith("-x") else None
                if code == "" and k + 1 < len(rest):
                    code = rest[k + 1]
                if code is None and a == "--exec" and k + 1 < len(rest):
                    code = rest[k + 1]
                if code and (why := self.text(code, 1)):
                    return why
        if sub == "push":
            return self.push(rest, where)
        return destructive_git(sub, rest, where)

    def rm(self, args: list[str]) -> str | None:
        shorts, longs, pos = parse_opts(args)
        if not ({"r", "R"} & shorts or any(long_opt(o, "--recursive", 3) for o in longs)):
            return None
        import tempfile

        root = worktree_root(self.start)
        temp = os.path.realpath(tempfile.gettempdir())
        for t in pos:
            path = os.path.expandvars(os.path.expanduser(t))
            if "$" in path:  # a variable the shell sets: the target cannot be checked here
                continue
            full = os.path.realpath(os.path.join(self.cwd, path))
            if root not in ("/", os.path.realpath(os.path.expanduser("~"))) and within(full, root):
                if full != root and ".git" not in os.path.relpath(full, root).split(os.sep):
                    continue
            elif within(full, temp) and not within(root, temp):
                continue
            return RM
        return None

    def push(self, rest: list[str], where: str) -> str | None:
        pos, skip, tags_only = [], False, False
        for k, a in enumerate(rest):
            if skip:
                skip = False
            elif a == "--":
                pos += rest[k + 1 :]
                break
            elif a.startswith("--"):
                opt = a.split("=", 1)[0]
                if opt == "--force":
                    return FORCE
                if long_opt(opt, "--delete", 4) or opt == "--prune":
                    return REF_DELETE
                if long_opt(opt, "--mirror", 4) or long_opt(opt, "--all", 4) or long_opt(opt, "--branches", 4):
                    return MAIN
                tags_only = tags_only or opt == "--tags"
                skip = "=" not in a and opt in ("--repo", "--push-option", "--receive-pack", "--exec")
            elif a.startswith("-") and len(a) > 1:
                if "f" in a.split("o", 1)[0]:
                    return FORCE
                if "d" in a.split("o", 1)[0]:
                    return REF_DELETE
                skip = a.endswith("o")
            else:
                pos.append(a)
        refspecs = pos[1:]
        for r in refspecs:
            if r.startswith("+"):
                return FORCE
            if r.startswith(":"):  # an empty source deletes the destination
                return REF_DELETE
            dst = r.split(":")[-1].removeprefix("refs/").removeprefix("heads/")
            if dst in ("HEAD", "@"):
                dst = current_branch(where) or ""
            if dst in ("main", "master") or "*" in dst:  # a wildcard refspec can land on main
                return MAIN
        if not refspecs and not tags_only and current_branch(where) in ("main", "master"):
            return MAIN
        return None


def grep_pattern(argv: list[str]) -> int | None:
    """Index of grep's pattern argument (text, not a path), or None when -e / -f give the pattern instead."""
    k = 1
    while k < len(argv):
        a = argv[k]
        if a in ("-e", "-f", "--regexp", "--file") or a.startswith(("--regexp=", "--file=")):
            return None
        if a == "--":
            return k + 1 if k + 1 < len(argv) else None
        if not a.startswith("-") or a == "-":
            return k
        k += 2 if a in GREP_VALUE else 1
    return None


COMMIT_VALUE = {"--message", "--file", "--reuse-message", "--reedit-message", "--template", "--author", "--date"}
COMMIT_VALUE |= {"--fixup", "--squash", "--trailer", "--cleanup", "--pathspec-from-file"}


def config_reads_only(rest: list[str]) -> bool:
    """True when `git config <rest>` can only read: a get/list action, or a bare name. Unknown flags count as writes."""
    names, flags, i = [], set(), 0
    while i < len(rest):
        a = rest[i]
        opt = a.split("=", 1)[0]
        if opt in CONFIG_VALUE_OPTS:
            i += 1 if "=" in a else 2
            continue
        if a.startswith("-"):
            if opt not in CONFIG_READ_FLAGS | CONFIG_READ_ACTIONS:
                return False
            flags.add(opt)
        else:
            names.append(a)
        i += 1
    if names[:1] in (["get"], ["list"]):
        return True
    if names[:1] and names[0] in ("set", "unset", "rename-section", "remove-section", "edit"):
        return False
    return bool(flags & CONFIG_READ_ACTIONS) or len(names) == 1


def commit_skips_hooks(rest: list[str]) -> bool:
    """git commit -n is --no-verify; -m, -F, -c, -C and -t take a value, as the rest of the cluster or the next arg."""
    skip = False
    for a in rest:
        if skip:
            skip = False
            continue
        if a == "--":
            break
        if a in COMMIT_VALUE:  # --message -n: "-n" is the message
            skip = True
        elif a.startswith("-") and not a.startswith("--"):
            for j, ch in enumerate(a[1:]):
                if ch == "n":
                    return True
                if ch in "mFcCt":
                    skip = j == len(a) - 2
                    break
    return False


def parse_opts(args: list[str], valued: str = "") -> tuple[set[str], set[str], list[str]]:
    """Split args into short flag letters, long option names and positionals (all of them after `--`).
    `valued`: short options whose value is the rest of the cluster or the next word."""
    shorts: set[str] = set()
    longs: set[str] = set()
    pos: list[str] = []
    skip = False
    for k, a in enumerate(args):
        if skip:
            skip = False
        elif a == "--":
            pos += args[k + 1 :]
            break
        elif a.startswith("--"):
            longs.add(a.split("=", 1)[0])
        elif a.startswith("-") and len(a) > 1:
            for j, ch in enumerate(a[1:], 1):
                shorts.add(ch)
                if ch in valued:
                    skip = j == len(a) - 1
                    break
        else:
            pos.append(a)
    return shorts, longs, pos


def destructive_git(sub: str, rest: list[str], where: str) -> str | None:
    """The git subcommands that throw away uncommitted work, stashes, worktrees or unmerged branches."""
    shorts, longs, pos = parse_opts(rest, {"checkout": "bB", "switch": "cC", "restore": "s", "clean": "e"}.get(sub, ""))

    def has(letter: str, name: str, shortest: int) -> bool:
        return letter in shorts or any(long_opt(o, name, shortest) for o in longs)

    def whole_tree() -> bool:
        return any(p in WHOLE_TREE or os.path.isdir(os.path.join(where, p)) for p in pos)

    if sub == "reset" and any(long_opt(o, "--hard", 4) or long_opt(o, "--merge", 4) for o in longs):
        return RESET
    if sub == "checkout" and (has("f", "--force", 4) or whole_tree()):
        return DISCARD
    if sub == "switch" and (has("f", "--force", 4) or any(long_opt(o, "--discard-changes", 3) for o in longs)):
        return DISCARD
    if sub == "restore" and (has("W", "--worktree", 3) or not has("S", "--staged", 4)) and whole_tree():
        return DISCARD
    if sub == "clean" and has("f", "--force", 3) and not has("n", "--dry-run", 3):
        return CLEAN
    if sub == "stash" and pos[:1] in (["drop"], ["clear"]):
        return STASH
    if sub == "worktree" and pos[:1] == ["remove"] and has("f", "--force", 3):
        return WORKTREE
    if sub == "branch" and ("D" in shorts or (has("d", "--delete", 4) and has("f", "--force", 3))):
        return BRANCH_D
    return None


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


def within(path: str, top: str) -> bool:
    return path == top or path.startswith(top.rstrip(os.sep) + os.sep)


def worktree_root(cwd: str) -> str:
    """The top of the worktree holding cwd, or cwd itself outside a repository."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return os.path.realpath(out or cwd)


def current_branch(where: str) -> str | None:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", where, "symbolic-ref", "--short", "-q", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


# Read-only agents (the PreToolUse input's `agent_type`, plugin prefix dropped), each with the one `dev` report it may
# run besides `dev board|code`: the consolidation review (#113) and the suite audit (#114) propose, builders execute, so
# a stray write can never delete progress or a test. Allowlist, not denylist: unknown means no. The suite-auditor gets
# `suite-audit status` and `report --quick` only: a full report runs the suite (also in the working tree), and
# `--file` edits and closes issues; both stay with the lead. `dev` refuses abbreviated options, so `--fi` is no way in.
READ_ONLY_AGENTS = {"architect": "consolidation", "suite-auditor": "suite-audit"}
READ_ONLY = (
    "this agent is read-only: it reads, measures and files issues. Allowed: read commands (cat, grep, ls, "
    "sed -n, ...), `git log|show|diff|status`, `gh issue view|list|create|comment`, `gh pr view|list|diff`, GET-only "
    "`gh api`, `uv run dev board|code`, and its own report (architect: `dev consolidation`; suite-auditor: "
    "`dev suite-audit status` or `dev suite-audit report --quick`). File an issue for a builder instead."
)


def quick_audit(args: list[str]) -> bool:
    """`suite-audit status`, or `report --quick` with no --file (nor a prefix of it): reruns nothing, files nothing."""
    if any(len(a) > 2 and "--file".startswith(a.split("=", 1)[0]) for a in args):
        return False
    return args[:1] == ["status"] or (args[:1] == ["report"] and "--quick" in args)


READ_CMDS = {"cat", "head", "tail", "wc", "ls", "grep", "egrep", "fgrep", "rg", "sort", "uniq", "cut", "tr", "jq"}
READ_CMDS |= {"echo", "printf", "pwd", "stat", "file", "diff", "cmp", "tree", "du", "df", "basename", "dirname"}
READ_CMDS |= {"realpath", "readlink", "test", "[", "[[", "true", "false", "column", "nl", "date", "which", "cd"}
READ_GIT = {"log", "show", "diff", "status", "blame", "grep", "ls-files", "ls-tree", "rev-parse", "cat-file"}
READ_GIT_GLOBAL = {"-C", "--no-pager", "-P", "--git-dir", "--work-tree"}  # no -c: core.fsmonitor, alias.x=!cmd run code
READ_GH = {"issue": {"view", "list", "create", "comment", "status"}, "pr": {"view", "list", "diff", "checks", "status"}}
READ_GH |= {"search": {"issues", "prs", "code", "repos", "commits"}}
READ_DEV = {"board", "code", "--help", "-h"}
# Options of allowed read commands that run a program or write a file (#145): (short letters that do, short letters
# that take a value and so end a cluster, long options; git-style prefixes of 3+ characters count).
RISKY_OPTS = {
    "sort": ("o", "ktST", {"--output", "--compress-program"}),
    "tree": ("o", "LPIHT", set()),
    "rg": ("", "", {"--pre"}),
    "file": ("C", "mPeFf", {"--compile"}),
    "date": ("s", "dfIr", {"--set"}),
    "git grep": ("O", "efABCm", {"--open-files-in-pager"}),
}
# Environment variables a read-only agent may set: the rest (GIT_CONFIG_*, GIT_EXTERNAL_DIFF, LD_PRELOAD, ...) can run
# code. A pager may only be turned off.
SAFE_VARS = {"NO_COLOR", "TERM", "COLUMNS", "LINES", "LANG", "LC_ALL", "TZ"}
SAFE_PAGERS = {"GIT_PAGER=", "GIT_PAGER=cat", "PAGER=", "PAGER=cat"}
SINKS = {"/dev/null", "/dev/stdout", "/dev/stderr", "-"}


def risky(name: str, args: list[str]) -> bool:
    """An option in RISKY_OPTS, or a second file operand for uniq (its output file)."""
    if name == "uniq":
        valued, files, i = ("-f", "-s", "-w"), 0, 0
        while i < len(args):
            files += not args[i].startswith("-") or args[i] == "-"
            i += 2 if args[i] in valued else 1
        return files > 1
    letters, valued, longs = RISKY_OPTS.get(name, ("", "", set()))
    for a in args:
        if a == "--":
            break
        if a.startswith("--"):
            if any(long_opt(a, opt, 3) for opt in longs):
                return True
        elif a.startswith("-"):
            for ch in a[1:]:
                if ch in letters:
                    return True
                if ch in valued:
                    break
    return False


def read_only(argv: list[str], writes: list[str], report: str = "", assigns: tuple[str, ...] = ()) -> str | None:
    """READ_ONLY unless the command only reads, files issues through `dev board` / `gh`, or runs the agent's own
    `dev <report>` (READ_ONLY_AGENTS)."""
    if any(not (w.isdigit() or w in SINKS) for w in writes):
        return READ_ONLY
    if any(a.split("=", 1)[0] not in SAFE_VARS and a not in SAFE_PAGERS for a in assigns):
        return READ_ONLY
    if not argv:
        return None
    name, args = base(argv[0]), argv[1:]
    if name == "sed":
        if any(a.startswith(("-i", "--in-place")) for a in args):
            return READ_ONLY
        scripts = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-e"] or [
            next((a for a in args if not a.startswith("-")), "")
        ]
        return None if all(re.fullmatch(r"[0-9,$;p ]*", s) for s in scripts) else READ_ONLY
    if name == "find":
        bad = {"-delete", "-fprint", "-fprint0", "-fprintf", "-fls", "-ok", "-okdir"}
        return READ_ONLY if bad & set(args) else None  # -exec's command is checked on its own
    if name in READ_CMDS:
        return READ_ONLY if risky(name, args) else None
    if name == "git":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            if args[i] not in READ_GIT_GLOBAL:
                return READ_ONLY
            i += 2 if args[i] in ("-C", "--git-dir", "--work-tree") else 1
        rest = args[i:]
        ok = rest and rest[0] in READ_GIT and not any(a.startswith(("--output", "-o")) for a in rest[1:])
        ok = ok and not (rest[0] == "grep" and risky("git grep", rest[1:]))
        return None if ok else READ_ONLY
    if name == "gh":
        if args[:1] == ["api"]:
            write = {"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"}
            return READ_ONLY if any(a.split("=", 1)[0] in write for a in args) else None
        return None if len(args) > 1 and args[1] in READ_GH.get(args[0], ()) else READ_ONLY
    if name == "dev" and args[:1] and args[0] in READ_DEV:
        return None
    if name == "dev" and args[:1] == [report] and (report != "suite-audit" or quick_audit(args[1:])):
        return None
    return READ_ONLY


# #381: commands that can wait forever. An agent's poll loop whose file is never written, or whose `pgrep -f` matches
# its own shell, otherwise outlives the agent as a stale shell. Such a command is rewritten, not refused, to run under
# `timeout <wait_bound_s>`. WAIT_HINT is the cheap test every command takes; only a hit is lexed.
WAIT_HINT = re.compile(r"\b(?:while|until|tail|watch|sleep|wait|inotifywait|flock|follow|journalctl|logs)\b")
WAIT_BOUND = 3600  # [tool.devkit] wait_bound_s; devkit/config.py documents it
BOUND_KEY = re.compile(r"(?m)^\s*wait_bound_s\s*=\s*(\d+)")
FOLLOWERS = {"docker", "podman", "kubectl", "oc", "nerdctl"}  # `<tool> [compose] logs -f`
STARTERS = {"nohup", "nice", "ionice", "setsid", "stdbuf", "exec", "command", "builtin", "time", "sudo", "doas", "env"}
SLEEP_ARG = re.compile(r"^(\d+(?:\.\d*)?|\.\d+)([smhd]?)$")
UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
PGREP_F = "`pgrep -f` may match this shell's own command line, so the loop never ends; prefer `pgrep -x` or a pid."


def short_flag(args: list[str], letters: str, longs: tuple[str, ...]) -> bool:
    """A flag given as one of `letters` (alone or in a cluster such as -fn) or one of the long options."""
    for a in args:
        if a == "--":
            return False
        if a.startswith("--"):
            if a.split("=", 1)[0] in longs:
                return True
        elif a.startswith("-") and any(ch in letters for ch in a[1:]):
            return True
    return False


def sleep_s(args: list[str]) -> float | None:
    total = 0.0
    for a in args:
        if a.startswith("inf"):
            return float("inf")
        m = SLEEP_ARG.match(a)
        if not m:
            return None  # $N, $((...)): not knowable here
        total += float(m.group(1)) * UNIT[m.group(2)]
    return total


def waits_forever(argv: list[str], bound: int) -> str | None:
    """What makes this one command (wrappers already stripped) wait without end, or None."""
    name, args = base(argv[0]), argv[1:]
    if name == "tail" and short_flag(args, "fF", ("--follow", "--retry")):
        return "tail -f"
    if name == "watch":
        return "watch"
    if name == "sleep" and (s := sleep_s(args)) is not None and s > bound:
        return f"sleep {' '.join(args)}"
    if name == "wait":
        return "wait"
    if name == "inotifywait" and (short_flag(args, "m", ("--monitor",)) or not short_flag(args, "t", ("--timeout",))):
        return "inotifywait without -t"
    if name == "flock" and not short_flag(args, "wnu", ("--wait", "--timeout", "--nonblock", "--unlock")):
        return "flock without -w"
    if name == "gh" and (args[:2] == ["run", "watch"] or (args[:2] == ["pr", "checks"] and "--watch" in args)):
        return f"gh {' '.join(args[:2])}"
    if name in FOLLOWERS and "logs" in args[:3] and short_flag(args, "f", ("--follow",)):
        return f"{name} logs -f"
    if name == "journalctl" and short_flag(args, "f", ("--follow",)):
        return "journalctl -f"
    return None


def strip_starters(argv: list[str]) -> list[str]:
    """Drop assignments and the wrappers that run a command without bounding it (nohup, nice, env, sudo ...)."""
    while argv and (base(argv[0]) in STARTERS or ASSIGN.match(argv[0])):
        name, argv = base(argv[0]), argv[1:]
        while name in STARTERS and argv and argv[0].startswith("-"):
            a, argv = argv[0], argv[1:]
            if a == "--":
                break
            if a in SKIP_VALUE.get(name, ()) and argv:
                argv = argv[1:]
    return argv


def unbounded(text: str, bound: int, depth: int = 0) -> tuple[list[str], bool]:
    """The waits in a shell command that nothing bounds, and whether it polls with `pgrep -f`.

    A wait under `timeout` is bounded. A `while read` loop ends with its input. Shell code passed with -c (or flock -c)
    or fed to a shell by a heredoc is searched too, unless a timeout wraps that shell."""
    found: list[str] = []
    pgrep_f = False
    if depth > MAX_DEPTH or not WAIT_HINT.search(text):
        return found, pgrep_f
    for pipe in lex(text):
        for c in pipe:
            words = list(c.words)
            loop = False
            while words and ((not words[0].quoted and words[0].value in KEYWORDS) or ASSIGN.match(words[0].value)):
                loop = loop or (words[0].value in ("while", "until") and not words[0].quoted)
                words.pop(0)
            argv = [w.value for w in words]
            argv = strip_starters(argv)
            if argv and base(argv[0]) == "pgrep" and short_flag(argv[1:], "f", ("--full",)):
                pgrep_f = True
            if loop and not (argv and base(argv[0]) == "read"):
                found.append("until/while loop")
            if not argv or base(argv[0]) == "timeout":
                continue
            if why := waits_forever(argv, bound):
                found.append(why)
            if base(argv[0]) not in SHELLS | {"flock"}:
                continue
            code = [body for body, _ in c.stdin] if base(argv[0]) in SHELLS else []  # bash <<'EOF' ... EOF
            for k, a in enumerate(argv[1:-1], 1):
                if a in ("-c", "--command") or (a.startswith("-") and not a.startswith("--") and "c" in a):
                    code = [argv[k + 1]]
                    break
            for chunk in code:
                more, pf = unbounded(chunk, bound, depth + 1)
                found += more
                pgrep_f = pgrep_f or pf
    return found, pgrep_f


def wait_bound(cwd: str) -> int:
    """[tool.devkit] wait_bound_s from the nearest pyproject.toml with a [tool.devkit] table above cwd (the worktree's
    own config), else above $CLAUDE_PROJECT_DIR, else the default.

    Read only for a command WAIT_HINT matches. A plain scan, not tomllib: hooks run on the host's python3 (3.10)."""
    for start in (cwd, os.environ.get("CLAUDE_PROJECT_DIR", "")):
        d = os.path.abspath(start) if start else ""
        while d:
            try:
                with open(os.path.join(d, "pyproject.toml"), encoding="utf-8") as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError):
                text = ""
            if (at := text.find("[tool.devkit]")) >= 0:
                table = re.split(r"(?m)^\s*\[", text[at + len("[tool.devkit]") :], maxsplit=1)[0]
                m = BOUND_KEY.search(table)
                return int(m.group(1)) if m else WAIT_BOUND
            d = "" if os.path.dirname(d) == d else os.path.dirname(d)
    return WAIT_BOUND


def bound_waits(cmd: str, cwd: str) -> tuple[str, str] | None:
    """(the rewritten command, the note for the agent) when the command waits without a bound, else None."""
    if not WAIT_HINT.search(cmd) or (bound := wait_bound(cwd)) <= 0:
        return None
    found, pgrep_f = unbounded(cmd, bound)
    if not found:
        return None
    body, tail = cmd.strip(), ""
    if body.endswith("&") and not body.endswith(("&&", "\\&", ">&", "<&")):
        body, tail = body[:-1].rstrip(), " &"  # a backgrounded wait: bound it in the background
    new = f"timeout {bound} bash -c {shlex.quote(body)}{tail}"
    what = ", ".join(dict.fromkeys(found))
    note = (
        f"guard-bash: this command can wait forever ({what}), so it runs as `timeout {bound} bash -c '...'` "
        f"([tool.devkit] wait_bound_s). Exit 124 means the bound ran out. Put a shorter `timeout <s>` in front "
        f"yourself to choose the bound."
    )
    return new, note + (" " + PGREP_F if pgrep_f else "")


def main(raw: str | None = None) -> int:
    """`raw`: the hook input file_guard.py already read from stdin."""
    try:
        data = json.loads(raw) if raw is not None else json.load(sys.stdin)
        cmd = (data.get("tool_input") or {}).get("command", "")
        url = (data.get("tool_input") or {}).get("url", "") if data.get("tool_name") == "WebFetch" else ""
    except (json.JSONDecodeError, AttributeError):
        return 0
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else os.getcwd()
    if isinstance(url, str) and LIT_API.match(url.strip()) and owner_env(LIT_RAW, cwd) != "1":
        print(f"Blocked by project rule: {LIT}", file=sys.stderr)
        return 2
    if data.get("tool_name") in ("Read", "Grep"):  # #601: the file tools reach the same secrets
        inp = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
        path = inp.get("file_path") or inp.get("path") or ""
        why = None
        if isinstance(path, str) and path:
            w = Word(path, glob=any(ch in path for ch in "*?["))
            why = ENV if is_env(w) else SECRETS if is_secrets(w) else GH_CRED if cred_path(path, cwd) else None
        glob = inp.get("glob")
        hint = glob if isinstance(glob, str) else ""
        if not why and (cred_path(hint, cwd, glob=True) or "hosts.yml" in hint or ".ssh/" in hint):
            why = GH_CRED
        if not why and data.get("tool_name") == "Grep" and holds_cred(path or cwd, cwd):
            why = GREP_ABOVE  # Grep searches hidden files: a folder above the store reaches it
        if why:
            print(f"Blocked by project rule: {why}", file=sys.stderr)
            return 2
        return 0
    if not isinstance(cmd, str):
        return 0
    agent = data.get("agent_type") if isinstance(data.get("agent_type"), str) else ""  # set inside a subagent
    try:
        kind = agent.rsplit(":", 1)[-1]
        why = Checker(cwd, read_only=READ_ONLY_AGENTS.get(kind, ""), agent=kind if agent else None).text(cmd)
    except RecursionError:  # hundreds of nested substitutions: nobody writes that by hand
        why = DEEP
    if why:
        print(f"Blocked by project rule: {why}", file=sys.stderr)
        return 2
    if data.get("tool_name", "Bash") == "Bash" and (bounded := bound_waits(cmd, cwd)):
        new, note = bounded
        inp = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
        out = {"hookEventName": "PreToolUse", "updatedInput": {**inp, "command": new}, "additionalContext": note}
        print(json.dumps({"hookSpecificOutput": out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
