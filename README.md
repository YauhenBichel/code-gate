# code-gate

Checks the code a model writes before it is called done: security, algorithms, data structures, design, code
smells and non-functional practice (timeouts, error handling, resources, logging). It is a command line, an MCP
server and a Claude Code plugin with a hook. Python, TypeScript/JavaScript, Java and Go.
Standard library only: it needs Python 3.9 or newer and nothing else.


## Install

The package on PyPI is `code-gate-llm`. The command it installs is `code-gate`.

```bash
uv tool install code-gate-llm        # or: pipx install code-gate-llm, or: pip install code-gate-llm
code-gate --version
```

This gives you the command line and the MCP server. The Claude Code plugin (the hook on every edit and the
`/code-gate:review` command) is installed from a checkout of the repository, and needs no `pip install`:

```bash
git clone https://github.com/YauhenBichel/code-gate
claude plugin marketplace add ./code-gate
claude plugin install code-gate@code-gate
```

## What it does

1. **On every edit** (a `PostToolUse` hook on Edit and Write) it runs static checks on the file, in a few
   milliseconds. A `high` finding blocks: Claude is told what is wrong and must fix it. A `medium` finding is
   passed to Claude as a note. `low` findings are not shown by the hook.
2. **On request** (`/code-gate:review`, the MCP tools, or `--deep`) it also asks a local model for what static
   checks cannot see: a function doing several jobs, a wrong container, a worse complexity than needed.

## Command line

```bash
code-gate check src/              # static checks
code-gate check app.py --deep     # also ask the local model
code-gate check src/ --json --fail-on medium
code-gate rules                   # the 38 static rules
```

Exit code: `0` nothing at or above `--fail-on` (default `high`), `1` such a finding, `2` a path that does not
exist or a wrong option. From a checkout, `bin/code-gate` does the same without installing.

Files that are not regular files (pipes, devices), files larger than 400 KB and files in other languages are
skipped and listed as skipped. A file the parser cannot take is reported as not fully checked; the run goes on.

## MCP tools

| Tool | What it does |
| --- | --- |
| `review_files` | check files on disk (`paths`, optional `deep`) |
| `review_code` | check a snippet up to 400 KB (`code`, `language` or `filename`, optional `deep`) |
| `list_rules` | the static rules with category and severity |

The plugin starts the server by itself. For any other MCP client, after installing the package:

```bash
claude mcp add code-gate -- code-gate mcp
```

or, in `.mcp.json`, `.cursor/mcp.json` or the client's own server list:

```json
{
  "mcpServers": {
    "code-gate": { "command": "code-gate", "args": ["mcp"] }
  }
}
```

The server speaks MCP over stdio and reads files only on the machine it runs on. The hook is specific to
Claude Code. Tested with Claude Code only.

## The local model

The deep pass is off unless you ask for it. It posts the file to an OpenAI-compatible server on your own
machine. The defaults are those of llm-harness, a local model router: the address
`http://127.0.0.1:11500/v1/chat/completions` and the model name `llm-harness/local-only`. That name is a lock:
the harness never passes such a request to a cloud model. If the running harness is too old to list
`local-only`, code-gate runs `llm-harness local --file <prompt>` instead. With no such server and no such
command, the static findings are returned with a note that the model was not used.

- Lines where a secret was found, and lines longer than 4,000 characters, are removed before the code is sent.
- The answer is used only when the server says, in the `x-harness-backend: local` header, that the local model
  served it. Any other answer is thrown away. A server that does not send this header (Ollama, llama.cpp,
  LM Studio, vLLM) needs `"allow_remote": true` in **your own** settings file; you then vouch for where it runs.
- Every model finding is checked: known category, known severity, a line that exists. The rest is dropped.
  Control characters and hidden Unicode marks are removed from what the model wrote before it is shown.
- If the model is down, slow or cut off, the static findings are returned with a note. Nothing fails.

Model findings are marked `model`. They are a second opinion and can be wrong.

## Settings

There are two files, and they are not trusted the same.

**Your own file** may set everything. It is `~/.config/code-gate/config.json` (or under `$XDG_CONFIG_HOME`),
or the file named by the `CODE_GATE_CONFIG` environment variable:

```json
{
  "model": {
    "url": "http://127.0.0.1:11434/v1/chat/completions",
    "model": "qwen2.5-coder",
    "command": ["my-model-cli", "--file"],
    "allow_remote": true
  }
}
```

**A project's file** is `.code-gate.json` in the folder of the file or any folder above it. It comes with the
repository, so it is written by whoever wrote the repository. It can tune the checks:

```json
{
  "disable": ["print-instead-of-log"],
  "limits": {"function_lines": 80},
  "hook": {"block_on": "high", "report_from": "medium"},
  "model": {"enabled": true, "timeout": 300, "max_lines": 400, "max_findings": 8}
}
```

It can **not** set `model.command`, `model.url`, `model.model` or `allow_remote`: a repository you cloned must
not choose a program to run on your machine or a server to send your code to. Such keys are ignored and a
warning names them. A settings file with a wrong type or an unknown key is also a warning, not a failure: the
bad value is left at its default and the checks run.

`block_on` can be `never`. To silence one line, put `code-gate: ignore[rule]` in a comment on it or on the line above.

## Limits

- Python is read with `ast`. The other three languages are read with brace and line heuristics, not a parser, so
  they miss more and can be wrong on unusual formatting. Java and Go have the fewest rules: for example
  `no-timeout` sees `requests`, `httpx` and `urllib.request.urlopen` in Python, `fetch` and `axios` in
  TypeScript/JavaScript and the default HTTP client in Go, and nothing in Java.
- A file with a line longer than 10,000 characters (minified or generated code) gets only the line checks; the
  result says so.
- Python code that uses grammar newer than the Python running code-gate cannot be parsed. It is reported as a
  `low` `newer-syntax` finding, which never blocks; run code-gate with a newer Python to check such a file.
- `empty-function` (medium) is a function whose body is only `pass` or `...`. It becomes `placeholder` (high)
  only when the code itself says it is unfinished (`TODO`, `FIXME`, "not implemented"). Abstract methods,
  protocols, overloads and documented hooks are not reported.
- `hardcoded-secret` looks at the value as well as the name, so a short or word-like secret can be missed.
- The hook checks the whole file, so an old problem in a file blocks a new edit to it until it is fixed or ignored.
- It does not follow data across functions or files. It is a gate for obvious mistakes, not a replacement for
  semgrep, a type checker or a human review.

## Development

```bash
python3 -m unittest discover -s tests     # no dependencies; Python 3.9 to 3.14
python3 bin/code-gate check src           # code-gate on its own source
```

Changes are listed in [CHANGELOG.md](https://github.com/YauhenBichel/code-gate/blob/main/CHANGELOG.md).
Licence: MIT, see [LICENSE](https://github.com/YauhenBichel/code-gate/blob/main/LICENSE).
