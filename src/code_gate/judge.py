"""Ask a local model what the static checks cannot see.

The model is reached through llm-harness's HTTP endpoint with the model
name `llm-harness/local-only`, a lock: the harness never passes such a
request to a paid cloud model. An older harness does not know that name
and would route it by its own rules, so before the first request the
model list is read; if `local-only` is not in it, the `llm-harness local`
command is used instead, which talks to the local backend directly.
Lines that hold a secret, and lines too long to be searched for one, are
blanked before the code leaves this process. An answer over HTTP is kept
only when the server says the local backend served it. A model that is
down, slow, cut off or talks nonsense produces a note, never an error:
the static findings still stand.

The command, the URL, the model name and `allow_remote` are taken from
the user's own config only (see review.Config), never from a project.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

from .checks_common import MAX_LINE, secret_lines
from .finding import CATEGORIES, SEVERITIES, Finding, clean
from .source import Source

DEFAULTS = {
    "command": ["llm-harness", "local", "--file"],  # the prompt file path is added at the end
    "url": "http://127.0.0.1:11500/v1/chat/completions",
    "model": "llm-harness/local-only",
    "timeout": 300,
    "max_lines": 400,
    "max_findings": 8,
}
MAX_ANSWER = 2_000_000  # bytes read from the model server, at most

RUBRIC = """You review source code that an AI assistant has just written. Report only real problems a senior engineer would ask to change before merging. Judge these six areas:

- design: unclear responsibility, a function or class doing several jobs, wrong layer, tight coupling, duplicated logic, an abstraction that is missing or not needed
- data-structure: a list where a set or map is needed, a wrong container for the access pattern, data copied without need, parallel arrays instead of records
- algorithm: worse time or memory complexity than the task needs (say the complexity now and what it could be), repeated work, wrong result on an edge case (empty input, one item, duplicates, overflow)
- security: injection, missing input validation, secrets, unsafe defaults, missing authorisation check, data leaked in logs or errors
- smell: dead code, misleading names, magic numbers, code that pretends to work (fake data, a stub that returns success)
- non-functional: no timeout or retry on a network call, unbounded memory or result size, errors swallowed or not reported, no logging where an operator needs it, resources not closed, not safe under concurrent use, hard to test

Rules:
- Use only the line numbers printed at the start of each line.
- Do not repeat a finding from the list "Already found".
- Do not report style, formatting, missing comments or missing tests.
- If the code is fine, return an empty list. Do not invent problems.
- At most {limit} findings, the most serious first.

