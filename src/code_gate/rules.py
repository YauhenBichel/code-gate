"""Every static rule in one table: id, category, severity, what it means, what to do."""

from __future__ import annotations

from .finding import Finding

# id: (category, severity, message, hint)
RULES: dict[str, tuple[str, str, str, str]] = {
    # security
    "hardcoded-secret": ("security", "high", "A secret is written in the source", "Read it from the environment or a secret store, and rotate the value"),
    "sql-built-from-strings": ("security", "high", "SQL is built by joining strings with values", "Pass the values as query parameters"),
    "shell-injection": ("security", "high", "A shell command is built from values", "Pass an argument list and no shell"),
    "eval": ("security", "high", "Code is run from a string", "Parse the input instead of evaluating it"),
    "unsafe-deserialisation": ("security", "high", "Untrusted data can run code when it is loaded", "Use a data-only format (JSON, safe YAML loader)"),
    "tls-check-off": ("security", "high", "Certificate checking is switched off", "Keep verification on; add the CA you need"),
    "weak-hash": ("security", "medium", "MD5 or SHA-1 is used", "Use SHA-256, or a password hash (argon2, bcrypt) for passwords"),
    "html-injection": ("security", "medium", "HTML is set from a value", "Set text, or sanitise the HTML first"),
    "debug-on": ("security", "medium", "Debug mode is switched on in code", "Take it from configuration, off by default"),
    "insecure-temp-file": ("security", "medium", "A temporary file name is made without creating the file", "Use mkstemp or NamedTemporaryFile"),
    # algorithm
    "deeply-nested-loops": ("algorithm", "medium", "Three or more loops are nested", "Index one side in a map or set, or sort and scan once"),
    "sort-in-loop": ("algorithm", "medium", "A sort runs on every pass of a loop", "Sort once before the loop, or keep a heap"),
    "io-in-loop": ("algorithm", "medium", "A query or a network call runs once per item", "Fetch in one batch before the loop"),
    "sequential-await-in-loop": ("algorithm", "low", "Each pass waits for the one before it", "Start them together (Promise.all) if they are independent"),
    # data structure
    "list-membership-in-loop": ("data-structure", "medium", "A list is searched on every pass of a loop", "Put the values in a set or a map first"),
    "queue-on-a-list": ("data-structure", "medium", "Items are taken from the front of a list", "Use a deque"),
    "string-built-in-loop": ("data-structure", "medium", "A string grows by one piece per pass", "Collect the pieces and join them once (StringBuilder, strings.Builder)"),
    "linked-list-by-index": ("data-structure", "medium", "A LinkedList is read by index", "Use an ArrayList, or iterate"),
    # design
    "high-complexity": ("design", "medium", "This function has too many branches to follow", "Split it by decision, one job per function"),
    "god-class": ("design", "medium", "This class has too many methods for one job", "Split it by responsibility"),
    "global-state": ("design", "medium", "A function changes a module-level variable", "Pass the state in, or keep it in an object"),
    "mutable-default": ("design", "medium", "A mutable default argument is shared between calls", "Default to None and create the value inside"),
    # smell
    "syntax-error": ("smell", "high", "The file does not parse", "Fix the syntax first; the other checks could not run"),
    "placeholder": ("smell", "high", "The code was left unfinished", "Write the body, or remove the function"),
    "empty-function": ("smell", "medium", "A function has no body", "Write the body, say in a docstring why it is empty, or remove it"),
    "newer-syntax": ("smell", "low", "The file was not checked", "Run code-gate with a newer Python to check this file"),
    "long-function": ("smell", "medium", "This function is too long to read in one go", "Extract the steps into named functions"),
    "too-many-parameters": ("smell", "low", "This function takes too many parameters", "Group them in one object"),
    "deep-nesting": ("smell", "medium", "The code is nested too deep", "Return early, or extract the inner block"),
    "large-file": ("smell", "low", "This file is very long", "Split it by topic"),
    "any-type": ("smell", "low", "`any` switches the type checker off", "Give the real type, or `unknown` and narrow it"),
    # non-functional
    "swallowed-error": ("non-functional", "high", "An error is caught and thrown away", "Handle it, log it, or let it rise"),
    "ignored-error": ("non-functional", "medium", "A returned error is discarded", "Check it"),
    "no-timeout": ("non-functional", "medium", "A network call can wait for ever", "Set a timeout"),
    "resource-not-closed": ("non-functional", "medium", "A file or connection may stay open", "Open it in a with/try-with-resources/defer block"),
    "defer-in-loop": ("non-functional", "medium", "defer in a loop runs only when the function ends", "Close inside the loop, or move the body to a function"),
    "print-instead-of-log": ("non-functional", "low", "Output goes to print, not to a logger", "Use the project's logger"),
    "stack-trace-printed": ("non-functional", "low", "The stack trace is printed, not logged", "Log it with the logger"),
}


def make(rule: str, line: int, detail: str = "") -> Finding:
    category, severity, message, hint = RULES[rule]
    return Finding(category, rule, severity, line, f"{message}: {detail}" if detail else message, hint)
