"""Python checks, read from the syntax tree."""

from __future__ import annotations

import ast
import re
import sys
import warnings
from typing import Iterator

from .finding import Finding
from .rules import make
from .source import Source

_LOOPS = (ast.For, ast.AsyncFor, ast.While)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_NESTING = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith)
_HTTP = {"get", "post", "put", "patch", "delete", "head", "request"}
_HTTP_MODULES = {"requests", "httpx"}
_IO_CALLS = {"execute", "executemany", "urlopen", "fetchone", "fetchall", "query"}
_SQL_WORDS = ("select ", "insert into", "update ", "delete from")
_SESSIONS = {"requests.Session", "requests.session"}  # objects whose calls take no timeout unless given one
_URLOPEN = {"urllib.request.urlopen", "urllib2.urlopen"}
_UNFINISHED = re.compile(r"(?i)\btodo\b|\bfixme\b|not\s+(?:yet\s+)?implemented|implement\s+(?:this|me|later|here)|placeholder")

_RUNNING = sys.version_info[:2]
# Grammar newer than the oldest Python this runs on: (first version that reads it, what it is,
# how it looks, how many lines below its start the older parser may give up).
_NEWER = (
    ((3, 10), "a match statement", re.compile(r"^[ \t]*match\b[^\n=]*:[ \t]*(?:#[^\n]*)?\n\s*case\b", re.M), 0),
    ((3, 10), "a parenthesised with", re.compile(r"^[ \t]*(?:async[ \t]+)?with[ \t]*\(", re.M), 20),
    ((3, 11), "except*", re.compile(r"^[ \t]*except[ \t]*\*", re.M), 0),
    ((3, 12), "type parameters", re.compile(r"^[ \t]*(?:(?:async[ \t]+)?def|class)[ \t]+\w+[ \t]*\[", re.M), 0),
    ((3, 12), "a type statement", re.compile(r"^[ \t]*type[ \t]+\w+[ \t]*(?:\[[^\]\n]*\])?[ \t]*=", re.M), 0),
    ((3, 14), "a template string", re.compile(r"""^[^\n#]*(?<![\w"'])(?:t|tr|rt)["']""", re.M | re.I), 0),
    ((3, 14), "except without parentheses", re.compile(r"^[ \t]*except[ \t]+[\w.]+[ \t]*,[ \t]*[\w.]+[^\n]*:", re.M), 0),
)


def _parse(text: str) -> ast.AST:
    with warnings.catch_warnings():  # the reviewed code's own SyntaxWarnings are not ours to print
        warnings.simplefilter("ignore")
        return ast.parse(text)


def _newer_syntax(text: str, error: SyntaxError) -> str:
    """What newer grammar the running Python gave up on, or "" when it is a plain syntax error."""
    line = error.lineno or 1
    if _RUNNING < (3, 12) and "f-string" in (error.msg or ""):
        return "f-string syntax of Python 3.12"
    for version, what, shape, reach in _NEWER:
        if _RUNNING >= version:
            continue
        for found in shape.finditer(text):
            start = text.count("\n", 0, found.start()) + 1
            if start <= line <= start + reach:
                return f"{what}, Python {version[0]}.{version[1]}"
    return ""


