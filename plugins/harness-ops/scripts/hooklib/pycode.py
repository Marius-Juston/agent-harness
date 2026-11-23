"""Python fed to an interpreter, read as a syntax tree (#690): whether it runs text it decodes, and whether it can start
a process at all. The words alone refused scripts that edit files (`s.replace('b64decode(x)', ...)`, `[::-1]` beside
`re.compile`). Unparsable code is judged by its words, as before. Stdlib only; `ast` is imported on first use."""

import re

CODE_RUNNERS = {"exec", "eval", "compile", "builtins.exec", "builtins.eval", "builtins.compile"}  # run Python text
PROC_RUNNER = re.compile(
    r"^(?:subprocess\.(?:run|call|check_call|check_output|Popen|getoutput|getstatusoutput)"
    r"|os\.(?:system|popen|exec\w*|spawn\w*|posix_spawn\w*)|pty\.spawn|platform\.popen|asyncio\.create_subprocess_\w+"
    r"|runpy\.\w+|(?:sh|pexpect|plumbum)\.[\w.]+)$"
)
# A runner's own name on any object: sys.modules['os'].system, m = subprocess; m.run(...).
RUNNER_LEAF = re.compile(
    r"^(?:system|popen|exec\w*|spawn\w*|posix_spawn\w*|run|call|check_call|check_output|Popen|getoutput"
    r"|getstatusoutput|create_subprocess_\w+|run_path|run_module)$"
)
DECODER = re.compile(
    r"(?:^|\.)(?:\w*b(?:64|32|16|85)decode|a85decode|fromhex|unhexlify|a2b_\w+|decompress|translate|rot_?13"
    r"|reversed)$|^codecs\.(?:de|en)code$"
)
# Methods that hand text to a file or a process's stdin: decoded text staged so, beside a runner, counts as run.
HANDS_ON = {"write", "write_text", "write_bytes", "writelines", "dump", "communicate", "send", "sendline"}
# Dispatch by computed name: what it calls cannot be read, so decoded text beside it counts as run.
DYNAMIC = {"getattr", "__import__", "globals", "locals", "vars", "setattr"}
DYNAMIC_MODULES = {"importlib", "operator", "builtins", "ctypes", "cffi"}
DUNDER_OK = {"__name__", "__file__", "__doc__", "__init__", "__main__"}  # any other dunder reaches internals
# Modules that cannot start a process; os only without its process calls (OS_SPAWN).
SAFE_IMPORTS = {"pathlib", "re", "json", "textwrap", "sys", "collections", "itertools", "functools", "difflib"}
SAFE_IMPORTS |= {"string", "tomllib", "csv", "glob", "fnmatch", "typing", "dataclasses", "datetime", "math", "io"}
SAFE_IMPORTS |= {"hashlib", "base64", "binascii", "shlex", "ast", "tokenize", "random", "bisect", "heapq", "html"}
SAFE_IMPORTS |= {"unicodedata", "enum", "copy", "pprint", "decimal", "fractions", "uuid", "time", "tempfile"}
SAFE_IMPORTS |= {"shutil", "zipfile", "gzip", "zlib", "struct", "codecs", "configparser", "argparse", "os"}
SAFE_IMPORTS |= {"__future__"}
OS_SPAWN = re.compile(r"^(?:system|popen|exec\w*|spawn\w*|posix_spawn\w*|fork\w*|startfile)$")
SPAWN_NAMES = {"exec", "eval", "compile", "breakpoint"}


def _tree(src: str):
    import ast

    try:
        return ast, ast.parse(src)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return ast, None


def _dynamic(ast, n) -> bool:
    """A node whose effect a reader of the text cannot name: getattr, a dunder, `import *`, importlib, a match
    statement (its capture patterns bind names this module does not follow)."""
    word = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else ""
    if word in DYNAMIC or (word.startswith("__") and word.endswith("__") and word not in DUNDER_OK):
        return True
    if isinstance(n, ast.Import | ast.ImportFrom):
        mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""]
        return any(m.split(".")[0] in DYNAMIC_MODULES for m in mods) or any(a.name == "*" for a in n.names)
    return isinstance(n, ast.Match)


def spawns(src: str) -> bool:
    """True unless the code imports only SAFE_IMPORTS and names none of os's process calls, exec or getattr: only then
    are its string literals text it handles rather than commands it may run."""
    ast, tree = _tree(src)
    if tree is None:
        return True
    for n in ast.walk(tree):
        if _dynamic(ast, n):
            return True
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] not in SAFE_IMPORTS for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom) and (n.level or (n.module or "").split(".")[0] not in SAFE_IMPORTS):
            return True
        words = [a.name for a in n.names] if isinstance(n, ast.ImportFrom) else []  # from os import system as s
        words += [n.id] if isinstance(n, ast.Name) else [n.attr] if isinstance(n, ast.Attribute) else []
        if any(w in SPAWN_NAMES or OS_SPAWN.match(w) for w in words):
            return True
        if isinstance(n, ast.Attribute) and RUNNER_LEAF.match(n.attr):  # sys.modules['subprocess'].run
            return True
    return False


