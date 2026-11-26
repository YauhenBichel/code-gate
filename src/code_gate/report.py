"""Findings as text a person or a model can act on."""

from __future__ import annotations

from .review import Result


def lines_for(result: Result, limit: int = 0) -> list[str]:
    shown = result.findings[:limit] if limit else result.findings
    out = []
    for finding in shown:
        where = f"{result.path}:{finding.line}" if result.path else f"line {finding.line}"
        tag = finding.rule if finding.source == "static" else "model"
        out.append(f"{where}  {finding.severity}  {finding.category}/{tag}  {finding.message}")
        if finding.hint:
            out.append(f"    fix: {finding.hint}")
    hidden = len(result.findings) - len(shown)
    if hidden > 0:
        out.append(f"    ... and {hidden} more in this file")
    return out


def text(results: list[Result], with_warnings: bool = False) -> str:
    out: list[str] = []
    total = 0
    for result in results:
        total += len(result.findings)
        out += lines_for(result)
        if result.note:
            out.append(f"{result.path or '(pasted code)'}: {result.note}")
    warnings: dict[str, None] = {}
    for result in results:
        for warning in result.warnings:
            warnings.setdefault(warning)
    if with_warnings:
        out += [f"code-gate: warning: {warning}" for warning in warnings]
    if not total:
        out.append("code-gate: no findings")
    else:
        high = sum(f.severity == "high" for r in results for f in r.findings)
        out.append(f"code-gate: {total} finding{'s' if total != 1 else ''}, {high} high")
    return "\n".join(out)
