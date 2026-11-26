"""TypeScript/JavaScript, Java and Go checks.

There is no parser here, on purpose: the plugin has no dependencies.
The checks read the source with comments and strings blanked out, match
braces to find functions and loop bodies, and then look at lines.
They are heuristics; `code-gate: ignore[rule]` silences a wrong one.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from typing import Iterator

from .finding import Finding
from .rules import make
from .source import Source

_NOT_A_FUNCTION = {
    "if", "for", "while", "switch", "catch", "return", "new", "else", "do", "try", "synchronized",
    "function", "super", "this", "throw", "await", "typeof", "delete", "case", "func", "go", "defer", "select",
}
_CONTROL = re.compile(r"\b(if|for|while|switch|try|else|catch|do|select|finally)\b")
_BRANCH = re.compile(r"\b(if|for|while|case|catch)\b|&&|\|\|")

_FUNCTION = {
    "go": [re.compile(r"^[ \t]*func\s+(?:\([^)]*\)\s*)?(?P<name>\w+)\s*(?:\[[^\]]*\]\s*)?\(", re.M)],
    "java": [
        re.compile(
            r"^[ \t]*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|private|protected|static|final|synchronized|abstract|default|native)\s+)*"
            r"(?:<[^>]+>\s+)?[\w.]+(?:<[^;(){}]*>)?(?:\[\])*\s+(?P<name>\w+)\s*\(",
            re.M,
        ),
        re.compile(r"^[ \t]*(?:public|private|protected)\s+(?P<name>[A-Z]\w*)\s*\(", re.M),
    ],
    "typescript": [
        re.compile(r"\bfunction\s*\*?\s*(?P<name>\w+)\s*(?:<[^>(]*>)?\s*\("),
        re.compile(r"\b(?:const|let|var)\s+(?P<name>\w+)\s*(?::[^=;]+)?=\s*(?:async\s*)?(?:<[^>(]*>\s*)?(?P<arrow>\(|\w+\s*=>)"),
        re.compile(
            r"^[ \t]*(?:(?:public|private|protected|static|async|override|readonly|get|set)\s+)*(?P<name>\w+)\s*(?:<[^>(]*>)?\s*\(",
            re.M,
        ),
    ],
}
_LOOP = {
    "go": re.compile(r"^[ \t]*(?:\w+:\s*)?for\b", re.M),
    "java": re.compile(r"\b(?:for|while)\s*\("),
    "typescript": re.compile(r"\b(?:for|while)\s*(?:await\s*)?\("),
}
_CALLBACK_LOOP = re.compile(r"\.(?:forEach|map|filter|flatMap|reduce|some|every|find)\s*\(")
_CLASS = re.compile(r"\b(?:class|interface)\s+(\w+)[^{;]*\{")

_SORT = re.compile(r"\.sort\s*\(|\bsort\.(?:Slice|SliceStable|Strings|Ints|Sort)\s*\(|\b(?:Collections|Arrays)\.sort\s*\(|\.sorted\s*\(\s*\)|\.toSorted\s*\(")
_IO = re.compile(
    r"\bfetch\s*\(|\baxios\s*(?:\.\w+)?\s*\(|\bhttp\.(?:Get|Post|Head|PostForm)\s*\(|"
    r"\.(?:query|execute|executeQuery|executeUpdate|findById|findOne|findUnique|findFirst|findMany|QueryRow|QueryContext|Query|Exec|ExecContext)\s*\("
)
_APPEND = re.compile(r"\b(\w+)\s*\+=")
_SEARCH = re.compile(r"\b(\w+)\.(?:includes|indexOf|contains)\s*\(")

_STRINGS = {
    "typescript": re.compile(r"\b(?:let|var)\s+(\w+)\s*(?::\s*string\s*)?=\s*[\"'`]"),
    "java": re.compile(r"\bString\s+(\w+)\s*="),
    "go": re.compile(r"\b(\w+)\s*:=\s*\"|\bvar\s+(\w+)\s+string\b"),
}
_LISTS = {
    "typescript": re.compile(r"\b(?:const|let|var)\s+(\w+)\s*(?::[^=]+)?=\s*\[|\b(\w+)\s*:\s*(?:[\w.<>]+\[\]|Array<|ReadonlyArray<)"),
    "java": re.compile(r"\b(?:List|ArrayList|LinkedList|Collection)\s*<[^;=()]*>\s+(\w+)"),
}
_LINKED = re.compile(r"\bLinkedList\s*<[^;=()]*>\s+(\w+)")
# fetch(...) and axios(...) / axios.get(...), but not `api.fetch(` or `function fetch(`.
_NET_CALL = re.compile(r"(?<![\w.$])(fetch|axios(?:\.(?:get|post|put|patch|delete|head|request))?)\s*\(")
_SENDS_A_BODY = ("axios.post", "axios.put", "axios.patch")

# A longer line is minified or generated code: brace matching says nothing useful about it,
# and the line patterns are not cheap on it.
MAX_LINE = 10_000


class TooLong(Exception):
    """The file has a line these checks do not read; the text is the note for the user."""


@dataclass
class _Function:
    name: str
    line: int
    open: int  # offset of the body's opening brace
    close: int
    parameters: int


class _Layout:
    """Offsets, brace pairs and loop bodies of one file."""

    def __init__(self, source: Source) -> None:
        self.source = source
        self.language = source.language
        self.text = "\n".join(source.code)
        self.starts = [0]
        for line in source.code[:-1]:
            self.starts.append(self.starts[-1] + len(line) + 1)
        self.pair: dict[int, int] = {}
        stack: list[int] = []
        for offset, char in enumerate(self.text):
            if char in "({[":
                stack.append(offset)
            elif char in ")}]" and stack:
                self.pair[stack.pop()] = offset
        self.loop_spans = self._loops()

    def line_of(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset)

    def _body_after(self, offset: int) -> tuple[int, int] | None:
        """The block that starts at the next `{` before any `;`."""
        index = offset
        while index < len(self.text):
            char = self.text[index]
            if char == "{":
                return (index, self.pair.get(index, len(self.text)))
            if char == ";":
                return None
            if char in "([":
                index = self.pair.get(index, index)
            index += 1
        return None

    def _loops(self) -> list[tuple[int, int, int, bool]]:
        """(header offset, body start, body end, is a real loop statement)."""
        spans = []
        for found in _LOOP[self.language].finditer(self.text):
            if self.language == "go":
                end_of_line = self.text.find("\n", found.end())
                end_of_line = len(self.text) if end_of_line < 0 else end_of_line
                brace = self.text.rfind("{", found.end(), end_of_line)
                body = (brace, self.pair.get(brace, len(self.text))) if brace >= 0 else None
            else:
                paren = found.end() - 1
                after = self.pair.get(paren, paren) + 1
                body = self._body_after(after)
                if body is None:  # one statement, no braces
                    stop = self.text.find(";", after)
                    body = (after, len(self.text) if stop < 0 else stop)
            if body:
                spans.append((found.start(), body[0], body[1], True))
        if self.language == "typescript":
            for found in _CALLBACK_LOOP.finditer(self.text):
                paren = found.end() - 1
                spans.append((found.start(), paren, self.pair.get(paren, paren), False))
        return spans

    def loops_around(self, offset: int, real_only: bool = False) -> int:
        return sum(1 for _, start, end, real in self.loop_spans if start < offset <= end and (real or not real_only))

    def functions(self) -> list[_Function]:
        seen: dict[int, _Function] = {}
        for pattern in _FUNCTION[self.language]:
            for found in pattern.finditer(self.text):
                name = found.group("name")
                if name in _NOT_A_FUNCTION:
                    continue
                groups = found.groupdict()
                paren = found.end() - 1
                parameters = 0
                if groups.get("arrow") and not groups["arrow"].startswith("("):
                    parameters, after = 1, found.end()
                else:
                    close = self.pair.get(paren)
                    if close is None:
                        continue
                    inside = self.text[paren + 1 : close]
                    parameters = _count_parameters(inside)
                    after = close + 1
                if groups.get("arrow"):
                    arrow = self.text.find("=>", after - 2 if not groups["arrow"].startswith("(") else after)
                    if arrow < 0 or ";" in self.text[after:arrow] or arrow - after > 120:
                        continue
                    after = arrow + 2
                    rest = self.text[after : after + 40].lstrip()
                    if not rest.startswith("{"):
                        continue
                body = self._body_after(after)
                if body is None or "\n\n" in self.text[after : body[0]] or body[0] - after > 200:
                    continue
                if self.language != "go" and re.search(r"[=]|\bnew\b", self.text[after : body[0]]) and not groups.get("arrow"):
                    continue
                seen.setdefault(body[0], _Function(name, self.line_of(found.start("name")), body[0], body[1], parameters))
        return list(seen.values())

    def nesting(self, function: _Function) -> int:
        deepest, stack = 0, []
        for offset in range(function.open + 1, function.close):
            char = self.text[offset]
            if char == "{":
                line_start = self.text.rfind("\n", 0, offset) + 1
                previous = self.text.rfind("\n", 0, max(line_start - 1, 0)) + 1
                head = self.text[line_start:offset] or self.text[previous:offset]
                stack.append(bool(_CONTROL.search(head)))
                deepest = max(deepest, sum(stack))
            elif char == "}" and stack:
                stack.pop()
        return deepest


def _count_parameters(inside: str) -> int:
    if not inside.strip():
        return 0
    depth, count = 0, 1
    for char in inside:
        if char in "(<[{":
            depth += 1
        elif char in ")>]}":
            depth -= 1
        elif char == "," and depth <= 0:
            count += 1
    if inside.rstrip().endswith(","):
        count -= 1
    return count


def _names(pattern: re.Pattern | None, text: str) -> set[str]:
    if pattern is None:
        return set()
    return {name for groups in pattern.findall(text) for name in ([groups] if isinstance(groups, str) else groups) if name}


def check(source: Source, limits: dict[str, int]) -> Iterator[Finding]:
    longest = max((len(line) for line in source.lines), default=0)
    if longest > MAX_LINE:
        raise TooLong(f"structure checks skipped: a line is {longest} characters long (minified or generated code)")
    layout = _Layout(source)
    language, text = source.language, layout.text
    functions = layout.functions()

    for function in functions:
        last_line = layout.line_of(function.close)
        length = last_line - function.line + 1
        if length > limits["function_lines"]:
            yield make("long-function", function.line, f"`{function.name}` is {length} lines")
        if function.parameters > limits["parameters"]:
            yield make("too-many-parameters", function.line, f"`{function.name}` takes {function.parameters}")
        score = 1 + len(_BRANCH.findall(text[function.open : function.close]))
        if score > limits["complexity"]:
            yield make("high-complexity", function.line, f"`{function.name}` scores {score}")
        depth = layout.nesting(function)
        if depth > limits["nesting"]:
            yield make("deep-nesting", function.line, f"`{function.name}` goes {depth} levels deep")
        body = text[function.open + 1 : function.close].strip()
        raw_body = "\n".join(source.lines[function.line - 1 : last_line])
        if not body and language != "java":
            if re.search(r"(?i)todo|implement|placeholder", raw_body):
                yield make("placeholder", function.line, f"`{function.name}` has no body")
        elif re.fullmatch(r"(?s)(?:throw new (?:Error|UnsupportedOperationException|NotImplementedError)\s*\([^)]*\)\s*;?|panic\s*\([^)]*\))", body):
            if re.search(r"(?i)not\s*(?:yet\s*)?implemented|todo|unsupported", raw_body):
                yield make("placeholder", function.line, f"`{function.name}` only throws")

    if language != "go":
        for found in _CLASS.finditer(text):
            open_brace = found.end() - 1
            close_brace = layout.pair.get(open_brace, len(text))
            methods = sum(1 for f in functions if open_brace < f.open < close_brace)
            if methods > limits["class_methods"]:
                yield make("god-class", layout.line_of(found.start()), f"`{found.group(1)}` has {methods} methods")

    for header, _start, _end, real in layout.loop_spans:
        if real and layout.loops_around(header) + 1 == limits["loop_depth"]:
            yield make("deeply-nested-loops", layout.line_of(header))

    strings = _names(_STRINGS.get(language), text)
    lists = _names(_LISTS.get(language), text)
    linked = _names(_LINKED, text) if language == "java" else set()

    for number, line in enumerate(source.code, 1):
        if not line.strip():
            continue
        start = layout.starts[number - 1]
        raw = source.lines[number - 1]

        def loops(match: re.Match, real_only: bool = False) -> int:
            return layout.loops_around(start + match.start(), real_only)

        def raw_hit(pattern: str) -> re.Match | None:
            """Match on the original line, but only where it is code, not a comment."""
            found = re.search(pattern, raw)
            if found and found.start() < len(line) and line[found.start()] == raw[found.start()] and raw[found.start()] != " ":
                return found
            return None

        found = _SORT.search(line)
        if found and loops(found):
            yield make("sort-in-loop", number)
        io = _IO.search(line)
        if io and loops(io):
            yield make("io-in-loop", number, f"`{io.group(0).strip('. (')}`")
        for found in _APPEND.finditer(line):
            if found.group(1) in strings and loops(found):
                yield make("string-built-in-loop", number, f"`{found.group(1)}`")
        for found in _SEARCH.finditer(line):
            if found.group(1) in lists and loops(found):
                yield make("list-membership-in-loop", number, f"`{found.group(1)}`")
        if re.search(r"\.(?:printStackTrace)\s*\(", line):
            yield make("stack-trace-printed", number)

        if language == "typescript":
            yield from _typescript(number, line, raw_hit, loops, bool(io))
        elif language == "java":
            yield from _java(number, line, raw_hit, linked)
        else:
            yield from _go(number, line, raw_hit, loops)

    if language == "typescript":
        yield from _network_calls(layout)

    if language in ("typescript", "java"):
        for found in re.finditer(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}", text):
            if not re.search(r"//|/\*", source.text[found.start() : found.end()]):  # a comment says why
                yield make("swallowed-error", layout.line_of(found.start()), "empty `catch`")
    else:
        for found in re.finditer(r"\bif\s+err\s*!=\s*nil\s*\{\s*\}", text):
            if not re.search(r"//|/\*", source.text[found.start() : found.end()]):
                yield make("swallowed-error", layout.line_of(found.start()), "empty `if err != nil`")


def _arguments(inside: str) -> list[str]:
    """The arguments of a call, split at the commas that are not inside brackets."""
    parts, depth, begin = [], 0, 0
    for index, char in enumerate(inside):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(inside[begin:index].strip())
            begin = index + 1
    parts.append(inside[begin:].strip())
    return [part for part in parts if part]


def _network_calls(layout: _Layout) -> Iterator[Finding]:
    """`fetch` without a `signal`, `axios` without a `timeout`: both wait for ever by default.

    Only what can be seen is reported: a call with no options, or with an options object
    written in place. Options passed in a variable are not followed.
    """
    text = layout.text
    axios_has_default = bool(re.search(r"\baxios\.defaults\.timeout\b", text))
    for found in _NET_CALL.finditer(text):
        what = found.group(1)
        opened = found.end() - 1
        closed = layout.pair.get(opened)
        if closed is None or re.search(r"\bfunction\s*\*?\s*$", text[max(0, found.start() - 12) : found.start()]):
            continue
        if re.match(r"\s*(?::[^;{=]{0,80})?\{", text[closed + 1 : closed + 100]):
            continue  # a method named fetch is being defined, not called
        if what != "fetch" and axios_has_default:
            continue
        parts = _arguments(text[opened + 1 : closed])
        if not parts or any(part.startswith("...") for part in parts):
            continue
        place = 2 if what in _SENDS_A_BODY else (0 if what == "axios" and len(parts) == 1 else 1)
        options = parts[place] if len(parts) > place else ""
        if what == "axios" and place == 0 and not options.startswith("{"):
            continue  # axios(config) or axios(url): cannot tell which
        if options and not options.startswith("{"):
            continue  # options built elsewhere
        if re.search(r"\b(?:signal|timeout)\b", options):
            continue
        need = "signal (AbortSignal.timeout)" if what == "fetch" else "timeout"
        yield make("no-timeout", layout.line_of(found.start()), f"`{what}` has no `{need}`")


def _typescript(number, line, raw_hit, loops, has_io) -> Iterator[Finding]:
    if re.search(r"\beval\s*\(|\bnew\s+Function\s*\(", line):
        yield make("eval", number)
    if re.search(r"\.(?:innerHTML|outerHTML)\s*=(?!=)|\bdangerouslySetInnerHTML\b|\bdocument\.write\s*\(", line):
        yield make("html-injection", number)
    if re.search(r"\brejectUnauthorized\s*:\s*false\b|NODE_TLS_REJECT_UNAUTHORIZED", line):
        yield make("tls-check-off", number)
    if raw_hit(r"\b(?:exec|execSync)\s*\(\s*(?:`[^`]*\$\{|[\"'][^\"']*[\"']\s*\+)"):
        yield make("shell-injection", number)
    if raw_hit(r"\bcreateHash\s*\(\s*[\"'](?:md5|sha1)[\"']"):
        yield make("weak-hash", number)
    if re.search(r"\bconsole\.(?:log|debug)\s*\(", line):
        yield make("print-instead-of-log", number)
    if re.search(r":\s*any\b|\bas\s+any\b|<any>", line):
        yield make("any-type", number)
    found = re.search(r"\bawait\b", line)
    if found and not has_io and loops(found, True):
        yield make("sequential-await-in-loop", number)


def _java(number, line, raw_hit, linked) -> Iterator[Finding]:
    if re.search(r"\bRuntime\.getRuntime\(\)\.exec\s*\(", line) and "+" in line:
        yield make("shell-injection", number)
    if raw_hit(r"\bMessageDigest\.getInstance\s*\(\s*\"(?:MD5|SHA-?1)\""):
        yield make("weak-hash", number)
    if re.search(r"\bnew\s+ObjectInputStream\s*\(", line):
        yield make("unsafe-deserialisation", number, "`ObjectInputStream`")
    if re.search(r"\bALLOW_ALL_HOSTNAME_VERIFIER\b|\bNoopHostnameVerifier\b|\bTrustAll\w*", line):
        yield make("tls-check-off", number)
    if re.search(r"\bSystem\.(?:out|err)\.print", line):
        yield make("print-instead-of-log", number)
    if re.search(r"\bnew\s+(?:FileInputStream|FileOutputStream|FileReader|FileWriter|BufferedReader|BufferedWriter|Socket)\s*\(", line) and not re.search(
        r"\btry\s*\(|\breturn\b", line
    ):
        yield make("resource-not-closed", number)
    for found in re.finditer(r"\b(\w+)\.get\s*\(", line):
        if found.group(1) in linked:
            yield make("linked-list-by-index", number, f"`{found.group(1)}`")


def _go(number, line, raw_hit, loops) -> Iterator[Finding]:
    if re.search(r"\bInsecureSkipVerify\s*:\s*true\b", line):
        yield make("tls-check-off", number)
    if raw_hit(r"\bexec\.Command(?:Context)?\s*\([^\"]*\"(?:sh|bash|/bin/sh|/bin/bash)\"\s*,\s*\"-c\"\s*,\s*(?:[^\"\s)]|\"[^\"]*\"\s*\+)"):
        yield make("shell-injection", number)
    if re.search(r"\b(?:md5|sha1)\.(?:New|Sum)\b", line):
        yield make("weak-hash", number)
    if re.search(r"\bhttp\.(?:Get|Post|Head|PostForm)\s*\(|&http\.Client\{\s*\}|\bhttp\.DefaultClient\b", line):
        yield make("no-timeout", number, "the default HTTP client has none")
    if re.search(r"\b\w+\s*,\s*_\s*:?=\s*[\w.]+\(", line) and not re.search(r"\brange\b|\.\(", line):
        yield make("ignored-error", number, "the second result goes to `_`")
    elif re.search(r"^\s*_\s*=\s*[\w.]+\(", line):
        yield make("ignored-error", number)
    found = re.search(r"^\s*defer\b", line)
    if found and loops(found):
        yield make("defer-in-loop", number)
    if re.search(r"\bfmt\.Print(?:ln|f)?\s*\(", line):
        yield make("print-instead-of-log", number)
