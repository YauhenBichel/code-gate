"""One finding format for every check, static or model."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

CATEGORIES = ("security", "algorithm", "data-structure", "design", "smell", "non-functional")
SEVERITIES = ("high", "medium", "low")
_RANK = {name: index for index, name in enumerate(SEVERITIES)}
# Control characters (terminal escape codes), and the Unicode marks that reorder or hide text.
_HIDDEN = ((0x200B, 0x200F), (0x2028, 0x202E), (0x2060, 0x2069), (0xFEFF, 0xFEFF))
_CONTROL = re.compile("[\\x00-\\x1f\\x7f-\\x9f" + "".join(f"{chr(a)}-{chr(b)}" for a, b in _HIDDEN) + "]")


def clean(text: object, limit: int = 0) -> str:
    """Text from outside (a model, a server) made safe to print: one line, no control characters."""
    cleaned = " ".join(_CONTROL.sub(" ", str(text)).split())
    return cleaned[:limit] if limit else cleaned


@dataclass(frozen=True)
class Finding:
    category: str
    rule: str
    severity: str
    line: int
    message: str
    hint: str = ""
    source: str = "static"  # "static" or "model"
    file: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def at_least(findings: list[Finding], severity: str) -> list[Finding]:
    limit = _RANK[severity]
    return [f for f in findings if _RANK[f.severity] <= limit]


def ordered(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (f.file, _RANK[f.severity], f.line, f.rule))
