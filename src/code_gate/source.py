"""Read a source file the way the checks need it.

`Source.code` is the text with comments and string contents blanked
out (same length, same line breaks), so a brace or a keyword inside a
string cannot confuse the structure checks. `Source.lines` keeps the
original text for checks that must read strings (SQL, secrets).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

LANGUAGES = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
    ".mjs": "typescript",
    ".cjs": "typescript",
    ".java": "java",
    ".go": "go",
}
NAMES = {"python", "typescript", "java", "go"}
ALIASES = {"py": "python", "ts": "typescript", "js": "typescript", "javascript": "typescript", "golang": "go"}

_IGNORE = re.compile(r"code-gate:\s*ignore(?:\[([\w\-, ]+)\])?")


def language_of(path: str, language: str = "") -> str:
    name = (language or "").strip().lower()
    name = ALIASES.get(name, name)
    if name in NAMES:
        return name
    dot = path.rfind(".")
    return LANGUAGES.get(path[dot:].lower(), "") if dot >= 0 else ""


def blank(text: str, language: str) -> str:
    """Replace comments and string contents with spaces."""
    out = list(text)
    i, n = 0, len(text)
    hash_comment = language == "python"
    slash_comment = language != "python"
    backtick = language in ("typescript", "go")

    def wipe(start: int, end: int) -> None:
        for k in range(start, min(end, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        ch = text[i]
        if hash_comment and ch == "#":
            end = text.find("\n", i)
            end = n if end < 0 else end
            wipe(i, end)
            i = end
        elif slash_comment and text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
            wipe(i, end)
            i = end
        elif slash_comment and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            wipe(i, end)
            i = end
        elif language == "python" and (text.startswith('"""', i) or text.startswith("'''", i)):
            quote = text[i : i + 3]
            end = text.find(quote, i + 3)
            end = n if end < 0 else end
            wipe(i + 3, end)
            i = end + 3
        elif ch in "\"'" or (backtick and ch == "`"):
            j = i + 1
            raw = ch == "`" and language == "go"
            while j < n and text[j] != ch:
                if text[j] == "\\" and not raw:
                    j += 1
                elif text[j] == "\n" and ch != "`":
                    break
                j += 1
            wipe(i + 1, j)
            i = j + 1
        else:
            i += 1
    return "".join(out)


@dataclass
class Source:
    path: str
    language: str
    text: str
    lines: list[str] = field(default_factory=list)
    code: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, text: str, path: str = "", language: str = "") -> "Source":
        lang = language_of(path, language)
        text = text.replace("\r\n", "\n")
        return cls(path, lang, text, text.split("\n"), blank(text, lang).split("\n"))

    def ignored(self, line: int, rule: str) -> bool:
        """True when the line, or the one above it, says `code-gate: ignore`."""
        for number in (line, line - 1):
            if 1 <= number <= len(self.lines):
                found = _IGNORE.search(self.lines[number - 1])
                if found and (not found.group(1) or rule in re.split(r"[,\s]+", found.group(1))):
                    return True
        return False