def _name(node: ast.AST) -> str:
    """Dotted name of a call target: `a.b.c`."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Call):
        parts.append(_name(node.func) + "()")
    return ".".join(reversed(parts))


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _is_true(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _is_false(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _text_of(node: ast.AST) -> str:
    """Literal text inside a string expression, lower case."""
    pieces = [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return " ".join(pieces).lower()


def _built_from_values(node: ast.AST) -> bool:
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        sides = (node.left, node.right)
        return any(not isinstance(s, ast.Constant) for s in sides) or any(_built_from_values(s) for s in sides)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return True
    return False


def _complexity(function: ast.AST) -> int:
    score = 1
    for node in ast.walk(function):
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp)):
            score += 1
        elif isinstance(node, ast.BoolOp):
            score += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            score += 1 + len(node.ifs)
    return score


def _depth(node: ast.AST, level: int = 0) -> int:
    deepest = level
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        deepest = max(deepest, _depth(child, level + 1 if isinstance(child, _NESTING) else level))
    return deepest


def _docstring(function: ast.AST) -> bool:
    body = function.body
    return bool(body) and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str)


def _stub(function: ast.AST) -> str:
    """"pass", "raise" or "" for a function that has a real body."""
    body = list(function.body[1:] if _docstring(function) else function.body)
    if not body:
        return "pass"  # a docstring and nothing else
    if len(body) != 1:
        return ""
    only = body[0]
    if isinstance(only, ast.Raise):
        return "raise" if "NotImplementedError" in ast.dump(only) else ""
    if isinstance(only, ast.Pass):
        return "pass"
    return "pass" if isinstance(only, ast.Expr) and isinstance(only.value, ast.Constant) and only.value.value is Ellipsis else ""


def _abstract(function: ast.AST, owner: ast.AST | None) -> bool:
    if any("abstract" in ast.dump(d) or "overload" in ast.dump(d) for d in function.decorator_list):
        return True
    if isinstance(owner, ast.ClassDef):
        bases = " ".join([_name(b) for b in owner.bases] + [_name(k.value) for k in owner.keywords]) + " " + owner.name
        return any(word in bases for word in ("Protocol", "ABC", "Interface", "Abstract", "Base", "Mixin"))
    return False


def _empty_on_purpose(function: ast.AST, kind: str, owner: ast.AST | None) -> bool:
    """A function with no body that is meant to have none: something to override, a callback, a no-op."""
    if _docstring(function) or _abstract(function, owner):
        return True
    if not isinstance(owner, (ast.Module, ast.ClassDef)):
        return True  # defined inside a function or a branch: a local callback, a fallback
    if isinstance(owner, ast.ClassDef) and (kind == "raise" or (function.name.startswith("__") and function.name.endswith("__"))):
        return True  # `raise NotImplementedError` in a method is how Python says "override me"
    arguments = function.args
    named = [a.arg for a in arguments.posonlyargs + arguments.args + arguments.kwonlyargs if a.arg not in ("self", "cls")]
    if (arguments.vararg or arguments.kwarg) and not named:
        return True  # `def on_close(*args): pass` takes anything and ignores it: a callback
    return bool(named) and all(name.startswith("_") for name in named)


class _Scope:
    """What the names in one function are known to hold."""

    def __init__(self, function: ast.AST) -> None:
        self.lists: set[str] = set()
        self.strings: set[str] = set()
        arguments = getattr(function, "args", None)
        if arguments:
            for argument in arguments.args + arguments.kwonlyargs:
                note = ast.dump(argument.annotation) if argument.annotation else ""
                if "'list'" in note or "'List'" in note or "'Sequence'" in note:
                    self.lists.add(argument.arg)
        for node in ast.walk(function):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                for target in targets:
                    if not isinstance(target, ast.Name) or value is None:
                        continue
                    if isinstance(value, (ast.List, ast.ListComp)) or (
                        isinstance(value, ast.Call) and _name(value.func) in ("list", "sorted")
                    ) or (isinstance(value, ast.Call) and _name(value.func).endswith((".split", ".readlines", ".keys()", ".values()"))):
                        self.lists.add(target.id)
                    elif isinstance(value, (ast.JoinedStr,)) or (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                        self.strings.add(target.id)


def check(source: Source, limits: dict[str, int]) -> Iterator[Finding]:
    try:
        tree = _parse(source.text)
    except SyntaxError as error:
        newer = _newer_syntax(source.text, error)
        if newer:
            # Valid code for a newer Python is not a broken file: say it was not read, and do not block.
            yield make("newer-syntax", error.lineno or 1, f"Python {_RUNNING[0]}.{_RUNNING[1]} could not parse it ({newer})")
        else:
            yield make("syntax-error", error.lineno or 1, error.msg or "")
        return
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    # Each answer is kept for every node on the way up, so one long expression is walked once, not once per call in it.
    loops_known: dict[ast.AST, int] = {}
    functions_known: dict[ast.AST, ast.AST] = {}
    with_known: dict[ast.AST, bool] = {}

    def loops_around(node: ast.AST) -> int:
        steps: list[tuple[ast.AST, int]] = []
        current, count = node, 0
        while current not in loops_known:
            above = parents.get(current)
            if above is None or isinstance(above, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                loops_known[current] = 0
                break
            # The iterable of a loop is evaluated once, so it is outside that loop.
            if isinstance(above, _LOOPS) and current is not getattr(above, "iter", None):
                steps.append((current, 1))
            elif isinstance(above, _COMPREHENSIONS):
                steps.append((current, len(above.generators)))
            else:
                steps.append((current, 0))
            current = above
        count = loops_known[current]
        for below, step in reversed(steps):
            count += step
            loops_known[below] = count
        return count

    def function_of(node: ast.AST) -> ast.AST:
        passed: list[ast.AST] = []
        current, found = node, tree
        while True:
            if current in functions_known:
                found = functions_known[current]
                break
            above = parents.get(current)
            if above is None:
                break
            passed.append(current)
            if isinstance(above, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found = above
                break
            current = above
        for below in passed:
            functions_known[below] = found
        return found

    imported = _imports(tree)
    sessions = _session_names(tree, imported)
    scopes: dict[ast.AST, _Scope] = {}

    def scope_of(node: ast.AST) -> _Scope:
        function = function_of(node)
        if function not in scopes:
            scopes[function] = _Scope(function)
        return scopes[function]

    def in_with(node: ast.AST) -> bool:
        passed: list[ast.AST] = []
        current, found = node, False
        while True:
            if current in with_known:
                found = with_known[current]
                break
            above = parents.get(current)
            if above is None or isinstance(above, (ast.withitem, ast.stmt)):
                found = isinstance(above, ast.withitem)
                break
            passed.append(current)
            current = above
        for below in passed + [current]:
            with_known[below] = found
        return found

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            length = end - node.lineno + 1
            if length > limits["function_lines"]:
                yield make("long-function", node.lineno, f"`{node.name}` is {length} lines")
            arguments = node.args
            count = len(arguments.args) + len(arguments.kwonlyargs) + len(arguments.posonlyargs)
            if arguments.args and arguments.args[0].arg in ("self", "cls"):
                count -= 1
            if count > limits["parameters"]:
                yield make("too-many-parameters", node.lineno, f"`{node.name}` takes {count}")
            score = _complexity(node)
            if score > limits["complexity"]:
                yield make("high-complexity", node.lineno, f"`{node.name}` scores {score}")
            depth = _depth(node)
            if depth > limits["nesting"]:
                yield make("deep-nesting", node.lineno, f"`{node.name}` goes {depth} levels deep")
            kind = _stub(node)
            if kind and _UNFINISHED.search("\n".join(source.lines[node.lineno - 1 : end])):
                yield make("placeholder", node.lineno, f"`{node.name}` has no body")
            elif kind and not _empty_on_purpose(node, kind, parents.get(node)):
                yield make("empty-function", node.lineno, f"`{node.name}`")
            for default in arguments.defaults + [d for d in arguments.kw_defaults if d is not None]:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)) or (
                    isinstance(default, ast.Call) and _name(default.func) in ("list", "dict", "set")
                ):
                    yield make("mutable-default", node.lineno, f"in `{node.name}`")
        elif isinstance(node, ast.ClassDef):
            methods = sum(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) for item in node.body)
            if methods > limits["class_methods"]:
                yield make("god-class", node.lineno, f"`{node.name}` has {methods} methods")
        elif isinstance(node, ast.Global):
            yield make("global-state", node.lineno, ", ".join(node.names))
        elif isinstance(node, _LOOPS):
            if loops_around(node) + 1 == limits["loop_depth"]:
                yield make("deeply-nested-loops", node.lineno)
        elif isinstance(node, ast.ExceptHandler):
            broad = node.type is None or _name(node.type) in ("Exception", "BaseException")
            silent = all(isinstance(s, (ast.Pass, ast.Continue)) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)) for s in node.body)
            last = getattr(node, "end_lineno", node.lineno)
            explained = any(
                "#" in source.lines[n - 1] and "#" not in source.code[n - 1] for n in range(node.lineno, last + 1)
            )
            if broad and silent and not explained:
                yield make("swallowed-error", node.lineno)
        elif isinstance(node, ast.Compare):
            if any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops) and loops_around(node):
                target = node.comparators[-1]
                if isinstance(target, ast.Name) and target.id in scope_of(node).lists:
                    yield make("list-membership-in-loop", node.lineno, f"`{target.id}`")
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.op, ast.Add) and isinstance(node.target, ast.Name) and loops_around(node):
                if node.target.id in scope_of(node).strings:
                    yield make("string-built-in-loop", node.lineno, f"`{node.target.id}`")
        elif isinstance(node, ast.Call):
            yield from _call(node, _real_name(_name(node.func), imported), loops_around(node), in_with(node), parents, sessions)


def _imports(tree: ast.AST) -> dict[str, str]:
    """Local name -> what it was imported as: `r` -> `requests`, `get` -> `requests.get`."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _real_name(name: str, imported: dict[str, str]) -> str:
    head, dot, rest = name.partition(".")
    return imported[head] + dot + rest if head in imported else name


