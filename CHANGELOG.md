# Changelog

## 0.1.0

First public version: a command line (`code-gate check`, `rules`, `hook`, `mcp`), an MCP server with three
tools, and a Claude Code plugin with a hook on every edit. 38 static rules for Python, TypeScript/JavaScript,
Java and Go, and an optional second opinion from a local model. The PyPI package is `code-gate-llm`.

Hardening done before this release, after a stress test:

- **Security.** A project's `.code-gate.json` can no longer choose the command that is run or the server the
  code is sent to. `model.command`, `model.url`, `model.model` and `allow_remote` are read only from the
  user's own file (`~/.config/code-gate/config.json` or `CODE_GATE_CONFIG`); in a project file they are ignored
  with a warning.
- A model answer is used only when the server names the local backend. A missing or empty
  `x-harness-backend` header is no longer accepted, unless the user's own file sets `allow_remote`.
- Text from the model is cleaned of control characters and hidden Unicode marks before it is shown.
- An answer that is cut off, too large (over 2 MB) or otherwise broken is a note; the static findings stand.
- Time grows with the size of the file: the secret check and the brace checks no longer take quadratic time
  on one long line, and a long expression with thousands of calls is walked once.
- A file the Python parser cannot take (very deep nesting, a NUL byte) is reported as not fully checked
  instead of ending the run.
- Named pipes and other non-regular files are skipped, not read. A path that does not exist is an error with
  exit code 2, not "no findings".
- A settings file with wrong types gives warnings and default values instead of a traceback or a silent hook.
- Python grammar newer than the running interpreter is a `low` `newer-syntax` finding, not a `high`
  `syntax-error`.
- The MCP server answers `-32602` for `params` that is not an object, refuses snippets over 400 KB, and keeps
  running whatever one request does.
- Fewer false alarms: `placeholder` (high) now needs an explicit unfinished marker, a plain empty body is the
  new `empty-function` (medium), and abstract methods, protocols, overloads and documented hooks are quiet.
  `hardcoded-secret` now looks at the value, so labels, field names and URLs are not reported.
- Fewer misses: `no-timeout` now sees `urllib.request.urlopen`, import aliases, `from requests import get`,
  `requests.Session()` in Python, and `fetch` and `axios` in TypeScript/JavaScript.
