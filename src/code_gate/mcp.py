"""A Model Context Protocol server over standard input and output. Standard library only.

    code-gate mcp

One JSON-RPC message per line in, one per line out. Only tools are offered.
"""

from __future__ import annotations

import json
import sys

from . import __version__
from .report import text as as_text
from .review import MAX_BYTES, Config, review_file, review_text
from .rules import RULES

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")  # newest first
MAX_FILES = 20

INSTRUCTIONS = (
    "code-gate checks code that a model has written: security, algorithms, data structures, design, "
    "code smells and non-functional practice (timeouts, error handling, resources, logging). "
    "Call review_files on the files you changed before you say the work is done, or review_code on a snippet. "
    "The fast pass is static checks. Set deep to true to add a second opinion from the local model on design, "
    "data structures and algorithms; it can take a minute. "
    "Fix every high finding. For a false alarm, tell the user why and add `code-gate: ignore[rule]` on the line."
)

TOOLS = [
    {
        "name": "review_code",
        "description": "Check a piece of code given as text. Returns findings with line, severity, category and a fix.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The source code"},
                "language": {"type": "string", "enum": ["python", "typescript", "javascript", "java", "go"]},
                "filename": {"type": "string", "description": "Optional file name, used to find the language"},
                "deep": {"type": "boolean", "description": "Also ask the local model (slower)", "default": False},
            },
            "required": ["code"],
        },
    },
    {
        "name": "review_files",
        "description": f"Check files on disk (at most {MAX_FILES}). Returns findings with file, line, severity, category and a fix.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "description": "Absolute file paths"},
                "deep": {"type": "boolean", "description": "Also ask the local model (slower)", "default": False},
            },
            "required": ["paths"],
        },
    },
    {
        "name": "list_rules",
        "description": "List the static rules with their category and severity.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class Problem(Exception):
    """Something the caller got wrong; the text is shown to the model."""


def call(name: object, arguments: object) -> tuple[str, dict]:
    if arguments is None:
        arguments = {}
    if not isinstance(name, str) or not isinstance(arguments, dict):
        raise Problem("name must be a tool name and arguments an object")
    if name == "review_code":
        code = arguments.get("code")
        if not isinstance(code, str) or not code.strip():
            raise Problem("code is required")
        if len(code) > MAX_BYTES:
            raise Problem("code is larger than 400 KB; send a smaller piece")
        filename, language = arguments.get("filename") or "", arguments.get("language") or ""
        if not isinstance(filename, str) or not isinstance(language, str):
            raise Problem("filename and language must be text")
        if not language and "." not in filename:
            raise Problem("give language or a filename with an extension")
        results = [review_text(code, filename, language, deep=bool(arguments.get("deep")), config=Config.near(None))]
    elif name == "review_files":
        paths = arguments.get("paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
            raise Problem("paths must be a list of file paths")
        if len(paths) > MAX_FILES:
            raise Problem(f"at most {MAX_FILES} files in one call")
        results = [review_file(path, deep=bool(arguments.get("deep"))) for path in paths]
    elif name == "list_rules":
        rules = [{"rule": rule, "category": row[0], "severity": row[1], "what": row[2]} for rule, row in RULES.items()]
        return "\n".join(f"{r['rule']}  {r['severity']}  {r['category']}  {r['what']}" for r in rules), {"rules": rules}
    else:
        raise Problem(f"unknown tool: {name}")
    return as_text(results, with_warnings=True), {"results": [result.to_dict() for result in results]}


def _result(id_, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _tools_call(params: dict) -> dict:
    try:
        text, data = call(params.get("name"), params.get("arguments"))
    except Problem as error:
        return {"content": [{"type": "text", "text": str(error)}], "isError": True}
    except Exception as error:  # noqa: BLE001 - the server must keep running
        return {"content": [{"type": "text", "text": f"code-gate failed: {error.__class__.__name__}"}], "isError": True}
    return {"content": [{"type": "text", "text": text}], "structuredContent": data, "isError": False}


def handle(message) -> dict | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "not a JSON-RPC 2.0 message")
    method, id_, params = message.get("method"), message.get("id"), message.get("params")
    if "id" not in message:
        return None  # a notification: nothing to answer
    if params is None:
        params = {}
    if not isinstance(method, str):
        return _error(id_, -32600, "method must be a string")
    if not isinstance(params, dict):
        return _error(id_, -32602, "params must be an object")
    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(
            id_,
            {
                "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "code-gate", "version": __version__},
                "instructions": INSTRUCTIONS,
            },
        )
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": TOOLS})
    if method == "tools/call":
        return _result(id_, _tools_call(params))
    return _error(id_, -32601, f"unknown method: {method[:80]}")


def _answer(line: str) -> dict | None:
    """The reply to one line of input. Nothing a client sends may end the server."""
    try:
        message = json.loads(line)
    except (ValueError, RecursionError, MemoryError):
        return _error(None, -32700, "not JSON")
    try:
        return handle(message)
    except Exception as error:  # noqa: BLE001 - the server must keep running
        id_ = message.get("id") if isinstance(message, dict) else None
        return _error(id_, -32603, f"code-gate failed: {error.__class__.__name__}")


def serve(stdin=None, stdout=None) -> int:
    if stdin is None:
        stdin = sys.stdin
        if hasattr(stdin, "reconfigure"):  # bytes that are not UTF-8 become "?" instead of ending the server
            stdin.reconfigure(encoding="utf-8", errors="replace")
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        reply = _answer(line)
        if reply is not None:
            try:
                stdout.write(json.dumps(reply) + "\n")
                stdout.flush()
            except (BrokenPipeError, ValueError):  # the client has gone
                return 0
    return 0