def _session_names(tree: ast.AST, imported: dict[str, str]) -> set[str]:
    """Names that hold a `requests.Session()`: `s = requests.Session()`, `with requests.Session() as s`."""

    def makes_session(value: ast.AST | None) -> bool:
        return isinstance(value, ast.Call) and _real_name(_name(value.func), imported) in _SESSIONS

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and makes_session(node.value):
            names |= {_name(target) for target in node.targets}
        elif isinstance(node, ast.AnnAssign) and makes_session(node.value):
            names.add(_name(node.target))
        elif isinstance(node, ast.withitem) and makes_session(node.context_expr) and node.optional_vars is not None:
            names.add(_name(node.optional_vars))
    names.discard("")
    return names


def _call(call: ast.Call, name: str, loops: int, in_with: bool, parents: dict, sessions: set[str] = frozenset()) -> Iterator[Finding]:
    line = call.lineno
    last = name.rsplit(".", 1)[-1]
    head = name.split(".", 1)[0]
    first = call.args[0] if call.args else None

    if name in ("eval", "exec"):
        yield make("eval", line, f"`{name}`")
    elif name in ("os.system", "os.popen") and first is not None and not isinstance(first, ast.Constant):
        yield make("shell-injection", line, f"`{name}`")
    elif head == "subprocess" and _is_true(_keyword(call, "shell")) and first is not None and not isinstance(first, ast.Constant):
        yield make("shell-injection", line, "`shell=True` with a built command")
    elif name in ("pickle.load", "pickle.loads", "marshal.loads", "dill.loads"):
        yield make("unsafe-deserialisation", line, f"`{name}`")
    elif name == "yaml.load" and "Safe" not in ast.dump(call):
        yield make("unsafe-deserialisation", line, "`yaml.load` without a safe loader")
    elif name in ("hashlib.md5", "hashlib.sha1"):
        yield make("weak-hash", line, f"`{name}`")
    elif name == "tempfile.mktemp":
        yield make("insecure-temp-file", line)
    elif last == "run" and _is_true(_keyword(call, "debug")):
        yield make("debug-on", line)
    elif name == "print":
        yield make("print-instead-of-log", line)
    elif name == "open" and not in_with:
        owner = parents.get(call)
        if not isinstance(owner, ast.Return):
            yield make("resource-not-closed", line, "`open` outside a `with` block")

    if last in ("execute", "executemany") and first is not None and _built_from_values(first):
        if any(word in _text_of(first) for word in _SQL_WORDS):
            yield make("sql-built-from-strings", line)
    if _is_false(_keyword(call, "verify")):
        yield make("tls-check-off", line, "`verify=False`")
    if _keyword(call, "timeout") is None and not any(keyword.arg is None for keyword in call.keywords):
        owner = name.rsplit(".", 1)[0] if "." in name else ""
        on_session = owner in sessions or (owner.endswith("()") and owner[:-2] in _SESSIONS)
        if last in _HTTP and ((head in _HTTP_MODULES and name.count(".") == 1) or on_session):
            yield make("no-timeout", line, f"`{name}`")
        elif name in _URLOPEN and len(call.args) < 3:
            yield make("no-timeout", line, f"`{name}`")

    if loops:
        if (last == "pop" and first is not None and isinstance(first, ast.Constant) and first.value == 0) or (
            last == "insert" and first is not None and isinstance(first, ast.Constant) and first.value == 0
        ):
            yield make("queue-on-a-list", line, f"`.{last}(0)` in a loop")
        if name == "sorted" or last == "sort":
            yield make("sort-in-loop", line)
        if (head in _HTTP_MODULES and last in _HTTP) or last in _IO_CALLS:
            yield make("io-in-loop", line, f"`{name}`")