Answer with JSON only, in this shape:
{{"findings": [{{"category": "<one of: {categories}>", "severity": "<high|medium|low>", "line": <number>, "message": "<what is wrong, one sentence>", "hint": "<what to do instead, one sentence>"}}]}}"""


def prompt_for(source: Source, known: list[Finding], limit: int, max_lines: int) -> str:
    secrets = secret_lines(source)
    shown = source.lines[:max_lines]
    numbered = []
    for number, line in enumerate(shown, 1):
        if number in secrets:
            line = "<line removed: it holds a secret>"
        elif len(line) > MAX_LINE:
            line = "<line removed: it is too long to check for secrets>"
        numbered.append(f"{number:4d}| {line}")
    parts = [
        RUBRIC.format(limit=limit, categories=", ".join(CATEGORIES)),
        f"\nLanguage: {source.language or 'unknown'}\nFile: {source.path or '(pasted code)'}",
    ]
    if len(source.lines) > max_lines:
        parts.append(f"Only the first {max_lines} of {len(source.lines)} lines are shown.")
    if known:
        parts.append("Already found:\n" + "\n".join(f"- line {f.line}: {f.rule}" for f in known[:30]))
    parts.append("Code:\n" + "\n".join(numbered))
    return "\n\n".join(parts)


def _json_in(text: str) -> dict | None:
    text = re.sub(r"(?s)<think>.*?</think>", "", text).strip()
    fenced = re.search(r"(?s)```(?:json)?\s*(\{.*\})\s*```", text)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def parse(answer: str, source: Source, known: list[Finding], limit: int) -> list[Finding]:
    """Keep only findings that are well formed and point at a real line."""
    value = _json_in(answer)
    if value is None or not isinstance(value.get("findings"), list):
        raise ValueError("the answer was not the JSON that was asked for")
    taken = {(f.line, f.category) for f in known}
    secrets = secret_lines(source)
    kept: list[Finding] = []
    for item in value["findings"]:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "")).strip().lower().replace("_", "-").replace(" ", "-")
        severity = str(item.get("severity", "")).strip().lower()
        message = clean(item.get("message", ""), 300)
        try:
            line = int(item.get("line"))
        except (TypeError, ValueError):
            continue
        if category not in CATEGORIES or severity not in SEVERITIES or not message:
            continue
        if not 1 <= line <= len(source.lines) or line in secrets or (line, category) in taken:
            continue
        if source.ignored(line, "model"):
            continue
        taken.add((line, category))
        hint = clean(item.get("hint", ""), 300)
        kept.append(Finding(category, "model", severity, line, message, hint, "model"))
        if len(kept) >= limit:
            break
    return kept


def ask(prompt: str, settings: dict) -> tuple[str, str]:
    """Return (answer text, backend that served it)."""
    if settings.get("url") and _lock_is_known(settings):
        return _ask_http(prompt, settings)
    return _ask_command(prompt, settings), "local"


_KNOWN: dict[tuple[str, str], bool] = {}


def _lock_is_known(settings: dict) -> bool:
    """True when the endpoint lists the model that is asked for (read once per process)."""
    key = (settings["url"], settings["model"])
    if key not in _KNOWN:
        models = settings["url"].rsplit("/chat/completions", 1)[0] + "/models"
        try:
            with urllib.request.urlopen(models, timeout=5) as response:  # noqa: S310 - the URL comes from the user's config
                listed = json.loads(response.read().decode("utf-8", "replace")).get("data", [])
            _KNOWN[key] = any(isinstance(m, dict) and m.get("id") == settings["model"] for m in listed)
        except (OSError, ValueError, AttributeError):
            _KNOWN[key] = False
    return _KNOWN[key]


def _ask_command(prompt: str, settings: dict) -> str:
    command = list(settings["command"])
    if not shutil.which(command[0]):
        raise OSError(f"`{command[0]}` is not installed")
    handle, path = tempfile.mkstemp(prefix="code-gate-", suffix=".md")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(prompt)
        done = subprocess.run(command + [path], capture_output=True, text=True, timeout=settings["timeout"], check=False)  # noqa: S603
    finally:
        os.unlink(path)
    if done.returncode != 0 or not done.stdout.strip():
        lines = [line for line in done.stderr.strip().splitlines() if line.strip()]
        raise OSError(lines[-1] if lines else f"`{command[0]}` gave no answer (exit {done.returncode})")
    return done.stdout


def _ask_http(prompt: str, settings: dict) -> tuple[str, str]:
    body = json.dumps(
        {
            "model": settings["model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
    ).encode()
    headers = {"content-type": "application/json", "x-harness-route": "local-only"}
    request = urllib.request.Request(settings["url"], data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=settings["timeout"]) as response:  # noqa: S310 - the URL comes from the user's config
        raw = response.read(MAX_ANSWER + 1)
        backend = (response.headers.get("x-harness-backend") or "").strip().lower()
    if len(raw) > MAX_ANSWER:
        raise ValueError("the answer is larger than 2 MB")
    payload = json.loads(raw.decode("utf-8", "replace"))
    return payload["choices"][0]["message"]["content"] or "", backend


# What a model that is down, slow, cut off (IncompleteRead) or answering in another shape raises.
_FAILED = (
    urllib.error.URLError, http.client.HTTPException, OSError, subprocess.TimeoutExpired,
    KeyError, IndexError, TypeError, AttributeError, ValueError,
)  # fmt: skip


def judge(source: Source, known: list[Finding], settings: dict | None = None, asker=ask) -> tuple[list[Finding], str]:
    """Return (findings, note). The note says why there are none when the model could not be used."""
    merged = {**DEFAULTS, **(settings or {})}
    prompt = prompt_for(source, known, merged["max_findings"], merged["max_lines"])
    try:
        answer, backend = asker(prompt, merged)
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = json.loads(error.read().decode("utf-8", "replace")).get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001 - any unreadable body is just no detail
            pass
        return [], clean(f"model not used: HTTP {error.code} {detail}", 240)
    except _FAILED as error:
        return [], clean(f"model not used: {type(error).__name__}: {error}", 240)
    backend = clean(backend, 40)
    if backend != "local" and not merged.get("allow_remote"):
        if backend:
            return [], f"model answer dropped: it was served by `{backend}`, not the local model"
        return [], "model answer dropped: the server did not say that the local model served it (set `allow_remote` in your own config to accept such answers)"
    try:
        findings = parse(str(answer), source, known, merged["max_findings"])
    except ValueError as error:
        return [], f"model answer dropped: {error}"
    note = f"model: {backend}" if backend else "model: asked"
    return findings, note