def runs_decoded(src: str, decode_words: re.Pattern, run_words: re.Pattern, depth: int = 0) -> bool:
    """Whether the code passes decoded or reversed text to exec or a process: a decoder's result, a name assigned from
    one, or a function parameter (a helper may run what it is passed). Dynamic dispatch or exec of computed text,
    beside a decoder, counts. Unparsable code: by its words (decode_words and run_words), as before #690."""
    ast, tree = _tree(src)
    if tree is None or depth > 8:
        return bool(decode_words.search(src) and run_words.search(src))
    aliases: dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            aliases |= {
                (a.asname or a.name.split(".")[0]): (a.name if a.asname else a.name.split(".")[0]) for a in n.names
            }
        elif isinstance(n, ast.ImportFrom) and n.module:
            aliases |= {(a.asname or a.name): f"{n.module}.{a.name}" for a in n.names}

    def dotted(node) -> str | None:
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return None if (head := dotted(node.value)) is None else f"{head}.{node.attr}"
        if isinstance(node, ast.Call):
            return None if (head := dotted(node.func)) is None else f"{head}()"
        return None

    def decodes(node) -> bool:
        if isinstance(node, ast.Call):
            return bool(DECODER.search(dotted(node.func) or ""))
        if isinstance(node, ast.Slice) and node.step is not None:
            step = node.step
            return (isinstance(step, ast.UnaryOp) and isinstance(step.op, ast.USub)) or (
                isinstance(step, ast.Constant) and isinstance(step.value, int) and step.value < 0
            )
        return False

    nodes = list(ast.walk(tree))
    if not any(decodes(n) for n in nodes) and not decode_words.search(src):
        return False
    called = {id(n.func) for n in nodes if isinstance(n, ast.Call)}

    def runner(node, name: str) -> bool:
        return bool(PROC_RUNNER.match(name) or (isinstance(node, ast.Attribute) and RUNNER_LEAF.match(node.attr)))

    for n in nodes:
        if _dynamic(ast, n):
            return True
        if isinstance(n, (ast.Name, ast.Attribute)) and id(n) not in called:  # f = os.system; map(exec, ...)
            name = dotted(n) or ""
            if name in CODE_RUNNERS or runner(n, name) or DECODER.search(name):
                return True
    params = {a.arg for n in nodes if isinstance(n, ast.arguments) for a in n.posonlyargs + n.args + n.kwonlyargs}
    params |= {a.arg for n in nodes if isinstance(n, ast.arguments) for a in (n.vararg, n.kwarg) if a}
    names: set[str] = set()
    attrs: set[str] = set()

    def dirty(node) -> bool:
        return any(
            decodes(x)
            or (isinstance(x, ast.Name) and x.id in names)
            or (isinstance(x, ast.Attribute) and x.attr in attrs)
            for x in ast.walk(node)
        )

    def taint(target) -> bool:
        before = len(names) + len(attrs)
        for x in ast.walk(target):
            if isinstance(x, ast.Name):
                names.add(x.id)
            elif isinstance(x, ast.Attribute):
                attrs.add(x.attr)
        return len(names) + len(attrs) > before

    changed = True
    while changed:  # flow-insensitive: a name tainted anywhere is tainted everywhere
        changed = False
        for n in nodes:
            pairs = []
            if isinstance(n, ast.Assign):
                pairs = [(t, n.value) for t in n.targets]
            elif isinstance(n, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)) and n.value is not None:
                pairs = [(n.target, n.value)]
            elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
                pairs = [(n.target, n.iter)]
            elif isinstance(n, ast.withitem) and n.optional_vars is not None:
                pairs = [(n.optional_vars, n.context_expr)]
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):  # l.append(decoded)
                pairs = [(n.func.value, a) for a in n.args + [k.value for k in n.keywords]]
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # its result may be decoded text: f() and self.f() both read as dirty
                pairs = [(ast.Attribute(ast.Name(n.name), n.name), b) for b in n.body]
            for target, value in pairs:
                if dirty(value) and taint(target):
                    changed = True
    runs = staged = False
    for n in nodes:
        if not isinstance(n, ast.Call):
            continue
        name, args = dotted(n.func) or "", n.args + [k.value for k in n.keywords]
        if name in CODE_RUNNERS:
            text = args[0] if args else None
            if not (isinstance(text, ast.Constant) and isinstance(text.value, str)):
                return True  # exec of computed text beside a decoder
            if runs_decoded(text.value, decode_words, run_words, depth + 1):
                return True
            runs = True
        elif runner(n.func, name):
            used = {x.id for a in args for x in ast.walk(a) if isinstance(x, ast.Name)}
            if any(dirty(a) for a in args) or used & params:
                return True
            runs = True
        handed = isinstance(n.func, ast.Attribute) and n.func.attr in HANDS_ON
        handed = handed or (name == "print" and any(k.arg == "file" for k in n.keywords))
        staged = staged or (handed and any(dirty(a) for a in args))
    return runs and staged  # decoded text written to a file or a process's stdin, beside a runner
