"""Python fed to an interpreter (#690): which of its words are code and which sit inside string literals or comments,
and whether it can start a process at all. The words alone refused scripts that only edit text, such as
`s.replace('os.system(base64.b64decode(x))', ...)`. Stdlib only; `ast` and `tokenize` are imported on first use."""

import re

# Dispatch by computed name: what it calls cannot be read.
DYNAMIC = {"getattr", "__import__", "globals", "locals", "vars", "setattr"}
DYNAMIC_MODULES = {"importlib", "operator", "builtins", "ctypes", "cffi"}
DUNDER_OK = {"__name__", "__file__", "__doc__", "__init__", "__main__"}  # any other dunder reaches internals
# The modules a file-editing script needs, none of which starts a process, runs text or resolves a name held in a
# string (string.Formatter.get_field, typing.get_type_hints, dataclasses' and namedtuple's exec, uuid's subprocess:
# none is here). os only without its process calls (OS_SPAWN). Kept short: a module not listed counts as spawning.
SAFE_IMPORTS = {"pathlib", "re", "json", "textwrap", "sys", "itertools", "difflib", "tomllib", "csv", "glob"}
SAFE_IMPORTS |= {"fnmatch", "io", "shutil", "tempfile", "os", "__future__"}
OS_SPAWN = re.compile(r"^(?:system|popen|exec\w*|spawn\w*|posix_spawn\w*|fork\w*|startfile)$")
# A runner's own name on any object: sys.modules['subprocess'].run, m = subprocess; m.run(...).
RUNNER_LEAF = re.compile(
    r"^(?:system|popen|exec\w*|spawn\w*|posix_spawn\w*|run|call|check_call|check_output|Popen|getoutput"
    r"|getstatusoutput|create_subprocess_\w+|run_path|run_module)$"
)
SPAWN_NAMES = {"exec", "eval", "compile", "breakpoint"}
# The same as words in code, a floor under the tree's reading.
SPAWN_WORDS = re.compile(r"\b(?:subprocess|system|popen|exec\w*|eval|spawn\w*|fork\w*|getattr|__import__|get_field)\b")
F_PREFIX = re.compile(r"[A-Za-z]*[fF]")  # f, rf, Fr...: before 3.12 a whole f-string is one STRING token


def code_only(src: str) -> str | None:
    """src with every plain string literal and comment blanked to spaces (offsets and lines kept); None when it does
    not tokenize, so the caller reads all of it. An f-string stays code, whole, on every Python version: its {...}
    runs, and tokenize splits it only from 3.12 on."""
    import io
    import tokenize

    lines = src.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    out = list(src)
    fstrings = 0  # depth inside FSTRING_START ... FSTRING_END (3.12+)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            kind = tokenize.tok_name[tok.type]
            fstrings += (kind == "FSTRING_START") - (kind == "FSTRING_END")
            if fstrings or kind not in ("STRING", "COMMENT") or (kind == "STRING" and F_PREFIX.match(tok.string)):
                continue
            a, b = starts[tok.start[0] - 1] + tok.start[1], starts[tok.end[0] - 1] + tok.end[1]
            out[a:b] = [c if c == "\n" else " " for c in out[a:b]]
    except (tokenize.TokenError, SyntaxError, IndentationError, IndexError):
        return None
    return "".join(out)


def _parse(src: str):
    import ast

    try:
        return ast, ast.parse(src)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return ast, None


def literals(src: str) -> list[str]:
    """Every str constant in the tree, those nested in f-string code among them; [] when it does not parse."""
    ast, tree = _parse(src)
    if tree is None:
        return []
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def is_code(text: str) -> bool:
    """A literal that parses as Python with a call or an import in it: text timeit, pdb or exec could run."""
    ast, tree = _parse(text)
    return tree is not None and any(isinstance(n, ast.Call | ast.Import | ast.ImportFrom) for n in ast.walk(tree))


def runs_decoded(src: str, decode_words: re.Pattern, run_words: re.Pattern) -> bool:
    """The word rule (a decode word and a run word) with one exemption: code that cannot start a process or run text
    (spawns() is False) whose words of both kinds sit only inside plain string literals or comments, as in a script
    that edits such text. Python can run a literal as code without naming a runner (timeit, pdb, code), so any
    doubt keeps the refusal."""
    if not (decode_words.search(src) and run_words.search(src)):
        return False
    if spawns(src):
        return True
    code = code_only(src)
    return code is None or bool(decode_words.search(code) or run_words.search(code))


def spawns(src: str) -> bool:
    """True unless the code imports only SAFE_IMPORTS, names none of os's process calls, exec or getattr, has no run
    word outside its literals and never passes a module on as a value: only then are its string literals text it
    handles rather than commands it may run."""
    ast, tree = _parse(src)
    if tree is None:
        return True
    if (code := code_only(src)) is None or SPAWN_WORDS.search(code):
        return True
    modules = {(a.asname or a.name.split(".")[0]) for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    bases = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for n in ast.walk(tree):
        word = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else ""
        if word in DYNAMIC or (word.startswith("__") and word.endswith("__") and word not in DUNDER_OK):
            return True
        if isinstance(n, ast.Match | ast.ClassDef):  # a class can hook attribute access, pickling, formatting
            return True
        if isinstance(n, ast.Name) and n.id in modules and id(n) not in bases:
            return True  # [os], f(sys): a module handed on can be searched by name
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] not in SAFE_IMPORTS for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom) and (n.level or (n.module or "").split(".")[0] not in SAFE_IMPORTS):
            return True
        words = [a.name for a in n.names] if isinstance(n, ast.ImportFrom) else [word]  # from os import system as s
        if any(w == "*" or w in SPAWN_NAMES or OS_SPAWN.match(w) for w in words):
            return True
        if isinstance(n, ast.Attribute) and RUNNER_LEAF.match(n.attr):
            return True
    return False
