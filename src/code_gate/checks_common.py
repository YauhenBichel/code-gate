"""Checks that read lines only, so they work for every language."""

from __future__ import annotations

import math
import re
from typing import Iterator

from .finding import Finding
from .rules import make
from .source import Source

LIMITS = {
    "function_lines": 60,
    "parameters": 6,
    "nesting": 4,
    "complexity": 15,
    "class_methods": 20,
    "file_lines": 800,
    "loop_depth": 3,
}

# The line checks read at most this much of one line: a longer line is minified or generated
# code, and a pattern that is cheap on a normal line is not cheap on 40 KB of it.
MAX_LINE = 4_000

# Values that are plainly not secrets.
_NOT_SECRET = re.compile(
    r"^(|x+|\*+|\.+|changeme|change-me|example|test|testing|dummy|secret|password|none|null|todo|your[-_ ].*|<.*>|\$\{.*\}|\{\{.*\}\}|%\(?\w*\)?s?)$",
    re.I,
)
# A name says "secret" when one of these words is in it ...
_SECRET_WORD = re.compile(r"(?i)pass(?:word|wd|phrase)|pwd|secret|api[_-]?key|apikey|access[_-]?key|auth[_-]?token|token|private[_-]?key")
# ... and it names the secret itself when the word ends it (`db_password`, `apiKey`), not `token_type` or `password_label`.
_NAMES_THE_SECRET = re.compile(r"(?i)(?:pass(?:word|wd|phrase)|pwd|secret|key|token)\d*$")
_WORDY_NAME = re.compile(r"(?i)(?:pass(?:word|wd|phrase)|pwd|secret)\d*$")
# The rest of the name, then `=`, `:` or `:=`, then a quoted value. Every part is bounded.
_ASSIGNED = re.compile(r"""\w{0,40}["']?\s{0,8}(?::=|=|:)\s{0,8}(["'`])([^"'`\s]{8,200})\1""")
# Text that is put together when the program runs, a placeholder, a URL or a path: not a secret that was typed in.
_NOT_TYPED_IN = re.compile(r"\$\{|\{\{|\{[\w.]*\}|%[sd(]|<\w+>|^\$|://|^[./~]|^\w+\.\w+\.\w+$")
_MAX_NAMES_PER_LINE = 40
_SECRET_SHAPES = (
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("API key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
)
_PLACEHOLDER = re.compile(
    r"(?i)(?:#|//|/\*)\s*(?:todo|fixme)?:?\s*(?:implement(?:ation)?\s+(?:this|here|me|later|goes here)|your (?:code|logic|implementation) here|"
    r"rest of (?:the )?(?:code|implementation|function|file)|\.\.\.\s*(?:existing|rest|remaining|other)\b|placeholder\b|not (?:yet )?implemented|add (?:real|actual) (?:logic|implementation))"
)
_SQL = re.compile(r"(?i)\b(?:select\s.+\sfrom|insert\s+into|update\s+\w+\s+set|delete\s+from)\b")
_SQL_JOIN = {
    "python": re.compile(r"""(?:\bf["'].*\{[^}]+\}|["']\s*%\s*[\w(]|["']\s*\+\s*\w|\.format\s*\()"""),
    "typescript": re.compile(r"""(?:\$\{[^}]+\}|["']\s*\+\s*\w)"""),
    "java": re.compile(r"""(?:["']\s*\+\s*\w|String\.format\s*\()"""),
    "go": re.compile(r"""(?:["`]\s*\+\s*\w|Sprintf\s*\()"""),
}


def secret_lines(source: Source) -> dict[int, str]:
    """Line number -> what kind of secret is on it."""
    found: dict[int, str] = {}
    for number, line in enumerate(source.lines, 1):
        line = line[:MAX_LINE]
        for kind, shape in _SECRET_SHAPES:
            if shape.search(line):
                found[number] = kind
                break
        else:
            name = _named_secret(line)
            if name:
                found[number] = f"value of `{name}`"
    return found


def _named_secret(line: str) -> str:
    """The name on this line that is given a value which looks like a secret, or ""."""
    tried = 0
    for word in _SECRET_WORD.finditer(line):
        tried += 1
        if tried > _MAX_NAMES_PER_LINE:
            break
        assigned = _ASSIGNED.match(line, word.end())
        if not assigned:
            continue
        begin = word.start()
        while begin > 0 and word.start() - begin < 60 and (line[begin - 1].isalnum() or line[begin - 1] in "_."):
            begin -= 1
        name = line[begin : assigned.start(1)].rstrip(" \t\"':=")
        if _looks_like_a_secret(name, assigned.group(2)):
            return name
    return ""


def _looks_like_a_secret(name: str, value: str) -> bool:
    """A label (`token_type = 'comment'`) is not a secret; a value with the shape of a key or a password is."""
    if _NOT_SECRET.match(value) or _looks_like_lookup(value) or _NOT_TYPED_IN.search(value):
        return False
    letters = sum(char.isalpha() for char in value)
    digits = sum(char.isdigit() for char in value)
    if not letters:
        return False
    if not _NAMES_THE_SECRET.search(name):
        # The word is only part of the name: take nothing but a long random-looking run.
        return len(value) >= 20 and digits >= 3 and bool(re.fullmatch(r"[A-Za-z0-9+/=_.-]+", value)) and _entropy(value) >= 3.5
    if digits:
        return True
    if any(char in "!@#$%^&*+=?" for char in value):
        return True
    # No digit and no symbol: a pass phrase in mixed case, only under a name that says password or secret.
    mixed = any(char.islower() for char in value) and any(char.isupper() for char in value[1:])
    return bool(_WORDY_NAME.search(name)) and mixed and value.isalnum() and len(value) >= 12


def _entropy(value: str) -> float:
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    return -sum(count / len(value) * math.log2(count / len(value)) for count in counts.values())


def _looks_like_lookup(value: str) -> bool:
    # "os.environ[...]", "process.env.X" and friends arrive here only when quoted oddly.
    return bool(re.search(r"(?i)environ|getenv|process\.env|\bvault\b", value))


def check(source: Source, limits: dict[str, int]) -> Iterator[Finding]:
    for number, kind in secret_lines(source).items():
        yield make("hardcoded-secret", number, kind)
    for number, line in enumerate(source.lines, 1):
        if _PLACEHOLDER.search(line[:MAX_LINE]):
            yield make("placeholder", number, line.strip()[:80])
    if len(source.lines) > limits["file_lines"]:
        yield make("large-file", 1, f"{len(source.lines)} lines")
    join = _SQL_JOIN.get(source.language)
    if join and source.language != "python":  # Python reads the syntax tree instead
        for number, line in enumerate(source.lines, 1):
            line = line[:MAX_LINE]
            if _SQL.search(line) and join.search(line):
                yield make("sql-built-from-strings", number)
