"""The PostToolUse hook: check the file Claude has just written.

Static checks only, so it answers in well under a second. It never
fails the tool call: whatever goes wrong, it prints `{}` and exits 0.
A settings file that is wrong is not "whatever goes wrong": the checks
run with the defaults and the problem is passed to Claude as context.
"""

from __future__ import annotations

import json
import os

from .finding import at_least
from .report import lines_for
from .review import Config, Result, review_file

MAX_LINES = 12


def answer(event: dict) -> dict:
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not isinstance(path, str) or not path or not os.path.isfile(path):
        return {}
    config = Config.near(os.path.dirname(os.path.abspath(path)))
    result = review_file(path, config=config)
    shown = at_least(result.findings, config.report_from)
    settings = "".join(f"\ncode-gate settings problem: {warning}" for warning in config.warnings[:5])
    if not shown:
        if not settings:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": settings.strip() + "\nTell the user."}}
    text = "\n".join(lines_for(Result(result.path, result.language, shown), MAX_LINES)) + settings
    blocking = [] if config.block_on == "never" else at_least(shown, config.block_on)
    if blocking:
        reason = (
            f"code-gate found {len(blocking)} problem(s) that must be fixed in the file just written:\n{text}\n"
            "Fix them now. If one is a false alarm, say why and add `code-gate: ignore[rule]` on that line."
        )
        return {"decision": "block", "reason": reason}
    context = f"code-gate notes on the file just written (fix them if they are real):\n{text}"
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}


def run(stdin, stdout) -> int:
    try:
        event = json.loads(stdin.read() or "{}")
        out = answer(event if isinstance(event, dict) else {})
    except Exception:  # noqa: BLE001 - a hook must never break the session
        out = {}
    stdout.write(json.dumps(out) + "\n")
    return 0
