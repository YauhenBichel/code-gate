"""Command line: code-gate check | rules | hook | mcp."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, hook, mcp
from .finding import SEVERITIES, at_least
from .report import text
from .review import review_file
from .rules import RULES
from .source import LANGUAGES

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "dist", "build", "target", "vendor", "__pycache__"}


def files_under(paths: list[str]) -> list[str]:
    found: list[str] = []
    for path in paths:
        if os.path.isdir(path):
            for folder, dirs, names in os.walk(path):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
                found += [os.path.join(folder, n) for n in sorted(names) if os.path.splitext(n)[1].lower() in LANGUAGES]
        else:
            found.append(path)
    return found


def _warnings(results: list) -> list[str]:
    seen: dict[str, None] = {}
    for result in results:
        for warning in result.warnings:
            seen.setdefault(warning)
    return list(seen)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="code-gate", description="Check code a model has written.")
    parser.add_argument("--version", action="version", version=f"code-gate {__version__}")
    commands = parser.add_subparsers(dest="command")
    check = commands.add_parser("check", help="check files or folders")
    check.add_argument("paths", nargs="+")
    check.add_argument("--deep", action="store_true", help="also ask the local model")
    check.add_argument("--json", action="store_true", help="print JSON")
    check.add_argument("--fail-on", choices=SEVERITIES + ("never",), default="high", help="exit 1 from this severity up")
    commands.add_parser("rules", help="list the static rules")
    commands.add_parser("hook", help="Claude Code PostToolUse hook (reads the event on standard input)")
    commands.add_parser("mcp", help="run the MCP server on standard input and output")
    arguments = parser.parse_args(argv)

    if arguments.command == "hook":
        return hook.run(sys.stdin, sys.stdout)
    if arguments.command == "mcp":
        return mcp.serve()
    if arguments.command == "rules":
        for rule, (category, severity, message, _hint) in RULES.items():
            print(f"{rule:28} {severity:7} {category:15} {message}")
        return 0
    if arguments.command == "check":
        missing = [path for path in arguments.paths if not os.path.lexists(path)]
        present = [path for path in arguments.paths if path not in missing]
        results = [review_file(path, deep=arguments.deep) for path in files_under(present)]
        if arguments.json:
            print(json.dumps({"results": [result.to_dict() for result in results]}, indent=2))
        else:
            print(text(results))
        for warning in _warnings(results):
            print(f"code-gate: warning: {warning}", file=sys.stderr)
        for path in missing:
            print(f"code-gate: no such file or folder: {path}", file=sys.stderr)
        if missing:
            return 2
        if arguments.fail_on == "never":
            return 0
        return 1 if any(at_least(result.findings, arguments.fail_on) for result in results) else 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
